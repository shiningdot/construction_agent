"""S5′ = CAICS-CoT+：逐步独立调用 + 程序化逻辑语句 + 建筑施工安全领域知识。

对齐对标论文 Liu et al. (ESWA 2025) 的 HFACS-CoT+ 三件事（见其附录 D）：

1. **逐步独立调用**：HFACS-CoT+ 把每个问题连同它自己的定义、以及见证叙述，
   *逐个、顺序*喂入模型（原文 "steps (questions) are fed into the LLM individually
   and sequentially"），而不是像 HFACS-CoT 那样一次性全灌进去。我们照此把 5 个因果层
   各做一次独立调用，前层结论注入后层。
2. **程序化逻辑语句**：论文用 Python 的 if/elif/else 取代提示词里的文字指令
   （"replaces textual instructions with structured programming logic statements"），
   以此消除"指令说互斥、模型却同时判是"的逻辑不一致。我们的对应实现见
   `apply_programmatic_rules()`——层级白名单、依赖必检、H1/H3 互斥全部由代码裁定，
   不写在提示词里。
3. **领域知识注入**：每层的判定要点里写入建筑施工安全的具体工艺与规范知识
   （模板支撑体系构造、扣件式钢管脚手架、有限元空间作业三要素、临边洞口防护、
   起重机械安全装置、临时用电三级配电两级保护等）。

与论文的差异（有意为之）：论文用**疑问式**的问题串（"Did X ...? If yes, proceed to ..."）。
我们自己的实验二已证明疑问式条件检查会触发「词汇门控效应」（M1 召回掉到 0.141、
21% 样本空输出），故此处仍用**陈述式**的判定要点，只借用论文的结构与程序化逻辑。

输出口径与其他五个策略一致（`codes` + 31 基础特征），由第 6 次汇总调用产出；
其中 `codes` 一律由本模块的程序化规则裁定后覆写，保证可复现。
"""
from __future__ import annotations

import json
import re
import time

# ---------------------------------------------------------------- 层定义

DOMAIN = {
    "T": """判定要点（建筑施工安全领域知识）：
- 模板支撑体系：浇筑混凝土时的竖向荷载与侧压力须由立杆、水平杆、剪刀撑共同承担。立杆间距与步距超标、缺扫地杆或剪刀撑、支撑落在未夯实的基土上，是浇筑过程中失稳坍塌的常见直接原因；浇筑速度过快或局部堆载过高会造成瞬时超载。
- 脚手架：扣件式钢管脚手架依靠立杆、纵横向水平杆、连墙件与剪刀撑形成稳定体系。扣件螺栓拧紧力矩不足、连墙件缺失或被提前拆除、悬挑梁锚固不到位，都会导致整体或局部失稳。
- 基坑与边坡：放坡系数不足、支护结构强度不够、地下水未有效控制、坡顶超载，会引发土体滑移、涌水或突泥。
- 起重机械：塔式起重机的力矩限制器、起重量限制器、高度与幅度限位器；施工升降机与物料提升机的防坠安全器；钢丝绳断丝、磨损、挤压变形达到报废标准仍继续使用。
- 有限空间：须"先通风、再检测、后作业"，氧含量保持在 19.5%–23.5%，并检测可燃气体、硫化氢、一氧化碳。
- 临时用电：TN-S 系统、三级配电两级保护，漏电保护器额定动作电流不大于 30mA。""",
    "F": """判定要点（建筑施工安全领域知识）：
- 临边洞口：楼层临边、阳台、屋面周边须设防护栏杆（高度不低于 1.2m）与挡脚板；边长 500mm 以上的洞口须设盖板或防护栏杆；电梯井口须设防护门，井内每隔两层且不超过 10m 设一道安全平网。
- 高处作业：坠落高度基准面 2m 及以上即为高处作业；安全带须高挂低用，不得低挂高用。
- 支撑体系：混凝土浇筑前必须对模板支撑体系进行验收，合格后方可浇筑；拆模须以同条件养护试块强度报告为依据。
- 特种设备：安全限位器、防坠安全器、力矩限制器属强制检验项，防坠安全器须每年标定一次。
- 个体防护：进入施工现场须戴安全帽；高处作业须系安全带；电焊作业须戴防护面罩与绝缘手套。""",
    "H": """判定要点（建筑施工安全领域知识）：
- "三违"指违章指挥、违章操作、违反劳动纪律。事故调查中"三违"是最常见的人因表述。
- 特种作业人员须持证上岗：电工、焊工、架子工、起重信号司索工、塔式起重机司机等。
- 事故后盲目施救是有限空间与坍塌事故中伤亡扩大的典型原因（未佩戴防护装备进入救援、二次坍塌时仍在施救）。
- 人的行为要区分两个不同侧面：**有没有违反规程**（故意违章）与**有没有低估风险**（过失）。二者**不是互斥选项**，在施工事故中常同时成立（作业者既违反了方案、又对风险估计不足），也可能只成立其一，需各自独立判断。""",
    "M": """判定要点（建筑施工安全领域知识）：
- 安全生产责任制：施工单位须建立项目安全生产责任制，配备专职安全生产管理人员；监理单位须履行安全监理职责，旁站与巡视关键工序。
- 危险性较大的分部分项工程：须编制专项施工方案；超过一定规模的（如搭设高度 8m 及以上的模板支撑体系、开挖深度 5m 及以上的基坑）须组织专家论证。
- 三级安全教育：公司、项目、班组三级，新入场工人须经教育培训合格后方可上岗；班前须进行安全技术交底并履行签字手续。
- 隐患排查治理：须建立隐患排查治理制度，对查出的隐患定人、定时间、定措施整改闭环。
- 市场行为：违法分包、转包、挂靠、无资质或超资质承揽、违规压缩工期，会系统性地降低安全投入。""",
    "C": """判定要点（建筑施工安全领域知识）：
- 生产安全事故报告：事故发生后，现场人员须立即报告本单位负责人，单位负责人接到报告后应于 1 小时内如实向县级以上应急管理部门报告；较大及以上事故须逐级上报，不得迟报、漏报、谎报、瞒报。
- 应急救援：施工单位应制定生产安全事故应急救援预案并配备应急救援器材；有限空间与坍塌事故中，救援不当是伤亡扩大的主要原因。
- 未及时上报或瞒报，会使外部专业救援力量无法及时介入，进而放大后果。""",
}

