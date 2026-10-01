"""`collector/attachments.py` 的判据测试。

这个模块 2026-10-01 第二十九轮才抽出来：判据原本在 `gd_em.py` 里是私有的
`_attachment_kind`，`beijing_em.py` 一份都没有（它把列表页 JS 数组 `xkrq[i]`
原样当 PDF 交给下游——实测该站 84 条附件里 15 条是 Word）。抽出来的理由是
`pdf_text.py` 被抄三份的教训：判据不一致会出现「同一份附件在一个源登记成
pdf、在另一个源登记成 office」。

**不联网。** 全是纯函数。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.attachments import (  # noqa: E402
    attachment_kind, kind_of_suffix)
from construction_agent.collector.executor import _ASSET_KEYS  # noqa: E402

# --------------------------------------------------------------------------
# 判据
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    # 常规
    ("/attach/0/a.pdf", "pdf"),
    ("/attach/0/a.docx", "office"),
    ("/attach/0/a.doc", "office"),
    # 大写：站点上真出现过 `.PDF`（`gd_em` 那轮实测）
    ("/attach/0/a.PDF", "pdf"),
    ("/attach/0/a.DOCX", "office"),
    # 查询串与锚点：`urlsplit` 一并处理，不该影响判断
    ("/attach/0/a.docx?download=1", "office"),
    ("/attach/0/a.pdf#page=2", "pdf"),
    # 绝对地址与相对路径
    ("http://yjglj.beijing.gov.cn/attach/0/a.pdf", "pdf"),
    ("a.pdf", "pdf"),
    ("./W0202607133876.pdf", "pdf"),
    # 认不出的**不猜**（这一档靠调用方接 warning，见 `beijing_em`）
    ("/attach/0/a.xlsx", ""),
    ("/attach/0/a.xls", ""),
    ("/attach/0/a.pptx", ""),
    # `hebei_em` 的 PDF 附件就是这样：**没有扩展名**。所以「URL 形态」在本
    # 项目里不是可靠的类型信号——这也是本轮否掉「按键名的 URL 形态守卫」的理由
    ("/portal/upload/downloadFile?id=6f2a-uuid", ""),
    ("/attach/0/a", ""),
    ("", ""),
    (None, ""),
])
def test_附件后缀判据(url, expected):
    assert attachment_kind(url) == expected


def test_按已取出的后缀判类型():
    """`kind_of_suffix` —— 给「手里已经有后缀」的调用方（`hebei_em`）。

    它拿到的不是 URL 而是 `Content-Disposition` 里的 filename，见下一条。
    """
    for s, want in [(".pdf", "pdf"), (".PDF", "pdf"),
                    (".doc", "office"), (".docx", "office"),
                    (".png", ""), (".xlsx", ""), ("", ""), (None, "")]:
        assert kind_of_suffix(s) == want, f"{s!r} 应判成 {want!r}"


def test_filename里那个问号不能走URL判据():
    """**`kind_of_suffix` 存在的第二个理由。**

    `hebei_em` 的 `Content-Disposition` 实测长这样（GBK 被当 latin-1 读）：

        attachment; filename=¡°3?13¡±½Ï´óÈ¼Æø±¬È¼ÊÂ¹Êµ÷²é±¨¸æ.pdf

    那个 `?` 是乱码产生的，而 `urlsplit` 把它当**查询串分隔符**——后面整段
    （含真后缀 `.pdf`）全被丢进 query，`attachment_kind` 于是判成「认不出」，
    一条本来好好的 PDF 会被当成未知类型丢掉。

    `Path(...).suffix` 不解析 `?`，在这儿是对的。
    """
    from pathlib import Path

    name = "¡°3?13¡±½Ï´óÈ¼Æø±¬È¼ÊÂ¹Êµ÷²é±¨¸æ.pdf"
    assert attachment_kind(name) == "", "前提变了：URL 判据不再被 `?` 截断"
    assert kind_of_suffix(Path(name).suffix) == "pdf"


def test_子串判据会误收而这版不会():
    """**这个模块存在的理由**：比后缀，不比子串。

    `gd_em` 原先写的是 `".doc" in href`——而 `.doc` 是 `.docx` 的子串。这不是
    「漏」，是「错」：Word 附件被判成 PDF 塞进了 `pdf_urls`。

    下面三个 URL 都是「含 `.doc`/`.pdf` 字样、但不是那种扩展名」的形状。
    """
    for url in ("/attach/0/a.doc.bak",        # 子串命中 `.doc`，真后缀是 `.bak`
                "/attach/0/x.pdf.zip",        # 子串命中 `.pdf`
                "/gk/zdlyxxgk/showdocview"):  # 纯字样，无扩展名
        assert attachment_kind(url) == "", f"{url} 不该被判成附件"


# --------------------------------------------------------------------------
# 与下游的键名对齐
# --------------------------------------------------------------------------


def test_判据认出的类型在_ASSET_KEYS里都有键接住():
    """**给未来写的钉子。**

    `attachment_kind` 说「这是 office」时，下游得有地方接住——`executor._ASSET_KEYS`
    按键名认类型，认出的每种类型都得有个 `<kind>_urls` 键在表里，否则那批 URL
    要么被丢掉、要么被人塞进别的键（`beijing_em` 之前就是这么干的）。

    往判据里加类型（比如哪天要收 `.xlsx`）而忘了配键，这条会红。
    """
    kinds = {attachment_kind(u) for u in ("a.pdf", "a.docx")}
    assert kinds == {"pdf", "office"}, "判据能认出的类型变了——这条钉子要一起改"

    keys = {k for k, _ in _ASSET_KEYS}
    for kind in kinds:
        assert f"{kind}_urls" in keys, (
            f"判据认得出 {kind}，但 `_ASSET_KEYS` 里没有 `{kind}_urls` 这个键，"
            f"下游接不住；表里现有的是 {sorted(keys)}"
        )
