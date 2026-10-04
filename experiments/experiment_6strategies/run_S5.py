"""S5 CAICSCoTPlus: 100条全量运行 — 5层串行 + 第6次汇总 + 逐层重试机制"""
import json, time, os, sys, re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(Path('/home/suntianhao/construction_agent/.env'), override=True)
from anthropic import Anthropic

PROJ = Path('/home/suntianhao/construction_agent')
sys.path.insert(0, str(PROJ))
from construction_agent.evaluation.experiment3.runner import extract_json

# ============ S5 5层定义 ============
S5_LAYERS = [
    {
        "layer": "M",
        "system": """你是安全管理专家。识别管理根基(M层)致因。发生死亡事故本身即说明现场监管缺失，即使文本未提也应输出。

M1现场监管缺失: 监理/安全员未及时发现制止不安全状态。死亡事故=监管缺失，即使文本未提也输出M1。
M2隐患排查失效: 未识别已存在危险源或未整改。涉及结构缺陷/设备问题未被发现→输出M2。
M3安全培训不足: 未培训/交底不到位。工人违章但未提培训→输出M3。
M4制度规程漏洞: 无安全方案/应急预案缺失/制度不健全。
M5市场行为违规: 违法分包/无资质/转包/抢工期。

输出JSON: {"M":["M1"],"reasoning":"简述依据"}""",
    },
    {
        "layer": "F",
        "system": """你是安全防护专家。识别防线失效(F层)致因。事故=防线被突破，按事故类型反推缺失防线。

F1临边洞口防护缺失: 无防护/无护栏。高处坠落事故→输出F1。
F2支撑体系失稳: 模板坍塌/脚手架失稳/支撑不足。坍塌涉及混凝土/模板→输出F2。
F3设备安全装置失效: 限位器被屏蔽/防坠器失效/安全装置损坏。
F4个体防护未使用: 未系安全带/未戴安全帽。高处坠落/物体打击→输出F4。

输出JSON: {"F":["F2"],"reasoning":"简述依据"}""",
    },
    {
        "layer": "T",
        "system": """你是事故分析专家。识别触发条件(T层)致因。

T1设备机具故障: 设备故障/钢丝绳断裂/机械失灵。
T2物料状态异常: 堆载超限/荷载过大/材料缺陷。坍塌未见明确触发物→输出T2。
T3地质环境突变: 涌水/边坡失稳/地基沉降。基坑/沟槽/隧道→输出T3。
T4气象水文触发: 暴雨/大风/冰雪/连日降雨。

输出JSON: {"T":["T2"],"reasoning":"简述依据"}""",
    },
    {
        "layer": "H",
        "system": """你是人员行为分析专家。识别人的行为(H层)致因。几乎每条施工事故都涉及人的行为。

H1违反操作规程: 违反方案/规程/规范。工人行为违背基本安全常识→输出H1。
H2违规指挥决策: 强令冒险/违章指挥。多人同时作业→输出H2。
H3风险辨识失败: 未意识到危险/安全意识薄弱。工人进入本应避开的危险区域→输出H3。
H4应急行为失当: 盲目施救/未及时撤离/救援不当扩大伤亡。

输出JSON: {"H":["H1"],"reasoning":"简述依据"}""",
    },
    {
        "layer": "C",
        "system": """你是应急救援分析专家。识别后果放大(C层)致因。

C1救援响应迟缓: 救援不及时/无预案/二次坍塌。死亡≥3人且未提救援→输出C1。
C2信息迟报瞒报: 未及时上报/瞒报/迟报/漏报。

输出JSON: {"C":["C1"],"reasoning":"简述依据"}""",
    },
]

# 第6次调用：综合汇总（修复：从5层分析中提取完整字段）
SUMMARY_SYSTEM = """你是施工安全事故报告撰写专家。已知该事故的完整CAICS致因链分析结果。

请从分析结果和事故文本中提取以下信息，输出JSON：
- codes: 汇总所有5层的编码列表（去掉[inferred]/[explicit]标记，纯编码如["M1","F2","H1"]）
- accident_type: 必须从以下12类严格选择: 坍塌|高处坠落|触电|机械伤害|起重伤害|物体打击|土方坍塌|车辆伤害|火灾爆炸|中毒窒息|淹溺|其他
- severity: 必须从以下4级严格选择: 一般事故|较大事故|重大事故|特别重大事故
- deaths: 整数（从文本中提取死亡人数）
- injuries: 整数（从文本中提取受伤人数）
- direct_cause: 一句话简述直接原因

输出示例: {"codes":["M1","M2","F2","T2","H1","H3"],"accident_type":"坍塌","severity":"重大事故","deaths":10,"injuries":4,"direct_cause":"模板支撑体系失稳导致浇筑时坍塌"}"""

