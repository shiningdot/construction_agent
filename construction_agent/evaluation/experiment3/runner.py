"""实验1: 6种 CAICS Prompt 策略对比 — 对标 Liu et al. Table 3"""

import json, re, time, os, sys
from pathlib import Path
from collections import Counter, defaultdict
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=True)

from anthropic import Anthropic

GT_PATH = Path("data/ground_truth/expertA.json")
SAMPLES_PATH = Path("data/ground_truth/gt_samples_100.json")
OUT_DIR = Path("data/experiments/exp3_6strategies")
OUT_DIR.mkdir(parents=True, exist_ok=True)

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.getenv("ANTHROPIC_MODEL")

# ── CAICS Taxonomy (shared by all strategies) ──
CAICS_TAXONOMY = """CAICS框架 (5层19类) — 每类含操作定义和典型关键词:

M.管理根基 — 系统性安全管理缺陷:
  M1现场监管缺失: 监理/安全员未在事故发生前发现并制止不安全状态
     关键词: 无人监管、监理失职、安全管理混乱、未及时发现制止、现场无安全员
  M2隐患排查失效: 执行层面未能识别已存在的危险源，或未在规定时间内整改关闭
     关键词: 未排查、未整改、隐患长期存在、未进行风险评估、检查走过场
  M3安全培训不足: 作业人员未接受与岗位风险匹配的安全培训，缺乏必要的安全知识
     关键词: 未经培训、安全交底不到位、交底走过场、缺乏安全知识、三级教育不足
  M4制度规程漏洞: 安全管理制度、施工方案、应急预案缺失或不完备
     关键词: 无安全方案、应急预案缺失、制度不健全、无施工方案、未编制专项方案
  M5市场行为违规: 违法分包/转包/挂靠/无资质/违规压缩工期等系统性降低安全投入
     关键词: 违法分包、无资质、转包、抢工期、未办理报建、挂靠

F.防线失效 — 物理屏障/防护的缺失或失效:
  F1临边洞口防护缺失: 临边/洞口未按规范设置防护栏杆、安全网、盖板等防坠落设施
     关键词: 无防护、无护栏、临边未设防、洞口未盖、未设置安全网、防护栏杆缺失
  F2支撑体系失稳: 模板支撑/脚手架/基坑支护因承载力不足或构造缺陷导致失稳
     关键词: 模板坍塌、脚手架失稳、支撑不足、支架倒塌、未按方案搭设、扣件松动
  F3设备安全装置失效: 特种设备的安全限位器/防坠器/力矩限制器被屏蔽或损坏
     关键词: 限位器被屏蔽、防坠器失效、安全装置损坏、保险装置失灵
  F4个体防护未使用: 危险作业中未佩戴安全帽/安全带/防滑鞋等个人防护装备
     关键词: 未系安全带、未戴安全帽、无防护用品、未使用PPE、安全带未挂点

T.触发条件 — 直接触发事故的物质或环境条件:
  T1设备机具故障: 施工机械/设备在运行中发生不可预期的断裂/失灵/失效
     关键词: 设备故障、塔吊倒塌、钢丝绳断裂、机械失灵、升降机故障、制动失效
  T2物料状态异常: 材料/构件/堆载物因性质变化(浸水/老化/超载)导致失稳
     关键词: 堆载超限、材料缺陷、荷载过大、物料失稳、珍珠岩浸水、瓦斯积聚
  T3地质环境突变: 地质条件/地下水状态突变(涌水/流沙/边坡滑动/地基沉降)
     关键词: 涌水、边坡失稳、地基沉降、基坑坍塌、土体滑移、流沙
  T4气象水文触发: 暴雨/大风/冰雪/雷电/高温等极端天气直接触发或降低安全性
     关键词: 暴雨、大风、冰雪、雷电、六级风、连日降雨、高温

H.人的行为 — 直接导致事故的人为行为:
  H1违反操作规程: 行为明确违反施工方案/安全操作规程/技术规范/法律法规
     关键词: 未按方案放坡、违反操作规程、擅自变更施工方案、违规拆除
  H2违规指挥决策: 管理人员明知风险仍发出指令要求工人继续或冒险作业
     关键词: 强令冒险作业、明知风险仍施工、违章指挥、要求工人冒险进入
  H3风险辨识失败: 未能识别危险或评估不足("没意识到""疏忽大意""判断失误")
     关键词: 未意识到危险、安全意识薄弱、未检查确认、疏忽大意、低估了风险
  H4应急行为失当: 事故后盲目施救/未及时撤离/救援方法错误导致伤亡扩大
     关键词: 盲目施救、未及时撤离、救援不当导致伤亡扩大、二次坍塌时仍在施救

C.后果放大 — 使事故后果超出预期的因素:
  C1救援响应迟缓: 从事故到有效救援间隔过长，或救援效率低下(无预案/设备不足)
     关键词: 救援不及时、无预案、二次坍塌、延误抢救、救援力量不足
  C2信息迟报瞒报: 责任单位未按规定及时上报或故意隐瞒/延迟报告事故信息
     关键词: 未及时上报、瞒报、迟报、漏报、未按规定报告、拖延上报"""

