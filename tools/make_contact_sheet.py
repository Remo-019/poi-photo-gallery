#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把采集到的照片拼成总览图（contact sheet），每店并排显示前几张。

默认每个 POI 显示 3 张：只放一张的话，挑中菜品特写就整格作废；并排三张时
同一行里就能看到备选，人工过一遍即可定位门头照。

用法：
    python make_contact_sheet.py <抓取目录>
    python make_contact_sheet.py ./poi_photos_北京 --per-poi 3 --per-page 24
    python make_contact_sheet.py ./poi_photos_北京/best --flat

输出：<抓取目录>/contact_sheet.jpg；POI 较多时按页输出 contact_sheet_p01.jpg 等。
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

EXTS = (".jpg", ".jpeg", ".png", ".webp")

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


def photo_rank(item: dict) -> tuple:
    """挑图排序键：先按像素分档，同档内优先横构图与相册靠前的图。

    门头照通常是横构图且多排在相册前几张；菜品、菜单、顾客照多为竖构图或
    排在后面。像素按 25 万（约 500x500）为一档，只在同档内用构图与序号加权，
    不为了横图牺牲清晰度。

    注意：本函数是 poi_core.photo_rank 的独立副本（tools 脚本可单独拷走用，
    不 import poi_core）。改排序规则时三处必须同步：poi_core.py / select_best.py /
    make_contact_sheet.py。
    """
    w, h = item.get("宽") or 0, item.get("高") or 0
    landscape = 1 if w >= h * 0.95 else 0
    try:
        idx = int(os.path.splitext(str(item.get("文件名", "")))[0][:3])
    except (ValueError, TypeError):
        idx = 99
    early = 1 if idx <= 2 else 0
    return (w * h // 250000, landscape, early, w * h, item.get("体积KB", 0))


def load_rows(folder: str) -> list[dict]:
    path = os.path.join(folder, "manifest.csv")
    if not os.path.exists(path):
        sys.exit(f"找不到清单：{path}")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.DictReader(fh) if r.get("目录")]


