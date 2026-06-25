"""省级行政区划标准化工具。

将所有地点信息统一为省级（省/自治区/直辖市/特别行政区）级别。
"""

from __future__ import annotations

import re
from typing import Optional

# ── 省级行政区划典 ──

# 23 个省
PROVINCES: dict[str, str] = {
    "河北": "河北省",
    "山西": "山西省",
    "辽宁": "辽宁省",
    "吉林": "吉林省",
    "黑龙江": "黑龙江省",
    "江苏": "江苏省",
    "浙江": "浙江省",
    "安徽": "安徽省",
    "福建": "福建省",
    "江西": "江西省",
    "山东": "山东省",
    "河南": "河南省",
    "湖北": "湖北省",
    "湖南": "湖南省",
    "广东": "广东省",
    "海南": "海南省",
    "四川": "四川省",
    "贵州": "贵州省",
    "云南": "云南省",
    "陕西": "陕西省",
    "甘肃": "甘肃省",
    "青海": "青海省",
    "台湾": "台湾省",
}

# 5 个自治区
AUTONOMOUS_REGIONS: dict[str, str] = {
    "内蒙古": "内蒙古自治区",
    "广西": "广西壮族自治区",
    "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区",
    "新疆": "新疆维吾尔自治区",
}

# 4 个直辖市
MUNICIPALITIES: dict[str, str] = {
    "北京": "北京市",
    "天津": "天津市",
    "上海": "上海市",
    "重庆": "重庆市",
}

# 2 个特别行政区
SAR: dict[str, str] = {
    "香港": "香港特别行政区",
    "澳门": "澳门特别行政区",
}

# ── 合并为规范名称查找表（简称 → 全称） ──
ALIAS_TO_CANONICAL: dict[str, str] = {}
ALIAS_TO_CANONICAL.update(PROVINCES)
ALIAS_TO_CANONICAL.update(AUTONOMOUS_REGIONS)
ALIAS_TO_CANONICAL.update(MUNICIPALITIES)
ALIAS_TO_CANONICAL.update(SAR)
# 额外别名（含"省"字已存在的也收录，保障幂等）
ALIAS_TO_CANONICAL.update({v: v for v in ALIAS_TO_CANONICAL.values()})
# 内蒙古自治区的常见写法
ALIAS_TO_CANONICAL["内蒙"] = "内蒙古自治区"
# 重庆简称"渝"不常用作地点前缀，但收录以防
ALIAS_TO_CANONICAL["渝"] = "重庆市"

# ── 按名称长度降序排列，优先匹配长名称 ──
_ALIASES_SORTED = sorted(ALIAS_TO_CANONICAL.keys(), key=len, reverse=True)

