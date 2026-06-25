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


class BaiduSearchEngine:
    """百度搜索引擎封装（通过模拟浏览器搜索）。"""

    SEARCH_URL = "https://www.baidu.com/s"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.BAIDU_SEARCH_API_KEY or ""
        # 解析 BCE 凭证（用于后续可能的 API 认证）
        self._parse_credential()

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
