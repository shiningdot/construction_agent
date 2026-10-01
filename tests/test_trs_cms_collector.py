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
# 第二条附件：`sh_em` 实测有页面同时挂两份**不同事故**的评估报告
_PDF_HREF2 = "./P020260713387609310238.pdf"
_PDF_URL2 = _LIST_DIR + "P020260713387609310238.pdf"


def _detail_url(date: str, seq: str) -> str:
    return f"{_LIST_DIR}t{date}_{seq}.html"


def _list_html() -> str:
    items = "".join(
        f'<li><a href="./202607/t{d}_{s}.html">{t}</a></li>'
        for t, d, s in _LINKS)
    return f"<html><body><ul>{items}</ul></body></html>"


def _detail_html(title: str, with_pdf: bool = True, body: str = "页面正文。" * 60,
                 pdf_hrefs: list[str] | None = None,
                 img_hrefs: list[str] | None = None,
                 footer: str = "") -> str:
    hrefs = (pdf_hrefs if pdf_hrefs is not None
             else ([_PDF_HREF] if with_pdf else []))
    tags = "".join(f'<a href="{h}">附件：调查报告</a>' for h in hrefs)
    imgs = "".join(f'<img src="{h}">' for h in (img_hrefs or []))
    return (f"<html><head><title>{title}</title></head>"
            f"<body><div class='con'>{body}</div>{tags}{imgs}"
            f"{footer}</body></html>")


def _collector(tmp_path: Path, *, with_pdf: bool = True,
               body: str = "页面正文。" * 60,
               pdf_hrefs: list[str] | None = None,
               pdf_max: int = 5,
               img_hrefs: list[str] | None = None,
               image_max: int = 50,
               footer: str = "") -> TrsCmsCollector:
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
        detail_pdf_max=pdf_max,
        detail_image_max=image_max,
        delay=0,
    )
    c = TrsCmsCollector(cfg, data_dir=tmp_path / "src")
    c.raw_store = RawAccidentStore(filepath=tmp_path / "raw.json")
    # **每条正文各不相同**（后缀带序号）。全都一样的话会被「跨页恒定行 =
    # 模板文字」那条判据（`collector/boilerplate.py`）整段剥掉——判据没错，
    # 是 fixture 不真实：五份不同的调查报告不会一字不差。
    details = {_detail_url(d, s): _detail_html(t, with_pdf=with_pdf,
                                               body=f"{body}（第 {i} 条）",
                                               pdf_hrefs=pdf_hrefs,
                                               img_hrefs=img_hrefs,
                                               footer=footer)
               for i, (t, d, s) in enumerate(_LINKS)}
    c._fetch = lambda url: _list_html() if url == _LIST_URL else details.get(url, "")
    return c


def _close(c: TrsCmsCollector) -> None:
    client = getattr(c, "_client", None)
    if client is not None:
        client.close()


# --------------------------------------------------------------------------
# D 组：模板文字剥离（2026-10-01 第二十三轮，缺陷 G）
# --------------------------------------------------------------------------
#
# 改前 `detail_text` 是 `body` 全文，导航与页脚全在里面。实测 `hain_em` 的
# `body` 1,226 字里模板占 **1,128 字**，真正文只剩 98 字——而 98 这个数从来
# 没有任何一道防线看到过，因为 `min_text_length` 量的是 1,226。

_FOOTER = "".join(f"<p>页脚导航第{i}项。</p>" for i in range(40))


def test_跨页恒定的页脚不进行正文(tmp_path):
    c = _collector(tmp_path, footer=_FOOTER)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert "页脚导航" not in item["content"]
        assert "页面正文" in item["content"]
    finally:
        _close(c)


