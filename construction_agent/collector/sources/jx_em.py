r"""江西省应急管理厅 (yjglt.jiangxi.gov.cn) 事故调查报告采集器。

目标: https://yjglt.jiangxi.gov.cn/jxsyjglt/col/251210dxsgal/（栏目「典型事故案例」）
TRS CMS, 基于 TrsCmsCollector 通用基类。

## 本源有**三层**故障，三层已全部修好（2026-10-01 起，2026-10-05 修完）

### 第一层：TLS 老式重协商（**已修**）

本源在健康检查表上一直是「列表 0 条」，原因不在解析、不在选择器，**在 TCP
之上就断了**：服务器仍在用 RFC 5746 之前的老式重协商，OpenSSL 3.x 默认禁用
（`UNSAFE_LEGACY_RENEGOTIATION_DISABLED`），握手直接谈不成。

放开 `OP_LEGACY_SERVER_CONNECT` 后 TLSv1.2 谈成、HTTP 200、271,906 字节
——站点本身完全健康，没有 412、没有 JS 挑战。所以这里显式打开该开关；
判据与安全代价见 `trs_cms_base.build_ssl_verify`。

### 第二层：列表页已不是 HTML（**已修** 2026-10-05）

TLS 修好后 `list_link_pattern` 命中 **0 条**。实测原因：原 `list_url` 返回的
是一个 SPA 壳——全页只有 7 个 `<a>`，**全部是 `javascript:void(0)`**。
真正的列表数据在 JSON 接口上：

```
GET /jxsyjglt/251210dxsgal/251210dxsgal-articleList.json   → 200 / 15 条
```

每条给 `title` / `pubDate` / `id` / `urls.pc`（详情页真址形如
`/jxsyjglt/col/251210dxsgal/content/content_<id>.html`）。**这个形态与老版
TRS 的 `/202609/t20260917_9714722.html` 完全不同**，基类现在没有 JSON 通路。

两个细节（2026-10-05 实测）：

- **`urls` 字段是双重编码的 JSON 字符串**——`json.dumps` 一层后形如
  `"{\"pc\":\"/jxsyjglt/col/…\"}"`。直接 `item["urls"]["pc"]` 会拿到一个
  `str` 的下标，或拼出一个带引号的 URL（`httpx.InvalidURL: Invalid port: '"'`）。
  要先 `json.loads(item["urls"])`。
- **接口只吐 15 条，而栏目元数据说 `articleCount = 49`**：GET 的六种分页参数
  （`page` / `pageNum` / `pageIndex` / `pageSize` / `size` / `limit`）、POST、
  路径式后缀（`/index_2.json` 等）**全部无效或 404**——接口不提供翻页。

### 第三层：正文藏在 JS 数据岛里，而且有**两种形态**（**已修** 2026-10-05）

> **2026-10-05 更正**：本条原先写「详情页是 Handlebars 模板壳，得先找到喂给
> 模板的数据接口；5 个候选（`content_<id>.json` 等）全部 404」。**那个判断错
> 了方向**——正文不需要任何额外接口，它就在详情页 HTML 自己的内联 `<script>`
> 里，`_crawl_detail` 拿不到只是因为 `BeautifulSoup` 对 `<script>` 内容一律当
> **文本**（与 `js_em` 数据岛是同一个机制，见 `_unwrap_data_islands`）。

数据岛形如（`content_2074388022702022656.html`，2265 字符）：

```js
// 1 519199 文章正文 col0
const col0_519199 = [{
  content:{"content":"<p …><a class=\"tinymce-annex\"
      href=\"/jxsyjglt/251210dxsgal/2074388022702022656/ZD7IpYi6.pdf\"
      download=\"…评估报告.pdf\">…评估报告．pdf</a>&nbsp;</p>"},
  images:[{}], title:'…', pubDate:'2026-07-07 14:59',
  urls:'{"pc":"/jxsyjglt/col/251210dxsgal/content/content_2074388022702022656.html"}',
  …
}]
```

三点实测结论：

1. **它是 JS 对象字面量，不是 JSON**——键无引号（`content:{…}`），
   `json.loads` 直接报 `Expecting property name enclosed in double quotes`。
   要解析得先给无引号键补引号，或按 `"content"\s*:\s*"((?:[^"\\]|\\.)*)"`
   抠值再反转义（后者实测三篇全部成功）。
2. **正文就是一串附件链接**——三篇样本的 `content.content` 剥掉标签后分别是
   「…评估报告．pdf」/「…调查报告.pdf」/「…评估报告.pdf」，正文实体全在
   **PDF 里**。这与 `ln_em`（默认下 PDF）、`gd_em`（附件是 Word）是同一族。
3. **PDF 是相对路径、可拼可下**：`/jxsyjglt/<文章id>/<随机8位>.pdf`。
   基类 `_crawl_detail` 的附件提取（PDF / Office / 图片、多附件去重、
   `_absolute`）**本来就能处理这种链接**——只要它进得了 DOM。

所以第三层的修法不是「找接口」，是**让数据岛里的 HTML 进 DOM**：给
`_unwrap_data_islands` 加一种形态（无 `type` 属性的内联 JS 字面量），附件
链接就会被现成的选择器抓到，PDF 下载与 `extract_pdf_text` 也都是现成的。

#### 2026-10-05 复测更正：数据岛里装的不只有附件链接

上面的推理只说对了一半。实际落地的第一版**只把 `<a>` 开标签补进 DOM**
（怕"凭空多出来的文字污染正文"），理由是那三篇样本剥掉标签后都是一串
「…报告．pdf」。**另外 3 条候选页给出了反例**：它们的 `content.content`
就是正文本身——784 / 2,842 / 276 字的 HTML 富文本，页面上 `<a href>` **0 个**。
只补 `<a>` 对它们是空操作，于是这三条正文**全是 0 字**，而健康检查表上
它们显示「有数据 3 条」——**只有"正文中位 0 字"这一格露了马脚**。

修法是把整个 `content` 交出去（函数随之从 `_island_attachment_tags` 改名为
`_island_content_html`）：`<a>` 天然包含在 HTML 里，附件通路一分不减，正文也
第一次进得了 DOM。详见 `_island_content_html` 的 docstring 与
`docs/web/01_在线采集.md` §2.5.55。

### 结论：三层已全部修好（2026-10-05）

**修好第一层不等于能采到**——这个站整体迁到了「JSON 列表 + JS 数据岛正文 +
PDF 附件」的新一代 TRS，三层故障是**叠着**的，缺任何一层都一条都采不到。

三层的修法都落在**子类**上，不动基类（理由见形态普查，`docs/web/01_在线采集.md`
§2.5.46：同位形态实测只有本源一家，而改基类要经过全部 13 个 TRS 源的页面）：

| 层 | 改哪里 | 做什么 |
|---|---|---|
| 列表 | `_discover_pages` | 返回 JSON 接口地址，不探分页（接口本就不给翻页） |
| 列表 | `_parse_list` | 解析 JSON；`urls` 双重编码，先反解再取 `pc` |
| 详情 | `_crawl_detail` | 把数据岛里的**整个 content HTML** 插进 `<body>` **里面**——附件与正文两条通路同时通（2026-10-05 更正：原先只补 `<a>` 开标签，正文型数据岛一个字也进不来，见下） |

**实测（2026-10-05）**：列表接口 15 条全部解析出（URL 干净、日期取自
`pubDate`）；详情页收到 1 个 PDF；下载后提取出 2,788 字正文——「吉安峡江县
戈坪铁矿有限公司『7·27』较大冒顶片帮瞒报事故…」。

**只做列表层是没有意义的**，这一点在实装之前就写在这里：那样健康检查会从
「解析空」变成「有数据 15 条」，而详情正文仍为空——**把「采不到」变成
「采到一堆空壳」**，比原来更容易被放过。所以两层必须同一轮做完。

#### 端到端实测（2026-10-05）：通路全通，但 `_collected` 仍是 0

脚本级直调（`_parse_list` / `_crawl_detail` / `_download_and_extract_pdf`）全过
之后，跑了一次完整的 `collect()`——**这一步才第一次答得出「到底采到几条」**：

| 计数器 | 值 | 修之前 |
|---|---|---|
| `_list_pages_ok` | 1 | — |
| `_total_listed` | **15** | **0** |
| `_fetch_failed` | 0 | 0（当时断在 TLS） |
| `_filtered_title` | 11 | — |
| `_filtered_classify` | 1 | — |
| `_collected` | **0** | 0 |
| `_review` | 3 | — |

**通路是通的，产出是 0**，原因是**源的内容主题与项目口径不匹配**：本源栏目
叫「典型事故案例」，内容是广义的——矿山冒顶片帮、尾矿库、煤与瓦斯突出、火灾、
道路交通。标题初筛放行的 4 条恰好全是「火灾 / 爆炸」，其中唯一带附件的一条
（赣州经开区「8·5」一般火灾）**PDF 提出 4,498 字真正文**，闸门判
「非建筑施工: 防汛」拦掉；其余 3 条本就没有附件 → 进审核队列。**每一步都对。**

所以「修好三层」的收益不是「采到建筑施工事故」，而是**这 15 条第一次有了
被裁决的机会**——在此之前它们连进闸门的资格都没有。

**（2026-10-05 补记：上面这张表里的 `_filtered_classify` = 1 / `_collected` = 0
是「只补 `<a>` 开标签」那一版的读数。同日复测发现 3 条候选页正文全为 0 字
（正文在数据岛里、页面上 `<a href>` 为 0），把整个 content 补进 DOM 之后这 3 条
正文变成 **786 / 2,842 / 276 字**，两格计数变成 **3 / 1**、`_review` 3 → 0
——分类器第一次拿到正文，才判得出「施工与否」。详见
`docs/web/01_在线采集.md` §2.5.55。**）**

#### 一个还在的风险：那 467 字模板源码

`_crawl_detail` 返回的 `text` 里**始终带着**同一份 467 字模板源码
（`{{#each data}}` 那一串）——**本轮的修法没有动它**，动的是同一个返回值里
**还多了真实正文**（见上一节）：修前那 3 条候选的 `text` 除了模板壳一无所有，
`strip_template` 剥完剩 0；修后模板壳照旧在，正文也进来了，剥除只吃掉壳。

剥除靠 `collect()` 的 `strip_template`，它有前提：**候选页数 ≥ `MIN_PAGES`**。
页数不够时 `collect()` **有日志**（「本轮不剥模板文字」，不无声），但**没有
防护**——那时这 467 字会**连着真实正文一起**原样进正文。某个季度只上来一两条
新候选时，就会发生。**本轮没有改变这一点**（记为 §2.5.55 第八节的未决项）。

#### 教训：脚本级直调过了 ≠ `collect()` 过了

上一轮的四段直调全绿就报了「三层全部打通」。直调验的是「给定这个 HTML，
函数返回对不对」；`collect()` 还要串过标题初筛、模板行统计、附件下载、分类
闸门四道——**「采到几条」这个最终判据只有 `collect()` 答得出**。跑完才知道
是 0 条。**这个 0 不是故障，但没跑之前没人知道它不是。**

同一个现象（表上显示「空」）在本源是**三层**，在 `jl_em` 是域名笔误 + 列表
JS 渲染两层，在 `hub_em` 是 JS 挑战型 WAF 一层——这正是 `_fetch_failed`
那个计数器存在的理由，也是「一格指标压掉多种故障」这件事的样本。

**（2026-10-05 补记：上面这句里对 `jl_em` 的预测已被实测证实——正好两层，
且详情层不用改，见 `docs/web/01_在线采集.md` §2.5.53。这是「形态普查的结论
能外推」的一个正面样本；`hub_em` 见 §2.5.52。）**
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
    _absolute,
)
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# 列表数据在 JSON 接口上（模块 docstring 第二层）。**这是接口的完整地址**，
# 由栏目名拼出来：`/jxsyjglt/<栏目>/<栏目>-articleList.json`。
_LIST_API = ("https://yjglt.jiangxi.gov.cn/jxsyjglt/251210dxsgal/"
             "251210dxsgal-articleList.json")

# 详情正文的 JS 字面量数据岛（模块 docstring 第三层）。
# 抠的是 `const col0_519199 = [{…}]` 里 `content.content` 那个**值**——值本身是
# 标准 JSON 字符串（带转义），所以只需反解，不必去理解外围的 JS 语法。
# 上一轮试过「给无引号键补引号再 json.loads」，岛里更复杂的 JS 语法让它失败了；
# 这一条窄，但窄在这里是优点。
_ISLAND_ASSIGN = re.compile(r"const\s+col\d+_\d+\s*=\s*\[")
_ISLAND_CONTENT = re.compile(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _island_content_html(html: str) -> str:
    """把 JS 字面量数据岛里的**正文 HTML** 取出来，交回给基类。

    **这个函数原先叫 `_island_attachment_tags`，只提 `<a>` 开标签。** 那个做法
    建立在「数据岛里装的是附件链接」这一观察上——2026-10-05 的三篇样本确实如此
    （`content.content` 剥掉标签后是「…调查报告.pdf」）。**但那只是数据岛两种
    内容形态里的一种。** 同一天复测 15 条里的另外 3 条，正文**直接就是 HTML
    富文本**：

    | 页面 | 数据岛里的正文 | 该页 `<a href>` |
    |---|---|---|
    | 民爆物品爆炸事故警示 | **784 字** | **0** |
    | 揭阳市“3·26”重大火灾事故调查报告 | **2,842 字** | **0** |
    | 江苏省镇江丹阳市吕城镇惠济村…爆炸并坍塌事故 | **276 字** | **0** |

    只补 `<a>` 对这三页是**完全的空操作**：`_crawl_detail` 拿到的仍是那一份
    467 字模板壳，`strip_template` 剥完剩 0——**产出 3 条正文全为 0 的记录，
    而健康检查表上它们叫「有数据 3 条 / 正文中位 0 字」**。这与 §2.5.54 同族
    （**同一个容器的两种形态，只接了一种**），区别是本轮更隐蔽：那一轮的产物
    是「列表 4 条 vs 10 条」，少了一半还看得出来；本轮的**条数完全正常，
    只有正文是空的**。

    改法不是「再加一条判据」，是把**整个 content 交出去**——`<a>` 天然包含在
    HTML 里，附件通路一分不减，而正文也第一次进得了 DOM。原注释担心「凭空多
    出来的文字会污染正文」，**那个担心指错了对象**：多出来的那些字正是要采的
    正文本身（浏览器渲染后它们本来就在页面上）。

    数据岛不存在时返回空串——**这不是错误**。绝大多数 TRS 源是纯 HTML
    （§2.5.46 实测 9 家有数据的源全部如此），这个函数对它们必须是无操作。
    """
    if not _ISLAND_ASSIGN.search(html):
        return ""
    parts: list[str] = []
    for m in _ISLAND_CONTENT.finditer(html):
        try:
            parts.append(json.loads('"' + m.group(1) + '"'))
        except json.JSONDecodeError:
            # 抠到了但反解不了：记一声再跳过。**静默跳过与「岛上没有正文」
            # 长得一样**，而这两种情况的处理完全不同（后者不用管，前者要查）。
            logger.warning(f"数据岛 content 反解失败（{len(m.group(1))} 字符的转义串）")
            continue
    return "".join(parts)


def _inject_into_body(html: str, extra: str) -> str:
    """把 `extra` 插进 `<body>` **里面**——不是追加到文档末尾。

    **这是本轮真正的病灶，而且它是隐形的。** 第一版修复（以及它之前那个只提
    `<a>` 开标签的原版）都是 `html + extra`，直接拼在 `</html>` 之后。两者的
    命运却完全不同：

    | 下游取什么 | 用的方法 | 尾拼接还能不能拿到 |
    |---|---|---|
    | 附件链接 | `soup.find_all("a")`——**整棵树** | **能** |
    | 正文 | `soup.find("body")`——**只在 body 里** | **不能** |

    所以「附件通路一直好、正文通路一直空」——**同一份拼接结果，两套选择器给出的
    答案相反**。BeautifulSoup（`html.parser`）确实会把尾部内容解析出来
    （`soup.get_text()` 看得见），但它落在 `<html>` **外面**，`soup.body` 里
    没有。零请求复现：

    ```
    >>> s = BeautifulSoup('<html><body><p>壳</p></body></html>' + '<p>正文</p>', 'html.parser')
    >>> s.get_text()        '壳正文'
    >>> s.body.get_text()   '壳'          ← 基类要的是这个
    ```

    **这个坑差点骗过验收**：单元测试里写的是 `BeautifulSoup(…).get_text()`，
    拿到的是**整棵树**，于是测试全绿而生产出 0 字。口径必须与产出函数一致——
    与 §2.5.53「核某函数产出多少字，唯一可信的比法是把那个函数再跑一遍」是同
    一条教训，这里是它的第二个变体：**测试与生产用不同的选择器，等于没测。**

    没有 `</body>` 时退回尾拼接（bs4 会自己补 body，行为与旧版一致）。
    """
    if not extra:
        return html
    m = re.search(r"</body\s*>", html, re.I)
    if m is None:
        return html + extra
    return html[:m.start()] + extra + html[m.start():]


class JxEMCollector(TrsCmsCollector):
    """江西省应急管理厅事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="jx_em",
            authority_name="江西省应急管理厅",
            base_url="https://yjglt.jiangxi.gov.cn",
            list_url="https://yjglt.jiangxi.gov.cn/jxsyjglt/col/251210dxsgal/",
            list_page_pattern="",
            # **这个 pattern 现在是失效的**（模块 docstring 第二层）：列表页
            # 已是 SPA 壳，全页没有一个可匹配的 href。JSON 通路 2026-10-05 做
            # 好之后，这个字段的处置是**保留**——它不再参与解析（`_parse_list`
            # 完全接管），只作为「本源老形态长什么样」的源码内记录。删掉它，
            # 这段历史就跟着没了。
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
            # 站点 TLS 太老，不放开老式重协商就握手失败（判据见模块 docstring）
            allow_legacy_renegotiation=True,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "jx_em"))

    # ------------------------------------------------------------------
    # 第二层：列表走 JSON 接口（见模块 docstring）
    # ------------------------------------------------------------------

    def _discover_pages(self) -> list[str]:
        """返回列表数据的地址——**本源上它不是网页，是 JSON 接口**。

        基类这一层的语义是「列表页 URL 的列表」，`collect()` 会逐个 `_fetch`
        再把结果交给 `_parse_list`。这里借用同一条通路，只是喂进去的是 JSON。

        恒返回一个地址、**不探测分页**：接口自己就不提供翻页（六种 GET 参数
        加 POST、路径后缀实测全无效），探也是白探。顺带一提，接口声明
        `articleCount = 49` 却只吐 15 条——这个差额详见 docstring 第二层。
        """
        return [_LIST_API]

    def _parse_list(self, html: str, page_url: str = "") -> list[dict]:
        """解析列表——**入参 `html` 实际上是 JSON 响应体**。

        保留基类的参数名与顺序，是为了让签名一致；真正的差别在函数体第一行。

        `urls` 字段是**双重编码的 JSON 字符串**（docstring 第二层记着这个坑）：
        直接 `item["urls"]["pc"]` 会拿一个 `str` 去下标——拼出来的 URL 带引号，
        `httpx` 抛 `InvalidURL: Invalid port: '"'`。所以先判 `isinstance(…, str)`
        再反解，**不能假定它一定是 dict**。
        """
        try:
            items = json.loads(html)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[{self.cfg.source_name}] 列表接口没返回 JSON，"
                           f"前 80 字符：{str(html)[:80]!r}")
            return []
        if not isinstance(items, list):
            return []

        base = page_url or self.cfg.list_url
        records: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            title = re.sub(r"\s+", "", it.get("title") or "")
            urls = it.get("urls")
            if isinstance(urls, str):
                try:
                    urls = json.loads(urls)
                except json.JSONDecodeError:
                    urls = None
            href = urls.get("pc") if isinstance(urls, dict) else None
            if not title or not href:
                continue
            records.append({
                "title": title,
                "url": _absolute(base, href),
                # `pubDate` 形如 "2026-07-07 14:59"，只要日期那半截
                "date": (it.get("pubDate") or "")[:10],
            })
        return records

    # ------------------------------------------------------------------
    # 第三层：正文在 JS 字面量数据岛里（见模块 docstring）
    # ------------------------------------------------------------------

    def _crawl_detail(self, url: str,
                      html: Optional[str] = None) -> Optional[dict]:
        """在把页面交给基类之前，先把数据岛里的正文 HTML 补进 DOM。

        **只补正文、不改别的**：数据岛的 content 有两种形态（见
        `_island_content_html` 的 docstring）——附件链接型与 HTML 正文型。
        **整段交出去两种都覆盖**：前者的 `<a>` 照样命中基类的附件选择器，走
        `_absolute` 解析相对路径、`_download_and_extract_pdf` 提出全文；后者
        的文字第一次进得了正文。

        **必须插进 `<body>` 里**（`_inject_into_body`）——基类的正文走
        `soup.find("body")`，而附件走 `soup.find_all("a")`（整棵树）；追加到
        `</html>` 之后，前者拿不到、后者拿得到。这个差别是本轮第一版修复
        「改完还是 0 字」的全部原因，详见 `_inject_into_body` 的 docstring。

        **这是覆盖在子类而不是加进基类**（§2.5.46）：同位形态实测只有本源一家，
        收益面 1 家而基类改动要经过全部 13 个 TRS 源的页面，风险面差 13 倍。
        """
        raw = html if html is not None else self._fetch(url)
        if not raw:
            return None
        return super()._crawl_detail(
            url, _inject_into_body(raw, _island_content_html(raw)))


def crawl_jx_em(delay: float = 1.0) -> list:
    return JxEMCollector(delay=delay).collect()
