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
import ssl
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector import registry  # noqa: E402
from construction_agent.collector.sources import trs_cms_base  # noqa: E402
from construction_agent.collector.sources.trs_cms_base import (  # noqa: E402
    TrsCmsCollector, TrsSiteConfig, build_ssl_verify)
from construction_agent.collector.stores import RawAccidentStore  # noqa: E402

_LIST_URL = "https://tsrc.gov.cn/list/"
_LIST_DIR = "https://tsrc.gov.cn/list/202607/"

# (标题, URL 里的日期段, URL 里的序号段) —— 标题含「事故」以过一级筛选
#
# **七条，默认只用前五条。** 第七、八两组是第三十二轮加的：本轮那个缺陷
# （末批只 `_save()` 不 `add_batch()`）**只在产出条数不是 5 的倍数时现形**，
# 而此前全部 21 条测试都用 5 条——恰好落在那条分支的正中间，一条都测不到。
_LINKS = [
    ("甲地隧道坍塌事故调查报告", "20260713", "90611300"),
    ("乙地脚手架坠落事故调查报告", "20260714", "90611400"),
    ("丙地物体打击事故调查报告", "20260715", "90611500"),
    ("丁地机械伤害事故调查报告", "20260716", "90611600"),
    ("戊地触电事故调查报告", "20260717", "90611700"),
    ("己地高处坠落事故调查报告", "20260718", "90611800"),
    ("庚地基坑坍塌事故调查报告", "20260719", "90611900"),
]

_PDF_HREF = "./P020260713387609310237.pdf"
_PDF_URL = _LIST_DIR + "P020260713387609310237.pdf"
# 第二条附件：`sh_em` 实测有页面同时挂两份**不同事故**的评估报告
_PDF_HREF2 = "./P020260713387609310238.pdf"
_PDF_URL2 = _LIST_DIR + "P020260713387609310238.pdf"


def _detail_url(date: str, seq: str) -> str:
    return f"{_LIST_DIR}t{date}_{seq}.html"


def _list_html(n: int = 5) -> str:
    items = "".join(
        f'<li><a href="./202607/t{d}_{s}.html">{t}</a></li>'
        for t, d, s in _LINKS[:n])
    return f"<html><body><ul>{items}</ul></body></html>"


def _detail_html(title: str, with_pdf: bool = True, body: str = "页面正文。" * 60,
                 pdf_hrefs: list[str] | None = None,
                 img_hrefs: list[str] | None = None,
                 office_hrefs: list[str] | None = None,
                 footer: str = "") -> str:
    hrefs = (pdf_hrefs if pdf_hrefs is not None
             else ([_PDF_HREF] if with_pdf else []))
    # Word 附件与 PDF 附件在**页面结构上完全一样**——都是 `<a href="…">`，
    # 区别只在后缀。旧代码分不开它们，正是因为它比的是硬编码的 `.pdf`。
    hrefs = hrefs + list(office_hrefs or [])
    tags = "".join(f'<a href="{h}">附件：调查报告</a>' for h in hrefs)
    imgs = "".join(f'<img src="{h}">' for h in (img_hrefs or []))
    # 非附件链接（导航 / 返回列表 / 相关信息）。第三十五轮把选择器从
    # `a[href$='.pdf']` 放宽到 `a[href]` 之后，**全页的每一个 `<a>` 都会过
    # 一遍判据**，所以页面上必须有这类链接，否则测不到「放宽 ≠ 误收」。
    nav = ('<a href="./202607/t20260713_90611999.html">上一页</a>'
           '<a href="/index.html">首页</a>')
    return (f"<html><head><title>{title}</title></head>"
            f"<body><div class='con'>{body}</div>{tags}{imgs}{nav}"
            f"{footer}</body></html>")


