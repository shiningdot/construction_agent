"""Step 1: 建筑施工事故基础信息提取器。

从事故文本中提取 31 个客观字段（不含致因分析）。
采用 Few-Shot + tool_use 策略（实验证明 F1=0.737 最优），
自由文本字段（work_task/equipment/worker_type/material）后续经 Phase B 归纳为枚举。

使用方式:
    extractor = Step1BasicExtractor()
    result = extractor.extract(text, metadata={})
    # result 是 Step1Result 对象，可直接 .to_dict()
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from construction_agent.config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Tool Schema (23 LLM 提取字段)
# ══════════════════════════════════════════════════════════════════

STEP1_TOOL = {
    "name": "extract_accident_basic_info",
    "description": "从建筑施工事故文本中提取基础信息（31个字段），不包含致因分析",
    "input_schema": {
        "type": "object",
        "properties": {
            # ── 核心必填 (8) ──
            "title": {
                "type": "string",
                "description": "事故标题。原文有则用原文，原文无则概括（≤30字）"
            },
            "date": {
                "type": "string",
                "description": "事故发生日期，ISO格式YYYY-MM-DD。'昨日''今日'需结合publish_date推算"
            },
            "location": {
                "type": "string",
                "description": "省级行政区全称，如'黑龙江省'。从城市或上下文推断"
            },
            "city": {
                "type": "string",
                "description": "地级市或直辖市名称，如'齐齐哈尔市'"
            },
            "accident_type": {
                "type": "string",
                "enum": [
                    "坍塌", "高处坠落", "触电", "机械伤害", "起重伤害",
                    "物体打击", "土方坍塌", "车辆伤害", "火灾爆炸",
                    "中毒窒息", "淹溺", "其他"
                ]
            },
            "severity": {
                "type": "string",
                "enum": ["一般事故", "较大事故", "重大事故", "特别重大事故"],
                "description": "按死亡人数判定: 0-2一般, 3-9较大, 10-29重大, 30+特别重大"
            },
            "deaths": {"type": "integer", "description": "死亡人数，未提及填0"},
            "injuries": {"type": "integer", "description": "受伤人数，未提及填0"},

            # ── 时空补充 (4) ──
            "accident_time": {
                "type": "string",
                "description": "具体时刻 HH:MM 格式。未提及填null"
            },
            "district": {
                "type": "string",
                "description": "区/县/县级市名称，如'龙沙区'。未提及填null"
            },
            "accident_location_detail": {
                "type": "string",
                "description": "事故发生的具体部位描述，如'体育馆屋顶''3号楼12层电梯井'。未提及填null"
            },
            "weather": {
                "type": "string",
                "description": "天气状况。未提及填'未提及'（默认正常天气）"
            },

            # ── 工程信息 (7) ──
            "project_name": {
                "type": "string",
                "description": "工程项目名称。未提及填null"
            },
            "project_type": {
                "type": "string",
                "enum": ["房屋建筑", "市政工程", "交通运输", "水利工程",
                         "工业厂房", "装饰装修", "拆除工程", "其他/未知"]
            },
            "building_structure": {
                "type": "string",
                "enum": [
                    "BLD01", "BLD02", "BLD03", "BLD04",
                    "BLD05", "BLD06", "BLD07", "BLD08",
                    "BLD09", "BLD10", "BLD11", "BLD12"
                ],
                "description": """建筑结构类型(12类枚举, 参考GB 50011)。按结构承重体系分类:

- BLD01钢结构: 钢材承重. 含钢框架/门式刚架/网架/轻钢/桁架/彩钢板结构. 钢结构≈钢架≈轻钢
- BLD02钢-混凝土混合结构: 钢与混凝土组合承重. 含钢管混凝土/钢骨混凝土/钢混
- BLD03砌体结构: 砖/石/砌块墙体承重. 含砖混/砖木/石砌/砖墙. 砖混≈砖混结构
- BLD04框架结构: 梁柱刚性连接承重. 含现浇框架/全框架/排架/框架式
- BLD05框架-剪力墙结构: 框架与剪力墙共同承重. 含框剪/框架-剪力墙
- BLD06剪力墙结构: 剪力墙承重并抗侧力. 含剪力墙
- BLD07框架-核心筒结构: 外框架与内核心筒组合. 含框架核心筒
- BLD08装配式混凝土结构: 预制构件装配. 含预制板/装配式/大板/升板
- BLD09木结构: 木框架或木构件承重. 含木结构/木架/木质
- BLD10混合结构: 多种结构体系或材料无主导组合. 含混合结构
- BLD11混凝土结构(未指明体系): 仅说明混凝土/钢筋混凝土材料,未明确框架/剪力墙等体系
- BLD12其他结构: 竹/土坯/冰/简易结构等罕见类型

