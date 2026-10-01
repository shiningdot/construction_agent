"""跨页恒定行 = 模板文字：从 `body` 全文里**减掉**导航与页脚。

## 为什么是「减」而不是「选」

第十六轮查出缺陷 G：`detail_text_selector` 默认 `body`，导航与页脚因此混进
正文，`min_text_length` 那道防线在 12 个 TRS 源上从未触发过。第十七轮试了
三条修法，**全不通**：

| 修法 | 撞墙处 |
|---|---|
| 预设选择器表 | `js_em` 上选中标题栏 |
| 指定最内层容器 | `cq_em` 上写不出 selector（容器 `id`/`class` 全空） |
| 剥离通用模板容器 | `cq_em`/`sh_em`/`sx_em` 上剥离 0 字（26 个选择器一个没命中） |

三条路都在找「**一条能选中正文的 CSS 选择器**」。换个方向：**不选，减**。

## 判据与它量出来的东西

一页的 `body` 全文切成行；对 N 个页面统计每行出现在多少页上；**出现在全部
N 页上的行 = 模板文字**。真正文 = 全部行 − 模板行。

2026-10-01 实测 12 个 TRS 源（每源 4–5 页）：

| 源 | `body` 字数 | 模板文字 | **真正文** | 模板占比 |
|---|---|---|---|---|
| `hain_em` | 1,224–1,230 | **1,128** | **96–102** | 92% |
| `tj_em` | 286–309 | **225** | **61–84** | 79% |
| `sx_em` | 547–566 | **426** | **121–140** | 78% |
| `gz_em` | 499–537 | **396** | **103–141** | 76% |
| `cq_em` | 1,706–1,798 | **911** | 795–887 | 53% |
| `shaanxi_em` | 1,579–7,338 | 808 | 771–6,530 | 11–51% |
| `sc_em` | 1,780–10,569 | 1,024 | 756–9,491 | 10–58% |
| `js_em` | 617–819 | 41 | 576–778 | 6% |
| `sh_em` | 286–454 | 7 | 279–447 | 2% |

前四个源**真正文不足 140 字**，而它们的 `body` 都在 300–1,230 字之间，
稳稳越过 `min_text_length(30)`——这就是缺陷 G 的全貌。而它们正是普查里
标记为「图片型」的那几个源：模板文字**掩盖了图片型**。

## 误伤验证

最可能被误伤的是**事故报告的小标题**——「一、事故经过」「（一）直接原因」
这类行在好几份报告里都会出现，若 N 页全有就会被剥掉。

实测四个模板行最多的源（`shaanxi_em` 116 行、`cq_em` 200 行、`sc_em` 177 行、
`hain_em` 190 行），**683 行里像正文小标题的 0 行**——全是页脚友情链接、备案号、
导航项：「省司法厅」「地址：西安市经开区未央路208号」「扫一扫在手机打开当前页」。

方向也是安全的：**减法是宁可少减**。漏剥一行模板，代价是正文多几字；误剥一行
小标题，代价是少一行，主体段落不受影响——跨页恒定的长段落不存在，683 行里
长度 ≥40 字的只有 2 行，也都是页脚（主办单位、监督电话）。
"""

from __future__ import annotations

from bs4 import BeautifulSoup

# 少于这么多页时**整体放弃剥离**。
#
# 2 页里「都出现」的行误判率太高（任意两页共有的小标题都会中招），而
# `collect()` 里候选常常只有三五条。够不着就退化成现行为（不剥），
# 而不是剥一半——**宁可少减**。
MIN_PAGES = 3


def text_lines(node) -> list[str]:
    """把一个 BeautifulSoup 节点切成非空行。

    **必须带 `separator`**：`get_text()` 不给分隔符时相邻标签的文字会直接
    粘成一整块，「行」这个概念就没了——而本模块整个判据都建立在行上。
    （`trs_cms_base._crawl_detail` 原先用的正是无分隔符的 `get_text()`。）
    """
    return [ln.strip() for ln in node.get_text(separator="\n").split("\n")
            if ln.strip()]


def page_lines(html: str, selector: str = "body") -> list[str]:
    """从一页 HTML 里取出正文候选行。取不到返回空列表。"""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(selector)
    if node is None:
        return []
    return text_lines(node)


def template_lines(pages: list[list[str]], min_pages: int = MIN_PAGES) -> set[str]:
    """出现在**全部**页上的行。

    页数不足 `min_pages` 时返回空集——调用方据此退化到「不剥」，见
    `MIN_PAGES` 的说明。
    """
    if len(pages) < min_pages:
        return set()
    common = set(pages[0])
    for p in pages[1:]:
        common &= set(p)
    return common


def strip_template(lines: list[str], tpl: set[str]) -> str:
    """去掉模板行后拼回正文。`tpl` 为空时原样拼回。"""
    if not tpl:
        return "\n".join(lines)
    return "\n".join(ln for ln in lines if ln not in tpl)
