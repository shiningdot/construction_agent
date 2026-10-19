"""`BeijingEMCollector` 的正文通路 + registry 参数名匹配的回归测试。

**不联网。** `_fetch` 与 `_download_and_extract` 都换成假的，
`classify_v2` 用真的（它是纯规则函数，见下面第三条）。

这一组盯的是 2026-10-01 查出的两层毛病：

1. **正文提出后被扔掉。** `pdf_text` 只喂给 `classify_v2`，分类完随作用域
   丢掉，item 的 `content` 写死成 `[PDF 报告] 标题`。后果是这个源的正文
   **从来没进过库**——主库 12 条 beijing_em 记录的 `raw_text` 全是
   106–206 字的占位。同一批调查还发现 `needs_ocr` 被写死成 `True`，
   与全项目「有附件 and 没提到正文」的约定相悖（这个标记现在还没人消费，
   见 `test_提出正文时不再标needs_ocr` 的说明）。

2. **registry 的参数名对不上，而且没人发现。** `_NO_CLASSIFY` 的键是
   `enable_classify`，本源构造器的参数叫 `enable_pdf_classify`；
   `registry.build()` 会把构造器不认识的参数**静默丢掉**（只记一条
   `logger.debug`）。所以「已给 beijing_em 关了分类」是个假象——实测实例上
   `_enable_pdf_classify is True`。这里用一条遍历全部源的断言把它钉死。

3. **顺带纠正 `_NO_CLASSIFY` 的存在理由。** 常量的注释原先写着「多花一次
   模型调用」，但开关背后调的是 `collector/classifier.py::classify_v2`——
   一个 242 行的纯关键词规则函数，没有一处网络或 LLM 调用。省不下模型成本，
   关掉的真实代价是条目全落 `status=review`。这条不写成断言，写在文档里。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector import registry  # noqa: E402
from construction_agent.collector.executor import (  # noqa: E402
    _row_from_sink_record)
from construction_agent.collector.sources.beijing_em import (  # noqa: E402
    BJEM_BASE, BeijingEMCollector)
from construction_agent.collector.stores import RawAccidentStore  # noqa: E402

# 真实页面里这四个数组的**下标就是字面 `[i]`**（写 `[0]` 一条都解析不出来，
# 排查时踩过一次）。标题挑主库里真有的那种，能过一级标题初筛。
#
# `xkrq[i]` 抽成参数：2026-10-01 第二十九轮起，这个值**不保证是 PDF**——
# 实测该站列表页 84 条附件里 `.pdf` 69、`.docx` 10、`.doc` 5。
def _list_page(xkrq: str) -> str:
    return f"""<html><body><script>