注意: 若原文描述的是工程类型(桥梁/隧道)或施工阶段(主体结构/装饰装修)而非结构体系 → 填null. 未提及填null"""
            },
            "construction_phase": {
                "type": "string",
                "enum": ["地基与基础", "主体结构", "装饰装修", "拆除",
                         "设备安装", "竣工验收", "维保/改造", "未知"]
            },
            "work_task": {
                "type": "string",
                "enum": [
                    "WT01", "WT02", "WT03", "WT04", "WT05",
                    "WT06", "WT07", "WT08", "WT09", "WT10", "WT99"
                ],
                "description": """施工作业类型(11类LLM枚举v2.0)。按以下决策规则从高到低匹配:

【决策规则(优先级从高到低)】
规则1-桥梁隧道优先: 桥梁墩台/梁体/桥面→WT08; 隧道/洞室/盾构/顶管→WT07
规则2-起重设备: 塔吊/龙门架/井架/施工升降机的安装/拆卸/顶升/吊运→WT06
规则3-拆除工程: 建构筑物整体/局部拆除(不含模板/脚手架/塔吊拆卸)→WT09
规则4-模板脚手架: 模板/支撑架/脚手架/操作平台的搭设/使用/拆除→WT03
规则5-装饰装修: 抹灰/涂料/保温/幕墙/门窗/防水/贴砖/砌填充墙→WT04
规则6-机电管线: 水暖电/通风/电梯/市政管道安装(不含沟槽开挖)→WT05
规则7-主体结构: 混凝土浇筑/钢筋制安/预应力/钢结构安装→WT02
规则8-地基基础: 土方/基坑/桩基/地基处理→WT01
规则9-辅助运输: 维修/清理/检测/运输/驾驶/临建等非直接建造活动→WT10
规则10-兜底: 以上均不匹配→WT99

【各代码范围】
- WT01地基与基础工程: 土方开挖回填/基坑支护/桩基施工/地基处理. 不含桥墩桩基(→WT08)
- WT02主体结构工程: 混凝土浇筑振捣养护/钢筋绑扎加工/预应力张拉/钢结构网架安装. 不含桥隧结构
- WT03模板与脚手架工程: 模板搭拆/支撑体系/脚手架/操作平台的全生命周期. 全生命周期原则:搭设/使用/拆除均归此类
- WT04装饰装修工程: 内外墙抹灰涂料/保温/幕墙/门窗/屋面防水/贴砖/非承重砌筑
- WT05机电安装及管线工程: 水暖电/通风/电梯/消防/市政管道安装与试压. 不含沟槽开挖(→WT01)和顶管(→WT07)
- WT06起重设备安拆与吊装: 塔吊/龙门吊/汽车吊/架桥机等起重设备的安装/顶升/吊运/拆卸全生命周期
- WT07隧道与地下工程: 隧道掘进/盾构/支护衬砌/顶管(非开挖)/地下管廊
- WT08桥梁工程: 桥墩台/箱梁/桥面系施工. 架桥机→WT06(起重设备优先)
- WT09拆除工程: 建构筑物拆除/爆破. 不含模板/脚手架拆除(→WT03)和塔吊拆卸(→WT06)
- WT10辅助作业与场内运输: 设备维修保养/结构加固/现场清理保洁/巡查检测/物料搬运场内运输/车辆驾驶/临建
- WT99其他: 跨类混合/非建筑施工/信息严重不足/罕见特殊作业"""
            },
            "contractor": {
                "type": "string",
                "description": "施工单位全称。未提及填null"
            },
            "contractor_type": {
                "type": "string",
                "enum": ["总包", "分包", "劳务分包", "无资质/挂靠", "未提及"]
            },

            # ── 事故细节 (6) ──
            "trapped_count": {
                "type": "integer",
                "description": "被困人数（区别于已确认的死亡/受伤）。未提及填0"
            },
            "direct_economic_loss": {
                "type": "number",
                "description": "直接经济损失，单位万元。未提及填null。注意'经济损失XX万元'的表述"
            },
            "collapse_subtype": {
                "type": "string",
                "enum": ["基坑坍塌", "模板坍塌", "脚手架坍塌", "建筑结构坍塌",
                         "桥梁坍塌", "隧道坍塌", "土方坍塌", "其他坍塌", "不适用"],
                "description": "仅坍塌/土方坍塌类填写。非坍塌类填'不适用'"
            },
            "fall_height": {
                "type": "number",
                "description": "坠落高度（米），仅高处坠落类填写。从文本如'从12米高处'提取数字"
            },
            "equipment_involved": {
                "type": "string",
                "enum": [
                    "EQP01", "EQP02", "EQP03", "EQP04", "EQP05",
                    "EQP06", "EQP07", "EQP08", "EQP09", "EQP10",
                    "EQP11", "EQP12", "EQP13"
                ],
                "description": """事故涉及的施工机械/设备/设施(13类枚举)。按设备功能分类,多人多设备用顿号分隔。

