# -*- coding: utf-8 -*-
"""命令行入口 —— 不装界面也能批量采集。

用法示例：

    # 高德 + 百度双源，每个 POI 最多 10 张
    python cli.py --city 北京市 --keywords 医院 --photos 10

    # 只跑高德，不限 POI 数量（翻到没有结果为止）
    python cli.py --city 北京市 --keywords 三甲医院 --source amap --max-poi 0

    # 只要清单和图片地址，不下载
    python cli.py --city 北京市 --keywords 咖啡馆 --dry-run

高德 Web 服务 Key 的优先级：--amap-key > 环境变量 AMAP_KEY。
百度走网页接口，不需要 Key。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import poi_core

SOURCE_MAP = {"both": ["高德", "百度"], "amap": ["高德"], "baidu": ["百度"]}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli.py",
        description="按城市 + 关键词采集地图 POI 门店照片（高德 / 百度双源）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("高德 Web 服务 Key")[0],
    )
    p.add_argument("--city", required=True, help="城市或区县名，如 北京市、浦东新区")
    p.add_argument("--keywords", required=True, help="关键词，如 医院、咖啡馆、连锁药店")
    p.add_argument("--source", default="both", choices=list(SOURCE_MAP),
                   help="数据源：both=高德+百度（默认）、amap、baidu")
    p.add_argument("--max-poi", type=int, default=20,
                   help="每个来源最多取多少个 POI，0 表示不限（默认 20）")
    p.add_argument("--photos", type=int, default=10,
                   help="每个 POI 最多下载几张图，0 表示全部（默认 10）")
    p.add_argument("--out", default="out", help="输出根目录（默认 ./out）")
    p.add_argument("--amap-key", default=None,
                   help="高德 Web 服务 Key；不填则读环境变量 AMAP_KEY")
    p.add_argument("--types", default=None,
                   help="高德 POI 分类码，可选，如 090100（医疗机构）")
    p.add_argument("--delay", type=float, default=0.3,
                   help="下载每张图之间的间隔秒数（默认 0.3，别设 0）")
    p.add_argument("--no-best", action="store_true",
                   help="跳过「挑最优图 + 生成总览图」")
    p.add_argument("--dry-run", action="store_true",
                   help="只搜索并列出清单，不下载图片")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    sources = SOURCE_MAP[args.source]
    if "高德" in sources and not (args.amap_key or os.environ.get("AMAP_KEY")):
        print("缺少高德 Key：加 --amap-key，或设环境变量 AMAP_KEY；"
              "只跑百度可加 --source baidu", file=sys.stderr)
        return 2
    amap_key = args.amap_key or os.environ.get("AMAP_KEY", "")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    tag = poi_core.safe_name(f"{args.city}_{args.keywords}", 30)
    root = os.path.join(args.out, f"{stamp}_{tag}")
    os.makedirs(root, exist_ok=True)
    print(f"输出目录：{root}\n")

    summary = []
    for idx, src in enumerate(sources, 1):
        out_dir = os.path.join(root, src)
        os.makedirs(out_dir, exist_ok=True)
        lim = "不限" if not args.max_poi else f"{args.max_poi} 个"
        per = "全部" if not args.photos else f"{args.photos} 张"
        print(f"===== [{idx}/{len(sources)}] {src} =====")
        print(f"城市 {args.city} | 关键词 {args.keywords} | POI {lim} | 每个最多 {per}")

        if src == "高德":
            items = poi_core.amap_search(
                amap_key, args.keywords, args.city, args.types,
                args.max_poi, 25, 0.4, log=print)
        else:
            items = poi_core.baidu_search(
                args.keywords, args.city, args.max_poi, 0.6, log=print)

        if not items:
            print(f"  {src} 没搜到结果，跳过\n")
            continue

        if args.dry_run:
            for i, it in enumerate(items, 1):
                print(f"  {i:03d}. {it['name']}  图片 {len(it.get('photos') or [])} 张"
                      f"  {it.get('address', '')}")
            print()
            summary.append(f"{src}：{len(items)} 个 POI（仅清单）")
            continue

        print(f"  共 {len(items)} 个 POI，开始下载照片…")
        rows = poi_core.download_items(
            items, out_dir, args.photos, 5000, args.delay, log=print)
        got = sum(r["已下载"] for r in rows)
        summary.append(f"{src}：{len(rows)} 个 POI / {got} 张图")

        if not args.no_best:
            st = poi_core.pick_best(out_dir, log=print)
            median = st.get("median", 0)
            if median and median < 800:
                print(f"  ! 中位宽度只有 {median}px。原图参数（limit_1）可能已失效——"
                      f"接口仍返回成功，但拿到的是缩略图，请先核对再使用。")
            elif median:
                print(f"  中位宽度 {median}px，原图参数正常。")
            poi_core.build_sheet_paged(out_dir, per_poi=3, per_page=24, log=print)
        print()

    if not summary:
        print("两个来源都没搜到结果。检查城市名（建议用标准区县名）或关键词。", file=sys.stderr)
        return 1
    print("\n".join(summary))
    print(f"\n保存位置：{root}")
    if not args.no_best:
        print("建议先看总览图：每页并排显示每店 3 张，可直接定位门头照。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
