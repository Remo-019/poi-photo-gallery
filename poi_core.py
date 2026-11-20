# -*- coding: utf-8 -*-
"""地图 POI 照片采集 —— 核心逻辑。

界面和命令行共用这一份实现：
- 高德：官方 v5 接口，show_fields=photos 直接返回图片 URL
- 图片：高德 CDN 是阿里云 OSS，默认给 500px 缩略图，
        必须带 limit_1 才是真实原图（不带会被放大成假高清）
- 百度：官方接口不给图片地址，只能解析网页详情页
"""

from __future__ import annotations

import csv
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

IMG_EXT_RE = re.compile(r"^https?://\S+\.(?:jpg|jpeg|png|webp|bmp|gif)(?:\?\S*)?$", re.I)
IMG_HOST_HINTS = (
    "is.autonavi.com", "aos-comment.amap.com", "store.is.autonavi.com",
    "bdimg.com", "hiphotos.baidu.com", "maponline", "gss0.baidu.com", "ugc",
)
IMG_DENY_RE = re.compile(
    r"(qt=vtile|qt=cen|qt=poi&|/tile/|staticimage|staticmap|/marker[s]?/|/icon[s]?/|logo\.)",
    re.I,
)
IMG_SRC_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)
OSS_RESIZE_HOSTS = ("store.is.autonavi.com", "is.autonavi.com", "aos-comment.amap.com")

IMG_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")


class StopRequested(Exception):
    """用户在界面上点了停止。"""


# ------------------------------------------------------------------ 基础工具


def http_get(url: str, headers: dict | None = None, timeout: int = 25, retries: int = 3,
             should_stop=None) -> bytes:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    last = None
    for attempt in range(retries):
        if should_stop and should_stop():
            raise StopRequested()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read()
        except StopRequested:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"请求失败：{url}（{last}）")


def http_json(url: str, headers: dict | None = None, should_stop=None) -> dict:
    return json.loads(http_get(url, headers=headers, should_stop=should_stop).decode("utf-8", "replace"))


def upscale_url(url: str, width: int = 5000) -> str:
    """取高德图片的真实原图。

    高德 CDN 默认只给 500px 缩略图。直接指定大 w 会得到**放大后的假像素**
    （原图 600x400，w_2000 会返回 2000x1333，细节一点没多）。
    必须加 limit_1 禁止放大，返回的才是这张图的真实原始分辨率。
    """
    if width <= 0:
        return url
    host = urllib.parse.urlparse(url).netloc
    if not any(host.endswith(h) for h in OSS_RESIZE_HOSTS):
        return url
    if "x-oss-process" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}x-oss-process=image/resize,w_{int(width)},limit_1"


def image_size(data: bytes) -> tuple[int, int]:
    """不依赖 Pillow 读取 JPEG / PNG 尺寸。"""
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
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                    return w, h
                seg = int.from_bytes(data[i + 2:i + 4], "big")
                i += 2 + seg
    except Exception:  # noqa: BLE001
        pass
    return 0, 0


def file_image_size(path: str) -> tuple[int, int]:
    try:
        with open(path, "rb") as fh:
            return image_size(fh.read(65536))
    except OSError:
        return 0, 0


def guess_ext(data: bytes, url: str) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    m = re.search(r"\.(jpg|jpeg|png|webp)(?:\?|$)", url, re.I)
    return "." + m.group(1).lower().replace("jpeg", "jpg") if m else ".jpg"


def safe_name(text: str, limit: int = 40) -> str:
    text = (text or "unnamed").strip()
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[_\s.]+$", "", text)
    return text[:limit] or "unnamed"


def dedupe(items):
    seen, out = set(), []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def collect_image_urls(obj) -> list[str]:
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            s = node.strip()
            if not s.lower().startswith("http") or IMG_DENY_RE.search(s):
                return
            if IMG_EXT_RE.match(s) or any(h in s for h in IMG_HOST_HINTS):
                if len(s) > 25:
                    found.append(s)

    walk(obj)
    return dedupe(found)


