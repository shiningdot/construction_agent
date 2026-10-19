"""四川省应急管理厅 (yjt.sc.gov.cn) 事故调查报告采集器。

目标: https://yjt.sc.gov.cn/scyjt/dcbg/GovInfoPublic.shtml
TRS CMS，基于 TrsCmsCollector 通用基类。

## 列表项有**两种形态**，只收一种等于漏掉一半（2026-10-05）

栏目页 53,737 字节 / 79 个 `<a href>`；真正属于 `dcbg` 条目的只有 **10** 个，
分两类（**4 + 6 = 10**，与 `_parse_list` 的产出逐条对得上——原先这张表写的
是"4 条 + 5 条"，加起来 9，**自己就对不上**）：

| 形态 | 样子 | 正文在哪 |
|---|---|---|
| 详情页 | `/scyjt/dcbg/2023/8/14/<32 位 hex>.shtml` | 页面自己（**4** 条，全是水电站 / 道路交通 / 食品厂）|
| **PDF 直链** | `/scyjt/dcbg/2026/1/5/<32 位 hex>/files/<名>.pdf` | **就是它自己**（**6** 条）|

（页面里另有 56 个 `<a>` 的 href 含 `.shtml`，形态不合格——那是导航与栏目页
自己。`_parse_list` 不去重，而页面上没有重复 href，故 10 进 10 出。）

**原先只收第一种，理由是「PDF 直链没有配套的详情解析，收进来只会多出一条
有标题、无正文的记录」。这个判断前半句错了。** PDF 直链**不需要**详情解析
——它自己就是正文；而基类 `_crawl_detail` 之后的 PDF 通路（`_download_and_
extract_pdf`，对全站默认有效）本来就能吃下它。写那条注释时把「没有详情页」
当成了「没有正文」，于是**唯一带建筑施工内容的几条恰好全被挡在外面**：

```
成都成华臻礼著项目“9·1”较大起重伤害事故调查报告        ← 通过初筛（项目/起重）
四川自贡九鼎大楼“7·17”重大火灾事故调查报告            ← 通过初筛（火灾）
自贡九鼎大楼“7·17”…防范和整改措施落实情况评估报告     ← 通过初筛（火灾）
```

而收得到的 4 条 `.shtml` 标题里一个建筑关键词都没有（健康检查表上正是
「空 / 列表 4 / 闭环 ✓」——**闭环成立说明 4 条全进了初筛过滤，账没错，
是入口少了**）。修法见 `docs/web/01_在线采集.md` §2.5.54。

## 分页：确实只有一页（顺带核实）

健康检查表提示「配了 `list_page_pattern` 却仍只有一页量级 → 分页 URL 可能
没拼对」。本页源码里写着：

```js
createPageHTML('page_div', 1, 1, 'GovInfoPublic', 'shtml', 10);
```

第 2、3 个参数都是 1——**总页数就是 1**，不是"单页"这句没查过的注释。
`_discover_pages` 不探分页是对的。

## 端到端验收（2026-10-05 实测一次 `collect()`）

```
[sc_em] 采集完成: 列表10 → 初筛过滤7 → 分类过滤2 → 收录0 + 审核1 (2.5s)
```

- **列表 4 → 10**：多出来的 5 条全是 PDF 直链，其中 3 条过初筛。
- **正文 0 字 → 29,937 字**：「成都成华臻礼著项目“9·1”较大起重伤害事故调查
  报告」整篇提取出来（标题 → 目录 → 正文，**0 个替换字符**）；另两份也都
  提出了整篇，无一份是空壳。口径 = `collect()` 产出 `raw_text` 的 `len()`，
  **与健康检查表同一口径**（直接调 `extract_pdf_text` 再 `.strip()` 会少 1 个
  字符，那是换行，不是差异）。
- 被分类闸门挡掉的两条是自贡九鼎大楼“7·17”重大火灾（正本 + 整改评估），
  `classify_v2` 判为**高置信非施工**——这是判据在工作，不是漏收。
- 起重伤害那条置信度不够 `high`，进**审核**队列而不是直接收录，符合设计。

## 已知的一处浪费：比原先记的更彻底（不是缺陷，记着）

`collect()` 的第 3b 步（统计模板文字）会先对**每个候选**抓一遍页面，
PDF 直链会在这里被下载一次，随后主循环的 `_download_and_extract_pdf`
**再下载一次**。

本轮实测发现这一抓是 **100% 白费**，比"多下载一次"更彻底：抓回来的 PDF
字节被投进 `page_lines(h, selector)` —— **PDF 当 HTML 解析，得 0 行**，
三页交集为空，`tpl` 是空集，整个模板剥除随后**直接退化不用**。而
`_crawl_detail` 对 PDF 直链本就短路、连 `detail_html` 都不看。这 3 次下载
（约 3.6 MB）加上解析，产出被完整丢弃。

没顺手合并不代表没看见：`_download_and_extract_pdf` 只认 URL、不做
"接收已抓字节"的分支，要省这一次得给基类加钩子（"这个地址要不要抓
HTML"）——那是**为一个源的特例改 12 个源的公共签名**，与 `jl_em` 那轮
「修法全落在子类，不动基类」是同一条取舍。本源的 PDF 候选只有个位数，
**记在这里而不是悄悄吞掉**。

## 顺带修掉基类一处说谎的日志（2026-10-05）

上面那个白抓还暴露了 `trs_cms_base.py` 一条日志缺陷：`tpl` 空集有**两种**
成因（① 页数不够 ／ ② 页数够但无公共行），旧日志把 ② 也说成 ①，打印出
「候选页只有 3 页（**<3**）」——**自相矛盾的句子**（3 不小于 3），把诊断
引向"分页没配好"。已按成因分岔，判据一字未动。**12 个 TRS 源此前只有 ①
的案例**，所以这句话一直没被证伪；不是"以前对、现在坏了"，是第一次走到
没走过的分支。
"""

