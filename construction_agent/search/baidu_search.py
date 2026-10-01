"""百度搜索引擎模块（爬虫方式获取搜索结果）。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from construction_agent.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """搜索结果项。"""
    title: str
    url: str
    snippet: str
    content: Optional[str] = None


# 百度对无状态的爬虫请求会回一个 **HTTP 200 的拦截页**：约 1,438 字节、
# `<title>百度安全验证</title>`、没有 `content_left` 容器。它与「这个查询真的
# 没有结果」在 `_parse_results` 那里**完全同形**——都返回空列表、都不抛异常、
# `raise_for_status()` 都放行。见 `is_block_page`。
_BLOCK_MARKERS: tuple[str, ...] = (
    "百度安全验证", "请完成安全验证", "请输入验证码", "wappass",
)
_RESULT_CONTAINER = "content_left"


def classify_response(html: str) -> str:
    """这一坨响应到底是哪种页：`results` / `blocked` / `unknown`。

    **三态，不是两态。** 起初这里只有 `is_block_page(html) -> bool`，而布尔
    装不下三种来路——于是「确认是百度风控」与「压根不认识这个页面」被压成
    同一个 `False`，后者又和「真·结果页」同形。这正是本项目反复撞见的同一
    件事（见 `check_collector_health._closure_of` 返回 `None`、`_status_of`
    的「空」）：**一格的取值域，装不下两种故障。**

    三态的来路：

    - `results`  有结果容器 `content_left`。**哪怕里面一个条目都没有**，也
      说明页面是结果页——这是「真的没搜到」，不是被挡。
    - `blocked`  没有结果容器，**且**命中风控标记词。1,438 字节的
      `<title>百度安全验证</title>` 壳子。
    - `unknown`  没有结果容器，也不认识。**这一格不该被当成「没结果」丢掉**：
      百度改版换了容器名、或者回了一个别样的错误页，都会落到这里，而它的
      正确处置是「去看页面」而不是「换关键词」。

    为什么 `blocked` 仍要求命中标记词：**误报风控比漏报更贵。** 把「百度
    改版了」误报成「你被风控了」，会把人引去换 IP / 换搜索源，而真正该做的
    是修解析器——方向错了。落进 `unknown` 至少说的是实话。
    """
    if _RESULT_CONTAINER in html:
        return "results"
    if any(m in html for m in _BLOCK_MARKERS):
        return "blocked"
    return "unknown"


def is_block_page(html: str) -> bool:
    """便捷布尔版。**注意它把 `unknown` 与 `results` 压成同一个 `False`**，
    需要分清时用 `classify_response`。

    2026-10-01 实测：同一 IP 连打 5 次同一个查询，**第 1 次拿到 889,697 字节
    的真结果页（解析出 7 条），之后 4 次全是 1,438 字节的 `百度安全验证`**。
    修前这件事在日志里只有一条 INFO（`获到 0 条结果`），调用方 `if not
    search_results: continue` 静默跳过——**「被风控挡了」和「真的没搜到」
    在整条链路上分不出**。
    """
    return classify_response(html) == "blocked"


class BaiduSearchEngine:
    """百度搜索引擎封装（通过模拟浏览器搜索）。"""

    SEARCH_URL = "https://www.baidu.com/s"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.BAIDU_SEARCH_API_KEY or ""
        # 解析 BCE 凭证（用于后续可能的 API 认证）
        self._parse_credential()

        # 被百度风控拦下的次数（2026-10-01 第三十九轮新增）。
        #
        # 它与「这个查询没有结果」是两件事，但在修前完全同形。**计数而不是
        # 只记日志**，是因为调用方（`NewsAggregator.collect()`）要看得出
        # 「这一轮 17 个查询全被挡了」——只记日志的话，17 条 INFO 连起来
        # 也读不出这件事，因为它们与「17 个查询都没结果」一字不差。
        self.blocked_pages = 0

        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.baidu.com/",
        }

    def _parse_credential(self):
        """解析百度云 BCE 凭证。"""
        self.bce_access_key = ""
        self.bce_secret_key = ""
        if self.api_key.startswith("bce-v3/"):
            parts = self.api_key.replace("bce-v3/", "", 1).split("/")
            if len(parts) >= 2:
                self.bce_access_key = parts[0]
                self.bce_secret_key = parts[1]
                logger.info(
                    f"已解析百度云 BCE 凭证: {self.bce_access_key[:15]}..."
                )

    def search(
        self,
        query: str,
        count: int = 10,
    ) -> list[SearchResult]:
        """执行百度搜索。

        Args:
            query: 搜索关键词
            count: 返回结果数（最多20条）

        Returns:
            搜索结果列表
        """
        try:
            params = {
                "wd": query,
                "rn": min(count, 20),
            }

            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                resp = client.get(
                    self.SEARCH_URL,
                    headers=self.headers,
                    params=params,
                )
                resp.raise_for_status()

            results = self._parse_results(resp.text)
            if not results:
                # 「被风控挡了」不能记成「这个查询没结果」——两者都是 0 条，
                # 但一个说明换个查询也没用，另一个说明该换关键词。
                verdict = classify_response(resp.text)
                if verdict != "results":
                    title = re.search(r"<title>(.*?)</title>", resp.text, re.S)
                    title = title.group(1).strip() if title else "—"
                    if verdict == "blocked":
                        self.blocked_pages += 1
                        logger.warning(
                            f"百度搜索「{query}」返回的是拦截页，不是结果页"
                            f"（HTTP {resp.status_code}、{len(resp.text)} 字节、"
                            f"title={title}）——"
                            f"本条计 0 条，但原因是风控，不是没有结果。"
                        )
                    else:
                        # `unknown`：既不是结果页，也不是已知的拦截页。
                        # **不能沉默**——沉默就等于把它记成「没结果」，那正是
                        # 修前那个 0 条无解释的样子。
                        logger.warning(
                            f"百度搜索「{query}」的响应里没有结果容器 "
                            f"`{_RESULT_CONTAINER}`、也不认识这是什么页"
                            f"（HTTP {resp.status_code}、{len(resp.text)} 字节、"
                            f"title={title}）——既不是「没有结果」，也不是已知的"
                            f"拦截页。很可能百度改了页面结构，要跟着改解析器，"
                            f"而不是换关键词。"
                        )
                    return results
            logger.info(f"百度搜索「{query}」获到 {len(results)} 条结果")
            return results

        except httpx.TimeoutException:
            logger.error(f"百度搜索超时: {query}")
            return []
        except httpx.HTTPStatusError as e:
            logger.error(f"百度搜索 HTTP 错误: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"百度搜索异常: {e}")
            return []

    def _parse_results(self, html: str) -> list[SearchResult]:
        """解析百度搜索结果页 HTML。"""
        results = []
        soup = BeautifulSoup(html, "lxml")

        # 百度搜索结果容器
        content_left = soup.find(id="content_left")
        if not content_left:
            # 备选容器
            content_left = soup.find("div", class_="result-page")
        if not content_left:
            return results

        # 遍历搜索结果块
        for result_div in content_left.find_all("div", class_=re.compile(r"result")):
            result = self._parse_single_result(result_div)
            if result:
                results.append(result)
                if len(results) >= 20:
                    break

        return results

    def _parse_single_result(self, div) -> Optional[SearchResult]:
        """解析单条搜索结果。"""
        try:
            # 标题和链接
            title_tag = div.find("h3")
            if not title_tag:
                title_tag = div.find("a")
            if not title_tag:
                return None

            title = title_tag.get_text(strip=True)
            if not title:
                return None

            # 提取 URL
            url = ""
            a_tag = title_tag.find("a") if title_tag.name != "a" else title_tag
            if a_tag and a_tag.get("href"):
                url = a_tag["href"]
                # 百度搜索结果链接是重定向URL，需要解析
                if url.startswith("/"):
                    url = "https://www.baidu.com" + url

            # 摘要
            snippet = ""
            # 尝试多种摘要容器
            abstract_div = div.find("div", class_="c-abstract")
            if not abstract_div:
                abstract_div = div.find("span", class_="content-right_")
            if not abstract_div:
                abstract_div = div.find("div", class_=re.compile(r"abstract"))
            if not abstract_div:
                # 尝试在 div 中查找非空文本
                for tag in div.find_all(["div", "span"]):
                    text = tag.get_text(strip=True)
                    if len(text) > 20 and text != title:
                        abstract_div = tag
                        break
            if abstract_div:
                snippet = abstract_div.get_text(strip=True)

            return SearchResult(
                title=title,
                url=url,
                snippet=snippet[:300] if snippet else "",
            )

        except Exception as e:
            logger.debug(f"解析单条结果异常: {e}")
            return None

    def search_accident(self, keyword: str) -> list[SearchResult]:
        """专门搜索事故信息。

        Args:
            keyword: 事故关键词

        Returns:
            搜索结果列表
        """
        queries = [
            f"{keyword} 建筑施工事故",
            f"{keyword} 安全事故 通报",
            f"{keyword} 事故 调查报告",
        ]

        all_results: dict[str, SearchResult] = {}
        for q in queries:
            results = self.search(q, count=5)
            for r in results:
                if r.url not in all_results:
                    all_results[r.url] = r

        logger.info(f"事故搜索去重后共 {len(all_results)} 条结果")
        return list(all_results.values())[:10]
