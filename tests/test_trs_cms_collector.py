"""`TrsCmsCollector` 基类的附件通路与批次字段归属回归测试。

**不联网。** `_fetch` 与 `_download_and_extract_pdf` 在 A 组换成假的，
B 组直接给 `_client` 一个假响应对象。

12 个省级 TRS 源全部继承这个基类，所以这里的一条断言等于对 12 个源各生效一次。

盯的是 2026-10-01 普查查出的三层问题：

1. **PDF 下载了，但 URL 和本地路径从不往外传。** `_download_and_extract_pdf`
   确实下载、提取、落盘，可产出里既没有 `pdf_urls` 键（`executor._ASSET_KEYS`
   按键名认附件，没有键就登记不上 `raw_accident_assets`），`local_pdf_path`
   更是个赋值后再无引用的死变量。实测 `sh_em` / `sx_em` 是**全 PDF 型**站点：
   页面 HTML 正文 631 / 717 字，PDF 里 8,635 / 7,523 字——正文主体在附件中。

2. **`needs_ocr` 从来不存在。** 附件提不出正文时（扫描件、老 `.doc`），下游
   看不到任何信号。

3. **批量写库时字段串批。** `results[-5:]` 取前五条，而 `publish_date` /
   `status` 引用的是外层变量——也就是**第五条**的值。前 4 条全被写上第五条
   的日期与状态。这条只有跨批次才现形，单条测试永远是对的。

4. **`except Exception: pass` 把下载失败吞得无声无息。** 四种失败（网络异常 /
   HTTP 非 200 / 响应过短 / 落盘失败）压成同一个 `("", "")`。
"""

from __future__ import annotations

import hashlib
import logging
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.sources import trs_cms_base  # noqa: E402
from construction_agent.collector.sources.trs_cms_base import (  # noqa: E402
    TrsCmsCollector, TrsSiteConfig)
from construction_agent.collector.stores import RawAccidentStore  # noqa: E402

_LIST_URL = "https://tsrc.gov.cn/list/"
_LIST_DIR = "https://tsrc.gov.cn/list/202607/"

# (标题, URL 里的日期段, URL 里的序号段) —— 标题含「事故」以过一级筛选
_LINKS = [
    ("甲地隧道坍塌事故调查报告", "20260713", "90611300"),
    ("乙地脚手架坠落事故调查报告", "20260714", "90611400"),
    ("丙地物体打击事故调查报告", "20260715", "90611500"),
    ("丁地机械伤害事故调查报告", "20260716", "90611600"),
    ("戊地触电事故调查报告", "20260717", "90611700"),
]

_PDF_HREF = "./P020260713387609310237.pdf"
_PDF_URL = _LIST_DIR + "P020260713387609310237.pdf"


def _detail_url(date: str, seq: str) -> str:
    return f"{_LIST_DIR}t{date}_{seq}.html"


def _list_html() -> str:
    items = "".join(
        f'<li><a href="./202607/t{d}_{s}.html">{t}</a></li>'
        for t, d, s in _LINKS)
    return f"<html><body><ul>{items}</ul></body></html>"


def _detail_html(title: str, with_pdf: bool = True, body: str = "页面正文。" * 30) -> str:
    pdf_tag = f'<a href="{_PDF_HREF}">附件：调查报告</a>' if with_pdf else ""
    return (f"<html><head><title>{title}</title></head>"
            f"<body><div class='con'>{body}</div>{pdf_tag}</body></html>")


def _collector(tmp_path: Path, *, with_pdf: bool = True,
               body: str = "页面正文。" * 30) -> TrsCmsCollector:
    """五条条目的不联网采集器。

    五条是刻意的：`collect()` 在 `len(results) % 5 == 0` 时才批量写库，
    不足五条根本不走那条分支——串批的缺陷只有攒够一批才现形。
    """
    cfg = TrsSiteConfig(
        source_name="tsrc",
        authority_name="测试厅",
        base_url="https://tsrc.gov.cn",
        list_url=_LIST_URL,
        list_page_pattern="",              # 关掉分页探测，只抓一页
        list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
        construction_keywords=["事故"],
        enable_classify_v2=False,          # 走 pending_review 分支，不跑规则分类
        min_text_length=30,
        delay=0,
    )
    c = TrsCmsCollector(cfg, data_dir=tmp_path / "src")
    c.raw_store = RawAccidentStore(filepath=tmp_path / "raw.json")
    details = {_detail_url(d, s): _detail_html(t, with_pdf=with_pdf, body=body)
               for t, d, s in _LINKS}
    c._fetch = lambda url: _list_html() if url == _LIST_URL else details.get(url, "")
    return c


