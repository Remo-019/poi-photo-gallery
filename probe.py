#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
健康探针：花 1 次搜索调用 + 几张图，确认「还能不能拿到原图」。

这个项目的价值建立在高德 OSS 的 limit_1 参数上。哪天它被忽略，接口照常返回
成功、退出码仍是 0，只是图片全变成 500px 缩略图——这种静默失效会直接进报告。
所以需要一个能放进定时任务、失败就报警的最小检查。

用法：
    set AMAP_KEY=你的key
    python probe.py
    python probe.py --city 北京市 --keywords 医院 --min-width 1200
    python probe.py --json probe.json      # 追加一行历史，便于看趋势

退出码：
    0  正常
    1  中位宽度低于阈值（原图参数疑似失效）
    2  参数缺失或调用失败

建议每天跑一次；连续两天退出码 1 就该去核对接口了。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time

import poi_core

DEFAULT_CITY = "北京市"
DEFAULT_KEYWORDS = "医院"
DEFAULT_TYPES = "090000"
MIN_WIDTH = 1200


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="高德原图链路健康探针")
    ap.add_argument("--city", default=DEFAULT_CITY)
    ap.add_argument("--keywords", default=DEFAULT_KEYWORDS)
    ap.add_argument("--types", default=DEFAULT_TYPES,
                    help="POI 分类码，默认 090000（医疗保健）")
    ap.add_argument("--poi", type=int, default=3, help="抽查几个 POI，默认 3")
    ap.add_argument("--photos", type=int, default=3, help="每个 POI 取几张，默认 3")
    ap.add_argument("--min-width", type=int, default=MIN_WIDTH,
                    help=f"中位宽度低于此值即报警，默认 {MIN_WIDTH}")
    ap.add_argument("--amap-key", default=None, help="高德 Web 服务 Key，默认读环境变量 AMAP_KEY")
    ap.add_argument("--json", default=None, help="把结果追加到该 JSON 文件")
    ap.add_argument("--keep", action="store_true", help="保留下载的图片，便于人工核对")
    args = ap.parse_args(argv)

    key = args.amap_key or os.environ.get("AMAP_KEY", "").strip()
    if not key:
        print("缺少高德 Key：用 --amap-key 传入，或设置环境变量 AMAP_KEY。", file=sys.stderr)
        return 2

    work = tempfile.mkdtemp(prefix="poi_probe_")
    print(f"探针：{args.city} / {args.keywords}，抽查 {args.poi} 个 POI")
    print(f"工作目录：{work}")

    try:
        items = poi_core.amap_search(
            key, args.keywords, args.city, args.types, args.poi, 25, 0.4, log=lambda *_: None)
    except Exception as exc:  # noqa: BLE001
        print(f"搜索失败：{exc}", file=sys.stderr)
        return 2

    if not items:
        print("搜索没返回结果，城市名或分类码可能有问题。", file=sys.stderr)
        return 2

    try:
        rows = poi_core.download_items(
            items, work, args.photos, 5000, 0.3, log=lambda *_: None)
    except Exception as exc:  # noqa: BLE001
        print(f"下载失败：{exc}", file=sys.stderr)
        return 2

    widths: list[int] = []
    for row in rows:
        raw = row.get("最大尺寸") or ""
        if "x" in raw:
            try:
                widths.append(int(raw.split("x")[0]))
            except ValueError:
                continue

    total = sum(r.get("已下载", 0) for r in rows)
    median = int(statistics.median(widths)) if widths else 0
    peak = max(widths) if widths else 0

    verdict = "正常"
    code = 0
    if not total:
        verdict = "一张图都没取到，接口或网络异常"
        code = 1
    elif median < args.min_width:
        verdict = f"中位宽度 {median}px 低于 {args.min_width}px，原图参数疑似失效"
        code = 1

    print(f"  POI {len(rows)} 个 / 图片 {total} 张 / 中位宽度 {median}px / 最大 {peak}px")
    print(f"  判定：{verdict}")

    if args.json:
        record = {
            "时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "城市": args.city, "关键词": args.keywords,
            "POI数": len(rows), "图片数": total,
            "中位宽度": median, "最大宽度": peak,
            "阈值": args.min_width, "判定": verdict, "退出码": code,
        }
        history = []
        if os.path.exists(args.json):
            try:
                with open(args.json, encoding="utf-8") as fh:
                    history = json.load(fh)
                if not isinstance(history, list):
                    history = []
            except (OSError, ValueError):
                history = []
        history.append(record)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(history, fh, ensure_ascii=False, indent=2)
        print(f"  记录已追加：{args.json}")

    if args.keep:
        print(f"  图片保留在：{work}")
    else:
        import shutil
        shutil.rmtree(work, ignore_errors=True)

    return code


if __name__ == "__main__":
    raise SystemExit(main())
