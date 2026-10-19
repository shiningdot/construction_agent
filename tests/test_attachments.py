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
    attachment_kind, kind_of_suffix, sort_into)
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
    # 图片（2026-10-01 第三十三轮新认的一族）。`hebei_em` 那张 `.png` 实测
    # 是罐区总平面布置图——内容图，改前走「认不出」被丢掉；这里连同族一起
    # 认全，见模块里 `_IMAGE_EXT` 的理由。这一档与 `is_attachment_icon`
    # （§2.5.26）判的不是一回事：那条判 DOM 里 `<img>` 的角色，这条判
    # 服务器声明的文件类型。
    ("/attach/0/a.png", "image"),
    ("/attach/0/a.JPG", "image"),
    ("/attach/0/a.jpeg", "image"),
    ("/attach/0/a.webp", "image"),
    ("/attach/0/gsze.png?v=2", "image"),
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
                    (".png", "image"), (".JPG", "image"),
                    (".xlsx", ""), ("", ""), (None, "")]:
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
# 分拣（2026-10-01 第三十三轮新增）
# --------------------------------------------------------------------------


def test_分拣按类型各归各位():
    """三种类型各进各的列表。"""
    pdf, office, image = [], [], []
    for kind, url in [("pdf", "p"), ("office", "o"), ("image", "i")]:
        assert sort_into(kind, url, pdf=pdf, office=office, image=image) is True

    assert pdf == ["p"]
    assert office == ["o"]
    assert image == ["i"]


def test_分拣认不出时一个列表都不动():
    """返回 `False` 是调用方唯一能拿到的信号——所以不能顺手往哪儿塞一个。

    `beijing_em` 拿 `False` 记 warning、`gd_em` 拿它决定要不要继续走全页回退
    扫描。要是这里往里塞了东西，那两处都会误判成「收下了」。
    """
    pdf, office, image = [], [], []
    for kind in ("", "xlsx", None, "PDF", "IMAGE", "pdf "):
        assert sort_into(kind, "u", pdf=pdf, office=office,
                         image=image) is False, f"{kind!r} 不该被收下"
    assert (pdf, office, image) == ([], [], [])


def test_二元分拣加第三种类型后会怎么错():
    """**判据自检，也是这一轮把分拣收进模块的理由。**

    加 `"image"` 之前，三个调用方各自写了一遍「二选一」，三行在两种类型下
    都对。这里把那些旧写法原地复刻一遍，证明它们在 `kind == "image"` 时
    **同时变错、而且错法不一样**——`sort_into` 对同样的输入是第四个结果。

    这条测试不调用被测代码（被测的是那三行旧写法），它钉的是**共享函数
    存在的理由**：改回任何一行旧写法，`hebei_em` / `gd_em` / `beijing_em`
    各自的图片用例就会红，红法各不相同。
    """
    kind = "image"

    # hebei_em / gd_em 改前：`(pdf_urls if kind == "pdf" else office_urls)`
    pdf, office = [], []
    (pdf if kind == "pdf" else office).append("u")
    assert office == ["u"], "复刻失败：旧写法本该把图片塞进 office_urls"
    assert pdf == []
    # → 阶段② 拿 antiword 去解一张 PNG。注册成 `asset_type='office'`。

    # beijing_em 改前：两个键各一条三元表达式
    old = {"pdf_urls": ["u"] if kind == "pdf" else [],
           "office_urls": ["u"] if kind == "office" else []}
    assert old == {"pdf_urls": [], "office_urls": []}
    # → 两个键都空，**静默丢掉**；本源 warning 挂在 `not kind` 上，
    #   而 `"image"` 是真值，一个字的提示都不会有。

    # 共享函数：收下，且进对的那一格
    pdf, office, image = [], [], []
    assert sort_into(kind, "u", pdf=pdf, office=office, image=image) is True
    assert (pdf, office, image) == ([], [], ["u"])


# --------------------------------------------------------------------------
# 与下游的键名对齐
# --------------------------------------------------------------------------


def test_判据认出的类型在_ASSET_KEYS里都有键接住():
    """**给未来写的钉子。**

    `attachment_kind` 说「这是 office」时，下游得有地方接住——`executor._ASSET_KEYS`
    按键名认类型，认出的每种类型都得有个 `<kind>_urls` 键在表里，否则那批 URL
    要么被丢掉、要么被人塞进别的键（`beijing_em` 之前就是这么干的）。

    往判据里加类型（比如哪天要收 `.xlsx`）而忘了配键，这条会红。

    `"image"` 是第三十三轮加进来的，加它的时候**下游的键早就有了**——
    `executor._ASSET_KEYS` 里 `("image_urls", "image")` 一直在表里等着被填，
    缺的是判据肯认、和分拣肯往那儿放（见 `test_二元分拣加第三种类型后会怎么错`）。
    """
    kinds = {attachment_kind(u) for u in ("a.pdf", "a.docx", "a.png")}
    assert kinds == {"pdf", "office", "image"}, \
        "判据能认出的类型变了——这条钉子要一起改"

    keys = {k for k, _ in _ASSET_KEYS}
    for kind in kinds:
        assert f"{kind}_urls" in keys, (
            f"判据认得出 {kind}，但 `_ASSET_KEYS` 里没有 `{kind}_urls` 这个键，"
            f"下游接不住；表里现有的是 {sorted(keys)}"
        )
