"""事故数据模型。"""

from __future__ import annotations

import hashlib
import json
import datetime
import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from construction_agent.utils.date_utils import parse_date_to_dateobj


class AccidentType(str, Enum):
    """事故类型枚举。"""

    COLLAPSE = "坍塌"
    FALL = "高处坠落"
    ELECTRIC_SHOCK = "触电"
    MACHINE_INJURY = "机械伤害"
    LIFTING_INJURY = "起重伤害"
    OBJECT_STRIKE = "物体打击"
    EXCAVATION_CAVE_IN = "土方坍塌"
    FIRE_EXPLOSION = "火灾爆炸"
    POISONING_SUFFOCATION = "中毒窒息"
    DROWNING = "淹溺"
    OTHER = "其他"


class SeverityLevel(str, Enum):
    """事故等级枚举。"""

    GENERAL = "一般事故"           # 3人以下死亡
    MAJOR = "较大事故"             # 3-10人死亡
    SERIOUS = "重大事故"           # 10-30人死亡
    CATASTROPHIC = "特别重大事故"   # 30人以上死亡


class FactorMixin:
    """致因因素混合类。"""

    description: str
    category: str


class HumanFactor(BaseModel):
    """人的因素。"""
    description: str = Field(description="具体描述")
    category: str = Field(description="分类，如：违章操作、安全意识不足、疲劳作业、技能不足、未佩戴PPE等")
    reasoning: str = Field(default="", description="判断依据：引用原文语句说明为什么选择此分类")


class MachineFactor(BaseModel):
    """设备/物的因素。"""
    description: str = Field(description="具体描述")
    category: str = Field(description="分类，如：设备故障、防护缺陷、工具不合格、安全装置失效等")
    reasoning: str = Field(default="", description="判断依据：引用原文语句说明为什么选择此分类")


class ManagementFactor(BaseModel):
    """管理因素。"""
    description: str = Field(description="具体描述")
    category: str = Field(description="分类，如：监管缺失、安全制度不健全、培训不足、隐患排查不到位、应急预案缺失等")
    reasoning: str = Field(default="", description="判断依据：引用原文语句说明为什么选择此分类")


class EnvironmentalFactor(BaseModel):
    """环境因素。"""
    description: str = Field(description="具体描述")
    category: str = Field(description="分类，如：恶劣天气、地质条件不良、作业环境复杂、照明不足等")
    reasoning: str = Field(default="", description="判断依据：引用原文语句说明为什么选择此分类")


class HfacsCode(BaseModel):
    """HFACS-CS 编码映射。"""
    code: str = Field(description="HFACS-CS 编码，如 O1, S2, A1")
    level: int = Field(description="层级 1-6")
    level_name_cn: str = Field(description="层级中文名，如'组织因素'")
    category_name_cn: str = Field(description="子类别中文名，如'安全文化缺失'")
    source_dimension: str = Field(description="来源维度，如 human/machine/management/environmental")
    source_category: str = Field(description="来源 V2 分类，如'监管缺失'")
    source_description: str = Field(description="来源 V2 具体描述")
    encoding_method: str = Field(default="llm", description="编码方式: llm / keyword")


def _normalize_title(title: str) -> str:
    """标准化标题用于查重去重。"""
    s = title.strip()
    # 去除常见无意义前后缀
    s = re.sub(r'^[〔【(（]?\d+[〕】)）]?\s*', '', s)  # 去掉序号如【123】
    s = re.sub(r'\s+', '', s)  # 去掉所有空白
    return s.lower()


def generate_accident_id(title: str, date_str: str = "", city: str = "") -> str:
    """根据标题+日期+城市生成唯一事故 ID（MD5前8位）。

    相同事故不论何时提取，ID 一致，天然支持查重。
    """
    key = f"{_normalize_title(title)}|{date_str or ''}|{city or ''}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:8]


