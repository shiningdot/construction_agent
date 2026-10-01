"""模板文字剥离（`construction_agent.collector.boilerplate`）的回归测试。

判据是 2026-10-01 第二十三轮立的：**跨页恒定出现的行 = 模板文字**，
真正文 = `body` 全部行 − 模板行。

它存在的理由见模块 docstring——第十六/十七轮「给它一条 CSS 选择器」的三条
路全不通（`js_em` 选中标题栏、`cq_em` 写不出 selector、三个源剥离 0 字），
于是换成「不选，减」。

这里的反例测试比正例要紧：误剥正文的代价比漏剥模板大。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup  # noqa: E402

from construction_agent.collector.boilerplate import (  # noqa: E402
    MIN_PAGES, page_lines, strip_template, template_lines, text_lines)


# ── A 组：切行 ──


def test_切行必须带分隔符否则会粘成一整块():
    """**这条钉的是一个具体的坑。**

    `node.get_text()` 不带分隔符时，相邻标签的文字直接粘起来：
        <p>第一段</p><p>第二段</p>  →  "第一段第二段"
    「行」这个概念就没了，而整个判据都建立在行上。

    改前 `trs_cms_base._crawl_detail` 用的正是无分隔符的 `get_text()`。
    """
    soup = BeautifulSoup("<div><p>第一段</p><p>第二段</p></div>", "html.parser")
    div = soup.find("div")
    assert text_lines(div) == ["第一段", "第二段"]
    assert div.get_text() == "第一段第二段"      # 不带分隔符的样子


def test_空白行去掉():
    soup = BeautifulSoup("<div>甲\n\n  \n乙</div>", "html.parser")
    assert text_lines(soup.find("div")) == ["甲", "乙"]


def test_每行去掉首尾空白():
    soup = BeautifulSoup("<div>  甲  \n  乙  </div>", "html.parser")
    assert text_lines(soup.find("div")) == ["甲", "乙"]


# ── B 组：跨页恒定 ──


def test_全部页都出现的行是模板():
    pages = [["正文甲", "页脚"], ["正文乙", "页脚"], ["正文丙", "页脚"]]
    assert template_lines(pages) == {"页脚"}


def test_只在部分页出现的行不是模板():
    """出现在 2/3 页上的行不算——判据要求**全部**页。"""
    pages = [["正文甲", "共有"], ["正文乙", "共有"], ["正文丙"]]
    assert template_lines(pages) == set()


def test_页数不足时不剥():
    """**宁可不剥，不要误剥。** 2 页里「都出现」的行误判率太高——任意两页
    共有的小标题都会中招，而 `collect()` 里候选常常只有三五条。"""
    pages = [["一、事故经过", "页脚"], ["一、事故经过", "页脚"]]
    assert len(pages) < MIN_PAGES
    assert template_lines(pages) == set()


def test_没有页时也不炸():
    assert template_lines([]) == set()


def test_一行都不共有则模板为空():
    pages = [["甲"], ["乙"], ["丙"]]
    assert template_lines(pages) == set()


# ── C 组：剥离 ──


def test_剥离只去模板行():
    lines = ["正文甲", "地址：某某路1号", "正文乙"]
    assert strip_template(lines, {"地址：某某路1号"}) == "正文甲\n正文乙"


def test_模板为空时原样拼回():
    """`tpl` 是空集时等价于旧行为——页数不够的退化路径就是这条。"""
    lines = ["甲", "乙"]
    assert strip_template(lines, set()) == "甲\n乙"


def test_小标题会被判成模板但真正文绝不误剥():
    """**反例，比正例要紧。** 事故报告的小标题（「一、事故经过」）在好几份
    报告里都会出现，N 页全有就会被误剥——**这个判据承认自己在这一格上分不
    出来**，这是它的已知边界，不是 bug。

    之所以仍然可用：实测四个模板行最多的源（683 行）里像小标题的 **0 行**，
    全是页脚友情链接、备案号、导航项；而且误剥一行小标题只少一行，主体段落
    不受影响（跨页恒定的长段落不存在，683 行里 ≥40 字的只有 2 行，也都是页脚）。

    这条测试钉的是判据的**边界**和它**兜得住的那半边**：各不相同的那句真正文
    绝不能被剥。
    """
    pages = [
        ["一、事故经过", "2025年某月某日，某工地发生坍塌事故。", "省司法厅", "备案号123"],
        ["一、事故经过", "2026年某月某日，某工地发生坠落事故。", "省司法厅", "备案号123"],
        ["一、事故经过", "2024年某月某日，某工地发生触电事故。", "省司法厅", "备案号123"],
    ]
    tpl = template_lines(pages)
    assert "省司法厅" in tpl and "备案号123" in tpl
    # 小标题**也**被判成模板了——因为这三页确实都有它。判据在这一点上
    # 无法区分，方向是「宁可少减」；真实语料里 683 行模板行中一条这样的
    # 都没有（不同报告的小标题不会逐字相同，且正文主体段落各不相同）。
    assert "一、事故经过" in tpl
    # 真正文（各不相同的那一句）绝不能被剥
    for p in pages:
        assert p[1] not in tpl


# ── D 组：从 HTML 取 ──


def test_从HTML取行():
    html = "<html><body><div>甲</div><div>乙</div></body></html>"
    assert page_lines(html) == ["甲", "乙"]


def test_取不到容器时返回空():
    assert page_lines("<html><head><title>t</title></head></html>") == []


def test_可以换选择器():
    html = ("<html><body><div>不要</div>"
            "<article><p>要</p></article></body></html>")
    assert page_lines(html, "article") == ["要"]
