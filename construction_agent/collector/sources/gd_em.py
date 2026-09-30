"""广东省应急管理厅 (yjgl.gd.gov.cn) 灾害事故调查评估报告爬虫。

目标: https://yjgl.gd.gov.cn/gk/zdlyxxgk/sgdcbg/index.html

架构:
- 纯 HTML 页面，无需 JS 渲染，httpx + BeautifulSoup 直接可爬
- 3 页分页: index.html, index_2.html, index_3.html
- 列表结构: <ul class="list"> → <li> → <h3><a> + <span class="pubDate">
- 详情页结构: <h1 class="article_t"> 标题, <div class="article_con"> 正文
- 内容格式多样化:
  1. HTML 文本（最常见,正文直接在 article_con 中）
  2. 图片（img.nfw-cms-img,需 OCR）
  3. PDF 附件（<a href="/attachment/...">或列表页直接链接 PDF）

与 beijing_em 的关键差异:
- 数据在 HTML 中而非 JS 数组中 → 直接用 BeautifulSoup 解析
- 有分页 → 需要遍历 3 页
- 大部分内容是 HTML 文本而非 PDF → 默认不需要 OCR
- 日期在列表页的 span.pubDate 中 → 无需爬详情页即可获取日期

筛选策略: 标题初筛 → 详情页正文二次筛选 → classify_v2 分类
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from construction_agent.collector.classifier import classify_v2, is_construction_candidate

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── URL 常量 ──
GDEM_BASE = "https://yjgl.gd.gov.cn"
GDEM_LIST_URL = "https://yjgl.gd.gov.cn/gk/zdlyxxgk/sgdcbg/index.html"
GDEM_PAGE_TEMPLATE = "https://yjgl.gd.gov.cn/gk/zdlyxxgk/sgdcbg/index_{}.html"

# (分类器已迁移至 construction_agent.collector.classifier)

# (分类器已迁移至 construction_agent.collector.classifier)

# ═══════════════════════════════════════════════
# 建筑事故标题初筛关键词（宽松版）
# ═══════════════════════════════════════════════

CONSTRUCTION_TITLE_KW: list[str] = [
    # 建筑公司/单位特征词
    "建筑", "建设", "城建", "市政", "中铁", "中建", "中交",
    # 施工行为
    "施工", "在建", "拆除", "装修",
    "浇筑", "打桩", "挖掘", "回填", "搭设",
    # 建筑结构/设备
    "脚手架", "塔吊", "起重机", "升降机", "基坑", "模板",
    "钢结构", "幕墙", "冷却塔",
    # 建筑/项目类型
    "房屋", "楼房", "厂房", "住宅", "小区", "酒店",
    "商场", "大厦", "体育馆", "宿舍楼", "预制构件",
    "自建房", "违建", "违规建设",
    # 建筑事故类型
    "坍塌", "倒塌", "垮塌", "坠落", "高坠", "塌方",
    "物体打击", "机械伤害", "起重伤害",
    # 基础设施工程
    "桥梁", "桥墩", "桥面", "高架", "隧道",
    "地铁施工", "轨道交通", "铁路",
    # 其他工程
    "排水", "供水", "电力施工",
    "工程",
]

# 非事故类记录（安全警示、科普教育等，直接跳过）
NON_ACCIDENT_KW = [
    "案例警示", "历史同期事故", "事故案例", "安全知识",
    "莫让悲剧", "反思", "安全提示", "安全警示",
    "安全预警",
]

# (is_construction_candidate 已迁移至 classifier 模块)

class GdEMCollector(BaseCollector):
    """广东省应急管理厅灾害事故调查评估报告采集器。

    数据流:
        HTML 列表页 → BS4 解析 ul.list → 标题+日期+URL
        → 详情页 → 正文提取 → classify_v2 分类
        → RawAccidentStore

    用法:
        collector = GdEMCollector()
        stats = collector.collect()
    """

    def __init__(
        self,
        delay: float = 1.0,
        crawl_details: bool = True,
        enable_classify: bool = True,
    ):
        self._delay = delay
        self._crawl_details = crawl_details
        self._enable_classify = enable_classify

        # HTTP 客户端
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
        return "gd_em"

    @property
    def collected_count(self) -> int:
        return self._collected

    def collect(self) -> list[RawAccidentText]:
        """执行采集，返回统计字典。"""
        t_start = time.time()

        logger.info("开始采集广东省应急管理厅灾害事故调查评估报告...")

        # Step 1: 确定需要爬取的页码
        page_urls = self._discover_pages()

        # Step 2: 从列表页提取所有记录
        all_records = []
        for page_url in page_urls:
            records = self._parse_list_page(page_url)
            all_records.extend(records)
            logger.info(f"  列表 {page_url.replace(GDEM_BASE, '')}: +{len(records)} 条")

        self._total_list = len(all_records)
        logger.info(f"列表采集完成: {self._total_list} 条记录")

        # Step 3: 逐条处理
        batch_items = []
        for i, record in enumerate(all_records):
            title = record["title"]
            record_url = record["url"]
            pub_date = record["pub_date"]

            # 一级筛选: 标题关键词
            is_ok, reason = is_construction_candidate(title)
            if not is_ok:
                self._filtered_out += 1
                logger.debug(f"  过滤: [{title[:60]}] - {reason}")
                continue

            logger.info(f"  候选({i+1}/{self._total_list}): [{title[:60]}] - {reason}")

            # 处理 PDF 直链
            if record_url.lower().endswith(".pdf"):
                item = self._handle_pdf_direct(record)
                if item:
                    batch_items.append(item)
                    self._collected += 1
                continue

            # ── 处理详情页 ──
            if not self._crawl_details:
                # 不爬详情页：仅用标题信息入库
                item = self._make_basic_item(title, record_url, pub_date)
                batch_items.append(item)
                self._collected += 1
                continue

            time.sleep(self._delay)
            detail = self._crawl_detail(record_url)

            if detail is None:
                # 详情页访问失败，用列表数据入库
                item = self._make_basic_item(title, record_url, pub_date)
                item["status"] = "review"
                item["classification_reason"] = "详情页访问失败"
                batch_items.append(item)
                self._review_count += 1
                logger.warning(f"    详情页访问失败，入审核队列")
                continue

            # 合并详情信息
            detail_title = detail.get("title") or title
            detail_text = detail.get("content") or ""
            detail_date = detail.get("date") or pub_date
            source = detail.get("source") or "广东省应急管理厅"
            pdf_urls = detail.get("pdf_urls") or []
            has_images = detail.get("has_images", False)

            # 二级筛选: classify_v2 文本分类
            if self._enable_classify and detail_text:
                result = classify_v2(detail_title, detail_text)
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
            else:
                classification = "pending_review"
                classification_reason = "无正文内容(图片格式或待OCR)" if has_images else "未启用分类"
                classification_confidence = "low"
                item_status = "review"

            # 构建内容摘要
            content_summary = self._build_content_summary(
                detail_title, detail_text, source, pdf_urls, has_images
            )

            item = {
                "title": detail_title,
                "content": content_summary[:50000],
                "source_url": record_url,
                "source_name": "gd_em",
                "publish_date": detail_date,
                "status": item_status,
                "pdf_urls": pdf_urls,
                # 需要OCR的标准: 有图片且正文很少(<200字)，或有PDF附件且正文很少
                "needs_ocr": (has_images and len(detail_text) < 200) or (bool(pdf_urls) and len(detail_text) < 200),
                "issuing_authority": source,
                "classification": classification,
                "classification_reason": classification_reason,
                "classification_confidence": classification_confidence,
                "review_note": "",
            }

            batch_items.append(item)
            if item_status == "review":
                self._review_count += 1
            else:
                self._collected += 1

            # 每 10 条批量保存
            if len(batch_items) >= 10:
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

    def _discover_pages(self) -> list[str]:
        """探测所有列表页 URL。

        通过访问首页，检查分页链接，确定全部页面。
        当前已知 3 页 (index.html, index_2.html, index_3.html)，
        但保留动态探测能力以应对未来增加页面。
        """
        pages = [GDEM_LIST_URL]

        try:
            html = self._fetch(GDEM_LIST_URL)
            if not html:
                return pages

            soup = BeautifulSoup(html, "html.parser")
            # 查找分页链接: index_N.html
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # 支持相对路径 (index_2.html) 和绝对路径 (http://.../index_2.html)
                m = re.search(r"index_(\d+)\.html", href)
                if m:
                    page_num = int(m.group(1))
                    if page_num >= 2:
                        full_url = urljoin(GDEM_BASE, href)
                        if full_url not in pages:
                            pages.append(full_url)

            # 按页码排序
            def _page_key(url: str) -> int:
                m = re.search(r"index_(\d+)", url)
                return int(m.group(1)) if m else 0

            pages.sort(key=_page_key)
            logger.info(f"探测到 {len(pages)} 个列表页")
        except Exception as e:
            logger.warning(f"页面探测异常: {e}")

        return pages

    def _parse_list_page(self, url: str) -> list[dict]:
        """解析列表页，提取所有记录。

        HTML 结构:
            <ul class="list">
              <li>
                <i></i>
                <h3><a href="...">标题</a></h3>
                <span class="pubDate">YYYY-MM-DD</span>
              </li>
            </ul>

        Returns:
            [{"title": str, "url": str, "pub_date": str}, ...]
        """
        html = self._fetch(url)
        if not html:
            logger.warning(f"列表页访问失败: {url}")
            return []

        soup = BeautifulSoup(html, "html.parser")
        ul = soup.find("ul", class_="list")
        if not ul:
            logger.warning(f"未找到 ul.list: {url}")
            return []

        records = []
        for li in ul.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue

            title = a.get_text(strip=True)
            href = a["href"]
            full_url = href if href.startswith("http") else urljoin(GDEM_BASE, href)

            date_span = li.find("span", class_="pubDate")
            pub_date = date_span.get_text(strip=True) if date_span else ""

            if title and full_url:
                records.append({
                    "title": title,
                    "url": full_url,
                    "pub_date": pub_date,
                })

        return records

    def _crawl_detail(self, url: str) -> Optional[dict]:
        """爬取详情页，提取标题、正文、日期、来源和 PDF 附件。

        HTML 结构:
            <h1 class="article_t">标题</h1>
            <div class="bor_bot">
                <div class="article_item fl">信息来源：XXX</div>
                <div class="article_item fl">时间：YYYY-MM-DD HH:MM</div>
            </div>
            <div class="article_con">
                正文内容...  (或 <img class="nfw-cms-img" .../>)
                <a href="/attachment/...">附件：xxx.pdf</a>
            </div>

        Returns:
            {
                "title": str, "content": str, "date": str,
                "source": str, "pdf_urls": [str], "has_images": bool
            }
            失败返回 None
        """
        html = self._fetch(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        # 标题
        h1 = soup.find("h1", class_="article_t")
        title = h1.get_text(strip=True) if h1 else ""

        # 日期和来源 —— 从 bor_bot 中提取
        date = ""
        source = ""
        # 遍历所有 article_item 提取信息
        for item_div in soup.find_all("div", class_="article_item"):
            text = item_div.get_text(strip=True)
            if "时间：" in text:
                m = re.search(r"时间[：:]\s*(.+)", text)
                if m:
                    date = m.group(1).strip()
            elif "信息来源：" in text:
                m = re.search(r"信息来源[：:]\s*(.+)", text)
                if m:
                    source = m.group(1).strip()
            elif "来源：" in text:
                m = re.search(r"来源[：:]\s*(.+)", text)
                if m:
                    source = m.group(1).strip()

        # 正文 —— article_con
        article_div = soup.find("div", class_="article_con")
        content_text = ""
        has_images = False
        pdf_urls = []

        if article_div:
            # 检测图片
            images = article_div.find_all("img", class_="nfw-cms-img")
            if images:
                has_images = True

            # 提取纯文本
            content_text = article_div.get_text(separator="\n").strip()
            # 清理多余空行
            content_text = re.sub(r"\n{3,}", "\n\n", content_text)

            # 提取 PDF 附件链接
            for a in article_div.find_all("a", href=True):
                href = a["href"]
                if ".pdf" in href.lower() or ".doc" in href.lower():
                    full_pdf = href if href.startswith("http") else urljoin(GDEM_BASE, href)
                    pdf_urls.append(full_pdf)

        # 如果 article_con 没找到 PDF，在整个页面范围内搜索附件
        if not pdf_urls:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)
                if (".pdf" in href.lower() or ".doc" in href.lower()) and "附件" in text:
                    full_pdf = href if href.startswith("http") else urljoin(GDEM_BASE, href)
                    pdf_urls.append(full_pdf)

        return {
            "title": title,
            "content": content_text,
            "date": date,
            "source": source,
            "pdf_urls": pdf_urls,
            "has_images": has_images,
        }

    def _handle_pdf_direct(self, record: dict) -> Optional[dict]:
        """处理列表页直接链接到 PDF 的记录。

        对于 PDF 直链，我们无法直接获取正文内容，标记为需要 OCR。
        但如果标题已经明确表明是非建筑事故（如石化泄漏），则直接过滤。
        """
        title = record["title"]
        pdf_url = record["url"]
        pub_date = record["pub_date"]

        # 先用标题做一次粗筛确认是建筑相关
        # PDF 直链通常不是建筑施工事故（往往是化工/道路等），但保守起见保留
        # 让用户人工审核
        return {
            "title": title,
            "content": f"[PDF 报告] {title}\n下载: {pdf_url}",
            "source_url": pdf_url,
            "source_name": "gd_em",
            "publish_date": pub_date,
            "status": "review",
            "pdf_urls": [pdf_url],
            "needs_ocr": True,
            "issuing_authority": "广东省应急管理厅",
            "classification": "pending_review",
            "classification_reason": "PDF直链, 需下载后OCR分类",
            "classification_confidence": "low",
            "review_note": "",
        }

    def _make_basic_item(self, title: str, url: str, pub_date: str) -> dict:
        """构建仅包含列表信息的记录（用于详情页不可用时的兜底）。"""
        return {
            "title": title,
            "content": f"{title}\n来源: {url}",
            "source_url": url,
            "source_name": "gd_em",
            "publish_date": pub_date,
            "status": "new",
            "pdf_urls": [],
            "needs_ocr": False,
            "issuing_authority": "广东省应急管理厅",
            "classification": "auto_construction",
            "classification_reason": "标题关键词匹配(未获取详情)",
            "classification_confidence": "low",
            "review_note": "",
        }

    def _build_content_summary(
        self,
        title: str,
        text: str,
        source: str,
        pdf_urls: list[str],
        has_images: bool,
    ) -> str:
        """构建内容摘要字段。"""
        parts = [title]
        if source:
            parts.append(f"信息来源: {source}")
        if has_images:
            parts.append("[图片格式报告，需 OCR 提取文字]")
        if pdf_urls:
            parts.append(f"附件({len(pdf_urls)}个):")
            for u in pdf_urls[:5]:
                parts.append(f"  {u}")
        if text:
            # 截取前 3000 字作为预览
            preview = text[:3000]
            if len(text) > 3000:
                preview += f"\n... (共 {len(text)} 字)"
            parts.append(f"\n正文:\n{preview}")

        return "\n".join(parts)

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

def crawl_gd_em(delay: float = 1.0) -> dict:
    """同步入口。"""
    c = GdEMCollector(delay=delay)
    return c.collect()
