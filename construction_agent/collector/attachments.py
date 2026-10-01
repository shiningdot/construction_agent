"""附件类型判据：从 URL 认出一份附件是 PDF 还是 Office 文档。

**为什么要有这个模块**（2026-10-01 第二十九轮）：这套判据此前在
`gd_em.py` 里写了一份私有的 `_attachment_kind`，而 `beijing_em.py` 一份都没有
——它把列表页 JS 数组 `xkrq[i]` 的值原样当 PDF 交给下游。2026-10-01 实测该站
列表页 84 条附件的后缀分布：`.pdf` 69、`.docx` 10、`.doc` 5。那 15 条 Word
一路写成 `"pdf_urls": [pdf_url]`，经 `executor._ASSET_KEYS` 按键名登记成
`asset_type='pdf'`，阶段② 会拿 PyMuPDF 去解 Word。

不各源各写一份的理由同 `pdf_text.py`（被抄三份的教训）：判据不一致会出现
「同一份附件在一个源登记成 pdf、在另一个源登记成 office」。

判据是**比后缀，不是比子串**。`gd_em` 原先写的是 `".doc" in href`，而 `.doc`
是 `.docx` 的子串——Word 附件于是全被当成 PDF 塞进了 `pdf_urls`。

认不出的返回 `""`——**不猜**。调用方自己决定「认不出怎么办」：`gd_em` 是丢掉
（它只认 Word 与 PDF），`beijing_em` 走「不静默丢掉、记一条 warning」。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

# 目前只认 Word。`.xlsx` / `.pptx` 这类**有意不收**——截至本轮，全部 24 个源
# 实测到的非 PDF 附件都是 Word（第三十轮又实测到一张 `.png`，同样不收，
# 理由见 `hebei_em` 的 warning 那一处）；放宽到没见过的类型等于替站点猜它的
# 意图。真出现了，判据会给 `""`，调用方那条 warning 会把它暴露出来。
_PDF_EXT = (".pdf",)
_OFFICE_EXT = (".docx", ".doc")


def kind_of_suffix(suffix: str) -> str:
    """按**已经取出来的后缀**判断类型，返回 `"pdf"` / `"office"` / `""`。

    给「手里已经有后缀」的调用方用——典型是 `hebei_em`：它得从响应头
    `Content-Disposition` 的 `filename` 判类型（那个 URL 没有扩展名）。

    **`filename` 不能走 `attachment_kind`**：那函数拿 `urlsplit` 拆 URL，而
    `filename` 里可能有 `?`——实测这条头是 GBK 被当 latin-1 读的乱码，
    `¡°3?13¡±½Ï´ó...报告.pdf` 里那个 `?` 会被 `urlsplit` 当查询串分隔符，
    后面整段（含真后缀）全被当成 query 丢掉。`Path().suffix` 不解析 `?`，
    在这儿是对的。
    """
    s = str(suffix or "").lower()
    if s in _PDF_EXT:
        return "pdf"
    if s in _OFFICE_EXT:
        return "office"
    return ""


def attachment_kind(url: str) -> str:
    """按 URL 后缀判断附件类型，返回 `"pdf"` / `"office"` / `""`（认不出）。

    大小写、查询串、锚点都在这一个函数里统一处理（`gd_em` 那版是手工
    `lower().split("?")[0].split("#")[0]`，等价但漏一半情形就出错）。
    """
    path = urlsplit(str(url or "")).path
    return kind_of_suffix(Path(path).suffix)
