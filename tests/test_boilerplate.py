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

# 站点 HTML 里的字面量转义序列（反斜杠是字符本身，不是控制符）。
# 实测形态只有两种，见 `boilerplate._ESCAPE_HEAD` 上方那段。
ESC_RN = "\\r\\n"        # 4 字符
ESC_RNT = "\\r\\n\\t"     # 6 字符


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


# ── E 组：行首的字面量转义序列 ──
#
# 缺陷与实测见 `boilerplate._ESCAPE_HEAD` 上方那段。一句话：站点编辑从 Word
# 粘贴，把 `\r\n\t` 当成**文本**存进了 `<p>` 里；`str.strip()` 剥不掉它
# （反斜杠不是空白），而 12 个 TRS 源又不过 `cleaners.TextCleaner`。
#
# 这一组的**反例比正例要紧**：剥多了会把正文里合法的反斜杠吃掉。


def test_行首的字面量转义被剥掉():
    """实测形态：`<p>\\r\\n\\t\\xa0\\xa0 正文`。"""
    soup = BeautifulSoup(f"<div><p>{ESC_RNT}   某工地发生坍塌事故。</p></div>",
                         "html.parser")
    assert text_lines(soup.find("div")) == ["某工地发生坍塌事故。"]


def test_不带制表符的字面量也剥():
    """全站 140 个 `\\r\\n` 里有一半后面不跟 `\\t`。"""
    soup = BeautifulSoup(f"<div><p>{ESC_RN}某工地发生坍塌事故。</p></div>",
                         "html.parser")
    assert text_lines(soup.find("div")) == ["某工地发生坍塌事故。"]


def test_不带回车符的字面量也剥():
    """**这一条钉的是初版判据漏掉的第三种形态。**

    初版只认 `\\r\\n`（可跟 `\\t`），理由是「按字符统计只有 `\\r` 140 /
    `\\n` 140 / `\\t` 70，都能被前两种形态解释完」。那是**字符**账，而形态
    是**序列**——46 组 `\\n\\t` 混在里面看不出缺口。修后复核残留 92 个转义
    才把它逼出来，全部在「4·19」神木县板定梁塔煤矿透水事故那一页。
    """
    soup = BeautifulSoup("<div><p>\\n\\t2017年4月19日凌晨，神木县发生透水事故。</p></div>",
                         "html.parser")
    assert text_lines(soup.find("div")) == ["2017年4月19日凌晨，神木县发生透水事故。"]


def test_连续多组字面量都剥():
    soup = BeautifulSoup(f"<div><p>{ESC_RNT}{ESC_RN}正文。</p></div>",
                         "html.parser")
    assert text_lines(soup.find("div")) == ["正文。"]


def test_贴着nbsp的字面量也剥():
    """实测是 `\\r\\n\\t` 紧接一串 `\\xa0`（`&nbsp;`）——先 strip 再剥再 strip。"""
    soup = BeautifulSoup(f"<div><p>{ESC_RNT}\xa0\xa0\xa0 正文。</p></div>",
                         "html.parser")
    assert text_lines(soup.find("div")) == ["正文。"]


def test_整行只有字面量时该行被剔掉():
    """剥完是空串，按「非空行」规则丢掉，不会留一条空行。"""
    soup = BeautifulSoup(f"<div><p>{ESC_RNT}</p><p>正文。</p></div>",
                         "html.parser")
    assert text_lines(soup.find("div")) == ["正文。"]


def test_反例_正文里合法的反斜杠不动():
    """**剥多了比剥少了糟。** 正文里合法出现的反斜杠不能碰——
    比如 Windows 路径、正则写法。判据只在**行首**且匹配实测形态时动手。"""
    soup = BeautifulSoup(
        "<div><p>安装路径 C:\\temp\\build 下的文件</p>"
        "<p>正则是 \\d+ 匹配数字</p></div>", "html.parser")
    assert text_lines(soup.find("div")) == [
        "安装路径 C:\\temp\\build 下的文件", "正则是 \\d+ 匹配数字"]


def test_反例_行首单独的字面量换行不动():
    """判据只认实测的三种形态：`\\r\\n\\t` / `\\r\\n` / `\\n\\t`。

    **单独一个 `\\n`（后面不跟 `\\t`）不剥**——本项目语料里没有这种形态，
    而放宽会把「正文里真以 `\\n` 开头的行」也吃掉。宁可少剥。

    注意边界就在那个 `\\t` 上：`\\n\\t` 剥，`\\n `（后跟空格）不剥。
    """
    soup = BeautifulSoup("<div><p>\\n 这一行以字面量反斜杠n开头</p></div>",
                         "html.parser")
    assert text_lines(soup.find("div")) == ["\\n 这一行以字面量反斜杠n开头"]


def test_反例_行中间的字面量不动():
    """只在行首剥。句子中间的 `\\r\\n` 保持原样（它可能是正文的一部分）。"""
    soup = BeautifulSoup("<div><p>前一句\\r\\n后一句</p></div>", "html.parser")
    assert text_lines(soup.find("div")) == ["前一句\\r\\n后一句"]


def test_真实页面形态_端到端():
    """照实测 HTML 写一条：正文四段，每段带 `\\r\\n\\t` + `&nbsp;` 前缀。"""
    html = ("<html><body><div class='lfNewsDetail'>"
            f"<p>{ESC_RNT}&nbsp;&nbsp;&nbsp; 2013年7月23日10时30分左右，"
            "渭南市澄城县硫磺矿井下炸药库炸药自燃，发生火灾。</p>"
            f"<p>{ESC_RNT}&nbsp;&nbsp;&nbsp; 事故发生后，省委、省政府高度重视。</p>"
            "</div></body></html>")
    lines = page_lines(html)
    assert len(lines) == 2
    assert all(not ln.startswith("\\") for ln in lines)
    assert lines[0].startswith("2013年7月23日10时30分左右")


def test_字面量不再虚增正文长度():
    """**这条钉的是危害本身**，不只是字符串处理。

    `trs_cms_base.py:396` 用 `len(combined_text) < BODY_MIN_CHARS(200)` 判
    「无有效正文」→ 进审核队列。字面量每段白送 6 个字符，实测 `shaanxi_em`
    178 页里有 3 条本该进审核队列的页被顶过门槛（196→216、192→222）。

    这里用一段短正文复现那条通路：剥之前够 200、剥之后不够。
    """
    from construction_agent.collector.base import BODY_MIN_CHARS
    # 20 段，每段 6 个字面量字符 + 5 个正文字
    body = "".join(f"<p>{ESC_RNT}正文五字</p>" for _ in range(20))
    html = f"<html><body><div>{body}</div></body></html>"
    raw = "".join(ln + "\n" for ln in
                  [f"{ESC_RNT}正文五字"] * 20)
    assert len(raw) > BODY_MIN_CHARS          # 旧行为：过闸
    cleaned = "\n".join(page_lines(html))
    assert len(cleaned) < BODY_MIN_CHARS      # 新行为：该进审核队列
    assert "\\" not in cleaned