function showNews_12345(start_num) {{
    urls[i] = '/art/2024/1/1/art_4520_1.html';
    headers[i] = "北京建工四建工程建设有限公司一般生产安全事故调查报告";
    xkxm[i] = '北京市应急管理局';
    xkrq[i] = '{xkrq}';
}}
</script></body></html>"""


_LIST_PAGE = _list_page('/attach/0/W020240101.pdf')


def _collector(tmp_path: Path, list_page: str = _LIST_PAGE,
               **kwargs) -> BeijingEMCollector:
    """造一个不联网的采集器。

    `raw_store` 必须显式改道：`BeijingEMCollector.__init__` 里是
    `RawAccidentStore()` 无参调用，落 `settings.DATA_DIR/raw_accidents.json`
    ——那是仓库里的共享文件，测试直接跑会读到别的源留下的条目、还会把
    整个文件重写一遍（排查时踩过）。
    """
    c = BeijingEMCollector(data_dir=tmp_path / "src", **kwargs)
    c.raw_store = RawAccidentStore(filepath=tmp_path / "raw.json")
    c._fetch = lambda url: list_page
    return c


def _close(c: BeijingEMCollector) -> None:
    client = getattr(c, "_client", None)
    if client is not None:
        client.close()


# --------------------------------------------------------------------------
# 正文通路
# --------------------------------------------------------------------------


def test_PDF提出正文时直接当content(tmp_path):
    """改前这里是 `[PDF 报告] 标题`——正文提出来了却扔掉。

    判据不是「有 content」（占位也是 content），而是 content 里真的
    是那份报告。
    """
    c = _collector(tmp_path)
    body = "事故调查报告正文。" * 200
    c._download_and_extract = lambda url: body
    try:
        c.collect()
        items = c.raw_store.get_all()
        assert len(items) == 1
        assert items[0]["content"] == body
        assert items[0]["content"].startswith("事故调查报告正文")
        assert "[PDF 报告]" not in items[0]["content"]
    finally:
        _close(c)


def test_提出正文时不再标needs_ocr(tmp_path):
    """`needs_ocr` 原先写死 `True`，把「已拿到正文」的条目也标成待 OCR。

    全项目的约定是 `needs_ocr = 有 PDF 附件 and 正文没提出来`——`henan_em`
    （`bool(pdf_url) and not combined_text`）、`nmg_em`、`ln_em` 三个源都是
    这么写的，写死 `True` 与它相悖。

    **但别把收益说大了**：这个标记目前没有任何消费方。`raw_accidents` 表里
    没有这一列，它塞在 `extra` JSON 里；审核模板不显示它，也没有自动 OCR
    队列在读它（`grep -rn needs_ocr` 除了注释零命中）。所以这里修的是
    「字段写对」，不是「省下一轮 OCR」——后者要等阶段②真的开始消费它。
    """
    c = _collector(tmp_path)
    c._download_and_extract = lambda url: "事故调查报告正文。" * 200
    try:
        c.collect()
        assert c.raw_store.get_all()[0]["needs_ocr"] is False
    finally:
        _close(c)


def test_提不出正文时退回占位并标needs_ocr(tmp_path):
    """扫描件 / 404 / 附件被撤 —— 这时占位与标记都是对的。

    `_download_and_extract` 失败返回空串（`extract_pdf_text` 也只在
    >50 字时才给文本），所以 `not pdf_text` 是个可靠判据。
    """
    c = _collector(tmp_path)
    c._download_and_extract = lambda url: ""
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["content"].startswith("[PDF 报告] ")
        assert item["needs_ocr"] is True
    finally:
        _close(c)


def test_关掉开关就既不下PDF也不给正文(tmp_path):
    """`enable_pdf_classify=False` 时正文来源一并关掉。

    这个开关的名字只说「分类」，实际管两件事——**本源正文只在 PDF 里**，
    所以 registry 不能给它挂 `_NO_CLASSIFY`（见文件头第 2 条与
    `test_registry里beijing_em没被关分类`）。
    """
    called = []
    c = _collector(tmp_path, enable_pdf_classify=False)
    c._download_and_extract = lambda url: called.append(url) or "不该被调用"
    try:
        c.collect()
        assert called == []
        item = c.raw_store.get_all()[0]
        assert item["content"].startswith("[PDF 报告] ")
        assert item["needs_ocr"] is True
    finally:
        _close(c)


# --------------------------------------------------------------------------
# 附件类型分拣（2026-10-01 第二十九轮）
# --------------------------------------------------------------------------


def test_Word附件登记到office_urls(tmp_path):
    """**这个源的附件不全是 PDF。**

    2026-10-01 实测它列表页一条 `xkrq[i]` 数组的取值分布：84 条里 `.pdf` 69、
    `.docx` 10、`.doc` 5。改前那 15 条 Word 原样写成 `"pdf_urls": [pdf_url]`，
    经 `executor._ASSET_KEYS` 按键名登记成 `asset_type='pdf'`——阶段② 会拿
    PyMuPDF 去解 Word。与 `gd_em` 那个「`.doc` 是 `.docx` 子串」的老 bug、
    以及上一轮 `esafety` 把图片塞进 `pdf_urls`，是**同一族：键名说了谎**。
    """
    url = f"{BJEM_BASE}/attach/0/W020240101.docx"
    c = _collector(tmp_path, list_page=_list_page("/attach/0/W020240101.docx"))
    c._download_and_extract = lambda _: ""
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
    finally:
        _close(c)

    assert item["pdf_urls"] == []
    assert item["office_urls"] == [url]
    # 占位行按实际类型写，不能一律说「PDF」
    assert item["content"].startswith("[Office 报告] ")
    assert "文档: " in item["content"]


def test_通路A把office_urls登记成office(tmp_path):
    """登记端：`executor._ASSET_KEYS` 里那条 `("office_urls", "office")`。

    `beijing_em` 的 record 是 dict，走 `raw_store.add_batch()` → 通路 A
    （`executor.py:132`）。
    """
    c = _collector(tmp_path, list_page=_list_page("/attach/0/W020240101.doc"))
    c._download_and_extract = lambda _: ""
    try:
        c.collect()
        row = _row_from_sink_record(c.raw_store.get_all()[0], "beijing_em", "t")
    finally:
        _close(c)

    assert [a["asset_type"] for a in row["_assets"]] == ["office"]
    assert row["_assets"][0]["url"].endswith(".doc")


@pytest.mark.parametrize("path,expect", [
    ("/attach/0/a.pdf", "pdf_urls"),
    ("/attach/0/a.docx", "office_urls"),
    ("/attach/0/a.doc", "office_urls"),
    ("/attach/0/a.PDF", "pdf_urls"),                    # 大写
    ("/attach/0/a.docx?download=1", "office_urls"),     # 带查询串
])
def test_判据自检_只换后缀两个键就互换(tmp_path, path, expect):
    """同一条记录、同一段代码，**只有后缀不同**——两个键互换。

    判据本身（含大小写与查询串）另有一组逐条的用例，见
    `tests/test_gd_em_collector.py::test_附件后缀分拣`——那里测的是共享模块
    `collector/attachments.py`，`gd_em` 与本源共用同一份。
    """
    other = "office_urls" if expect == "pdf_urls" else "pdf_urls"
    c = _collector(tmp_path, list_page=_list_page(path))
    c._download_and_extract = lambda _: ""
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
    finally:
        _close(c)

    assert item[expect], f"{path} 应进 {expect}"
    assert item[other] == []


def test_认不出的附件类型不入产出但留warning(tmp_path, caplog):
    """`.xlsx` 这类认不出的：两个键都不收，但**不静默**。

    与 `_assets_from`「键名不认识的不猜」同向——宁可少登记一条，也不要把
    一个普通链接当附件去下载。代价是漏，所以拿一条 warning 换：站点改版
    新增了别的附件类型，得有人知道。
    """
    c = _collector(tmp_path, list_page=_list_page("/attach/0/a.xlsx"))
    try:
        with caplog.at_level("WARNING"):
            c.collect()
        item = c.raw_store.get_all()[0]
    finally:
        _close(c)

    assert item["pdf_urls"] == []
    assert item["office_urls"] == []
    assert "附件类型认不出" in caplog.text


def test_图片附件进image_urls(tmp_path):
    """**这一条是本轮第二个缺陷的直接证据。**

    改前这里是对称的两条三元表达式：

        "pdf_urls":    [asset_url] if kind == "pdf" else [],
        "office_urls": [asset_url] if kind == "office" else [],

    判据只认 pdf / office 时它们都对。第三十三轮判据肯认 `"image"` 之后，
    同一段代码**两个键都会是空列表**——而且**一个字都不说**：本源的 warning
    挂在 `if asset_url and not kind:` 上，`"image"` 是真值，那条分支进不去。

    实测撞上它的机会有多大：这个源的附件是列表页 JS 数组 `xkrq[i]` 的值，
    2026-10-01 抽样的 84 条里没有图片——**所以这不是「已经丢了图」，是
    「再有一次站点改版就会静默丢图」**。`hebei_em` 那张被丢掉的罐区平面图
    是同一个坑的既遂版本。

    顺带钉住：图片附件**不该被下载去提正文**（`fitz` 打不开 PNG）。
    """
    url = f"{BJEM_BASE}/attach/0/W020240101.png"
    calls: list[str] = []
    c = _collector(tmp_path, list_page=_list_page("/attach/0/W020240101.png"))
    c._download_and_extract = lambda u: (calls.append(u), "")[1]
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
    finally:
        _close(c)

    assert item["image_urls"] == [url]
    assert item["pdf_urls"] == []
    assert item["office_urls"] == []
    # 占位行按实际类型写，别再说「PDF 报告」
    assert item["content"].startswith("[图片附件] ")
    assert "图片: " in item["content"]
    # 有附件、正文没提出来 → 仍标 needs_ocr（全项目约定，§2.5.13）
    assert item["needs_ocr"] is True
    assert calls == [], f"图片不该被拿去提文本，实际下载了 {calls}"


def test_图片附件不触发认不出的warning(tmp_path, caplog):
    """`"image"` 会从 `if asset_url and not kind:` 那条分支**穿过去**。

    这是好事（它认得出），但也意味着：假如分拣那边漏收，本源连一句提示都
    不会有。所以上一条断言必须落在 `image_urls` 的内容上——**warning 的有无
    在这里不是安全信号**。
    """
    c = _collector(tmp_path, list_page=_list_page("/attach/0/a.png"))
    try:
        with caplog.at_level("WARNING"):
            c.collect()
        item = c.raw_store.get_all()[0]
    finally:
        _close(c)

    assert item["image_urls"], "判据认了 image，分拣却没地方放——这条会红"
    assert "附件类型认不出" not in caplog.text


def test_没有附件时不假装有附件(tmp_path):
    """`xkrq` 数组比 `urls` 短时 `pdf_path` 为空——那是**压根没有附件**。

    改前 `content` 仍写 `[PDF 报告] 标题 … PDF: `（空地址）、`needs_ocr` 也
    照样标 True，两者都在说「有一条 PDF 等你 OCR」，而其实没有。此处按
    全项目约定回到「有附件 且 正文没提出来」。
    """
    c = _collector(tmp_path, list_page=_list_page(""))
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
    finally:
        _close(c)

    assert item["pdf_urls"] == []
    assert item["office_urls"] == []
    assert "[PDF 报告]" not in item["content"]
    assert item["needs_ocr"] is False


# --------------------------------------------------------------------------
# registry：参数名必须真的被构造器接受
# --------------------------------------------------------------------------


def test_registry里beijing_em没被关分类(tmp_path):
    """回归钉子。

    `_NO_CLASSIFY` 挂在这里时是个**静默无效**的配置：常量的键
    `enable_classify` 与构造器的 `enable_pdf_classify` 对不上，
    `build()` 把它丢进 `logger.debug` 就不管了。实测实例上那个开关
    一直是 `True`。

    现在摘掉不是「改行为」，是**把既成事实写实**：挂着的确也关不掉，
    而真要关掉了，这个源的正文就一并没了。
    """
    c, spec = registry.build("beijing_em", data_dir=tmp_path)
    try:
        assert getattr(c, "_enable_pdf_classify") is True
        assert "enable_classify" not in dict(spec.build_kwargs)
        assert "enable_pdf_classify" not in dict(spec.build_kwargs)
    finally:
        _close(c)


@pytest.mark.parametrize("key", sorted(registry.SOURCES))
def test_每个源的build_kwargs都被构造器接受(key):
    """**这条是给未来写的钉子。**

    `build()` 遇到构造器不认识的参数只记一条 `logger.debug` 就丢掉——
    不报错、不警告、测试也照过。于是「我给这个源配了 X」和「X 真的生效了」
    可以长期不一致，`beijing_em` 就这么瞒了很久。

    不指望 Registry 改成抛错（有些源本来就不接受 `data_dir`，静默跳过是
    有意的），改成在这里逐个核对：登记表里写下的每个键，构造器签名里
    都得有。
    """
    spec = registry.get(key)
    module = __import__(f"construction_agent.collector.sources.{spec.module}",
                        fromlist=[spec.cls])
    cls = getattr(module, spec.cls)
    params = set(inspect.signature(cls.__init__).parameters)
    unknown = set(dict(spec.build_kwargs)) - params
    assert not unknown, (
        f"{key} 的 build_kwargs 里 {sorted(unknown)} 构造器不认，"
        f"会被 build() 静默丢弃；构造器接受的是 {sorted(params)}"
    )