def _close(c: TrsCmsCollector) -> None:
    client = getattr(c, "_client", None)
    if client is not None:
        client.close()


# --------------------------------------------------------------------------
# A 组：collect() 产出的字段
# --------------------------------------------------------------------------


def test_PDF型条目把URL传出并标needs_ocr为假(tmp_path):
    """改前这里什么都没有——PDF 下载了却登记不上，阶段② 无从知道该取哪些文件。"""
    c = _collector(tmp_path)
    c._download_and_extract_pdf = lambda u, t, d: ("PDF 里的正文。" * 50, "/x/a.pdf")
    try:
        c.collect()
        items = c.raw_store.get_all()
        assert len(items) == 5
        for item in items:
            assert item["pdf_urls"] == [_PDF_URL]
            # 正文已经拿到了，它本来要的不是 OCR
            assert item["needs_ocr"] is False
            # 正文用的是 PDF 那份（combined_text = pdf_text or detail_text）
            assert "PDF 里的正文" in item["content"]
    finally:
        _close(c)


def test_PDF提不出正文时标needs_ocr为真(tmp_path):
    """扫描件和老 `.doc`（OLE2）`fitz` 读不动 —— 必须让下游知道正文没拿到。

    判据是「有 PDF URL **且** PDF 正文为空」，而不是「合并正文为空」：
    `sh_em` 实测 HTML 正文 631 字、PDF 里 8,635 字，HTML 那份非空不影响
    「附件里的主体还没拿到」这个事实。
    """
    c = _collector(tmp_path)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["needs_ocr"] is True
        # URL 仍要留着，人工审核时得能点开
        assert item["pdf_urls"] == [_PDF_URL]
    finally:
        _close(c)


def test_无PDF时不标needs_ocr(tmp_path):
    c = _collector(tmp_path, with_pdf=False)
    c._download_and_extract_pdf = lambda u, t, d: ("不该被调用", "/x/a.pdf")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["pdf_urls"] == []
        assert item["needs_ocr"] is False
    finally:
        _close(c)


def test_每条的日期各归各的不串批(tmp_path):
    """**本轮修的既有缺陷。** 批量写库时前 4 条被写成第 5 条的日期。

    原代码：
        self.raw_store.add_batch([
            {"publish_date": detail_date, "status": status, ...}
            for r in results[-5:]])
    `detail_date` / `status` 是循环外变量 = **当前（第 5）条**的值，而
    `results[-5:]` 是前五条。于是批次里前 4 条的日期全部错位。

    错位在批次边界上，单条测试测不出来：`results` 不足 5 条时不走这条分支，
    恰好 5 条时最后一条又是对的。
    """
    c = _collector(tmp_path)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        items = c.raw_store.get_all()
        got = [it["publish_date"] for it in items]
        want = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for _, d, _ in _LINKS]
        assert got == want, f"日期串批: {got} != {want}"
        # 五条各不相同 —— 「全部等于第 5 条」的写法在这里必挂
        assert len(set(got)) == 5
    finally:
        _close(c)


# --------------------------------------------------------------------------
# B 组：_download_and_extract_pdf 的失败分支
# --------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code: int = 200, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content
        self.text = ""


class _FakeClient:
    def __init__(self, resp: _FakeResp | None = None,
                 exc: Exception | None = None) -> None:
        self.resp, self.exc = resp, exc
        self.calls: list[str] = []

    def get(self, url: str, **kw):
        self.calls.append(url)
        if self.exc is not None:
            raise self.exc
        assert self.resp is not None
        return self.resp

    def close(self) -> None:
        pass


