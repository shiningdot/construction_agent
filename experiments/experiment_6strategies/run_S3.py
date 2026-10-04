"""S3 PlanAndSolve: 100条全量运行 — 先计划后求解 + 重试机制"""
import json, time, os, sys, re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(Path('/home/suntianhao/construction_agent/.env'), override=True)
from anthropic import Anthropic

PROJ = Path('/home/suntianhao/construction_agent')
sys.path.insert(0, str(PROJ))
from construction_agent.evaluation.experiment3.runner import CAICS_TAXONOMY, extract_json

S3_SYSTEM = f"""你是施工安全事故分析专家。从事故文本中提取致因编码。

{CAICS_TAXONOMY}

输出最终JSON。字段要求:
- codes: CAICS编码列表,如["M1","F2","H1"]
- accident_type: 必须从以下12类严格选择: 坍塌|高处坠落|触电|机械伤害|起重伤害|物体打击|土方坍塌|车辆伤害|火灾爆炸|中毒窒息|淹溺|其他
- severity: 必须从以下4级严格选择: 一般事故|较大事故|重大事故|特别重大事故
- deaths: 整数
- injuries: 整数
- direct_cause: 一句话简述

若无相关编码，codes可为空数组[]，但accident_type和severity必须填写。

Let's first make a plan to analyze the accident causes, then solve it step by step.
先制定分析计划，再按计划逐步提取编码。分析完毕后，在最后一行输出最终JSON。"""

S3_USER_PREFIX = "请为以下事故制定分析计划并执行：\n\n"

SAMPLES_PATH = PROJ / 'experiments/experiment_kappa/groundtruth/gt_samples_100.json'
OUT_DIR = PROJ / 'experiments/experiment_6strategies/S3_result'
OUT_DIR.mkdir(parents=True, exist_ok=True)

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.getenv("ANTHROPIC_MODEL")
MAX_RETRIES = 2


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
    seen = set()
    merged = []
    for c in all_codes:
        clean = re.sub(r'\s*\[.*?\]', '', c).strip().upper()
        if re.match(r'^[MFTHC]\d$', clean) and clean not in seen:
            seen.add(clean)
            merged.append(clean)
    parsed['codes'] = merged
    parsed['_layered_merged'] = True
    return parsed


def call_llm_s3(text: str) -> dict:
    """调用 LLM，含重试 + 分层合并"""
    user_msg = S3_USER_PREFIX + text
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=4096, temperature=0.0,
                system=S3_SYSTEM, messages=[{"role": "user", "content": user_msg}],
            )
            raw = ""
            for blk in resp.content:
                if hasattr(blk, "text"):
                    raw += blk.text

            parsed = extract_json(raw)
            parsed = merge_layered_json(parsed)

            if parsed and not parsed.get("_error"):
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

print(f"S3 PlanAndSolve 开始运行 | 模型: {MODEL} | 样本: {N}条 | 最大重试: {MAX_RETRIES}次", flush=True)
print(f"{'='*70}", flush=True)

for i, task in enumerate(samples):
    text = task['data']['text']
    aid = task['data']['accident_id'][:8]

    title = ''
    if '## 事故标题' in text:
        title = text.split('## 事故标题\n')[1].split('\n')[0][:50]

    t0 = time.time()
    result = call_llm_s3(text)
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
    "strategy": "S3_PlanAndSolve",
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

out_path = OUT_DIR / "S3_results.json"
out_path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding='utf-8')

print(f"\n{'='*70}")
print(f"✅ S3 PlanAndSolve 完成: {ok}/{N} | {total_t:.0f}s | {total_t/N:.1f}s/条")
print(f"   总编码: {code_total} | 平均: {code_total/N:.1f}/条 | 0编码: {zero_code}条")
print(f"   重试: {retried}条")
print(f"📁 {out_path}")