def _collector(tmp_path: Path, *, n: int = 5, with_pdf: bool = True,
               body: str = "页面正文。" * 60,
               pdf_hrefs: list[str] | None = None,
               pdf_max: int = 5,
               img_hrefs: list[str] | None = None,
               image_max: int = 50,
               office_hrefs: list[str] | None = None,
               footer: str = "") -> TrsCmsCollector:
    """`n` 条条目的不联网采集器，默认五条。

    **默认五条是历史的**：`collect()` 在 `len(results) % 5 == 0` 时才批量
    写库，串批那个缺陷（§2.5.26）只有攒够一批才现形。但五条**恰好避开**了
    另一条分支——末批余数。第三十二轮那个缺陷（产出不是 5 的倍数时余数
    彻底丢失）正是被「全部测试都用五条」掩盖到今天的，所以要用 `n=` 显式
    指定别的条数。
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
                                               office_hrefs=office_hrefs,
                                               footer=footer)
               for i, (t, d, s) in enumerate(_LINKS[:n])}
    c._fetch = lambda url: _list_html(n) if url == _LIST_URL else details.get(url, "")
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


def test_既无附件也无内容图时不标needs_ocr(tmp_path):
    """**原名是「无 PDF 时不标」——那个说法第二十七轮起不成立了。**

    判据补了图片那一支之后，「无 PDF」不再蕴含「不标」：无 PDF 但有内容图、
    且正文区短，照样要标。这一条钉的是它真正的条件——两样都没有。
    """
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
        want = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for _, d, _ in _LINKS[:5]]
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


# --------------------------------------------------------------------------
# `needs_ocr` 的图片支（2026-10-01 第二十七轮）
#
# 改前的判据是 `bool(pdf_urls) and not pdf_text`——**只看 PDF**。于是图片型报告
# （正文区是空的、正文全在扫描页里）恒为 `False`：`sc_em` 实测那条「正文 154 字
# + 24 张 `…/images/0001.jpg` 就是一份调查报告全部内容」的记录，产出里标的是
# 「不需要 OCR」，阶段② 永远不会去取那 24 张图。
#
# 图片没有「提出文字」这个事件可用（采集层不做 OCR），所以这一支拿「正文区够
# 不够」当代理；带 `BODY_MIN_CHARS` 那一半是为了别把 OCR 队列稀释掉。
# 判据全文与理由见 `trs_cms_base.py` 里 `needs_ocr` 上方那段注释。
# --------------------------------------------------------------------------


def _short_body() -> str:
    """正文区只有一百来字——图片型报告的样子（`sc_em` 实测 154 字）。"""
    return "短正文。" * 20                          # 100 字，在 200 之下


def _scan_hrefs(n: int = 24) -> list[str]:
    """一页挂 n 张同目录图 —— 扫描件报告的形态。"""
    return [f"./W0202607133876093{i:04d}.png" for i in range(1, n + 1)]


def test_图片型条目标needs_ocr为真(tmp_path):
    """改前恒为 `False` 的那一类。"""
    c = _collector(tmp_path, with_pdf=False, body=_short_body(),
                   img_hrefs=_scan_hrefs())
    try:
        c.collect()
        items = c.raw_store.get_all()
        assert len(items) == 5
        for item in items:
            assert len(item["image_urls"]) == 24
            assert item["needs_ocr"] is True
    finally:
        _close(c)


def test_有内容图但正文够长时不标needs_ocr(tmp_path):
    """带 `BODY_MIN_CHARS` 那一半的作用：别把 OCR 队列稀释掉。

    `gz_em` 有一条挂 30 张图、却有 16,670 字正文的记录。图片支若只判「有没有
    内容图」，每个带插图的普通页面都会进 OCR 队列。
    """
    c = _collector(tmp_path, with_pdf=False, img_hrefs=[_IMG_HREF])
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["image_urls"] == [_IMG_URL]
        assert item["needs_ocr"] is False
    finally:
        _close(c)


def test_判据自检_把图拿掉就不标了(tmp_path):
    """同一条正文，只把图去掉——标不标必须跟着变。

    没有这一条，上面那条可能只是在断言一个恒真的东西（正文短本来就不标）。
    """
    c = _collector(tmp_path, with_pdf=False, body=_short_body())
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["image_urls"] == []
        assert item["needs_ocr"] is False
    finally:
        _close(c)


def test_附件已提出正文时图片支仍独立成立(tmp_path):
    """两支是**或**的关系，互不覆盖。

    有 PDF 且提出了正文（第一支为假），但正文区仍是空的、还挂着内容图——图片
    里的字照样没取过，第二支独立判真。
    """
    c = _collector(tmp_path, body=_short_body(), img_hrefs=[_IMG_HREF])
    c._download_and_extract_pdf = lambda u, t, d: ("PDF 里的正文。" * 50, "/x/a.pdf")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["pdf_urls"] == [_PDF_URL]
        assert item["image_urls"] == [_IMG_URL]
        assert item["needs_ocr"] is True
    finally:
        _close(c)


# --------------------------------------------------------------------------
# I 组：末批余数（2026-10-01 第三十二轮，缺陷 H）
# --------------------------------------------------------------------------
#
# 改前 `collect()` 的末尾是孤零零一句 `self.raw_store._save()`。周期批写是
# `if len(results) % 5 == 0`，所以**产出条数不是 5 的倍数时，余数那一批从来
# 没进过 `_data`**——`_save()` 只把内存里的 `_data` 写盘，而记录是
# `add_batch` → `add()` 才 append 进去的（挂了 sink 时还要在 `add()` 里
# 把记录交给 sink）。
#
# 实测（同一份 HTML，`collect()` 返回值当真值）：
#
#     产出 3 条 → store 0 条 / sink 0 条     （全丢）
#     产出 5 条 → store 5 条 / sink 5 条     （5 的倍数才全到）
#     产出 6 条 → store 5 条 / sink 5 条     （丢 1）
#     产出 7 条 → store 5 条 / sink 5 条     （丢 2）
#
# 线上代价是**采集链路上的静默数据丢失**：sink 是在 `add()` 里被调的，没进
# `add()` 就交不出去，executor 收不到、工作库里就没有——而采集日志照写
# 「收录 N 条」。TRS 系 12 个源全部继承这个基类，全都中招。
#
# 这条缺陷**从来没被测到**，因为本文件此前 21 条测试**全部用 5 条**，恰好
# 落在那条分支的正中间（见 `_collector` 的 docstring 与 `_LINKS` 的注释）。


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7])
def test_产出条数不是五的倍数时一条不丢(tmp_path, n):
    """缺陷 H 的回归测试：`collect()` 返回几条，store 里就该有几条。

    `n=5` 也在参数表里。它是唯一本来就正确的条数，留着当**反向对照**——
    免得哪天有人把余数分支写成「无条件再补一遍最后五条」，让 5 的倍数反而
    翻倍。这一条同时钉住「既不丢、也不重」。

    断言标题集合而不只是条数：重复写同一批也能凑够条数，集合能拆穿它。
    """
    c = _collector(tmp_path, n=n)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        ret = c.collect()
        items = c.raw_store.get_all()
        assert len(ret) == n
        assert len(items) == n
        assert {i["title"] for i in items} == {r.title for r in ret}
    finally:
        _close(c)


@pytest.mark.parametrize("n", [3, 7])
def test_余数也交给sink(tmp_path, n):
    """在线采集走的是 sink，不是文件——余数丢的正是这一条通路。

    `sink` 在 `RawAccidentStore.add()` 里被调（`stores/__init__.py:163`），
    所以「余数没进 `add()`」等于「执行器一条都收不到、工作库里没有」，而
    采集日志照写「收录 3 条」。这条盯的就是那段缺口。

    顺序一并断言：`add_batch` 先写前 5 条、再写余数，与 `collect()` 的返回
    顺序一致。若哪天有人调换了两个批次的先后，这里会红。
    """
    c = _collector(tmp_path, n=n)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    received: list[dict] = []
    c.raw_store.attach(lambda rec: received.append(rec))
    try:
        ret = c.collect()
        assert len(ret) == n
        assert len(received) == n
        assert [r["source_url"] for r in received] == [r.source_url for r in ret]
    finally:
        _close(c)


def test_判据抓得住旧写法(tmp_path):
    """反改验证：把末批换回旧写法（只 `_save()`），证明上面两条真的会红。

    这里把 `add_batch` 换成「只调 `_save()`」——那正是改前末尾那一句的净
    效果。于是 `test_产出条数不是五的倍数时一条不丢` 里
    `len(items) == n` 会拿到 0，`test_余数也交给sink` 里 `len(received)`
    同样是 0。两条判据都对写法敏感，不是写什么都会绿的摆设。
    """
    c = _collector(tmp_path, n=3)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")

    def _old_style_add_batch(items):
        c.raw_store._save()          # 改前末尾那一句就是这个
        return 0

    c.raw_store.add_batch = _old_style_add_batch
    try:
        ret = c.collect()
        assert len(ret) == 3                   # 采集本身照样产出三条
        assert c.raw_store.get_all() == []     # 旧写法下 store 是空的
        assert c.raw_store.get_new() == []     # 待处理队列里也没有
    finally:
        _close(c)


# --------------------------------------------------------------------------
# J 组：附件判据被写死成「PDF 附件」（2026-10-01 第三十五轮）
# --------------------------------------------------------------------------
#
# 改前这条通路有两层，**两层都只认 PDF**：
#
#     detail_pdf_selector = "a[href$='.pdf']"      # 选择器层
#     if href.endswith(".pdf"): …                   # 循环体层
#
# 于是 `.docx` / `.doc` 从选择器那一层就进不来，**连一条 warning 都没有**——
# 12 个 TRS 子类**无一覆写** `detail_pdf_selector`，12 个源一起中招。
#
# 实测 `shaanxi_em`（该假设的最强反例）：
#
#     全站 473 条详情页，`a[href$='.pdf']` 选中的是**空集**——一条 PDF
#     附件都没有；而其中 5 条挂着 Word 附件共 17 个文件，一个都没登记。
#
# 其中两条的**报告本体就在 `.doc` 里**：西安「9·10」重大建筑施工坍塌事故
# （与之一并挂出的还有「11·14」液化石油气泄漏爆炸重大事故），页面正文是
# 一份 7,971 字的通报，第 114 行「附：」之后紧跟着两行锚文本「西安"9·10"…
# 调查报告」「西安"11·14"…调查报告」——**两份完整报告各自一个 `.doc`**。
# **正中论文主题的那起事故，报告本体从来没被采集链路看见过。**
#
# 这是「同一件事各源各写一遍、只有一处写对、而没有地方写着正确答案」那族
# 问题的第五次（前四次见 §2.5.29 / §2.5.32 / §2.5.33 / §2.5.34）。

_DOCX_HREF = "./P020250219344648336403.docx"
_DOCX_URL = _LIST_DIR + "P020250219344648336403.docx"
_DOC_HREF = "./P02014073115562396101714.doc"
_DOC_URL = _LIST_DIR + "P02014073115562396101714.doc"


def test_Word附件进office_urls(tmp_path):
    """`.docx` / `.doc` 都要进 `office_urls`，且**不进** `pdf_urls`。

    改前这两条断言各错一边：前者的期望值是空集（收不到），后者的期望值是
    `[_DOCX_URL]`（Word 被按调用的列表名硬塞进 PDF 那一列）。
    """
    c = _collector(tmp_path, pdf_hrefs=[], office_hrefs=[_DOCX_HREF, _DOC_HREF])
    calls: list[str] = []
    c._download_and_extract_pdf = lambda u, t, d: (calls.append(u), ("", ""))[1]
    try:
        c.collect()
        items = c.raw_store.get_all()
        assert len(items) == 5
        for item in items:
            assert item["office_urls"] == [_DOCX_URL, _DOC_URL]
            assert item["pdf_urls"] == []
        # **Word 只登记、不下载**：`_download_and_extract_pdf` 走的是 PyMuPDF，
        # 拿它去解 Word 只会白跑一趟。转换器是另一个量级的工程，见 `extra`
        # 里 `office_urls` 那段注释。
        assert calls == []
    finally:
        _close(c)


def test_PDF附件仍然进pdf_urls(tmp_path):
    """放宽选择器的回归对照：PDF 那条路一点没变。"""
    c = _collector(tmp_path, office_hrefs=[_DOCX_HREF])
    c._download_and_extract_pdf = lambda u, t, d: ("PDF 正文。" * 40, "/x/a.pdf")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["pdf_urls"] == [_PDF_URL]
        assert item["office_urls"] == [_DOCX_URL]
        # 正文仍只由 PDF 那份构成 —— Word 没混进 `pdf_text`
        assert "PDF 正文" in item["content"]
    finally:
        _close(c)


@pytest.mark.parametrize("href,key", [
    ("./A.DOCX", "office_urls"),          # 大写后缀（站点大小写不统一是常态）
    ("./B.docx?v=2", "office_urls"),      # 带查询串
    ("./C.doc#top", "office_urls"),       # 带锚点
    ("./D.PDF", "pdf_urls"),
    ("./E.pdf?download=1", "pdf_urls"),
])
def test_后缀的大小写查询串锚点都不影响归类(tmp_path, href, key):
    """判据统一在 `attachments.attachment_kind` 里，这里钉住它对 TRS 的表层。

    旧写法 `href.endswith(".pdf")` 对 `./D.PDF` 判否、对 `./E.pdf?download=1`
    也判否——**同一个 PDF 换个写法就收不到**。而 `a[href$='.pdf']` 选择器
    同样匹配不到带查询串的那条，两层错法还不一样。
    """
    c = _collector(tmp_path, pdf_hrefs=[], office_hrefs=[href])
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item[key] == [_LIST_DIR + href.removeprefix("./")]
    finally:
        _close(c)


def test_导航链接不会被误收成附件(tmp_path):
    """选择器放宽到 `a[href]` 之后，**全页每一个 `<a>` 都会过一遍判据**。

    fixture 里固定挂着两条非附件链接（`./202607/t20260713_90611999.html`、
    `/index.html`），它们在 `a[href$='.pdf']` 下本就被挡住，靠的是选择器；
    现在选择器不管了，得靠判据挡住。**收进来等于每个页面白登记几条附件。**
    """
    c = _collector(tmp_path, with_pdf=False)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["pdf_urls"] == []
        assert item["office_urls"] == []
        assert item["image_urls"] == []
    finally:
        _close(c)


def test_同一个Word附件挂两个链接只登记一次(tmp_path):
    """去重仍在这一层做——`sort_into` 只分拣、不去重。

    `sc_em` 那条实测（正文里链一次、附件区再列一次）本来只覆盖 PDF；加了
    `office_urls` 之后，原先那句 `if full not in pdf_urls` **只查 PDF 那一列**，
    一篇挂两次的 Word 会重复登记。这条钉的是改法，不是改前的行为。
    """
    c = _collector(tmp_path, pdf_hrefs=[],
                   office_hrefs=[_DOCX_HREF, _DOCX_HREF])
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        assert c.raw_store.get_all()[0]["office_urls"] == [_DOCX_URL]
    finally:
        _close(c)


def test_Word附件的条目通路B登记office类型asset(tmp_path):
    """一路走到 `raw_accident_assets`：`executor._ASSET_KEYS` 里**早就**有
    `("office_urls", "office")` 这一项，只是此前没有任何采集器往外传过这个键，
    所以 `asset_type='office'` 一直是 0 行。
    """
    from construction_agent.collector.executor import _row_from_raw_item
    c = _collector(tmp_path, pdf_hrefs=[], office_hrefs=[_DOCX_HREF])
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        ret = c.collect()
        row = _row_from_raw_item(ret[0], "tsrc", "task1")
        assert row is not None
        assert row["_assets"] == [{"asset_type": "office", "url": _DOCX_URL}]
    finally:
        _close(c)


def test_Word附件不并进needs_ocr(tmp_path):
    """`needs_ocr` 指的是**光学字符识别**，Word 里的字本来就是字符。

    这一条钉住「有意不并」这个决定本身：页面只挂 Word、正文区短，也不该
    标 OCR——它缺的是格式转换。代价照实记在 `needs_ocr` 上方的注释里：
    **Word 附件的正文，采集层没有任何人负责**。
    """
    c = _collector(tmp_path, pdf_hrefs=[], office_hrefs=[_DOCX_HREF],
                   body="短正文。" * 20)          # 100 字，在 200 之下
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["office_urls"] == [_DOCX_URL]
        assert item["needs_ocr"] is False
        # 旁证：正文确实短到进了审核队列 —— 排除「因为正文够长才没标」
        assert item["classification_reason"] == "无有效正文"
    finally:
        _close(c)


def test_判据自检_选择器改回以pdf结尾就收不到Word(tmp_path):
    """反改验证（一）：把 `detail_pdf_selector` 改回 `a[href$='.pdf']`。

    这正是改前的原值。若这一条不红，说明上面几条断言其实什么也没测到。
    """
    c = _collector(tmp_path, pdf_hrefs=[], office_hrefs=[_DOCX_HREF])
    c.cfg.detail_pdf_selector = "a[href$='.pdf']"
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        assert c.raw_store.get_all()[0]["office_urls"] == []
    finally:
        _close(c)


def test_判据自检_判据改回endswith就收不到Word(tmp_path, monkeypatch):
    """反改验证（二）：选择器保持放宽，把**循环体的判据**换回 `endswith(".pdf")`。

    旧代码是两层叠加，只还原一层（上一条）不够——这一条还原第二层。判据函数
    在 `trs_cms_base` 的模块命名空间里，改这里等价于换回旧写法。
    """
    monkeypatch.setattr(
        trs_cms_base, "attachment_kind",
        lambda u: "pdf" if str(u).lower().endswith(".pdf") else "")
    c = _collector(tmp_path, pdf_hrefs=[], office_hrefs=[_DOCX_HREF])
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        assert c.raw_store.get_all()[0]["office_urls"] == []
    finally:
        _close(c)


# --------------------------------------------------------------------------
# K 组：站点 TLS 太老，需要放开老式重协商（2026-10-01 第三十七轮）
# --------------------------------------------------------------------------
#
# `jx_em` 在健康检查表上一直是「列表 0 条」，与 `hub_em`（WAF 412 拦）、
# `jl_em`（域名 DNS 解析不了，**2026-10-05 已修**，见 `docs/web/01_在线采集.md`
# §2.5.53——那是域名笔误 + 列表 JS 渲染两层，与本源毫不相干）
# **逐格长得一模一样**。实测诊断到 TCP 层才发现
# 三者毫不相干：本源的故障在**握手**上——服务器只认 RFC 5746 之前的老式
# 重协商，而 OpenSSL 3.x 默认禁用它（CVE-2009-3555 的缓解措施）。放开后
# TLSv1.2 谈成、HTTP 200 / 271,906 字节，站点本身完全健康。
#
# 修法**不是**全局关掉那个缓解措施，而是逐源显式开关。所以这一组测两件事：
# 开关本身对不对（1–4），以及**十二个源里只有 `jx_em` 开了**（5–7）。


def _legacy_bit_on(client) -> bool:
    """从 httpx client 里读出**实际生效**的 SSLContext，看 legacy 位开没开。

    `httpx.Client` 没有公开的 `verify` 属性，但探针实测
    `client._transport._pool._ssl_context` **就是构造时传进去的那个对象**
    （`is` 判断为真；httpx 0.28.1）。断言实际生效的对象比断言「我们传了什么」
    强——传了但被丢掉也算过的那种测试没有意义。
    """
    ctx = client._transport._pool._ssl_context
    assert ctx is not None, "client 上没有 SSLContext，断言无从谈起"
    return bool(ctx.options & ssl.OP_LEGACY_SERVER_CONNECT)


def _trs_collectors(tmp_path: Path) -> list[tuple[str, TrsCmsCollector]]:
    """构造注册表里全部 TRS 系源（不联网），返回 `[(key, collector)]`。

    **走 `registry` 而不是手写源名单**：手写的话，将来新增一个源忘了同步，
    这组测试会安静地少测一个——而它恰恰是那种「漏测不可见」的测试。

    `registry.build` 返回的是 `(collector, spec)` 元组（执行器两边都要用），
    不是 collector 本身。
    """
    out: list[tuple[str, TrsCmsCollector]] = []
    for spec in registry.all_sources():
        try:
            c, _ = registry.build(spec.key, data_dir=tmp_path / spec.key)
        except Exception as e:      # 某些源构造需要 key/依赖，跳过不影响判据
            logging.debug("构造 %s 失败，跳过：%s", spec.key, e)
            continue
        if isinstance(c, TrsCmsCollector):
            out.append((spec.key, c))
    return out


def test_默认配置不开老式重协商():
    """新字段默认必须是 False——默认值是这道防线的全部意义。"""
    assert TrsSiteConfig().allow_legacy_renegotiation is False


def test_开关关闭时传的就是True():
    """不开开关的源，拿到的仍是 httpx 的默认严格校验（原先的 `verify` 缺省）。"""
    assert build_ssl_verify(False) is True


def test_开关打开时返回SSLContext且legacy位为真():
    ctx = build_ssl_verify(True)
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.options & ssl.OP_LEGACY_SERVER_CONNECT


def test_放开重协商不等于放宽证书校验():
    """**反例**：修法容易被顺手写成 `verify=False`，那等于把证书校验也关掉。

    本轮的故障自始至终是「重协商谈法」，不是「证书不可信」。这个 context
    必须仍然 `CERT_REQUIRED` + 校验主机名——否则抓到的内容有没有被中间人
    改过就没人保证了。
    """
    ctx = build_ssl_verify(True)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_十二个TRS源里只有jx_em开了开关(tmp_path):
    rows = _trs_collectors(tmp_path)
    try:
        assert len(rows) >= 12, f"只构造出 {len(rows)} 个 TRS 源，源名单可能没走到"
        on = sorted(k for k, c in rows if c.cfg.allow_legacy_renegotiation)
        assert on == ["jx_em"], f"开了重协商的源是 {on}，预期只有 jx_em"
    finally:
        for _, c in rows:
            _close(c)


def test_jx_em的client真的拿到了开legacy位的context(tmp_path):
    """配置位 → 函数 → client 的端到端：只有中间任何一环断开这条都会红。"""
    c, _ = registry.build("jx_em", data_dir=tmp_path / "jx_em")
    try:
        assert isinstance(c, TrsCmsCollector)
        assert _legacy_bit_on(c._client) is True
    finally:
        _close(c)


def test_其余TRS源的client都没开legacy位(tmp_path):
    """**反例（集成）**：其余源的实际 context 上这一位必须是 0。

    只测「配置字段是 False」不够——若 `__init__` 里写成
    `build_ssl_verify(True)` 常数，字段就变成摆设，而上面那条仍然会过。
    """
    rows = _trs_collectors(tmp_path)
    try:
        bad = sorted(k for k, c in rows if k != "jx_em" and _legacy_bit_on(c._client))
        assert bad == [], f"这些源不该开 legacy 位却开了：{bad}"
    finally:
        for _, c in rows:
            _close(c)


def test_判据自检_函数改回恒返回True这条就红(tmp_path, monkeypatch):
    """反改验证：把 `build_ssl_verify` 改回「什么都返回 True」。

    这等价于**没修**——`jx_em` 的 client 会退回默认严格校验，握手仍然谈不成。
    若这一条不红，说明 `test_jx_em的client真的拿到了开legacy位的context`
    其实什么也没测到。
    """
    monkeypatch.setattr(trs_cms_base, "build_ssl_verify", lambda _flag: True)
    c, _ = registry.build("jx_em", data_dir=tmp_path / "jx_em")
    try:
        assert _legacy_bit_on(c._client) is False
    finally:
        _close(c)


# --------------------------------------------------------------------------
# L 组：`_list_pages_ok`——列表页正文真取到了几页
# --------------------------------------------------------------------------
#
# 第三十八轮。上一轮把 `jx_em` 的 TLS 修通之后，它在健康检查表上从
# 「没连上」变成了「空」——**与「站点通了、栏目确实没内容」逐格相同**。
# 而它的真相是列表页 HTTP 200 / 271,906 字节（SPA 壳，49 条全在 JSON 接口
# 里）。修好一层只是把一个源从一种「看不出区别」挪到另一种。
#
# `_total_listed` 答的是「解析出几条」，答不了「页面拿到没有」。这一组测的
# 就是这个新计数器：它在哪几种情形下该涨，以及它到底分开了什么。


def test_列表页取到正文时页数加一(tmp_path):
    c = _collector(tmp_path)
    c._download_and_extract_pdf = lambda u, t, d: ("", "")
    try:
        c.collect()
        assert c._list_pages_ok == 1
        assert c._total_listed == 5
    finally:
        _close(c)


def test_列表页压根取不到时页数为零(tmp_path):
    """`hub_em`(412) / `jl_em`(DNS，**2026-10-05 已修**) 那一类：页面一张都没回来。

    `jl_em` 修好之后不再属于这一类（现在是「有数据」），保留它作例子会让这条
    用例读起来像在描述一个已不存在的形态——更贴切的现役样本是 `hub_em`
    （JS 挑战型 WAF）与 `news_aggregator`（百度拦截页）。
    """
    c = _collector(tmp_path)
    c._fetch = lambda url: ""
    try:
        c.collect()
        assert c._list_pages_ok == 0
        assert c._total_listed == 0
    finally:
        _close(c)


def test_页面拿到了却解析不出条目时页数为一而条数为零(tmp_path):
    """**`jx_em` 的形态**：HTTP 200、27 万字节，一个可匹配的 href 都没有。

    列表页是真取到了（`_list_pages_ok == 1`），只是 `list_link_pattern`
    在这个页面上一条都对不上——条目全在 `…-articleList.json` 里。
    """
    c = _collector(tmp_path)
    c._fetch = lambda url: ('<html><body><div class="col">'
                            '<a href="javascript:void(0)">栏目</a>'
                            '</div></body></html>')
    try:
        c.collect()
        assert c._list_pages_ok == 1, "页面明明取到了，这个数必须是 1"
        assert c._total_listed == 0
    finally:
        _close(c)


def test_同一个零条两种来路只有页数分得开(tmp_path):
    """**这是加这个计数器的全部理由。**

    「页面没回来」与「页面回来了但解析不出」在 `_total_listed` 上都是 0，
    在 `_fetch_failed` 上也都是 0（`_fetch` 返回空串不算重试耗尽）。
    2026-10-01 的实测就是这个形状：`jx_em` 修好 TLS 之后的
    `net_failed = 0` + `listed = 0`，与真·空栏目**逐格相同**。

    所以这一条正着断两件事：`_fetch_failed` 分不开（断言两者相等），
    `_list_pages_ok` 分得开（断言两者不等）。
    """
    down = _collector(tmp_path / "down")        # 页面取不到
    down._fetch = lambda url: ""
    parsed = _collector(tmp_path / "parsed")    # 页面取到了、解析不出
    parsed._fetch = lambda url: ('<html><body><p>栏目暂无内容</p></body></html>')
    try:
        for c in (down, parsed):
            c.collect()
        # 旧有的两个计数器把它们看成同一个东西
        assert (down._total_listed, down._fetch_failed) == (0, 0)
        assert (parsed._total_listed, parsed._fetch_failed) == (0, 0)
        # 新计数器分得开
        assert down._list_pages_ok == 0
        assert parsed._list_pages_ok == 1
    finally:
        _close(down)
        _close(parsed)


# --------------------------------------------------------------------------
# M 组：412 JS 挑战页不重试
# --------------------------------------------------------------------------
#
# 第四十九轮。`hub_em`（湖北省应急管理厅）在健康检查表上长期显示 412。
# 2026-10-05 实测：六种请求头组合（只有 UA ／ +Accept ／ +Accept-Language ／
# +Referer ／ +Upgrade-Insecure-Requests ／ 全套浏览器头）**全部 412**，
# 响应体恒约 3.1 KB 的同一份挑战页——**不是缺头**。
#
# 挑战页要的是浏览器执行 `$_ss.cd` 那段混淆代码算 cookie 再重载。**不逆向**
# （理由见 `_is_js_challenge` 的 docstring）。代码侧能做、也唯一该做的，是
# 让 `_fetch` **认得出这是挑战页、别白重试三次**：退避 1s + 2s 之后拿回来的
# 还是同一份挑战页，而反复打是对抗性 WAF 最容易被升级封禁的行为
# （`news_aggregator` 已被风控挡死的形态）。
#
# 这里真东西只有一份：`hub_em_412.html` 里那段响应体。下面的
# `_CHALLENGE_BODY` 是从它截的原文（`$_ss` 变量名 + `$_ss.cd` 赋值 +
# 外部 js 三个特征都在）。

_CHALLENGE_BODY = (
    "<script type=\"text/javascript\" r='m'>$_ss=window['$_ss'];"
    "if(!$_ss)$_ss={};$_ss.nsd=18589;"
    "$_ss.cd=\"qhrqrpAlEf3qWqlarGgumGqarkqmkOWaqu9vmGqaqsqDkOWarkqmqflEtn"
    "qKqkqrWq3iWS3qWqAar1lumGqAJA9KqkqcWqWiWuqXlA9lEkqDkPEkm\";"
    "if($_ss.lcd)$_ss.lcd();</script>"
    "<script type=\"text/javascript\" charset=\"utf-8\" "
    "src=\"/8aLU4IL2XLG3/khy8TTLck25V.294cc83.js\" r='m'></script>"
)

# 一个**带脚本**但不是挑战页的 412：网关要登录。用来钉住判据的窄度。
_PLAIN_412_BODY = (
    "<html><body><h1>412 Precondition Failed</h1>"
    "<p>您的会话已过期，请重新登录。</p>"
    "<script>setTimeout(function(){location.href='/login';},3000);</script>"
    "</body></html>"
)


def test_412挑战页只请求一次不重试(tmp_path):
    """**这一组的主断言。** 改前 `_fetch` 会退避 1s + 2s 再打两次。"""
    c = _bare(tmp_path)
    resp = _FakeResp(412)
    resp.text = _CHALLENGE_BODY
    client = _FakeClient(resp)
    c._client = client
    try:
        assert c._fetch("https://yjt.hubei.gov.cn/yjgl/aqsc/sgdc/") is None
        assert len(client.calls) == 1, "挑战页不该重试，只允许打一次"
        assert c._fetch_failed == 1, "要计成「没连上」，不能一声不响"
    finally:
        _close(c)


def test_412挑战页的日志说得出这是什么(tmp_path, caplog):
    """日志得让人一眼看出「重试无用」，而不是含糊的 `HTTP 412`。

    含糊的日志会把人引去调请求头——**这一轮实测已经证伪了那条路**
    （六种头组合全 412）。日志里必须点名「挑战」。
    """
    c = _bare(tmp_path)
    resp = _FakeResp(412)
    resp.text = _CHALLENGE_BODY
    c._client = _FakeClient(resp)
    try:
        with caplog.at_level(logging.WARNING):
            c._fetch("https://yjt.hubei.gov.cn/yjgl/aqsc/sgdc/")
        hits = [r for r in caplog.records if "挑战" in r.getMessage()]
        assert hits, f"没有一条日志点明是挑战页：{[r.getMessage() for r in caplog.records]}"
    finally:
        _close(c)


def test_412但不是挑战页时仍然重试三次(tmp_path):
    """**判据取窄的反向钉。** 带 `<script>` 的登录页不算挑战页。

    若判据写成「含 `<script>` 就算」，这类页会被当成挑战页一次就打住——
    可它们退避几秒再打**是可能好的**，白丢一个源。
    """
    c = _bare(tmp_path)
    resp = _FakeResp(412)
    resp.text = _PLAIN_412_BODY
    client = _FakeClient(resp)
    c._client = client
    try:
        assert c._fetch(_LIST_URL) is None
        assert len(client.calls) == 3, "非挑战页要走原重试路径"
        assert c._fetch_failed == 1
    finally:
        _close(c)


def test_挑战页判据只认下划线双s那个变量名():
    """直接钉函数：真挑战页认，别的都不认。"""
    assert trs_cms_base._is_js_challenge(_CHALLENGE_BODY) is True
    # 只留外部 js 那一行也要认（有的响应体省略内联那段）
    assert trs_cms_base._is_js_challenge(
        "<script src=\"/8aLU4IL2XLG3/khy8TTLck25V.294cc83.js\"></script>") is False
    # 带脚本的普通 412 不认
    assert trs_cms_base._is_js_challenge(_PLAIN_412_BODY) is False
    # 普通的 200 页面不认
    assert trs_cms_base._is_js_challenge("<html><body>列表</body></html>") is False
    assert trs_cms_base._is_js_challenge("") is False


def test_412挑战页在端到端里表现为零条零页(tmp_path):
    """接到 `collect()` 上：挑战页 = 这个源这一轮一条也拿不到。

    与 `test_列表页压根取不到时页数为零` 同一个形态——**这正是我们想要的**：
    不是「解析出 0 条」，而是明确地「页面一张都没回来」。

    `_collector` 会把 `_fetch` 换成假的（`_list_html`），所以这里必须先
    `del` 掉那个实例属性，让真正的 `_fetch` 接上假 `_client`——**否则这条
    测试压根没走 `_fetch`，是个恒真的假绿**。
    """
    c = _collector(tmp_path, n=2)
    real_client = c._client
    resp = _FakeResp(412)
    resp.text = _CHALLENGE_BODY
    fake = _FakeClient(resp)
    del c._fetch
    c._client = fake
    try:
        out = c.collect()
        assert out == []
        assert c._list_pages_ok == 0
        assert c._total_listed == 0
        assert c._fetch_failed > 0
        assert len(fake.calls) == 1, "挑战页只该打一次"
    finally:
        fake.close()
        real_client.close()
