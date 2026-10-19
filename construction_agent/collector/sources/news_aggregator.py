"""新闻搜索引擎聚合器。

复用现有 SearchIntegrator（百度搜索）进行建筑事故新闻的主动搜索采集。

工作原理：
1. 使用预定义的建筑事故搜索查询模板，定期搜索新闻
2. 对搜索结果中的 URL，下载详情页内容
3. 清洗后通过清洗层提取字段

与现有搜索模块的区别：
- 现有搜索是被动式的（用户输入事故 → 搜索补充信息）
- 本聚合器是主动式的（定期用查询模板主动搜索 → 发现新事故）
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.config import settings
from construction_agent.search.baidu_search import BaiduSearchEngine

logger = logging.getLogger(__name__)

# ── 主动搜索查询模板 ──
# 使用多种关键词组合覆盖不同类型的事故报道
SEARCH_QUERIES: list[tuple[str, str]] = [
    ("建筑施工 安全事故 死亡", "general"),
    ("工地坍塌 事故 通报", "collapse"),
    ("高处坠落 施工 死亡", "fall"),
    ("塔吊 倒塌 事故 伤亡", "crane"),
    ("基坑坍塌 施工 事故", "excavation"),
    ("脚手架 倒塌 建筑事故", "scaffold"),
    ("触电事故 建筑工地", "electric"),
    ("建筑施工 火灾 事故", "fire"),
    ("中毒窒息 施工 安全事故", "poison"),
    ("物体打击 建筑事故 死亡", "strike"),
    ("机械伤害 工地 事故", "machine"),
    ("起重伤害 施工 事故", "lifting"),
    ("较大事故 建筑施工", "major"),
    ("重大事故 建筑安全", "serious"),
    ("房屋市政工程 事故通报", "mohurd_report"),
    ("建筑安全生产事故", "general2"),
    ("施工安全事故 调查报告", "report"),
]

# 新闻网站域名白名单（避免爬取低质量聚合站）
NEWS_DOMAINS: set[str] = {
    "people.com.cn", "xinhuanet.com", "chinanews.com",
    "sina.com.cn", "163.com", "sohu.com", "qq.com",
    "ifeng.com", "ce.cn", "gmw.cn", "youth.cn",
    "cctv.com", "cnr.cn", "china.com.cn",
    "safehoo.com", "zhulong.com", "jzsg.cn",
    "mem.gov.cn", "mohurd.gov.cn",
}


class NewsAggregator(BaseCollector):
    """新闻搜索引擎聚合器。

    使用百度搜索主动发现建筑事故新闻，下载详情页后清洗入库。
    去重由后续 Deduplicator 统一处理。
    """

    def __init__(
        self,
        queries: list[tuple[str, str]] | None = None,
        max_results_per_query: int = 8,
        delay: float | None = None,
        data_dir: Optional[Path] = None,
    ):
        """
        Args:
            queries: 搜索查询模板列表，None 使用默认
            max_results_per_query: 每个查询最多获取的结果数
            delay: 请求间隔（秒）
            data_dir: 缓存目录
        """
        self.queries = queries or SEARCH_QUERIES
        self.max_results_per_query = max_results_per_query
        self._delay = delay if delay is not None else 3.0
        self.data_dir = data_dir or (settings.DATA_DIR / "news")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 使用现有的百度搜索引擎
        self._searcher = BaiduSearchEngine()

        self._client = httpx.Client(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

        self._visited_urls: set[str] = set()

        # ── 三个计数器（2026-10-01 第三十九轮新增）──
        #
        # 加它们之前，这个源在健康检查表上是「空 / 0 条 / 列表 — / 闭环 —」，
        # **内部状态一格都看不见**；而它实际有两条各自独立的断路（见 `collect()`）。
        # 有了 `_total_listed` 与 `_fetch_failed`，「列表」与「结果」两栏都能出数，
        # 表上就从「说不出话」变成「说得出话」。
        #
        # 搜索结果总条数。名字与 TRS 基类一致，`check_collector_health.py` 的
        # `_probe_child` 按这三个名字读（`_total_listed` / `_total_list` /
        # `_total_records`），所以这一栏会真的出数，而不是 `—`。
        self._total_listed = 0
        # 被百度风控拦下的**查询**数。与 TRS 基类的 `_fetch_failed` 同义：
        # 都是「没拿到有效内容」。区别是那边只数重试耗尽、404 不算；这边数
        # 拦截页——**拦截页也不是「没有结果」**，这一点两边一致。
        self._fetch_failed = 0
        # 被域名白名单拦下的**结果**数。它和上面那个是两回事：那个说「搜索这层
        # 没通」，这个说「搜索通了，但拿到的全是百度跳转链接、域名判不过」。
        self._filtered_domain = 0

    def source_name(self) -> str:
        return "news_aggregator"

    def get_crawl_delay(self) -> float:
        return self._delay

    def collect(self) -> list[RawAccidentText]:
        """执行聚合搜索采集。

        **两条各自独立的断路，修前都看不见**（2026-10-01 第三十九轮实测）：

        1. **风控。** 同一 IP 连打，第 1 次拿到真结果页，之后每次都回一个
           1,438 字节的 `百度安全验证` 拦截页（HTTP 200）。修前它与「这个查询
           没有结果」在整条链路上同形——`_parse_results` 都返回空、调用方都
           `continue`、日志都只有一条 INFO。
        2. **域名白名单。** 即便风控放行，搜索结果的 href 也全是
           `http://www.baidu.com/link?url=…`，域名是 `www.baidu.com`，**不在
           19 个白名单域名里** → `_is_allowed_domain` 全部拒掉（实测 5 个样本：
           2 个百度跳转链接全拦、3 个白名单域名全放）。修前这一层一条日志都
           没有，只是一个光秃秃的 `continue`。

        本轮**只让这两件事看得见，不修它们**——第 1 条是百度对这个 IP 的策略、
        不是代码能绕的；第 2 条要跟着重定向才能拿到真实域名，而重定向在风控
        之后才谈得上。两条都交作者裁决（换搜索源 / 申请官方 API key）。

        **但第 3 条缺陷修了**（同一轮验证时撞出来的）：修前函数体里那两个
        `continue`（空结果、抛异常）**把循环末尾的查询间隔一起跳过**，于是
        「17 个查询全被风控」这一轮实际是**零间隔连打 17 次**——2.7 秒跑完，
        而正常有结果时同样的 17 个查询要 100 秒出头。**越被拦打得越快，越打
        越快越被拦**，是个正反馈。间隔改到 `finally` 里：无论走哪条分支都等。
        """
        all_results: list[RawAccidentText] = []
        blocked_before = self._searcher.blocked_pages

        for i, (query, category) in enumerate(self.queries, 1):
            logger.info(f"搜索查询 [{i}/{len(self.queries)}]: {query}")

            try:
                # Step 1: 百度搜索
                search_results = self._searcher.search(query, count=self.max_results_per_query)
                self._total_listed += len(search_results)

                if not search_results:
                    logger.debug(f"查询 '{query}': 无结果")
                    continue

                logger.info(f"查询 '{query}': 获取 {len(search_results)} 条搜索结果")

                # Step 2: 下载详情页
                for sr in search_results:
                    if sr.url in self._visited_urls:
                        continue

                    # 可选：过滤域名
                    if not self._is_allowed_domain(sr.url):
                        self._filtered_domain += 1
                        continue

                    try:
                        raw = self._download_article(sr.url, sr.title, category)
                        if raw:
                            all_results.append(raw)
                            self._visited_urls.add(sr.url)
                    except Exception as e:
                        logger.debug(f"下载 {sr.url[:60]} 失败: {e}")
                        continue

                    time.sleep(self._delay)

            except Exception as e:
                logger.warning(f"查询 '{query}' 失败: {e}")

            finally:
                # 查询间隔。**必须在 `finally` 里。**
                #
                # 修前这两行在循环体末尾，而上面有两处 `continue`（空结果、
                # 抛异常）**会把它一起跳过**。后果不是「少睡一会儿」，是
                # **正反馈**：被风控 → 每个查询都空 → 每次都跳过间隔 →
                # 17 个查询零间隔连打 → 更被风控。2026-10-01 实测，全被拦
                # 那一轮 17 个查询共 2.7 秒（≈0.15 秒/次），而正常有结果、
                # 间隔生效时同样 17 个查询要 100 秒出头——**两轮耗时差 40 倍，
                # 差的就是这个**。
                time.sleep(self._delay * 2)

        # ── 汇总结论 ──
        # 逐条 INFO 读不出「整轮都被挡了」：17 条 `获到 0 条结果` 与「17 个查询
        # 真的都没结果」一字不差。所以这里必须有一条**说得出话**的日志。
        self._fetch_failed = self._searcher.blocked_pages - blocked_before
        n = len(self.queries)
        if self._fetch_failed >= n:
            logger.error(
                f"[news_aggregator] {n} 个查询**全部**拿到的是百度拦截页、"
                f"不是结果页——本源当前采不到任何东西，且原因不在代码里"
                f"（百度对这个 IP 的风控）。换关键词、加重试都不会有用。"
            )
        elif self._fetch_failed:
            logger.warning(
                f"[news_aggregator] {n} 个查询里 {self._fetch_failed} 个被百度"
                f"拦截页挡下，另有 {self._total_listed} 条搜索结果、"
                f"{self._filtered_domain} 条被域名白名单拦掉，"
                f"最终产出 {len(all_results)} 条。"
            )
        if self._total_listed and not all_results:
            logger.warning(
                f"[news_aggregator] 搜到了 {self._total_listed} 条结果却一条都没"
                f"产出，其中 {self._filtered_domain} 条被域名白名单拦掉——"
                f"百度搜索结果的 href 是 `www.baidu.com/link?url=…` 跳转链接，"
                f"**真实域名要跟一次重定向才知道**，白名单判不到它。"
            )
        return all_results

    def _is_allowed_domain(self, url: str) -> bool:
        """检查 URL 是否属于可信新闻源。"""
        try:
            domain = urlparse(url).netloc.lower()
            # 移除 www 前缀
            domain = re.sub(r'^www\.', '', domain)
            # 允许任意 .gov.cn 域名
            if domain.endswith('.gov.cn'):
                return True
            # 检查白名单
            return any(domain.endswith(d) for d in NEWS_DOMAINS)
        except Exception:
            return False

    def _download_article(
        self, url: str, search_title: str, category: str
    ) -> Optional[RawAccidentText]:
        """下载新闻详情页并提取内容。"""
        try:
            resp = self._client.get(url)
            if resp.status_code != 200:
                return None

            # 跳过过大的响应（非文本内容）
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return None

            html = resp.text
            if len(html) < 100:
                return None

            soup = BeautifulSoup(html, "lxml")

            # ── 标题 ──
            title = ""
            for selector in ["h1", ".article-title", ".title", "#title",
                             ".news-title", "h2", "h3"]:
                tag = soup.select_one(selector)
                if tag and tag.get_text(strip=True):
                    title = tag.get_text(strip=True)
                    # 去除"新闻网"等后缀
                    title = re.sub(r'[_\|\-—].{2,20}$', '', title).strip()
                    break

            if not title:
                title_tag = soup.find("title")
                if title_tag:
                    title = title_tag.get_text(strip=True)
                    title = re.sub(r'[_\|\-—].{2,30}$', '', title).strip()

            if not title:
                title = search_title

            # ── 日期 ──
            from construction_agent.utils.date_utils import parse_date_to_dateobj
            date_obj = None
            for selector in [".article-info", ".info", ".time", ".date",
                             ".pub-date", ".source", ".news-info"]:
                tag = soup.select_one(selector)
                if tag:
                    date_obj = parse_date_to_dateobj(tag.get_text(strip=True))
                    if date_obj:
                        break

            # ── 正文 ──
            content = ""
            for selector in [".article-content", ".content", "#content",
                             ".article-body", ".news-content", ".text",
                             ".main-content", "article", ".TRS_Editor"]:
                tag = soup.select_one(selector)
                if tag:
                    # 去除脚本和样式
                    for noise in tag.find_all(["script", "style", "iframe"]):
                        noise.decompose()
                    content = tag.get_text(separator="\n", strip=True)
                    if len(content) > 100:
                        break

            if not content or len(content) < 80:
                return None

            # ── 快速过滤：确认与建筑事故相关 ──
            construction_kw = ["建筑", "施工", "工地", "坍塌", "坠落", "塔吊",
                               "脚手架", "基坑", "工程", "监理", "安全", "事故"]
            text_sample = (title + content[:500]).lower()
            if not any(kw in text_sample for kw in construction_kw):
                return None

            return RawAccidentText(
                source=f"news_{category}",
                title=title,
                raw_text=content,
                raw_html=html[:50000],
                source_url=url,
                extracted_date=date_obj,
                confidence_date=0.7 if date_obj else 0.0,
            )

        except httpx.TimeoutException:
            logger.debug(f"新闻页面超时: {url[:60]}")
            return None
        except Exception as e:
            logger.debug(f"新闻页面异常: {url[:60]} - {e}")
            return None

    def __del__(self):
        try:
            self._client.close()
        except Exception:
            pass
