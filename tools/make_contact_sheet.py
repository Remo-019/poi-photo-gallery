#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把采集到的照片拼成一张总览图（contact sheet）。

用法：
    python make_contact_sheet.py <抓取目录>
    python make_contact_sheet.py ./poi_photos_北京 --cols 6 --width 300

输出：<抓取目录>/contact_sheet.jpg
依赖：Pillow（pip install pillow）
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from math import ceil

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("需要 Pillow：pip install pillow")

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def cover_resize(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """等比缩放后居中裁切，铺满目标框。"""
    img = img.convert("RGB")
    w, h = img.size
    scale = max(box_w / w, box_h / h)
    img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    left = (img.width - box_w) // 2
    top = (img.height - box_h) // 2
    return img.crop((left, top, left + box_w, top + box_h))


def load_rows(folder: str) -> list[dict]:
    path = os.path.join(folder, "manifest.csv")
    if not os.path.exists(path):
        sys.exit(f"找不到清单：{path}")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.DictReader(fh) if r.get("目录")]


def first_image(folder: str, dirname: str) -> str | None:
    full = os.path.join(folder, dirname)
    if not os.path.isdir(full):
        return None
    names = sorted(n for n in os.listdir(full) if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
    return os.path.join(full, names[0]) if names else None


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 POI 照片总览图")
    parser.add_argument("folder", help="采集输出目录（需含 manifest.csv）")
    parser.add_argument("--cols", type=int, default=5, help="列数，默认 5")
    parser.add_argument("--width", type=int, default=320, help="单格宽，默认 320")
    parser.add_argument("--out", default=None, help="输出文件名，默认 contact_sheet.jpg")
    parser.add_argument("--flat", action="store_true",
                        help="把 folder 当作扁平图片目录（如 best/），文件名即标签")
    args = parser.parse_args()

    if args.flat:
        folder = os.path.abspath(args.folder)
        names = sorted(n for n in os.listdir(folder)
                       if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
        if not names:
            sys.exit("目录里没有图片")
        rows = [{"序号": str(i), "名称": os.path.splitext(n)[0], "目录": n,
                 "分类": "", "地址": "", "已下载": "1", "照片数": "1"}
                for i, n in enumerate(names, 1)]
        get_image = lambda d: os.path.join(folder, d)  # noqa: E731
    else:
        rows = load_rows(args.folder)
        get_image = lambda d: first_image(args.folder, d)  # noqa: E731

    if not rows:
        sys.exit("清单里没有已下载的 POI")

    cols = max(1, args.cols)
    cell_w = args.width
    img_h = int(cell_w * 0.75)
    label_h = 74
    cell_h = img_h + label_h
    gap = 12
    pad = 24
    header_h = 64

    n_rows = ceil(len(rows) / cols)
    canvas_w = pad * 2 + cols * cell_w + (cols - 1) * gap
    canvas_h = pad * 2 + header_h + n_rows * cell_h + (n_rows - 1) * gap

    canvas = Image.new("RGB", (canvas_w, canvas_h), (250, 250, 248))
    draw = ImageDraw.Draw(canvas)

    title_font = load_font(26)
    name_font = load_font(15)
    meta_font = load_font(13)

    title = f"{os.path.basename(folder)}  共 {len(rows)} 个 POI"
    draw.text((pad, pad + 12), title, fill=(30, 30, 30), font=title_font)

    ok = 0
    for idx, row in enumerate(rows):
        r, c = divmod(idx, cols)
        x = pad + c * (cell_w + gap)
        y = pad + header_h + r * (cell_h + gap)

        path = get_image(row["目录"])
        draw.rectangle([x, y, x + cell_w, y + img_h], fill=(232, 232, 228))
        if path:
            try:
                with Image.open(path) as src:
                    canvas.paste(cover_resize(src, cell_w, img_h), (x, y))
                ok += 1
            except Exception as exc:  # noqa: BLE001
                draw.text((x + 8, y + 8), "读取失败", fill=(150, 60, 60), font=meta_font)
                print(f"  ! {row['名称']}: {exc}")

        draw.rectangle([x, y + img_h, x + cell_w, y + cell_h], fill=(255, 255, 255))
        name = row["名称"]
        while name_font.getlength(name) > cell_w - 12 and len(name) > 4:
            name = name[:-1]
        draw.text((x + 6, y + img_h + 6), name, fill=(20, 20, 20), font=name_font)

        cat = (row.get("分类") or "").split(";")[-1]
        addr = row.get("地址") or ""
        line2 = f"{row['序号']}. {cat} {addr}"
        while meta_font.getlength(line2) > cell_w - 12 and len(line2) > 4:
            line2 = line2[:-1]
        draw.text((x + 6, y + img_h + 30), line2, fill=(110, 110, 110), font=meta_font)

        photos = f"{row.get('已下载','0')}/{row.get('照片数','0')} 张"
        draw.text((x + 6, y + img_h + 50), photos, fill=(150, 150, 150), font=meta_font)

    if args.out:
        out_path = os.path.abspath(args.out)
    else:
        out_path = os.path.join(folder, "contact_sheet.jpg")
    canvas.save(out_path, quality=88, optimize=True)
    print(f"总览图已生成：{out_path}")
    print(f"  尺寸 {canvas_w}x{canvas_h}，成功贴图 {ok}/{len(rows)}")


if __name__ == "__main__":
    main()
