"""标准化器：将规则提取的字段值映射到项目统一分类体系。

复用现有 construction_agent.utils 中的 location 和 date_utils 模块。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional


class Normalizer:
    """字段标准化器。

    将各种来源的异构表述统一映射到项目定义的标准值：
    - 日期 → ISO YYYY-MM-DD
    - 地点 → 省级行政区全称
    - 事故类型 → AccidentType 枚举值
    - 事故等级 → SeverityLevel 枚举值
    - 数字 → 阿拉伯数字（"三人死亡" → 3）
    """

    # ── 事故类型关键词映射 ──
    # 格式：标准类型名 → [关键词列表]
    # 优先级：列表越靠前的关键词匹配优先级越高
    ACCIDENT_TYPE_KEYWORDS: dict[str, list[str]] = {
        "高处坠落": ["高处坠落", "高坠", "坠楼", "坠落身亡", "失足坠落", "从.*?坠落",
                     "坠入深坑", "跌落", "掉入"],
        "坍塌": ["坍塌", "垮塌", "倒塌", "倾覆", "塌方", "棚架坍塌", "墙体倒塌",
                 "脚手架倒塌", "屋面塌陷", "楼板坍塌"],
        "物体打击": ["物体打击", "砸中", "砸伤", "砸死", "坠物", "落物", "高处坠物",
                     "飞来物", "打击致死", "被砸"],
        "起重伤害": ["起重伤害", "塔吊", "起重机", "起重机械", "吊车", "龙门吊",
                     "升降机", "卷扬机", "吊装", "吊篮", "施工电梯"],
        "触电": ["触电", "电击", "电死", "漏电", "电伤", "高压线", "电弧"],
        "机械伤害": ["机械伤害", "搅拌机", "切割机", "卷入", "绞伤", "碾压",
                     "钢筋机", "打桩机", "挖掘机"],
        "土方坍塌": ["土方坍塌", "基坑坍塌", "沟槽坍塌", "边坡滑移", "基坑",
                     "沟槽", "边坡塌方", "土体滑坡"],
        "火灾爆炸": ["火灾", "爆炸", "燃爆", "起火", "爆燃", "瓦斯", "粉尘爆炸"],
        "中毒窒息": ["中毒", "窒息", "有毒气体", "硫化氢", "一氧化碳", "沼气",
                     "缺氧", "闷死", "有限空间"],
        "淹溺": ["淹溺", "溺水", "落水", "坠河", "掉入水中", "淹死"],
        "车辆伤害": ["车辆伤害", "车祸", "碾压致死", "渣土车", "搅拌车", "工程车",
                     "倒车撞", "货车"],
    }

    # 事故类型 → 关键词的反向索引（构建一次缓存）
    _type_cache: list[tuple[str, list[re.Pattern]]] | None = None

    def __init__(self):
        self._build_type_cache()

    def _build_type_cache(self):
        """构建事故类型关键词的正则缓存。"""
        if Normalizer._type_cache is not None:
            return
        Normalizer._type_cache = []
        for type_name, keywords in self.ACCIDENT_TYPE_KEYWORDS.items():
            patterns = [re.compile(kw) for kw in keywords]
            Normalizer._type_cache.append((type_name, patterns))

    # ── 日期标准化 ──
    def normalize_date(self, date_str: str) -> Optional[date]:
        """将各种中文日期格式标准化为 date 对象。

        支持的格式：
        - 2024年3月15日、2024年03月15日
        - 2024-03-15、2024/03/15、2024.03.15
        - 2024年3月（默认day=1）
        - 二〇二四年三月十五日（中文大写数字）
        """
        from construction_agent.utils.date_utils import parse_date_to_dateobj

        if not date_str or not date_str.strip():
            return None

        date_str = date_str.strip()

        # 中文大写数字 → 阿拉伯数字
        cn_num_map = {
            '〇': '0', '零': '0', '一': '1', '二': '2', '三': '3', '四': '4',
            '五': '5', '六': '6', '七': '7', '八': '8', '九': '9',
            '十': '10', '○': '0',
        }
        for cn, num in cn_num_map.items():
            date_str = date_str.replace(cn, num)

        # 处理 "二〇二四" 这种混合
        date_str = re.sub(r'二([一二三]?)', lambda m: f'2{m.group(1)}', date_str)

        return parse_date_to_dateobj(date_str)

    # ── 地点标准化 ──
    def normalize_province(self, location: str) -> str:
        """将地点标准化为省级行政区全称。"""
        from construction_agent.utils.location import normalize_province
        return normalize_province(location)

    def extract_city(self, text: str) -> str:
        """从文本中提取城市名。"""
        # 匹配 "XX市"
        m = re.search(r'([一-鿿]{2,6}(?:市|地区|自治州|盟))', text)
        if m:
            city = m.group(1)
            # 过滤误匹配（如"城市"、"全市"等）
            if city not in ("全市", "城市", "我市", "本市", "该市"):
                return city
        return ""

    def extract_province_from_text(self, text: str) -> str:
        """从文本中提取并标准化省份。"""
        # 先尝试匹配 XX省 / XX自治区 / XX市（直辖市）
        m = re.search(r'([一-鿿]{2,4}(?:省|自治区|特别行政区))', text)
        if m:
            return self.normalize_province(m.group(1))
        # 尝试匹配直辖市
        for city in ["北京市", "天津市", "上海市", "重庆市"]:
            if city in text:
                return city
        # 通过城市名反推
        city = self.extract_city(text)
        if city:
            from construction_agent.utils.location import CITY_TO_PROVINCE
            return CITY_TO_PROVINCE.get(city, "")
        return ""

    # ── 事故类型标准化 ──
    def normalize_accident_type(self, text: str) -> tuple[str, float]:
        """将文本映射到标准事故类型，返回(类型名, 置信度)。

        优先级：先匹配正文内容中的关键词，再匹配标题。
        """
        if Normalizer._type_cache is None:
            self._build_type_cache()

        # 搜索范围：优先正文（前2000字），其次全文
        search_text = text[:2000]

        best_type = "其他"
        best_score = 0

        for type_name, patterns in Normalizer._type_cache:  # type: ignore[union-attr]
            for p in patterns:
                match = p.search(search_text)
                if match:
                    # 打分：关键词越长，置信度越高；正文前部匹配更可靠
                    matched_text = match.group(0)
                    score = len(matched_text) / max(len(search_text[:500]), 1)
                    # 出现在文本前500字内加分
                    if match.start() < 500:
                        score += 0.3
                    # 标题中出现加分（文本第一行视为标题）
                    first_line = search_text.split('\n')[0]
                    if p.search(first_line):
                        score += 0.2

                    if score > best_score:
                        best_score = score
                        best_type = type_name

        # 置信度评估
        if best_score > 0.01:
            confidence = min(0.95, best_score * 20)
        elif best_type != "其他":
            confidence = 0.5
        else:
            confidence = 0.3

        return best_type, min(confidence, 1.0)

    # ── 事故等级标准化 ──
    def normalize_severity(self, deaths: int, injuries: int = 0, text: str = "") -> tuple[str, float]:
        """根据死亡人数推断事故等级，返回(等级名, 置信度)。

        标准依据《生产安全事故报告和调查处理条例》：
        - 一般事故: 死亡 < 3
        - 较大事故: 3 ≤ 死亡 < 10
        - 重大事故: 10 ≤ 死亡 < 30
        - 特别重大事故: 死亡 ≥ 30
        """
        # 优先从文本中直接提取（法规有时有特殊定义）
        if text:
            severity_kw = {
                "特别重大事故": ["特别重大事故", "特大事故"],
                "重大事故": ["重大事故"],
                "较大事故": ["较大事故"],
                "一般事故": ["一般事故"],
            }
            for sev_name, keywords in severity_kw.items():
                for kw in keywords:
                    if kw in text[:500]:
                        return sev_name, 0.95

        # 根据死亡人数推断
        if deaths >= 30:
            return "特别重大事故", 0.9
        elif deaths >= 10:
            return "重大事故", 0.9
        elif deaths >= 3:
            return "较大事故", 0.9
        elif deaths > 0:
            return "一般事故", 0.85
        else:
            return "一般事故", 0.5  # 无法确定

    # ── 中文数字 → 阿拉伯数字 ──
    _CN_NUM_MAP: dict[str, int] = {
        '零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
        '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
        '十': 10, '百': 100,
    }

    def cn_num_to_int(self, cn_str: str) -> int:
        """中文数字字符串转整数。

        Examples:
            "三人" → 3
            "十二人" → 12
            "一百二十三人" → 123
        """
        # 如果已经是阿拉伯数字
        m = re.match(r'(\d+)', cn_str)
        if m:
            return int(m.group(1))

        # 简单中文数字（十以内）
        if cn_str in self._CN_NUM_MAP and cn_str not in ('十', '百'):
            return self._CN_NUM_MAP[cn_str]

        result = 0
        current = 0
        for ch in cn_str:
            if ch in self._CN_NUM_MAP:
                n = self._CN_NUM_MAP[ch]
                if n >= 10:
                    if current == 0:
                        current = 1
                    result += current * n
                    current = 0
                else:
                    current = n
            else:
                # 遇到非数字字符（如"人"），停止
                break
        result += current
        return result if result > 0 else 0


# 模块级单例
_default_normalizer: Normalizer | None = None


def get_normalizer() -> Normalizer:
    """获取默认标准化器单例。"""
    global _default_normalizer
    if _default_normalizer is None:
        _default_normalizer = Normalizer()
    return _default_normalizer
