"""`GdEMCollector` 的附件正文通路回归测试。

**不联网。** `_fetch` 与 `_download_and_extract` 都换成假的。

盯的是 2026-10-01 查出的三层问题：

1. **附件抽出来从不去取。** `pdf_urls` 抽出来只写进摘要当提示，没有任何一步
   去下载它。实测那两条 Word 附件的正文（`.docx` 一条 8,170 字）一直躺在
   URL 后面没人取——省级站的常态正是「页面只有一行『点击链接查看报告详情：』，
   报告本体在附件里」。

2. **`.doc` 子串吞掉 `.docx`。** 判据原先是 `".doc" in href`，而 `.doc` 是
   `.docx` 的子串，于是 Word 附件混进了 `pdf_urls`（字段名说是 PDF），再经
   `executor._ASSET_KEYS` 按键名登记成 `asset_type="pdf"`。三处名实不符，
   源头是那一行。现在按后缀分拣成 `pdf_urls` / `office_urls`。

3. **`needs_ocr` 看的是页面正文。** 附件明明提出了正文，标记还照着页面那
   几十个字算，于是 `.docx` 那两条被标成「需要 OCR」——它要的是 Office
   解析，不是 OCR。现在判据看合并后的 `body_text`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.sources.gd_em import (  # noqa: E402
    GDEM_BASE, GDEM_LIST_URL, GdEMCollector, _attachment_kind)
from construction_agent.collector.stores import RawAccidentStore  # noqa: E402

_DETAIL_URL = f"{GDEM_BASE}/gk/zdlyxxgk/sgdcbg/content/post_1.html"
_TITLE = ('珠海市兴业快线（南段）一标段工程石景山隧道"7·15"'
          "重大透水事故防范和整改措施落实情况评估报告")

_LIST_PAGE = f"""<html><body>
<ul class="list">
  <li><h3><a href="/gk/zdlyxxgk/sgdcbg/content/post_1.html">{_TITLE}</a></h3>
      <span class="pubDate">2022-08-01</span></li>
</ul>
</body></html>"""

# 正文只有一行「点击链接查看报告详情：」，真东西在附件里——实测的形态。
_ATTACHMENT_BODY = "点击链接查看报告详情："


def _detail_page(attach_href: str, body: str = _ATTACHMENT_BODY,
                 img: bool = False) -> str:
    image_tag = '<img class="nfw-cms-img" src="/x.png"/>' if img else ""
    return f"""<html><body>
