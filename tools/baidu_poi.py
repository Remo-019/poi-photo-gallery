# -*- coding: utf-8 -*-
"""百度地图 POI 照片采集（免费，不需要任何 API Key）。

原理
----
百度官方地点检索接口不给图片 URL（只给张数），而网页版还有两条不用鉴权的接口：

1. 搜索  https://map.baidu.com/?qt=s&wd=<关键词>&c=<城市码>&pn=<页码>
   → JSON，content[] 里每个 POI 有 uid / name / addr / tel / geo

2. 详情  https://map.baidu.com/?qt=inf&uid=<uid>
   → content.ext.detail_info.vs_content.invisible.bigdata.photo_album[]
     每个元素含 url，是真实图片地址

取原图
------
相册 URL 带 `x-bce-process=image/resize,.../quality,q_80` 之类的处理参数，
那是**缩略图**。直接去掉整个 query 才是真实原图。
（注意：反向加 resize,w_2000 会返回 2000px，但那是 CDN 插值放大，细节不增）

需要过滤
--------
- 全景图：host 是 *.bdimg.com 且带 qt=pr3d，去掉参数会 404，必须跳过
- 小程序图标、公告图等杂项
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 图片 CDN 白名单：只收这几家的，避免抓到图标/广告图
PIC_HOSTS = ("poi-pic-gz.cdn.bcebos.com", "poi-pic.cdn.bcebos.com", "lbsugc.cdn.bcebos.com")
# 携程图（百度部分 POI 的封面来自携程），可升级尺寸
CTRIP_HOSTS = ("c-ctrip.com",)


def _get(url: str, timeout: int = 25, retries: int = 3) -> bytes:
    ctx = ssl.create_default_context()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Referer": "https://map.baidu.com/"})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"请求失败 {url} ({last})")


def _json(url: str) -> dict:
    return json.loads(_get(url).decode("utf-8", "replace"))


def search(keyword: str, city_code: int | str = 132, max_poi: int = 40,
           pages: int = 12, delay: float = 0.8, log=print, should_stop=None) -> list[dict]:
    """按关键词 + 城市码搜索 POI。city_code 132 = 重庆。"""
    out: list[dict] = []
    for pn in range(pages):
        if should_stop and should_stop():
            return out
        url = ("https://map.baidu.com/?qt=s&wd=" + urllib.parse.quote(keyword)
               + f"&c={city_code}&src=0&pn={pn}&sug=0&l=12&from=webmap")
        try:
            d = _json(url)
        except Exception as exc:  # noqa: BLE001
            log(f"  ! 第 {pn} 页失败：{exc}")
            break
        content = d.get("content") or []
        if not content:
            break
        for p in content:
            geo = p.get("geo") or ""
            lng = lat = ""
            m = re.search(r"\|(-?[\d.]+),(-?[\d.]+);", geo)
            if m:
                # 百度 geo 是墨卡托，这里只留原始值，换算交给下游
                lng, lat = m.group(1), m.group(2)
            out.append({
                "uid": p.get("uid", ""),
                "name": p.get("name", ""),
                "addr": p.get("addr", ""),
                "tel": p.get("tel", ""),
                "tag": p.get("std_tag", ""),
                "area": p.get("area_name", ""),
                "lng": lng, "lat": lat,
            })
            if len(out) >= max_poi:
                return out
        log(f"  第 {pn + 1} 页：累计 {len(out)} 个")
        time.sleep(delay)
    return out


def detail_images(uid: str, delay: float = 0) -> list[str]:
    """取某个 POI 的相册原图地址（已去参数、已过滤全景）。"""
    if delay:
        time.sleep(delay)
    try:
        d = _json(f"https://map.baidu.com/?qt=inf&uid={uid}")
    except Exception:  # noqa: BLE001
        return []
    try:
        di = d["content"]["ext"]["detail_info"]
    except (KeyError, TypeError):
        return []

    urls: list[str] = []

    album = (((di.get("vs_content") or {}).get("invisible") or {})
             .get("bigdata") or {}).get("photo_album") or []
    for item in album:
        u = item.get("url") if isinstance(item, dict) else None
        if u:
            urls.append(u)

    # 封面兜底
    for key in ("image", "image11"):
        if isinstance(di.get(key), str):
            urls.append(di[key])

    out: list[str] = []
    for u in urls:
        clean = normalize(u)
        if clean and clean not in out:
            out.append(clean)
    return out


def normalize(url: str) -> str | None:
    """去掉缩略图参数，返回真实原图；无法处理则返回 None。"""
    host = urllib.parse.urlparse(url).netloc
    # 全景图：去掉参数会 404，直接放弃
    if "qt=pr3d" in url:
        return None
    if any(h in host for h in PIC_HOSTS):
        return url.split("?")[0]
    if any(h in host for h in CTRIP_HOSTS):
        # 携程图：_R_640_10000_Q90 是尺寸段，换成大图
        return re.sub(r"_R_\d+_\d+_Q\d+", "_R_2000_10000_Q90", url)
    return None


def image_size(data: bytes) -> tuple[int, int]:
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            import struct
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
        if data[:2] == b"\xff\xd8":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                mk = data[i + 1]
                if mk in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                    return w, h
                i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    except Exception:  # noqa: BLE001
        pass
    return 0, 0


def safe_name(text: str, limit: int = 40) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (text or "unnamed").strip())
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"[_\s.]+$", "", text)[:limit] or "unnamed"


def run(keyword: str, city_code: int | str, max_poi: int, out_dir: str,
        max_photos: int = 3, delay: float = 0.8, log=print, should_stop=None) -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    pois = search(keyword, city_code, max_poi, delay=delay, log=log, should_stop=should_stop)
    if not pois:
        log("没有搜到 POI")
        return []

    rows = []
    for i, p in enumerate(pois, 1):
        if should_stop and should_stop():
            break
        urls = detail_images(p["uid"], delay=delay)[:max_photos]
        folder = os.path.join(out_dir, f"{i:03d}_{safe_name(p['name'])}__{p['uid']}")
        log(f"[{i}/{len(pois)}] {p['name']}  相册 {len(urls)} 张")
        saved = 0
        best = (0, 0)
        if urls:
            os.makedirs(folder, exist_ok=True)
        for k, u in enumerate(urls, 1):
            try:
                data = _get(u)
                if len(data) < 2000:
                    continue
                ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
                with open(os.path.join(folder, f"{k:02d}{ext}"), "wb") as fh:
                    fh.write(data)
                w, h = image_size(data)
                if w > best[0]:
                    best = (w, h)
                saved += 1
                log(f"      ✓ {k:02d}{ext}  {w}x{h}  {len(data)//1024} KB")
            except Exception as exc:  # noqa: BLE001
                log(f"      ✗ 第 {k} 张失败：{exc}")
            time.sleep(delay / 2)
        rows.append({
            "序号": i, "名称": p["name"], "UID": p["uid"], "地址": p["addr"],
            "电话": p["tel"], "分类": p["tag"], "区域": p["area"],
            "照片数": len(urls), "已下载": saved,
            "最大尺寸": f"{best[0]}x{best[1]}" if best[0] else "",
            "目录": os.path.basename(folder) if saved else "",
            "来源": "百度地图 POI 相册",
            "来源URL": f"https://map.baidu.com/?qt=inf&uid={p['uid']}",
        })

    path = os.path.join(out_dir, "manifest_baidu.csv")
    if rows:
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        log(f"清单：{path}")
    return rows


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="百度地图 POI 照片采集（免费）")
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--city-code", default="132", help="城市码，重庆=132")
    ap.add_argument("--max-poi", type=int, default=20)
    ap.add_argument("--max-photos", type=int, default=3)
    ap.add_argument("--out", default="./baidu_poi")
    ap.add_argument("--delay", type=float, default=0.8)
    a = ap.parse_args()
    run(a.keyword, a.city_code, a.max_poi, a.out, a.max_photos, a.delay)