各代码范围:
- EQP01起重机械: 塔吊/汽车吊/履带吊/架桥机/龙门吊等各类起重机. 塔吊≈塔式起重机
- EQP02物料提升设备: 龙门架/井架/井字架/卷扬机/物料提升机(仅载货,不可载人)
- EQP03施工升降设备: 施工升降机/施工电梯(人货两用,有轿厢)
- EQP04高处作业平台: 吊篮/高空作业车/剪叉式升降平台/自行式高空作业平台
- EQP05脚手架: 钢管脚手架/门式脚手架/附着式升降脚手架/竹脚手架. 脚手架≈外架≈架子
- EQP06模板及支撑体系: 模板支撑系统/支模架/模板支架/高支模/模板
- EQP07土方及筑路机械: 挖掘机/装载机/推土机/压路机/平地机/强夯机/铲车. 挖掘机≈挖机≈挖土机
- EQP08桩工及地下工程机械: 打桩机/钻机/盾构机/隧道掘进机/锚杆机
- EQP09混凝土机械: 混凝土泵车/搅拌机/振捣器/混凝土喷射机. 搅拌机≈砼搅拌机
- EQP10运输车辆: 叉车/卡车/手推车/斗车/自卸汽车/混凝土搅拌车/拖拉机
- EQP11小型施工机具: 电钻/电焊机/潜水泵/磨石机/电动葫芦/空压机/电锯等手持或轻便机具
- EQP12施工设施: 卸料平台/人字梯/水泥罐/钢跳板/安全网/配电柜等非机械临时设施
- EQP13其他: 罕见/特殊设备或无法归入以上类别的值. 未提及填null"""
            },
            "material_involved": {
                "type": "string",
                "enum": [
                    "MAT01", "MAT02", "MAT03", "MAT04",
                    "MAT05", "MAT06", "MAT07"
                ],
                "description": """事故涉及的关键材料/物料(7类枚举)。多人多材料用顿号分隔,取第一个材料归类。

各代码范围:
- MAT01混凝土及水泥制品: 混凝土/钢筋混凝土/预制板/水泥/砂浆/水泥板. 混凝土≈砼≈商砼
- MAT02土方及地质材料: 土方/泥土/岩石/碎石/回填土/砂石/黄土. 土方≈泥土≈渣土
- MAT03金属材料: 钢筋/钢管/钢结构/钢模板/彩钢板/钢丝绳/钢板/型钢
- MAT04砌体及石材: 砖/砖块/砌块/石料/石材/大理石/花岗岩
- MAT05木材及竹材: 木板/木方/毛竹/竹材/模板(未注明材质默认归此)
- MAT06危险品及流体物质: 燃气/乙炔/氧气/炸药/油漆/污水/泥浆等危险或流态物质
- MAT07其他材料及杂物: 玻璃/复合材料/建筑垃圾/保温板/沥青/织物/绳索/积雪等. 未提及填null"""
            },

            # ── 人员信息 (1) ──
            "worker_type": {
                "type": "string",
                "enum": [
                    "WKR01", "WKR02", "WKR03", "WKR04",
                    "WKR05", "WKR06", "WKR07", "WKR08",
                    "WKR09", "WKR10", "WKR11", "WKR12", "WKR13"
                ],
                "description": """伤亡人员工种/角色(13类枚举)。多人不同工种用顿号分隔,取第一个工种归类。