<h1 class="article_t">{_TITLE}</h1>
<div class="bor_bot"><div class="article_item fl">信息来源：广东省应急管理厅</div>
<div class="article_item fl">时间：2022-08-01 10:00</div></div>
<div class="article_con">
{image_tag}{body}
<a href="{attach_href}">附件：评估报告</a>
</div>
</body></html>"""


def _collector(tmp_path: Path, detail_html: str, **kwargs) -> GdEMCollector:
    """造一个不联网的采集器。

    `delay=0` 去掉采集间隔；`raw_store` 必须显式改道——`GdEMCollector.__init__`
    里是 `RawAccidentStore()` 无参调用，落 `settings.DATA_DIR/raw_accidents.json`，
    那是仓库里的共享文件。
    """
    c = GdEMCollector(data_dir=tmp_path / "src", delay=0, **kwargs)
    c.raw_store = RawAccidentStore(filepath=tmp_path / "raw.json")
    c._fetch = lambda url: _LIST_PAGE if url == GDEM_LIST_URL else detail_html
    return c


def _close(c: GdEMCollector) -> None:
    client = getattr(c, "_client", None)
    if client is not None:
        client.close()


# --------------------------------------------------------------------------
# 附件后缀分拣
# --------------------------------------------------------------------------


@pytest.mark.parametrize("href,expected", [
    ("/attachment/0/585/585826/3261144.docx", "office"),
    ("/attachment/0/585/585820/3261122.doc", "office"),
    ("/attachment/0/598/598873/4802837.pdf", "pdf"),
    # 大写、带查询串、绝对地址都要认得
    ("/attachment/0/1/2/3.PDF", "pdf"),
    ("/attachment/0/1/2/3.docx?download=1", "office"),
    (f"{GDEM_BASE}/attachment/0/1/2/3.pdf", "pdf"),
    # 认不出的不收
    ("/gk/zdlyxxgk/sgdcbg/index.html", ""),
    ("/gk/zdlyxxgk/sgdcbg/index_2.html", ""),
])
def test_附件后缀分拣(href, expected):
    """**`.doc` 是 `.docx` 的子串**——原先那行 `".doc" in href` 就是这么错的。

    这里逐条钉住后缀判据。注意 `index_2.html` 这类也不能被误收：老写法
    `".pdf" in href` 尚可，但同源的 `".doc" in href` 会把任何含 `.doc`
    的路径（含 `showdoc` 之类）收进来，放宽了附件集合。
    """
    assert _attachment_kind(href) == expected


# --------------------------------------------------------------------------
# 正文通路
# --------------------------------------------------------------------------


def test_正文短且有Word附件时下载并提正文(tmp_path):
    """改前这里是「抽出来就完了」——附件正文从未进过库。

    判据不是「content 非空」（页面那行字也是内容），而是 content 里真的有
    附件正文，且 `needs_ocr` 不再标 True。
    """
    c = _collector(tmp_path, _detail_page("/attachment/0/585/585826/3261144.docx"))
    body = "佛山市轨道交通2号线一期工程“2·7”透水坍塌重大事故整改措施落实情况评估报告。" * 100
    c._download_and_extract = lambda url: body
    try:
        c.collect()
        items = c.raw_store.get_all()
        assert len(items) == 1
        item = items[0]
        assert body in item["content"]
        assert item["office_urls"] == [
            f"{GDEM_BASE}/attachment/0/585/585826/3261144.docx"]
        assert item["pdf_urls"] == []
        # 正文拿到了 → 不该再要求 OCR（它本来要的是 Office 解析）
        assert item["needs_ocr"] is False
    finally:
        _close(c)


def test_正文短且附件提不出时标needs_ocr(tmp_path):
    """老 `.doc`（OLE2）`fitz` 读不动 —— 实测 `extract_pdf_text` 返回空串。

    这一路**仍然必须标 `needs_ocr`**：正文确实没拿到。不是「不需要处理」，
    是没有可用的处理办法，得让下游知道。
    """
    c = _collector(tmp_path, _detail_page("/attachment/0/585/585820/3261122.doc"))
    c._download_and_extract = lambda url: ""
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["needs_ocr"] is True
        assert item["office_urls"] == [
            f"{GDEM_BASE}/attachment/0/585/585820/3261122.doc"]
        # 附件 URL 仍要留在摘要里，人工审核时得能点开
        assert "3261122.doc" in item["content"]
    finally:
        _close(c)


def test_正文够长就不去下载附件(tmp_path):
    """下载是额外请求，不能每条都做。

    触发条件是「正文短于 200 字」——正文本来就完整的条目，附件多半是同一份
    报告的复本，再下一遍纯属浪费。
    """
    long_body = "事故调查报告正文。" * 50      # 400 字
    c = _collector(tmp_path, _detail_page("/attachment/0/1/2/3.pdf", body=long_body))
    called = []
    c._download_and_extract = lambda url: called.append(url) or "不该被调用"
    try:
        c.collect()
        assert called == []
        item = c.raw_store.get_all()[0]
        assert item["needs_ocr"] is False
    finally:
        _close(c)


def test_多个附件时取第一个能提出来的(tmp_path):
    """第一个失败（.doc）就试下一个（.docx）。"""
    c = _collector(tmp_path, _detail_page("/attachment/0/585/585820/3261122.doc"))
    seen = []

    def fake_download(url):
        seen.append(url)
        # 第一个（.doc）读不出，第二个（.docx）读出正文
        return "" if url.endswith(".doc") else "附件里的正文。" * 40

    c._download_and_extract = fake_download
    # 手工塞进第二个附件，模拟页面同时挂 .doc 与 .docx
    orig = c._crawl_detail

    def patched(url):
        d = orig(url)
        d["office_urls"].append(f"{GDEM_BASE}/attachment/0/585/585826/3261144.docx")
        return d

    c._crawl_detail = patched
    try:
        c.collect()
        assert len(seen) == 2
        assert seen[0].endswith(".doc")
        assert seen[1].endswith(".docx")
        assert c.raw_store.get_all()[0]["needs_ocr"] is False
    finally:
        _close(c)


def test_摘要分别列出PDF与Word附件(tmp_path):
    """`pdf_urls` 里不再混着 Word —— 摘要上要看得出这是两种东西。"""
    c = _collector(tmp_path, _detail_page("/attachment/0/585/585826/3261144.docx"))
    c._download_and_extract = lambda url: ""
    try:
        c.collect()
        content = c.raw_store.get_all()[0]["content"]
        assert "Word 附件(1个):" in content
        assert "PDF 附件" not in content
    finally:
        _close(c)


def test_图片型报告仍标needs_ocr(tmp_path):
    """图片格式报告（真扫描件）这条路不能修坏 —— 它本来就该标 OCR。"""
    c = _collector(tmp_path, _detail_page("/attachment/0/1/2/3.pdf", img=True))
    c._download_and_extract = lambda url: ""
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["needs_ocr"] is True
        assert "[图片格式报告，需 OCR 提取文字]" in item["content"]
    finally:
        _close(c)


# --------------------------------------------------------------------------
# 构造器契约
# --------------------------------------------------------------------------


def test_构造器接受data_dir(tmp_path):
    """2026-10-01 起本源要下载附件提正文，于是需要落盘位置。

    此前 `GdEMCollector` 不收 `data_dir`，`registry.build()` 把它静默丢掉，
    registry 的 note 也照实写着「不接受 data_dir 参数（实测不建目录）」。
    现在参数对上了，注入生效。

    （这条也由 `test_beijing_em_collector.py` 里的全源参数化断言兜着——
    那里遍历 `registry.SOURCES` 核对 build_kwargs 与构造器签名。）
    """
    c = GdEMCollector(data_dir=tmp_path / "cache")
    try:
        assert c.data_dir == tmp_path / "cache"
        assert c.data_dir.is_dir()
    finally:
        _close(c)