# ── 城市 → 省份映射（用于"广州市"→"广东省"这类推断） ──
# 只含副省级城市和常见地级市，避免映射爆炸
CITY_TO_PROVINCE: dict[str, str] = {
    # 广东省
    "广州市": "广东省", "深圳市": "广东省", "珠海市": "广东省",
    "汕头市": "广东省", "佛山市": "广东省", "韶关市": "广东省",
    "湛江市": "广东省", "肇庆市": "广东省", "江门市": "广东省",
    "茂名市": "广东省", "惠州市": "广东省", "梅州市": "广东省",
    "汕尾市": "广东省", "河源市": "广东省", "阳江市": "广东省",
    "清远市": "广东省", "东莞市": "广东省", "中山市": "广东省",
    "潮州市": "广东省", "揭阳市": "广东省", "云浮市": "广东省",
    # 浙江省
    "杭州市": "浙江省", "宁波市": "浙江省", "温州市": "浙江省",
    "嘉兴市": "浙江省", "湖州市": "浙江省", "绍兴市": "浙江省",
    "金华市": "浙江省", "衢州市": "浙江省", "舟山市": "浙江省",
    "台州市": "浙江省", "丽水市": "浙江省",
    # 江苏省
    "南京市": "江苏省", "无锡市": "江苏省", "徐州市": "江苏省",
    "常州市": "江苏省", "苏州市": "江苏省", "南通市": "江苏省",
    "连云港市": "江苏省", "淮安市": "江苏省", "盐城市": "江苏省",
    "扬州市": "江苏省", "镇江市": "江苏省", "泰州市": "江苏省",
    "宿迁市": "江苏省",
    # 山东省
    "济南市": "山东省", "青岛市": "山东省", "淄博市": "山东省",
    "枣庄市": "山东省", "东营市": "山东省", "烟台市": "山东省",
    "潍坊市": "山东省", "济宁市": "山东省", "泰安市": "山东省",
    "威海市": "山东省", "日照市": "山东省", "临沂市": "山东省",
    "德州市": "山东省", "聊城市": "山东省", "滨州市": "山东省",
    "菏泽市": "山东省",
    # 福建省
    "福州市": "福建省", "厦门市": "福建省", "莆田市": "福建省",
    "三明市": "福建省", "泉州市": "福建省", "漳州市": "福建省",
    "南平市": "福建省", "龙岩市": "福建省", "宁德市": "福建省",
    # 四川省
    "成都市": "四川省", "绵阳市": "四川省", "自贡市": "四川省",
    "攀枝花市": "四川省", "泸州市": "四川省", "德阳市": "四川省",
    "广元市": "四川省", "遂宁市": "四川省", "内江市": "四川省",
    "乐山市": "四川省", "南充市": "四川省", "宜宾市": "四川省",
    # 湖北省
    "武汉市": "湖北省", "黄石市": "湖北省", "十堰市": "湖北省",
    "宜昌市": "湖北省", "襄阳市": "湖北省", "鄂州市": "湖北省",
    "荆门市": "湖北省", "孝感市": "湖北省", "荆州市": "湖北省",
    # 湖南省
    "长沙市": "湖南省", "株洲市": "湖南省", "湘潭市": "湖南省",
    "衡阳市": "湖南省", "邵阳市": "湖南省", "岳阳市": "湖南省",
    "常德市": "湖南省", "张家界市": "湖南省",
    # 河南省
    "郑州市": "河南省", "开封市": "河南省", "洛阳市": "河南省",
    "平顶山市": "河南省", "安阳市": "河南省", "新乡市": "河南省",
    "南阳市": "河南省",
    # 河北省
    "石家庄市": "河北省", "唐山市": "河北省", "秦皇岛市": "河北省",
    "邯郸市": "河北省", "保定市": "河北省",
    # 安徽省
    "合肥市": "安徽省", "芜湖市": "安徽省", "蚌埠市": "安徽省",
    "马鞍山市": "安徽省",
    # 辽宁省
    "沈阳市": "辽宁省", "大连市": "辽宁省", "鞍山市": "辽宁省",
    # 吉林省
    "长春市": "吉林省", "吉林市": "吉林省",
    # 黑龙江省
    "哈尔滨市": "黑龙江省", "齐齐哈尔市": "黑龙江省",
    "大庆市": "黑龙江省",
    # 陕西省
    "西安市": "陕西省", "咸阳市": "陕西省",
    # 其他主要城市
    "昆明市": "云南省", "贵阳市": "贵州省",
    "南宁市": "广西壮族自治区",
    "乌鲁木齐市": "新疆维吾尔自治区",
    "兰州市": "甘肃省", "海口市": "海南省",
    "南昌市": "江西省", "太原市": "山西省",
    "呼和浩特市": "内蒙古自治区",
    "银川市": "宁夏回族自治区",
    "西宁市": "青海省",
    "拉萨市": "西藏自治区",
    "台北市": "台湾省",
    "香港": "香港特别行政区",
    "澳门": "澳门特别行政区",
}

