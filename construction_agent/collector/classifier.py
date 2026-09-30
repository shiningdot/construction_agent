"""建筑施工事故统一分类器。

所有采集器共用的 classify_v2 函数，确保分类标准一致。

核心设计：
  1. 公司类型预筛选：标题中出现工业/非建筑行业关键词 → 需要更严格的施工信号
  2. 施工特定信号：仅在建筑施工场景中高频出现的术语
  3. 强施工信号：建筑施工相关的，但工业场景也可能出现
  4. 强排除信号：明确非建筑施工的行业/事故类型

规则优先级：
  Rule 0: 强排除 + 无特定信号 → 高置信度排除
  Rule 1: 工业公司 + 信号不足 → 高置信度排除
  Rule 2: 施工特定信号 → 高置信度建筑
  Rule 3: 强信号 ≥2 (非工业公司) → 高置信度建筑
  Rule 4: 强信号 ≥3 (工业公司) → 高置信度建筑
  Rule 5: 单一强信号 → 低置信度(审核)
"""

# ═══════════════════════════════════════════════
# 施工特定信号 — 几乎只在建筑施工中出现
# ═══════════════════════════════════════════════
SPECIFIC_CONSTRUCTION = [
    # 施工机械/设备
    "塔式起重机", "塔吊", "施工升降机", "物料提升机",
    # 施工工艺
    "基坑工程", "模板工程", "模板坍塌", "模板支撑",
    "混凝土浇筑", "钢筋绑扎", "土方开挖", "桩基施工",
    "砌筑", "抹灰", "幕墙安装",
    "筏板基础", "地下室施工", "主体结构施工",
    # 建筑结构
    "住宅楼工程", "地下车库浇筑",
    "箱梁垮塌", "箱梁坍塌", "桥梁施工",
    "围墙建设工程", "围墙施工",
    # 人员/单位
    "建筑劳务", "项目部",
    # 项目类型（专属建筑施工）
    "三期项目", "二期项目", "商务办公楼",
    "水利建筑工程有限公司",  # 水利建设公司（施工类）
    # 公司类型（明确是建筑施工企业）
    "建设工程有限公司", "建设集团有限公司", "建筑安装工程有限公司",
]

# ═══════════════════════════════════════════════
# 强施工信号 — 建筑相关但工业也可能出现
# ═══════════════════════════════════════════════
STRONG_CONSTRUCTION = [
    "建筑施工", "房屋建筑工程", "市政工程施工", "市政工程",
    "建设工程施工", "建设工程",
    "施工现场", "建筑工地", "在建工程", "在建项目",
    "标段", "项目工地",
    "脚手架",  # 工业维护也用，需要更多证据
    "高处作业",  # 工业场景极常见，需要更多证据
    "临边防护",
    "建筑拆除工程", "拆除工程安全技术", "JGJ",
    "精装修", "水电工程",
    "房地产开发",
    "污水管道", "上跨", "下穿",
    "沥青拌合站",
    "预制构件", "预制厂",
    "市政管网", "养护维修工程",
    "路桥工程", "盛通路桥",
    "水利建设",
]

# ═══════════════════════════════════════════════
# 工业/非建筑公司关键词 — 出现在标题中需要更严格的判断
# ═══════════════════════════════════════════════
INDUSTRIAL_COMPANY_KW = [
    # 矿山采矿
    "矿业", "矿山", "矿井", "煤矿", "铁矿", "金矿", "采矿",
    # 钢铁冶金
    "钢铁", "炼铁", "炼钢", "冶金", "铸管", "铸造", "铸钢", "轧钢",
    "焦化", "脱硫", "板材",
    # 建材水泥
    "水泥", "砖窑", "耐材", "建材", "玻璃",
    # 化工能源
    "化工", "石化", "燃气", "供热", "热力",
    # 制造
    "食品", "麦芽", "纺织", "服装", "制革", "制鞋",
    "铝业", "铝合金", "铜业",
    "新能源汽车", "汽车销售", "汽车零配件",
    "电子", "电器", "电气设备",
    "造纸", "印刷", "家具",
    # 仓储物流
    "物流园", "仓储", "装卸服务",
    # 电力通信
    "电力工程", "电力管井", "通信管道",
    # 农业
    "生态农业", "养殖", "屠宰",
    # 其他非建筑
    "足疗", "饭店", "洗浴", "酒店",
    "消防", "救援",
]

# ═══════════════════════════════════════════════
# 强排除信号
# ═══════════════════════════════════════════════
STRONG_EXCLUDE = [
    "道路交通事故", "高速公路", "高速路段", "国道", "省道",
    "列车脱轨", "地铁追尾",
    "煤矿", "矿山", "矿业", "矿井", "瓦斯", "煤与瓦斯",
    "渔船", "翻沉", "水上交通", "船舶",
    "山洪", "自然灾害", "防汛", "漫坝",
    "水利枢纽",
    "危险化学品",
    "爆燃", "燃气爆燃",
    "液氨",
]


