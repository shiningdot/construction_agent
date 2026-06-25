"""HFACS-CS 六层级技能实现。

每个技能 = 一个独立 HFACS 层级，拥有专注的提示词和 schema。
基于 data/hfacs_cs_taxonomy.json 的 29 个子类定义。
"""

from __future__ import annotations

from construction_agent.versions.v3.skills.base import HFACSSkill


# ── Level 1: Organizational Factors ──

class OrganizationalSkill(HFACSSkill):
    """组织因素技能 — Level 1"""
    level = 1
    name = "组织因素"

    subcategories = [
        ("O1", "安全文化缺失", "组织未建立积极的安全文化，安全主体责任不落实"),
        ("O2", "分包/承包商管理缺陷", "违法分包、转包、使用无资质承包商"),
        ("O3", "工期/成本压力", "不合理压缩工期、削减安全投入"),
        ("O4", "培训体系不健全", "组织层面缺乏系统化的安全培训体系"),
    ]


# ── Level 2: Unsafe Supervision ──

class SupervisionSkill(HFACSSkill):
    """不安全监督技能 — Level 2"""
    level = 2
    name = "不安全监督"

    subcategories = [
        ("S1", "现场监管缺失", "现场缺乏有效的安全监督，监管人员不到位"),
        ("S2", "隐患排查不充分", "未对风险进行识别、评估和控制"),
        ("S3", "安全制度/规程不健全", "安全管理制度缺失、应急预案不完善"),
        ("S4", "违规组织施工", "未经审批施工、违法建设、违规组织作业"),
        ("S5", "安全培训/交底不足", "岗前安全培训和安全技术交底不充分"),
    ]


# ── Level 3: Preconditions for Unsafe Acts ──

class PreconditionSkill(HFACSSkill):
    """不安全行为的前提条件技能 — Level 3"""
    level = 3
    name = "不安全行为的前提条件"

    subcategories = [
        ("P1", "操作技能/经验不足", "作业人员缺乏必要的操作技能或经验"),
        ("P2", "安全意识薄弱", "作业人员对安全风险认知不足、侥幸心理"),
        ("P3", "生理/心理状态不佳", "疲劳、疾病、注意力不集中、酒后作业等"),
        ("P4", "沟通/协调不足", "作业人员间信息传递不畅、协同失误"),
    ]


# ── Level 4: Unsafe Acts ──

class UnsafeActsSkill(HFACSSkill):
    """不安全行为技能 — Level 4"""
    level = 4
    name = "不安全行为"

    subcategories = [
        ("A1", "违章操作", "习惯性违反安全操作规程的行为"),
        ("A2", "违规指挥", "管理人员违章指挥、强令冒险作业"),
        ("A3", "冒险作业", "明知风险仍继续作业"),
        ("A4", "未使用PPE", "未按规定使用个人防护装备"),
        ("A5", "操作失误", "非故意的操作错误"),
    ]


# ── Level 5: Workplace/Site Conditions ──

class SiteConditionSkill(HFACSSkill):
    """施工现场条件技能 — Level 5（建筑施工特有层级）"""
    level = 5
    name = "施工现场条件"

    subcategories = [
        ("W1", "脚手架/模板/支撑体系缺陷", "脚手架、模板支撑系统缺陷或失效"),
        ("W2", "临时设施/防护缺陷", "临边防护、洞口防护、安全网等缺失"),
        ("W3", "设备/机具故障", "施工设备、机具故障或失效"),
        ("W4", "建/构筑物结构缺陷", "建筑物或构筑物本身的结构缺陷"),
        ("W5", "材料/构件质量问题", "施工材料或预制构件的质量问题"),
        ("W6", "地基/基坑条件不良", "地基承载力不足、基坑支护不当"),
    ]


# ── Level 6: Physical Environment ──

class PhysicalEnvSkill(HFACSSkill):
    """物理环境技能 — Level 6"""
    level = 6
    name = "物理环境"

    subcategories = [
        ("E1", "不良地质条件", "地质条件复杂：软土、断层、地下水等"),
        ("E2", "恶劣天气", "降雨、大风、高温、冰冻等不利天气"),
        ("E3", "作业空间/环境复杂", "受限空间、高空作业、交叉作业等"),
        ("E4", "照明/视线不足", "夜间施工、照明不足、视线遮挡"),
        ("E5", "外部干扰", "施工活动以外的震动、车辆、相邻施工等"),
    ]