def test_真正文不足阈值时入审核队列(tmp_path):
    """**缺陷 G 的回归测试。**

    五页各挂一段 60 字正文加一段 40 行页脚。改前 `combined_text` 是 `body`
    全文（约 500 字），稳过 `min_text_length(30)`，条目被判成「有正文」而
    直接进库；剥掉页脚后只剩 60 余字，落进 `BODY_MIN_CHARS(200)` 之下，
    才第一次被判成「无有效正文」。

    实测形态：`hain_em` 真正文 98 字、`tj_em` 61 字、`sx_em` 140 字、
    `gz_em` 103 字——而这四个源的 `body` 都在 300–1,230 字之间。
    """
    c = _collector(tmp_path, body="短正文。" * 15, footer=_FOOTER)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        items = c.raw_store.get_all()
        assert len(items) == 5
        for item in items:
            assert item["classification_reason"] == "无有效正文"
            # 正文本身还在产出里，只是长度不够——不是被丢掉了
            assert "短正文" in item["content"]
    finally:
        _close(c)


def test_正文够长时不受影响(tmp_path):
    """剥模板不该把正常条目也推进审核队列。"""
    c = _collector(tmp_path, body="页面正文。" * 120, footer=_FOOTER)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["classification_reason"] == "未启用分类"
        assert "页脚导航" not in item["content"]
    finally:
        _close(c)


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


# --------------------------------------------------------------------------
# D 组：一条详情页挂多个附件（缺陷 F，2026-10-01 第十五轮查出）
#
# 旧写法 `pdf_url = ...; break` 只留第一个附件，而多附件是真实存在的形态，
# 且往往是最要紧的那种：`sh_em` 的 /xxgk/…/202507/36eba1e4….html 一份页面上
# 同时挂着**两个不同事故**的评估报告（崇明兴海联「2·26」+ 江南造船「12·17」）
# ——正是三分类里 `multi` 的典型场景。第二份从不被下载、也不被登记。
# --------------------------------------------------------------------------


def test_两个附件都下载且正文合并(tmp_path):
    """两份报告的正文都要进来，不能只留第一份。"""
    c = _collector(tmp_path, pdf_hrefs=[_PDF_HREF, _PDF_HREF2])
    calls: list[str] = []

    def fake(url, title, date):
        calls.append(url)
        return f"来自 {url} 的正文", "/x/a.pdf"

    c._download_and_extract_pdf = fake
    try:
        c.collect()
        # 5 条 × 每条 2 个附件
        assert len(calls) == 10, f"下载了 {len(calls)} 次，应为 10"
        assert set(calls) == {_PDF_URL, _PDF_URL2}
        for item in c.raw_store.get_all():
            assert item["pdf_urls"] == [_PDF_URL, _PDF_URL2]
            # **合并**，不是「取第一个非空的」
            assert f"来自 {_PDF_URL} 的正文" in item["content"]
            assert f"来自 {_PDF_URL2} 的正文" in item["content"]
            assert item["needs_ocr"] is False
    finally:
        _close(c)


def test_同一个PDF挂两个链接只下载一次(tmp_path):
    """`sc_em` 实测存在这种页面：正文里链一次、附件区再列一次，href 完全相同。

    不去重就会把同一份 PDF 下两遍——一条页面还好，全站采集就是成倍的白跑。
    """
    c = _collector(tmp_path, pdf_hrefs=[_PDF_HREF, _PDF_HREF])
    calls: list[str] = []

    def fake(url, title, date):
        calls.append(url)
        return "正文", "/x/a.pdf"

    c._download_and_extract_pdf = fake
    try:
        c.collect()
        assert len(calls) == 5, f"5 条各下载 {len(calls) // 5} 次，应各 1 次"
        assert c.raw_store.get_all()[0]["pdf_urls"] == [_PDF_URL]
    finally:
        _close(c)