def collect_urls_from_text(text: str) -> list[str]:
    cand = re.findall(r'https?://[^\s"\'\\<>]+', text)
    return dedupe([c for c in cand
                   if not IMG_DENY_RE.search(c)
                   and (IMG_EXT_RE.match(c) or any(h in c for h in IMG_HOST_HINTS))])


# ------------------------------------------------------------------ 高德


MUNICIPALITIES = ("北京市", "上海市", "天津市", "重庆市")


def normalize_region(city: str) -> str:
    """把「广东省深圳市」这类写法收敛成高德能识别的「深圳市」。

    坑：高德 region 参数不接受「省+市」组合，传「广东省深圳市」会**静默退化成
    全国搜索**，返回异地城市的结果且不报错。必须去掉省级前缀。
    """
    c = (city or "").strip()
    if not c:
        return c
    for m in MUNICIPALITIES:
        if c.startswith(m) and len(c) > len(m):
            return c[len(m):]
    m = re.match(r"^.+?(省|自治区|特别行政区)", c)
    if m and m.end() < len(c):
        return c[m.end():]
    return c


def region_core(city: str) -> str:
    """取城市核心词用于结果校验，如「深圳市」→「深圳」。"""
    c = normalize_region(city)
    return re.sub(r"(市辖区|市|地区|自治州|盟|区|县)$", "", c)


def amap_search(key: str, keywords: str, city: str, types: str | None, max_poi: int,
                page_size: int = 25, delay: float = 0.4, log=print, should_stop=None) -> list[dict]:
    """v5 搜索，带 photos 字段。max_poi<=0 表示不限制（翻到没有结果为止）。"""
    region = normalize_region(city)
    if region != city:
        log(f"  城市名已规范化为「{region}」（高德不认「省+市」写法）")
    results: list[dict] = []
    page = 1
    while page <= 100:
        if max_poi and len(results) >= max_poi:
            break
        params = {
            "key": key, "keywords": keywords, "region": region, "city_limit": "true",
            "show_fields": "photos,business", "page_size": str(min(page_size, 25)),
            "page_num": str(page), "output": "json",
        }
        if types:
            params["types"] = types
        url = "https://restapi.amap.com/v5/place/text?" + urllib.parse.urlencode(params)
        data = http_json(url, should_stop=should_stop)

        if str(data.get("status")) != "1":
            info = data.get("info")
            code = data.get("infocode")
            guess = ""
            if code in ("10003", "10001", "10009"):
                guess = "（Key 无效或不是「Web服务」类型）"
            elif code in ("10044", "10045", "20000"):
                guess = "（今日配额已用尽，次日 0 点重置）"
            elif code == "20001":
                guess = "（请求过快，把间隔调大）"
            raise RuntimeError(f"高德接口报错：{info}（infocode={code}）{guess}")

        pois = data.get("pois") or []
        if not pois:
            break

        for poi in pois:
            photos = [p.get("url") for p in (poi.get("photos") or []) if isinstance(p, dict) and p.get("url")]
            loc = poi.get("location", "") if isinstance(poi.get("location"), str) else ""
            lng, lat = ("", "")
            if "," in loc:
                lng, lat = [x.strip() for x in loc.split(",", 1)]
            results.append({
                "poi_id": poi.get("id", ""),
                "name": poi.get("name", ""),
                "address": poi.get("address") if isinstance(poi.get("address"), str) else "",
                "typecode": poi.get("typecode", ""),
                "type_name": poi.get("type", "") if isinstance(poi.get("type"), str) else "",
                "lng": lng, "lat": lat, "location": loc,
                "tel": poi.get("tel", "") if isinstance(poi.get("tel"), str) else "",
                "adname": poi.get("adname", "") if isinstance(poi.get("adname"), str) else "",
                "cityname": poi.get("cityname", "") if isinstance(poi.get("cityname"), str) else "",
                "photos": dedupe(photos),
            })
            if max_poi and len(results) >= max_poi:
                break
        log(f"  第 {page} 页：累计 {len(results)} 个 POI")
        page += 1
        time.sleep(delay)

    # 结果校验：防止城市没被识别、返回其它地区的数据
    core = region_core(city)
    if results and core:
        hit = sum(1 for it in results if core in it.get("adname", "") or core in it.get("cityname", ""))
        if hit / len(results) < 0.5:
            sample = "、".join(it["name"] for it in results[:3])
            raise RuntimeError(
                f"高德没有识别城市「{city}」，返回的结果像是其它地区（例如：{sample}）。\n"
                f"请把城市改成不带省份的写法，比如「深圳市」；或直接填行政区划代码（深圳市是 440300）。"
            )

    # 搜索没带图的，补一次详情
    for item in results:
        if item["photos"] or not item["poi_id"]:
            continue
        if should_stop and should_stop():
            raise StopRequested()
        try:
            params = {"key": key, "id": item["poi_id"], "show_fields": "photos", "output": "json"}
            detail = http_json("https://restapi.amap.com/v5/place/detail?" + urllib.parse.urlencode(params),
                               should_stop=should_stop)
            pois = detail.get("pois") or []
            if pois:
                item["photos"] = dedupe([p.get("url") for p in (pois[0].get("photos") or [])
                                         if isinstance(p, dict) and p.get("url")])
        except StopRequested:
            raise
        except Exception as exc:  # noqa: BLE001
            log(f"  ! 详情补充失败 {item['name']}：{exc}")
        time.sleep(delay)

    return results


