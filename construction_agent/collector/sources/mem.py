"""应急管理部 (mem.gov.cn) 特别重大事故调查报告爬虫。

目标: https://www.mem.gov.cn/gk/sgcc/tbzdsgdcbg/

架构（复用 safehoo.py 模式）:
- httpx + BeautifulSoup 负责 HTTP 和 HTML 解析
- 三级页面结构: 主页 → 年份目录 → 事故报告列表
- 3 种 URL 模式（历经多次 CMS 迁移）:
  旧CMS: ./{YYYYMM}/t{YYYYMMDD}_{id}.shtml  (2003-2015, 2020)
  PDF直链: ./{YYYYMM}/P020XXXXX.pdf          (2016-2018, 2022)
  新CMS: ../../../zfxxgkpt/fdzdgknr/...      (2021, 2023-2025)
- 两级关键词筛选: 建筑词 + 排除词
- RawAccidentStore 独立缓存 (data/raw_accidents.json)
- 提取 PDF 下载链接 → 后续可调用 MinerU OCR
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from construction_agent.collector.base import (BaseCollector, CollectResult,
                                               RawAccidentText)
from construction_agent.collector.pdf_text import extract_pdf_text
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── URL 常量 ──
MEM_BASE = "https://www.mem.gov.cn"
MEM_LIST_URL = "https://www.mem.gov.cn/gk/sgcc/tbzdsgdcbg/"

# ═══════════════════════════════════════════════
# 建筑施工事故关键词筛选器（适配 MEM 场景）
# ═══════════════════════════════════════════════

# 建筑相关关键词（至少匹配 1 个）
CONSTRUCTION_KW: list[str] = [
    # 核心施工词汇
    "施工平台", "施工", "自建房", "建筑", "在建",
    # 建筑结构/设备
    "脚手架", "塔吊", "起重机", "升降机", "基坑", "模板",
    "冷却塔", "钢结构", "幕墙",
    # 建筑/项目类型
    "房屋", "楼房", "厂房", "住宅", "小区", "校区",
    "酒店", "商场", "大厦",
    # 施工活动
    "违规建设", "违法建筑", "违建", "装修", "拆除",
    "爆破", "浇筑", "打桩",
    # 事故类型（需结合其他词判断）
    "坍塌", "倒塌", "垮塌", "坠落", "高坠", "塌方",
    # 桥梁/道路工程
    "桥梁", "桥墩", "桥面", "高架", "路面",
    # 住建相关
    "住建局", "住建厅",
]

# 排除关键词（匹配任一则过滤为非建筑事故）
EXCLUDE_KW: list[str] = [
    # 矿山/煤矿
    "煤矿", "矿山", "矿井", "瓦斯", "煤与瓦斯", "透水",
    "煤尘", "采煤",
    # 化工/危化
    "化工厂", "化学品", "危险品仓库", "烟花爆竹",
    "石化", "炼油", "油罐", "民爆",
    # 交通（非施工类）
    "道路交通事故", "高速公路上", "大巴侧翻", "客车侧翻",
    "列车脱轨", "铁路事故", "航空器", "飞机失事",
    # 火灾
    "火灾事故",
    # 自然灾害
    "暴雨灾害", "洪水", "地震", "台风",
    # 企业名排除
    "煤业公司", "煤业集团", "矿业公司", "矿业集团",
    "煤矿安全监察",
]


def is_construction_accident(title: str, content: str = "") -> tuple[bool, str]:
    """判断是否为建筑施工事故报告。

    筛选策略（适配 MEM 场景，特别重大事故以煤矿/交通/火灾为主）：
    1. 先检测建筑关键词（≥1 个即进入候选）
    2. 检查排除词 → 命中则过滤
    3. "坍塌/倒塌/垮塌" + 建筑结构词 → 通过（如"自建房倒塌"）
    4. "坍塌/倒塌/垮塌" + 煤矿企业 → 过滤

    Returns:
        (是否建筑事故, 判定原因)
    """
    text = (title + " " + (content or "")[:2000]).lower()

    # Phase 1: 检测建筑关键词
    matched = []
    for kw in CONSTRUCTION_KW:
        if kw in text:
            matched.append(kw)
            if len(matched) >= 5:
                break

    # Phase 2: 检查排除词（排除词优先级高于建筑词）
    for kw in EXCLUDE_KW:
        if kw in text:
            if matched:
                return False, f"已匹配{len(matched)}个建筑词'{','.join(matched[:3])}'但排除词命中'{kw}'"
            else:
                return False, f"无建筑词且排除'{kw}'"

    # Phase 3: 有建筑关键词 → 通过
    if matched:
        return True, f"建筑关键词({len(matched)}个): {', '.join(matched[:5])}"

    # Phase 4: 兜底 — 标题含"坍塌/倒塌/垮塌"且含建筑结构词 → 通过
    collapse_kw = ["坍塌", "倒塌", "垮塌"]
    structure_kw = ["楼", "房", "屋", "墙", "梁", "柱", "顶", "棚", "板"]
    if any(kw in title for kw in collapse_kw) and any(kw in title for kw in structure_kw):
        return True, "坍塌类关键词+建筑结构（推测为建筑事故）"

    return False, "无建筑关键词"


# ═══════════════════════════════════════════════
# 爬虫核心
# ═══════════════════════════════════════════════

class MemCollector(BaseCollector):
    """应急管理部特别重大事故调查报告采集器。

    数据流:
        主页 → 年份目录列表 → 各年份报告列表 → 详情页/PDF → RawAccidentStore

    用法:
        collector = MemCollector(start_year=2025, end_year=2005)
        stats = collector.collect()
        # 原始数据在 data/raw_accidents.json
    """

    def __init__(
        self,
        start_year: int = 2025,
        end_year: int = 2005,
        delay: float = 2.0,
        data_dir: Optional[Path] = None,
    ):
        self.start_year = start_year
        self.end_year = end_year
        self._delay = delay
        # `data_dir=None` 时落 `settings.DATA_DIR/<源名>`（旧行为）。在线采集
        # 传一个临时目录进来，`data/<源名>/` 就不再产生——采集产出自 M2 起
        # 只落工作库的 `raw_accidents` 表（蓝图 01 §3.4）。
        self.data_dir = data_dir or (settings.DATA_DIR / "mem")
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

        # 原始数据缓存（复用 safehoo 同款存储）
        self.raw_store = RawAccidentStore()

        # 状态统计
        self._collected: int = 0
        self._filtered_out: int = 0
        self._errors: int = 0
        self._year_count: int = 0

    # ── 兼容 Web GUI 的 start_page/end_page 参数 ──
    # Web GUI 通用采集参数使用 start_page/end_page 命名，
    # 但对 MEM 采集器, 这两个参数映射到年份范围
    @property
    def start_page(self) -> int:
        return self.start_year

    @start_page.setter
    def start_page(self, value: int):
        if value > 2000:  # 年份（如 2025）
            self.start_year = value
        # 否则忽略（可能是页数而非年份）

    @property
    def end_page(self) -> int:
        return self.end_year

    @end_page.setter
    def end_page(self, value: int):
        if value > 2000:  # 年份（如 2005）
            self.end_year = value
        # 否则忽略（可能是页数而非年份）

    def source_name(self) -> str:
        return "mem"

    @property
    def collected_count(self) -> int:
        return self._collected

    def collect(self) -> CollectResult:
        """执行采集，返回统计字典。"""
        t_start = time.time()

        # Step 1: 获取主页，解析年份目录
        logger.info("开始采集 MEM 特别重大事故调查报告...")
        main_html = self._fetch(MEM_LIST_URL)
        if not main_html:
            logger.error(f"无法访问 MEM 主页: {MEM_LIST_URL}")
            return self._make_stats(0)

        year_links = self._get_year_links(main_html)
        logger.info(f"主页解析: 发现 {len(year_links)} 个年份目录 ({year_links[0][0]}-{year_links[-1][0]})")

        batch_items = []

        for year, year_url in year_links:
            if year > self.start_year or year < self.end_year:
                continue

            logger.info(f"--- 采集 {year} 年: {year_url} ---")
            self._year_count += 1
            time.sleep(self._delay)

            year_html = self._fetch(year_url)
            if not year_html:
                logger.warning(f"{year}年页面请求失败")
                continue

            # Step 2: 解析年份页 → 提取事故报告列表
            reports = self._parse_year_page(year_html, year, year_url)
            if not reports:
                logger.info(f"  {year}年: 无建筑相关报告")
                continue

            logger.info(f"  {year}年: {len(reports)}篇候选报告（已预筛选）")

            # Step 3: 逐个处理报告
            for report in reports:
                try:
                    if report["link_type"] == "pdf":
                        item = self._handle_pdf_link(report)
                    else:
                        item = self._crawl_detail_article(report)
                        time.sleep(self._delay)  # 详情页间延迟

                    if item:
                        batch_items.append(item)
                        self._collected += 1
                        logger.info(f"    ✅ [{report['title'][:60]}]")

                        if self._collected % 3 == 0:
                            self.raw_store.add_batch(batch_items)
                            batch_items = []
                    else:
                        self._filtered_out += 1

                except Exception as e:
                    self._errors += 1
                    logger.debug(f"    报告处理异常: {e}")
                    continue

        # 保存剩余
        if batch_items:
            self.raw_store.add_batch(batch_items)

        return self._make_stats(time.time() - t_start)

    def _fetch(self, url: str) -> Optional[str]:
        """获取 HTML，带重试。"""
        for attempt in range(3):
            try:
                resp = self._client.get(url)
                if resp.status_code == 200:
                    return resp.text
                elif resp.status_code == 404:
                    return None
            except Exception as e:
                logger.debug(f"请求失败(尝试{attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2)
        return None

    def _get_year_links(self, html: str) -> list[tuple[int, str]]:
        """从主页解析年份目录链接。

        主页中有年份链接如: <a href="./2025dcbg_6344/">2025</a>
        年份目录命名不规则（有的带后缀，如 _6344, tbzdsgcc）
        返回 [(年份, 完整URL), ...], 按年份降序排列
        """
        soup = BeautifulSoup(html, "lxml")
        results: list[tuple[int, str]] = []

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            text = a_tag.get_text(strip=True)

            # 文本必须是纯数字年份
            if not text.isdigit():
                continue
            year = int(text)
            if year < 2000 or year > 2030:
                continue

            # 排除外链和 JS 链接
            if any(skip in href.lower() for skip in
                   ["javascript", "http://", "https://", "mailto:"]):
                continue

            full_url = urljoin(MEM_LIST_URL, href)
            results.append((year, full_url))

        # 去重并按年份降序
        seen = set()
        unique: list[tuple[int, str]] = []
        for year, url in results:
            if year not in seen:
                seen.add(year)
                unique.append((year, url))

        unique.sort(key=lambda x: x[0], reverse=True)
        return unique

    def _parse_year_page(
        self, html: str, year: int, year_url: str
    ) -> list[dict]:
        """解析年份页，返回事故报告列表。

        自动识别 3 种 URL 模式:
        - 新CMS: ../../../zfxxgkpt/fdzdgknr/{YYYYMM}/t{YYYYMMDD}_{id}.shtml
        - 旧CMS: ./{YYYYMM}/t{YYYYMMDD}_{id}.shtml
        - PDF直链: ./{YYYYMM}/P020XXXXX.pdf

        返回 [{"title": str, "url": str, "link_type": str, "date_str": str, "year": int}, ...]
        """
        soup = BeautifulSoup(html, "lxml")
        reports: list[dict] = []

        # 导航链接文本（排除项）
        nav_texts = {
            "首页", "机构", "新闻", "公开", "服务", "互动", "党建",
            "应急科普", "社会救援服务", "政府信息公开", "事故及灾害查处",
            "调查报告", "原国家安全监管总局文件", "应急管理部行政许可",
            "应急管理部电子证照", "国家政务服务投诉与建议", "网站地图",
            "网站声明", "联系我们",
        }

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            title = a_tag.get_text(strip=True)

            # 跳过空链接和无效链接
            if not title or len(title) < 8:
                continue

            # 跳过导航链接
            if title in nav_texts or title.startswith("原国家"):
                continue

            # 跳过 javascript: 和非 mem.gov.cn 的外链
            if href.startswith("javascript"):
                continue
            if href.startswith("http") and "mem.gov.cn" not in href:
                continue

            # 从标题末尾提取日期（格式: "标题YYYY-MM-DD"）
            date_str = ""
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})\s*$', title)
            if date_match:
                date_str = date_match.group(1)
                title = title[:date_match.start()].strip()

            # 判断链接类型
            if href.lower().endswith(".pdf"):
                link_type = "pdf"
            elif "zfxxgkpt/fdzdgknr" in href:
                link_type = "new_cms"
            elif re.search(r'/\d{6}/t\d{8}_\d+\.shtml', href):
                link_type = "old_cms"
            elif ".shtml" in href:
                link_type = "other_shtml"
            else:
                continue  # 跳过非文章链接

            # 解析完整 URL
            full_url = urljoin(year_url, href)

            # 标题预筛选（一级筛选）
            is_ok, reason = is_construction_accident(title, "")
            if not is_ok:
                logger.debug(f"    预筛选过滤: [{title[:60]}] - {reason}")
                continue

            logger.debug(f"    预筛选通过: [{title[:60]}] - {reason}")

            reports.append({
                "title": title,
                "url": full_url,
                "link_type": link_type,
                "date_str": date_str,
                "year": year,
            })

        return reports

    def _crawl_detail_article(self, report: dict) -> Optional[dict]:
        """爬取 HTML 详情页，提取正文和 PDF 链接。

        MEM 报告的特殊性：HTML 页面通常只有摘要/标题，
        完整调查报告以 PDF 附件形式提供。
        因此主要目标是提取 PDF 链接和元数据。

        Args:
            report: {"title", "url", "link_type", "date_str", "year"}

        Returns:
            与 RawAccidentStore 兼容的 dict，或 None（过滤掉）
        """
        url = report["url"]
        title = report["title"]
        date_str = report["date_str"]
        link_type = report["link_type"]

        html = self._fetch(url)
        if not html:
            logger.debug(f"    详情页请求失败: {url}")
            return None

        soup = BeautifulSoup(html, "lxml")

        # ── 提取正文（尝试多种选择器，但 MEM 报告正文通常在 PDF 中）──
        content = ""
        if link_type == "old_cms":
            # 旧 CMS: .zhenwen_neir（正文内容区域）
            content_selectors = [
                ".zhenwen_neir", ".article-content", ".TRS_Editor",
                "#content", ".content", ".text",
            ]
        else:
            # 新 CMS: .scy_main_detail, #content, .zw-file2
            content_selectors = [
                ".scy_main_detail", ".scy_box_nei",
                "#content", ".article-content", ".TRS_Editor",
                ".Custom_UnionStyle", ".pages_content",
            ]

        for selector in content_selectors:
            tag = soup.select_one(selector)
            if tag:
                for noise in tag.find_all(["script", "style", "iframe", "nav"]):
                    noise.decompose()
                text = tag.get_text(separator="\n", strip=True)
                if len(text) > 30:  # MEM 页面正文很短也可以接受
                    content = text
                    break

        # 兜底: 从 <title> 和 meta 提取描述
        if not content:
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                content = meta_desc["content"]

        # ── 提取 PDF 下载链接 ──
        pdf_urls = self._extract_pdf_links(soup, url)

        # ── 正文二次筛选 ──
        is_ok, reason = is_construction_accident(title, content)
        if not is_ok:
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            self.raw_store.mark_filtered(url_hash, reason)
            logger.debug(f"    正文二次筛选过滤: [{title[:60]}] - {reason}")
            return None

        # ── 使用页面 <title> 作为最终标题 ──
        page_title = title
        title_tag = soup.find("title")
        if title_tag:
            t = title_tag.get_text(strip=True)
            # 清理站点后缀
            t = re.sub(r'[-_]\s*(?:中华人民共和国应急管理部|应急管理部).*$', '', t)
            if len(t) > 5:
                page_title = t

        # ── 提取日期 ──
        if not date_str:
            # 尝试 .time 类
            time_tag = soup.select_one(".time")
            if time_tag:
                date_str = time_tag.get_text(strip=True)
            if not date_str:
                date_str = self._extract_date(soup, content)

        publish_date = self._normalize_date(date_str)

        # ── 正文改取 PDF 全文 ──
        # 放在二次筛选**之后**是有意的：筛选口径保持与改之前逐字相同
        # （那时也只拿 HTML 摘要判），否则正文一变长，关键词命中率跟着变，
        # 同一个源两次采样的取舍会漂移，且无从对账。
        #
        # 实测 HTML 页上的正文是摘要或 meta 描述，中位 248 字；而 51 页的
        # 报告 PDF 提出 37,618 字。抽到的 HTML 内容留着当兜底——解析失败
        # （扫描件、下载失败）时它至少还是一点东西。命中即停，不多下。
        pdf_parsed = False
        for pdf_url in pdf_urls:
            pdf_text = self._fetch_pdf_text(pdf_url)
            if pdf_text and len(pdf_text) > len(content):
                content = pdf_text
                pdf_parsed = True
                break

        # ── 构建内容文本 ──
        # 如果有 PDF，标题+PDF链接作为主要信息
        if pdf_urls and not content:
            content = f"[PDF 报告] {page_title}"
        elif not content:
            content = page_title

        return {
            "title": page_title,
            "content": content[:50000],
            "source_url": url,
            "source_name": "mem",
            "publish_date": publish_date,
            "status": "new",
            "pdf_urls": pdf_urls,
            # 有 PDF **且没解析出来**才需要 OCR
            "needs_ocr": bool(pdf_urls) and not pdf_parsed,
        }

    def _fetch_pdf_text(self, pdf_url: str) -> str:
        """下载 PDF 附件并**就地提出正文**；提不出返回空串。

        2026-10-01 实测：应急部这些调查报告 PDF 都是**文字版**，本地
        PyMuPDF 一次到位——欣佳酒店 51 页提出 37,618 字、商洛桥梁 39 页
        提出 28,315 字。不需要 OCR、不需要 token、不需要联网。

        改之前这里两处都写着「等 MinerU」：

            `_crawl_detail_article`  抽到 PDF 链接就记 `needs_ocr=True`，从不下载
            `_handle_pdf_link`       注释直说「不做正文提取」

        而 MinerU 这个依赖**不存在**——`settings.MINERU_API_TOKEN` 没这个
        字段，`construction_agent.input.pdf_processor` 那个目录是空的。
        于是应急部的报告入库时正文只有 HTML 页上的摘要，实测中位 **248 字**
        （主库 3 条 `mem` 记录的 raw_text 分别是 50 / 134 / 3,572 字），
        而 PDF 里躺着三万字的全文。

        落盘再提，与 `MohurdCollector._handle_pdf` 同一套路：`extract_pdf_text`
        收的是文件路径，而且原件留着可查——真要 OCR 时下游拿得到那份 PDF。
        """
        try:
            resp = self._client.get(pdf_url)
        except Exception as e:  # noqa: BLE001 —— 下载失败只降级，不掀整轮
            logger.debug(f"    PDF 下载失败: {pdf_url} — {e}")
            return ""

        if resp.status_code != 200:
            logger.debug(f"    PDF HTTP {resp.status_code}: {pdf_url}")
            return ""

        # 文件名从 URL 路径取，去掉 query 与非文件名字符
        stem = Path(unquote(urlparse(pdf_url).path)).stem or "attachment"
        path = self.data_dir / (re.sub(r'[^\w\-.]', '_', stem)[:50] + ".pdf")
        try:
            path.write_bytes(resp.content)
        except OSError as e:
            logger.debug(f"    PDF 落盘失败: {path} — {e}")
            return ""

        text = extract_pdf_text(path)
        if not text:
            logger.info(f"    PDF 提不出正文（扫描件？）: {pdf_url}")
        return text

    def _handle_pdf_link(self, report: dict) -> Optional[dict]:
        """处理 PDF 直链：下载并就地把正文提出来。

        改之前这里只记一条 `[PDF 待 OCR] 标题 + 地址`——正文全靠一个不存在
        的 MinerU。现在提得出来就直接用，提不出来才留待 OCR 的标记。

        Args:
            report: {"title", "url", "link_type", "date_str", "year"}
        """
        title = report["title"]
        url = report["url"]
        date_str = report["date_str"]

        # 关键词再次筛选（兜底）。**筛在下载之前**——省流量，而且筛的
        # 口径与改之前逐字相同（那时也是只拿标题判）。
        is_ok, reason = is_construction_accident(title, "")
        if not is_ok:
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            self.raw_store.mark_filtered(url_hash, reason)
            return None

        publish_date = self._normalize_date(date_str)

        pdf_text = self._fetch_pdf_text(url)
        if pdf_text:
            logger.info(f"    PDF 正文 {len(pdf_text):,} 字: {title[:50]}")
            return {
                "title": title,
                "content": pdf_text[:50000],
                "source_url": url,
                "source_name": "mem",
                "publish_date": publish_date,
                "status": "new",
                "pdf_urls": [url],
                "needs_ocr": False,
            }

        return {
            "title": title,
            "content": f"[PDF 待 OCR] {title}\nPDF 地址: {url}",
            "source_url": url,
            "source_name": "mem",
            "publish_date": publish_date,
            "status": "new",
            "pdf_urls": [url],
            "needs_ocr": True,
        }

    def _extract_pdf_links(self, soup: BeautifulSoup, page_url: str) -> list[str]:
        """从详情页中提取所有 PDF 下载链接。"""
        pdf_urls: list[str] = []

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if href.lower().endswith(".pdf"):
                full_url = urljoin(page_url, href)
                pdf_urls.append(full_url)

        # 保持顺序去重
        return list(dict.fromkeys(pdf_urls))

    @staticmethod
    def _extract_date(soup: BeautifulSoup, content: str) -> str:
        """从页面内容和元数据中提取发布日期。"""
        # 方法1: 正文开头搜索 "YYYY年MM月DD日"
        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', content[:1000])
        if m:
            return f"{m.group(1)}年{m.group(2)}月{m.group(3)}日"

        # 方法2: 元数据区域
        for selector in [".article-info", ".info", ".time", ".date",
                         ".source", ".article-source", ".sp_time"]:
            tag = soup.select_one(selector)
            if tag:
                text = tag.get_text(strip=True)
                m = re.search(
                    r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})?[日]?', text
                )
                if m:
                    if m.group(3):
                        return f"{m.group(1)}年{m.group(2)}月{m.group(3)}日"
                    return f"{m.group(1)}年{m.group(2)}月"

        return ""

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """标准化日期格式为 YYYY-MM-DD。"""
        if not date_str:
            return ""

        # 已有标准格式
        m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', date_str)
        if m:
            return date_str

        # 中文格式
        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日?', date_str)
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 2000 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                    return f"{y}-{mo:02d}-{d:02d}"
            except ValueError:
                pass

        return date_str

    def _make_stats(self, duration: float) -> dict:
        """生成采集统计。"""
        cache_stats = self.raw_store.stats()
        return {
            "total_crawled": self._collected,
            "filtered_out": self._filtered_out,
            "errors": self._errors,
            "cached_total": cache_stats["total"],
            "cached_new": cache_stats["new"],
            "years_processed": self._year_count,
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

def crawl_mem(
    start_year: int = 2025,
    end_year: int = 2005,
    delay: float = 2.0,
) -> dict:
    """同步入口，返回统计字典（与 safehoo.crawl_safehoo() 接口一致）。"""
    c = MemCollector(
        start_year=start_year,
        end_year=end_year,
        delay=delay,
    )
    return c.collect()