def test_超过上限只下载前N个但URL全部登记(tmp_path):
    """上限防的是病态页面（一次挂十几份报告的汇总页）让下载耗时线性膨胀。

    **登记与下载的数目故意不同**：`pdf_urls` 登记**全部**（阶段② 可能要用
    到没下载的那几份），只对**下载**设上限。少登记等于替下游做决定。
    """
    hrefs = [f"./P02026071338760{i:06d}.pdf" for i in range(8)]
    urls = [_LIST_DIR + h[2:] for h in hrefs]
    c = _collector(tmp_path, pdf_hrefs=hrefs, pdf_max=3)
    calls: list[str] = []

    def fake(url, title, date):
        calls.append(url)
        return "正文", "/x/a.pdf"

    c._download_and_extract_pdf = fake
    try:
        c.collect()
        assert len(calls) == 15, f"5 条 × 3 个上限 = 15，实际 {len(calls)}"
        item = c.raw_store.get_all()[0]
        assert item["pdf_urls"] == urls, "URL 应当全部登记"
    finally:
        _close(c)


def test_一份提不出来另一份能提出来(tmp_path):
    """`needs_ocr` 判的是「所有附件都提不出正文」，不是「第一个提不出来」。"""
    c = _collector(tmp_path, pdf_hrefs=[_PDF_HREF, _PDF_HREF2])
    c._download_and_extract_pdf = lambda u, t, d: (
        ("", "") if u == _PDF_URL else ("能读的那份正文", "/x/a.pdf"))
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["needs_ocr"] is False
        assert "能读的那份正文" in item["content"]
        # 两份 URL 都要留着，人工审核时得能点开看不出来的那份
        assert item["pdf_urls"] == [_PDF_URL, _PDF_URL2]
    finally:
        _close(c)


def test_所有附件都提不出来时标needs_ocr(tmp_path):
    c = _collector(tmp_path, pdf_hrefs=[_PDF_HREF, _PDF_HREF2])
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["needs_ocr"] is True
        assert item["pdf_urls"] == [_PDF_URL, _PDF_URL2]
    finally:
        _close(c)


def test_多个附件的条目通路B登记多个asset(tmp_path):
    """登记进 `raw_accident_assets` 的也必须是两个——那是阶段②的取件清单。"""
    from construction_agent.collector.executor import _row_from_raw_item
    c = _collector(tmp_path, pdf_hrefs=[_PDF_HREF, _PDF_HREF2])
    c._download_and_extract_pdf = lambda u, t, d: ("正文", "/x/a.pdf")
    try:
        ret = c.collect()
        row = _row_from_raw_item(ret[0], "tsrc", "task1")
        assert row["_assets"] == [
            {"asset_type": "pdf", "url": _PDF_URL},
            {"asset_type": "pdf", "url": _PDF_URL2},
        ]
    finally:
        _close(c)


# --------------------------------------------------------------------------
# E 组：内容图（缺陷 C，2026-10-01 第十八轮定的判据）
# --------------------------------------------------------------------------
#
# 详情页 URL 是 `https://tsrc.gov.cn/list/202607/t20260713_90611300.html`，
# 所以：
#   · 同目录     `./W0202607….png`                      → 内容图
#   · 路径前缀   `./t20260713_90611300/images/0001.jpg` → 内容图（sc_em 形态）
#   · 模板图     `/material/2021_static/logo.png`       → 跨目录，必须排除


_IMG_HREF = "./W020260713387609310237.png"
_IMG_URL = _LIST_DIR + "W020260713387609310237.png"
_IMG_HREF2 = "./W020260713387609310238.png"
_IMG_URL2 = _LIST_DIR + "W020260713387609310238.png"
# 模板图：`tj_em` 实测的形态就是「同目录 + 时间戳连续」，所以判据只能靠
# 目录对不对，不能靠命名。
_TPL_HREF = "/material/2021_static/logo.png"
_TPL_URL = "https://tsrc.gov.cn/material/2021_static/logo.png"


def test_同目录图登记为内容图(tmp_path):
    """改前 `_crawl_detail` 对图一个字都不读，图片型报告到采集层就丢了图片这一维。"""
    c = _collector(tmp_path, img_hrefs=[_IMG_HREF])
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["image_urls"] == [_IMG_URL]
    finally:
        _close(c)