OUTPUT_JSON = """输出最终JSON。字段要求:
- codes: CAICS编码列表,如["M1","F2","H1"]
- accident_type: 必须从以下12类严格选择: 坍塌|高处坠落|触电|机械伤害|起重伤害|物体打击|土方坍塌|车辆伤害|火灾爆炸|中毒窒息|淹溺|其他
- severity: 必须从以下4级严格选择: 一般事故|较大事故|重大事故|特别重大事故
- deaths: 整数
- injuries: 整数
- direct_cause: 一句话简述

示例: {"codes":["M1","F2"], "accident_type":"坍塌", "severity":"较大事故", "deaths":3, "injuries":2, "direct_cause":"模板支撑体系失稳导致浇筑时坍塌"}"""
OUTPUT_JSON_EMPTY = """若无相关编码，codes可为空数组[]，但accident_type和severity必须填写。"""

# 3个真实GT案例
FEWSHOT_EXAMPLES = """以下3个真实案例展示了如何提取CAICS编码：

示例1(坍塌/特别重大/74死2伤):
文本:"江西丰城电厂三期工程冷却塔施工平桥吊坍塌，造成74人死亡2人受伤。工人在拆除冷凝塔外围木制脚手架时，尚未干透的混凝土开始脱落，导致整个施工平台坍塌。"
输出:{"codes":["M1","M2","F2","T2","H1"],"accident_type":"坍塌","severity":"特别重大事故","deaths":74,"injuries":2,"direct_cause":"拆除脚手架时混凝土未干透导致平台整体坍塌"}

示例2(起重伤害/重大/12死):
文本:"福建霞浦县一建筑工地发生载人升降机坠落事故，造成12人死亡。升降机未经检验合格即投入使用，施工单位安全管理混乱，未对作业人员进行安全培训。"
输出:{"codes":["M1","M3","M4","F2","T1","H1","H2"],"accident_type":"起重伤害","severity":"重大事故","deaths":12,"injuries":0,"direct_cause":"升降机钢丝绳断裂且未经检验合格投入使用"}

示例3(高处坠落/重大/10死2伤):
文本:"西安市凯玄大厦工地发生附着式升降脚手架坠落事故，造成10人死亡2人受伤。事故发生时脚手架从20层坠落，工程存在违法分包、安全监管薄弱等问题。"
输出:{"codes":["M1","M5","F2","H1","H2"],"accident_type":"高处坠落","severity":"重大事故","deaths":10,"injuries":2,"direct_cause":"附着式升降脚手架从20层坠落"}"""


# ── 6 Strategy Definitions ──

