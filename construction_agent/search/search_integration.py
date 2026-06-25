"""搜索集成模块：整合文本搜索和图片搜索，统一管理搜索结果元数据。

职责：
1. 对事故关键词执行联网文本搜索，收集搜索结果摘要
2. 对事故关键词执行图片搜索并下载
3. 返回结构化的搜索元数据，供各版本 Pipeline 或批量脚本注入到 Accident 模型
"""

from __future__ import annotations

import logging
from typing import Optional

from construction_agent.search.baidu_search import BaiduSearchEngine
from construction_agent.search.image_search import ImageSearcher, ImageResult
from construction_agent.search.baidu_search import SearchResult

logger = logging.getLogger(__name__)


class SearchIntegrator:
    """搜索集成器：统一管理联网文本搜索和图片搜索的调用与结果保存。"""

    def __init__(self):
        self.text_searcher = BaiduSearchEngine()
        self.image_searcher = ImageSearcher()

    def search_text(
        self,
        keyword: str,
        max_results: int = 10,
    ) -> tuple[list[SearchResult], list[str], list[dict]]:
        """对关键词执行联网文本搜索。

        Args:
            keyword: 搜索关键词（通常是事故标题）
            max_results: 最多返回结果数

        Returns:
            (search_results, queries, snippets_meta)
            - search_results: SearchResult 列表
            - queries: 实际执行的搜索查询列表
            - snippets_meta: 结构化的摘要元数据，每项为
              {url, title, snippet, query}
        """
        queries = [
            f"{keyword} 建筑施工事故",
            f"{keyword} 安全事故 通报",
            f"{keyword} 事故 调查报告",
        ]

        seen_urls: set[str] = set()
        all_results: list[SearchResult] = []
        snippets_meta: list[dict] = []

        for q in queries:
            results = self.text_searcher.search(q, count=5)
            for r in results:
                if r.url and r.url not in seen_urls:
                    seen_urls.add(r.url)
                    all_results.append(r)
                    snippets_meta.append({
                        "url": r.url,
                        "title": r.title,
                        "snippet": r.snippet,
                        "query": q,
                    })

        logger.info(
            f"文本搜索「{keyword}」: {len(queries)}个查询, "
            f"去重后{len(all_results)}条结果"
        )
        return all_results[:max_results], queries, snippets_meta

    def search_images(
        self,
        keyword: str,
        accident_id: str,
        max_images: int = 3,
    ) -> tuple[list[ImageResult], list[str], list[dict]]:
        """对关键词搜索并下载图片。

        Args:
            keyword: 搜索关键词（通常是事故标题）
            accident_id: 事故 ID，用于文件命名
            max_images: 最多下载图片数

        Returns:
            (image_results, local_paths, image_sources)
            - image_results: ImageResult 列表
            - local_paths: 本地下载路径列表
            - image_sources: 图片来源元数据，每项为
              {source_url, thumb_url, local_path}
        """
        image_results = self.image_searcher.search_accident_images(
            keyword, count=max_images
        )
        if not image_results:
            return [], [], []

        local_paths = self.image_searcher.download_images(
            image_results, accident_id
        )

        image_sources = []
        for img, local_path in zip(image_results, local_paths):
            image_sources.append({
                "source_url": img.source_url,
                "thumb_url": img.thumb_url,
                "local_path": local_path,
            })

        logger.info(
            f"图片搜索「{keyword}」: 找到{len(image_results)}张, "
            f"下载{len(local_paths)}张"
        )
        return image_results, local_paths, image_sources

    def build_enhanced_text(
        self,
        original_text: str,
        search_results: list[SearchResult],
    ) -> str:
        """将搜索结果的摘要补充到原始文本中，构建增强后的输入文本。

        Args:
            original_text: 原始输入文本
            search_results: 搜索结果列表

        Returns:
            增强后的文本（原始文本 + 搜索补充信息）
        """
        if not search_results:
            return original_text

        parts = [original_text, "\n\n---\n## 联网检索到的补充信息\n"]
        for i, r in enumerate(search_results, 1):
            parts.append(f"来源{i}: {r.title}")
            parts.append(f"链接: {r.url}")
            if r.snippet:
                parts.append(f"摘要: {r.snippet}")
            parts.append("")
        return "\n".join(parts)

    def integrate_into_accident(
        self,
        accident,
        search_results: list[SearchResult],
        search_queries: list[str],
        snippets_meta: list[dict],
        image_sources: list[dict],
        image_local_paths: list[str],
    ):
        """将搜索元数据注入到 Accident 对象中。

        这是核心整合方法：将文本搜索和图片搜索的结果
        完整写入 Accident 的对应字段。

        Args:
            accident: Accident 实例（会被原地修改）
            search_results: 文本搜索结果
            search_queries: 执行的搜索查询
            snippets_meta: 摘要元数据
            image_sources: 图片来源元数据
            image_local_paths: 图片本地路径
        """
        # 合并来源 URL
        existing_urls = set(accident.source_urls)
        for r in search_results:
            if r.url and r.url not in existing_urls:
                accident.source_urls.append(r.url)
                existing_urls.add(r.url)

        # 注入搜索查询
        if search_queries:
            existing_queries = set(accident.search_queries)
            for q in search_queries:
                if q not in existing_queries:
                    accident.search_queries.append(q)
                    existing_queries.add(q)

        # 注入搜索摘要
        if snippets_meta:
            existing_urls_in_snippets = {s.get("url", "") for s in accident.search_snippets}
            for s in snippets_meta:
                if s.get("url", "") not in existing_urls_in_snippets:
                    accident.search_snippets.append(s)
                    existing_urls_in_snippets.add(s.get("url", ""))

        # 注入图片路径和来源
        if image_local_paths:
            existing_images = set(accident.images)
            for p in image_local_paths:
                if p not in existing_images:
                    accident.images.append(p)
                    existing_images.add(p)

        if image_sources:
            existing_thumbs = {u.get("thumb_url", "") for u in accident.image_urls}
            for u in image_sources:
                if u.get("thumb_url", "") not in existing_thumbs:
                    accident.image_urls.append(u)
                    existing_thumbs.add(u.get("thumb_url", ""))

    def process_full(
        self,
        text: str,
        keyword: str,
        accident_id: str,
        enable_text_search: bool = True,
        enable_image_search: bool = True,
    ) -> dict:
        """完整的搜索集成流程：文本搜索 + 图片搜索。

        这是便捷方法，一次调用完成所有搜索并返回元数据。

        Args:
            text: 原始输入文本
            keyword: 搜索关键词（通常为事故标题）
            accident_id: 事故 ID（用于图片文件名）
            enable_text_search: 是否执行文本搜索
            enable_image_search: 是否执行图片搜索

        Returns:
            {
                "enhanced_text": str,
                "search_results": list[SearchResult],
                "search_queries": list[str],
                "snippets_meta": list[dict],
                "image_results": list[ImageResult],
                "image_local_paths": list[str],
                "image_sources": list[dict],
            }
        """
        result = {
            "enhanced_text": text,
            "search_results": [],
            "search_queries": [],
            "snippets_meta": [],
            "image_results": [],
            "image_local_paths": [],
            "image_sources": [],
        }

        # 文本搜索
        if enable_text_search and keyword:
            search_results, queries, snippets = self.search_text(keyword)
            result["search_results"] = search_results
            result["search_queries"] = queries
            result["snippets_meta"] = snippets
            result["enhanced_text"] = self.build_enhanced_text(
                text, search_results
            )

        # 图片搜索
        if enable_image_search and keyword:
            img_results, local_paths, img_sources = self.search_images(
                keyword, accident_id
            )
            result["image_results"] = img_results
            result["image_local_paths"] = local_paths
            result["image_sources"] = img_sources

        return result