SAMPLES_PATH = PROJ / 'experiments/experiment_kappa/groundtruth/gt_samples_100.json'
OUT_DIR = PROJ / 'experiments/experiment_6strategies/S5_result'
OUT_DIR.mkdir(parents=True, exist_ok=True)

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.getenv("ANTHROPIC_MODEL")
MAX_RETRIES = 2


def call_layer(system: str, user: str, layer_name: str) -> dict:
    """单层调用，含重试。返回 {"codes":[], "reasoning":""}"""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=1024, temperature=0.0,
                system=system, messages=[{"role": "user", "content": user}],
            )
            raw = "".join(blk.text for blk in resp.content if hasattr(blk, "text"))
            parsed = extract_json(raw)
            codes = parsed.get(layer_name, [])
            if not isinstance(codes, list):
                codes = []
            reasoning = parsed.get("reasoning", "")
            if codes or reasoning:
                return {"codes": codes, "reasoning": reasoning, "attempts": attempt + 1}
            else:
                last_error = "empty_response"
                if attempt < MAX_RETRIES:
                    time.sleep(1)
                continue
        except Exception as e:
            last_error = str(e)[:80]
            if attempt < MAX_RETRIES:
                time.sleep(2)
            continue
    return {"codes": [], "reasoning": f"[{layer_name}层失败: {last_error}]", "attempts": MAX_RETRIES + 1}


def clean_codes(codes: list) -> list:
    """清洗编码：去标记、标准化、去重"""
    seen = set()
    cleaned = []
    for c in codes:
        if not isinstance(c, str):
            continue
        clean = re.sub(r'\s*\[.*?\]', '', c).strip().upper()
        if re.match(r'^[MFTHC]\d$', clean) and clean not in seen:
            seen.add(clean)
            cleaned.append(clean)
    return cleaned


def call_llm_s5(text: str) -> dict:
    """S5主流程：5层串行 + 第6次汇总 + 逐层重试"""
    all_codes = []
    layer_results = {}  # 存储每层的reasoning用于后续层注入
    total_attempts = 0

    # Phase 1: 5层串行
    for layer_cfg in S5_LAYERS:
        system = layer_cfg["system"]
        user = f"事故文本:\n{text[:1500]}"

        # 注入前层分析结果
        if layer_results:
            context_parts = []
            for lname, lresult in layer_results.items():
                if lresult.get("reasoning"):
                    context_parts.append(f"{lname}层: {lresult['reasoning']}")
            if context_parts:
                system = f"{system}\n\n前层分析结果:\n" + "\n".join(context_parts)

        result = call_layer(system, user, layer_cfg["layer"])
        total_attempts += result.get("attempts", 1)

        codes = result.get("codes", [])
        cleaned = clean_codes(codes)
        all_codes.extend(cleaned)
        layer_results[layer_cfg["layer"]] = {
            "codes": cleaned,
            "reasoning": result.get("reasoning", ""),
        }

    # Phase 2: 第6次汇总调用（修复：提取 accident_type/severity/deaths/injuries）
    # 构建立完整的5层分析上下文
    context_full = "\n".join([
        f"{ln}层编码: {', '.join(lr['codes']) if lr['codes'] else '无'} — {lr.get('reasoning','')}"
        for ln, lr in layer_results.items()
    ])
    summary_user = f"事故文本:\n{text[:1500]}\n\nCAICS分析结果:\n{context_full}"

    summary_result = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=2048, temperature=0.0,
                system=SUMMARY_SYSTEM, messages=[{"role": "user", "content": summary_user}],
            )
            raw = "".join(blk.text for blk in resp.content if hasattr(blk, "text"))
            summary_result = extract_json(raw)
            if summary_result and not summary_result.get("_error"):
                total_attempts += attempt + 1
                break
            if attempt < MAX_RETRIES:
                time.sleep(1)
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(2)
    else:
        total_attempts += MAX_RETRIES + 1

    if summary_result is None or summary_result.get("_error"):
        summary_result = {"accident_type": "", "severity": "", "deaths": 0, "injuries": 0, "direct_cause": ""}

    # 合并：汇总中的codes优先（更完整），fallback到5层累计
    summary_codes = clean_codes(summary_result.get("codes", []))
    final_codes = summary_codes if summary_codes else list(dict.fromkeys(all_codes))

    return {
        "codes": final_codes,
        "accident_type": summary_result.get("accident_type", ""),
        "severity": summary_result.get("severity", ""),
        "deaths": summary_result.get("deaths", 0),
        "injuries": summary_result.get("injuries", 0),
        "direct_cause": summary_result.get("direct_cause", ""),
        "layer_results": layer_results,
        "total_attempts": total_attempts,
    }