LAYERS = [
    {"key": "T", "title": "触发条件 T 层",
     "intro": "你专门判定事故的【触发条件 T 层】：直接引发这起事故的物质或环境条件。",
     "codes": {
         "T1": "设备机具故障：施工机械、设备、机具在运行过程中发生不可预期的故障、断裂、失灵等机械失效，直接导致或加速了事故的发生",
         "T2": "物料状态异常：施工材料、构件、堆载物因自身性质变化（浸水、老化、变质）或外部因素（超载、偏载）导致状态不稳定，引发结构失效或物体打击",
         "T3": "地质环境突变：施工区域的地质条件或地下水状态发生突变，如涌水、流沙、边坡滑动、地基突然沉降",
         "T4": "气象水文触发：暴雨、大风、冰雪、雷电、高温等极端天气条件直接触发事故，或显著降低了结构/防护的安全性",
     },
     "extra": "先写出一句话的物理机制（是什么引发了什么），再据此给出编码。"},

    {"key": "F", "title": "防线失效 F 层",
     "intro": "你专门判定事故的【防线失效 F 层】：哪一道物理屏障或防护被突破了。事故之所以发生，意味着至少一道防线失效。",
     "codes": {
         "F1": "临边洞口防护缺失：临边（楼层边缘、阳台、屋面周边）、洞口（电梯井、预留孔洞）未按规范设置防护栏杆、安全网、盖板等防坠落设施",
         "F2": "支撑体系失稳：模板支撑系统、脚手架、基坑支护等临时结构在设计、搭设、使用或拆除过程中，因承载力不足、构造缺陷或违规操作导致失稳或坍塌",
         "F3": "设备安全装置失效：塔吊、施工升降机、物料提升机等特种设备的安全限位器、防坠器、力矩限制器、保险装置被人为屏蔽、损坏或未安装",
         "F4": "个体防护未使用：作业人员在危险作业环境中未按规定佩戴或使用安全帽、安全带、防滑鞋、防护手套等个人防护装备",
     },
     "extra": "由前层判定的物理机制与事故类型反推被突破的防线。"},

    {"key": "H", "title": "人的行为 H 层",
     "intro": "你专门判定事故的【人的行为 H 层】：直接导致事故的人为行为。",
     "codes": {
         "H1": "违反操作规程：作业人员的行为明确违反了已制定的施工方案、安全操作规程、技术规范或法律法规要求（**明知故犯**）",
         "H2": "违规指挥决策：管理人员（项目经理、工长、班组长）在明知存在安全风险的情况下，仍发出指令要求工人继续或冒险作业",
         "H3": "风险辨识失败：作业人员或管理人员未能识别作业环境中存在的危险，或对危险程度评估不足（**过失**：没意识到、疏忽大意、判断失误）",
         "H4": "应急行为失当：事故发生后，现场人员在救援过程中的不当行为导致伤亡扩大，如盲目施救、未及时撤离、救援方法错误",
     },
     "extra": ("注意：H1（违反规程）与 H3（低估风险）**不是互斥选项**——它们刻画同一行为的不同侧面，"
               "可以同时成立，也可以只成立其一。请各自独立判断，不要用其中一个的结论去否定另一个；"
               "也不要因为一个成立就默认另一个也成立。")},

    {"key": "M", "title": "管理根基 M 层",
     "intro": "你专门判定事故的【管理根基 M 层】：使上述直接致因得以发生的系统性安全管理缺陷。",
     "codes": {
         "M1": "现场监管缺失：施工现场安全监督力量未有效覆盖关键作业面，监理或安全员未能在事故发生前发现并制止不安全状态",
         "M2": "隐患排查失效：组织建立了隐患排查制度要求，但执行层面未能识别已存在的危险源，或识别后未在规定时间内整改关闭",
         "M3": "安全培训不足：作业人员或管理人员未接受与岗位风险相匹配的安全培训，或培训内容与实际作业脱节",
         "M4": "制度规程漏洞：安全管理制度、施工方案、操作规程、应急预案缺失、不完备，或未能覆盖实际施工场景中的风险点",
         "M5": "市场行为违规：参建单位通过违法分包、转包、挂靠、无资质或超资质承揽、违规压缩工期等市场行为，系统性地降低安全管理投入",
     },
     "extra": ("发生死亡事故本身即说明现场监管存在缺失（M1）；事故所涉的结构缺陷或设备问题此前未被发现，"
               "说明隐患排查失效（M2）；工人明显违章而文本未提培训，说明培训不足（M3）。这些推断允许脱离字面证据。")},

    {"key": "C", "title": "后果放大 C 层",
     "intro": "你专门判定事故的【后果放大 C 层】：使事故后果超出直接预期的因素。",
     "codes": {
         "C1": "救援响应迟缓：从事故发生到有效救援到达的时间间隔过长，或救援过程因缺乏预案、设备不足、组织混乱等原因效率低下",
         "C2": "信息迟报瞒报：事故发生后责任单位未按规定及时向上级主管部门报告，或故意隐瞒、延迟报告事故信息",
     },
     "extra": "仅当伤亡后果确实因救援或上报环节被放大时才输出本层编码。"},
]

