"""S1 FewShot: 100条全量运行"""
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
s1 = STRATEGIES["S1_FewShot"]

SAMPLES_PATH = PROJ / 'experiments/experiment_kappa/groundtruth/gt_samples_100.json'
OUT_DIR = PROJ / 'experiments/experiment_6strategies/S1_result'
OUT_DIR.mkdir(parents=True, exist_ok=True)

samples = json.loads(SAMPLES_PATH.read_text(encoding='utf-8'))
N = len(samples)
results = []
t_start = datetime.now()

for i, task in enumerate(samples):
    text = task['data']['text']
    aid = task['data']['accident_id'][:8]
    user = s1["user_prefix"] + text
    title = text.split('## 事故标题\n')[1].split('\n')[0][:50] if '## 事故标题' in text else ''

    t0 = time.time()
    try:
        resp = client.messages.create(model=MODEL, max_tokens=2048, temperature=0.0,
                                       system=s1["system"], messages=[{"role":"user","content":user}])
    except Exception as e:
        print(f"[{i+1:03d}/100] {aid} ❌ API: {e}", flush=True)
        results.append({"index": i+1, "accident_id": aid, "title": title, "error": f"API: {e}"})
        continue

    raw = ""
    for blk in resp.content:
        if hasattr(blk, "text"): raw += blk.text
    elapsed = time.time() - t0
    parsed = extract_json(raw)

    codes = parsed.get("codes") or []
    err = parsed.get("_error") or ""
    d_val = parsed.get("deaths") or 0
    pt = resp.usage.input_tokens if hasattr(resp,'usage') and resp.usage else 0
    ct = resp.usage.output_tokens if hasattr(resp,'usage') and resp.usage else 0

    elapsed_total = (datetime.now() - t_start).total_seconds()
    rate = (i+1) / max(elapsed_total, 1) * 60
    eta = max(0, (N - i - 1) / max(rate, 0.01))

    print(f"[{i+1:03d}/100] {aid} {'⚠' if err else '✅'} | codes={len(codes):2d} "
          f"type={parsed.get('accident_type') or '?':8s} sev={parsed.get('severity') or '?':8s} "
          f"d={d_val:2d} | {elapsed:.1f}s tok={pt}+{ct} | ETA {eta:.0f}s", flush=True)

    results.append({
        "index": i+1, "accident_id": aid, "title": title,
        "parsed": parsed,
        "elapsed_sec": round(elapsed, 1),
        "prompt_tokens": pt, "completion_tokens": ct,
        "parse_error": err,
    })

total_t = (datetime.now() - t_start).total_seconds()
total_pt = sum(r.get("prompt_tokens",0) for r in results)
total_ct = sum(r.get("completion_tokens",0) for r in results)
ok = sum(1 for r in results if not r.get("error") and not r.get("parse_error"))

summary = {"strategy":"S1_FewShot","model":MODEL,"timestamp":t_start.isoformat(),
           "total":N,"success":ok,"elapsed_total_sec":round(total_t,1),
           "avg_sec_per_case":round(total_t/N,1),
           "total_prompt_tokens":total_pt,"total_completion_tokens":total_ct}
out_path = OUT_DIR / "S1_results.json"
out_path.write_text(json.dumps({"summary":summary,"results":results}, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"\n✅ {ok}/{N} | {total_t:.0f}s | {total_t/N:.1f}s/条 | tok={total_pt}+{total_ct}")
print(f"📁 {out_path}")