STRATEGIES = {
    "S0_ZeroShot": {
        "system": f"""你是施工安全事故分析专家。从事故文本中提取致因编码。

{CAICS_TAXONOMY}

{OUTPUT_JSON}
{OUTPUT_JSON_EMPTY}""",
        "user_prefix": "请为以下事故提取CAICS编码：\n\n"
    },

    "S1_FewShot": {
        "system": f"""你是施工安全事故分析专家。从事故文本中提取致因编码。

{CAICS_TAXONOMY}

{FEWSHOT_EXAMPLES}

{OUTPUT_JSON}""",
        "user_prefix": "请参照示例为以下事故提取CAICS编码：\n\n"
    },

    "S2_AutoCoT": {
        "system": f"""你是施工安全事故分析专家。从事故文本中提取致因编码。

{CAICS_TAXONOMY}

请按以下因果链逐步推理：M(管理缺陷)→F(防线失效)→T(触发条件)→H(人的行为)→C(后果放大)。
对每一层：判断文本中是否有相关证据，有则列出编码，无则说明"该层无编码"。
输出格式：先写一段逐层推理文字，最后单独一行输出最终JSON。JSON放在最后。""",
        "user_prefix": "请按M→F→T→H→C因果链分析以下事故：\n\n"
    },

    "S3_PlanAndSolve": {
        "system": f"""你是施工安全事故分析专家。从事故文本中提取致因编码。

{CAICS_TAXONOMY}

{OUTPUT_JSON}

Let's first make a plan to analyze the accident causes, then solve it step by step.
先制定分析计划，再按计划逐步提取编码。""",
        "user_prefix": "请为以下事故制定分析计划并执行：\n\n"
    },

    "S4_CAICSCoT": {
        "system": f"""你是建筑施工安全事故分析专家。使用CAICS框架按M→F→T→H→C因果链逐层分析。

核心原则: 同时运用显性证据匹配和隐性领域推断。部分致因在文本中无直接关键词，但基于事故性质和施工行业共性规律可合理推断——请标记为"inferred"，关键词匹配的标记为"explicit"。宁可多标不漏标。

M.管理根基 — 系统性安全管理缺陷（注意: 几乎所有严重事故都存在管理缺陷）：
   M1现场监管缺失: 线索: 无人监管/监理失职/安全管理混乱。**推断**: 发生死亡事故本身即说明现场监管严重缺失，即使文本未直接提及也应输出M1[inferred]
   M2隐患排查失效: 线索: 未排查/未整改/隐患长期存在。**推断**: 若事故涉及已存在的结构缺陷或设备问题而未被发现→M2[inferred]
   M3安全培训不足: 线索: 未经培训/交底走过场/缺乏安全知识。**推断**: 工人明显违章操作但文本未提培训→M3[inferred]
   M4制度规程漏洞: 线索: 无安全方案/应急预案缺失/制度不健全
   M5市场行为违规: 线索: 违法分包/无资质/转包/抢工期/未办理报建

F.防线失效 — 物理屏障/防护的缺失或失效（注意: 事故=防线被突破，根据事故类型反推缺失的防线）：
   F1临边洞口防护缺失: 线索: 无防护/无护栏/临边未设防。**推断**: 高处坠落事故→F1[inferred]
   F2支撑体系失稳: 线索: 模板坍塌/脚手架失稳/支撑不足。**推断**: 坍塌事故涉及混凝土浇筑/模板→F2[inferred]
   F3设备安全装置失效: 线索: 限位器被屏蔽/防坠器失效/安全装置损坏
   F4个体防护未使用: 线索: 未系安全带/未戴安全帽。**推断**: 高处坠落或物体打击伤亡→F4[inferred]

T.触发条件 — 直接触发事故的物质或环境条件：
   T1设备机具故障: 线索: 设备故障/钢丝绳断裂/机械失灵
   T2物料状态异常: 线索: 堆载超限/荷载过大/珍珠岩浸水/瓦斯积聚。**推断**: 坍塌事故未见明确触发物→T2[inferred]
   T3地质环境突变: 线索: 涌水/边坡失稳/地基沉降。**推断**: 基坑/沟槽/隧道事故→T3[inferred]
   T4气象水文触发: 线索: 暴雨/大风/冰雪/连日降雨

H.人的行为 — 直接导致事故的人为行为（注意: 几乎每条事故都涉及人的行为）：
   H1违反操作规程: 线索: 明确违反方案/规程/规范。**推断**: 事故中工人行为违背基本安全常识→H1[inferred]
   H2违规指挥决策: 线索: 强令冒险/违章指挥。**推断**: 涉及多人同时作业的事故往往存在指挥问题→H2[inferred]
   H3风险辨识失败: 线索: 未意识到危险/安全意识薄弱/疏忽大意。**推断**: 工人进入了本应避开的危险区域→H3[inferred]
   H4应急行为失当: 线索: 盲目施救/未及时撤离/救援不当扩大伤亡

C.后果放大 — 使事故后果超出预期的因素：
   C1救援响应迟缓: 线索: 救援不及时/无预案/二次坍塌。**推断**: 若死亡≥3人且文本未提救援细节→C1[inferred]
   C2信息迟报瞒报: 线索: 未及时上报/瞒报/迟报/漏报

{OUTPUT_JSON}
分析完毕后在最后一行输出JSON，codes为explicit和inferred编码的合集（不带标记）。""",
        "user_prefix": "请按CAICS因果链逐层分析以下事故：\n\n"
    },
}

