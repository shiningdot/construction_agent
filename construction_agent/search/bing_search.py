"""Bing Search API 集成模块。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

import httpx

from construction_agent.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """搜索结果项。"""
    title: str
    url: str
    snippet: str
    content: Optional[str] = None  # 页面内容（如果抓取）


class BingSearchEngine:
    """Bing 搜索引擎封装。"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.BING_SEARCH_API_KEY
        self.endpoint = settings.BING_SEARCH_ENDPOINT
        if not self.api_key:
            logger.warning("Bing Search API Key 未配置，搜索功能不可用")

    def search(
        self,
        query: str,
        count: Optional[int] = None,
        market: Optional[str] = None,
        offset: int = 0,
    ) -> list[SearchResult]:
        """执行搜索。

        Args:
            query: 搜索关键词
            count: 返回结果数
            market: 市场代码，如 zh-CN
            offset: 偏移量

        Returns:
            搜索结果列表
        """
        if not self.api_key:
            logger.debug("Bing API Key 未配置，跳过搜索")
            return []

        count = count or settings.BING_SEARCH_COUNT
        market = market or settings.BING_SEARCH_MARKET

        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
        }
        params = {
            "q": query,
            "count": count,
            "offset": offset,
            "mkt": market,
            "textFormat": "Raw",
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(self.endpoint, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()

            results = []
            for item in data.get("webPages", {}).get("value", []):
                results.append(SearchResult(
                    title=item.get("name", ""),
                    url=item.get("url", ""),
                    snippet=item.get("snippet", ""),
                ))

            logger.info(f"搜索「{query}」获得 {len(results)} 条结果")
            return results

        except httpx.TimeoutException:
            logger.error(f"搜索超时: {query}")
            return []
        except httpx.HTTPStatusError as e:
            logger.error(f"搜索 HTTP 错误: {e.response.status_code} - {e.response.text}")
            return []
        except Exception as e:
            logger.error(f"搜索异常: {e}")
            return []

    def search_accident(self, keyword: str) -> list[SearchResult]:
        """专门搜索事故信息。

        Args:
            keyword: 事故关键词（如工地名、事故描述）

        Returns:
            搜索结果列表
        """
        if not self.api_key:
            logger.info("Bing API Key 未配置，跳过联网搜索")
            return []

        # 构建更有针对性的搜索查询
        queries = [
            f"{keyword} 建筑施工事故",
            f"{keyword} 安全事故 通报",
            f"{keyword} 住建部 事故",
        ]

        all_results: dict[str, SearchResult] = {}
        for q in queries:
            results = self.search(q, count=5)
            for r in results:
                if r.url not in all_results:
                    all_results[r.url] = r

        logger.info(f"事故搜索去重后共 {len(all_results)} 条结果")
        return list(all_results.values())[:10]

    async def search_async(
        self,
        query: str,
        count: Optional[int] = None,
        market: Optional[str] = None,
    ) -> list[SearchResult]:
        """异步搜索。"""
        if not self.api_key:
            return []

        count = count or settings.BING_SEARCH_COUNT
        market = market or settings.BING_SEARCH_MARKET

        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
        }
        params = {
            "q": query,
            "count": count,
            "mkt": market,
            "textFormat": "Raw",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.endpoint, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()

            results = []
            for item in data.get("webPages", {}).get("value", []):
                results.append(SearchResult(
                    title=item.get("name", ""),
                    url=item.get("url", ""),
                    snippet=item.get("snippet", ""),
                ))
            return results

        except Exception as e:
            logger.error(f"异步搜索异常: {e}")
            return []
