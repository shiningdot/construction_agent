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
from construction_agent.collector.sources.beijing_em import (  # noqa: E402
    BeijingEMCollector)
from construction_agent.collector.stores import RawAccidentStore  # noqa: E402

# 真实页面里这四个数组的**下标就是字面 `[i]`**（写 `[0]` 一条都解析不出来，
# 排查时踩过一次）。标题挑主库里真有的那种，能过一级标题初筛。
_LIST_PAGE = """<html><body><script>
function showNews_12345(start_num) {
    urls[i] = '/art/2024/1/1/art_4520_1.html';
    headers[i] = "北京建工四建工程建设有限公司一般生产安全事故调查报告";
    xkxm[i] = '北京市应急管理局';
    xkrq[i] = '/attach/0/W020240101.pdf';
}
</script></body></html>"""


def _collector(tmp_path: Path, **kwargs) -> BeijingEMCollector:
    """造一个不联网的采集器。

    `raw_store` 必须显式改道：`BeijingEMCollector.__init__` 里是
    `RawAccidentStore()` 无参调用，落 `settings.DATA_DIR/raw_accidents.json`
    ——那是仓库里的共享文件，测试直接跑会读到别的源留下的条目、还会把
    整个文件重写一遍（排查时踩过）。
    """
    c = BeijingEMCollector(data_dir=tmp_path / "src", **kwargs)
    c.raw_store = RawAccidentStore(filepath=tmp_path / "raw.json")
    c._fetch = lambda url: _LIST_PAGE
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