# S5: CAICS-CoT+ — 5次独立API调用，逐层串行。前层reasoning注入后层
S5_LAYERS = [
    {
        "layer": "M", "prefix": "M",
        "system": """你是安全管理专家。从事故文本中识别管理根基(M层)致因。
注意: 发生死亡事故本身即说明现场监管严重缺失，需同时考虑显性证据和隐性推断。

M1现场监管缺失: 线索: 无人监管/监理失职/安全管理混乱。**推断**: 几乎所有死亡事故都存在M1，即使文本未直接提及也应考虑输出M1
M2隐患排查失效: 线索: 未排查/未整改/隐患长期存在。**推断**: 若事故涉及结构缺陷/设备问题未被发现→M2
M3安全培训不足: 线索: 未经培训/交底走过场。**推断**: 工人明显违章但文本未提培训→M3
M4制度规程漏洞: 线索: 无安全方案/应急预案缺失/制度不健全
M5市场行为违规: 线索: 违法分包/无资质/转包/抢工期

输出JSON: {"M":["M1","M2"],"reasoning":"M1[explicit]因为... M2[inferred]因为..."}""",
    },
    {
        "layer": "F", "prefix": "F",
        "system": """你是安全防护专家。已知管理: {prev}，从事故文本中识别防线失效(F层)致因。
注意: 事故=防线被突破，根据事故类型反推缺失的防线。

F1临边洞口防护缺失: 线索: 无防护/无护栏。**推断**: 高处坠落事故→F1
F2支撑体系失稳: 线索: 模板坍塌/脚手架失稳/支撑不足。**推断**: 坍塌且涉及混凝土/模板→F2
F3设备安全装置失效: 线索: 限位器被屏蔽/防坠器失效/安全装置损坏
F4个体防护未使用: 线索: 未系安全带/未戴安全帽。**推断**: 高处坠落或物体打击伤亡→F4

输出JSON: {"F":["F2"],"reasoning":"F2因为..."}""",
    },
    {
        "layer": "T", "prefix": "T",
        "system": """你是事故分析专家。已知管理: {prev_m}，防护: {prev_f}，从文本中识别触发条件(T层)致因。

T1设备机具故障: 线索: 设备故障/钢丝绳断裂/机械失灵
T2物料状态异常: 线索: 堆载超限/荷载过大/瓦斯积聚。**推断**: 坍塌事故未见明确触发物→考虑T2
T3地质环境突变: 线索: 涌水/边坡失稳/地基沉降。**推断**: 基坑/沟槽/隧道事故→T3
T4气象水文触发: 线索: 暴雨/大风/冰雪/连日降雨

输出JSON: {"T":["T2"],"reasoning":"T2因为..."}""",
    },
    {
        "layer": "H", "prefix": "H",
        "system": """你是人员行为分析专家。已知管理: {prev_m}，防护: {prev_f}，触发: {prev_t}，从文本中识别人的行为(H层)致因。
注意: 几乎每条施工事故都涉及人的行为因素。

H1违反操作规程: 线索: 明确违反方案/规程/规范。**推断**: 工人行为违背基本安全常识→H1
H2违规指挥决策: 线索: 强令冒险/违章指挥。**推断**: 多人同时作业的事故→可能存在H2
H3风险辨识失败: 线索: 未意识到危险/安全意识薄弱/疏忽大意。**推断**: 工人进入本应避开的危险区域→H3
H4应急行为失当: 线索: 盲目施救/未及时撤离/救援不当扩大伤亡

输出JSON: {"H":["H1","H2"],"reasoning":"H1因为...H2因为..."}""",
    },
    {
        "layer": "C", "prefix": "C",
        "system": """你是应急救援分析专家。已知管理: {prev_m}，防护: {prev_f}，触发: {prev_t}，行为: {prev_h}，从文本中识别后果放大(C层)致因。

C1救援响应迟缓: 线索: 救援不及时/无预案/二次坍塌。**推断**: 死亡≥3人且文本未提救援→C1
C2信息迟报瞒报: 线索: 未及时上报/瞒报/迟报/漏报

输出JSON: {"C":["C1"],"reasoning":"C1因为..."}""",
    },
]


