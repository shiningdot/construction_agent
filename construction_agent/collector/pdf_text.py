"""PDF 全文提取 —— 全采集器共用的那一份实现。

**为什么要有这个模块**（2026-10-01）：动态试跑 24 个源时，`beijing_em`
直接以 `NameError: name '_extract_pdf_text' is not defined` 崩掉，
`ln_em` / `henan_em` 也各在 PDF 分支上调它——**但没有一个模块定义它**。

原来这份函数被复制了三份，分别在 `trs_cms_base.py`、`nmg_em.py`、
`hebei_em.py` 里各写一遍（三份逐字相同）。复制的时候漏掉了另外三个模块，
而漏掉的恰好只在 PDF 分支上被调用——不走 PDF 的条目照跑，于是这几处
一直没暴露：`henan_em` 靠 HTML 正文就能收满 3 条，NameError 被它自己
`except Exception` 吞在日志里；`ln_em` 抓到的 PDF 条目正文是空的。

收敛到一处，是为了让「漏掉一个模块」这件事不再可能——复制粘贴的第四份
总会再漏一次。
"""

from __future__ import annotations

from pathlib import Path


def extract_pdf_text(filepath: Path) -> str:
    """用 PyMuPDF 提取 PDF 全文。取不到（或短于 50 字）返回空字符串。

    短于 50 字判为失败，是因为政府站上的 PDF 常常是扫描件——PyMuPDF 只能
    取到零星的页眉页码，那种「提取到了」比提取不到更有害：它会让下游以为
    正文已经拿到。真正需要 OCR 的走 `needs_ocr` 那条路。
    """
    try:
        import fitz
        doc = fitz.open(str(filepath))
        text = "".join(page.get_text() for page in doc)
        doc.close()
        return text if len(text) > 50 else ""
    except Exception:  # noqa: BLE001 —— 缺 fitz、文件损坏都归为「取不到」
        return ""
