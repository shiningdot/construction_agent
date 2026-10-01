"""河南省应急管理厅 (yjglt.henan.gov.cn) 事故信息爬虫。

目标: https://yjglt.henan.gov.cn/zwgk/xxgkml/sgxx/

架构:
- 政府 CMS，纯 HTML 渲染，无 JS
- 5 页分页: index.html, index_2.html ~ index_5.html，每页 16 条
- 列表结构: <div class="zfxxgk_zdgkc"> → <ul> → <li> → <a title="标题"> + <b>日期</b>
- 详情页几乎没有正文内容，事故报告全文以 PDF 附件形式存在
- PDF 托管在 oss.henan.gov.cn

采集策略:
  1. 遍历 5 个列表页 → 解析 <li> → 获取标题、URL、日期
  2. 过滤非事故记录（挂牌督办通知书、管理办法等）
  3. 获取详情页 → 提取 PDF 附件 URL
  4. 下载 PDF → PyMuPDF 提取全文 → classify_v2 分类
  5. 存入 RawAccidentStore

与 ln_em 的关键差异:
- 有 5 页分页
- 列表日期在 <b> 标签中
- 详情页几乎空（仅标题+日期+来源+PDF链接）
- PDF 必须下载才能获取事故文本
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

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
HNEM_BASE = "https://yjglt.henan.gov.cn"
HNEM_LIST_URL = "https://yjglt.henan.gov.cn/zwgk/xxgkml/sgxx/"

# (分类器已迁移至 construction_agent.collector.classifier)

# (分类器已迁移至 construction_agent.collector.classifier)

# ═══════════════════════════════════════════════
# 标题初筛
# ═══════════════════════════════════════════════

# 挂牌督办通知不是事故报告
NON_ACCIDENT_KW = [
    "挂牌督办",
    "暂行办法", "管理办法", "实施细则",
    "关于进一步加强", "关于进一步做好",
]

# (is_construction_candidate 已迁移至 classifier 模块)

class HenanEMCollector(BaseCollector):
    """河南省应急管理厅事故信息采集器。

    数据流:
        列表页(5页) → 解析 <li><a> + <b>日期</b>
        → 详情页 → 提取 PDF 附件 URL
        → 下载 PDF → PyMuPDF 提取全文
        → classify_v2 分类 → RawAccidentStore

    本地存储:
        data/henan_em/pdfs/ — 下载的 PDF 文件

    用法:
        collector = HenanEMCollector()
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

        # 本地存储
        # `data_dir=None` 时落 `settings.DATA_DIR/<源名>`（旧行为）。在线采集
        # 传一个临时目录进来，`data/<源名>/` 就不再产生——采集产出自 M2 起
        # 只落工作库的 `raw_accidents` 表（蓝图 01 §3.4）。
        self.data_dir = data_dir or (settings.DATA_DIR / "henan_em")
        self.pdf_dir = self.data_dir / "pdfs"
        self.pdf_dir.mkdir(parents=True, exist_ok=True)

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

        # 状态
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
        return "henan_em"

    @property
    def collected_count(self) -> int:
        return self._collected

    def collect(self) -> CollectResult:
        """执行采集，返回统计字典。"""
        t_start = time.time()

        logger.info("开始采集河南省应急管理厅事故信息...")

        # Step 1: 探测所有列表页
        page_urls = self._discover_pages()

        # Step 2: 解析所有列表页
        all_records = []
        for page_url in page_urls:
            html = self._fetch(page_url)
            if not html:
                continue
            records = self._parse_list_page(html)
            all_records.extend(records)
            logger.info(f"  列表页 {len(page_urls)}/{len(page_urls)}: +{len(records)} 条")

        self._total_list = len(all_records)
        logger.info(f"列表采集完成: {self._total_list} 条记录")

        # Step 3: 逐条处理
        batch_items = []
        for i, record in enumerate(all_records):
            title = record["title"]
            record_url = record["url"]
            pub_date = record["pub_date"]

            # 一级筛选: 标题关键词 + 排除非事故记录
            is_ok, reason = is_construction_candidate(title)
            if not is_ok:
                self._filtered_out += 1
                logger.debug(f"  过滤: [{title[:60]}] - {reason}")
                continue

            logger.info(f"  候选({i+1}/{self._total_list}): [{title[:60]}] - {reason}")
            time.sleep(self._delay)

            # ── 获取详情页 ──
            detail = self._crawl_detail(record_url)

            pdf_url = ""
            detail_date = pub_date
            detail_source = "河南省应急管理厅"
            detail_text = ""
            detail_title = title

            if detail:
                detail_title = detail.get("title") or title
                pdf_url = detail.get("pdf_url") or ""
                detail_date = detail.get("date") or pub_date
                detail_source = detail.get("source") or detail_source
                detail_text = detail.get("text") or ""

            # ── 处理 PDF ──
            #
            # 落盘路径**不往外传**（2026-10-01 第二十四轮）。`_process_pdf` 仍要
            # 落盘（`extract_pdf_text` 读的是文件），但那个路径只在内部有意义：
            # 经在线采集进来的 `data_dir` 是临时目录，任务结束即被
            # `shutil.rmtree` 删掉——写进产出等于记录一个必然消失的位置。
            # 裁决的出处与实测见 `sources/nmg_em.py` 同一处注释。
            pdf_text = ""
            if pdf_url and self._download_pdfs:
                pdf_text, _ = self._process_pdf(pdf_url, title, detail_date)

            # 合并所有文本用于分类
            combined_text = detail_text
            if pdf_text:
                combined_text = pdf_text  # PDF 文本优先

            # 如果无 PDF 且详情页文字过少(<500字有效正文)，不信任分类结果，直接入审核
            no_pdf_weak_text = (not pdf_text) and (not pdf_url or len(detail_text) < 500)

            # ── classify_v2 二级筛选 ──
            if self._enable_classify and combined_text and not no_pdf_weak_text:
                result = classify_v2(detail_title, combined_text)
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
            elif not combined_text and pdf_url:
                classification = "pending_review"
                classification_reason = "PDF文本提取失败"
                classification_confidence = "low"
                item_status = "review"
            else:
                classification = "pending_review"
                classification_reason = "无PDF附件或无正文"
                classification_confidence = "low"
                item_status = "review"

            # ── 构建内容摘要 ──
            # 不拼 `[本地文件: …]`（2026-10-01 第二十四轮）。`local_path` 只在
            # `_process_pdf` 内部有意义，见上方注释；此前那行会把
            # `/tmp/collect_<urandom>/…` 混进正文。
            parts = [detail_title]
            if detail_source:
                parts.append(f"信息来源: {detail_source}")
            if pdf_url:
                parts.append(f"PDF: {pdf_url}")
            if combined_text:
                preview = combined_text[:3000]
                if len(combined_text) > 3000:
                    preview += f"\n... (共 {len(combined_text)} 字)"
                parts.append(f"\n正文:\n{preview}")

            item = {
                "title": detail_title,
                "content": "\n".join(parts)[:50000],
                "source_url": record_url,
                "source_name": "henan_em",
                "publish_date": detail_date,
                "status": item_status,
                "pdf_urls": [pdf_url] if pdf_url else [],
                "needs_ocr": bool(pdf_url) and not combined_text,
                "issuing_authority": detail_source,
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

            # 每 5 条保存
            if len(batch_items) >= 5:
                self.raw_store.add_batch(batch_items)
                batch_items = []

        if batch_items:
            self.raw_store.add_batch(batch_items)

        logger.info(
            f"采集完成: {self._collected} 条入库, "
            f"{self._review_count} 条待审, "
            f"{self._filtered_out} 条过滤"
        )

        return self._make_stats(time.time() - t_start)

    def _discover_pages(self) -> list[str]:
        """探测所有列表页 URL。

        两处都错过，合起来的效果是**这个源一直只抓第一页**：

        - **编号是零基的**。`list_url`（`.../sgxx/`）是第 1 页，第 2 页写作
          `index_1.html`。原先 `range(2, 20)` 从 `index_2.html` 起探，
          **`index_1.html` 从未被访问过**——而它正是第 2 页。
        - **判据用 GET，不用 HEAD**。原先发 HEAD，与 `shaanxi_em` 是同一个坑：
          站点的 HEAD 响应不可信，403 或超时会被读成「页面不存在」。
          这里加一条「本页解析出 0 条也停」——超出末页时不一定给 404。

        实测（2026-10-01）：`index_1` / `index_2` / `index_3` 各 16 条，
        **32 条是新条目**，挂牌督办编号从〔2026〕3 号递减到〔2024〕1 号。
        """
        pages = [HNEM_LIST_URL]

        for page_num in range(1, 30):
            url = f"{HNEM_BASE}/zwgk/xxgkml/sgxx/index_{page_num}.html"
            try:
                resp = self._client.get(url, timeout=15)
            except Exception:
                break
            if resp.status_code != 200:
                break
            if not self._parse_list_page(resp.text):
                break
            pages.append(url)

        logger.info(f"探测到 {len(pages)} 个列表页")
        return pages

    def _parse_list_page(self, html: str) -> list[dict]:
        """解析列表页，提取事故记录。

        HTML 结构:
            <div class="zfxxgk_zdgkc">
              <ul>
                <li>
                  <a href="..." title="标题">标题</a>
                  <b>YYYY-MM-DD</b>
                </li>
              </ul>
            </div>

        Returns:
            [{"title": str, "url": str, "pub_date": str}, ...]
        """
        soup = BeautifulSoup(html, "html.parser")
        container = soup.find("div", class_="zfxxgk_zdgkc")
        if not container:
            logger.warning("未找到 div.zfxxgk_zdgkc")
            return []

        records = []
        for li in container.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue

            title = a.get("title", "").strip() or a.get_text(strip=True)
            href = a["href"]
            full_url = href if href.startswith("http") else urljoin(HNEM_BASE, href)

            # 日期: 从 <b> 标签提取
            b_tag = li.find("b")
            pub_date = b_tag.get_text(strip=True) if b_tag else ""

            # 回退: 从 URL 路径提取 (/YYYY/MM-DD/)
            if not pub_date:
                date_match = re.search(r'/(\d{4})/(\d{2}-\d{2})/', full_url)
                if date_match:
                    pub_date = f"{date_match.group(1)}-{date_match.group(2)}"

            if title:
                records.append({
                    "title": title,
                    "url": full_url,
                    "pub_date": pub_date,
                })

        return records

    def _crawl_detail(self, url: str) -> Optional[dict]:
        """获取详情页，提取元数据和 PDF 附件 URL。

        Returns:
            {"title": str, "date": str, "source": str,
             "text": str, "pdf_url": str} 或 None
        """
        html = self._fetch(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        # 标题: 从 <title> 提取
        title_tag = soup.find("title")
        title = ""
        if title_tag:
            title_text = title_tag.get_text(strip=True)
            # 格式: "标题_事故信息_河南省应急管理厅"
            if "_" in title_text:
                title = title_text.split("_")[0].strip()

        # 日期: "时间：YYYY-MM-DD"
        date = ""
        date_match = re.search(r"时间[：:]\s*(\d{4}-\d{2}-\d{2})", html)
        if date_match:
            date = date_match.group(1)

        # 来源
        source = ""
        source_match = re.search(r"来源[：:]\s*(.+?)(?:\s|</)", html)
        if source_match:
            source = source_match.group(1).strip()

        # PDF 附件
        pdf_url = ""
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if ".pdf" in href.lower():
                pdf_url = href if href.startswith("http") else urljoin(HNEM_BASE, href)
                break

        # 正文（极少，详情页主要是元数据+PDF链接）
        text = ""
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n").strip()
            text = re.sub(r"\n{3,}", "\n\n", text)

        return {
            "title": title,
            "date": date,
            "source": source,
            "text": text,
            "pdf_url": pdf_url,
        }

    def _process_pdf(self, pdf_url: str, title: str, pub_date: str) -> tuple[str, str]:
        """下载 PDF 并提取文本。

        Returns:
            (提取的文本, 本地文件路径)，失败返回 ("", "")
        """
        url_hash = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        local_filename = f"{pub_date}_{safe_title}_{url_hash}.pdf"
        local_path = self.pdf_dir / local_filename

        if local_path.exists():
            logger.debug(f"    PDF 已缓存: {local_filename}")
            return extract_pdf_text(local_path), str(local_path)

        try:
            resp = self._client.get(pdf_url)
            if resp.status_code != 200 or len(resp.content) < 100:
                logger.warning(f"    PDF 下载失败: HTTP {resp.status_code}")
                return "", ""

            local_path.write_bytes(resp.content)
            logger.info(f"    PDF 已下载: {local_filename} ({len(resp.content)} bytes)")

            text = extract_pdf_text(local_path)
            if text:
                logger.info(f"    文本提取: {len(text)} 字")
            else:
                logger.warning(f"    文本提取失败")
            return text, str(local_path)

        except Exception as e:
            logger.error(f"    PDF 处理异常: {e}")
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

def crawl_henan_em(delay: float = 1.0) -> dict:
    """同步入口。"""
    c = HenanEMCollector(delay=delay)
    return c.collect()
