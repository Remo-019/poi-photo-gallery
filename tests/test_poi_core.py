# -*- coding: utf-8 -*-
"""poi_core 纯函数的单元测试（不联网、不碰磁盘数据）。

用 pytest 运行：pytest -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import poi_core


# ---------------------------------------------------------------- normalize_region

def test_normalize_region_strips_province():
    assert poi_core.normalize_region("广东省深圳市") == "深圳市"
    assert poi_core.normalize_region("广西壮族自治区南宁市") == "南宁市"
    assert poi_core.normalize_region("内蒙古自治区呼和浩特市") == "呼和浩特市"


def test_normalize_region_keeps_municipality():
    # 直辖市本身就是城市，不应被剥成「市」后面的空串
    assert poi_core.normalize_region("北京市") == "北京市"
    assert poi_core.normalize_region("重庆市") == "重庆市"


def test_normalize_region_passthrough():
    assert poi_core.normalize_region("深圳市") == "深圳市"
    assert poi_core.normalize_region("") == ""


# ---------------------------------------------------------------- region_core

def test_region_core():
    assert poi_core.region_core("深圳市") == "深圳"
    assert poi_core.region_core("广东省深圳市") == "深圳"
    assert poi_core.region_core("浦东新区") == "浦东新"


# ---------------------------------------------------------------- normalize_baidu_url

def test_normalize_baidu_url_strips_query_on_known_cdn():
    u = "https://poi-pic.cdn.bcebos.com/abc.jpg?x-bce-process=image/resize,w_500"
    assert poi_core.normalize_baidu_url(u) == "https://poi-pic.cdn.bcebos.com/abc.jpg"


def test_normalize_baidu_url_rejects_panorama_and_unknown():
    assert poi_core.normalize_baidu_url("https://x.com/a?qt=pr3d") is None
    assert poi_core.normalize_baidu_url("https://example.com/a.jpg") is None
    assert poi_core.normalize_baidu_url("") is None


def test_normalize_baidu_url_ctrip_upgrades():
    u = "https://dimg04.c-ctrip.com/images/abc_R_800_600_Q70.jpg"
    assert poi_core.normalize_baidu_url(u) == "https://dimg04.c-ctrip.com/images/abc_R_2000_10000_Q90.jpg"


# ---------------------------------------------------------------- image_size

def _jpeg(w: int, h: int) -> bytes:
    """构造最小可被 image_size 解析的 JPEG（SOI + SOF0 段 + EOI）。"""
    sof = bytes([0xFF, 0xC0, 0x00, 0x11, 0x08,
                 (h >> 8) & 0xFF, h & 0xFF,
                 (w >> 8) & 0xFF, w & 0xFF,
                 0x03, 0x01, 0x22, 0x00, 0x02, 0x11, 0x00, 0x03, 0x11, 0x00])
    return b"\xff\xd8" + sof + b"\xff\xd9"


def test_image_size_jpeg():
    assert poi_core.image_size(_jpeg(1920, 1080)) == (1920, 1080)


def test_image_size_invalid():
    assert poi_core.image_size(b"not an image") == (0, 0)


# ---------------------------------------------------------------- guess_ext

def test_guess_ext_by_magic():
    assert poi_core.guess_ext(b"\xff\xd8\xff\xe0", "https://x/y") == ".jpg"
    assert poi_core.guess_ext(b"\x89PNG\r\n\x1a\n", "https://x/y") == ".png"


def test_guess_ext_by_url_fallback():
    assert poi_core.guess_ext(b"xxxx", "https://x/a.webp?t=1") == ".webp"
    assert poi_core.guess_ext(b"xxxx", "https://x/noext") == ".jpg"


# ---------------------------------------------------------------- safe_name

def test_safe_name_sanitizes():
    assert poi_core.safe_name('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert poi_core.safe_name("   ") == "unnamed"


# ---------------------------------------------------------------- photo_rank

def test_photo_rank_prefers_more_pixels_within_band():
    lo = {"宽": 500, "高": 500, "体积KB": 100, "文件名": "1.jpg"}
    hi = {"宽": 900, "高": 900, "体积KB": 100, "文件名": "2.jpg"}
    # 900*900=81 万 vs 500*500=25 万，同处一档内（81万//25万=3, 25万//25万=1）
    # 但这里要验证的是像素分档在前：hi 档位更高，必排前
    assert poi_core.photo_rank(hi) > poi_core.photo_rank(lo)


def test_photo_rank_prefers_landscape_within_same_pixels():
    landscape = {"宽": 800, "高": 600, "体积KB": 100, "文件名": "1.jpg"}
    portrait = {"宽": 600, "高": 800, "体积KB": 100, "文件名": "2.jpg"}
    # 像素相同（48 万），横图优先
    assert poi_core.photo_rank(landscape) > poi_core.photo_rank(portrait)


def test_photo_rank_prefers_early_album_position():
    early = {"宽": 800, "高": 600, "体积KB": 100, "文件名": "01.jpg"}
    late = {"宽": 800, "高": 600, "体积KB": 100, "文件名": "99.jpg"}
    assert poi_core.photo_rank(early) > poi_core.photo_rank(late)


def test_photo_rank_tolerates_bad_filename():
    # 文件名无数字前缀时不抛异常，回落 idx=99
    item = {"宽": 800, "高": 600, "体积KB": 100, "文件名": "abc.jpg"}
    assert poi_core.photo_rank(item) is not None


# ---------------------------------------------------------------- upscale_url

def test_upscale_url_appends_oss_process_only_for_oss_host():
    oss = "https://store.is.autonavi.com/foo.jpg"
    assert "x-oss-process=image/resize,w_5000,limit_1" in poi_core.upscale_url(oss, 5000)


def test_upscale_url_leaves_non_oss_untouched():
    u = "https://example.com/foo.jpg"
    assert poi_core.upscale_url(u, 5000) == u


def test_upscale_url_skips_if_already_has_process():
    u = "https://store.is.autonavi.com/foo.jpg?x-oss-process=image/resize,w_100"
    assert poi_core.upscale_url(u, 5000) == u


# ---------------------------------------------------------------- _norm_name

def test_norm_name_drops_bracket_and_space():
    # 同一家机构在高德、百度的写法常差一对括号或一个空格
    assert poi_core._norm_name("测试医院（东院）") == "测试医院"
    assert poi_core._norm_name("测试医院(东院)") == "测试医院"
    assert poi_core._norm_name(" 和平 药店 ") == "和平药店"
    assert poi_core._norm_name("华联·超市") == "华联超市"
    assert poi_core._norm_name("") == ""


# ---------------------------------------------------------------- build_merge_table

def _write_manifest(folder: str, rows: list[dict]) -> None:
    import csv as _csv
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "manifest.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = _csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def test_build_merge_table_splits_address_and_phone_by_source(tmp_path):
    import csv as _csv
    base = {"序号": "1", "分类": "综合医院", "分类码": "090100",
            "经度": "116.1", "纬度": "39.1", "照片数": "4",
            "已下载": "3", "最大尺寸": "1600x1200", "抓取日期": "2026-11-24 10:00",
            "目录": "001_x", "来源": "高德", "坐标": "116.1,39.1"}
    _write_manifest(str(tmp_path / "高德"), [
        dict(base, 名称="测试医院（东院）", POI_ID="A1", 地址="高德路1号", 电话="010-1111"),
        dict(base, 序号="2", 名称="只有高德的店", POI_ID="A2", 地址="高德路2号", 电话=""),
    ])
    _write_manifest(str(tmp_path / "百度"), [
        dict(base, 名称="测试医院", POI_ID="B1", 地址="百度路88号", 电话="010-2222;010-3333"),
        dict(base, 序号="2", 名称="只有百度的店", POI_ID="B2", 地址="百度路99号", 电话="010-4444"),
    ])

    path = poi_core.build_merge_table(str(tmp_path), log=lambda *_: None)
    assert path and os.path.exists(path)

    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(_csv.DictReader(fh))

    assert len(rows) == 3
    by_state = {r["匹配状态"]: r for r in rows}
    assert set(by_state) == {"两源都有", "仅高德", "仅百度"}

    both = by_state["两源都有"]
    # 地址与电话必须各归各列，不能混成一列
    assert both["高德地址"] == "高德路1号"
    assert both["高德电话"] == "010-1111"
    assert both["百度地址"] == "百度路88号"
    assert both["百度电话"] == "010-2222;010-3333"

    assert by_state["仅高德"]["百度地址"] == ""
    assert by_state["仅百度"]["高德地址"] == ""


def test_build_merge_table_returns_none_without_manifests(tmp_path):
    assert poi_core.build_merge_table(str(tmp_path), log=lambda *_: None) is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
