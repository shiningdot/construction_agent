"""S2 AutoCoT: 100条全量运行 — 因果链推理 + 分层JSON后处理 + 重试机制"""
import json, time, os, sys, re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(Path('/home/suntianhao/construction_agent/.env'), override=True)
from anthropic import Anthropic

PROJ = Path('/home/suntianhao/construction_agent')
sys.path.insert(0, str(PROJ))
from construction_agent.evaluation.experiment3.runner import CAICS_TAXONOMY, extract_json

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.getenv("ANTHROPIC_MODEL")

# ============ S2 修复版 Prompt ============
# 修复1: 加入完整 OUTPUT_JSON 模板，含12类事故类型枚举
# 修复2: 保留因果链推理，但明确要求最终输出 codes 数组

S2_SYSTEM = f"""你是施工安全事故分析专家。从事故文本中提取致因编码。

{CAICS_TAXONOMY}

分析步骤：按 M(管理缺陷)→F(防线失效)→T(触发条件)→H(人的行为)→C(后果放大) 因果链逐层推理。
对每一层：判断文本中是否有相关证据，有则列出编码，无则简要说明原因。

输出最终JSON。字段要求:
- codes: CAICS编码列表,如["M1","F2","H1"]（将所有层级的编码都放入此数组）
- accident_type: 必须从以下12类严格选择: 坍塌|高处坠落|触电|机械伤害|起重伤害|物体打击|土方坍塌|车辆伤害|火灾爆炸|中毒窒息|淹溺|其他
- severity: 必须从以下4级严格选择: 一般事故|较大事故|重大事故|特别重大事故
- deaths: 整数
- injuries: 整数
- direct_cause: 一句话简述

若无相关编码，codes可为空数组[]，但accident_type和severity必须填写。
注意：最终JSON必须包含codes数组，不要用M/F/T/H/C分层键。codes中放入所有层的编码。

示例: {{"codes":["M1","F2","T2","H1"], "accident_type":"坍塌", "severity":"较大事故", "deaths":3, "injuries":2, "direct_cause":"模板支撑体系失稳导致浇筑时坍塌"}}
分析完毕后，在最后一行输出最终JSON。"""

S2_USER_PREFIX = "请按M→F→T→H→C因果链分析以下事故：\n\n"

SAMPLES_PATH = PROJ / 'experiments/experiment_kappa/groundtruth/gt_samples_100.json'
OUT_DIR = PROJ / 'experiments/experiment_6strategies/S2_result'
OUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_RETRIES = 2  # 最多重试2次（总计3次尝试）


def merge_layered_json(parsed: dict) -> dict:
    """后处理：如果 LLM 输出了分层格式 {M:[], F:[], T:[], H:[], C:[]}，自动合并到 codes"""
    layered_keys = {'M', 'F', 'T', 'H', 'C'}
    found_layers = [k for k in layered_keys if k in parsed and isinstance(parsed[k], list)]
    if not found_layers:
        return parsed  # 不是分层格式，直接返回

    # 合并所有分层编码到 codes
    all_codes = []
    for k in found_layers:
        for item in parsed[k]:
            if isinstance(item, str):
                all_codes.append(item)
    # 去重保持顺序
    seen = set()
    merged = []
    for c in all_codes:
        if c not in seen:
            seen.add(c)
            merged.append(c)

    parsed['codes'] = merged
    # 清理分层键（可选，保留也不影响）
    return parsed


def call_llm_s2(text: str) -> dict:
    """调用 LLM，含重试机制和分层JSON后处理"""
    user_msg = S2_USER_PREFIX + text
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=2048, temperature=0.0,
                system=S2_SYSTEM, messages=[{"role": "user", "content": user_msg}],
            )
            raw = ""
            for blk in resp.content:
                if hasattr(blk, "text"):
                    raw += blk.text

            parsed = extract_json(raw)

            # 修复3: 分层JSON后处理 — 合并 M/F/T/H/C 到 codes
            parsed = merge_layered_json(parsed)

            # 如果解析成功且有内容，直接返回
            if parsed and not parsed.get("_error"):
                return {"parsed": parsed, "raw": raw, "attempts": attempt + 1}
            else:
                last_error = parsed.get("_error", "unknown parse error")
                if attempt < MAX_RETRIES:
                    time.sleep(1)  # 短暂等待后重试
                continue

        except Exception as e:
            last_error = str(e)[:100]
            if attempt < MAX_RETRIES:
                time.sleep(2)  # API错误等久一点
            continue

    # 全部重试耗尽
    return {"parsed": {"codes": [], "_error": last_error}, "raw": "", "attempts": MAX_RETRIES + 1}


# ============ 主流程 ============
samples = json.loads(SAMPLES_PATH.read_text(encoding='utf-8'))
N = len(samples)
results = []
t_start = datetime.now()

print(f"S2 AutoCoT 开始运行 | 模型: {MODEL} | 样本: {N}条 | 最大重试: {MAX_RETRIES}次", flush=True)
print(f"{'='*70}", flush=True)

for i, task in enumerate(samples):
    text = task['data']['text']
    aid = task['data']['accident_id'][:8]

    # 提取标题
    title = ''
    if '## 事故标题' in text:
        title = text.split('## 事故标题\n')[1].split('\n')[0][:50]

    t0 = time.time()
    result = call_llm_s2(text)
    elapsed = time.time() - t0

    parsed = result["parsed"]
    raw = result.get("raw", "")
    attempts = result.get("attempts", 1)

    codes = parsed.get("codes") or []
    err = parsed.get("_error") or ""

    # 修复4: None 值安全
    d_val = parsed.get('deaths') or 0
    i_val = parsed.get('injuries') or 0
    a_type = parsed.get('accident_type') or '?'
    severity = parsed.get('severity') or '?'

    # 进度估算
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
        "raw_response": raw[:3000],
        "codes": codes,
        "accident_type": a_type,
        "severity": severity,
        "deaths": d_val,
        "injuries": i_val,
        "direct_cause": parsed.get("direct_cause") or "",
        "elapsed_sec": round(elapsed, 1),
        "attempts": attempts,
        "parse_error": err,
    })

# ============ 保存结果 ============
total_t = (datetime.now() - t_start).total_seconds()
ok = sum(1 for r in results if not r.get("error") and not r.get("parse_error"))
retried = sum(1 for r in results if r.get("attempts", 1) > 1)
code_total = sum(len(r.get("codes", [])) for r in results)
zero_code = sum(1 for r in results if len(r.get("codes", [])) == 0)

summary = {
    "strategy": "S2_AutoCoT",
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

out_path = OUT_DIR / "S2_results.json"
out_path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding='utf-8')

print(f"\n{'='*70}")
print(f"✅ S2 AutoCoT 完成: {ok}/{N} | {total_t:.0f}s | {total_t/N:.1f}s/条")
print(f"   总编码: {code_total} | 平均: {code_total/N:.1f}/条 | 0编码: {zero_code}条")
print(f"   重试: {retried}条")
print(f"📁 {out_path}")