各代码范围:
- WKR01通用劳务工: 普工/民工/工人/临时工/力工/杂工等无特定技能标识的劳务人员. 普工≈民工≈施工人员≈建筑工人
- WKR02木工及模板工: 木工/模板工/支模工
- WKR03瓦工/砌筑/抹灰工: 瓦工/泥工/砖工/抹灰工/砌筑工/贴砖工. 瓦工≈泥工≈砖工
- WKR04钢筋工: 钢筋工/钢筋绑扎工
- WKR05混凝土工: 混凝土工/砼工/振捣工
- WKR06架子工: 架工/架子工/脚手架工. 架工≈架子工
- WKR07焊工: 电焊工/焊工/气焊工
- WKR08起重及塔吊作业人员: 起重工/塔吊司机/司索工/信号工/吊车司机. 需持特种作业证
- WKR09施工机械操作工及司机: 挖掘机/铲车/推土机/搅拌机等非起重类机械的操作工和司机
- WKR10机电设备安装与维修工: 电工/机修工/水暖工/管道工/钳工/铆工
- WKR11安装工: 安装工/钢结构安装工/门窗安装工/幕墙安装工/电梯安装工
- WKR12外部人员: 路人/村民/居民/学生/顾客/群众等非施工参与方的第三方人员
- WKR13其他专业人员: 油漆工/防水工/保温工/爆破工/管理人员/技术员/测量员等低频工种. 未提及填null"""
            },

            # ── 元数据 (2) ──
            "report_type": {
                "type": "string",
                "enum": ["新闻报道", "事故调查报告", "政府通报", "教材/案例", "其他"],
                "description": "文本的体裁类型"
            },
            "summary": {
                "type": "string",
                "description": "事故经过摘要，100-200字，包含时间地点事件伤亡四要素"
            },

            # ── 新增补充字段 ──
            "developer": {
                "type": "string",
                "description": "建设单位（甲方/业主）名称。未提及填null"
            },
            "supervision_company": {
                "type": "string",
                "description": "监理单位名称。未提及填null"
            },

            # ── 直接原因 (1) ──
            # 2026-09-30 补：此字段此前只由旧链路 claude_extractor.py:114 产出，
            # S1–S4 链路缺失，而它是详情页 A 档必显字段（既有 2,623 条有值率 99.89%）。
            # 不补上则新管道并入的数据在这一列全为 NULL，新老数据不同构。
            "direct_cause": {
                "type": "string",
                "description": (
                    "事故直接原因简述，一句话，如'沟槽未按规定放坡致边坡坍塌'、"
                    "'违规堆放珍珠岩经雨水浸泡增重致屋顶超载'。"
                    "只写直接致害环节（物的不安全状态或人的不安全行为），"
                    "不写管理责任、不写整改建议——那部分由致因编码环节负责。"
                    "不确定填空字符串"
                )
            },
        },
        "required": [
            "title", "date", "location", "city",
            "accident_type", "severity", "deaths", "injuries",
            "summary"
        ]
    }
}


# ══════════════════════════════════════════════════════════════════
# System Prompt (Few-Shot)
# ══════════════════════════════════════════════════════════════════

STEP1_SYSTEM_PROMPT = """你是一名建筑施工事故基础信息提取专家。从事故文本中提取结构化数据。
使用 extract_accident_basic_info 函数输出。遵循以下规则:

【核心规则】
1. 日期: "昨日""今日"需根据publish_date推算。"近日"如无具体日期填null
2. 事故类型: 严格从11种枚举中选择。"XX坍塌"选具体类型(土方坍塌/坍塌)
   - 土方/基坑/沟槽/边坡坍塌→土方坍塌
   - 建筑/结构/屋顶/墙体坍塌→坍塌
3. 死亡/受伤/被困人数: 未提及填0，不编造。"多人"→填已知最小数
4. 严重等级: 按死亡人数: 一般(0-2)、较大(3-9)、重大(10-29)、特别重大(30+)
5. collapse_subtype: 仅坍塌类填写，非坍塌类填"不适用"
6. fall_height: 仅高处坠落类填写，从"从X米高处""X米高"中提取数字
7. work_task: 从11类枚举中选择(WT01-WT10,WT99)。判定决策规则(从高到低):
   a. 桥梁/隧道优先: 桥梁→WT08, 隧道/盾构/顶管→WT07
   b. 起重设备: 塔吊/龙门架/施工升降机安装/拆卸/顶升/吊运→WT06
   c. 建构筑物拆除(不含模板/脚手架/塔吊拆卸)→WT09
   d. 模板/脚手架/支撑体系→WT03 (全生命周期:搭设/使用/拆除均归此类)
   e. 装饰装修: 抹灰/涂料/保温/幕墙/门窗/防水/贴砖→WT04
   f. 机电管线: 水暖电/通风/电梯/市政管道→WT05
   g. 主体结构: 混凝土/钢筋/预应力/钢结构→WT02
   h. 地基基础: 土方/基坑/桩基/地基处理→WT01
   i. 辅助运输: 维修/清理/检测/运输/驾驶→WT10
   j. 都不匹配→WT99