def _bare(tmp_path: Path) -> TrsCmsCollector:
    """只要一个能调 `_download_and_extract_pdf` 的实例，不跑 collect。"""
    cfg = TrsSiteConfig(source_name="tsrc", authority_name="测试厅",
                        base_url="https://tsrc.gov.cn", list_url=_LIST_URL,
                        list_page_pattern="", delay=0)
    return TrsCmsCollector(cfg, data_dir=tmp_path / "src")


def test_下载成功时落盘并返回路径(tmp_path, monkeypatch):
    monkeypatch.setattr(trs_cms_base, "extract_pdf_text",
                        lambda p: "从 PDF 提取的正文")
    c = _bare(tmp_path)
    c._client = _FakeClient(_FakeResp(200, b"%PDF-1.4" + b"x" * 500))
    try:
        text, local = c._download_and_extract_pdf(_PDF_URL, "某报告", "2026-07-13")
        assert text == "从 PDF 提取的正文"
        p = Path(local)
        assert p.exists()
        assert p.stat().st_size == 508
    finally:
        _close(c)


def test_已落盘的文件不再重复下载(tmp_path, monkeypatch):
    """缓存命中：第二次调用同一个 URL 不发请求。"""
    monkeypatch.setattr(trs_cms_base, "extract_pdf_text", lambda p: "缓存正文")
    c = _bare(tmp_path)
    client = _FakeClient(_FakeResp(200, b"%PDF-1.4" + b"x" * 500))
    c._client = client
    try:
        c._download_and_extract_pdf(_PDF_URL, "某报告", "2026-07-13")
        assert len(client.calls) == 1
        text, local = c._download_and_extract_pdf(_PDF_URL, "某报告", "2026-07-13")
        assert text == "缓存正文"
        assert len(client.calls) == 1, "缓存命中却又发了一次请求"
    finally:
        _close(c)


@pytest.mark.parametrize("resp,exc,marker", [
    (_FakeResp(404, b""), None, "PDF HTTP 404"),
    (_FakeResp(403, b"x" * 500), None, "PDF HTTP 403"),
    (_FakeResp(200, b"short"), None, "PDF 响应过短"),
    (None, RuntimeError("connection reset"), "PDF 下载异常"),
])
def test_四种下载失败都留日志且返回空(tmp_path, caplog, resp, exc, marker):
    """**原先是 `except Exception: pass`。** 四种失败压成同一个 `("", "")`。

    它们的后果并不相同：网络异常和落盘失败可能是暂时的，HTTP 404 说明链接
    本身已经失效。混在一起、且不留痕迹，就只能靠人重新抓一遍才知道发生了什么。
    """
    c = _bare(tmp_path)
    c._client = _FakeClient(resp, exc)
    try:
        with caplog.at_level(logging.DEBUG,
                             logger="construction_agent.collector.sources.trs_cms_base"):
            text, local = c._download_and_extract_pdf(_PDF_URL, "某报告", "2026-07-13")
        assert (text, local) == ("", "")
        assert marker in caplog.text
        assert _PDF_URL in caplog.text
    finally:
        _close(c)


