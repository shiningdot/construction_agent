"""规则字段提取器：用纯代码从事故文本中提取结构化字段。

这是整个采集系统的"省钱核心"——80% 以上的字段通过正则/规则提取，
只有在综合置信度偏低时才提交 LLM 增强。

提取字段清单：
- 日期、地点（省/市）、伤亡人数（死亡/受伤）
- 事故类型、事故等级
- 施工单位、项目名称、直接原因
- 经济损失
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from construction_agent.collector.base import RawAccidentText
from construction_agent.collector.cleaners.normalizer import Normalizer, get_normalizer


class RuleBasedFieldExtractor:
    """基于正则和规则的字段提取器（零 API 成本）。"""

    # ── 日期提取正则 ──
    DATE_PATTERNS: list[tuple[re.Pattern, int]] = [
        # 格式: 2024年3月15日 (最高优先级)
        (re.compile(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日'), 10),
        # 格式: 2024-03-15
        (re.compile(r'(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})'), 9),
        # 格式: 2024年3月 (无日期)
        (re.compile(r'(\d{4})\s*年\s*(\d{1,2})\s*月'), 6),
        # 格式: 2024-03 (无日期)
        (re.compile(r'(\d{4})\s*[-/.]\s*(\d{1,2})(?![-/.]\s*\d)'), 5),
        # 格式: 3月15日 (无年份，优先从上下文找年份)
        (re.compile(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日'), 4),
    ]

    # ── 伤亡人数提取正则 ──
    DEATH_PATTERNS: list[re.Pattern] = [
        re.compile(r'造成\s*(\d+)\s*人\s*死亡'),
        re.compile(r'(\d+)\s*人\s*死亡'),
        re.compile(r'(\d+)\s*人\s*遇难'),
        re.compile(r'死亡\s*(\d+)\s*人'),
        re.compile(r'(\d+)\s*人\s*当场死亡'),
        re.compile(r'致\s*(\d+)\s*死'),
        re.compile(r'(\d+)\s*名\s*(?:工人|施工人员|作业人员|人员)?\s*死亡'),
        re.compile(r'造成\s*(\d+)\s*死'),
        re.compile(r'(\d+)\s*死\s*(?:\d+\s*伤)?'),
        re.compile(r'共?\s*(?:导致|造成|致)\s*(\d+)\s*人\s*身\s*亡'),
    ]

    INJURY_PATTERNS: list[re.Pattern] = [
        re.compile(r'(\d+)\s*人\s*受伤'),
        re.compile(r'(\d+)\s*人\s*重伤'),
        re.compile(r'受伤\s*(\d+)\s*人'),
        re.compile(r'重伤\s*(\d+)\s*人'),
        re.compile(r'(\d+)\s*伤\s*(?:\d+\s*死)?'),
        re.compile(r'造成\s*(\d+)\s*人\s*受伤'),
        re.compile(r'(\d+)\s*名\s*(?:工人|人员)?\s*受伤'),
    ]

    # ── 被困人数 ──
    TRAPPED_PATTERNS: list[re.Pattern] = [
        re.compile(r'(\d+)\s*人\s*被困'),
        re.compile(r'(\d+)\s*人\s*被埋'),
        re.compile(r'(\d+)\s*人\s*失联'),
        re.compile(r'(\d+)\s*人\s*下落不明'),
        re.compile(r'被困\s*(\d+)\s*人'),
    ]

    # ── 经济损失 ──
    LOSS_PATTERNS: list[re.Pattern] = [
        re.compile(r'直接经济损失[约达]?\s*(\d+\.?\d*)\s*万\s*元'),
        re.compile(r'经济损失[约达]?\s*(\d+\.?\d*)\s*万\s*元'),
        re.compile(r'造成\s*(\d+\.?\d*)\s*万\s*元\s*(?:的)?\s*(?:直接)?\s*经济损失'),
        re.compile(r'(\d+\.?\d*)\s*万\s*元\s*(?:的)?\s*(?:直接)?\s*经济损失'),
    ]

    # ── 施工单位 ──
    CONTRACTOR_PATTERNS: list[re.Pattern] = [
        re.compile(r'施工(?:总)?承包(?:单位|方|企业)[:：]?\s*([一-鿿a-zA-Z()（）一-鿿]{4,40}(?:公司|集团|局|中心|有限公司|有限责任公司|股份有限公司))'),
        re.compile(r'施工单位[:：]?\s*([一-鿿a-zA-Z()（）一-鿿]{4,40}(?:公司|集团|局|中心|有限公司|有限责任公司|股份有限公司))'),
        re.compile(r'(?:由|被)?\s*([一-鿿a-zA-Z()（）一-鿿]{4,20}(?:建设|建筑|工程).{0,8}(?:公司|集团|局))(?:承建|施工|总承包)'),
    ]

    # ── 项目名称 ──
    PROJECT_PATTERNS: list[re.Pattern] = [
        re.compile(r'(?:工程|项目)[名称为]?\s*[:：]?\s*["""]?([^\n""""，,]{3,60}(?:工程|项目|标段|地块|小区|大厦|中心|广场|花园|城))'),
        re.compile(r'(?:发生|事发|事故).*?(?:地点|现场|位于)\s*[:：]?\s*([^\n，,]{3,40}(?:工地|施工现场|项目|工程))'),
    ]

    def __init__(self, normalizer: Normalizer | None = None):
        self.normalizer = normalizer or get_normalizer()

    def extract(self, raw: RawAccidentText) -> RawAccidentText:
        """对 RawAccidentText 执行规则提取，原地修改并返回。

        提取顺序：
        1. 日期 → 2. 地点 → 3. 伤亡 → 4. 事故类型 → 5. 等级 → 6. 经济 → 7. 其他

        Args:
            raw: 待提取的原始事故文本

        Returns:
            原地修改后的同一个 RawAccidentText 对象
        """
        text = raw.raw_text or raw.title or ""

        # Step 1: 提取日期
        self._extract_date(raw, text)

        # Step 2: 提取地点
        self._extract_location(raw, text)

        # Step 3: 提取伤亡
        self._extract_casualties(raw, text)

        # Step 4: 提取事故类型
        self._extract_accident_type(raw, text)

        # Step 5: 推断事故等级
        self._extract_severity(raw)

        # Step 6: 提取经济损失
        self._extract_economic_loss(raw, text)

        # Step 7: 提取施工单位和项目名
        self._extract_project_info(raw, text)

        # Step 8: 计算综合置信度
        self._calculate_confidence(raw)

        return raw

    def _extract_date(self, raw: RawAccidentText, text: str):
        """从文本中提取日期。"""
        # 优先从标题中提取
        title = raw.title or ""
        context_year = self._find_context_year(text)

        for pattern, priority in self.DATE_PATTERNS:
            # 先在标题中找
            match = pattern.search(title[:100])
            if not match:
                # 再在正文前1000字中找
                match = pattern.search(text[:1000])

            if match:
                groups = match.groups()
                try:
                    if len(groups) == 3:
                        year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                    elif len(groups) == 2:
                        year, month = int(groups[0]), int(groups[1])
                        day = 1
                    else:
                        continue

                    # 合理性检查
                    if 2000 <= year <= 2030 and 1 <= month <= 12:
                        if day == 1 and len(groups) == 2:
                            # 只有年月，置信度减半
                            raw.confidence_date = min(0.7, priority / 15)
                        else:
                            if 1 <= day <= 31:
                                raw.extracted_date = date(year, month, day)
                                raw.confidence_date = min(0.95, priority / 10)
                                return
                            elif day == 1 and len(groups) == 3:
                                # 日可能被误识别为1
                                raw.extracted_date = date(year, month, 1)
                                raw.confidence_date = min(0.7, priority / 15)
                                return
                except (ValueError, TypeError):
                    continue

        # 处理 "3月15日" 无年份的情况
        for pattern, priority in self.DATE_PATTERNS:
            if len(pattern.groups) == 2:
                match = pattern.search(text[:500])
                if match:
                    month, day = int(match.group(1)), int(match.group(2))
                    if context_year and 1 <= month <= 12 and 1 <= day <= 31:
                        raw.extracted_date = date(context_year, month, day)
                        raw.confidence_date = 0.4
                        return

    def _find_context_year(self, text: str) -> Optional[int]:
        """从文本上下文中查找年份。"""
        m = re.search(r'(\d{4})\s*年', text[:500])
        if m:
            year = int(m.group(1))
            if 2000 <= year <= 2030:
                return year
        return None

    def _extract_location(self, raw: RawAccidentText, text: str):
        """从文本中提取地点（省/市）。"""
        # 市名
        city = self.normalizer.extract_city(text)
        if city:
            raw.extracted_city = city
            raw.confidence_city = 0.85

        # 省份
        province = self.normalizer.extract_province_from_text(text)
        if province:
            raw.extracted_province = province
            if not city:
                # 无法获取城市但能获取省份时置信度降低
                raw.confidence_city = 0.3
        elif city:
            # 通过城市反推省份
            from construction_agent.utils.location import CITY_TO_PROVINCE
            province = CITY_TO_PROVINCE.get(city, "")
            if province:
                raw.extracted_province = province

    def _extract_casualties(self, raw: RawAccidentText, text: str):
        """提取伤亡人数。"""
        # 提取死亡人数
        deaths = 0
        death_confidence = 0.0
        for pattern in self.DEATH_PATTERNS:
            match = pattern.search(text[:2000])
            if match:
                d = int(match.group(1))
                # 合理性检查
                if 0 < d <= 200:
                    deaths = d
                    death_confidence = 0.95
                    break

        # 提取受伤人数
        injuries = 0
        injury_confidence = 0.0
        for pattern in self.INJURY_PATTERNS:
            match = pattern.search(text[:2000])
            if match:
                i = int(match.group(1))
                if 0 < i <= 500:
                    injuries = i
                    injury_confidence = 0.9
                    break

        # 提取被困人数
        trapped = 0
        for pattern in self.TRAPPED_PATTERNS:
            match = pattern.search(text[:2000])
            if match:
                t = int(match.group(1))
                if 0 < t <= 500:
                    trapped = t
                    break

        raw.extracted_deaths = deaths
        raw.extracted_injuries = injuries
        raw.confidence_deaths = death_confidence

        # 将被困人数存入 extra
        if trapped > 0:
            raw.extra["trapped_count"] = trapped

        # 中文数字兜底（如"三人死亡"）
        if deaths == 0:
            cn_match = re.search(r'([一二三四五六七八九十]+)\s*人\s*死亡', text[:1000])
            if cn_match:
                d = self.normalizer.cn_num_to_int(cn_match.group(1))
                if d > 0:
                    raw.extracted_deaths = d
                    raw.confidence_deaths = 0.7

    def _extract_accident_type(self, raw: RawAccidentText, text: str):
        """提取事故类型。"""
        type_name, confidence = self.normalizer.normalize_accident_type(text)
        raw.extracted_accident_type = type_name
        raw.confidence_accident_type = confidence

    def _extract_severity(self, raw: RawAccidentText):
        """根据伤亡人数推断事故等级。"""
        sev_name, confidence = self.normalizer.normalize_severity(
            raw.extracted_deaths, raw.extracted_injuries
        )
        raw.extracted_severity = sev_name

    def _extract_economic_loss(self, raw: RawAccidentText, text: str):
        """提取直接经济损失。"""
        for pattern in self.LOSS_PATTERNS:
            match = pattern.search(text[:2000])
            if match:
                try:
                    loss = float(match.group(1))
                    if loss > 0:
                        raw.extra["direct_economic_loss"] = loss
                        break
                except ValueError:
                    continue

    def _extract_project_info(self, raw: RawAccidentText, text: str):
        """提取施工单位和项目名称。"""
        # 施工单位
        for pattern in self.CONTRACTOR_PATTERNS:
            match = pattern.search(text[:2000])
            if match:
                contractor = match.group(1).strip()
                if len(contractor) >= 4:
                    raw.extra["contractor"] = contractor
                    break

        # 项目名称
        for pattern in self.PROJECT_PATTERNS:
            match = pattern.search(text[:2000])
            if match:
                project = match.group(1).strip()
                if len(project) >= 3:
                    raw.extra["project_name"] = project
                    break

    def _calculate_confidence(self, raw: RawAccidentText):
        """计算综合置信度。

        权重分布：
        - 日期: 25%
        - 城市: 20%
        - 事故类型: 20%
        - 死亡人数: 20%
        - 其他字段: 15%
        """
        weights = {
            'date': 0.25,
            'city': 0.20,
            'accident_type': 0.20,
            'deaths': 0.20,
        }
        score = (
            raw.confidence_date * weights['date'] +
            raw.confidence_city * weights['city'] +
            raw.confidence_accident_type * weights['accident_type'] +
            (0.5 if raw.extracted_deaths > 0 else 0.0) * weights['deaths']
        )
        # 有额外字段加分
        bonus = 0.0
        if raw.extra:
            bonus = min(0.15, len(raw.extra) * 0.03)
        raw.confidence_overall = min(1.0, score + bonus)


# 模块级单例
_default_extractor: RuleBasedFieldExtractor | None = None


def get_extractor() -> RuleBasedFieldExtractor:
    """获取默认规则提取器单例。"""
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = RuleBasedFieldExtractor()
    return _default_extractor
