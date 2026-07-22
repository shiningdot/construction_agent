"""住建部/应急管理部事故通报采集器。

采集住建部 (MOHURD) 和应急管理部 (MEM) 发布的建筑安全事故通报。

住建部年度通报：
- 网址: https://www.mohurd.gov.cn/gongkai/zc/wjk/
- 每年发布"房屋市政工程生产安全事故情况的通报"
- 格式: PDF，含有年度统计数据和典型案例
- 覆盖年份: 2014年至今

应急管理部事故通报：
- 网址: https://www.mem.gov.cn/gk/sgcc/
- 重大及以上事故调查通报
- 格式: HTML页面

采集策略：
1. 搜索通报列表 → 获取 PDF/HTML 链接
2. 下载 PDF → 复用现有 MinerU 解析管道
3. HTML 页面 → 直接解析
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── 住建部页面结构 ──
MOHURD_BASE = "https://www.mohurd.gov.cn"
MOHURD_SEARCH_URL = "https://www.mohurd.gov.cn/gongkai/zc/wjk/"
MOHURD_SAFETY_URL = "https://zlaq.mohurd.gov.cn"

# 应急管理部
MEM_BASE = "https://www.mem.gov.cn"
MEM_SGCC_URL = "https://www.mem.gov.cn/gk/sgcc/"


class MohurdCollector(BaseCollector):
    """住建部事故通报采集器。

    采集住建部官网的房屋市政工程事故年度通报和个案通报。
    支持两种模式：
    1. list 模式：从通报列表页提取链接 → 下载 HTML
    2. pdf 模式：下载 PDF 文件 → 调用 MinerU 解析
    """

    def __init__(
        self,
        collect_mem: bool = True,
        delay: float | None = None,
        data_dir: Optional[Path] = None,
    ):
        """
        Args:
            collect_mem: 是否同时采集应急管理部数据
            delay: 请求间隔（秒）
            data_dir: 数据缓存目录
        """
        self.collect_mem = collect_mem
        self._delay = delay if delay is not None else 3.0
        self.data_dir = data_dir or (settings.DATA_DIR / "mohurd")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._client = httpx.Client(
            timeout=30.0,
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

    def source_name(self) -> str:
        return "mohurd"

    def get_crawl_delay(self) -> float:
        return self._delay

    def collect(self) -> list[RawAccidentText]:
        """采集住建部和应急管理部事故通报。"""
        results: list[RawAccidentText] = []

        # ── 住建部通报 ──
        try:
            mohurd_results = self._collect_mohurd()
            results.extend(mohurd_results)
            logger.info(f"住建部: 采集 {len(mohurd_results)} 条")
        except Exception as e:
            logger.warning(f"住建部采集失败: {e}")

        # ── 应急管理部 ──
        if self.collect_mem:
            try:
                mem_results = self._collect_mem()
                results.extend(mem_results)
                logger.info(f"应急管理部: 采集 {len(mem_results)} 条")
            except Exception as e:
                logger.warning(f"应急管理部采集失败: {e}")

        return results

    def _collect_mohurd(self) -> list[RawAccidentText]:
        """采集住建部通报。"""
        results: list[RawAccidentText] = []

        # Step 1: 从政策文件列表页搜索事故相关通报
        article_links = self._search_mohurd_articles()
        if not article_links:
            logger.warning("住建部: 未找到事故相关通报")
            return results

        logger.info(f"住建部: 找到 {len(article_links)} 个相关通报")

        # Step 2: 逐篇下载解析
        for i, (url, title, date_str) in enumerate(article_links, 1):
            try:
                raw = self._download_mohurd_article(url, title, date_str)
                if raw:
                    results.append(raw)
            except Exception as e:
                logger.debug(f"住建部文章 {url} 失败: {e}")
                continue

            time.sleep(self._delay)

        return results

    def _search_mohurd_articles(self) -> list[tuple[str, str, str]]:
        """搜索住建部事故通报列表，返回 (url, title, date) 列表。"""
        articles: list[tuple[str, str, str]] = []

        # 搜索关键词
        search_terms = [
            "事故通报", "生产安全事故", "建筑施工事故",
            "房屋市政工程", "安全事故情况",
        ]

        # 方式1: 直接从政策文件列表获取
        try:
            resp = self._client.get(MOHURD_SEARCH_URL)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                for a_tag in soup.find_all("a", href=True):
                    title = a_tag.get_text(strip=True)
                    href = a_tag["href"]
                    full_url = urljoin(MOHURD_BASE, href)

                    # 检查标题是否包含建筑事故关键词
                    if any(kw in title for kw in search_terms):
                        # 提取日期
                        date_str = self._extract_date_from_text(
                            a_tag.find_parent("li").get_text() if a_tag.find_parent("li") else title
                        )
                        if not date_str:
                            date_str = ""
                        articles.append((full_url, title, date_str))
        except Exception as e:
            logger.debug(f"住建部列表页请求失败: {e}")

        # 方式2: 从住建部工程质量安全监管平台获取
        try:
            resp = self._client.get(MOHURD_SAFETY_URL)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                for a_tag in soup.find_all("a", href=True):
                    title = a_tag.get_text(strip=True)
                    href = a_tag["href"]
                    full_url = urljoin(MOHURD_SAFETY_URL, href)

                    if any(kw in title for kw in search_terms + ["事故案例", "统计分析"]):
                        articles.append((full_url, title, ""))
        except Exception as e:
            logger.debug(f"住建部安全平台请求失败: {e}")

        # 去重（按 URL）
        seen = set()
        unique = []
        for url, title, date_str in articles:
            if url not in seen:
                seen.add(url)
                unique.append((url, title, date_str))

        return unique

    def _download_mohurd_article(
        self, url: str, title: str, date_str: str
    ) -> Optional[RawAccidentText]:
        """下载单篇住建部通报。"""
        resp = self._client.get(url)
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get("content-type", "")

        # PDF 文件
        if url.endswith(".pdf") or "application/pdf" in content_type:
            return self._handle_pdf(url, title, date_str, resp.content)

        # HTML 页面
        soup = BeautifulSoup(resp.text, "lxml")

        # 提取正文
        content = ""
        for selector in [".article-content", "#content", ".content",
                         ".TRS_Editor", ".pages_content", ".text"]:
            tag = soup.select_one(selector)
            if tag:
                content = tag.get_text(separator="\n", strip=True)
                if len(content) > 100:
                    break

        if not content:
            body = soup.find("body")
            if body:
                for noise in body.find_all(["nav", "footer", "script", "style"]):
                    noise.decompose()
                content = body.get_text(separator="\n", strip=True)

        if not content or len(content) < 100:
            return None

        # 日期
        from construction_agent.utils.date_utils import parse_date_to_dateobj
        date_obj = parse_date_to_dateobj(date_str) if date_str else None
        if not date_obj:
            date_obj = self._extract_date_from_content(soup)

        return RawAccidentText(
            source="mohurd",
            title=title,
            raw_text=content,
            raw_html=resp.text[:50000],
            source_url=url,
            extracted_date=date_obj,
            confidence_date=0.9 if date_obj else 0.0,
        )

    def _handle_pdf(
        self, url: str, title: str, date_str: str, pdf_data: bytes
    ) -> Optional[RawAccidentText]:
        """处理 PDF 格式的通报。

        优先尝试 MinerU API 解析，失败时返回空（等待后续批量 PDF 处理）。
        """
        # 保存 PDF 到本地
        pdf_name = re.sub(r'[^\w\-.]', '_', title)[:50] + ".pdf"
        pdf_path = self.data_dir / pdf_name
        pdf_path.write_bytes(pdf_data)
        logger.info(f"PDF 已保存: {pdf_path}")

        # 尝试 MinerU 解析
        from construction_agent.config import settings as s
        if s.MINERU_API_TOKEN:
            try:
                from construction_agent.input.pdf_processor import PDFProcessor
                processor = PDFProcessor(token=s.MINERU_API_TOKEN)
                segments = processor.process(pdf_path, mode="auto", language="ch")
                if segments:
                    # 合并所有文本片段
                    full_text = "\n\n".join(seg.text for seg in segments)
                    from construction_agent.utils.date_utils import parse_date_to_dateobj
                    date_obj = parse_date_to_dateobj(date_str) if date_str else None

                    return RawAccidentText(
                        source="mohurd_pdf",
                        title=title,
                        raw_text=full_text,
                        source_url=url,
                        extracted_date=date_obj,
                        confidence_date=0.9 if date_obj else 0.0,
                        extra={"pdf_path": str(pdf_path)},
                    )
            except Exception as e:
                logger.warning(f"MinerU 解析失败: {e}")

        # 无法解析时，返回带元数据的记录（后续可批量处理）
        return RawAccidentText(
            source="mohurd_pdf_unparsed",
            title=title,
            raw_text=f"[PDF待解析] {title}",
            source_url=url,
            extra={"pdf_path": str(pdf_path), "needs_parsing": True},
        )

    def _collect_mem(self) -> list[RawAccidentText]:
        """采集应急管理部事故通报。"""
        results: list[RawAccidentText] = []

        try:
            resp = self._client.get(MEM_SGCC_URL)
            if resp.status_code != 200:
                return results

            soup = BeautifulSoup(resp.text, "lxml")

            # 查找事故通报链接
            for a_tag in soup.find_all("a", href=True):
                title = a_tag.get_text(strip=True)
                href = a_tag["href"]
                full_url = urljoin(MEM_BASE, href)

                # 只关注建筑相关事故
                construction_kw = ["建筑", "施工", "坍塌", "坠落", "工地", "工程",
                                   "房屋", "市政", "塔吊", "基坑"]
                if any(kw in title for kw in construction_kw):
                    raw = self._download_mem_article(full_url, title)
                    if raw:
                        results.append(raw)
                    time.sleep(self._delay)

        except Exception as e:
            logger.warning(f"应急管理部采集失败: {e}")

        return results

    def _download_mem_article(self, url: str, title: str) -> Optional[RawAccidentText]:
        """下载应急管理部单篇事故通报。"""
        try:
            resp = self._client.get(url)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "lxml")

            # 提取正文
            content = ""
            for selector in [".article-content", "#content", ".TRS_Editor",
                             ".pages_content", ".text", ".con"]:
                tag = soup.select_one(selector)
                if tag:
                    content = tag.get_text(separator="\n", strip=True)
                    if len(content) > 100:
                        break

            if not content or len(content) < 50:
                return None

            # 提取日期
            date_obj = self._extract_date_from_content(soup)

            return RawAccidentText(
                source="mem",
                title=title,
                raw_text=content,
                raw_html=resp.text[:50000],
                source_url=url,
                extracted_date=date_obj,
                confidence_date=0.85 if date_obj else 0.0,
            )

        except Exception as e:
            logger.debug(f"应急管理部页面 {url} 失败: {e}")
            return None

    @staticmethod
    def _extract_date_from_text(text: str) -> str:
        """从文本中提取日期字符串。"""
        m = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})?[日]?', text)
        if m:
            year, month = m.group(1), m.group(2)
            day = m.group(3) or "01"
            return f"{year}-{int(month):02d}-{int(day):02d}"
        return ""

    @staticmethod
    def _extract_date_from_content(soup: BeautifulSoup):
        """从页面内容中提取日期。"""
        date_str = ""
        for selector in [".article-info", ".info", ".time", ".date", ".source"]:
            tag = soup.select_one(selector)
            if tag:
                date_str = tag.get_text(strip=True)
                break

        if date_str:
            from construction_agent.utils.date_utils import parse_date_to_dateobj
            return parse_date_to_dateobj(date_str)
        return None

    def __del__(self):
        try:
            self._client.close()
        except Exception:
            pass
