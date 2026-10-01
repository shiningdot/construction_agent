"""北京市应急管理局 (yjglj.beijing.gov.cn) 事故调查结果爬虫。

目标: http://yjglj.beijing.gov.cn/col/col4520/index.html

架构:
- 事故数据嵌入在 HTML 的 JS 函数 showNews_XXXXX() 中
- 4 个 JS 数组: urls[] / headers[] / xkxm[] (发布单位) / xkrq[] (PDF附件路径)
- PDF 路径在列表页直接暴露，无需爬详情页即可获取
- 一级筛选：标题宽松关键词 → 候选集（~31篇）
- 二级筛选：PDF全文提取 → classify_v2() → 高置信度自动分类 / 低置信度入审核队列
- 提出的正文留作 content（改前只喂分类、用完即弃，正文因此从未入库）

域名注意：不带 www，使用 HTTP 协议
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

from construction_agent.collector.attachments import attachment_kind
from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── URL 常量 ──
BJEM_BASE = "http://yjglj.beijing.gov.cn"
BJEM_LIST_URL = "http://yjglj.beijing.gov.cn/col/col4520/index.html"


def _placeholder(kind: str, title: str, unit: str, url: str) -> str:
    """附件没提出正文时的占位行。

    **按实际类型写**：本源附件里有 Word（2026-10-01 第二十九轮实测 84 条中
    15 条），占位一律说「PDF」是在骗下游。
    """
    if kind == "office":
        return f"[Office 报告] {title}\n发布单位: {unit}\n文档: {url}"
    if kind == "pdf":
        return f"[PDF 报告] {title}\n发布单位: {unit}\nPDF: {url}"
    # 认不出、或压根没附件：只写标题与单位，不假装有附件
    return f"{title}\n发布单位: {unit}"


# (分类器已迁移至 construction_agent.collector.classifier)

# ═══════════════════════════════════════════════
# 建筑施工事故关键词筛选器（宽松版）
# ═══════════════════════════════════════════════

# 建筑关键词 —— 采用宽松匹配策略
# 因为所有记录都是安全生产事故，标题+发布单位足以做粗筛
# 最终精筛由 MinerU OCR 完成
CONSTRUCTION_KW: list[str] = [
    # 建筑公司特征词（发布单位或标题中出现）
    "建筑", "建设", "城建", "市政", "中铁", "中建", "中交",
    # 施工行为
    "施工", "在建", "拆除", "装修", "爆破",
    "浇筑", "打桩", "挖掘", "回填", "搭设",
    # 建筑结构/设备
    "脚手架", "塔吊", "起重机", "升降机", "基坑", "模板",
    "钢结构", "幕墙", "冷却塔",
    # 建筑/项目类型
    "房屋", "楼房", "厂房", "住宅", "小区", "酒店",
    "商场", "大厦", "体育馆", "宿舍楼",
    "自建房", "违建", "违规建设",
    # 建筑事故类型
    "坍塌", "倒塌", "垮塌", "坠落", "高坠", "塌方",
    "物体打击", "机械伤害", "起重伤害",
    # 基础设施工程
    "桥梁", "桥墩", "桥面", "高架", "路面", "隧道",
    "地铁施工", "轨道交通",
    # 其他
    "排水", "供水", "燃气管道施工", "电力施工",
    "水利建筑工程", "工程",
]

# (is_construction_candidate 已迁移至 classifier 模块)

class BeijingEMCollector(BaseCollector):
    """北京市应急管理局事故调查结果采集器。

    数据流:
        HTML → regex 提取 JS 函数 → 解析 4 个数组 → 标题筛选 → RawAccidentStore

    用法:
        collector = BeijingEMCollector()
        stats = collector.collect()
    """

    def __init__(
        self,
        delay: float = 1.0,
        crawl_details: bool = False,
        # 这个开关同时管两件事：下载 PDF 提正文、拿正文跑 classify_v2。
        # 本源的正文**只在 PDF 里**（列表页那条 HTML 只有标题和附件链接），
        # 所以关掉它等于关掉正文来源，不只是关分类。
        enable_pdf_classify: bool = True,
        data_dir: Optional[Path] = None,
    ):
        self._delay = delay
        self._crawl_details = crawl_details
        self._enable_pdf_classify = enable_pdf_classify
        # `data_dir=None` 时落 `settings.DATA_DIR/<源名>`（旧行为）。在线采集
        # 传一个临时目录进来，`data/<源名>/` 就不再产生——采集产出自 M2 起
        # 只落工作库的 `raw_accidents` 表（蓝图 01 §3.4）。
        self.data_dir = data_dir or (settings.DATA_DIR / "beijing_em")
        self.data_dir.mkdir(parents=True, exist_ok=True)

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
        self._total_records: int = 0
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
        pass  # 单页无分页，忽略

    @property
    def end_page(self) -> int:
        return 1

    @end_page.setter
    def end_page(self, value: int):
        pass  # 单页无分页，忽略

    def source_name(self) -> str:
        return "beijing_em"

    @property
    def collected_count(self) -> int:
        return self._collected

    def collect(self) -> list[RawAccidentText]:
        """执行采集，返回统计字典。"""
        t_start = time.time()

        logger.info("开始采集北京市应急管理局事故调查结果...")

        # Step 1: 获取列表页 HTML
        html = self._fetch(BJEM_LIST_URL)
        if not html:
            logger.error(f"无法访问: {BJEM_LIST_URL}")
            return self._make_stats(0)

        # Step 2: 从 JS 函数中提取所有记录
        records = self._extract_js_records(html)
        self._total_records = len(records)
        logger.info(f"JS 数组解析: 提取 {len(records)} 条记录")

        # Step 3: 标题初筛 + PDF 下载 + 全文分类
        batch_items = []
        for record in records:
            title = record["title"]
            unit = record["unit"]
            pdf_path = record["pdf_path"]
            article_url = record["article_url"]

            # 一级筛选：标题宽松关键词
            is_ok, reason = is_construction_candidate(title)
            if not is_ok:
                self._filtered_out += 1
                logger.debug(f"  过滤: [{title[:60]}] - {reason}")
                continue

            logger.info(f"  候选: [{title[:60]}] - {reason}")

            # 构建附件完整 URL，并按后缀判类型。**这个源的附件不全是 PDF**
            # （2026-10-01 第二十九轮实测列表页 84 条 `xkrq[i]`：`.pdf` 69、
            # `.docx` 10、`.doc` 5）。原先那 15 条 Word 原样写成
            # `"pdf_urls": [...]`，经 `executor._ASSET_KEYS` 按键名登记成
            # `asset_type='pdf'`，阶段② 会拿 PyMuPDF 去解 Word。
            # 判据与 `gd_em` 共用 `collector/attachments.py`。
            asset_url = self._build_pdf_url(pdf_path)
            kind = attachment_kind(asset_url)
            if asset_url and not kind:
                # 认不出的一律不收（与 `_assets_from`「不认识的不猜」同向），
                # 但**不静默**——站点改版新增了别的附件类型，得有人知道。
                logger.warning(f"    附件类型认不出，不入产出: {asset_url}")

            # ── 二级筛选：PDF 全文分类（可选）──
            classification = "pending_review"
            classification_reason = "待人工审核"
            classification_confidence = "low"
            item_status = "review"

            # 提出来的正文要留到下面构建 item 用。改之前这里只把 `pdf_text`
            # 喂给 `classify_v2`，分类完随作用域丢掉，item 的 content 写死成
            # `[PDF 报告] 标题`——**这个源的正文因此从来没进过库**：主库 12 条
            # beijing_em 记录的 raw_text 全是 106–206 字的占位，最长那条
            # （206 字）也只是把一长串承包单位名字塞进了标题。
            pdf_text = ""
            if self._enable_pdf_classify and asset_url:
                # 下载附件（PDF 与 Word 都交给它，`fitz` 按内容嗅探）
                pdf_text = self._download_and_extract(asset_url)
                if pdf_text:
                    result = classify_v2(title, pdf_text)
                    logger.info(
                        f"    分类: is_const={result['is_construction']}, "
                        f"confidence={result['confidence']}, "
                        f"reason={result['reason']}"
                    )

                    if result["is_construction"] and result["confidence"] == "high":
                        # 高置信度建筑事故 → 直接入库
                        classification = "auto_construction"
                        classification_reason = result["reason"]
                        classification_confidence = "high"
                        item_status = "new"
                    elif not result["is_construction"] and result["confidence"] == "high":
                        # 高置信度非建筑 → 丢弃
                        self._filtered_out += 1
                        logger.info(f"    排除: {result['reason']}")
                        continue
                    else:
                        # 低置信度 → 入审核队列
                        classification = "pending_review"
                        classification_reason = result["reason"]
                        classification_confidence = "low"
                        item_status = "review"
                else:
                    # PDF 下载失败 → 入审核队列
                    classification_reason = "PDF下载或文本提取失败"
            else:
                # 未启用 PDF 分类 → 全部入审核队列
                pass

            # 构建记录
            item = {
                "title": title,
                # 正文已在 PDF 里提出 → 直接当正文用；提不出（扫描件、404、
                # 或压根没开下载）才退回占位——这时由 `needs_ocr` 标出来，
                # 下游才知道该走 OCR 而不是以为正文已经拿到。
                "content": (pdf_text or
                            _placeholder(kind, title, unit, asset_url)),
                "source_url": article_url or BJEM_LIST_URL,
                "source_name": "beijing_em",
                "publish_date": "",
                "status": item_status,
                # 按判出来的类型分家：`executor._ASSET_KEYS` 按键名认类型，
                # Word 放进 `pdf_urls` 就登记成 `asset_type='pdf'`。
                # 认不出/为空时两个键都是空列表——不写空串进去。
                "pdf_urls": [asset_url] if kind == "pdf" else [],
                "office_urls": [asset_url] if kind == "office" else [],
                # 「有附件 且 正文没提出来」——全项目约定（§2.5.13）。加
                # `bool(kind)` 这一项：`xkrq` 数组比 `urls` 短时 `pdf_path`
                # 为空，那是**压根没有附件**，标成「要去 OCR」是虚报。
                "needs_ocr": bool(kind) and not pdf_text,
                "issuing_authority": unit,
                "pdf_path": pdf_path,
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

        # 保存
        if batch_items:
            self.raw_store.add_batch(batch_items)
            logger.info(f"原始缓存: 新增 {len(batch_items)} 条建筑事故候选")

        self.raw_store._save()
        return self._make_stats(time.time() - t_start)

    def _fetch(self, url: str) -> Optional[str]:
        """获取 HTML，带重试。"""
        for attempt in range(3):
            try:
                resp = self._client.get(url)
                if resp.status_code == 200:
                    # 北京应急管理局网站编码可能是 GBK 或 UTF-8
                    return resp.text
                elif resp.status_code == 404:
                    return None
            except Exception as e:
                logger.debug(f"请求失败(尝试{attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2)
        return None

    def _extract_js_records(self, html: str) -> list[dict]:
        """从 HTML 的 JavaScript 函数中提取事故记录列表。

        页面结构：
            function showNews_XXXXX(start_num) {
                urls[i] = '/art/...';
                headers[i] = "标题";
                xkxm[i] = '发布单位';
                xkrq[i] = '/attach/0/xxx.pdf';  // PDF附件路径
            }

        Returns:
            [{"title": str, "unit": str, "pdf_path": str, "article_url": str}, ...]
        """
        # Step 1: 找到 showNews_XXXXX 函数
        func_match = re.search(r'function\s+showNews_\d+\s*\([^)]*\)\s*\{', html)
        if not func_match:
            logger.error("未找到 showNews_XXXXX 函数")
            return []

        # 通过大括号匹配找到完整函数体
        start = func_match.start()
        depth = 0
        func_body = ""
        for j in range(start, len(html)):
            if html[j] == '{':
                depth += 1
            elif html[j] == '}':
                depth -= 1
                if depth == 0:
                    func_body = html[start:j + 1]
                    break

        if not func_body:
            logger.error("无法提取完整函数体")
            return []

        logger.debug(f"JS 函数体: {len(func_body)} 字符")

        # Step 2: 从函数体中提取数组元素
        # 格式:
        #   urls[i] = '/art/2026/6/12/art_4520_760.html';
        #   headers[i] = "事故标题";
        #   xkxm[i] = '发布单位';
        #   xkrq[i] = '/attach/0/xxx.pdf';

        urls = re.findall(r"urls\[i\]\s*=\s*'([^']*)'", func_body)
        headers = re.findall(r'headers\[i\]\s*=\s*"([^"]*)"', func_body)
        xkxm_vals = re.findall(r"xkxm\[i\]\s*=\s*'([^']*)'", func_body)
        xkrq_vals = re.findall(r"xkrq\[i\]\s*=\s*'([^']*)'", func_body)

        logger.debug(
            f"JS 数组: urls={len(urls)}, headers={len(headers)}, "
            f"xkxm={len(xkxm_vals)}, xkrq={len(xkrq_vals)}"
        )

        # Step 3: 按索引合并为记录
        max_len = max(len(urls), len(headers), len(xkrq_vals))
        records = []
        for i in range(max_len):
            title = headers[i] if i < len(headers) else ""
            unit = xkxm_vals[i] if i < len(xkxm_vals) else ""
            pdf_path = xkrq_vals[i] if i < len(xkrq_vals) else ""
            article_url_raw = urls[i] if i < len(urls) else ""

            # 跳过空记录
            if not title:
                continue

            # 构建文章详情页完整 URL
            article_url = ""
            if article_url_raw:
                article_url = urljoin(BJEM_BASE, article_url_raw)

            records.append({
                "title": title.strip(),
                "unit": unit.strip(),
                "pdf_path": pdf_path.strip(),
                "article_url": article_url,
            })

        return records

    def _download_and_extract(self, url: str) -> str:
        """下载附件并提取文本。

        交给 `extract_pdf_text` 就够——它背后的 `fitz` 同时认 PDF 与 OOXML。
        这个源的附件里有 Word（第二十九轮实测 84 条中 15 条），`.docx` 提得出
        正文，老 `.doc`（OLE2）提不出；这个差别不在这里判断，直接交给它试。
        """
        if not url:
            return ""

        # 缓存到本地避免重复下载。**后缀按 URL 取**，不再写死 `.pdf`：
        # `fitz` 是按内容嗅探的，写死不影响提取，但缓存目录里一份 Word 顶着
        # `.pdf` 的名字，人工排查时是误导（与 `gd_em` 对齐）。
        suffix = Path(url.split("?")[0]).suffix.lower() or ".bin"
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        cache_path = self.data_dir / f"{url_hash}{suffix}"

        if not cache_path.exists():
            try:
                resp = self._client.get(url)
                if resp.status_code != 200 or len(resp.content) < 100:
                    logger.debug(f"    附件下载失败: HTTP {resp.status_code}")
                    return ""
                cache_path.write_bytes(resp.content)
            except Exception as e:  # noqa: BLE001 —— 网络异常不该打断整轮采集
                logger.debug(f"    附件下载异常: {e}")
                return ""

        return extract_pdf_text(cache_path)

    def _fetch_detail_date(self, article_url: str) -> str:
        """可选：爬取详情页获取发布日期。"""
        html = self._fetch(article_url)
        if not html:
            return ""

        # 从页面中提取日期
        m = re.search(r'(\d{4}-\d{2}-\d{2})', html)
        if m:
            return m.group(1)

        return ""

    def _build_pdf_url(self, pdf_path: str) -> str:
        """把列表页 JS 里那条附件路径补成完整 URL。

        支持格式:
        - /attach/0/xxx.pdf → http://yjglj.beijing.gov.cn/attach/0/xxx.pdf
        - http://... 绝对 URL → 原样返回

        这里**只补 URL，不判类型**——类型由 `collector/attachments.py` 的
        `attachment_kind` 按后缀判（站上确实有 `.docx`/`.doc`，见 `collect()`）。
        函数名里的 `pdf` 是历史包袱，改名会牵动调用方与 `pdf_path` 那个 extra
        键，本轮不动。
        """
        if not pdf_path:
            return ""

        # 已经是绝对 URL
        if pdf_path.startswith("http://") or pdf_path.startswith("https://"):
            return pdf_path

        # 相对路径
        return urljoin(BJEM_BASE, pdf_path)

    def _make_stats(self, duration: float) -> dict:
        cache_stats = self.raw_store.stats()
        return {
            "total_records": self._total_records,
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

def crawl_beijing_em(delay: float = 1.0) -> dict:
    """同步入口。"""
    c = BeijingEMCollector(delay=delay)
    return c.collect()