class Accident(BaseModel):
    """事故完整信息模型。"""

    # ── 唯一标识（自动生成） ──
    id: str = Field(default="", description="事故唯一ID（MD5(标题+日期+城市)前8位，自动生成）")

    # ── 基础信息 ──
    title: str = Field(description="事故标题")
    date: Optional[datetime.date] = Field(default=None, description="事故发生日期")
    accident_time: Optional[str] = Field(default=None, description="事故发生具体时刻（HH:MM格式，如14:52）")
    location: str = Field(default="", description="事故地点（省级：省/自治区/直辖市/特别行政区）")
    city: str = Field(default="", description="地级市，如齐齐哈尔市")
    accident_location_detail: str = Field(default="", description="事故具体部位，如3#楼北侧外脚手架")
    project_name: str = Field(default="", description="工程项目名称")
    project_type: str = Field(default="", description="工程类型，如房屋建筑工程、市政基础设施工程等")
    building_structure: str = Field(default="", description="建筑结构，如砖混结构、钢结构等")
    building_completion_year: str = Field(default="", description="建筑建成年份")
    construction_phase: str = Field(default="", description="施工阶段，如主体施工、装饰装修、拆除等（保留原始字眼，后续归一化）")
    accident_type: str = Field(default="", description="事故类型")
    severity: str = Field(default="", description="事故等级")

    # ── 时间派生字段（自动推导） ──
    day_of_week: Optional[str] = Field(default=None, description="事故发生星期（如星期一）")
    season: Optional[str] = Field(default=None, description="事故发生季节（春/夏/秋/冬）")
    time_period: Optional[str] = Field(default=None, description="事故发生时段（上午/下午/晚上/夜间）")
    lighting: Optional[str] = Field(default=None, description="光照情况（光照良好/光照不足/无法判断）")
    weather: str = Field(default="", description="气象情况（天气良好/大风/无法判断）")

    # ── 人员伤亡 ──
    deaths: int = Field(default=0, description="死亡人数")
    injuries: int = Field(default=0, description="受伤人数")

    # ── 经济损失 ──
    direct_economic_loss: Optional[float] = Field(
        default=None, description="直接经济损失（万元）"
    )

    # ── 事故直接原因 ──
    direct_cause: str = Field(default="", description="事故直接原因简述，如'污水支管施工开挖过程中发生坍塌'")
    contractor: str = Field(default="", description="施工总承包单位名称")
    trapped_count: int = Field(default=0, description="被困人数（区别于死亡/受伤）")

    # ── CAICS 统一致因 ──
    causes: list[dict] = Field(default_factory=list, description="CAICS致因列表 [{code,name,description,reasoning}]")
    caics_codes: list[str] = Field(default_factory=list, description="CAICS编码链，如['M1','F1','T2','H1']")

    # ── 四维度致因（V1 分类）──
    human_factors: list[dict] = Field(default_factory=list, description="人的因素列表")
    machine_factors: list[dict] = Field(default_factory=list, description="设备/物因素列表")
    management_factors: list[dict] = Field(default_factory=list, description="管理因素列表")
    environmental_factors: list[dict] = Field(default_factory=list, description="环境因素列表")

    # ── HFACS-CS 编码 ──
    hfacs_codes: list[str] = Field(default_factory=list, description="HFACS-CS 编码列表")
    hfacs_details: list[dict] = Field(default_factory=list, description="HFACS-CS 编码明细")
    hfacs_encoding_version: str = Field(default="", description="HFACS 编码版本号")

    # ── 来源 ──
    source: str = Field(default="", description="数据来源标注：PDF识别/新闻报告/用户输入/RSS抓取/CSV导入等")
    source_urls: list[str] = Field(default_factory=list, description="信息来源链接")
    summary: str = Field(default="", description="事故摘要/经过描述")

    # ── 搜索元数据 ──
    search_snippets: list[dict] = Field(
        default_factory=list,
        description="联网搜索结果的摘要信息，每项包含 {url, title, snippet, query}",
    )
    search_queries: list[str] = Field(
        default_factory=list,
        description="执行了哪些搜索查询",
    )

    # ── 图片 ──
    images: list[str] = Field(default_factory=list, description="本地图片路径列表")
    image_urls: list[dict] = Field(
        default_factory=list,
        description="图片来源信息，每项包含 {source_url, thumb_url, local_path}",
    )

    # ── 元数据 ──
    raw_input: str = Field(default="", description="原始输入文本")
    created_at: Optional[str] = Field(default=None, description="记录创建时间")

    def ensure_id(self) -> str:
        """确保有唯一ID，如为空则自动生成。"""
        if not self.id:
            date_str = self.date.isoformat() if self.date else ""
            self.id = generate_accident_id(self.title, date_str, self.city)
        return self.id

    def to_dict(self) -> dict:
        """转为字典。"""
        self.ensure_id()
        d = self.model_dump()
        if self.date:
            d["date"] = self.date.isoformat()
        return d

    def to_json(self, ensure_ascii: bool = False, indent: int = 2) -> str:
        """转为 JSON 字符串。"""
        return self.model_dump_json(indent=indent)

    def auto_derive_fields(self):
        """根据已有字段自动推导派生字段（星期、季节、时段、光照、ID）。"""
        self.ensure_id()
        if self.date:
            weekday_map = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            self.day_of_week = weekday_map[self.date.weekday()]

            month = self.date.month
            if 3 <= month <= 5:
                self.season = "春"
            elif 6 <= month <= 8:
                self.season = "夏"
            elif 9 <= month <= 11:
                self.season = "秋"
            else:
                self.season = "冬"

        if self.accident_time:
            try:
                hour = int(self.accident_time.split(":")[0])
                if 6 <= hour < 12:
                    self.time_period = "上午"
                elif 12 <= hour < 18:
                    self.time_period = "下午"
                elif 18 <= hour < 22:
                    self.time_period = "晚上"
                else:
                    self.time_period = "夜间"
            except (ValueError, IndexError):
                pass

        # 光照情况：从时段推导，文本中提取的天气可覆盖
        if self.time_period and not self.lighting:
            if self.time_period in ("上午", "下午"):
                self.lighting = "光照良好"
            else:
                self.lighting = "光照不足"

        return self

    def to_flat_dict(self) -> dict:
        """转为扁平字典（适合 CSV 输出）。"""
        self.ensure_id()
        base = {
            "id": self.id,
            "title": self.title,
            "date": self.date.isoformat() if self.date else "",
            "accident_time": self.accident_time or "",
            "day_of_week": self.day_of_week or "",
            "season": self.season or "",
            "time_period": self.time_period or "",
            "lighting": self.lighting or "",
            "weather": self.weather or "",
            "location": self.location,
            "city": self.city or "",
            "accident_location_detail": self.accident_location_detail or "",
            "project_name": self.project_name,
            "project_type": self.project_type or "",
            "building_structure": self.building_structure or "",
            "building_completion_year": self.building_completion_year or "",
            "construction_phase": self.construction_phase or "",
            "accident_type": self.accident_type,
            "severity": self.severity,
            "deaths": self.deaths,
            "injuries": self.injuries,
            "direct_economic_loss": self.direct_economic_loss or "",
            "direct_cause": self.direct_cause or "",
            "contractor": self.contractor or "",
            "trapped_count": self.trapped_count or 0,
            "causes": "; ".join(f"[{c['code']}] {c['name']}" for c in self.causes),
            "caics_codes": "; ".join(self.caics_codes),
            "summary": self.summary,
            "source": self.source,
            "source_urls": "; ".join(self.source_urls),
            "search_queries": "; ".join(self.search_queries),
            "search_snippets": " ||| ".join(
                s.get("snippet", "")[:80] for s in self.search_snippets
            ),
            "image_urls": "; ".join(
                u.get("source_url", "") for u in self.image_urls
            ),
            "images": "; ".join(self.images),
            "hfacs_codes": "; ".join(self.hfacs_codes),
            "hfacs_encoding_version": self.hfacs_encoding_version,
        }
        return base