def test_落盘失败是warning级(tmp_path, monkeypatch, caplog):
    """磁盘写不进去属于环境问题，不该只在 DEBUG 下可见。"""
    c = _bare(tmp_path)
    c._client = _FakeClient(_FakeResp(200, b"%PDF-1.4" + b"x" * 500))

    def boom(self, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", boom)
    try:
        with caplog.at_level(logging.WARNING,
                             logger="construction_agent.collector.sources.trs_cms_base"):
            text, local = c._download_and_extract_pdf(_PDF_URL, "某报告", "2026-07-13")
        assert (text, local) == ("", "")
        assert any(r.levelno == logging.WARNING for r in caplog.records)
        assert "PDF 落盘失败" in caplog.text
    finally:
        _close(c)


def test_落盘文件名沿用URL哈希(tmp_path, monkeypatch):
    """缓存文件名 = `{日期}_{安全标题}_{md5(url)[:12]}.pdf` —— 换个名字就命中不了缓存。"""
    monkeypatch.setattr(trs_cms_base, "extract_pdf_text", lambda p: "x")
    c = _bare(tmp_path)
    c._client = _FakeClient(_FakeResp(200, b"%PDF-1.4" + b"x" * 500))
    try:
        _, local = c._download_and_extract_pdf(_PDF_URL, "某/报告:名*", "2026-07-13")
        name = Path(local).name
        assert name.startswith("2026-07-13_")
        assert name.endswith(hashlib.md5(_PDF_URL.encode()).hexdigest()[:12] + ".pdf")
        # 标题里的路径分隔符与通配符必须被清掉，否则会写到别的目录去
        assert "/" not in name and ":" not in name and "*" not in name
    finally:
        _close(c)


# --------------------------------------------------------------------------
# C 组：extracted_date 的类型契约
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expect", [
    ("2026-07-13", date(2026, 7, 13)),
    # `re.match` 只看开头，后面带时间也认 —— 详情页常写成「2026-07-13 10:00」
    ("2026-07-13 10:00", date(2026, 7, 13)),
    ("", None),
    (None, None),
    ("2026-7-13", None),        # 月日不补零：正则要求两位
    ("2026-02-30", None),       # 形状对、值非法 —— 得在构造 date 时挡下来
])
def test_日期解析返回date对象(raw, expect):
    """**返回类型是 `date`，不是 `str`。**

    原先 `return date_str` 把字符串原样交出去，而 `RawAccidentText.
    extracted_date` 的注解是 `Optional[date]`，下游（`generate_fingerprint`
    的 `.strftime`、`executor._row_from_raw_item` 的 `.isoformat`）全按 date 用。
    """
    got = TrsCmsCollector._parse_date_str(raw)
    assert got == expect
    assert isinstance(got, date) or got is None, f"返回了 {type(got).__name__}"


def test_产出能被通路B转换(tmp_path):
    """**探针从来没覆盖过的一环。**

    历次健康检查与各源探针挂的都是只计数、不做转换的 `NullSink`，而真实的
    `_row_from_raw_item` 只在 executor 里跑。于是「通路 B 有产出」这个计数
    一路正常，实际上转换必崩、数据一条都没进库。

    这条测试把那段真实转换接上：`collect()` 的返回值喂给 `_row_from_raw_item`，
    断言它不抛异常，且日期、指纹、附件三样都对。
    """
    from construction_agent.collector.executor import _row_from_raw_item
    c = _collector(tmp_path)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        ret = c.collect()
        assert len(ret) == 5
        row = _row_from_raw_item(ret[0], "tsrc", "task1")
        assert row is not None
        assert row["publish_date"] == "2026-07-13"
        # 指纹要算得出来 —— extracted_date 是 str 时这里当场 AttributeError
        assert row["fingerprint"]
        assert row["title_hash"]
        assert row["_assets"] == [{"asset_type": "pdf", "url": _PDF_URL}]
    finally:
        _close(c)


def test_日期解析失败时通路B仍能转换(tmp_path):
    """日期抠不出来（`extracted_date=None`）不该连累整条记录 —— 其余字段照常。

    **注意不能靠「详情页没写日期」来造这个场景**：`collect()` 里
    `detail_date = detail.get("date") or list_date` 会回退到列表页 URL 里抠出来的
    日期，那是刻意的兜底（TRS 站的日期基本只出现在 URL 里）。所以这里直接构造
    `extracted_date=None` 的产出。
    """
    from construction_agent.collector.base import RawAccidentText
    from construction_agent.collector.executor import _row_from_raw_item

    raw = RawAccidentText(
        source="tsrc", title="甲地隧道坍塌事故调查报告",
        raw_text="页面正文。" * 30, source_url="https://tsrc.gov.cn/list/a.html",
        fetch_time="2026-10-01T00:00:00",
        extracted_date=TrsCmsCollector._parse_date_str(""),   # → None
        extra={"classification": "pending_review", "pdf_urls": [],
               "needs_ocr": False},
    )
    row = _row_from_raw_item(raw, "tsrc", "task1")
    assert row is not None
    assert row["publish_date"] is None
    assert row["_assets"] == []
    assert row["fingerprint"]      # 没有日期也要算得出指纹