8. equipment_involved: 从13类枚举中选择(EQP01-EQP13)。按设备功能分类，多人多设备用顿号。
   同义词注意: 塔吊≈塔式起重机→EQP01, 龙门架≠龙门吊(龙门架→EQP02,龙门吊→EQP01)
9. material_involved: 从7类枚举中选择(MAT01-MAT07)。按材质分类，多材料取第一个。
10. worker_type: 从13类枚举中选择(WKR01-WKR13)。取第一个工种。同义词: 普工≈民工≈工人→WKR01
11. 无信息=null或0(整数)或"未提及"(字符串)，不要编造
12. direct_cause: 一句话(≤40字)写直接致害环节，如"沟槽未按规定放坡致边坡坍塌"、
    "违规堆放珍珠岩经雨水浸泡增重致屋顶超载"。只写直接原因，不写管理责任、
    不写整改建议(那部分归致因编码环节)。不确定填空字符串

## Few-Shot 示例

示例1: 坍塌事故
输入: "2024年3月15日14时30分许，黑龙江省齐齐哈尔市龙沙区第三十四中学体育馆在屋顶防水施工过程中发生坍塌，施工方将珍珠岩违规堆放在屋顶，降雨后珍珠岩增重导致屋顶结构超载坍塌，造成11人死亡、4人受伤。施工单位为黑龙江嘉美建设有限公司，无建筑工程施工资质。监理单位未制止违规堆放。"
publish_date: "2024-03-16"
输出: {title:"齐齐哈尔市第三十四中学体育馆屋顶坍塌事故",date:"2024-03-15",accident_time:"14:30",location:"黑龙江省",city:"齐齐哈尔市",district:"龙沙区",project_name:"第三十四中学体育馆",project_type:"房屋建筑",construction_phase:"装饰装修",accident_type:"坍塌",severity:"重大事故",deaths:11,injuries:4,trapped_count:0,collapse_subtype:"建筑结构坍塌",work_task:"WT04",equipment_involved:null,material_involved:"MAT07",worker_type:null,weather:"未提及",contractor:"黑龙江嘉美建设有限公司",contractor_type:"无资质/挂靠",developer:null,supervision_company:null,direct_cause:"违规堆放珍珠岩经雨水浸泡增重，超过屋顶结构承载能力致坍塌",report_type:"新闻报道",summary:"2024年3月15日14时30分，黑龙江省齐齐哈尔市龙沙区第三十四中学体育馆在屋顶防水施工时，因违规堆放珍珠岩经雨水浸泡增重，导致屋顶结构超载坍塌，造成11人死亡、4人受伤。施工单位无资质承揽，监理单位失职。"}

示例2: 高处坠落
输入: "2019年6月18日下午15时许，浙江省杭州市滨江区一在建商业大楼施工现场，一名木工在12层电梯井道作业时，因临边防护栏杆缺失，从约36米高处坠落至底层，当场死亡。"
publish_date: "2019-06-19"
输出: {title:"杭州滨江一在建大楼电梯井高处坠落事故",date:"2019-06-18",accident_time:"15:00",location:"浙江省",city:"杭州市",district:"滨江区",project_name:"在建商业大楼",project_type:"房屋建筑",construction_phase:"主体结构",accident_type:"高处坠落",severity:"一般事故",deaths:1,injuries:0,fall_height:36.0,work_task:"WT03",equipment_involved:null,material_involved:null,worker_type:"WKR02",weather:"未提及",contractor:null,contractor_type:"未提及",direct_cause:"电梯井临边防护栏杆缺失，木工在12层作业时坠落至底层",report_type:"新闻报道",summary:"2019年6月18日15时，杭州市滨江区一在建商业大楼12层电梯井道，一名木工因临边防护栏杆缺失从约36米处坠落，当场死亡。"}

