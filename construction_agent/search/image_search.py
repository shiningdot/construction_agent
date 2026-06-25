"""图片搜索与下载模块。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from construction_agent.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ImageResult:
    """图片搜索结果。"""
    title: str = ""
    thumb_url: str = ""       # 缩略图 URL
    source_url: str = ""      # 来源页面 URL
    local_path: str = ""      # 本地保存路径
    width: int = 0
    height: int = 0


class ImageSearcher:
    """图片搜索引擎（基于百度图片搜索）。"""

    SEARCH_URL = "https://image.baidu.com/search/acjson"

    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://image.baidu.com/",
        }
        self.image_dir = settings.DATA_DIR / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)

    def search(
        self,
        keyword: str,
        count: int = 5,
    ) -> list[ImageResult]:
        """搜索图片。

        Args:
            keyword: 搜索关键词
            count: 需要的图片数量

        Returns:
            图片结果列表
        """
        results: list[ImageResult] = []

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(
                    self.SEARCH_URL,
                    headers=self.headers,
                    params={
                        "tn": "resultjson_com",
                        "word": keyword,
                        "pn": 0,
                        "rn": count * 2,  # 多取一些以过滤无效
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            for item in data.get("data", []):
                if not isinstance(item, dict):
                    continue
                thumb_url = item.get("thumbURL") or item.get("middleURL") or ""
                if not thumb_url:
                    continue

                results.append(ImageResult(
                    title=item.get("fromPageTitle", "") or "",
                    thumb_url=thumb_url,
                    source_url=item.get("fromURL", "") or "",
                    width=item.get("width", 0) or 0,
                    height=item.get("height", 0) or 0,
                ))

                if len(results) >= count:
                    break

            logger.info(f"图片搜索「{keyword}」找到 {len(results)} 张图片")
            return results

        except Exception as e:
            logger.error(f"图片搜索异常: {e}")
            return []

    def search_accident_images(
        self, keyword: str, count: int = 3
    ) -> list[ImageResult]:
        """专门搜索事故相关图片。

        Args:
            keyword: 事故关键词
            count: 需要图片数

        Returns:
            图片结果列表
        """
        queries = [
            keyword,
            f"{keyword} 事故现场",
            f"{keyword} 坍塌",
        ]

        seen_urls: set[str] = set()
        all_results: list[ImageResult] = []

        for q in queries:
            results = self.search(q, count=count)
            for r in results:
                if r.thumb_url and r.thumb_url not in seen_urls:
                    seen_urls.add(r.thumb_url)
                    all_results.append(r)
                    if len(all_results) >= count:
                        break
            if len(all_results) >= count:
                break

        return all_results[:count]

    def download_image(
        self, image: ImageResult, accident_id: str, index: int
    ) -> Optional[str]:
        """下载单张图片到本地。

        Args:
            image: 图片信息
            accident_id: 事故标识（用于文件名）
            index: 图片序号

        Returns:
            本地文件路径，失败返回 None
        """
        if not image.thumb_url:
            return None

        # 从 URL 推断扩展名
        ext = self._guess_extension(image.thumb_url)
        # 生成文件名：事故ID_序号.扩展名
        safe_id = re.sub(r'[^\w\-]', '_', accident_id)[:30]
        filename = f"{safe_id}_{index:02d}{ext}"
        filepath = self.image_dir / filename

        # 避免重复下载
        if filepath.exists() and filepath.stat().st_size > 1024:
            image.local_path = str(filepath)
            return str(filepath)

        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.get(
                    image.thumb_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://image.baidu.com/",
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()

            filepath.write_bytes(resp.content)
            image.local_path = str(filepath)
            logger.info(f"图片已下载: {filename} ({len(resp.content)} bytes)")
            return str(filepath)

        except Exception as e:
            logger.warning(f"图片下载失败: {image.thumb_url[:50]}... - {e}")
            return None

    def download_images(
        self,
        images: list[ImageResult],
        accident_id: str,
    ) -> list[str]:
        """批量下载图片。

        Args:
            images: 图片列表
            accident_id: 事故标识

        Returns:
            成功下载的本地路径列表
        """
        local_paths = []
        for i, img in enumerate(images):
            path = self.download_image(img, accident_id, i + 1)
            if path:
                local_paths.append(path)
        return local_paths

    def _guess_extension(self, url: str) -> str:
        """从 URL 推断图片扩展名。"""
        # 方法1: 从路径中提取扩展名
        path_match = re.search(r'\.(jpg|jpeg|png|gif|webp|bmp)(?:\?|$)', url, re.I)
        if path_match:
            ext = path_match.group(1).lower()
            if ext == "jpeg":
                ext = "jpg"
            return f".{ext}"

        # 方法2: 从 f= 参数提取（如 f=JPEG, f=PNG）
        fmt_param = re.search(r'[?&]f=(\w+)', url, re.I)
        if fmt_param:
            ext = fmt_param.group(1).lower()
            if ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp"):
                if ext == "jpeg":
                    ext = "jpg"
                return f".{ext}"

        # 方法3: 从 fmt= 参数提取（排除 auto）
        fmt_param2 = re.search(r'[?&]fmt=(\w+)', url, re.I)
        if fmt_param2:
            ext = fmt_param2.group(1).lower()
            if ext != "auto" and ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp"):
                if ext == "jpeg":
                    ext = "jpg"
                return f".{ext}"

        # 方法4: 从 ?type= 参数提取
        type_param = re.search(r'[?&]type=(\w+)', url, re.I)
        if type_param:
            ext = type_param.group(1).lower()
            if ext in ("jpg", "jpeg", "png", "gif", "webp"):
                return f".{ext}"

        return ".jpg"  # 默认