# ── Helper: extract JSON from mixed text ──
def extract_json(text: str) -> dict:
    """从混合输出(推理文字+JSON)中提取JSON。先找最后一行，再找最后{...}"""
    # 尝试直接解析
    try: return json.loads(text)
    except: pass
    # 找 ```json 代码块
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    # 从最后一行开始找 — 多数LLM把JSON放在最后
    lines = text.strip().split('\n')
    for line in reversed(lines):
        if line.strip().startswith('{'):
            try: return json.loads(line.strip())
            except: pass
    # Smart brace: 从最后一个 { 开始匹配
    last_brace = text.rfind('{')
    if last_brace >= 0:
        depth = 0; end = -1
        for i in range(last_brace, len(text)):
            if text[i] == '{': depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0: end = i+1; break
        if end > 0:
            try: return json.loads(text[last_brace:end])
            except: pass
    # 贪婪 fallback
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        s = m.group(0)
        if s.count('{') > s.count('}'): s += '}' * (s.count('{') - s.count('}'))
        try: return json.loads(s)
        except: pass
    return {"codes": [], "_error": "no_json", "_raw": text[:200]}

# ── Helper: call LLM ──
def call_llm(system: str, user: str) -> dict:
    """Call DeepSeek and parse JSON response."""
    try:
        resp = client.messages.create(model=MODEL, max_tokens=2048, temperature=0.0,
                                       system=system, messages=[{"role": "user", "content": user}])
        raw = ""
        for blk in resp.content:
            if hasattr(blk, "text"): raw += blk.text
        result = extract_json(raw)
        if isinstance(result, list):
            result = {"codes": result}
        if "_error" in result:
            result["_raw"] = raw[:200]
        return result
    except Exception as e:
        return {"codes": [], "_error": str(e)[:80]}


# ── S5专用: 5次串行调用 ──
def call_llm_s5(text: str) -> dict:
    """CAICS-CoT+: 逐层调用，前层reasoning注入后层"""
    context = ""
    all_codes = []
    for layer_cfg in S5_LAYERS:
        system = layer_cfg["system"]
        if context:
            system = f"{system}\n\n前层分析结果:\n{context}"
        user = f"事故文本:\n{text[:1500]}"
        try:
            resp = client.messages.create(model=MODEL, max_tokens=512, temperature=0.0,
                                           system=system, messages=[{"role": "user", "content": user}])
            raw = ""
            for blk in resp.content:
                if hasattr(blk, "text"): raw += blk.text
            layer_result = extract_json(raw)
            layer_key = layer_cfg["layer"]
            layer_codes = layer_result.get(layer_key, [])
            reasoning = layer_result.get("reasoning", "")
            if layer_codes:
                all_codes.extend(layer_codes)
                context += f"\n{layer_key}层: {', '.join(layer_codes)} — {reasoning}"
            else:
                context += f"\n{layer_key}层: 无编码"
        except Exception as e:
            context += f"\n{layer_cfg['layer']}层调用失败: {e}"
    return {"codes": all_codes, "_s5_context": context}


