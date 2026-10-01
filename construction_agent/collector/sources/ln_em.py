"""辽宁省应急管理厅 (yjgl.ln.gov.cn) 事故调查处理爬虫。

目标: https://yjgl.ln.gov.cn/yjgl/xxgk/zwgkzdgz/aqsczdlyxxgk/sgdccl/index.shtml

架构:
- TRS WCM (大汉/拓尔思 CMS), 纯 HTML 渲染, 无需 JS
- 单页 20 条记录, 无分页
- 列表结构: <ul class="easysite-list-modelone"> → <li class="clearfix">
  → <a title="标题" aisite-article-file="yjgl,hash,YYYY-MM-DD HH:MM:SS" href="...">
  + <span class="datetime">YYYY-MM-DD</span>

两种详情格式:
  1. PDF viewer (较新, ~9条):
     /uiFramework/js/pdfjs/web/viewer.html?file=/yjgl/articleFileDir/{YYYY-MM}/{DD}/{hash}/{file}.pdf
     → 从 file= 参数提取 PDF URL → 下载 PDF → PyMuPDF 提取文本
  2. HTML 详情页 (较旧, ~11条):
     /yjgl/xxgk/.../sgdccl/{GUID}/index.shtml
     → 正文在 <div class="news-text"> 或 <div class="TRS_Editor">

与 gd_em/beijing_em 的关键差异:
- 单页无分页（20条全部在一页）
- PDF 格式为主（较新的事故报告，需 OCR/PDF提取）
- 日期从 <a> 标签的 aisite-article-file 属性提取
- 需下载 PDF 文件到本地保存

数据保留策略:
- PDF 文件: 下载到 data/ln_em/pdfs/ 目录
- HTML 详情页: 保存完整 HTML 到 data/ln_em/details/ 目录
- 音频/视频: 记录 URL 到 extra 字段
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, unquote

import httpx
from bs4 import BeautifulSoup
from construction_agent.collector.classifier import classify_v2, is_construction_candidate
from construction_agent.collector.pdf_text import extract_pdf_text

from construction_agent.collector.base import (BaseCollector, CollectResult,
                                               RawAccidentText)
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── URL 常量 ──
LNEM_BASE = "https://yjgl.ln.gov.cn"
LNEM_LIST_URL = "https://yjgl.ln.gov.cn/yjgl/xxgk/zwgkzdgz/aqsczdlyxxgk/sgdccl/index.shtml"

# (分类器已迁移至 construction_agent.collector.classifier)

# (分类器已迁移至 construction_agent.collector.classifier)

# ═══════════════════════════════════════════════
# 建筑事故标题初筛关键词（宽松版）
# ═══════════════════════════════════════════════

# 非事故记录（管理办法、通知、培训班等）
NON_ACCIDENT_PATTERNS = [
    r"暂行办法", r"管理办法", r"实施细则",
    r"关于进一步加强", r"关于加强", r"关于进一步做好",
    r"培训班", r"培训会",
    r"挂牌督办",  # 督办通知不是调查报告
    r"结案批复",
    r"生产安全事故报告和调查处理条例",  # 法规原文
]

# (is_construction_candidate 已迁移至 classifier 模块)

class LnEMCollector(BaseCollector):
    """辽宁省应急管理厅事故调查处理采集器。

    数据流:
        HTML 列表页 → 解析 <ul class="easysite-list-modelone">
        → PDF viewer 链接: 提取 file= 参数 → 下载 PDF → PyMuPDF 提取文本
        → HTML 详情链接: 解析 div.news-text / div.TRS_Editor
        → classify_v2 分类 → RawAccidentStore

    本地存储:
        data/ln_em/pdfs/     — 下载的 PDF 文件
        data/ln_em/details/  — 保存的 HTML 详情页

    用法:
        collector = LnEMCollector()
        stats = collector.collect()
    """

    def __init__(
        self,
        delay: float = 1.0,
        download_pdfs: bool = True,
        enable_classify: bool = True,
        data_dir: Optional[Path] = None,
    ):
        self._delay = delay
        self._download_pdfs = download_pdfs
        self._enable_classify = enable_classify

        # 本地存储目录
        # `data_dir=None` 时落 `settings.DATA_DIR/<源名>`（旧行为）。在线采集
        # 传一个临时目录进来，`data/<源名>/` 就不再产生——采集产出自 M2 起
        # 只落工作库的 `raw_accidents` 表（蓝图 01 §3.4）。
        self.data_dir = data_dir or (settings.DATA_DIR / "ln_em")
        self.pdf_dir = self.data_dir / "pdfs"
        self.detail_dir = self.data_dir / "details"
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.detail_dir.mkdir(parents=True, exist_ok=True)

        # HTTP 客户端 — 使用 Scrapling 风格的 headers
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

        # 原始数据缓存
        self.raw_store = RawAccidentStore()

        # 状态统计
        self._total_list: int = 0
        self._filtered_out: int = 0
        self._collected: int = 0
        self._review_count: int = 0
        self._errors: int = 0

    # ── 兼容 Web GUI ──
    @property
    def start_page(self) -> int:
        return 1

    @start_page.setter
    def start_page(self, value: int):
        pass

    @property
    def end_page(self) -> int:
        return 1

    @end_page.setter
    def end_page(self, value: int):
        pass

    def source_name(self) -> str:
        return "ln_em"

    @property
    def collected_count(self) -> int:
        return self._collected

    def collect(self) -> CollectResult:
        """执行采集，返回统计字典。"""
        t_start = time.time()

        logger.info("开始采集辽宁省应急管理厅事故调查处理...")

        # Step 1: 获取列表页
        html = self._fetch(LNEM_LIST_URL)
        if not html:
            logger.error(f"无法访问: {LNEM_LIST_URL}")
            return self._make_stats(0)

        # Step 2: 解析列表
        records = self._parse_list_page(html)
        self._total_list = len(records)
        logger.info(f"列表解析完成: {self._total_list} 条记录")

        # Step 3: 逐条处理
        batch_items = []
        for i, record in enumerate(records):
            title = record["title"]
            record_url = record["url"]
            pub_date = record["pub_date"]
            is_pdf = record["is_pdf"]
            pdf_url = record["pdf_url"]

            # 一级筛选: 标题关键词
            is_ok, reason = is_construction_candidate(title)
            if not is_ok:
                self._filtered_out += 1
                logger.debug(f"  过滤: [{title[:60]}] - {reason}")
                continue

            logger.info(f"  候选({i+1}/{self._total_list}): [{title[:60]}] - {reason}")

            time.sleep(self._delay)

            # ── 处理 PDF viewer 链接 ──
            #
            # 落盘路径**不往外传**（2026-10-01 第二十四轮）。`_process_pdf` /
            # `_process_html_detail` 仍要落盘（`extract_pdf_text` 读的是文件、
            # HTML 也是先写盘再解析），但那个路径只在各自内部有意义：经在线
            # 采集进来的 `data_dir` 是 `_run_source` 建的**临时目录**，任务一
            # 结束就被 `shutil.rmtree` 删掉——写进产出等于记录一个必然消失的
            # 位置。空着只是缺信息，填错值是在撒谎。裁决的出处与实测见
            # `sources/nmg_em.py` 同一处注释。
            if is_pdf and pdf_url and self._download_pdfs:
                detail_text, _ = self._process_pdf(pdf_url, title, pub_date)
            elif is_pdf:
                # 不下载PDF，仅记录URL
                detail_text = ""
            else:
                # ── 处理 HTML 详情页 ──
                detail_text, _ = self._process_html_detail(
                    record_url, title, pub_date
                )

            # ── classify_v2 二级筛选 ──
            if self._enable_classify and detail_text:
                result = classify_v2(title, detail_text)
                logger.info(
                    f"    分类: is_const={result['is_construction']}, "
                    f"confidence={result['confidence']}, "
                    f"reason={result['reason']}"
                )

                if result["is_construction"] and result["confidence"] == "high":
                    classification = "auto_construction"
                    classification_reason = result["reason"]
                    classification_confidence = "high"
                    item_status = "new"
                elif not result["is_construction"] and result["confidence"] == "high":
                    self._filtered_out += 1
                    logger.info(f"    排除: {result['reason']}")
                    continue
                else:
                    classification = "pending_review"
                    classification_reason = result["reason"]
                    classification_confidence = "low"
                    item_status = "review"
            elif not detail_text and is_pdf:
                # PDF 文本提取失败
                classification = "pending_review"
                classification_reason = "PDF文本提取失败, 需人工审核"
                classification_confidence = "low"
                item_status = "review"
            elif not detail_text and not is_pdf:
                # HTML 详情页获取失败
                classification = "pending_review"
                classification_reason = "详情页访问失败, 需人工审核"
                classification_confidence = "low"
                item_status = "review"
            else:
                # 未启用分类
                classification = "pending_review"
                classification_reason = "未启用分类" if not self._enable_classify else "无正文"
                classification_confidence = "low"
                item_status = "review"

            # ── 构建内容摘要 ──
            # 不拼 `[本地文件: …]`（2026-10-01 第二十四轮）——理由见上方注释。
            content_parts = [title]
            if is_pdf and pdf_url:
                content_parts.append(f"PDF: {pdf_url}")
            if detail_text:
                preview = detail_text[:3000]
                if len(detail_text) > 3000:
                    preview += f"\n... (共 {len(detail_text)} 字)"
                content_parts.append(f"\n正文:\n{preview}")

            # 提取来源部门
            issuing = "辽宁省应急管理厅"

            item = {
                "title": title,
                "content": "\n".join(content_parts)[:50000],
                "source_url": record_url,
                "source_name": "ln_em",
                "publish_date": pub_date,
                "status": item_status,
                "pdf_urls": [pdf_url] if pdf_url else [],
                "needs_ocr": is_pdf and not detail_text,
                "issuing_authority": issuing,
                "classification": classification,
                "classification_reason": classification_reason,
                "classification_confidence": classification_confidence,
                "review_note": "",
                "is_pdf_format": is_pdf,
            }

            batch_items.append(item)
            if item_status == "review":
                self._review_count += 1
            else:
                self._collected += 1

            # 每 5 条批量保存
            if len(batch_items) >= 5:
                self.raw_store.add_batch(batch_items)
                batch_items = []

        # 保存剩余
        if batch_items:
            self.raw_store.add_batch(batch_items)
        self.raw_store._save()

        logger.info(
            f"采集完成: {self._collected} 条入库, "
            f"{self._review_count} 条待审, "
            f"{self._filtered_out} 条过滤"
        )

        return self._make_stats(time.time() - t_start)

    def _parse_list_page(self, html: str) -> list[dict]:
        """解析列表页，提取所有事故记录。

        HTML 结构:
            <ul class="easysite-list-modelone">
              <li class="clearfix">
                <a aisite-article-file="yjgl,hash,YYYY-MM-DD HH:MM:SS"
                   href="/uiFramework/js/pdfjs/web/viewer.html?file=..."
                   title="标题" ...>
                <span class="datetime">YYYY-MM-DD</span>
              </li>
            </ul>

        Returns:
            [{"title": str, "url": str, "pub_date": str,
              "is_pdf": bool, "pdf_url": str}, ...]
        """
        soup = BeautifulSoup(html, "html.parser")
        ul = soup.find("ul", class_="easysite-list-modelone")
        if not ul:
            logger.warning("未找到 ul.easysite-list-modelone")
            return []

        records = []
        for li in ul.find_all("li", class_="clearfix"):
            a = li.find("a")
            if not a:
                continue

            title = a.get("title", "").strip()
            if not title:
                title = a.get_text(strip=True)

            href = a.get("href", "").strip()
            full_url = href if href.startswith("http") else urljoin(LNEM_BASE, href)

            # 提取日期: 优先从 aisite-article-file 属性
            # 格式: "yjgl,hash,YYYY-MM-DD HH:MM:SS"
            pub_date = ""
            article_file = a.get("aisite-article-file", "")
            date_match = re.search(r",(\d{4}-\d{2}-\d{2})", article_file)
            if date_match:
                pub_date = date_match.group(1)

            # 回退: 从 span.datetime 提取
            if not pub_date:
                span = li.find("span", class_="datetime")
                if span:
                    pub_date = span.get_text(strip=True)

            # 判断是否为 PDF viewer 链接
            is_pdf = "viewer.html?file=" in href
            pdf_url = ""
            if is_pdf:
                # 从 file= 参数提取 PDF 路径
                file_match = re.search(r"file=([^&\s\"']+)", href)
                if file_match:
                    pdf_path = file_match.group(1)
                    # URL 解码
                    pdf_path = unquote(pdf_path)
                    pdf_url = urljoin(LNEM_BASE, pdf_path)

            records.append({
                "title": title,
                "url": full_url,
                "pub_date": pub_date,
                "is_pdf": is_pdf,
                "pdf_url": pdf_url,
            })

        return records

    def _process_pdf(self, pdf_url: str, title: str, pub_date: str) -> tuple[str, str]:
        """下载 PDF 并提取文本。

        Returns:
            (提取的文本, 本地文件路径)，失败返回 ("", "")
        """
        # 生成本地文件名
        url_hash = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        local_filename = f"{pub_date}_{safe_title}_{url_hash}.pdf"
        local_path = self.pdf_dir / local_filename

        # 如果已缓存，直接读取
        if local_path.exists():
            logger.debug(f"    PDF 已缓存: {local_filename}")
            text = extract_pdf_text(local_path)
            return text, str(local_path)

        # 下载 PDF
        try:
            resp = self._client.get(pdf_url)
            if resp.status_code != 200 or len(resp.content) < 100:
                logger.warning(f"    PDF 下载失败: HTTP {resp.status_code}, size={len(resp.content)}")
                return "", ""

            local_path.write_bytes(resp.content)
            logger.info(f"    PDF 已下载: {local_filename} ({len(resp.content)} bytes)")

            # 提取文本
            text = extract_pdf_text(local_path)
            if text:
                logger.info(f"    文本提取: {len(text)} 字")
            else:
                logger.warning(f"    文本提取失败")
            return text, str(local_path)

        except Exception as e:
            logger.error(f"    PDF 处理异常: {e}")
            return "", ""

    def _process_html_detail(
        self, detail_url: str, title: str, pub_date: str
    ) -> tuple[str, str]:
        """获取 HTML 详情页并提取文本。

        Returns:
            (提取的文本, 本地文件路径)，失败返回 ("", "")
        """
        html = self._fetch(detail_url)
        if not html:
            return "", ""

        # 保存完整 HTML 到本地
        url_hash = hashlib.md5(detail_url.encode()).hexdigest()[:12]
        local_filename = f"{pub_date}_{url_hash}.html"
        local_path = self.detail_dir / local_filename
        try:
            local_path.write_text(html, encoding="utf-8")
            logger.debug(f"    HTML 已保存: {local_filename}")
        except Exception:
            pass

        # 解析正文
        soup = BeautifulSoup(html, "html.parser")

        # 优先查找正文区域
        content_div = (
            soup.find("div", class_="news-text")
            or soup.find("div", class_="TRS_Editor")
            or soup.find("div", class_="article_con")
            or soup.find("div", class_="content")
        )

        if content_div:
            text = content_div.get_text(separator="\n").strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text, str(local_path)

        # 回退: 从整个页面提取文本
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n").strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text, str(local_path)

        return "", ""

    def _fetch(self, url: str) -> Optional[str]:
        """获取页面 HTML，带重试。"""
        for attempt in range(3):
            try:
                resp = self._client.get(url)
                if resp.status_code == 200:
                    return resp.text
                elif resp.status_code == 404:
                    return None
                else:
                    logger.debug(f"HTTP {resp.status_code}: {url}")
            except Exception as e:
                logger.debug(f"请求失败(尝试{attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2)
        return None

    def _make_stats(self, duration: float) -> dict:
        cache_stats = self.raw_store.stats()
        return {
            "total_records": self._total_list,
            "total_crawled": self._collected,
            "pending_review": self._review_count,
            "filtered_out": self._filtered_out,
            "errors": self._errors,
            "cached_total": cache_stats["total"],
            "cached_new": cache_stats["new"],
            "cached_review": cache_stats["review"],
            "duration_seconds": round(duration, 1),
        }

    def __del__(self):
        try:
            self._client.close()
        except Exception:
            pass

# ═══════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════

def crawl_ln_em(delay: float = 1.0) -> dict:
    """同步入口。"""
    c = LnEMCollector(delay=delay)
    return c.collect()