def test_跨目录的模板图不登记(tmp_path):
    """模板图必须排除。

    `tj_em` 实测每页都挂同一批 10 张 `/qdfy/tytb/202008/` 的图，而它是四个
    **不同**的详情页（URL 与 `<title>` 两两不同）——那批图是栏目模板图。
    判据若不看目录，这些图会被逐条登记成「扫描页」。
    """
    c = _collector(tmp_path, img_hrefs=[_TPL_HREF])
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["image_urls"] == []
    finally:
        _close(c)


def test_以页面路径为前缀的图也算内容图(tmp_path):
    """`sc_em` 形态：正文目录就是文章 hash 本身。

    实测 `…/b3eebb40….html` 配 `…/b3eebb40…/images/0001.jpg`——**不同目录**，
    但图片在页面路径之下，是同一篇文章自己的图。
    """
    href = "./t20260713_90611300/images/0001.jpg"
    url = _LIST_DIR + "t20260713_90611300/images/0001.jpg"
    c = _collector(tmp_path, img_hrefs=[href])
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["image_urls"] == [url]
    finally:
        _close(c)


def test_同目录图与模板图混在一页时只登记前者(tmp_path):
    c = _collector(tmp_path, img_hrefs=[_IMG_HREF, _TPL_HREF, _IMG_HREF2])
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["image_urls"] == [_IMG_URL, _IMG_URL2]
    finally:
        _close(c)


def test_同一张图挂两次只登记一次(tmp_path):
    c = _collector(tmp_path, img_hrefs=[_IMG_HREF, _IMG_HREF])
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["image_urls"] == [_IMG_URL]
    finally:
        _close(c)


def test_超过图片上限只登记前N张(tmp_path):
    c = _collector(tmp_path, img_hrefs=[f"./W02026071338760931{i:04d}.png"
                                        for i in range(8)],
                   image_max=3)
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert len(item["image_urls"]) == 3
    finally:
        _close(c)


def test_上限为0时不抽取图片(tmp_path):
    c = _collector(tmp_path, img_hrefs=[_IMG_HREF], image_max=0)
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["image_urls"] == []
    finally:
        _close(c)


def test_内容图的条目通路B登记image类型asset(tmp_path):
    """`executor._ASSET_KEYS` 里早有 `("image_urls", "image")`，却从来没有源
    往外传这个键——`raw_accident_assets` 里 `asset_type='image'` 一直是 0 行。"""
    from construction_agent.collector.executor import _row_from_raw_item
    c = _collector(tmp_path, with_pdf=False, img_hrefs=[_IMG_HREF])
    try:
        ret = c.collect()
        row = _row_from_raw_item(ret[0], "tsrc", "task1")
        assert row["_assets"] == [{"asset_type": "image", "url": _IMG_URL}]
    finally:
        _close(c)


def test_图片与PDF可以挂在同一条上(tmp_path):
    """`sh_em` 实测有页面既挂 PDF 又带插图，两条通路互不干扰。"""
    from construction_agent.collector.executor import _row_from_raw_item
    c = _collector(tmp_path, pdf_hrefs=[_PDF_HREF], img_hrefs=[_IMG_HREF])
    c._download_and_extract_pdf = lambda u, t, d: ("PDF 正文", "/x/a.pdf")
    try:
        ret = c.collect()
        row = _row_from_raw_item(ret[0], "tsrc", "task1")
        assert row["_assets"] == [
            {"asset_type": "pdf", "url": _PDF_URL},
            {"asset_type": "image", "url": _IMG_URL},
        ]
    finally:
        _close(c)


def test_图片走的是URL而不是下载(tmp_path):
    """采集层只登记 URL，**不下载图片**——下载是阶段② OCR 时的事。

    `nmg_em` 一个源实测 121 张内容图，全下等于把采集任务变成图片搬运工。
    """
    c = _collector(tmp_path, img_hrefs=[_IMG_HREF], with_pdf=False)
    calls: list[str] = []

    def _spy(url, *a, **kw):
        calls.append(url)
        raise AssertionError(f"不该下载图片: {url}")

    c._client.get = _spy
    try:
        c.collect()
        assert calls == []
    finally:
        _close(c)
