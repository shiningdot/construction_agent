"""附件类型判据：从 URL（或响应头里的后缀）认出一份附件是 PDF / Office / 图片。

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

**第三十三轮起认图片**（`"image"`）。此前那张 `.png` 走的是「认不出」分支被
丢掉，而它其实是**内容图**——2026-10-01 第三十三轮把它下载下来看了：`hebei_em`
全站 110 页里唯一一张 `.png` 附件（19,830 字节）是「沧州市渤海新区南大港东兴
工业区鼎睿石化有限公司"5·31"火灾事故调查报告」里的**罐区总平面布置图**
（612×374，标着锅炉房明火源、1#~5# 储罐、消防道路与防火间距尺寸 24.82 /
32.43 / 33.24）。**丢掉的是一条事故的关键现场图，不是装饰。**

新增一种类型要同时改 n 个调用方的分拣，所以分拣本身也收进这个模块
（`sort_into`）——加类型只改这一个文件。详见 `sort_into` 的 docstring。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

# `.xlsx` / `.pptx` / `.zip` 这类**有意不收**——截至本轮，全部 24 个源实测到的
# 非 PDF 附件只有 Word 与图片两种。放宽到没见过的类型等于替站点猜它的意图。
# 真出现了，判据会给 `""`，调用方那条 warning 会把它暴露出来。
_PDF_EXT = (".pdf",)
_OFFICE_EXT = (".docx", ".doc")
# 图片这一族一次认全，不只认实测到的那一个 `.png`。理由与 `_OFFICE_EXT` 收
# `.doc` + `.docx` 相同：**同族一起收**，否则下一个挂 `.jpg` 扫描页的站点又会
# 掉进「认不出」分支——那正是这一轮要消灭的失败模式。这一条与 §2.5.26 那轮
# 挡下的「附件行图标」不冲突：那条判的是**页面 DOM 里 `<img>` 的角色**，这条判
# 的是**服务器在 `Content-Disposition` 里声明的文件类型**，两层各管各的。
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def kind_of_suffix(suffix: str) -> str:
    """按**已经取出来的后缀**判断类型，返回 `"pdf"` / `"office"` / `"image"` / `""`。

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
    if s in _IMAGE_EXT:
        return "image"
    return ""


def attachment_kind(url: str) -> str:
    """按 URL 后缀判断附件类型，返回 `"pdf"` / `"office"` / `"image"` / `""`。

    大小写、查询串、锚点都在这一个函数里统一处理（`gd_em` 那版是手工
    `lower().split("?")[0].split("#")[0]`，等价但漏一半情形就出错）。
    """
    path = urlsplit(str(url or "")).path
    return kind_of_suffix(Path(path).suffix)


def sort_into(kind: str, url: str, *, pdf: list[str], office: list[str],
              image: list[str]) -> bool:
    """把 `url` 按 `kind` 收进对应的列表，返回**是否收下**。

    **为什么分拣也要收进这个模块。** `kind` 有三种有效取值，而三个调用方
    各自写了一遍「二选一」的分拣表达式：

        hebei_em      (pdf_urls if kind == "pdf" else office_urls).append(...)
        gd_em         (pdf_urls if kind == "pdf" else office_urls).append(...)
        beijing_em    "pdf_urls":    [u] if kind == "pdf" else []
                      "office_urls": [u] if kind == "office" else []

    这三行在「只有 pdf / office 两种」时都对。第三十三轮加上 `"image"` 之后
    **三行同时变错，而且错法各不相同**：前两行会把图片塞进 `office_urls`
    （登记成 `asset_type='office'`，阶段② 拿 antiword 去解 PNG），最后一处
    **两个键都空、静默丢掉且连 warning 都没有**（它的 warning 挂在
    `not kind` 上，而 `"image"` 是真值）。

    ——这就是「加一种类型要改 n 处、漏一处就静默出错」的经典形状，同一个
    教训在本项目已出现三次（`pdf_text.py` 被抄三份、`attachments.py` 判据
    不统一、`trs_cms_base` 的末批写法）。所以分拣不再各写各的：收进这里，
    **加类型只改这一个文件，三个调用方一行都不用动**。

    认不出的 `kind` 返回 `False` 且**不动任何列表**——调用方用这个返回值
    决定「要不要记 warning」或「要不要走全页回退扫描」（`gd_em` 用它替掉了
    原先那个 `if not pdf_urls and not office_urls`，那个条件加图片后会把
    「只有图片附件」的页面误判成「没有附件」）。
    """
    target = {"pdf": pdf, "office": office, "image": image}.get(kind)
    if target is None:
        return False
    target.append(url)
    return True