LAYER_CODES = {L["key"]: list(L["codes"]) for L in LAYERS}
ALL_CODES = [c for L in LAYERS for c in L["codes"]]


def build_layer_system(layer: dict, prev_context: str) -> str:
    code_lines = "\n".join(f"  {k} {v}" for k, v in layer["codes"].items())
    ctx = f"\n\n## 前序层级已判定的结论\n{prev_context}\n" if prev_context else ""
    return f"""你是建筑施工安全事故分析专家。{layer['intro']}

{DOMAIN[layer['key']]}

## 本层编码定义

{code_lines}

{layer['extra']}{ctx}
## 输出

只输出一个 JSON 对象：
{{"codes": ["{LAYER_CODES[layer['key']][0]}"], "mechanism": "一句话机制或结论", "reasoning": "判定依据"}}

`codes` 只能取自本层的 {"/".join(LAYER_CODES[layer['key']])}；本层若无编码则输出空数组 []。
不要输出其它文字。"""


# ---------------------------------------------------------------- 程序化逻辑

# 依赖约束：由 T 层判定的物理机制反推「必检」的防线。
# 这是论文"用编程逻辑语句替代文字指令"的对应实现——规则写在代码里，不由模型自觉遵守。
MECH_FORCE_RULES = [
    (("坍塌", "模板", "混凝土", "支撑", "脚手架", "支架", "浇"), ["F2"],
     "判定涉及模板/混凝土/支撑/脚手架 → F2 支撑体系失稳必检"),
    (("坠落", "高处"), ["F1", "F4"],
     "判定为高处坠落 → F1 临边洞口防护、F4 个体防护必检"),
    (("塔吊", "塔式起重机", "升降机", "物料提升机", "起重机", "起重", "吊装"), ["F3"],
     "涉及起重机械/特种设备 → F3 设备安全装置失效必检"),
    (("基坑", "沟槽", "隧道", "边坡"), ["F2"],
     "判定为基坑/沟槽/隧道/边坡类 → F2 支撑体系失稳必检"),
]