示例3: 起重伤害
输入: "2021年7月15日上午10时许，广东省深圳市南山区某超高层项目工地，塔吊在吊运钢筋过程中基础节螺栓松动导致整体倾覆，砸中下方作业面，造成2名钢筋工死亡、1名信号工受伤。施工单位为中建X局，总包单位。"
publish_date: "2021-07-16"
输出: {title:"深圳南山超高层项目塔吊倾覆事故",date:"2021-07-15",accident_time:"10:00",location:"广东省",city:"深圳市",district:"南山区",project_name:"超高层项目",project_type:"房屋建筑",construction_phase:"主体结构",accident_type:"起重伤害",severity:"一般事故",deaths:2,injuries:1,work_task:"WT06",equipment_involved:"EQP01",material_involved:"MAT03",worker_type:"WKR04、WKR08",weather:"未提及",contractor:"中建X局",contractor_type:"总包",direct_cause:"塔吊基础节螺栓松动未紧固，吊运钢筋时整体倾覆",report_type:"新闻报道",summary:"2021年7月15日10时，深圳市南山区某超高层项目塔吊在吊运钢筋时基础节螺栓松动倾覆，造成2死1伤。"}

示例4: 中毒窒息
输入: "2020年7月3日上午11时许，江苏省泰兴市一污水管网工地，3名工人在未进行气体检测、未佩戴防毒面具的情况下进入井下作业，因硫化氢中毒晕倒。井下通风不良。未制定有限空间作业方案。事故造成1人死亡、2人重伤。"
publish_date: "2020-07-03"
输出: {title:"泰兴污水管网工地硫化氢中毒事故",date:"2020-07-03",accident_time:"11:00",location:"江苏省",city:"泰兴市",project_name:"污水管网工程",project_type:"市政工程",construction_phase:"主体结构",accident_type:"中毒窒息",severity:"一般事故",deaths:1,injuries:2,work_task:"WT05",equipment_involved:null,material_involved:"MAT06",worker_type:"WKR01",weather:"未提及",contractor:null,contractor_type:"未提及",direct_cause:"未进行气体检测、未佩戴防护面具即进入井下作业，硫化氢中毒晕倒",report_type:"新闻报道",summary:"2020年7月3日11时，泰兴市污水管网工地3名工人未检测气体、未戴防毒面具进入井下作业，因硫化氢中毒造成1死2伤。"}