# ------------------------------------------------------------------ 百度
#
# 百度官方地点检索接口只返回 image_num（张数）不给图片地址，但它网页版有两条
# 免鉴权接口可用：
#   ?qt=s&wd=<关键词>            → POI 列表（含 uid / name / addr / tel）
#   ?qt=inf&uid=<uid>            → 详情，photo_album[] 里是图片地址
# 相册地址带 x-bce-process=... 缩略图参数，**整段 query 去掉才是原图**。
# （反过来加 resize,w_2000 会得到 2000px，但那是插值放大，细节不增）


def _baidu_get(url: str, should_stop=None) -> bytes:
    try:
        return http_get(url, headers={"Referer": "https://map.baidu.com/"}, should_stop=should_stop)
    except StopRequested:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(str(exc)) from exc


def normalize_baidu_url(url: str) -> str | None:
    """百度相册 URL 去参数取原图；全景图和杂项返回 None。"""
    if not url or "qt=pr3d" in url:
        return None
    host = urllib.parse.urlparse(url).netloc
    if any(h in host for h in ("poi-pic-gz.cdn.bcebos.com", "poi-pic.cdn.bcebos.com",
                               "lbsugc.cdn.bcebos.com")):
        return url.split("?")[0]
    if "c-ctrip.com" in host:
        return re.sub(r"_R_\d+_\d+_Q\d+", "_R_2000_10000_Q90", url)
    return None


