"""吉林省应急管理厅 事故调查报告采集器。

目标栏目: 吉林省应急管理厅「安全生产事故调查报告」
（`https://xxgk.jl.gov.cn/zsjg/aqjg/xxgkmlqy/` 的子栏目 `5359`）
TRS CMS + 吉林省政府信息公开平台, 基于 TrsCmsCollector 通用基类。

## 本源有**两层**故障（2026-10-05 复查，两层都已修）

`jx_em` 的 docstring 里早有这一句预测：「同一个现象（表上显示「空」）在本源
是**三层**，在 `jl_em` 是域名笔误 + 列表 JS 渲染两层」。2026-10-05 实测
**证实了这个预测**——正好两层，且详情层不用改。

### 第一层：域名笔误——`yjglt.jl.gov.cn` 不存在（**已修**）

原配置 `base_url` 与 `list_url` 都写 `https://yjglt.jl.gov.cn`。实测该域名
**DNS 根本解析不出来**（各省应急厅是 `yjglt.<省>.gov.cn` 的规律——`jx_em`
就是 `yjglt.jiangxi.gov.cn`——但吉林没有这个二级域名）。真实站点是
**`xxgk.jl.gov.cn`**（吉林省政府信息公开专栏，省应急管理厅是其一个厅局子站）。

这一层技术上毫无含量，但**它的取证过程值得记一句**：同一个域名在仓库里出现
三处，`construction_agent/collector/scripts/run_collect.py` 里写的是
**`xxgk.jl.gov.cn`（对的）**，而 `jl_em.py` 与 `registry.py` 里写的是
`yjglt.jl.gov.cn`（错的）——**一处理正确、两处笔误**。健康检查表上显示的
URL 取的正是 `registry.py` 那份，所以从表上看只能是「没连上」。

「没连上」这四个字同时覆盖域名不存在、TLS 谈不成、403/412、超时四种情况——
**一格指标压掉多种故障**，这是本项目反复出现的形态（`hub_em` 的 412 挑战页
在第一轮诊断里也被记成过「形态未知」）。

### 第二层：列表页是 JS 渲染的壳（**已修**）

域名修对之后，栏目页 GET 得到 200 / 52,867 B，但 `<ul id="result-body">`
**内层 0 字符**——列表由 JS 填。数据在 JSONP 接口上：

```
GET https://infogate.jl.gov.cn/govsearch/jsonp/zf_jd_list.jsp
    ?page=1&lb=140459&channelId=140459&subcat=5359&callback=result
 → 200 / 18,808 B / num=5 / nPage=1 / 每页 16 条
```

**三个参数都得对，而它们的出处分散在三处**（本轮最花时间的地方）：

| 参数 | 值 | 出处 |
|---|---|---|
| `lb` | `140459` | 栏目页内联脚本：`var channelId = "140459"; var lb = "140459";` |
| `channelId` | `140459` | 同上——**它不是子栏目 id** |
| `subcat` | `5359` | 页面 `div[name=trs_js_subcat][trs_js_subcatId="5359"]`，即子栏目「安全生产事故调查报告」 |

**`channelId` 与 `subcat` 是两个不同的东西，混起来会得到 `num=0`。** 实测：
只把 `channelId` 换成 `5359`（无 `subcat`）→ 200 / **100 B** / `num=0`——
那个 100 字节的空 JSONP 长得跟「这个 id 不存在」一模一样，很容易就此收工。
分岔逻辑在 `govinfo_common20210806.js` 的
`search(page, tiaojian, value, flvalue)`：

```js
} else if (tiaojian == 2) {           // 2 = 按主题查询
    searchForm.append(chennalInput);  // name=channelId  value=140459
    searchForm.append(subcatInput);   // name=subcat     value=5359
}
```

而 `ticaiSearch(5359, 2)`——点子栏目时走的那个函数——第二个参数 `2` 正好
就是 `tiaojian`。三处线索对上，通路才成立。

`ticaiSearch` 本身只是把当前页 URL 加上 `?stit=5359&num=2` 再跳转
（`govinfo_common20210806.js:295-302`）；实测那个 URL **返回的字节数与原页
完全相同（52,867 B）**——`stit` 是给 JS 读的，服务端不理会。所以「照抄带
参数的那个 URL」这条路是死的，必须走接口。

接口返回的字段有个**形态特征值得记**：一条记录吐 ~90 个键，其中
`jd_*` / `tj_*` / `xwfbh_*` / `sp_*` / `xgbd_*` / `dw_*` 六大族全是空串，
真正有值的只有 `puburl` / `title` / `MetaDataId` / `efectdate` / `effect` /
`tip`。原因是这个 `zf_jd_list.jsp` 是**全省所有厅局所有栏目共用的一张宽表**
（同一个 `infogate.jl.gov.cn` 域），键是各种挂载关系的并集。

### 详情层**不用改**（与 `jx_em` 的关键区别）

`jx_em` 的第三层是「正文藏在 JS 字面量数据岛里」，得给 `_unwrap_data_islands`
加一种形态。**本源没有这一层**：附件链接直接写在详情页 HTML 里——

```html
<a href="//xxgk.jl.gov.cn/PDFfile/202211/8639621.pdf">
<a href="//xxgk.jl.gov.cn/PDFfile/202211/8639621.doc">
```

无数据岛（`grep 'const col\\d+_\\d+ ='` 零命中）。基类 `_crawl_detail` 的附件
通路原样就能吃下：`detail_pdf_selector`（默认 `a[href]`）→ `_absolute` 的
`urljoin`（docstring 里明写着正确处理 `//host/x` 协议相对 URL）→
`_download_and_extract_pdf`。

并且 `collect()` 的合并规则是 `combined_text = pdf_text or detail_text`
（`trs_cms_base.py:484`）——**PDF 正文优先**。这一条对本源是必需的：详情页
`body` 只有 489 字，全是「索引号 / 分类 / 发文机关 / 成文日期 / 发文字号」
这类元数据，**没有一句正文**（实测「事故」×5、「调查」×5，都出自标题与
栏目名）。若合并顺序反过来，489 字的元数据会盖掉 PDF 里的真正文。

## 修法与实测（2026-10-05）

两层都由**子类覆盖**，不动基类（与 `jx_em` 同一处置，理由见 §2.5.46：
同位形态实测只有这两家，而改基类要经过全部 13 个 TRS 源的页面）：

| 层 | 改哪里 | 做什么 |
|---|---|---|
| 域名 | `TrsSiteConfig` | `yjglt.jl.gov.cn` → `xxgk.jl.gov.cn` |
| 列表 | `_discover_pages` | 返回 JSONP 接口地址；从第 1 页的 `nPage` 定页数 |
| 列表 | `_parse_list` | 剥 JSONP、取 `data`、`puburl` 升级为 https |

**保真度**：本源 `list_link_pattern` 保留——它不再参与解析（`_parse_list`
完全接管），只作为「本源老形态长什么样」的源码内记录，处置同 `jx_em`。

**实测（2026-10-05）**：接口 `num=5`，5 条全部解析出（标题、URL、日期）；
其中 3 条是事故调查报告本体、2 条是「防范和整改措施落实情况评估报告」。
详见 `docs/web/01_在线采集.md` §2.5.53。
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Optional

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── 列表接口（模块 docstring 第二层）────────────────────────────────
#
# 注意域名是 `infogate.jl.gov.cn`，**与详情页的 `xxgk.jl.gov.cn` 不是同一个**。
# 接口是全省共用的检索服务，站点是厅局子站，两者分属不同子系统。
_LIST_API = "https://infogate.jl.gov.cn/govsearch/jsonp/zf_jd_list.jsp"

# `lb` 与 `channelId` 取值相同，但**语义不同**，故分成两个常量：`lb` 是接口
# 侧的数据分区，`channelId` 是站点侧的栏目。合并成一个会让将来调试时看不出
# 该改哪一个。
_LB = "140459"
_CHANNEL_ID = "140459"

# 子栏目 id → 「安全生产事故调查报告」。取值来自页面
# `div[name=trs_js_subcat]` 的 `trs_js_subcatId` 属性，共 41 个里接口承认
# 存在的 11 个之一（另外几个是财政预决算 / 地方法规和规章 / 应急管理公告…，
# 与本项目无关）。
_SUBCAT_ACCIDENT_REPORT = "5359"

# JSONP 外壳：`result({...});`。**回调名固定写 `result`**（接口支持 `callback`
# 参数，但站点 JS 写死 `jsonpCallback: "result"`），所以这里不生成随机名。
_JSONP = re.compile(r"^\s*[\w$.]+\s*\((.*)\)\s*;?\s*$", re.S)

# **只把本省的 `xxgk.jl.gov.cn` 升级成 https**，不做全局 http→https 替换。
# 实测该域名支持 https（详情页 200 / 60,711 B），而接口返回的 `puburl` 全写成
# 明文 `http://`。写死白名单而不是「一律升级」，是因为列表里将来可能混进别的
# 域名（这个接口是全省共用的），而**对不支持的域名强行 https 会得到握手失败**
# ——那会把「这个站本来就是 http」变成一个采集故障。
_HTTPS_HOSTS = frozenset({"xxgk.jl.gov.cn"})


def _prefer_https(href: str) -> str:
    """把白名单域名的 `http://` 前缀换掉；其余原样返回。"""
    m = re.match(r"^http://([^/]+)", href)
    if m and m.group(1).lower() in _HTTPS_HOSTS:
        return "https://" + href[len("http://"):]
    return href


def _loads_jsonp(raw: str) -> Optional[Any]:
    """剥掉 JSONP 外壳并反解。**失败返回 `None`**（与「解析出空数据」区分）。

    区分这两件事是必要的：`None` 意味着接口换形态了、要改解析；而
    `{"num": 0, "data": []}` 是「这个栏目真的没有内容」。两者在健康检查表上
    都显示成「解析空」，但处置完全相反。
    """
    if not raw:
        return None
    m = _JSONP.match(raw)
    try:
        return json.loads(m.group(1) if m else raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _norm_date(raw: Any) -> str:
    """接口日期 → `YYYY-MM-DD`。

    `efectdate` 实测形如 `2022.11.21 00:00:00`（**点分隔**，且带时间尾巴），
    与 `jx_em` 的 `pubDate`（`2026-07-07 14:59`）不是一种写法。基类
    `_date_from_url` 抠 URL 得到的也是 `YYYY-MM-DD`，故这里统一成同一格式，
    免得同一个字段在两个源上有两种形态。
    """
    s = str(raw or "").strip()
    m = re.match(r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if not m:
        return ""
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _api_url(page: int) -> str:
    return (f"{_LIST_API}?page={page}&lb={_LB}"
            f"&channelId={_CHANNEL_ID}&subcat={_SUBCAT_ACCIDENT_REPORT}"
            f"&callback=result")


class JlEMCollector(TrsCmsCollector):
    """吉林省应急管理厅事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="jl_em",
            authority_name="吉林省应急管理厅",
            # 域名笔误的修法（docstring 第一层）。**三处同源配置要一起改**：
            # 这里、`collector/registry.py` 的 URL 字段、`scripts/run_collect.py`
            # 的描述（后者本来就写对了）。
            base_url="https://xxgk.jl.gov.cn",
            list_url="https://xxgk.jl.gov.cn/zsjg/aqjg/xxgkmlqy/",
            list_page_pattern="",
            # 失效字段，保留作形态记录（同 `jx_em` 的处置）。
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "jl_em"))

    # ------------------------------------------------------------------
    # 第二层：列表走 JSONP 接口（见模块 docstring）
    # ------------------------------------------------------------------

    def _discover_pages(self) -> list[str]:
        """发现列表页——**本源上它们不是网页，是同一个接口的不同 `page`**。

        与 `jx_em` 不同，**本源要翻页**：接口给了 `page` 参数与 `nPage`
        总页数（`jx_em` 那个接口六种分页参数全无效）。所以这一层不是「恒返回
        一个地址」，而是先打第 1 页拿到 `nPage`，再算出全部页地址。

        拿 `nPage` 而不是自己 `ceil(num / 每页条数)`：接口自己说的总页数最准，
        而「每页 16 条」是观察值、不是契约。`nPage` 缺失或非数字时才退化到
        按第一页实际条数推算。

        第 1 页的响应**存进 `_page_cache`**，`collect()` 随后对同一 URL
        `_fetch` 时直接取用（`trs_cms_base.py:1016`）——不存的话这一页会被
        打两次。基类默认实现也用同一个机制。

        **本源的 `_SUBCAT_ACCIDENT_REPORT` 只有 5 条（1 页）**，所以下面
        翻页那几行当前走不到第二页。保留它是因为换 `subcat` 就会用到——
        而换 subcat 是极可能的操作（本文件顶部就记着另外 10 个栏目的 id）。
        **但真要换成 `channelId=140459`（1,521 条 / 96 页）时，需要先加上限**：
        这里没有任何封顶，96 页会被一路爬完。
        """
        first_url = _api_url(1)
        first = self._fetch(first_url)
        if not first:
            # 第 1 页都取不到。`_fetch` 已经把这一笔记进 `_fetch_failed`，
            # 这里返回空列表让 `collect()` 走「0 个列表页」路径，不再重复计数。
            return []

        payload = _loads_jsonp(first)
        if not isinstance(payload, dict):
            logger.warning(
                f"[{self.cfg.source_name}] 列表接口没返回可解析的 JSONP，"
                f"前 80 字符：{first[:80]!r}")
            return [first_url]

        rows = payload.get("data") or []
        if not rows:
            # 空栏目。仍然把地址交回去，让 `_parse_list` 自己给出「0 条」
            # ——**不在这里替它下结论**，否则「解析出 0 条」这件事就没有
            # 任何一行代码看得见。
            return [first_url]

        n_page = payload.get("nPage")
        try:
            pages = int(n_page)
        except (TypeError, ValueError):
            pages = math.ceil(int(payload.get("num") or len(rows)) / len(rows))
        pages = max(1, pages)

        self._page_cache[first_url] = first
        if pages > 1:
            logger.info(f"[{self.cfg.source_name}] 接口 num={payload.get('num')} "
                        f"/ 每页 {len(rows)} 条 → {pages} 页")
        return [_api_url(n) for n in range(1, pages + 1)]

    def _parse_list(self, html: str, page_url: str = "") -> list[dict]:
        """解析列表——**入参 `html` 实际上是 JSONP 响应体**。

        保留基类的参数名与顺序只为签名一致，真正的差别在函数体第一行。

        四个字段的取值都有讲究：

        - `title`：去掉所有空白。TRS 标题里常见全角空格与换行（`jx_em` 同一处理）。
        - `puburl`：**可能是 `javascript:void(0)`**——站点 JS 对这种情况会改指向
          「依申请公开」表单（`govinfo_common20210806.js:138-139`），那不是事故
          详情页。这里直接跳过，**不猜**。
        - `url`：`puburl` 是完整绝对地址（含 scheme），不需要 `_absolute` 拼；
          只把白名单域名的 http 升级成 https（见 `_prefer_https`）。
        - `date`：`efectdate` 优先，为空则退 `tip.dates`。**两个都取**不是冗余
          ——这个接口一条记录 ~90 个键、绝大多数为空（docstring 第二层记着），
          「某个字段缺失」是常态而非异常。
        """
        payload = _loads_jsonp(html)
        if not isinstance(payload, dict):
            logger.warning(f"[{self.cfg.source_name}] 列表接口没返回可解析的 "
                           f"JSONP，前 80 字符：{str(html)[:80]!r}")
            return []

        records: list[dict] = []
        for it in payload.get("data") or []:
            if not isinstance(it, dict):
                continue
            title = re.sub(r"\s+", "", it.get("title") or "")
            href = (it.get("puburl") or "").strip()
            if not title or not href or href.lower().startswith("javascript:"):
                continue
            tip = it.get("tip")
            date = _norm_date(it.get("efectdate"))
            if not date and isinstance(tip, dict):
                date = _norm_date(tip.get("dates"))
            records.append({
                "title": title,
                "url": _prefer_https(href),
                "date": date,
            })
        return records


def crawl_jl_em(delay: float = 1.0) -> list:
    return JlEMCollector(delay=delay).collect()
