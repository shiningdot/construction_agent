"""`EsafetyCollector` 的图片 URL 登记回归测试。

**不联网。** `_fetch_page` 与 `_fetch_detail` 两个方法换成假的；`raw_store`
显式改道到 `tmp_path`——`EsafetyCollector.__init__` 里是 `RawAccidentStore()`
无参调用，落 `settings.DATA_DIR/raw_accidents.json`，那是仓库里的共享文件。

盯的是 2026-10-01 第二十八轮改掉的一件事：**图片 URL 原先塞在 `pdf_urls` 里**。

`executor._ASSET_KEYS` 是**按键名**认类型的（`executor.py:83`），同一批 URL
放在 `pdf_urls` 下就登记成 `asset_type='pdf'`，阶段② 会拿它们当 PDF 去下载
并解析，一份也解不出来。易安网走 API，报告正文本身就是一张张扫描图
（`regulationPageInfos[].imageUrl`），本源**没有任何 PDF 附件**——所以
`pdf_urls` 现在是真的空，不是漏了。

§2.5.15（第十四轮）把这条登记为「潜在缺陷而非现行缺陷」：该源当时无 token、
0 条产出，路上没人。为什么这一轮才轮到它、且只能靠单测：**空源没有可端到端
实测的产出**。其中 `test_判据自检_…` 那一条是刻意去证明「改前的缺陷真的
存在过」，不是纸面推断。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.executor import (  # noqa: E402
    _row_from_sink_record,
)
from construction_agent.collector.sources.esafety import (  # noqa: E402
    EsafetyCollector,
)
from construction_agent.collector.stores import RawAccidentStore  # noqa: E402

# 假 token 必须是纯 ASCII：`EsafetyCollector.__init__` 直接把它塞进
# `Authorization` header，httpx 按 ascii 编码 header 值，中文会在构造时
# 就抛 `UnicodeEncodeError`。真 token 是 JWT，本就是 ASCII。
_TOKEN = "fake.token.offline"
_TITLE = '某某工程"7·15"较大坍塌事故调查报告'
_IMG1 = "https://cdn.esafety.cn/report/2026/aaaa/page-1.jpg"
_IMG2 = "https://cdn.esafety.cn/report/2026/aaaa/page-2.jpg"
_IMG3 = "https://cdn.esafety.cn/report/2026/aaaa/page-3.jpg"


def _list_item(rid: str = "R001") -> dict:
    return {
        "id": rid,
        "title": _TITLE,
        "executionDate": "2026-07-15",
        "accidentTypeId": "T1",
        "accidentLevelId": "L2",
        "viewCount": 7,
    }


def _page_api(items: list[dict]) -> dict:
    """列表接口的响应形状（`pages=1`，只取第一页）。"""
    return {"errcode": 0,
            "data": {"total": len(items), "pages": 1, "list": items}}


def _detail_api(infos: list[dict]) -> dict:
    return {"errcode": 0, "data": {"regulationPageInfos": infos}}


def _collector(tmp_path: Path, items: list[dict],
               details: dict[str, dict]) -> EsafetyCollector:
    """造一个不联网的采集器。

    `delay=0` 去掉采集间隔（每条的详情请求前还有 `delay * 0.5`）。
    `token` 必须非空：`collect()` 在 `:119` 先查 token，空 token 直接返回，
    一条也不走。
    """
    c = EsafetyCollector(token=_TOKEN, delay=0.0)
    c._fetch_page = lambda page: _page_api(items)
    c._fetch_detail = lambda rid: details.get(rid)
    c.raw_store = RawAccidentStore(filepath=tmp_path / "raw.json")
    return c


def _close(c: EsafetyCollector) -> None:
    client = getattr(c, "_client", None)
    if client is not None:
        client.close()


def _only_record(c: EsafetyCollector) -> dict:
    rows = c.raw_store.get_all()
    assert len(rows) == 1, f"期望 1 条产出，实际 {len(rows)} 条"
    return rows[0]


# --------------------------------------------------------------------------
# 源端：键名与判据
# --------------------------------------------------------------------------


def test_图片URL登记为image而非pdf(tmp_path):
    """一条 3 页的图片报告：`image_urls` 三个、`pdf_urls` 空、标 `needs_ocr`。"""
    details = {"R001": _detail_api(
        [{"imageUrl": u} for u in (_IMG1, _IMG2, _IMG3)])}
    c = _collector(tmp_path, [_list_item()], details)
    try:
        stats = c.collect()
        rec = _only_record(c)

        # `pdf_urls` 曾经装的是这三个图片地址——改后它必须真的是空的
        assert rec["pdf_urls"] == [], "图片 URL 又回到 pdf_urls 里了"
        assert rec["image_urls"] == [_IMG1, _IMG2, _IMG3]
        assert rec["needs_ocr"] is True

        # content 是给下一阶段看的摘要，页数与地址都要对得上
        assert "[图片报告 3页]" in rec["content"]
        assert _IMG1 in rec["content"]

        # 统计口径没被这一轮改动带偏
        assert stats["total_crawled"] == 1
        assert stats["cached_total"] == 1
    finally:
        _close(c)


def test_通路A把图片键登记成image(tmp_path):
    """登记端：走真实的 `_row_from_sink_record`（通路 A）。

    esafety 的 record 是 **dict**，由 `raw_store.add_batch()` 交出去，正好落
    在通路 A（`executor.py:132`）上——不是 `collect()` 返回值那条通路 B。
    """
    details = {"R001": _detail_api([{"imageUrl": _IMG1}, {"imageUrl": _IMG2}])}
    c = _collector(tmp_path, [_list_item()], details)
    try:
        c.collect()
        row = _row_from_sink_record(_only_record(c), "esafety", "task-1")
    finally:
        _close(c)

    assert row is not None
    assert [a["asset_type"] for a in row["_assets"]] == ["image", "image"]
    assert {a["url"] for a in row["_assets"]} == {_IMG1, _IMG2}


def test_没有图片时两个键都空且不标needs_ocr(tmp_path):
    """详情里没有 `regulationPageInfos`（或为空）时不能留下空串。"""
    details = {"R001": _detail_api([])}
    c = _collector(tmp_path, [_list_item()], details)
    try:
        c.collect()
        rec = _only_record(c)
    finally:
        _close(c)

    assert rec["pdf_urls"] == []
    assert rec["image_urls"] == []
    assert rec["needs_ocr"] is False
    assert rec["content"] == _TITLE          # 没有图就不拼摘要
    assert _row_from_sink_record(rec, "esafety", "t")["_assets"] == []


@pytest.mark.parametrize("blank", [
    {"imageUrl": ""},          # 实测形态：某一页没有图，接口交出空串
    {"imageUrl": None},
    {"imageUrl": "   "},
    {},                        # 连这个键都没有
])
def test_空imageUrl不虚报页数(tmp_path, blank):
    """接口里的空串被滤掉——否则每个判据都会跟着失真。

    空串留着的话：`needs_ocr` 因为一个空串标成「要 OCR」、`content` 写
    「[图片报告 2页]」却列不出两个地址、登记时还得靠 asset 层再滤一次
    （`_assets_from` 确实滤了，但那是别人的防御）。
    """
    details = {"R001": _detail_api([{"imageUrl": _IMG1}, blank])}
    c = _collector(tmp_path, [_list_item()], details)
    try:
        c.collect()
        rec = _only_record(c)
    finally:
        _close(c)

    assert rec["image_urls"] == [_IMG1]
    assert "[图片报告 1页]" in rec["content"]
    assert _row_from_sink_record(rec, "esafety", "t")["_assets"] == [
        {"asset_type": "image", "url": _IMG1}]


# --------------------------------------------------------------------------
# 判据自检
# --------------------------------------------------------------------------


def test_判据自检_同一批URL放进pdf_urls就会被登记成pdf():
    """**证明改前的缺陷是真的**，不是纸面推断。

    直接构造一个「图片 URL 塞在 `pdf_urls` 里」的 record 喂给通路 A：它给出
    `asset_type='pdf'`。这就是改前每一条 esafety 记录的登记结果——阶段② 会
    照这个类型去下载并解析 PDF，一份也解不出来。

    配对看前一条 `test_通路A把图片键登记成image`：**同一批 URL、同一个函数**，
    只有键名不同，登记类型就翻转。这说明修的是键名，不是别的什么。
    """
    rec = {
        "source_url": "https://www.esafety.cn/law/accident-reports/detail/R001",
        "title": _TITLE,
        "content": _TITLE,
        "pdf_urls": [_IMG1, _IMG2],      # 改前的写法
        "needs_ocr": True,
    }
    row = _row_from_sink_record(rec, "esafety", "task-1")
    assert [a["asset_type"] for a in row["_assets"]] == ["pdf", "pdf"]

    # 键名换成 `image_urls`，其余一切不动
    rec["image_urls"] = rec.pop("pdf_urls")
    row2 = _row_from_sink_record(rec, "esafety", "task-1")
    assert [a["asset_type"] for a in row2["_assets"]] == ["image", "image"]