def baidu_search(keyword: str, city: str, max_poi: int, delay: float = 0.8,
                 log=print, should_stop=None) -> list[dict]:
    """百度地图搜索（免费，无需 AK）。city 拼进关键词里即可。max_poi<=0 表示不限制。"""
    wd = f"{city} {keyword}".strip() if city and city not in keyword else keyword
    results: list[dict] = []
    for pn in range(200):
        if max_poi and len(results) >= max_poi:
            break
        if should_stop and should_stop():
            raise StopRequested()
        url = ("https://map.baidu.com/?qt=s&wd=" + urllib.parse.quote(wd)
               + f"&src=0&pn={pn}&sug=0&l=12&from=webmap")
        try:
            data = json.loads(_baidu_get(url, should_stop).decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            log(f"  ! 百度第 {pn} 页失败：{exc}")
            break
        content = data.get("content") or []
        if not content:
            break
        for p in content:
            name = p.get("name", "")
            addr = p.get("addr", "")
            # 过滤明显跑偏的结果（既不含城市也不含关键词）
            blob = f"{name}{addr}"
            if city and city[:2] not in blob and keyword[:2] not in blob:
                continue
            results.append({
                "poi_id": p.get("uid", ""),
                "name": name,
                "address": addr,
                "typecode": p.get("std_tag", ""),
                "type_name": p.get("std_tag", ""),
                "lng": "", "lat": "",
                "location": "",
                "tel": p.get("tel", ""),
                "photos": [],
            })
            if max_poi and len(results) >= max_poi:
                break
        log(f"  百度第 {pn + 1} 页：累计 {len(results)} 个")
        if max_poi and len(results) >= max_poi:
            break
        time.sleep(delay)

    # 逐个取相册
    for item in results:
        if should_stop and should_stop():
            raise StopRequested()
        item["photos"] = baidu_photo_urls(item["poi_id"], should_stop=should_stop)
        time.sleep(delay)
    return results


def baidu_photo_urls(uid: str, should_stop=None) -> list[str]:
    if not uid:
        return []
    try:
        d = json.loads(_baidu_get(f"https://map.baidu.com/?qt=inf&uid={uid}", should_stop)
                       .decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return []
    try:
        di = d["content"]["ext"]["detail_info"]
    except (KeyError, TypeError):
        return []

    raw: list[str] = []
    album = (((di.get("vs_content") or {}).get("invisible") or {})
             .get("bigdata") or {}).get("photo_album") or []
    for it in album:
        if isinstance(it, dict) and it.get("url"):
            raw.append(it["url"])
    for key in ("image", "image11"):
        if isinstance(di.get(key), str):
            raw.append(di[key])

    out: list[str] = []
    for u in raw:
        clean = normalize_baidu_url(u)
        if clean and clean not in out:
            out.append(clean)
    return out


# ------------------------------------------------------------------ 下载


def download_items(items: list[dict], out_dir: str, max_photos: int, width: int, delay: float,
                   log=print, on_progress=None, should_stop=None) -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    rows: list[dict] = []
    total_files = 0

    for seq, item in enumerate(items, 1):
        if should_stop and should_stop():
            raise StopRequested()
        urls = item["photos"] if max_photos <= 0 else item["photos"][:max_photos]
        folder_name = f"{seq:03d}_{safe_name(item['name'])}__{item['poi_id']}"
        folder = os.path.join(out_dir, folder_name)
        log(f"[{seq}/{len(items)}] {item['name']}（{len(urls)} 张）")

        saved = 0
        best_w = best_h = 0
        if urls:
            os.makedirs(folder, exist_ok=True)
            for i, url in enumerate(urls, 1):
                if should_stop and should_stop():
                    raise StopRequested()
                try:
                    real = upscale_url(url, width)
                    data = http_get(real, headers={
                        "Referer": "https://map.baidu.com/" if item.get("source") == "baidu"
                        else "https://www.amap.com/"}, should_stop=should_stop)
                    if len(data) < 2000:
                        continue
                    path = os.path.join(folder, f"{i:02d}{guess_ext(data, real)}")
                    with open(path, "wb") as fh:
                        fh.write(data)
                    w, h = image_size(data)
                    if w > best_w:
                        best_w, best_h = w, h
                    saved += 1
                    total_files += 1
                    log(f"      ✓ {os.path.basename(path)}  {w}x{h}  {len(data)//1024} KB")
                except StopRequested:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log(f"      ✗ 第 {i} 张失败：{exc}")
                time.sleep(delay / 2)

        rows.append({
            "序号": seq, "名称": item["name"], "POI_ID": item["poi_id"],
            "分类": item.get("type_name", ""), "分类码": item["typecode"],
            "地址": item["address"], "电话": item.get("tel", ""),
            "经度": item["lng"], "纬度": item["lat"],
            "坐标": item["location"], "照片数": len(urls), "已下载": saved,
            "最大尺寸": f"{best_w}x{best_h}" if best_w else "",
            "目录": folder_name if saved else "",
        })
        if on_progress:
            on_progress(seq, len(items))

    if rows:
        path = os.path.join(out_dir, "manifest.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        log(f"清单已保存：{path}")
    log(f"完成：{len(items)} 个 POI，下载 {total_files} 张图")
    return rows


# ------------------------------------------------------------------ 历史记录

RUN_DIR_RE = re.compile(r"^(\d{8})_(\d{6})_(.+)$")


def scan_history(root: str) -> list[dict]:
    """扫描输出根目录，列出每次抓取（目录名形如 20261120_161344_北京市_医院）。"""
    if not os.path.isdir(root):
        return []
    items: list[dict] = []
    for name in os.listdir(root):
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue
        m = RUN_DIR_RE.match(name)
        if not m:
            continue
        date, clock, tag = m.groups()
        row = {
            "目录": full,
            "名称": name,
            "时间": f"{date[:4]}-{date[4:6]}-{date[6:]} {clock[:2]}:{clock[2:4]}:{clock[4:]}",
            "标签": tag.replace("_", " / "),
            "来源": {},
            "机构数": 0,
            "图片数": 0,
        }
        for src in ("高德", "百度"):
            man = os.path.join(full, src, "manifest.csv")
            if not os.path.exists(man):
                continue
            try:
                with open(man, encoding="utf-8-sig", newline="") as fh:
                    rows = list(csv.DictReader(fh))
            except Exception:  # noqa: BLE001
                continue
            n_poi = len(rows)
            n_img = 0
            for r in rows:
                try:
                    n_img += int(r.get("已下载") or 0)
                except ValueError:
                    pass
            row["来源"][src] = f"{n_poi} 个 / {n_img} 张"
            row["机构数"] += n_poi
            row["图片数"] += n_img
        items.append(row)
    items.sort(key=lambda x: x["名称"], reverse=True)
    return items


# ------------------------------------------------------------------ 挑最优


def pick_best(out_dir: str, best_name: str = "best", log=print) -> dict:
    from PIL import Image

    manifest = os.path.join(out_dir, "manifest.csv")
    if not os.path.exists(manifest):
        raise RuntimeError("找不到 manifest.csv，请先抓取")
    with open(manifest, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    best_dir = os.path.join(out_dir, best_name)
    os.makedirs(best_dir, exist_ok=True)

    inventory = []
    for row in rows:
        sub = row.get("目录", "")
        sub_path = os.path.join(out_dir, sub)
        if not sub or not os.path.isdir(sub_path):
            continue
        for name in sorted(os.listdir(sub_path)):
            if not name.lower().endswith(IMG_SUFFIXES):
                continue
            path = os.path.join(sub_path, name)
            try:
                with Image.open(path) as im:
                    w, h = im.size
            except Exception:  # noqa: BLE001
                continue
            inventory.append({
                "POI序号": row["序号"], "POI名称": row["名称"], "文件名": name,
                "路径": os.path.join(sub, name), "宽": w, "高": h,
                "像素数": w * h, "体积KB": os.path.getsize(path) // 1024,
                "宽度对应A4插图dpi": round(w / (16 / 2.54)) if w else 0,
            })

    inv_path = os.path.join(out_dir, "photo_inventory.csv")
    if inventory:
        with open(inv_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(inventory[0].keys()))
            writer.writeheader()
            writer.writerows(inventory)

    by_poi: dict[str, list[dict]] = {}
    for item in inventory:
        by_poi.setdefault(item["POI序号"], []).append(item)

    import shutil
    picked = 0
    widths = []
    for row in rows:
        cands = by_poi.get(row["序号"], [])
        if not cands:
            continue
        best = max(cands, key=lambda x: (x["像素数"], x["体积KB"]))
        ext = os.path.splitext(best["文件名"])[1].lower()
        dst = os.path.join(best_dir, f"{row['序号']}_{safe_name(row['名称'])}{ext}")
        shutil.copyfile(os.path.join(out_dir, best["路径"]), dst)
        picked += 1
        widths.append(best["宽"])

    stats = {"picked": picked, "inventory": len(inventory), "best_dir": best_dir,
             "min": min(widths) if widths else 0,
             "median": int(sorted(widths)[len(widths) // 2]) if widths else 0,
             "max": max(widths) if widths else 0,
             "dpi150": sum(1 for w in widths if w >= 945),
             "dpi300": sum(1 for w in widths if w >= 1890)}
    log(f"最优图：{picked} 张 -> {best_dir}")
    log(f"宽度 最小 {stats['min']} / 中位 {stats['median']} / 最大 {stats['max']}")
    log(f"达 A4 16cm @300dpi：{stats['dpi300']}/{picked}，@150dpi：{stats['dpi150']}/{picked}")
    return stats


# ------------------------------------------------------------------ 总览图

_FONTS = [
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhl.ttc", r"C:\Windows\Fonts\simhei.ttf",
]


def build_contact_sheet(folder: str, cols: int = 5, cell_w: int = 300, log=print) -> str:
    from math import ceil

    from PIL import Image, ImageDraw, ImageFont

    def font(size):
        for p in _FONTS:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(IMG_SUFFIXES))
    if not names:
        raise RuntimeError("目录里没有图片")

    img_h = int(cell_w * 0.75)
    label_h = 74
    cell_h = img_h + label_h
    gap, pad, header_h = 12, 24, 64
    rows_n = ceil(len(names) / cols)
    W = pad * 2 + cols * cell_w + (cols - 1) * gap
    H = pad * 2 + header_h + rows_n * cell_h + (rows_n - 1) * gap

    canvas = Image.new("RGB", (W, H), (250, 250, 248))
    draw = ImageDraw.Draw(canvas)
    draw.text((pad, pad + 12), f"{os.path.basename(folder)}  共 {len(names)} 张",
              fill=(30, 30, 30), font=font(26))

    name_font, meta_font = font(15), font(13)
    for idx, fn in enumerate(names):
        r, c = divmod(idx, cols)
        x = pad + c * (cell_w + gap)
        y = pad + header_h + r * (cell_h + gap)
        draw.rectangle([x, y, x + cell_w, y + img_h], fill=(232, 232, 228))
        try:
            with Image.open(os.path.join(folder, fn)) as src:
                src = src.convert("RGB")
                w0, h0 = src.size
                scale = max(cell_w / w0, img_h / h0)
                src = src.resize((max(1, int(w0 * scale)), max(1, int(h0 * scale))), Image.LANCZOS)
                left = (src.width - cell_w) // 2
                top = (src.height - img_h) // 2
                canvas.paste(src.crop((left, top, left + cell_w, top + img_h)), (x, y))
        except Exception:  # noqa: BLE001
            pass
        draw.rectangle([x, y + img_h, x + cell_w, y + cell_h], fill=(255, 255, 255))
        label = os.path.splitext(fn)[0]
        while name_font.getlength(label) > cell_w - 12 and len(label) > 4:
            label = label[:-1]
        draw.text((x + 6, y + img_h + 8), label, fill=(20, 20, 20), font=name_font)
        try:
            with Image.open(os.path.join(folder, fn)) as im:
                info = f"{im.size[0]}x{im.size[1]}"
        except Exception:  # noqa: BLE001
            info = ""
        draw.text((x + 6, y + img_h + 34), info, fill=(120, 120, 120), font=meta_font)

    out = os.path.join(os.path.dirname(folder.rstrip("\\/")), "contact_sheet.jpg")
    canvas.save(out, quality=88, optimize=True)
    log(f"总览图：{out}")
    return out