示例5: 土方坍塌
输入: "2022年8月21日16时左右，四川省成都市天府新区某市政排水管网工程沟槽开挖施工中，因未按规定放坡(实际1:0.3，方案要求1:0.75)，且沟槽边1米范围内堆放弃土，导致边坡突然坍塌，将正在沟槽底部作业的2名工人掩埋致死。当日天气晴。"
publish_date: "2022-08-22"
输出: {title:"成都天府新区排水管沟土方坍塌事故",date:"2022-08-21",accident_time:"16:00",location:"四川省",city:"成都市",district:"天府新区",project_name:"市政排水管网工程",project_type:"市政工程",construction_phase:"地基与基础",accident_type:"土方坍塌",severity:"一般事故",deaths:2,injuries:0,collapse_subtype:"土方坍塌",work_task:"WT01",equipment_involved:null,material_involved:"MAT02",worker_type:"WKR01",weather:"晴",contractor:null,contractor_type:"未提及",direct_cause:"沟槽未按规定放坡(实际1:0.3，方案要求1:0.75)且槽边1米内堆土致边坡坍塌",report_type:"新闻报道",summary:"2022年8月21日16时，成都天府新区排水管沟因未按规定放坡且沟槽边堆土导致边坡坍塌，掩埋2名工人致死。"}"""


# ══════════════════════════════════════════════════════════════════
# 输出数据类
# ══════════════════════════════════════════════════════════════════

@dataclass
class Step1Result:
    """Step 1 提取结果。31 个字段的完整集合。"""

    # 核心 (8 required + 4 optional)
    title: str = ""
    date: str = ""
    location: str = ""
    city: str = ""
    accident_type: str = ""
    severity: str = ""
    deaths: int = 0
    injuries: int = 0
    accident_time: Optional[str] = None
    district: Optional[str] = None
    accident_location_detail: Optional[str] = None
    weather: str = "未提及"

    # 工程信息 (7)
    project_name: Optional[str] = None
    project_type: str = "其他/未知"
    building_structure: Optional[str] = None
    construction_phase: str = "未知"
    work_task: Optional[str] = None
    contractor: Optional[str] = None
    contractor_type: str = "未提及"

    # 事故细节 (6)
    trapped_count: int = 0
    direct_economic_loss: Optional[float] = None
    collapse_subtype: str = "不适用"
    fall_height: Optional[float] = None
    equipment_involved: Optional[str] = None
    material_involved: Optional[str] = None

    # 人员 (1)
    worker_type: Optional[str] = None

    # 直接原因 (1) —— 2026-09-30 新增，说明见 STEP1_TOOL 中同名字段
    direct_cause: str = ""

    # 元数据 (3)
    report_type: str = "新闻报道"
    summary: str = ""
    developer: Optional[str] = None
    supervision_company: Optional[str] = None

    # 内部
    _raw_response: Optional[dict] = field(default=None, repr=False)
    _processing_time: float = 0.0

    def to_dict(self) -> dict:
        """转为 JSON 兼容字典，None 值保留。"""
        return {
            "title": self.title,
            "date": self.date,
            "accident_time": self.accident_time,
            "location": self.location,
            "city": self.city,
            "district": self.district,
            "accident_location_detail": self.accident_location_detail,
            "project_name": self.project_name,
            "project_type": self.project_type,
            "building_structure": self.building_structure,
            "construction_phase": self.construction_phase,
            "accident_type": self.accident_type,
            "severity": self.severity,
            "deaths": self.deaths,
            "injuries": self.injuries,
            "trapped_count": self.trapped_count,
            "direct_economic_loss": self.direct_economic_loss,
            "collapse_subtype": self.collapse_subtype,
            "fall_height": self.fall_height,
            "work_task": self.work_task,
            "equipment_involved": self.equipment_involved,
            "material_involved": self.material_involved,
            "worker_type": self.worker_type,
            "weather": self.weather,
            "contractor": self.contractor,
            "contractor_type": self.contractor_type,
            "developer": self.developer,
            "supervision_company": self.supervision_company,
            "direct_cause": self.direct_cause,
            "report_type": self.report_type,
            "summary": self.summary,
            "_processing_time": self._processing_time,
        }


# ══════════════════════════════════════════════════════════════════
# 提取器
# ══════════════════════════════════════════════════════════════════

class Step1BasicExtractor:
    """Step 1 基础信息提取器。

    使用 Few-Shot + tool_use 从事故文本中提取 23 个基础字段。

    用法:
        extractor = Step1BasicExtractor()
        result = extractor.extract(text, metadata={"publish_date": "2024-03-16"})
        print(result.to_dict())
    """

    def __init__(self, api_key: Optional[str] = None):
        from anthropic import Anthropic
        key = api_key or settings.ANTHROPIC_API_KEY
        self.client = Anthropic(api_key=key, base_url=settings.ANTHROPIC_BASE_URL) if key else None
        self._model = settings.ANTHROPIC_MODEL
        self._max_tokens = getattr(settings, 'ANTHROPIC_MAX_TOKENS', 4096)

    def extract(self, text: str, metadata: Optional[dict] = None) -> Optional[Step1Result]:
        """从文本中提取基础信息。"""
        if not self.client:
            logger.error("Anthropic 客户端未初始化")
            return None

        publish_date = (metadata or {}).get("publish_date", "")
        t0 = time.time()

        user_msg = f"请提取以下建筑施工事故的基础信息：\n\n{text}"
        if publish_date:
            user_msg += f"\n\n【文本发布日期: {publish_date}】"

        try:
            response = self.client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=0.0,
                system=STEP1_SYSTEM_PROMPT,
                tools=[STEP1_TOOL],
                messages=[{"role": "user", "content": user_msg}],
            )
            parsed = self._parse_tool_response(response)
            if parsed:
                normalized = self._normalize_result(parsed)
                result = Step1Result(**normalized)
                result._raw_response = parsed
                result._processing_time = time.time() - t0
                return result

            logger.warning("tool_use 解析失败，重试纯JSON模式")
            retry = self.client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=0.0,
                system="你是施工安全专家。从事故文本提取基础信息。只输出纯JSON。包含title,date,location,city,accident_type,severity,deaths,injuries,summary。",
                messages=[{"role": "user", "content": text}],
            )
            parsed = self._parse_json_response(retry)
            if parsed:
                normalized = self._normalize_result(parsed)
                result = Step1Result(**normalized)
                result._raw_response = parsed
                result._processing_time = time.time() - t0
                return result

        except Exception as e:
            logger.error(f"提取失败: {type(e).__name__}: {e}")

        return None

    def extract_batch(
        self, items: list[dict], max_concurrency: int = 5
    ) -> list[Optional[Step1Result]]:
        """批量提取，并发加速。

        Args:
            items: [{"text": "...", "publish_date": "...", ...}, ...]
            max_concurrency: 并发数

        Returns:
            与 items 同长度的结果列表
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        n = len(items)
        results: list[Optional[Step1Result]] = [None] * n
        logger.info(f"批量提取 {n} 条, 并发={max_concurrency}")

        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            futures = {}
            for i, item in enumerate(items):
                text = item.get("text", "") or item.get("content", "")
                meta = {k: v for k, v in item.items() if k != "text" and k != "content"}
                futures[executor.submit(self.extract, text, meta)] = i

            completed = 0
            for future in as_completed(futures):
                i = futures[future]
                try:
                    results[i] = future.result()
                except Exception as e:
                    logger.error(f"提取异常 [{i}]: {e}")
                completed += 1
                if completed % 10 == 0 or completed == n:
                    elapsed = [r._processing_time for r in results if r]
                    avg_t = sum(elapsed) / max(len(elapsed), 1)
                    logger.info(f"提取进度: {completed}/{n} (平均{avg_t:.1f}s/条)")

        return results

    # ══════════════════════════════════════════════════════════
    # 结果规范化
    # ══════════════════════════════════════════════════════════

    # LLM 可能输出这些字符串表示"无"，统一转为 None
    _NULL_STRINGS = {"未提及", "null", "无", "未知", "不清楚", "不明确", "暂无", "nullm"}

    @classmethod
    def _normalize_result(cls, raw: dict) -> dict:
        """后处理规范化：清理 LLM 输出的不一致格式。"""
        cleaned = {}

        for key, value in raw.items():
            # 字符串字段：清理 "未提及"→None
            if isinstance(value, str):
                v = value.strip()
                if v.lower() in cls._NULL_STRINGS or v in cls._NULL_STRINGS:
                    cleaned[key] = None
                else:
                    cleaned[key] = v
            # 数字字段：处理 "nullm"/"未知"等异常值
            elif value is None:
                cleaned[key] = None
            else:
                cleaned[key] = value

        # 确保 integer 字段是整数
        for int_field in ["deaths", "injuries", "trapped_count"]:
            if int_field in cleaned and cleaned[int_field] is not None:
                try:
                    cleaned[int_field] = int(cleaned[int_field])
                except (ValueError, TypeError):
                    cleaned[int_field] = 0

        # fall_height: 处理 "nullm"、字符串数字等
        if "fall_height" in cleaned and cleaned["fall_height"] is not None:
            try:
                cleaned["fall_height"] = float(cleaned["fall_height"])
            except (ValueError, TypeError):
                cleaned["fall_height"] = None

        # direct_economic_loss: 同理
        if "direct_economic_loss" in cleaned and cleaned["direct_economic_loss"] is not None:
            try:
                cleaned["direct_economic_loss"] = float(cleaned["direct_economic_loss"])
            except (ValueError, TypeError):
                cleaned["direct_economic_loss"] = None

        return cleaned

    @staticmethod
    def _parse_tool_response(response) -> Optional[dict]:
        """从 tool_use 响应中解析提取结果。"""
        for block in response.content:
            if hasattr(block, 'type') and block.type == 'thinking':
                continue
            if hasattr(block, 'type') and block.type == 'tool_use':
                return dict(block.input) if hasattr(block, 'input') else None
            if hasattr(block, 'text'):
                # 有时 tool_use 被包在 text 里
                text = block.text.strip()
                try:
                    data = json.loads(text)
                    if isinstance(data, dict) and 'title' in data:
                        return data
                except (json.JSONDecodeError, TypeError):
                    pass
        return None

    @staticmethod
    def _parse_json_response(response) -> Optional[dict]:
        """从纯 JSON 响应中解析（重试回退）。"""
        for block in response.content:
            if hasattr(block, 'type') and block.type == 'thinking':
                continue
            if hasattr(block, 'text'):
                text = block.text.strip()
                # 策略: 直接解析 / 代码块 / 正则
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    pass
                m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except (json.JSONDecodeError, TypeError):
                        pass
                m = re.search(r'\{[\s\S]*"title"[\s\S]*\}', text)
                if m:
                    try:
                        return json.loads(m.group(0))
                    except (json.JSONDecodeError, TypeError):
                        pass
        return None
