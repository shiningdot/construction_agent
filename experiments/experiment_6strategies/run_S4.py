"""S4 CAICSCoT: 100条全量运行 — 精简Prompt + explicit/inferred双模 + 重试 + 编码清洗"""
import json, time, os, sys, re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(Path('/home/suntianhao/construction_agent/.env'), override=True)
from anthropic import Anthropic

PROJ = Path('/home/suntianhao/construction_agent')
sys.path.insert(0, str(PROJ))
from construction_agent.evaluation.experiment3.runner import extract_json

# ============ S4 精简优化版 Prompt ============
# 保留 explicit/inferred 双模 + 推断规则，缩短~30%

S4_SYSTEM = """你是建筑施工安全事故分析专家。使用CAICS框架按M→F→T→H→C因果链逐层分析。

核心原则: 同时运用显性证据匹配和隐性领域推断。文本中有关键词→标记"explicit"，基于事故性质和行业规律合理推断→标记"inferred"。宁可多标不漏标。

M.管理根基（几乎所有严重事故都存在管理缺陷）：
   M1现场监管缺失: 线索→无人监管/监理失职/安全管理混乱。**推断→发生死亡事故本身即说明现场监管缺失，即使文本未提也应输出M1[inferred]**
   M2隐患排查失效: 线索→未排查/未整改/隐患长期存在。**推断→事故涉及结构缺陷/设备问题未被发现→M2[inferred]**
   M3安全培训不足: 线索→未经培训/交底走过场。**推断→工人明显违章但文本未提培训→M3[inferred]**
   M4制度规程漏洞: 线索→无安全方案/应急预案缺失/制度不健全
   M5市场行为违规: 线索→违法分包/无资质/转包/抢工期/未办理报建

F.防线失效（事故=防线被突破，按事故类型反推）：
   F1临边洞口防护缺失: 线索→无防护/无护栏。**推断→高处坠落事故→F1[inferred]**
   F2支撑体系失稳: 线索→模板坍塌/脚手架失稳/支撑不足。**推断→坍塌涉及混凝土/模板→F2[inferred]**
   F3设备安全装置失效: 线索→限位器被屏蔽/防坠器失效/安全装置损坏
   F4个体防护未使用: 线索→未系安全带/未戴安全帽。**推断→高处坠落/物体打击伤亡→F4[inferred]**

T.触发条件（直接触发事故的物质或环境条件）：
   T1设备机具故障: 线索→设备故障/钢丝绳断裂/机械失灵
   T2物料状态异常: 线索→堆载超限/荷载过大/珍珠岩浸水/瓦斯积聚。**推断→坍塌未见明确触发物→T2[inferred]**
   T3地质环境突变: 线索→涌水/边坡失稳/地基沉降。**推断→基坑/沟槽/隧道事故→T3[inferred]**
   T4气象水文触发: 线索→暴雨/大风/冰雪/连日降雨

H.人的行为（几乎每条事故都涉及人的行为因素）：
   H1违反操作规程: 线索→违反方案/规程/规范。**推断→工人行为违背基本安全常识→H1[inferred]**
   H2违规指挥决策: 线索→强令冒险/违章指挥。**推断→多人同时作业往往存在指挥问题→H2[inferred]**
   H3风险辨识失败: 线索→未意识到危险/安全意识薄弱。**推断→工人进入本应避开的危险区域→H3[inferred]**
   H4应急行为失当: 线索→盲目施救/未及时撤离/救援不当

C.后果放大（使后果超出预期的因素）：
   C1救援响应迟缓: 线索→救援不及时/无预案/二次坍塌。**推断→死亡≥3人且未提救援细节→C1[inferred]**
   C2信息迟报瞒报: 线索→未及时上报/瞒报/迟报/漏报

输出最终JSON。字段要求:
- codes: 所有explicit和inferred编码的合集（不带标记，纯编码如["M1","F2","H1"]）
- accident_type: 必须从以下12类严格选择: 坍塌|高处坠落|触电|机械伤害|起重伤害|物体打击|土方坍塌|车辆伤害|火灾爆炸|中毒窒息|淹溺|其他
- severity: 必须从以下4级严格选择: 一般事故|较大事故|重大事故|特别重大事故
- deaths: 整数
- injuries: 整数
- direct_cause: 一句话简述

若无相关编码codes可为空数组[]，但accident_type和severity必须填写。
分析完毕后，在最后一行输出最终JSON。"""

S4_USER_PREFIX = "请按CAICS因果链逐层分析以下事故：\n\n"

SAMPLES_PATH = PROJ / 'experiments/experiment_kappa/groundtruth/gt_samples_100.json'
OUT_DIR = PROJ / 'experiments/experiment_6strategies/S4_result'
OUT_DIR.mkdir(parents=True, exist_ok=True)

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.getenv("ANTHROPIC_MODEL")
MAX_RETRIES = 2


def clean_codes(codes: list) -> list:
    """清洗编码：去除 [inferred]/[explicit] 标记后缀，去重保持顺序"""
    seen = set()
    cleaned = []
    for c in codes:
        if not isinstance(c, str):
            continue
        # 去除 [inferred]、[explicit] 等标记
        clean = re.sub(r'\s*\[.*?\]', '', c).strip().upper()
        # 标准化：确保格式如 M1, F2, T3, H1, C1
        if re.match(r'^[MFTHC]\d$', clean) and clean not in seen:
            seen.add(clean)
            cleaned.append(clean)
    return cleaned


