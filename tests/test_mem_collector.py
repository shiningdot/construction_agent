"""`MemCollector` 的 PDF 正文路径。

**不联网。** PDF 用 PyMuPDF 现场造，HTTP 客户端换成假的。

这组测试盯的是 2026-10-01 查出的一处「正文从来没取到过」：

- 应急部的调查报告正文**全在 PDF 附件里**，HTML 详情页只有摘要或 meta
  描述——实测中位 **248 字**，主库 3 条 `mem` 记录的 raw_text 分别是
  50 / 134 / 3,572 字。
- 而 `_handle_pdf_link` 的注释直说「不做正文提取，仅记录元数据供后续
  MinerU OCR」，`_crawl_detail_article` 抽到 PDF 链接也只记 `needs_ocr=True`，
  **从不下载**。
- ~~那个 MinerU **不存在**：`settings.MINERU_API_TOKEN` 没这个字段，
  `construction_agent.input.pdf_processor` 目录是空的。~~
  **⚠️ 2026-10-05 更正（§2.5.61 / §2.5.62）**，原句**半对半错**：
  ①`settings.MINERU_API_TOKEN` **确实**没这个字段 —— 成立；
  ②「MinerU 不存在」**不成立** —— 它存在，是 **MinerU 云端 API**；
  ③「`input/` 目录是空的」**不成立** —— `pdf_processor.py` 已归档回原位（§2.5.62）。
  准确说法：MinerU 存在，只是**不挂在 `settings` 上** —— `process_pdf.py` /
  `process_pdf3.py` / `process_ebook800.py` 三个脚本都是自己
  `os.getenv('MINERU_API_TOKEN')` 再经构造参数传入，不经过 `settings`。
  **「`settings` 里没有这个字段」与「项目没有 MinerU」是两件事**，原句说成了一件事。
- 实测：欣佳酒店调查报告 51 页提出 **37,618 字**、商洛桥梁 39 页提出
  **28,315 字**，都是文字版，本地 PyMuPDF 一次到位。

判据放在「提出来的正文有没有替换掉摘要」，而不是「有没有调用解析」——
后者在解析器静默失败时照样绿。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.sources.mem import MemCollector  # noqa: E402

# 一条真实标题：含「坍塌」，过得了 mem 自己的二次筛选
TITLE = "福建省泉州市欣佳酒店“3·7”坍塌事故调查报告"


def _make_pdf(path: Path, body: str = "") -> bytes:
    """造一份真 PDF。`body` 为空时是空白页（模拟扫描件）。"""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    if body:
        page.insert_text((72, 72), body, fontsize=9)
    doc.save(str(path))
    doc.close()
    return path.read_bytes()


@pytest.fixture
def collector(tmp_path):
    c = MemCollector(data_dir=tmp_path / "mem")
    # 别碰 `data/raw_accidents.json`：真实的 store 会在 mark_filtered 时
    # 写盘，而这是单元测试。
    c.raw_store = types.SimpleNamespace(
        mark_filtered=lambda *a, **k: None,
        add_batch=lambda *a, **k: None,
        _save=lambda *a, **k: None,
    )
    yield c
    close = getattr(c._client, "close", None)
    if close is not None:
        close()


def _client_serving(files: dict[str, bytes], status: dict[str, int] | None = None):
    """假客户端：按 URL 前缀返回字节；`status` 里指定的 URL 返回该状态码。"""
    status = status or {}

    def _get(url, *a, **k):
        for prefix, code in status.items():
            if url.startswith(prefix):
                return httpx.Response(code, request=httpx.Request("GET", url))
        for prefix, data in files.items():
            if url.startswith(prefix):
                return httpx.Response(
                    200, content=data, request=httpx.Request("GET", url))
        return httpx.Response(404, request=httpx.Request("GET", url))

    return types.SimpleNamespace(get=_get)


# --------------------------------------------------------------------------
# 成功路径：PDF 正文替换掉摘要
# --------------------------------------------------------------------------


def test_PDF直链要提出全文而不是只记地址(collector, tmp_path):
    """改之前这里产出的是 `[PDF 待 OCR] 标题\\nPDF 地址: …`，正文一个字没有。"""
    body = "Accident investigation report body text. " * 40  # > 50 字
    pdf = _make_pdf(tmp_path / "r.pdf", "\n".join(
        f"Line {i}: accident investigation report full text here." for i in range(14)))
    collector._client = _client_serving({"https://x/r.pdf": pdf})

    item = collector._handle_pdf_link({
        "title": TITLE, "url": "https://x/r.pdf",
        "link_type": "pdf", "date_str": "2020-07-14", "year": 2020,
    })

    assert item is not None
    assert "accident investigation report full text" in item["content"]
    assert len(item["content"]) > 50
    # 提出来了就不该再挂 OCR 的标记——否则下游还会再排一次队
    assert item["needs_ocr"] is False
    assert body  # 造数据用的，避免被当成未使用变量


def test_扫描件才留待OCR(collector, tmp_path):
    """提不出正文 = 扫描件，不是错误。原件留在盘上供下游 OCR。"""
    blank = _make_pdf(tmp_path / "blank.pdf")  # 空白页
    collector._client = _client_serving({"https://x/blank.pdf": blank})

    item = collector._handle_pdf_link({
        "title": TITLE, "url": "https://x/blank.pdf",
        "link_type": "pdf", "date_str": "2020-07-14", "year": 2020,
    })

    assert item is not None
    assert item["needs_ocr"] is True
    assert "待 OCR" in item["content"]
    assert item["pdf_urls"] == ["https://x/blank.pdf"]


def test_详情页正文取PDF全文(collector, tmp_path):
    """详情页这条主路径：HTML 上只有摘要，正文在附件里。

    判据是「PDF 全文替换掉了摘要」，不是「解析被调用过」——后者在解析器
    静默失败时照样成立。
    """
    pdf = _make_pdf(tmp_path / "detail.pdf", "\n".join(
        f"Line {i}: the full investigation report text goes here." for i in range(20)))
    html = (f"<html><head><title>{TITLE}</title></head><body>"
            f"<div class='scy_main_detail'>摘要很短</div>"
            f"<a href='./detail.pdf'>报告全文</a></body></html>").encode()
    collector._client = _client_serving({
        "https://x/page.shtml": html,
        "https://x/detail.pdf": pdf,
    })

    item = collector._crawl_detail_article({
        "title": TITLE, "url": "https://x/page.shtml",
        "link_type": "new_cms", "date_str": "2020-07-14", "year": 2020,
    })

    assert item is not None
    assert "full investigation report text" in item["content"]
    assert "摘要很短" not in item["content"]
    assert item["needs_ocr"] is False


def test_详情页没有PDF时摘要照旧(collector, tmp_path):
    """没有附件就别乱动——HTML 抽到什么就是什么，一条都不许丢。"""
    html = (f"<html><head><title>{TITLE}</title></head><body>"
            f"<div class='scy_main_detail'>这是一段足够长的建筑事故摘要，"
            f"用于验证没有 PDF 附件时原样保留，不被改动。</div></body></html>").encode()
    collector._client = _client_serving({"https://x/page.shtml": html})

    item = collector._crawl_detail_article({
        "title": TITLE, "url": "https://x/page.shtml",
        "link_type": "new_cms", "date_str": "2020-07-14", "year": 2020,
    })

    assert item is not None
    assert "建筑事故摘要" in item["content"]
    assert item["needs_ocr"] is False  # 没有 PDF，就谈不上需要 OCR


# --------------------------------------------------------------------------
# 降级：下载不成就退回摘要，一条都不许掀
# --------------------------------------------------------------------------


def test_附件404时退回摘要(collector, tmp_path):
    """附件挂了不是这一篇的死刑——HTML 摘要仍然是有效产出。

    注意摘要得**长过 30 字**才抽得出来（`_crawl_detail_article` 里那道
    `len(text) > 30` 的门槛），否则会退到 `[PDF 报告] 标题` 兜底。两种
    降级都合理，这里验的是前者。
    """
    html = (f"<html><head><title>{TITLE}</title></head><body>"
            f"<div class='scy_main_detail'>附件挂掉时保留的这段摘要，"
            f"长度足够越过三十字的门槛，因此应当被原样抽出来当作正文的兜底。</div>"
            f"<a href='./missing.pdf'>报告全文</a></body></html>").encode()
    collector._client = _client_serving(
        {"https://x/page.shtml": html},
        status={"https://x/missing.pdf": 404})

    item = collector._crawl_detail_article({
        "title": TITLE, "url": "https://x/page.shtml",
        "link_type": "new_cms", "date_str": "2020-07-14", "year": 2020,
    })

    assert item is not None
    assert "保留的这段摘要" in item["content"]
    # 附件没拿到 → 仍挂着「需要 OCR」，下游看得见这一篇不完整
    assert item["needs_ocr"] is True


def test_下载抛异常不掀整轮(collector):
    """采集器里到处是 `except Exception`，单条失败不许掀掉整轮。"""
    def boom(url, *a, **k):
        raise httpx.ConnectError("timed out")

    collector._client = types.SimpleNamespace(get=boom)
    assert collector._fetch_pdf_text("https://x/r.pdf") == ""


def test_落盘的PDF留在data_dir(collector, tmp_path):
    """原件留着可查——真要 OCR 时下游拿得到那份 PDF。

    文件名从 URL 路径取，去掉 query 与非文件名字符。
    """
    pdf = _make_pdf(tmp_path / "src.pdf", "Accident report body text. " * 10)
    collector._client = _client_serving({"https://x/路径/报告.pdf": pdf})

    text = collector._fetch_pdf_text("https://x/路径/报告.pdf?v=2")

    assert "Accident report body text" in text
    saved = list(collector.data_dir.glob("*.pdf"))
    assert len(saved) == 1
    # 汉字等非文件名字符被换成下划线；后缀保留、query 已去掉
    assert saved[0].suffix == ".pdf"
    assert "?" not in saved[0].name


# --------------------------------------------------------------------------
# 省流量：筛掉的条目不许下载
# --------------------------------------------------------------------------


def test_被筛掉的条目不下载PDF(collector, tmp_path):
    """二次筛选在下载**之前**——省流量，且口径与改之前逐字相同。

    改之前只拿标题判，现在也是。要是挪到下载之后，同一个源两次采样的
    取舍会跟着正文长度漂移，且无从对账。
    """
    calls = []
    collector._client = types.SimpleNamespace(
        get=lambda url, *a, **k: calls.append(url))

    item = collector._handle_pdf_link({
        "title": "某煤矿瓦斯爆炸事故调查报告",  # 排除词命中
        "url": "https://x/coal.pdf", "link_type": "pdf",
        "date_str": "2020-07-14", "year": 2020,
    })

    assert item is None
    assert calls == []  # 一个请求都没发