# ── Helper: compute per-class metrics ──
def compute_metrics(predictions, ground_truth):
    """Compute P/R/F1/Acc per CAICS class + Ave/Ave+"""
    all_classes = sorted(set(
        c for gt in ground_truth for c in (gt.get("codes",[]) or [])
        if isinstance(gt, dict) and isinstance(c, str)
    ) | set(
        c for pred in predictions for c in (pred.get("codes",[]) or [])
        if isinstance(pred, dict) and isinstance(c, str)
    ))

    results = {}
    for cls in all_classes:
        tp = sum(1 for i, gt in enumerate(ground_truth)
                 if isinstance(gt, dict) and i < len(predictions)
                 and cls in gt.get("codes",[]) and cls in predictions[i].get("codes",[]))
        fp = sum(1 for pred in predictions
                 if isinstance(pred, dict) and cls in pred.get("codes",[])
                 and (predictions.index(pred) >= len(ground_truth)
                      or not isinstance(ground_truth[predictions.index(pred)], dict)
                      or cls not in ground_truth[predictions.index(pred)].get("codes",[])))
        fn = sum(1 for gt in ground_truth
                 if isinstance(gt, dict) and cls in gt.get("codes",[])
                 and (ground_truth.index(gt) >= len(predictions)
                      or cls not in predictions[ground_truth.index(gt)].get("codes",[])))

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        acc = tp / len(ground_truth) if len(ground_truth) > 0 else 0
        results[cls] = {"P": prec, "R": rec, "F1": f1, "Acc": acc, "count": sum(1 for gt in ground_truth if isinstance(gt, dict) and cls in gt.get("codes",[]))}

    vals = [v for v in results.values()]
    ave = {metric: sum(v[metric] for v in vals)/len(vals) if vals else 0 for metric in ["P","R","F1","Acc"]}
    # Ave+: exclude classes with <5 GT positives
    vals_plus = [v for v in vals if v["count"] >= 5]
    ave_plus = {metric: sum(v[metric] for v in vals_plus)/len(vals_plus) if vals_plus else 0 for metric in ["P","R","F1","Acc"]}

    # Accident type/severity accuracy
    type_match = sum(1 for i, gt in enumerate(ground_truth)
                     if isinstance(gt, dict) and i < len(predictions) and isinstance(predictions[i], dict)
                     and predictions[i].get("accident_type","") == gt.get("accident_type",""))
    sev_match = sum(1 for i, gt in enumerate(ground_truth)
                    if isinstance(gt, dict) and i < len(predictions) and isinstance(predictions[i], dict)
                    and predictions[i].get("severity","") == gt.get("severity",""))

    return {
        "per_class": results, "ave": ave, "ave_plus": ave_plus,
        "type_acc": type_match/len(ground_truth) if ground_truth else 0,
        "sev_acc": sev_match/len(ground_truth) if ground_truth else 0,
    }