from __future__ import annotations

import re
from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings

# 详情页与 PDF 直链共用一个前缀，只在尾段分岔：`.shtml` 还是 `/files/*.pdf`。
# 两者都收（见模块 docstring）。PDF 名带 URL 编码的中文，故尾段用 `[^"']+`。
_LIST_ITEM = (
    r"/scyjt/dcbg/\d{4}/\d{1,2}/\d{1,2}/[0-9a-f]{16,}\.shtml"
    r"|/scyjt/dcbg/\d{4}/\d{1,2}/\d{1,2}/[0-9a-f]{16,}/files/[^\"']+\.pdf"
)

# 「这一条列表项本身就是 PDF」的判据。去掉 query 再比尾段——附件链接偶带
# `?download=1` 一类后缀，按整串判会漏。
_PDF_TAIL = re.compile(r"\.pdf$", re.I)


def _is_direct_pdf(url: str) -> bool:
    return bool(_PDF_TAIL.search(url.split("?", 1)[0]))


class ScEMCollector(TrsCmsCollector):
    """四川省应急管理厅事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="sc_em",
            authority_name="四川省应急管理厅",
            base_url="https://yjt.sc.gov.cn",
            list_url="https://yjt.sc.gov.cn/scyjt/dcbg/GovInfoPublic.shtml",
            list_page_pattern="",
            # 原值 r"/\d{6}/t\d{8}_\d+\.html" 是另一种 CMS 的格式，本页一条都不匹配
            # ——列表页 HTTP 200、54 KB、79 个 <a>，正则命中 0，于是整个源恒返回空。
            # 2026-10-05 补上 PDF 直链分支（原只收 .shtml，理由见模块 docstring）。
            list_link_pattern=_LIST_ITEM,
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "sc_em"))

    def _crawl_detail(self, url: str,
                      html: object = None) -> dict | None:
        """列表项是 PDF 时，跳过详情解析，把地址原样交回主循环当附件。

        **不抓页面**：这个 URL 后面没有 HTML，`_fetch` 拿回来的就是 PDF 字节
        （主循环第 3b 步已经抓过一次，传进来的 `html` 就是那份）。基类的
        `_crawl_detail` 会把它当 HTML 塞进 `BeautifulSoup`——解析不报错，
        但什么也取不到，最后落一条「有标题、无正文」的记录，**正是原先
        写「收进来只会多一条空壳」时担心的那个形态**。

        `title` / `date` 留空是有意的：主循环里
        `detail_title = detail.get("title") or title`、
        `detail_date = detail.get("date") or list_date`——两处都会退回**列表项
        自己的**标题与日期（`_parse_list` 已从 `<a>` 文本和 URL 里取到了），
        比在这里重算一遍可靠。
        """
        if _is_direct_pdf(url):
            return {"title": "", "date": "", "text": "", "text_lines": [],
                    "pdf_urls": [url], "office_urls": [], "image_urls": []}
        return super()._crawl_detail(url, html)          # type: ignore[arg-type]


def crawl_sc_em(delay: float = 1.0) -> list:
    return ScEMCollector(delay=delay).collect()