def merge_layered_json(parsed: dict) -> dict:
    """后处理：合并分层格式 {M:[],F:[],T:[],H:[],C:[]} 到 codes"""
    layered_keys = {'M', 'F', 'T', 'H', 'C'}
    found = [k for k in layered_keys if k in parsed and isinstance(parsed.get(k), list)]
    if not found:
        return parsed
    all_codes = []
    for k in found:
        for item in parsed[k]:
            if isinstance(item, str):
                all_codes.append(item)
    # 清洗并合并
    cleaned = clean_codes(all_codes)
    parsed['codes'] = cleaned
    parsed['_layered_merged'] = True
    return parsed


def call_llm_s4(text: str) -> dict:
    """调用 LLM，含重试 + 编码清洗 + 分层合并"""
    user_msg = S4_USER_PREFIX + text
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=2048, temperature=0.0,
                system=S4_SYSTEM, messages=[{"role": "user", "content": user_msg}],
            )
            raw = ""
            for blk in resp.content:
                if hasattr(blk, "text"):
                    raw += blk.text

            parsed = extract_json(raw)
            parsed = merge_layered_json(parsed)

            if parsed and not parsed.get("_error"):
                # 清洗编码中的 [inferred]/[explicit] 标记
                codes = parsed.get("codes", [])
                parsed["codes"] = clean_codes(codes)
                return {"parsed": parsed, "raw": raw, "attempts": attempt + 1}
            else:
                last_error = parsed.get("_error", "unknown parse error")
                if attempt < MAX_RETRIES:
                    time.sleep(1)
                continue

        except Exception as e:
            last_error = str(e)[:100]
            if attempt < MAX_RETRIES:
                time.sleep(2)
            continue

    return {"parsed": {"codes": [], "_error": last_error}, "raw": "", "attempts": MAX_RETRIES + 1}


# ============ 主流程 ============
samples = json.loads(SAMPLES_PATH.read_text(encoding='utf-8'))
N = len(samples)
results = []
t_start = datetime.now()

print(f"S4 CAICSCoT 开始运行 | 模型: {MODEL} | 样本: {N}条 | 最大重试: {MAX_RETRIES}次", flush=True)
print(f"{'='*70}", flush=True)

for i, task in enumerate(samples):
    text = task['data']['text']
    aid = task['data']['accident_id'][:8]

    title = ''
    if '## 事故标题' in text:
        title = text.split('## 事故标题\n')[1].split('\n')[0][:50]

    t0 = time.time()
    result = call_llm_s4(text)
    elapsed = time.time() - t0

    parsed = result["parsed"]
    attempts = result.get("attempts", 1)

    codes = parsed.get("codes") or []
    err = parsed.get("_error") or ""
    d_val = parsed.get('deaths') or 0
    a_type = parsed.get('accident_type') or '?'
    severity = parsed.get('severity') or '?'

    elapsed_total = (datetime.now() - t_start).total_seconds()
    rate = (i + 1) / max(elapsed_total, 1) * 60
    eta = max(0, (N - i - 1) / max(rate, 0.01))

    retry_tag = f" 🔄{attempts}" if attempts > 1 else ""
    status = "⚠" if err else "✅"
    print(f"[{i+1:03d}/100] {aid} {status} | codes={len(codes):2d} "
          f"type={a_type:8s} sev={severity:8s} d={d_val:2d} | "
          f"{elapsed:.1f}s{retry_tag} | ETA {eta:.0f}s", flush=True)

    results.append({
        "index": i + 1,
        "accident_id": aid,
        "title": title,
        "parsed": parsed,
        "raw_response": result.get("raw", "")[:3000],
        "codes": codes,
        "accident_type": a_type,
        "severity": severity,
        "deaths": d_val,
        "injuries": parsed.get("injuries") or 0,
        "direct_cause": parsed.get("direct_cause") or "",
        "elapsed_sec": round(elapsed, 1),
        "attempts": attempts,
        "parse_error": err,
    })

# ============ 保存结果 ============
total_t = (datetime.now() - t_start).total_seconds()
ok = sum(1 for r in results if not r.get("parse_error"))
retried = sum(1 for r in results if r.get("attempts", 1) > 1)
code_total = sum(len(r.get("codes", [])) for r in results)
zero_code = sum(1 for r in results if len(r.get("codes", [])) == 0)

summary = {
    "strategy": "S4_CAICSCoT",
    "model": MODEL,
    "timestamp": t_start.isoformat(),
    "total": N,
    "success": ok,
    "retried": retried,
    "total_codes": code_total,
    "avg_codes_per_case": round(code_total / N, 1),
    "zero_code_cases": zero_code,
    "elapsed_total_sec": round(total_t, 1),
    "avg_sec_per_case": round(total_t / N, 1),
}

out_path = OUT_DIR / "S4_results.json"
out_path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding='utf-8')

print(f"\n{'='*70}")
print(f"✅ S4 CAICSCoT 完成: {ok}/{N} | {total_t:.0f}s | {total_t/N:.1f}s/条")
print(f"   总编码: {code_total} | 平均: {code_total/N:.1f}/条 | 0编码: {zero_code}条")
print(f"   重试: {retried}条")
print(f"📁 {out_path}")
