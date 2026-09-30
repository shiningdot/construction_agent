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
                all_links.extend(self._parse_list(html))

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
        """发现所有分页 URL。"""
        if not self.cfg.list_page_pattern:
            return [self.cfg.list_url]

        pages = [self.cfg.list_url]
        for n in range(self.cfg.list_page_start + 1, self.cfg.list_page_max_probe + 1):
            page_url = self._build_page_url(n)
            try:
                resp = self._client.head(page_url, timeout=5)
                if resp.status_code == 200:
                    pages.append(page_url)
                else:
                    break
            except Exception:
                break
        return pages

    def _build_page_url(self, page_num: int) -> str:
        """根据分页模式构建 URL。子类可覆盖以定制分页逻辑。"""
        pattern = self.cfg.list_page_pattern
        if "{n}" in pattern:
            return pattern.format(n=page_num)
        if "index_" in pattern:
            # 常见模式: list_url + "index_{n}.html"
            return f"{self.cfg.list_url.rstrip('/')}/{pattern.format(n=page_num)}"
        return pattern

    def _parse_list(self, html: str) -> list[dict]:
        """解析列表页 HTML，提取文章链接。

        Returns:
            [{"title": str, "url": str, "date": str}, ...]
        """
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
                    full_url = href if href.startswith("http") else (
                        self.cfg.base_url.rstrip("/") + "/" + href.lstrip("/")
                    )
                    date = ""
                    m = re.search(r"(\d{4})[/-](\d{2})[/-](\d{2})", href)
                    if m:
                        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
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
                full_url = href if href.startswith("http") else (
                    self.cfg.base_url.rstrip("/") + "/" + href.lstrip("/")
                )
                date = ""
                m = re.search(r"(\d{4})(\d{2})(\d{2})", href)
                if m:
                    date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
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
        pdf_url = ""
        if self.cfg.detail_pdf_selector:
            for a_tag in soup.select(self.cfg.detail_pdf_selector):
                href = a_tag.get("href", "")
                if href.endswith(".pdf"):
                    pdf_url = href if href.startswith("http") else (
                        self.cfg.base_url.rstrip("/") + "/" + href.lstrip("/")
                    )
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
        """HTTP GET 带重试。"""
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
