"""TRS CMS 通用采集器基类。

中国各省应急管理厅大多使用 TRS CMS 或类似政府CMS发布事故调查报告。
这些网站共享相同的爬取模式：

1. 分页列表页 → 解析文章链接（标题、URL、日期）
2. 标题关键词初筛 → 过滤非建筑施工事故
3. 逐条爬取详情页 → 提取正文/PDF附件
4. classify_v2 二级文本分类 → 高置信度自动分类 / 低置信度入审核队列
5. 存储到 RawAccidentStore

本模块提供 TrsCmsCollector(BaseCollector)，封装上述通用逻辑。
各省只需提供 URL 和 CSS 选择器配置即可。
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.collector.classifier import classify_v2, is_construction_candidate
from construction_agent.collector.pdf_text import extract_pdf_text
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)


# ── 默认建筑施工关键词（可被各省覆盖）──
DEFAULT_CONSTRUCTION_KW = [
    "建筑", "建设", "施工", "坍塌", "倒塌", "工地", "坠落", "工程",
    "地铁", "电梯", "拆除", "基坑", "模板", "脚手架", "项目", "标段",
    "塔吊", "高处", "触电", "起重", "物体打击", "火灾", "爆炸",
]


@dataclass
class TrsSiteConfig:
    """TRS CMS 站点配置。

    所有字段均可被具体站点覆盖。
    """

    # ── 必填 ──
    source_name: str = ""          # 采集器名称（如 "beijing_em"）
    authority_name: str = ""       # 发布机构（如 "北京市应急管理局"）

    # ── URL 配置 ──
    base_url: str = ""             # 站点基础 URL（如 "https://yjglj.beijing.gov.cn"）
    list_url: str = ""             # 列表页 URL（第一页）
    list_page_pattern: str = ""    # 分页 URL 模式（如 "index_{n}.html"，None 表示无分页）
    list_page_start: int = 1       # 分页起始页码
    list_page_max_probe: int = 20  # 最大探测页数

    # ── 解析选择器 ──
    list_item_selector: str = ""          # 列表项 CSS 选择器（如 "li a[href]"）
    list_link_pattern: str = ""           # 链接 URL 正则模式（如 r'/art/\d{8}/'）
    list_title_attr: str = "text"         # 标题提取方式: "text" / "title" / "aria-label"
    list_date_pattern: str = ""           # 日期提取正则（从 URL 或文本中提取）

    # ── 详情页配置 ──
    detail_title_selector: str = "title"  # 详情标题提取: "title"(<title>标签) / "h1" / CSS选择器
    detail_text_selector: str = "body"    # 详情正文容器: "body" / "article" / CSS选择器
    detail_pdf_selector: str = "a[href$='.pdf']"  # PDF 附件链接选择器

    # ── 筛选配置 ──
    construction_keywords: list[str] = field(default_factory=lambda: DEFAULT_CONSTRUCTION_KW)
    enable_classify_v2: bool = True       # 是否启用 classify_v2 二级文本分类
    min_text_length: int = 30             # 最小正文长度（低于此值入审核队列）

    # ── 请求配置 ──
    delay: float = 1.0                    # 请求间隔（秒）
    timeout: int = 30                     # HTTP 超时（秒）
    max_retries: int = 3                  # 最大重试次数
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )


# ── PDF 文本提取工具函数 ──
#
# 原实现在这里，2026-10-01 收敛到 `construction_agent.collector.pdf_text`：
# 这份函数被复制了三份（本文件 + `nmg_em` + `hebei_em`），而 `beijing_em` /
# `ln_em` / `henan_em` 三个模块调它却没有定义。收敛成一份，见那个模块的
# docstring。


# ═══════════════════════════════════════════════════════════════════
# TRS CMS 通用采集器
# ═══════════════════════════════════════════════════════════════════

def _unwrap_data_islands(html: str) -> str:
    """把 `<script type="text/xml">` 数据岛里的列表条目提出来，让 BeautifulSoup 看得见。

    **这是 `js_em` 一条都抓不到的原因**（2026-10-01 动态试跑查出）。
    那个站的列表页长这样：

        <div id="397082"><script type="text/xml"><datastore>
        <recordset><record><![CDATA[
        <li class="cf"><a title="…" href="/art/2026/4/22/art_81395_11761062.html">…</a>…
        ]]> </record> … </recordset></script></div>

    两道坎叠在一起，缺一不可：
    1. `html.parser` 对 `<script>` 的内容**一律当文本**（script 是 CDATA 元素），
       里面的 `<a>` 不会成为标签；
    2. 那条 `CDATA` 声明也不被识别，整段仍是文本。

    于是那一页的 45 条链接一条都进不了 DOM，`find_all("a")` 只能看见 16 个导航
    链接——而链接正则只在 `<a href>` 上匹配，结果恒为 0。**正则、URL、状态码
    全是好的**，问题在解析之前。

    只对真含这种数据岛的页面动手：正则匹配不上时原样返回，其余 11 个 TRS 源
    的页面结构不受影响。

    正则放宽了三处（`js_em` 实测用的是双引号、无 charset 后缀，另外两处属于
    **防御性**，没有实测样本）：引号单双皆可、`type` 值允许带参数后缀、
    `type` 关键字大小写不限。放宽的理由是这三处一旦不匹配，表现与「这个源
    没数据」完全一样——又是沉默失效，宁可多认几种写法。
    """
    def _one(m: "re.Match[str]") -> str:
        return re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.S)

    return re.sub(r"""<script[^>]*type=["']text/xml[^"']*["'][^>]*>(.*?)</script>""",
                  _one, html, flags=re.S | re.I)


def _absolute(page_url: str, href: str) -> str:
    """把列表页里的 href 还原成绝对 URL。

    **必须拿列表页自己的地址去 `urljoin`，不能拿 `base_url` 拼。**
    TRS 的列表页常把详情链接写成相对当前目录的形式——`./202609/t20260918_…html`。
    原先写的是 `base_url.rstrip("/") + "/" + href.lstrip("/")`，而 `lstrip("/")`
    **不去掉开头的 `./`**，拼出来是 `https://yjj.cq.gov.cn/./202609/…`，
    服务器对 `/./` 一律 404。

    于是一个源会呈现成**最容易被放过的那种样子**：列表页正常、条目正常解析出
    15 条、每条都有标题，只是详情页全 404、正文一个字都没有。健康检查若只看
    「抓到几条」，这四个源全部判绿。实测三种拼法（2026-10-01，`cq_em` 第一条）：

    | 拼法 | 结果 |
    |---|---|
    | 旧拼法（含 `/./`） | **404** |
    | `urljoin(列表页, href)` | **200，49,969 字符** |
    | `base_url` + href 去掉 `./` | **404** |

    第三行说明它也**不是相对于站点根**：`./202609/…` 相对的是列表页所在目录
    `/zwgk_230/…/dcbg/`，所以只能拿列表页 URL 去 join。顺带地，`urljoin` 还
    正确对待三种旧拼法处理不了的写法：`//host/x`（协议相对）、`../x`（上一级）、
    已是绝对 URL 的 href。
    """
    return urljoin(page_url, href)


def _date_from_url(href: str) -> str:
    """从详情页 URL 的路径里抠出日期，返回 `YYYY-MM-DD`；抠不到返回空串。

    两种写法都要认：

    - 斜杠分段、**月/日不补零**——`/art/2019/4/18/…`（TRS 常见）、
      `/scyjt/dcbg/2023/8/14/…`
    - 连续八位——`/20230101/`

    原先写的是 `(\\d{4})[/-](\\d{2})[/-](\\d{2})`，`\\d{2}` 要求月日补零，
    于是上面两种主流写法**一条都匹配不上**，列表页日期字段恒为空串。
    空值本身不报错，看起来就像「这个站列表页不写日期」——**又一个恒空的判据**。
    2026-10-01 补数据岛测试时撞出来的：URL 里明摆着 `2019/4/18`，`date` 却是空串。

    连续八位那种额外加 `(?<!\\d)` / `(?!\\d)`，免得从 `art_81395_8732508`
    这类长数字串中间切出一段假的八位数。抠出来的三组数还做范围校验，
    `1234/56/78` 这种不返回。
    """
    for pattern in (
        r"(?<!\d)(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?!\d)",
        r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)",
    ):
        m = re.search(pattern, href)
        if not m:
            continue
        year, month, day = (int(g) for g in m.groups())
        if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


class TrsCmsCollector(BaseCollector):
    """TRS CMS 通用采集器基类。

    封装了政府 CMS 网站的通用爬取流程。子类只需提供 site_config 即可。

    使用方式:
        config = TrsSiteConfig(
            source_name="beijing_em",
            authority_name="北京市应急管理局",
            base_url="https://yjglj.beijing.gov.cn",
            list_url="https://yjglj.beijing.gov.cn/art/",
            ...
        )
        collector = TrsCmsCollector(config)
        results = collector.collect()  # → list[RawAccidentText]
    """

    def __init__(self, site_config: TrsSiteConfig, data_dir: Optional[Path] = None):
        self.cfg = site_config
        self.data_dir = data_dir or (settings.DATA_DIR / site_config.source_name)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._client = httpx.Client(
            timeout=self.cfg.timeout,
            follow_redirects=True,
            headers={"User-Agent": self.cfg.user_agent},
        )
        self.raw_store = RawAccidentStore()
        # `_discover_pages` 探测分页时已经 GET 到正文，存这里供 `_fetch` 复用，
        # 免得列表页各请求两遍（见 `_fetch`）。
        self._page_cache: dict[str, str] = {}

        # 统计计数器
        self._total_listed = 0
        self._filtered_title = 0
        self._filtered_classify = 0
        self._collected = 0
        self._review = 0

    # ── BaseCollector 接口 ──

    def source_name(self) -> str:
        return self.cfg.source_name

    def collect(self) -> list[RawAccidentText]:
        """执行完整采集流程，返回 RawAccidentText 列表。"""
        t_start = time.time()
        logger.info(f"[{self.cfg.source_name}] 开始采集: {self.cfg.list_url}")

        # 1. 发现所有列表页
        pages = self._discover_pages()
        logger.info(f"[{self.cfg.source_name}] 发现 {len(pages)} 个列表页")

        # 2. 解析列表，提取所有文章链接
        all_links: list[dict] = []
        for page_url in pages:
            html = self._fetch(page_url)
            if html:
                # 传 `page_url` 而不是让 `_parse_list` 自己去猜：相对链接
                # （`./202609/…`）要对着**这一页**的地址解析，分页时页页不同。
                all_links.extend(self._parse_list(html, page_url))

        self._total_listed = len(all_links)
        logger.info(f"[{self.cfg.source_name}] 列表共 {self._total_listed} 篇文章")

        # 3. 逐条爬取
        results: list[RawAccidentText] = []
        for i, link in enumerate(all_links):
            title = link["title"]
            url = link["url"]
            list_date = link.get("date", "")

            # 一级筛选：标题关键词
            is_candidate, reason = is_construction_candidate(
                title, keywords=self.cfg.construction_keywords
            )
            if not is_candidate:
                self._filtered_title += 1
                continue

            logger.info(f"[{self.cfg.source_name}] 候选({i+1}/{self._total_listed}): "
                        f"[{title[:50]}] - {reason}")

            time.sleep(self.cfg.delay)

            # 爬取详情页
            detail = self._crawl_detail(url)
            detail_title = (detail or {}).get("title") or title
            detail_date = (detail or {}).get("date") or list_date
            detail_text = (detail or {}).get("text") or ""
            pdf_url = (detail or {}).get("pdf_url") or ""

            # 下载 PDF 并提取全文
            pdf_text = ""
            local_pdf_path = ""
            if pdf_url:
                pdf_text, local_pdf_path = self._download_and_extract_pdf(
                    pdf_url, detail_title, detail_date
                )

            # 合并正文
            combined_text = pdf_text or detail_text
            if not combined_text or len(combined_text) < self.cfg.min_text_length:
                # 无有效正文 → 入审核队列
                self._review += 1
                classification = "pending_review"
                class_reason = "无有效正文"
                class_confidence = "low"
                status = "review"
            elif self.cfg.enable_classify_v2:
                # 二级筛选：classify_v2 文本分类
                cr = classify_v2(detail_title, combined_text)
                if cr["is_construction"] and cr["confidence"] == "high":
                    classification = "auto_construction"
                    class_reason = cr["reason"]
                    class_confidence = "high"
                    status = "new"
                    self._collected += 1
                elif not cr["is_construction"] and cr["confidence"] == "high":
                    self._filtered_classify += 1
                    continue  # 高置信度非施工 → 跳过
                else:
                    classification = "pending_review"
                    class_reason = cr["reason"]
                    class_confidence = "low"
                    status = "review"
                    self._review += 1
            else:
                classification = "pending_review"
                class_reason = "未启用分类"
                class_confidence = "low"
                status = "review"
                self._review += 1

            # 构建 RawAccidentText
            raw = RawAccidentText(
                source=self.cfg.source_name,
                title=detail_title,
                raw_text=combined_text[:50000],
                source_url=url,
                fetch_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
                extracted_date=self._parse_date_str(detail_date),
                # 下面三个不是 `RawAccidentText` 的字段。dataclass 的 `__init__`
                # 不收未声明的关键字，多给一个就 `TypeError`。原先写成平铺的
                # 关键字参数，12 个 TRS 源里凡是走到这一步的全部当场崩掉
                # （2026-10-01 动态试跑：cq/gz/hain/sh/shaanxi/sx/tj 七个报
                # `unexpected keyword argument 'classification'`）。
                # 扩展数据本来就该走 `extra`，`stores/__init__.py` 也是按这几个
                # 键名从 extra 里读的。
                extra={
                    "classification": classification,
                    "classification_reason": class_reason,
                    "classification_confidence": class_confidence,
                },
            )
            # 附加信息通过 metadata 传递
            raw.raw_html = ""
            results.append(raw)

            # 周期性存储到 RawAccidentStore
            if len(results) % 5 == 0:
                self.raw_store.add_batch([
                    {"title": r.title, "content": r.raw_text,
                     "source_url": r.source_url, "source_name": r.source,
                     "publish_date": detail_date, "status": status,
                     "classification": classification,
                     "classification_reason": class_reason,
                     "classification_confidence": class_confidence,
                     "issuing_authority": self.cfg.authority_name}
                    for r in results[-5:]
                ])
                self.raw_store._save()

        # 最后一批写入
        self.raw_store._save()

        elapsed = time.time() - t_start
        logger.info(
            f"[{self.cfg.source_name}] 采集完成: "
            f"列表{self._total_listed} → 初筛过滤{self._filtered_title} → "
            f"分类过滤{self._filtered_classify} → "
            f"收录{self._collected} + 审核{self._review} "
            f"({elapsed:.1f}s)"
        )

        return results

    # ── 内部方法 ──

    def _discover_pages(self) -> list[str]:
        """发现所有分页 URL。

        **判据是 GET 的状态码，不是 HEAD 的。** `shaanxi_em` 整站拒 HEAD：
        第 2、3、4 页 HEAD 全 403，而 GET 全 200、每页 38,000 字符内容完整。
        用 HEAD 判「还有没有下一页」，这个源的结论是「只有一页」——
        **而且是静默的，看上去与「这个站确实只有一页」没有任何区别**。

        `list_page_start` 的语义是「`list_url` 对应的页码」，默认 1，
        所以从 `start + 1` 开始往后探。

        探测时把响应正文收进 `_page_cache`，`collect()` 随后对同一页
        `_fetch` 时直接取用，不重复发请求。
        """
        if not self.cfg.list_page_pattern:
            return [self.cfg.list_url]

        pages = [self.cfg.list_url]
        for n in range(self.cfg.list_page_start + 1, self.cfg.list_page_max_probe + 1):
            page_url = self._build_page_url(n)
            try:
                resp = self._client.get(page_url, timeout=15)
            except Exception as e:
                logger.warning(f"[{self.cfg.source_name}] 第 {n} 页探测失败，停止分页: {e}")
                break
            if resp.status_code != 200:
                logger.info(
                    f"[{self.cfg.source_name}] 第 {n} 页 HTTP {resp.status_code}，"
                    f"判定分页结束（共 {len(pages)} 页）"
                )
                break
            self._page_cache[page_url] = resp.text
            pages.append(page_url)
        return pages

    def _build_page_url(self, page_num: int) -> str:
        """根据分页模式构建第 `page_num` 页的 URL。子类可覆盖以定制分页逻辑。

        **一律 `urljoin`，不许字符串拼接。** 站点给的 `list_page_pattern` 有两种形状，
        而 `list_url` 本身也有两种形状，组合起来四种，拼接只对其中一种成立：

        | `list_page_pattern` | `list_url` | 拼接结果 | `urljoin` |
        |---|---|---|---|
        | `index_{n}.html` | `…/sgdc/index_1.html`（**文件**） | `…/index_1.html/index_2.html` ✗ | `…/sgdc/index_2.html` ✓ |
        | `index_{n}.html` | `…/sgdcbg/`（**目录**） | `…/sgdcbg/index_2.html` ✓ | 同上 ✓ |
        | `https://…/index_{n}.html` | 任意 | 取决于拼接 | 原样返回 ✓ |

        **曾经的写法的害处不在拼错，在于错得看不出来**：
        ```python
        if "{n}" in pattern:
            return pattern.format(n=page_num)   # ← `index_{n}.html` 命中这里
        if "index_" in pattern:                 # ← 这个分支从没执行过
            return f"{self.cfg.list_url.rstrip('/')}/{pattern.format(n=page_num)}"
        ```
        `index_{n}.html` **同时**含 `{n}` 和 `index_`，而第一个分支在前面，
        于是返回的是裸文件名 `index_2.html`——**没有协议头的相对路径**。
        `_discover_pages` 对它发 HEAD 必然抛 `UnsupportedProtocol`，
        落进那里的 `except Exception: break`，**静默只剩第一页**，
        返回「1 页」看起来与「这个站只有一页」完全一样。
        """
        return urljoin(self.cfg.list_url, self.cfg.list_page_pattern.format(n=page_num))

    def _parse_list(self, html: str, page_url: str = "") -> list[dict]:
        """解析列表页 HTML，提取文章链接。

        Args:
            html: 列表页 HTML。
            page_url: **这一页**的 URL。用来把相对链接还原成绝对 URL——
                列表页里的 href 可能是 `./202609/…` 这种相对当前目录的写法，
                只能对着列表页地址解析。省略时退回 `cfg.list_url`，
                对第一页是对的，对分页不对——**分页的循环必须传**。

        Returns:
            [{"title": str, "url": str, "date": str}, ...]
        """
        base = page_url or self.cfg.list_url
        html = _unwrap_data_islands(html)
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict] = []

        # 方法1: 使用配置的链接正则模式
        if self.cfg.list_link_pattern:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if re.search(self.cfg.list_link_pattern, href):
                    title = a_tag.get_text(strip=True)
                    title = re.sub(r"\s+", "", title)
                    if len(title) < 5:
                        continue
                    full_url = _absolute(base, href)
                    date = _date_from_url(href)
                    if title:
                        records.append({"title": title, "url": full_url, "date": date})
            return records

        # 方法2: 尝试匹配包含日期路径的链接
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if re.search(r"/\d{4,8}/", href):
                title = a_tag.get_text(strip=True)
                title = re.sub(r"\s+", "", title)
                if len(title) < 5:
                    continue
                full_url = _absolute(base, href)
                date = _date_from_url(href)
                if title:
                    records.append({"title": title, "url": full_url, "date": date})

        return records

    def _crawl_detail(self, url: str) -> Optional[dict]:
        """爬取详情页，提取标题、日期、正文和 PDF 链接。"""
        html = self._fetch(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        # 标题
        title = ""
        if self.cfg.detail_title_selector == "title":
            t_tag = soup.find("title")
            if t_tag:
                title = t_tag.get_text(strip=True)

        # 日期
        date = ""
        m = re.search(r"(\d{4}-\d{2}-\d{2})", html)
        if m:
            date = m.group(1)

        # PDF 链接
        # 同样要用 `_absolute` 对着**详情页自己的地址**解析。TRS 的附件链接也写作
        # `./P020240905567173081196.pdf`——旧拼法会把它接到站点根上，拼出
        # `https://yjt.shanxi.gov.cn/P020240905567173081196.pdf`，**整条路径都丢了**，
        # 下载必然 404。而 PDF 恰恰是省级源的主要载体：`sx_em` 的详情页正文
        # 一个字都不在 HTML 里，全在这个附件中。
        pdf_url = ""
        if self.cfg.detail_pdf_selector:
            for a_tag in soup.select(self.cfg.detail_pdf_selector):
                href = a_tag.get("href", "")
                if href.endswith(".pdf"):
                    pdf_url = _absolute(url, href)
                    break

        # 正文
        text = ""
        if self.cfg.detail_text_selector in ("body", "article"):
            body = soup.find(self.cfg.detail_text_selector)
            if body:
                text = body.get_text()

        return {"title": title, "date": date, "text": text, "pdf_url": pdf_url}

    def _download_and_extract_pdf(
        self, pdf_url: str, title: str, date: str
    ) -> tuple[str, str]:
        """下载 PDF 并提取全文。返回 (text, local_path)。"""
        hash_suffix = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        local_path = self.data_dir / f"{date}_{safe_title}_{hash_suffix}.pdf"

        if local_path.exists():
            return extract_pdf_text(local_path), str(local_path)

        try:
            resp = self._client.get(pdf_url)
            if resp.status_code == 200 and len(resp.content) >= 100:
                local_path.write_bytes(resp.content)
                return extract_pdf_text(local_path), str(local_path)
        except Exception:
            pass
        return "", ""

    def _fetch(self, url: str) -> Optional[str]:
        """HTTP GET 带重试。

        先查 `_page_cache`：`_discover_pages` 探测分页时已经 GET 过列表页，
        这里直接取用（`pop` 掉，同一 URL 至多缓存一次）。
        """
        cached = self._page_cache.pop(url, None)
        if cached is not None:
            return cached

        for attempt in range(self.cfg.max_retries):
            try:
                resp = self._client.get(url)
                if resp.status_code == 200:
                    return resp.text
                elif resp.status_code in (404, 410):
                    return None
                logger.warning(f"HTTP {resp.status_code} (尝试 {attempt+1}): {url}")
            except Exception as e:
                logger.warning(f"请求失败 (尝试 {attempt+1}): {url} - {e}")
            if attempt < self.cfg.max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避
        return None

    @staticmethod
    def _parse_date_str(date_str: str) -> Optional[str]:
        """解析日期字符串为 ISO 格式。"""
        if not date_str:
            return None
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
        if m:
            return date_str
        return None

    def __del__(self):
        try:
            self._client.close()
        except Exception:
            pass