# ── Run Experiment ──
if __name__ == "__main__":
    # Load GT
    gt_data = json.loads(GT_PATH.read_text(encoding="utf-8"))
    samples = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))

    # Build GT labels from expertA (as reference — or use majority vote from all 3)
    gt_labels = []
    for i, record in enumerate(gt_data):
        codes = set()
        atype = sev = ""
        for ann in record.get("annotations", []):
            for res in ann.get("result", []):
                name = res["from_name"]
                val = res.get("value", {})
                if name in ('M','F','T','H','C'):
                    for v in val.get('choices', []):
                        codes.add(v.split('-')[0])
                elif name == 'accident_type':
                    atype = val.get('choices', [''])[0]
                elif name == 'severity':
                    sev = val.get('choices', [''])[0]
        gt_labels.append({"codes": sorted(codes), "accident_type": atype, "severity": sev})

    print(f"GT: {len(gt_labels)} 条, 平均{sum(len(g['codes']) for g in gt_labels)/len(gt_labels):.1f}编码/条\n")

    # Run each strategy
    all_results = {}
    for sname, sconfig in STRATEGIES.items():
        print(f"\n{'='*50}")
        print(f"策略: {sname}")
        print(f"{'='*50}")

        predictions = []
        ok = 0
        t0 = time.time()

        for i, sample in enumerate(samples):
            # Extract clean text (first 1000 chars)
            text = sample['data']['text']
            # Remove markdown headers for cleanliness
            clean = re.sub(r'^#{1,4}\s+.*$', '', text, flags=re.MULTILINE).strip()[:1500]

            result = call_llm(sconfig["system"], sconfig["user_prefix"] + clean)
            predictions.append(result)

            if result.get("codes"):
                ok += 1
            if (i+1) % 20 == 0:
                elapsed = time.time() - t0
                print(f"  [{i+1:3d}/100] {ok}有编码 ({elapsed:.0f}s)")

        elapsed = time.time() - t0
        metrics = compute_metrics(predictions, gt_labels)
        all_results[sname] = metrics

        print(f"  ✅ {ok}/100 有编码 ({elapsed:.0f}s)")
        print(f"  Ave+(F1): {metrics['ave_plus']['F1']:.3f}  Ave+(P): {metrics['ave_plus']['P']:.3f}  Ave+(R): {metrics['ave_plus']['R']:.3f}")
        print(f"  TypeAcc: {metrics['type_acc']:.3f}  SevAcc: {metrics['sev_acc']:.3f}")

        # Save per-strategy result
        (OUT_DIR / f"{sname}.json").write_text(json.dumps({
            "strategy": sname, "metrics": metrics,
            "predictions": predictions, "elapsed": elapsed,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── S5: CAICS-CoT+ (逐层串行, cost=5×) ──
    sname = "S5_CAICSCoTPlus"
    print(f"\n{'='*50}")
    print(f"策略: {sname} (5次API调用/条)")
    print(f"{'='*50}")
    predictions5 = []
    ok5 = 0
    t0 = time.time()
    for i, sample in enumerate(samples):
        text = re.sub(r'^#{1,4}\s+.*$', '', sample['data']['text'], flags=re.MULTILINE).strip()[:1500]
        result = call_llm_s5(text)
        predictions5.append(result)
        if result.get("codes"): ok5 += 1
        if (i+1) % 10 == 0:
            print(f"  [{i+1:3d}/100] {ok5}有编码 ({time.time()-t0:.0f}s)")
    elapsed = time.time() - t0
    metrics5 = compute_metrics(predictions5, gt_labels)
    all_results[sname] = metrics5
    print(f"  ✅ {ok5}/100 有编码 ({elapsed:.0f}s, {elapsed/100:.1f}s/条)")
    print(f"  Ave+(F1): {metrics5['ave_plus']['F1']:.3f}")
    (OUT_DIR / f"{sname}.json").write_text(json.dumps({
        "strategy": sname, "metrics": metrics5, "predictions": predictions5, "elapsed": elapsed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Summary Table ──
    print(f"\n\n{'='*70}")
    print(f"📊 对标 Liu et al. Table 3 — 6种策略对比")
    print(f"{'='*70}")
    print(f"{'策略':<20} {'Ave+F1':>8} {'Ave+R':>8} {'Ave+P':>8} {'Ave+Acc':>8} {'TypeAcc':>8} {'SevAcc':>8}")
    print(f"{'-'*70}")
    for sname, m in all_results.items():
        a = m['ave_plus']
        print(f"{sname:<20} {a['F1']:>8.3f} {a['R']:>8.3f} {a['P']:>8.3f} {a['Acc']:>8.3f} {m['type_acc']:>8.3f} {m['sev_acc']:>8.3f}")

    # Save summary
    summary = {
        "experiment": "6-strategy CAICS prompt comparison",
        "sample_size": 100,
        "model": MODEL,
        "results": {sname: {"ave_plus_F1": m['ave_plus']['F1'], "ave_plus_P": m['ave_plus']['P'],
                             "ave_plus_R": m['ave_plus']['R'], "type_acc": m['type_acc'],
                             "sev_acc": m['sev_acc']} for sname, m in all_results.items()}
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 报告: {OUT_DIR}/summary.json")
