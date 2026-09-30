"""安全管理网 (safehoo.com) 建筑事故新闻爬虫。

目标: https://www.safehoo.com/NewsSpecial/Build/

架构:
- scrapling Fetcher 负责 HTTP 请求（带重试/UA轮换）
- BeautifulSoup 负责 HTML 解析（稳定可靠）
- is_construction_accident() 关键词筛选器
- RawAccidentStore 独立缓存 (data/raw_accidents.json)
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import date as dt_date
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── URL 常量 ──
SAFEHOO_BUILD_LIST = "https://www.safehoo.com/NewsSpecial/Build/"
SAFEHOO_BASE = "https://www.safehoo.com"


def _list_page_url(page: int) -> str:
    """列表页 URL: 第1页=Index.shtml, 第N页=List_N.shtml"""
    if page <= 1:
        return f"{SAFEHOO_BUILD_LIST}Index.shtml"
    return f"{SAFEHOO_BUILD_LIST}List_{page}.shtml"


# ═══════════════════════════════════════════════
# 建筑施工事故关键词筛选器
# ═══════════════════════════════════════════════

# 建筑相关关键词（至少匹配1个）
CONSTRUCTION_KW: list[str] = [
    # 核心建筑词汇
    "建筑", "施工", "工地", "工程", "在建", "承建",
    # 建筑结构/部位
    "脚手架", "塔吊", "起重机", "升降机", "龙门吊", "吊篮", "吊装", "吊车",
    "基坑", "沟槽", "模板", "支护", "边坡", "桩基", "围挡",
    "楼板", "墙体", "幕墙", "钢构", "屋面", "地下室", "隧道",
    "高架桥", "立交桥", "路面塌陷",
    # 施工活动
    "拆除", "装修", "浇筑", "打桩", "挖掘", "回填", "搭设", "拆卸",
    "爆破", "灌浆", "动火", "电焊",
    # 参与方
    "施工单位", "监理单位", "建设方", "分包", "项目部",
    "建筑工人", "施工人员", "架子工", "钢筋工", "工人",
    # 项目/建筑类型
    "楼盘", "小区", "商品房", "安置房", "住宅楼", "商业楼",
    "厂房", "车间", "工业园", "产业园", "别墅",
    "市政工程", "道路工程", "桥梁工程", "水利工程", "地铁施工",
    "房屋", "自建房", "自建", "骑楼", "预制板", "工棚",
    "板房", "屋顶", "顶棚", "雨棚", "遮阳棚",
    # 事故类型关键词
    "坍塌", "倒塌", "垮塌", "坠落", "高坠", "塌方", "塌陷",
    "触电", "物体打击", "机械伤害", "起重伤害",
    # 住建相关
    "住建局", "住建厅", "住房和城乡建设", "住建部",
    # 其他相关
    "深井", "人工岛", "路面", "路基", "桥墩", "桥面",
    "华侨城", "东部华侨城", "景区设施",
]

# 排除关键词（匹配任一则过滤，但当已经匹配到建筑关键词时不排除）
EXCLUDE_KW: list[str] = [
    # 矿山/煤矿
    "煤矿", "矿山", "矿井", "采煤", "煤层", "非煤矿山", "铁矿", "金矿",
    "矿难", "透水", "冒顶",
    # 化工/危化
    "化工厂", "化学品", "化工企业", "石化", "炼油", "油罐",
    "烟花爆竹", "爆竹厂", "炸药", "民爆",
    # 交通（非施工）
    "高速公路上", "高速路上", "大巴侧翻", "客车侧翻", "校车", "公交车事故",
    "飞机失事", "航空器", "渔船沉没", "渡轮", "沉船", "铁路事故", "列车脱轨",
    # 自然灾害
    "森林火灾", "山火爆发", "地震", "洪水泛滥", "台风登陆",
    # 其他领域
    "食物中毒", "传染病爆发", "疫情",
    # 非事故安全内容（培训/会议等）
    "安全知识讲座", "安全培训会", "安全月活动", "应急演练",
    "消防演练", "安全标准化", "安全文化", "安全工作会议", "安全讲座",
]


def is_construction_accident(title: str, content: str = "") -> tuple[bool, str]:
    """判断是否为建筑施工事故新闻。

    改进策略:
    1. 先检测建筑关键词（≥1个）
    2. 如果已匹配建筑关键词 → 只有当排除词明确指向非建筑领域时才过滤
    3. 如果没有建筑关键词 → 排除词直接过滤
    4. 兜底: 伤亡数字+地点 → 保留

    Returns:
        (是否建筑事故, 判定原因)
    """
    text = (title + " " + (content or "")[:1000]).lower()

    # Phase 1: 检测建筑关键词
    matched = []
    for kw in CONSTRUCTION_KW:
        if kw in text:
            matched.append(kw)
            if len(matched) >= 3:
                break

    # Phase 2: 检查排除词
    if matched:
        # 有建筑关键词 → 排除词必须是非常明确的非建筑领域才能过滤
        # 放宽: 仅以下类别在有建筑关键词时仍然有效
        strong_exclude = [
            "煤矿", "矿山", "矿井", "采煤", "非煤矿山",
            "化工厂", "石化", "炼油", "油罐",
            "烟花爆竹", "爆竹厂",
            "飞机失事", "航空器", "渔船沉没", "渡轮",
            "森林火灾", "山火爆发", "地震", "洪水泛滥", "台风登陆",
            "食物中毒", "传染病爆发", "疫情",
            "安全知识讲座", "安全培训会", "安全月活动",
        ]
        for kw in strong_exclude:
            if kw in text:
                return False, f"已匹配{len(matched)}个建筑词但排除词命中: '{kw}'"

        return True, f"建筑关键词({len(matched)}个): {', '.join(matched[:5])}"

    # Phase 3: 没有建筑关键词 → 排除词直接生效
    for kw in EXCLUDE_KW:
        if kw in text:
            return False, f"无建筑词且排除: '{kw}'"

    # Phase 4: 兜底 — 伤亡数字+地点
    if re.search(r'\d+人死亡|死亡\d+人|\d+人受伤|致\d+死', title):
        if "省" in text or "市" in text:
            return True, "伤亡标题+地点（无建筑关键词但推测为事故）"

    return False, "无建筑关键词且无伤亡地点信息"


# ═══════════════════════════════════════════════
# 爬虫核心
# ═══════════════════════════════════════════════

class SafehooCollector(BaseCollector):
    """安全管理网建筑事故新闻采集器。

    数据流: 列表页 → 标题筛选 → 详情页 → 正文筛选 → RawAccidentStore

    用法:
        collector = SafehooCollector(start_page=1, end_page=3, max_items=50)
        raw_accidents = collector.collect()  # → list[RawAccidentText]
        # 原始数据也同步缓存到 data/raw_accidents.json
    """

    def __init__(
        self,
        start_page: int = 1,
        end_page: int = 0,         # 0 = 自动到末页
        max_items: int = 0,         # 0 = 不限
        delay: float = 2.0,
        data_dir: Optional[Path] = None,
    ):
        self.start_page = max(1, start_page)
        self.end_page = end_page
        self.max_items = max_items
        self._delay = delay
        # `data_dir=None` 时落 `settings.DATA_DIR/<源名>`（旧行为）。在线采集
        # 传一个临时目录进来，`data/<源名>/` 就不再产生——采集产出自 M2 起
        # 只落工作库的 `raw_accidents` 表（蓝图 01 §3.4）。
        self.data_dir = data_dir or (settings.DATA_DIR / "safehoo")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # HTTP 客户端
        self._client = httpx.Client(
            timeout=20.0,
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

        # 原始数据缓存（独立于 processed 数据集）
        self.raw_store = RawAccidentStore()

        # 状态
        self._total_pages: int = 0
        self._collected: int = 0
        self._filtered_out: int = 0
        self._errors: int = 0

    def source_name(self) -> str:
        return "safehoo"

    @property
    def total_pages(self) -> int:
        return self._total_pages

    @property
    def collected_count(self) -> int:
        return self._collected

    def collect(self) -> list[RawAccidentText]:
        """执行采集，返回 RawAccidentText 列表。

        同时将原始数据缓存到 RawAccidentStore (data/raw_accidents.json)。
        """
        t_start = time.time()
        raw_results: list[RawAccidentText] = []

        # Step 0: 探测分页
        first_html = self._fetch(_list_page_url(self.start_page))
        if not first_html:
            logger.error(f"无法访问 safehoo: {_list_page_url(self.start_page)}")
            return self._make_stats(0)

        self._detect_pagination(first_html)

        actual_end = self.end_page if self.end_page > 0 else self._total_pages
        actual_end = min(actual_end, self._total_pages)

        logger.info(
            f"safehoo 采集: 第{self.start_page}-{actual_end}页/{self._total_pages}页, "
            f"最多{self.max_items or '不限'}条, 延迟{self._delay}s"
        )

        batch_items = []  # 批量写入缓存

        for page in range(self.start_page, actual_end + 1):
            if self.max_items > 0 and self._collected >= self.max_items:
                break

            # 获取列表页
            if page == self.start_page:
                html = first_html
            else:
                html = self._fetch(_list_page_url(page))
                if not html:
                    logger.warning(f"第{page}页请求失败，跳过")
                    continue

            # 解析列表
            news_links = self._parse_list(html, page)
            logger.info(f"第{page}页: {len(news_links)}条建筑事故 (过滤掉{100-len(news_links)}条非建筑)")

            # 逐条爬详情
            for title, url, list_date in news_links:
                if self.max_items > 0 and self._collected >= self.max_items:
                    break

                try:
                    item = self._crawl_detail(title, url, list_date)
                    if item:
                        batch_items.append(item)
                        self._collected += 1

                        # 构建 RawAccidentText 用于返回值
                        raw_results.append(RawAccidentText(
                            source="safehoo",
                            title=item["title"],
                            raw_text=item["content"][:50000],
                            source_url=item["source_url"],
                            fetch_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
                        ))

                        if self._collected % 10 == 0:
                            logger.info(f"  已采集 {self._collected} 条...")
                            # 定期保存
                            self.raw_store.add_batch(batch_items)
                            batch_items = []

                except Exception as e:
                    self._errors += 1
                    logger.debug(f"详情页异常 {url}: {e}")
                    continue

                time.sleep(self._delay)

            time.sleep(self._delay)

        # 保存剩余
        if batch_items:
            self.raw_store.add_batch(batch_items)
        self.raw_store._save()

        elapsed = time.time() - t_start
        logger.info(
            f"safehoo 采集完成: 收录{self._collected}条, "
            f"过滤{self._filtered_out}条, 错误{self._errors}条 "
            f"({elapsed:.1f}s)"
        )
        return raw_results

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

    def _detect_pagination(self, html: str):
        """探测总页数。"""
        soup = BeautifulSoup(html, "lxml")

        # 方式1: "共 1408 条数据"
        text = soup.get_text()[:5000]
        m = re.search(r'共\s*(\d+)\s*条数据', text)
        total = int(m.group(1)) if m else 0
        m2 = re.search(r'(\d+)\s*条数据/页', text)
        per_page = int(m2.group(1)) if m2 else 100

        if total > 0:
            self._total_pages = (total + per_page - 1) // per_page
            logger.info(f"分页: {total}条/{per_page}每页 = {self._total_pages}页")
            return

        # 方式2: 跳页下拉框
        jump = soup.find("select", id="Jumppage")
        if jump:
            max_page = 1
            for opt in jump.find_all("option"):
                try:
                    max_page = max(max_page, int(opt.get_text(strip=True)))
                except ValueError:
                    pass
            self._total_pages = max_page
            return

        self._total_pages = 15
        logger.warning(f"无法探测分页，默认 {self._total_pages} 页")

    def _parse_list(self, html: str, page_num: int) -> list[tuple[str, str, str]]:
        """解析列表页，返回 [(标题, 详情URL, 列表日期)] 三元组。

        标题预筛选：非建筑事故直接丢弃，不请求详情页。
        """
        results = []
        soup = BeautifulSoup(html, "lxml")

        for a_tag in soup.find_all("a", target="_blank"):
            href = (a_tag.get("href") or "").strip()
            title = a_tag.get_text(strip=True)

            if "/News/" not in href:
                continue
            if not title or len(title) < 6:
                continue

            # 标题预筛选
            is_ok, _ = is_construction_accident(title, "")
            if not is_ok:
                self._filtered_out += 1
                continue

            full_url = f"{SAFEHOO_BASE}{href}" if href.startswith("/") else href

            # 从相邻 <span class="mor"> 提取日期
            list_date = ""
            parent_li = a_tag.find_parent("li")
            if parent_li:
                date_span = parent_li.find("span", class_="mor")
                if date_span:
                    list_date = date_span.get_text(strip=True)

            results.append((title, full_url, list_date))

        return results

    def _crawl_detail(
        self, list_title: str, url: str, list_date: str
    ) -> Optional[dict]:
        """爬取详情页，返回 {title, content, source_url, source_name, publish_date}。"""
        html = self._fetch(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")

        # 标题: c_title_text
        title = list_title
        title_div = soup.find("div", class_="c_title_text")
        if title_div:
            t = title_div.get_text(strip=True)
            if t and len(t) > 4:
                title = t

        # 正文: con_zw
        content = ""
        content_div = soup.find("div", class_="con_zw")
        if content_div:
            for noise in content_div.find_all(["script", "style", "iframe"]):
                noise.decompose()
            content = content_div.get_text(separator="\n", strip=True)

        if not content or len(content) < 30:
            return None

        # 正文二次筛选
        is_ok, reason = is_construction_accident(title, content)
        if not is_ok:
            # 记录过滤
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            self.raw_store.mark_filtered(url_hash, reason)
            self._filtered_out += 1
            return None

        # 日期
        publish_date = ""
        m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', content[:1000])
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 2000 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                    publish_date = f"{y}-{mo:02d}-{d:02d}"
            except ValueError:
                pass

        if not publish_date and list_date:
            m = re.search(r'/(\d{4})(\d{2})/', url)
            if m:
                year = int(m.group(1))
                dm = re.match(r'(\d{1,2})-(\d{1,2})', list_date)
                if dm:
                    try:
                        publish_date = f"{year}-{int(dm.group(1)):02d}-{int(dm.group(2)):02d}"
                    except ValueError:
                        pass

        return {
            "title": title,
            "content": content,
            "source_url": url,
            "source_name": "safehoo",
            "publish_date": publish_date,
            "status": "new",
        }

    def _make_stats(self, duration: float) -> dict:
        cache_stats = self.raw_store.stats()
        return {
            "total_crawled": self._collected,
            "filtered_out": self._filtered_out,
            "errors": self._errors,
            "cached_total": cache_stats["total"],
            "cached_new": cache_stats["new"],
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

def crawl_safehoo(
    start_page: int = 1,
    end_page: int = 0,
    max_items: int = 0,
    delay: float = 2.0,
) -> list[RawAccidentText]:
    """同步入口。"""
    c = SafehooCollector(
        start_page=start_page,
        end_page=end_page,
        max_items=max_items,
        delay=delay,
    )
    return c.collect()