def classify_v2(title: str, detail_text: str = "") -> dict:
    """统一建筑施工事故分类器。

    Args:
        title: 事故标题
        detail_text: PDF/网页提取的正文内容

    Returns:
        {"is_construction": bool, "confidence": "high"|"low",
         "reason": str, "specific_signals": list, "exclude_signals": list,
         "industrial_company": bool}
    """
    full = title + "\n" + detail_text[:5000]

    specific = [kw for kw in SPECIFIC_CONSTRUCTION if kw in full]
    strong = [kw for kw in STRONG_CONSTRUCTION if kw in full]
    exclude = [kw for kw in STRONG_EXCLUDE if kw in full]
    industrial = [kw for kw in INDUSTRIAL_COMPANY_KW if kw in title]

    result = {
        "specific_signals": specific,
        "exclude_signals": exclude,
        "industrial_company": len(industrial) > 0,
    }

    # ── Rule 0: 强排除信号 → 除非有施工特定信号 ──
    if exclude and not specific:
        result.update(is_construction=False, confidence="high",
                      reason=f"非建筑施工: {', '.join(exclude[:2])}")
        return result

    # ── Rule 1: 施工特定信号 → 高置信度建筑（即使工业公司也认） ──
    if specific:
        result.update(is_construction=True, confidence="high",
                      reason=f"施工特定信号: {specific[0]}")
        return result

    # ── Rule 2: 工业公司 + 信号不足 → 高置信度排除 ──
    if industrial and len(strong) < 3:
        result.update(is_construction=False, confidence="high",
                      reason=f"工业公司({industrial[0]})信号不足")
        return result

    # ── Rule 3: 非工业公司 + 强信号 ≥2 → 高置信度 ──
    if not industrial and len(strong) >= 2:
        result.update(is_construction=True, confidence="high",
                      reason=f"强施工信号({len(strong)}): {', '.join(strong[:3])}")
        return result

    # ── Rule 4: 工业公司 + 强信号 ≥3 → 高置信度（严格标准） ──
    if industrial and len(strong) >= 3:
        result.update(is_construction=True, confidence="high",
                      reason=f"工业公司但强施工信号({len(strong)}): {', '.join(strong[:3])}")
        return result

    # ── Rule 5: 火灾/爆炸 + 无施工信号 → 非建筑 ──
    if any(kw in full for kw in ["火灾事故", "火灾", "爆炸"]):
        if "施工现场" not in full and not specific:
            result.update(is_construction=False, confidence="high",
                          reason="火灾/爆炸事故(无施工信号)")
            return result

    # ── Rule 6: 单一强信号 → 低置信度审核 ──
    if len(strong) == 1:
        prefix = f"工业公司({industrial[0]})+" if industrial else ""
        result.update(is_construction=True, confidence="low",
                      reason=f"{prefix}单一强信号需审核: {strong[0]}")
        return result

    # ── 默认: 证据不足 ──
    result.update(is_construction=False, confidence="low",
                  reason="无足够施工信号")
    return result


# ═══════════════════════════════════════════════
# 通用的标题预筛选函数
# ═══════════════════════════════════════════════

# 默认建筑标题关键词（各采集器可自定义）
DEFAULT_TITLE_KW = [
    "建筑", "建设", "城建", "市政", "施工", "在建", "拆除", "装修",
    "脚手架", "塔吊", "起重机", "升降机", "基坑", "模板",
    "房屋", "楼房", "厂房", "住宅", "小区", "工地",
    "坍塌", "倒塌", "垮塌", "坠落", "高坠",
    "物体打击", "机械伤害", "起重伤害",
    "桥梁", "隧道", "工程",
    "围墙", "电梯",
]

# 非事故记录模式
NON_ACCIDENT_PATTERNS = [
    r"挂牌督办",
    r"暂行办法", r"管理办法", r"实施细则",
    r"关于进一步加强", r"关于加强",
    r"培训班", r"培训会",
    r"生产安全事故报告和调查处理条例",
]


def is_construction_candidate(
    title: str,
    keywords: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> tuple[bool, str]:
    """判断标题是否为建筑施工事故候选。

    Args:
        title: 事故标题
        keywords: 自定义建筑关键词列表（默认使用 DEFAULT_TITLE_KW）
        exclude_patterns: 自定义排除模式（默认 NON_ACCIDENT_PATTERNS）

    Returns:
        (是否为候选, 原因说明)
    """
    if keywords is None:
        keywords = DEFAULT_TITLE_KW
    if exclude_patterns is None:
        exclude_patterns = NON_ACCIDENT_PATTERNS

    for pattern in exclude_patterns:
        if re.search(pattern, title):
            return False, f"非事故记录: {pattern}"

    matched = [kw for kw in keywords if kw in title]
    if matched:
        return True, f"关键词({len(matched)}个): {', '.join(matched[:5])}"
    return False, "无建筑关键词"


import re  # noqa: E402 (needed for is_construction_candidate)
