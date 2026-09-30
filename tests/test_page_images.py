"""内容图抽取（`construction_agent.collector.images`）的回归测试。

**不联网。** `_fetch` 换成假的。

判据是 2026-10-01 第十八/十九轮定的：**图片 URL 与详情页 URL 同目录**
（或以页面路径为前缀）。它的前身「同目录 + 编号/时间戳连续」被 `tj_em`
当场推翻——那个源有 10 张同目录、时间戳连续递增的图，却是模板图（四个不同
页面每页都挂同一批）。所以这里的反例测试比正例更要紧。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup  # noqa: E402

from construction_agent.collector.images import (  # noqa: E402
    extract_page_images, is_page_image)
from construction_agent.collector.sources.nmg_em import (  # noqa: E402
    NMGEM_LIST_URL, NmgEMCollector)
from construction_agent.collector.stores import RawAccidentStore  # noqa: E402

_PAGE = "https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/202511/t20251121_123.html"
_DIR = "https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/202511/"


# --------------------------------------------------------------------------
# A 组：判据本身
# --------------------------------------------------------------------------


def test_同目录的图算内容图():
    assert is_page_image(_PAGE, _DIR + "W020251121650679042482_ORIGIN.jpg")


def test_以页面路径为前缀的图算内容图():
    """`sc_em` 形态：`…/b3eebb40….html` 配 `…/b3eebb40…/images/0001.jpg`
    ——**不同目录**，但图片在页面路径之下。"""
    page = "https://yjt.sc.gov.cn/scyjt/dcbg/2021/7/5/b3eebb4076634c3482006b829e5fb467.html"
    img = ("https://yjt.sc.gov.cn/scyjt/dcbg/2021/7/5/"
           "b3eebb4076634c3482006b829e5fb467/images/0001.jpg")
    assert is_page_image(page, img)


def test_跨目录的图不算内容图():
    """`tj_em` 的模板图形态：页面在 `/ZWGK6939/SGDCBG354/202608/`，
    图在 `/qdfy/tytb/202008/`。**命名与内容图一模一样**（同目录、时间戳连续
    递增的 TRS 资源），唯一能分开的就是目录对不对。"""
    page = "https://yjgl.tj.gov.cn/ZWGK6939/SGDCBG354/202608/t20260821_7356773.html"
    img = "https://yjgl.tj.gov.cn/qdfy/tytb/202008/W020200824411649201603.png"
    assert not is_page_image(page, img)


def test_换域名的图不算内容图():
    assert not is_page_image(_PAGE, "https://cdn.example.com/202511/a.jpg")


def test_同目录但不同站也不算():
    assert not is_page_image(_PAGE, "https://other.gov.cn/zwgk/zdxx/sgxxccpg/202511/a.jpg")


# --------------------------------------------------------------------------
# B 组：从 DOM 里抽
# --------------------------------------------------------------------------


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_抽出同目录图并还原成绝对URL():
    s = _soup('<img src="./W020251121650679042482_ORIGIN.jpg">')
    assert extract_page_images(_PAGE, s) == [_DIR + "W020251121650679042482_ORIGIN.jpg"]


def test_懒加载的data_src也认():
    """只认 `src` 会拿到占位图。"""
    s = _soup('<img src="data:image/gif;base64,R0lGOD" data-src="./W0202511211.jpg">')
    assert extract_page_images(_PAGE, s) == [_DIR + "W0202511211.jpg"]


def test_source标签也收():
    s = _soup('<source src="./W0202511212.jpg">')
    assert extract_page_images(_PAGE, s) == [_DIR + "W0202511212.jpg"]


def test_模板图混在里面时只取内容图():
    s = _soup('<img src="/material/2021_static/logo.png">'
              '<img src="./W0202511213.jpg">'
              '<img src="https://yjglt.nmg.gov.cn/images/top/banner.png">')
    assert extract_page_images(_PAGE, s) == [_DIR + "W0202511213.jpg"]


def test_非图片扩展名跳过():
    s = _soup('<img src="./a.pdf"><img src="./b.jpg">')
    assert extract_page_images(_PAGE, s) == [_DIR + "b.jpg"]


def test_内联base64跳过():
    s = _soup('<img src="data:image/png;base64,iVBORw0KGgo">')
    assert extract_page_images(_PAGE, s) == []


def test_同一张图去重():
    s = _soup('<img src="./a.jpg"><img src="./a.jpg"><img src="a.jpg">')
    assert extract_page_images(_PAGE, s) == [_DIR + "a.jpg"]


def test_上限为0时不抽取():
    s = _soup('<img src="./a.jpg">')
    assert extract_page_images(_PAGE, s, max_n=0) == []


def test_超过上限时截断并保序():
    s = _soup("".join(f'<img src="./{i:03d}.jpg">' for i in range(8)))
    got = extract_page_images(_PAGE, s, max_n=3)
    assert got == [_DIR + f"{i:03d}.jpg" for i in range(3)]


def test_带查询串的图片URL归一后不重复():
    s = _soup('<img src="./a.jpg?x=1"><img src="./a.jpg?x=2">')
    assert extract_page_images(_PAGE, s) == [_DIR + "a.jpg"]


# --------------------------------------------------------------------------
# C 组：nmg_em 的接入（此前它的图片一张都不登记）
# --------------------------------------------------------------------------
#
# `nmg_em` 实测 121 张内容图，是全项目最多的一块。它不继承 TRS 基类，所以
# 第十九轮在基类里做的落地**碰不到它**——这一组就是盯这件事。

_LINK = "./202511/t20251121_123.html"
_DETAIL_URL = NMGEM_LIST_URL + "202511/t20251121_123.html"


def _list_html() -> str:
    return ('<html><body>'
            f'<div class="sn-item"><a href="{_LINK}" '
            'title="某地高处坠落事故调查报告">某地高处坠落事故调查报告</a>'
            '<span class="sn-date">2025-11-21</span></div>'
            '</body></html>')


def _detail_html() -> str:
    return ('<html><head>'
            '<meta name="ArticleTitle" content=\'某地高处坠落事故调查报告\'/>'
            '<meta name="PubDate" content=\'2025-11-21 10:00:00\'/>'
            '</head><body>'
            '<div class="con">正文正文正文</div>'
            '<img src="/material/2021_static/logo.png">'
            '<img src="./W020251121650679042482_ORIGIN.jpg">'
            '<img src="./W020251121650679093210_ORIGIN.jpg">'
            '</body></html>')


def _nmg(tmp_path: Path, image_max: int = 50) -> NmgEMCollector:
    c = NmgEMCollector(delay=0, enable_classify=False,
                       data_dir=tmp_path / "nmg", image_max=image_max)
    c._fetch = lambda url: _list_html() if url == NMGEM_LIST_URL else _detail_html()
    c.raw_store = RawAccidentStore(filepath=tmp_path / "raw.json")
    return c


def _close(c: NmgEMCollector) -> None:
    client = getattr(c, "_client", None)
    if client is not None:
        client.close()


def test_nmg_em_详情页抽出内容图(tmp_path):
    c = _nmg(tmp_path)
    try:
        d = c._crawl_detail(_DETAIL_URL)
        assert d["image_urls"] == [
            NMGEM_LIST_URL + "202511/W020251121650679042482_ORIGIN.jpg",
            NMGEM_LIST_URL + "202511/W020251121650679093210_ORIGIN.jpg",
        ]
    finally:
        _close(c)


def test_nmg_em_产出里带上image_urls(tmp_path):
    """这个键此前根本不存在，`raw_accident_assets` 里 `asset_type='image'`
    因此一直是 0 行。"""
    c = _nmg(tmp_path)
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert len(item["image_urls"]) == 2
        assert all("_ORIGIN.jpg" in u for u in item["image_urls"])
    finally:
        _close(c)


def test_nmg_em_模板图不登记(tmp_path):
    c = _nmg(tmp_path)
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert not any("/material/" in u for u in item["image_urls"])
    finally:
        _close(c)


def test_nmg_em_上限为0时不出图(tmp_path):
    c = _nmg(tmp_path, image_max=0)
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["image_urls"] == []
    finally:
        _close(c)


def test_nmg_em_的图能落成image类型asset(tmp_path):
    from construction_agent.collector.executor import _row_from_raw_item
    c = _nmg(tmp_path)
    try:
        c.collect()
        raw = c.raw_store.get_all()[0]
        row = _row_from_raw_item(_as_raw_item(raw), "nmg_em", "task1")
        assert [a["asset_type"] for a in row["_assets"]] == ["image", "image"]
    finally:
        _close(c)


def _as_raw_item(d: dict):
    """`RawAccidentStore` 的 dict → `RawAccidentText`。

    通路 A 与通路 B 的字段名不同（正文一个叫 `content` 一个叫 `raw_text`），
    `nmg_em` 走的是 store 通路，所以测试要自己转这道手。
    """
    from construction_agent.collector.base import RawAccidentText
    return RawAccidentText(
        source=d.get("source_name", "nmg_em"),
        title=d.get("title", ""),
        raw_text=d.get("content", ""),
        source_url=d.get("source_url", ""),
        fetch_time="",
        extracted_date=None,
        extra={"image_urls": d.get("image_urls") or [],
               "pdf_urls": d.get("pdf_urls") or []},
    )