# 互斥约束：**当前为空**。
#
# 对标论文用「error 与 known deviation 互斥」作为代码级控制流的一个范例
# （其附录 D：`if q2_bool == "0" and q3_bool == "0": ... else: print("Q4 is not needed.")`）。
# 我们一度照搬为「H1 与 H3 互斥」，但**在 CAICS 里这是错的**：人工 GT 中 63.7% 的事故
# 同时含有 H1 与 H3（H1+H3 是 GT 第 8 高频共现对）。照搬后 S4′ 的 H3 召回从 0.57 掉到 0.19、
# S5′ 更是掉到 0.05。CAICS 的 19 类里目前没有找到语义上真正互斥的编码对，
# 故这条规则置空——S5′ 的程序化逻辑由下面的**依赖约束**（MECH_FORCE_RULES）承担，
# 那部分是站得住的（F 层召回实测因此提升）。
MUTEX: list[tuple[str, str, str, str]] = []


def apply_programmatic_rules(layer_codes: dict[str, list[str]],
                             t_mechanism: str) -> tuple[list[str], list[str]]:
    """把 5 层各自的编码按代码级规则合成最终编码集合。

    返回 (最终编码列表, 触发的规则说明列表)。规则全程可复现，不依赖模型自觉。
    """
    fired: list[str] = []

    # 1) 层内白名单：本层只接受本层编码，越界的一律丢弃
    clean: dict[str, list[str]] = {}
    for key, codes in layer_codes.items():
        allowed = set(LAYER_CODES[key])
        kept = [c for c in codes if c in allowed]
        dropped = [c for c in codes if c not in allowed]
        if dropped:
            fired.append(f"{key} 层越界编码被丢弃：{dropped}")
        clean[key] = list(dict.fromkeys(kept))

    # 2) 依赖约束：由 T 层机制文本决定强制补入的防线编码
    hay = t_mechanism or ""
    extra_f: list[str] = []
    for kws, add, why in MECH_FORCE_RULES:
        if any(k in hay for k in kws):
            extra_f += add
            fired.append(why)
    for c in dict.fromkeys(extra_f):
        if c not in clean["F"]:
            clean["F"].append(c)
            fired.append(f"强制补入 {c}")

    # 3) 互斥约束：同时出现时只保留优先方
    for keep, drop, _, why in MUTEX:
        owner_keep = next((k for k, cs in clean.items() if keep in cs), None)
        owner_drop = next((k for k, cs in clean.items() if drop in cs), None)
        if owner_keep and owner_drop and keep in clean[owner_keep] and drop in clean[owner_drop]:
            clean[owner_drop].remove(drop)
            fired.append(f"{keep} 与 {drop} 同时命中 → 依互斥约束保留 {keep}（{why}）")

    # 4) 按 T→F→H→M→C 固定顺序输出，保证可比
    out = [c for L in LAYERS for c in clean[L["key"]]]
    return out, fired


# ---------------------------------------------------------------- 调用

def _call(client, model, system, user, max_tokens, use_stream, attempts=3) -> tuple[str, str, int, int]:
    """单次调用，返回 (raw_text, error, prompt_tokens, completion_tokens)。"""
    last = ""
    for i in range(attempts):
        try:
            if use_stream:
                with client.messages.stream(model=model, max_tokens=max_tokens,
                                            temperature=0.0, system=system,
                                            messages=[{"role": "user", "content": user}]) as st:
                    resp = st.get_final_message()
            else:
                resp = client.messages.create(model=model, max_tokens=max_tokens,
                                              temperature=0.0, system=system,
                                              messages=[{"role": "user", "content": user}])
            u = getattr(resp, "usage", None)
            return ("".join(getattr(b, "text", "") for b in resp.content), "",
                    getattr(u, "input_tokens", 0) if u else 0,
                    getattr(u, "output_tokens", 0) if u else 0)
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 ** i)
    return "", last, 0, 0


def _call_msgs(client, model, system, messages, max_tokens, use_stream, attempts=3):
    """按 messages 发一次请求（补全重试需要多轮消息）。返回同 _call。"""
    last = ""
    for i in range(attempts):
        try:
            if use_stream:
                with client.messages.stream(model=model, max_tokens=max_tokens,
                                            temperature=0.0, system=system,
                                            messages=messages) as st:
                    resp = st.get_final_message()
            else:
                resp = client.messages.create(model=model, max_tokens=max_tokens,
                                              temperature=0.0, system=system, messages=messages)
            u = getattr(resp, "usage", None)
            return ("".join(getattr(b, "text", "") for b in resp.content), "",
                    getattr(u, "input_tokens", 0) if u else 0,
                    getattr(u, "output_tokens", 0) if u else 0)
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 ** i)
    return "", last, 0, 0