class AccidentCreateRequest(BaseModel):
    """用户输入的事故信息。"""
    text: str = Field(description="用户输入的事故描述文本")


class CauseItem(BaseModel):
    """CAICS 致因条目。"""
    code: str = Field(description="CAICS编码，如M1/F1/T2/H1")
    name: str = Field(description="分类名")
    description: str = Field(default="", description="引用原文的具体描述")
    reasoning: str = Field(default="", description="判断逻辑")


class AccidentExtractionResult(BaseModel):
    """Claude API 提取的结构化结果。"""
    title: str = Field(description="事故标题")
    causes: list[CauseItem] = Field(default_factory=list, description="CAICS致因编码列表")
    date: Optional[str] = Field(default=None, description="事故发生日期（YYYY-MM-DD格式）")
    accident_time: Optional[str] = Field(default=None, description="事故发生具体时刻（HH:MM格式，如14:52）")
    location: str = Field(default="", description="事故地点（仅省级：省/自治区/直辖市/特别行政区）")
    city: str = Field(default="", description="地级市，如齐齐哈尔市")
    accident_location_detail: str = Field(default="", description="事故具体部位，如3#楼北侧外脚手架")
    project_name: str = Field(default="", description="工程项目名称")
    project_type: str = Field(default="", description="工程类型，如房屋建筑工程、市政基础设施工程等")
    building_structure: str = Field(default="", description="建筑结构，如砖混结构、钢结构等")
    building_completion_year: str = Field(default="", description="建筑建成年份")
    construction_phase: str = Field(default="", description="施工阶段，如主体施工、装饰装修、拆除等")
    accident_type: str = Field(description="事故类型（坍塌/高处坠落/触电/机械伤害/起重伤害/物体打击/土方坍塌/火灾爆炸/中毒窒息/淹溺/其他）")
    severity: str = Field(description="事故等级（一般事故/较大事故/重大事故/特别重大事故）")
    deaths: int = Field(default=0, description="死亡人数")
    injuries: int = Field(default=0, description="受伤人数")
    direct_economic_loss: Optional[float] = Field(default=None, description="直接经济损失（万元）")
    weather: str = Field(default="", description="气象情况（天气良好/大风/无法判断）")
    direct_cause: str = Field(default="", description="事故直接原因简述")
    contractor: str = Field(default="", description="施工总承包单位名称")
    trapped_count: int = Field(default=0, description="被困人数")
    human_factors: list[dict] = Field(default_factory=list, description="人的因素列表，每项包含category和description")
    machine_factors: list[dict] = Field(default_factory=list, description="设备/物因素列表")
    management_factors: list[dict] = Field(default_factory=list, description="管理因素列表")
    environmental_factors: list[dict] = Field(default_factory=list, description="环境因素列表")
    summary: str = Field(default="", description="事故经过摘要")
    source: str = Field(default="", description="数据来源标注：PDF识别/新闻报告/用户输入/RSS抓取/CSV导入等")
    source_urls: list[str] = Field(default_factory=list, description="信息来源链接")
    images: list[str] = Field(default_factory=list, description="本地图片路径")

    # 搜索元数据（注入而非从LLM提取）
    search_snippets: list[dict] = Field(default_factory=list, description="联网搜索结果摘要")
    search_queries: list[str] = Field(default_factory=list, description="搜索查询")
    image_urls: list[dict] = Field(default_factory=list, description="图片来源信息")

    def to_accident(self, raw_input: str = "") -> Accident:
        """转换为 Accident 完整模型。"""
        parsed_date = parse_date_to_dateobj(self.date) if self.date else None

        codes = [c.code for c in self.causes] if self.causes else []
        cause_dicts = [{"code": c.code, "name": c.name, "description": c.description, "reasoning": c.reasoning} for c in self.causes]

        accident = Accident(
            title=self.title,
            date=parsed_date,
            accident_time=self.accident_time,
            location=self.location,
            city=self.city,
            accident_location_detail=self.accident_location_detail,
            project_name=self.project_name,
            project_type=self.project_type,
            building_structure=self.building_structure,
            building_completion_year=self.building_completion_year,
            construction_phase=self.construction_phase,
            accident_type=self.accident_type,
            severity=self.severity,
            deaths=self.deaths,
            injuries=self.injuries,
            direct_economic_loss=self.direct_economic_loss,
            weather=self.weather,
            direct_cause=self.direct_cause,
            contractor=self.contractor,
            trapped_count=self.trapped_count,
            causes=cause_dicts,
            caics_codes=codes,
            summary=self.summary,
            source=self.source,
            source_urls=self.source_urls,
            search_snippets=self.search_snippets,
            search_queries=self.search_queries,
            images=self.images,
            image_urls=self.image_urls,
            raw_input=raw_input,
        )
        # 自动推导派生字段（星期、季节、时段、光照）
        accident.auto_derive_fields()
        return accident
