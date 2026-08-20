"""事故图片采集模块：从搜索增强URL中抓取事故相关图片。

管道:
  1. 获取事故的 source_urls (原始 + 搜索增强)
  2. 访问每个网页 → 提取 <img> 标签
  3. 过滤: 去logo/二维码/图标/小图(宽高<100px或<10KB)
  4. 下载 → data/images/{accident_id}/
  5. VLM 两阶段分析 → 入库 image_analyses
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from construction_agent.extraction.image_analyzer import ImageAnalyzer, ImageAnalysisResult

logger = logging.getLogger(__name__)

IMAGE_DIR = "data/images"

# 过滤规则：URL/alt文本含这些关键词 → 跳过
SKIP_KEYWORDS = [
    "logo", "icon", "avatar", "qrcode", "qr_code", "二维码", "banner",
    "广告", "button", "btn", "arrow", "bg", "background", "footer",
    "header", "nav", "menu", "share", "点赞", "收藏", "wx_fmt",
    "emoji", "表情", "头像", "勋章", "loading", "spinner",
]

# 最小的有效事故图片尺寸（像素）
MIN_WIDTH = 120
MIN_HEIGHT = 120
# 最小文件大小（字节）：小于此值视为图标/占位符
MIN_FILE_SIZE = 8192  # 8KB


@dataclass
class CollectedImage:
    """采集到的单张图片。"""
    url: str                     # 图片原始URL
    page_url: str                # 来源网页URL
    local_path: str              # 本地存储路径
    file_size: int = 0           # 文件大小(字节)
    error: Optional[str] = None
    analysis: Optional[ImageAnalysisResult] = None  # VLM分析结果


class ImageCollector:
    """事故图片采集器。

    用法:
        collector = ImageCollector()
        images = collector.collect_for_accident(
            accident_id="abc123",
            urls=["https://...", "https://..."],
            accident_context={"accident_type":"坍塌","deaths":3,...},
            max_images=5,
        )
    """

    def __init__(self):
        self.analyzer = ImageAnalyzer()
        os.makedirs(IMAGE_DIR, exist_ok=True)

    # ══════════════════════════════════════════════════════════
    # 公共 API
    # ══════════════════════════════════════════════════════════

    def collect_for_accident(
        self,
        accident_id: str,
        urls: list[str],
        accident_context: Optional[dict] = None,
        max_images: int = 5,
        max_pages: int = 10,
    ) -> list[CollectedImage]:
        """从多个 URL 中采集事故图片。

        Args:
            accident_id: 事故ID
            urls: 网页URL列表(来自source_urls)
            accident_context: Step1提取结果, 用于VLM Phase2
            max_images: 最多采集图片数
            max_pages: 最多访问网页数
        """
        img_dir = os.path.join(IMAGE_DIR, accident_id)
        os.makedirs(img_dir, exist_ok=True)

        all_images: list[CollectedImage] = []
        pages_visited = 0

        for url in urls:
            if pages_visited >= max_pages or len(all_images) >= max_images:
                break

            logger.info(f"采集图片 [{accident_id}]: {url[:80]}")
            page_images = self._extract_from_page(url, img_dir, accident_id)
            all_images.extend(page_images)
            pages_visited += 1

            if page_images:
                logger.info(f"  从该页提取 {len(page_images)} 张图片")

        # 截断到 max_images
        selected = all_images[:max_images]

        # VLM 分析
        if selected and self.analyzer._enabled:
            paths = [img.local_path for img in selected]
            logger.info(f"VLM分析 {len(paths)} 张图片...")
            vlm_results = self.analyzer.analyze_images(paths, accident_context)
            for img, vlm_r in zip(selected, vlm_results):
                img.analysis = vlm_r

        return selected

    # ══════════════════════════════════════════════════════════
    # 网页图片提取
    # ══════════════════════════════════════════════════════════

    def _extract_from_page(
        self, page_url: str, img_dir: str, accident_id: str,
    ) -> list[CollectedImage]:
        """从一个网页中提取并下载有效的事故图片。"""
        try:
            html = self._fetch_page(page_url)
            if not html:
                return []

            img_urls = self._find_images(html, page_url)
            if not img_urls:
                return []

            collected = []
            for img_url in img_urls[:8]:  # 每页最多处理8张
                result = self._download_if_valid(img_url, page_url, img_dir)
                if result:
                    collected.append(result)

            return collected

        except Exception as e:
            logger.warning(f"页面处理异常 [{page_url[:60]}]: {e}")
            return []

    def _fetch_page(self, url: str, timeout: int = 10) -> Optional[str]:
        """获取网页 HTML。"""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            if resp.status_code != 200:
                return None
            return resp.text
        except Exception:
            return None

    def _find_images(self, html: str, page_url: str) -> list[str]:
        """从 HTML 中提取 <img> 标签的 src，并补全相对路径。"""
        soup = BeautifulSoup(html, "lxml")
        img_urls = []

        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
            alt = (img.get("alt") or "").lower()

            if not src:
                continue

            # 过滤：alt文本含跳过关键词
            if any(kw in alt for kw in SKIP_KEYWORDS):
                continue

            # 过滤：URL含跳过关键词
            src_lower = src.lower()
            if any(kw in src_lower for kw in SKIP_KEYWORDS):
                continue

            # 过滤：base64内嵌图片(通常是图标)
            if src.startswith("data:"):
                continue

            # 补全相对路径
            full_url = urljoin(page_url, src)
            # 去参数(部分CDN图片参数影响格式识别)
            if '?' in full_url:
                base = full_url.split('?')[0]
                if any(base.lower().endswith(ext) for ext in ['.jpg','.jpeg','.png','.gif','.webp','.bmp']):
                    pass  # 保留参数
                else:
                    full_url = base  # 去参数避免格式识别问题

            if full_url not in img_urls:
                img_urls.append(full_url)

        return img_urls

    # ══════════════════════════════════════════════════════════
    # 图片下载与过滤
    # ══════════════════════════════════════════════════════════

    def _download_if_valid(
        self, img_url: str, page_url: str, img_dir: str,
    ) -> Optional[CollectedImage]:
        """下载图片并校验有效性。"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
            "Referer": page_url,
        }

        try:
            resp = httpx.get(img_url, headers=headers, timeout=15, follow_redirects=True)
            if resp.status_code != 200:
                return None

            content = resp.content
            content_type = resp.headers.get("content-type", "").lower()

            # 过滤：不是图片类型
            if not any(t in content_type for t in ['image', 'octet-stream']):
                return None

            # 过滤：文件太小
            if len(content) < MIN_FILE_SIZE:
                logger.debug(f"图片太小 ({len(content)}B): {img_url[:60]}")
                return None

            # 确定扩展名
            ext = ".jpg"
            if "png" in content_type:
                ext = ".png"
            elif "gif" in content_type:
                ext = ".gif"
            elif "webp" in content_type:
                ext = ".webp"
            elif "bmp" in content_type:
                ext = ".bmp"

            # 生成本地文件名
            url_hash = hashlib.md5(img_url.encode()).hexdigest()[:10]
            filename = f"{url_hash}{ext}"
            local_path = os.path.join(img_dir, filename)

            # 保存
            with open(local_path, "wb") as f:
                f.write(content)

            return CollectedImage(
                url=img_url, page_url=page_url, local_path=local_path,
                file_size=len(content),
            )

        except Exception as e:
            logger.debug(f"图片下载失败 [{img_url[:60]}]: {e}")
            return None
