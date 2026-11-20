#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从采集结果（manifest.csv）里，按真实像素量挑出每个 POI 最清晰的一张。

产出：
    photo_inventory.csv   每张图的尺寸/像素/体积（可排序挑图）
    best/                 每个 POI 一张最优图，命名 序号_名称.jpg
    best/_report.csv      入选清单（含是否达到印刷门槛）

用法：
    python select_best.py poi_photos_北京
    python select_best.py poi_photos_北京 --min-width 1000
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import statistics
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("需要 Pillow：pip install pillow")

EXTS = (".jpg", ".jpeg", ".png", ".webp")


def img_size(path: str) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def safe(text: str, limit: int = 40) -> str:
    bad = '\\/:*?"<>|'
    for ch in bad:
        text = text.replace(ch, "_")
    return text.strip(" .")[:limit] or "unnamed"


def photo_rank(item: dict) -> tuple:
    """挑图排序键：先按像素分档，同档内优先横构图与相册靠前的图。

    门头照通常是横构图且多排在相册前几张；菜品、菜单、顾客照多为竖构图或
    排在后面。像素按 25 万（约 500x500）为一档，只在同档内用构图与序号加权，
    不为了横图牺牲清晰度。
    """
    w, h = item.get("宽") or 0, item.get("高") or 0
    landscape = 1 if w >= h * 0.95 else 0
    try:
        idx = int(os.path.splitext(str(item.get("文件名", "")))[0][:3])
    except (ValueError, TypeError):
        idx = 99
    early = 1 if idx <= 2 else 0
    return (w * h // 250000, landscape, early, w * h, item.get("体积KB", 0))


def main() -> None:
    ap = argparse.ArgumentParser(description="挑出每个 POI 最清晰的照片")
    ap.add_argument("folder", help="采集输出目录（需含 manifest.csv）")
    ap.add_argument("--min-width", type=int, default=0,
                    help="低于此宽度的图不入选（默认 0，即只要有就选最清晰的）")
    ap.add_argument("--best-dir", default="best", help="最优图输出目录名，默认 best")
    args = ap.parse_args()

    folder = os.path.abspath(args.folder)
    manifest = os.path.join(folder, "manifest.csv")
    if not os.path.exists(manifest):
        sys.exit(f"找不到清单：{manifest}")

    with open(manifest, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    best_dir = os.path.join(folder, args.best_dir)
    os.makedirs(best_dir, exist_ok=True)

    # 1) 全量盘点
    inventory = []
    for row in rows:
        sub = row.get("目录", "")
        if not sub:
            continue
        sub_path = os.path.join(folder, sub)
        if not os.path.isdir(sub_path):
            continue
        for name in sorted(os.listdir(sub_path)):
            if not name.lower().endswith(EXTS):
                continue
            path = os.path.join(sub_path, name)
            try:
                w, h = img_size(path)
            except Exception:  # noqa: BLE001
                continue
            inventory.append({
                "POI序号": row["序号"],
                "POI名称": row["名称"],
                "文件名": name,
                "路径": os.path.join(sub, name),
                "宽": w, "高": h,
                "像素数": w * h,
                "体积KB": os.path.getsize(path) // 1024,
                "宽度对应A4插图dpi": round(w / (16 / 2.54)) if w else 0,
            })

    inv_path = os.path.join(folder, "photo_inventory.csv")
    if inventory:
        with open(inv_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(inventory[0].keys()))
            writer.writeheader()
            writer.writerows(inventory)

    # 2) 每个 POI 挑像素最多的
    by_poi: dict[str, list[dict]] = {}
    for item in inventory:
        by_poi.setdefault(item["POI序号"], []).append(item)

    report = []
    for row in rows:
        seq = row["序号"]
        cands = by_poi.get(seq, [])
        if not cands:
            continue
        best = max(cands, key=photo_rank)
        reason = []
        if best["宽"] >= best["高"] * 0.95:
            reason.append("横构图")
        if os.path.splitext(best["文件名"])[0][:3].isdigit() and int(os.path.splitext(best["文件名"])[0][:3]) <= 2:
            reason.append("相册靠前")
        if best["宽"] < args.min_width:
            report.append({
                "序号": seq, "名称": row["名称"], "入选文件": "",
                "尺寸": f"{best['宽']}x{best['高']}",
                "峰值尺寸": f"{best['宽']}x{best['高']}",
                "入选理由": "低于最小宽度门槛",
                "达标本": "否",
            })
            continue
        src = os.path.join(folder, best["路径"])
        ext = os.path.splitext(best["文件名"])[1].lower()
        dst = os.path.join(best_dir, f"{seq}_{safe(row['名称'])}{ext}")
        shutil.copyfile(src, dst)
        report.append({
            "序号": seq,
            "名称": row["名称"],
            "入选文件": os.path.basename(dst),
            "尺寸": f"{best['宽']}x{best['高']}",
            "峰值尺寸": f"{best['宽']}x{best['高']}",
            "入选理由": "+".join(reason) or "最高像素",
            "达标本": "是",
        })

    rpath = os.path.join(best_dir, "_report.csv")
    if report:
        with open(rpath, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(report[0].keys()))
            writer.writeheader()
            writer.writerows(report)

    # 3) 汇总
    picked = [r for r in report if r["达标本"] == "是"]
    widths = [int(r["尺寸"].split("x")[0]) for r in picked]
    print(f"盘点 {len(inventory)} 张图，覆盖 {len(report)} 个 POI")
    print(f"入选 {len(picked)} 张 -> {best_dir}")
    if widths:
        print(f"入选图宽度：最小 {min(widths)} / 中位 {int(statistics.median(widths))} / 最大 {max(widths)}")
        for th, label in ((1890, "A4 16cm @300dpi"), (1260, "A4 16cm @200dpi"), (945, "A4 16cm @150dpi")):
            n = sum(1 for w in widths if w >= th)
            print(f"  达到 {label}：{n}/{len(widths)}")
    print(f"清单：{inv_path}")
    print(f"入选清单：{rpath}")


if __name__ == "__main__":
    main()