def pick_images(folder: str, dirname: str, count: int) -> list[dict]:
    """按 photo_rank 取某个 POI 目录下最值得看的 count 张。"""
    full = os.path.join(folder, dirname)
    if not os.path.isdir(full):
        return []
    items = []
    for name in sorted(n for n in os.listdir(full) if n.lower().endswith(EXTS)):
        path = os.path.join(full, name)
        try:
            with Image.open(path) as im:
                w, h = im.size
            size_kb = os.path.getsize(path) // 1024
        except Exception:  # noqa: BLE001
            continue
        items.append({"文件名": name, "路径": path, "宽": w, "高": h, "体积KB": size_kb})
    items.sort(key=photo_rank, reverse=True)
    return items[:max(1, count)]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 POI 照片总览图")
    parser.add_argument("folder", help="采集输出目录（需含 manifest.csv）")
    parser.add_argument("--cols", type=int, default=4, help="列数，默认 4")
    parser.add_argument("--width", type=int, default=420, help="单格宽，默认 420")
    parser.add_argument("--per-poi", type=int, default=3,
                        help="每个 POI 显示几张，默认 3（1 即每店一张）")
    parser.add_argument("--per-page", type=int, default=24,
                        help="每页几个 POI，默认 24；0 表示不分页")
    parser.add_argument("--out", default=None, help="输出文件名，默认 contact_sheet.jpg")
    parser.add_argument("--flat", action="store_true",
                        help="把 folder 当作扁平图片目录（如 best/），文件名即标签")
    args = parser.parse_args()

    if args.flat:
        folder = os.path.abspath(args.folder)
        names = sorted(n for n in os.listdir(folder)
                       if n.lower().endswith(EXTS))
        if not names:
            sys.exit("目录里没有图片")
        rows = [{"序号": str(i), "名称": os.path.splitext(n)[0], "目录": n,
                 "分类": "", "地址": "", "已下载": "1", "照片数": "1", "抓取日期": ""}
                for i, n in enumerate(names, 1)]
        get_images = lambda d, n: [{"文件名": d, "路径": os.path.join(folder, d),
                                    "宽": 0, "高": 0, "体积KB": 0}]  # noqa: E731
        per_poi = 1
    else:
        folder = os.path.abspath(args.folder)
        rows = load_rows(folder)
        per_poi = max(1, args.per_poi)
        get_images = lambda d, n: pick_images(folder, d, n)  # noqa: E731

    if not rows:
        sys.exit("清单里没有已下载的 POI")

    cols = max(1, args.cols)
    cell_w = args.width
    inner = 6
    thumb_w = max(40, (cell_w - (per_poi - 1) * inner) // per_poi)
    img_h = max(30, int(thumb_w * 0.75))
    label_h = 66
    cell_h = img_h + label_h
    gap = 12
    pad = 24
    header_h = 60
    per_page = args.per_page if args.per_page and args.per_page > 0 else len(rows)

    title_font = load_font(24)
    name_font = load_font(14)
    meta_font = load_font(12)

    total_pages = ceil(len(rows) / per_page)
    written = []

    for page in range(total_pages):
        chunk = rows[page * per_page:(page + 1) * per_page]
        n_rows = ceil(len(chunk) / cols)
        canvas_w = pad * 2 + cols * cell_w + (cols - 1) * gap
        canvas_h = pad * 2 + header_h + n_rows * cell_h + (n_rows - 1) * gap
        canvas = Image.new("RGB", (canvas_w, canvas_h), (250, 250, 248))
        draw = ImageDraw.Draw(canvas)

        title = f"{os.path.basename(folder.rstrip(os.sep))}  共 {len(rows)} 个 POI，每店 {per_poi} 张"
        if total_pages > 1:
            title += f"（第 {page + 1}/{total_pages} 页）"
        draw.text((pad, pad + 14), title, fill=(30, 30, 30), font=title_font)

        ok = 0
        for idx, row in enumerate(chunk):
            r, c = divmod(idx, cols)
            x0 = pad + c * (cell_w + gap)
            y0 = pad + header_h + r * (cell_h + gap)

            images = get_images(row["目录"], per_poi)
            for j in range(per_poi):
                x = x0 + j * (thumb_w + inner)
                draw.rectangle([x, y0, x + thumb_w, y0 + img_h], fill=(232, 232, 228))
                if j >= len(images):
                    continue
                try:
                    with Image.open(images[j]["路径"]) as src:
                        canvas.paste(cover_resize(src, thumb_w, img_h), (x, y0))
                    ok += 1
                    if images[j]["宽"]:
                        tag = f'{images[j]["宽"]}x{images[j]["高"]}'
                        tw = meta_font.getlength(tag)
                        draw.rectangle([x, y0 + img_h - 16, x + tw + 7, y0 + img_h],
                                       fill=(35, 35, 35))
                        draw.text((x + 3, y0 + img_h - 15), tag,
                                  fill=(255, 255, 255), font=meta_font)
                except Exception as exc:  # noqa: BLE001
                    draw.text((x + 4, y0 + 4), "读取失败", fill=(150, 60, 60), font=meta_font)
                    print(f"  ! {row['名称']}: {exc}")

            draw.rectangle([x0, y0 + img_h, x0 + cell_w, y0 + cell_h], fill=(255, 255, 255))
            name = row["名称"]
            while name_font.getlength(name) > cell_w - 12 and len(name) > 4:
                name = name[:-1]
            draw.text((x0 + 6, y0 + img_h + 3), name, fill=(20, 20, 20), font=name_font)

            cat = (row.get("分类") or "").split(";")[-1]
            addr = row.get("地址") or ""
            line2 = f"{row['序号']}. {cat} {addr}"
            while meta_font.getlength(line2) > cell_w - 12 and len(line2) > 4:
                line2 = line2[:-1]
            draw.text((x0 + 6, y0 + img_h + 24), line2, fill=(110, 110, 110), font=meta_font)

            badge = (row.get("抓取日期") or "").strip()
            badge += f'  {row.get("已下载", "0")}/{row.get("照片数", "0")} 张'
            draw.text((x0 + 6, y0 + img_h + 44), badge.strip(),
                      fill=(150, 150, 150), font=meta_font)

        if args.out and total_pages == 1:
            out_path = os.path.abspath(args.out)
        elif args.out:
            root, ext = os.path.splitext(os.path.abspath(args.out))
            out_path = f"{root}_p{page + 1:02d}{ext or '.jpg'}"
        elif total_pages == 1:
            out_path = os.path.join(folder, "contact_sheet.jpg")
        else:
            out_path = os.path.join(folder, f"contact_sheet_p{page + 1:02d}.jpg")

        canvas.save(out_path, quality=88, optimize=True)
        written.append(out_path)
        if total_pages > 1:
            print(f"  第 {page + 1}/{total_pages} 页：{out_path}  贴图 {ok}")
        else:
            print(f"总览图已生成：{out_path}")
            print(f"  尺寸 {canvas_w}x{canvas_h}，贴图 {ok} 张，覆盖 {len(chunk)} 个 POI")

    if total_pages > 1:
        print(f"共 {total_pages} 页，合计贴图覆盖 {len(rows)} 个 POI")


if __name__ == "__main__":
    main()