def _extract_obj(text: str) -> dict:
    from run_S1_golden import extract_json
    return extract_json(text)


def call_s5(client, model, system, rec, max_tokens, use_stream) -> dict:
    """S5′ 全流程：5 层独立调用 + 程序化规则 + 1 次汇总。签名与 run_strategy.call_one 对齐。"""
    t0 = time.time()
    text = rec["raw_text"]
    layers: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}
    tok_in = tok_out = 0
    failed: list[str] = []
    hit_cap = False

    for L in LAYERS:
        key = L["key"]
        prev_parts = []
        for k in ("T", "F", "H", "M"):
            if k == key:
                break
            if k in layers:
                prev_parts.append(f"{k} 层：{','.join(layers[k]) or '无'} —— {reasons.get(k, '')}")
        prev = "\n".join(prev_parts)
        sys_msg = build_layer_system(L, prev)
        user = f"事故文本：\n{text}"
        raw, err, ti, to = _call(client, model, sys_msg, user, max_tokens, use_stream)
        tok_in += ti
        tok_out += to
        hit_cap = hit_cap or to >= max_tokens
        if err:
            failed.append(f"{key}: {err}")
            layers[key] = []
            continue
        obj = _extract_obj(raw)
        layers[key] = [c for c in (obj.get("codes") or []) if isinstance(c, str)]
        reasons[key] = str(obj.get("mechanism") or obj.get("reasoning") or "")[:200]

    t_mech = reasons.get("T", "")
    final_codes, fired = apply_programmatic_rules(layers, t_mech)

    # 第 6 次调用：汇总，产出 31 个基础特征（codes 由上面的程序化规则裁定）
    import prompt_build
    detail = "\n".join(f"{L['key']} 层编码：{','.join(layers[L['key']]) or '无'} —— {reasons.get(L['key'], '')}"
                       for L in LAYERS)
    sum_sys = ("你是建筑施工安全事故报告撰写专家。已完成该事故的 CAICS 致因链分层分析，"
               "现在请结合事故文本填写基础特征字段。\n\n"
               + prompt_build.build_output_block())
    sum_user = (f"事故文本：\n{text}\n\nCAICS 分层分析结果：\n{detail}\n\n"
                f"已裁定的最终 codes（请原样放入 codes 字段）：{json.dumps(final_codes, ensure_ascii=False)}")
    raw6, err6, ti6, to6 = _call(client, model, sum_sys, sum_user, max_tokens, use_stream)
    tok_in += ti6
    tok_out += to6
    hit_cap = hit_cap or to6 >= max_tokens
    if err6:
        failed.append(f"summary: {err6}")
        parsed = {"_error": "summary_failed"}
    else:
        parsed = _extract_obj(raw6)
        if "_error" in parsed:
            parsed = {"_error": parsed["_error"]}

    from run_S1_golden import validate
    import completion_repair

    # 汇总调用同样可能漏写字段（同 completion_repair 模块注释所述），走同一条补全逻辑
    repaired = 0
    if "_error" not in parsed:
        extra = {"pt": 0, "ct": 0}

        def _repair(msgs):
            r2, e2, p2, c2 = _call_msgs(client, model, sum_sys, msgs, max_tokens, use_stream)
            extra["pt"] += p2
            extra["ct"] += c2
            return (r2, None) if not e2 else ("", None)

        parsed, repaired = completion_repair.try_repair(_repair, sum_user, raw6, parsed)
        tok_in += extra["pt"]
        tok_out += extra["ct"]

    if "_error" not in parsed:
        parsed["codes"] = final_codes          # 覆写：保证 codes 由程序化规则裁定
    schema_errors = validate(parsed) if "_error" not in parsed else []
    missing = completion_repair.missing_fields(parsed) if "_error" not in parsed else []

    return {
        "id": rec["id"], "title": rec["title"], "strategy": "S5",
        "tier": rec["tier"], "source_name": rec["source_name"],
        "text_len": len(text),
        "parsed": parsed,
        "parse_error": parsed.get("_error", ""),
        "schema_errors": schema_errors,
        "missing_fields": missing,
        "repaired_fields": repaired,
        "hit_cap": hit_cap,                    # 任一层或汇总调用撞顶
        "elapsed_sec": round(time.time() - t0, 1),
        "prompt_tokens": tok_in, "completion_tokens": tok_out,
        "reason_chars": sum(len(v) for v in reasons.values()),
        "n_explicit": 0, "n_inferred": 0,
        "layer_codes": {k: v for k, v in layers.items()},
        "rules_fired": fired,
        "layers_failed": failed,
        "attempts": 1,
    }