# ============ 主流程 ============
samples = json.loads(SAMPLES_PATH.read_text(encoding='utf-8'))
N = len(samples)
results = []
t_start = datetime.now()

print(f"S5 CAICSCoTPlus 开始运行 | 模型: {MODEL} | 样本: {N}条 | 逐层重试: {MAX_RETRIES}次", flush=True)
print(f"每条约6次API调用 (M/F/T/H/C + 汇总) | 预计耗时: ~{N*50}s", flush=True)
print(f"{'='*70}", flush=True)

for i, task in enumerate(samples):
    text = task['data']['text']
    aid = task['data']['accident_id'][:8]

    title = ''
    if '## 事故标题' in text:
        title = text.split('## 事故标题\n')[1].split('\n')[0][:50]

    t0 = time.time()
    result = call_llm_s5(text)
    elapsed = time.time() - t0

    codes = result.get("codes", [])
    d_val = result.get("deaths") or 0
    a_type = result.get("accident_type") or "?"
    severity = result.get("severity") or "?"
    attempts = result.get("total_attempts", 6)

    elapsed_total = (datetime.now() - t_start).total_seconds()
    rate = (i + 1) / max(elapsed_total, 1) * 60
    eta = max(0, (N - i - 1) / max(rate, 0.01))

    # 显示每层编码数
    layer_info = "|".join(
        f"{ln}:{len(lr.get('codes',[]))}" for ln, lr in result.get("layer_results", {}).items()
    )

    # 只显示基本行，不要layer_info太长
    print(f"[{i+1:03d}/100] {aid} ✅ | codes={len(codes):2d} "
          f"type={a_type:8s} sev={severity:8s} d={d_val:2d} | "
          f"{elapsed:.1f}s ({attempts}次) | ETA {eta:.0f}s", flush=True)

    results.append({
        "index": i + 1,
        "accident_id": aid,
        "title": title,
        "codes": codes,
        "accident_type": a_type,
        "severity": severity,
        "deaths": d_val,
        "injuries": result.get("injuries") or 0,
        "direct_cause": result.get("direct_cause") or "",
        "layer_results": {
            ln: {"codes": lr.get("codes", []), "reasoning": lr.get("reasoning", "")[:200]}
            for ln, lr in result.get("layer_results", {}).items()
        },
        "elapsed_sec": round(elapsed, 1),
        "total_attempts": attempts,
    })

# ============ 保存结果 ============
total_t = (datetime.now() - t_start).total_seconds()
code_total = sum(len(r.get("codes", [])) for r in results)
zero_code = sum(1 for r in results if len(r.get("codes", [])) == 0)
type_ok = sum(1 for r in results if r.get("accident_type", "?") != "?")

summary = {
    "strategy": "S5_CAICSCoTPlus",
    "model": MODEL,
    "timestamp": t_start.isoformat(),
    "total": N,
    "success": N,
    "total_codes": code_total,
    "avg_codes_per_case": round(code_total / N, 1),
    "zero_code_cases": zero_code,
    "type_extracted": type_ok,
    "elapsed_total_sec": round(total_t, 1),
    "avg_sec_per_case": round(total_t / N, 1),
    "total_api_calls": sum(r.get("total_attempts", 6) for r in results),
}

out_path = OUT_DIR / "S5_results.json"
out_path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding='utf-8')

print(f"\n{'='*70}")
print(f"✅ S5 CAICSCoTPlus 完成: {N}/{N} | {total_t:.0f}s | {total_t/N:.1f}s/条")
print(f"   总编码: {code_total} | 平均: {code_total/N:.1f}/条 | 0编码: {zero_code}条")
print(f"   总API调用: {summary['total_api_calls']}次")
print(f"📁 {out_path}")
