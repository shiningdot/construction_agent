"""S0 ZeroShot: 100条全量运行，实时进度 + 技术指标记录"""
import json, time, os, sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(Path('/home/suntianhao/construction_agent/.env'), override=True)
from anthropic import Anthropic

PROJ = Path('/home/suntianhao/construction_agent')
sys.path.insert(0, str(PROJ))
from construction_agent.evaluation.experiment3.runner import STRATEGIES, extract_json

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.getenv("ANTHROPIC_MODEL")
s0 = STRATEGIES["S0_ZeroShot"]

SAMPLES_PATH = PROJ / 'experiments/experiment_kappa/groundtruth/gt_samples_100.json'
OUT_DIR = PROJ / 'experiments/experiment_6strategies/S0_result'
OUT_DIR.mkdir(parents=True, exist_ok=True)

samples = json.loads(SAMPLES_PATH.read_text(encoding='utf-8'))
N = len(samples)
results = []
t_start = datetime.now()

for i, task in enumerate(samples):
    text = task['data']['text']
    aid = task['data']['accident_id'][:8]
    user = s0["user_prefix"] + text

    title = ''
    if '## 事故标题' in text:
        title = text.split('## 事故标题\n')[1].split('\n')[0][:50]

    t0 = time.time()
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=2048, temperature=0.0,
            system=s0["system"], messages=[{"role": "user", "content": user}],
        )
    except Exception as e:
        print(f"[{i+1:03d}/100] {aid} ❌ API error: {e}")
        results.append({"accident_id": aid, "title": title, "error": f"API: {e}"})
        continue

    raw = ""
    for blk in resp.content:
        if hasattr(blk, "text"):
            raw += blk.text

    elapsed = time.time() - t0
    result = extract_json(raw)
    codes = result.get("codes", [])
    err = result.get("_error", "")

    # Token 用量
    prompt_tokens = resp.usage.input_tokens if hasattr(resp, 'usage') and resp.usage else 0
    completion_tokens = resp.usage.output_tokens if hasattr(resp, 'usage') and resp.usage else 0

    # 进度
    elapsed_total = (datetime.now() - t_start).total_seconds()
    rate = (i + 1) / max(elapsed_total, 1) * 60  # 条/分钟
    eta = (N - i - 1) / max(rate, 0.01)

    status = f"{'✅' if not err else '⚠'}"
    d_val = result.get('deaths') or 0
    i_val = result.get('injuries') or 0
    print(f"[{i+1:03d}/100] {aid} {status} | "
          f"codes={len(codes):2d} type={str(result.get('accident_type','?')):8s} sev={str(result.get('severity','?')):8s} "
          f"d={d_val:2d} | {elapsed:.1f}s tok={prompt_tokens}+{completion_tokens} | "
          f"ETA {eta:.0f}s", flush=True)

    results.append({
        "index": i + 1,
        "accident_id": aid,
        "title": title,
        "raw_response": raw[:3000],
        "parsed": result,
        "codes": codes,
        "accident_type": result.get("accident_type") or "",
        "severity": result.get("severity") or "",
        "deaths": result.get("deaths") or 0,
        "injuries": result.get("injuries") or 0,
        "direct_cause": result.get("direct_cause") or "",
        "elapsed_sec": round(elapsed, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "parse_error": err,
    })

# ── 保存 ──
total_t = (datetime.now() - t_start).total_seconds()
total_prompt = sum(r["prompt_tokens"] for r in results)
total_completion = sum(r["completion_tokens"] for r in results)
success = sum(1 for r in results if "error" not in r or not r.get("error"))

summary = {
    "strategy": "S0_ZeroShot",
    "model": MODEL,
    "timestamp": t_start.isoformat(),
    "total": N,
    "success": success,
    "elapsed_total_sec": round(total_t, 1),
    "avg_sec_per_case": round(total_t / N, 1),
    "total_prompt_tokens": total_prompt,
    "total_completion_tokens": total_completion,
    "total_tokens": total_prompt + total_completion,
}

output = {"summary": summary, "results": results}
out_path = OUT_DIR / "S0_results.json"
out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"\n{'='*50}")
print(f"✅ 完成: {success}/{N} | {total_t:.0f}s | {total_t/N:.1f}s/条")
print(f"📊 Token: prompt={total_prompt} completion={total_completion} total={total_prompt+total_completion}")
print(f"📁 {out_path}")