# 额外收录"市"字可能省略的城市名
CITY_TO_PROVINCE["广州"] = "广东省"
CITY_TO_PROVINCE["深圳"] = "广东省"
CITY_TO_PROVINCE["成都"] = "四川省"
CITY_TO_PROVINCE["武汉"] = "湖北省"
CITY_TO_PROVINCE["南京"] = "江苏省"
CITY_TO_PROVINCE["杭州"] = "浙江省"
CITY_TO_PROVINCE["西安"] = "陕西省"
CITY_TO_PROVINCE["重庆"] = "重庆市"
CITY_TO_PROVINCE["上海"] = "上海市"
CITY_TO_PROVINCE["北京"] = "北京市"
CITY_TO_PROVINCE["天津"] = "天津市"

# ── 优先级前缀：确保直辖市/自治区不降级 ──
# 当输入是"北京市朝阳区"时，前缀"北京"应优先匹配为直辖市
_DIRECT_PREFIXES = sorted(
    list(MUNICIPALITIES.keys()) + list(SAR.keys()),
    key=len, reverse=True,
)
# 自治区简称前缀
_AUTO_PREFIXES = sorted(
    list(AUTONOMOUS_REGIONS.keys()),
    key=len, reverse=True,
)


def extract_province(location: str) -> Optional[str]:
    """从地点字符串中提取省级行政区。

    处理顺序：
    1. 直接匹配已有规范名称（如"广东省"）
    2. 前缀匹配（如"北京市朝阳区"→"北京市"）
    3. 简称短名称映射（如"广东"→"广东省"）
    4. 城市名反向映射（如"广州市"→"广东省"）

    Args:
        location: 原始地点字符串，如"黑龙江省齐齐哈尔市龙沙区"

    Returns:
        规范化后的省级名称，如"黑龙江省"；无法识别返回 None
    """
    if not location or not location.strip():
        return None

    location = location.strip()

    # Step 1: 直接命中规范名称
    loc = location
    if loc in ALIAS_TO_CANONICAL:
        return ALIAS_TO_CANONICAL[loc]

    # Step 2: 前缀匹配 — 按长度降序遍历
    # 先匹配直辖市前缀（如"北京市朝阳区"中的"北京"）
    for alias in _DIRECT_PREFIXES:
        if loc.startswith(alias):
            return ALIAS_TO_CANONICAL[alias]

    # 再匹配自治区前缀
    for alias in _AUTO_PREFIXES:
        if loc.startswith(alias):
            return ALIAS_TO_CANONICAL[alias]

    # 再匹配普通省份前缀
    for alias in _ALIASES_SORTED:
        if loc.startswith(alias):
            return ALIAS_TO_CANONICAL[alias]

    # Step 3: 正则匹配末尾的省级后缀
    # 匹配 XX省、XX自治区、XX特别行政区
    m = re.search(r'([一-鿿]{2}省)', loc)
    if m:
        name = m.group(1)
        if name in ALIAS_TO_CANONICAL:
            return ALIAS_TO_CANONICAL[name]
    m = re.search(r'([一-鿿]{2,6}(?:自治区|特别行政区))', loc)
    if m:
        name = m.group(1)
        if name in ALIAS_TO_CANONICAL:
            return ALIAS_TO_CANONICAL[name]

    # Step 4: 城市名反向映射
    # 尝试匹配 "XX市"
    for city, province in CITY_TO_PROVINCE.items():
        if city in loc:
            return province

    # Step 5: 兜底 — 去掉已知后缀后再匹配
    cleaned = re.sub(r'(?:省|市|区|县|自治州|地区|盟)$', '', loc)
    if cleaned in ALIAS_TO_CANONICAL:
        return ALIAS_TO_CANONICAL[cleaned]
    # 再去掉更细的行政单位
    cleaned = re.sub(r'[市县区镇乡街道路号]', '', loc)
    for alias in _ALIASES_SORTED:
        if cleaned.startswith(alias):
            return ALIAS_TO_CANONICAL[alias]

    return None


def normalize_province(location: str) -> str:
    """将地点归一化为省级行政区名称。失败时返回原始输入。

    Args:
        location: 原始地点字符串

    Returns:
        规范化的省级名称或原始输入
    """
    result = extract_province(location)
    if result:
        return result
    return location if location else ""
