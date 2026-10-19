"""江西省应急管理厅 (yjglt.jiangxi.gov.cn) 事故调查报告采集器。

目标: https://yjglt.jiangxi.gov.cn/jxsyjglt/col/251210dxsgal/（栏目「典型事故案例」）
TRS CMS, 基于 TrsCmsCollector 通用基类。

## 本源有**三层**故障，本轮只修好了第一层（2026-10-01 第三十七轮）

### 第一层：TLS 老式重协商（**已修**）

本源在健康检查表上一直是「列表 0 条」，原因不在解析、不在选择器，**在 TCP
之上就断了**：服务器仍在用 RFC 5746 之前的老式重协商，OpenSSL 3.x 默认禁用
（`UNSAFE_LEGACY_RENEGOTIATION_DISABLED`），握手直接谈不成。

放开 `OP_LEGACY_SERVER_CONNECT` 后 TLSv1.2 谈成、HTTP 200、271,906 字节
——站点本身完全健康，没有 412、没有 JS 挑战。所以这里显式打开该开关；
判据与安全代价见 `trs_cms_base.build_ssl_verify`。

### 第二层：列表页已不是 HTML（**未修**）

TLS 修好后 `list_link_pattern` 命中 **0 条**。实测原因：原 `list_url` 返回的
是一个 SPA 壳——全页只有 7 个 `<a>`，**全部是 `javascript:void(0)`**。
真正的列表数据在 JSON 接口上：

```
GET /jxsyjglt/251210dxsgal/251210dxsgal-articleList.json   → 200 / 15 条
```

每条给 `title` / `pubDate` / `id` / `urls.pc`（详情页真址形如
`/jxsyjglt/col/251210dxsgal/content/content_<id>.html`）。**这个形态与老版
TRS 的 `/202609/t20260917_9714722.html` 完全不同**，基类现在没有 JSON 通路。

**接口只吐 15 条，而栏目元数据说 `articleCount = 49`**：GET 的六种分页参数
（`page` / `pageNum` / `pageIndex` / `pageSize` / `size` / `limit`）、POST、
路径式后缀（`/index_2.json` 等）**全部无效或 404**——接口不提供翻页。

### 第三层：详情页是 Handlebars 模板壳（**未修**）

`content_<id>.html` 返回 200 / 28,293 字节，但 `<body>` 里躺着的是**模板源码**：

```
{{#each data}}{{#with this}}
{{title}}
发布日期：{{formatDate pubDate 'YYYY'}}-…
{{{content.content}}}          ← 正文占位符，等前端 JS 注入
```

所以 `_crawl_detail` 对四条不同的事故各提到**完全相同的 467 字**——提的是
模板文字本身。想走通静态 HTML 通路，得先找到喂给模板的那个数据接口；
5 个候选（`content_<id>.json` 等）**全部 404**，`relation` 接口返回 `[]`。

### 结论

**修好第一层不等于能采到**：这个站已经整体迁到「JSON 数据 + 前端渲染」的
新一代 TRS。三步里只完成了第一步，`_total_listed` 仍然是 0——**与修之前
逐格相同**，区别只在于 `_fetch_failed` 从「握手失败」变成了「列表 0 条」。

同一个现象（表上显示「空」）在本源是**三层**，在 `jl_em` 是域名笔误 + 列表
JS 渲染两层，在 `hub_em` 是 JS 挑战型 WAF 一层——这正是 `_fetch_failed`
那个计数器存在的理由，也是「一格指标压掉多种故障」这件事的样本。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings


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
            # 已是 SPA 壳，全页没有一个可匹配的 href。留着它是为了让「它本来
            # 该怎么匹配」这件事留在源码里——等 JSON 通路做好后，这个字段是
            # 留是删有了明确依据，而不是被人忘掉。
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
            # 站点 TLS 太老，不放开老式重协商就握手失败（判据见模块 docstring）
            allow_legacy_renegotiation=True,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "jx_em"))


def crawl_jx_em(delay: float = 1.0) -> list:
    return JxEMCollector(delay=delay).collect()
