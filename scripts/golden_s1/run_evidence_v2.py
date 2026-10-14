"""在 golden 样本上试跑「证据链 v2」（source_facts + inference_rule + evidence_type）。

- 分层抽 N 条（默认 30）：按事故类型配额，层内按库内编码数均匀取，保证覆盖不同复杂度
- 模型默认 deepseek-v4-flash（与四模型对照同档）
- **捕获思考过程**：不再丢弃 thinking 块，全文存入 raw_reasoning，
  并置 raw_reasoning_is_evidence=false —— 它只是审计附件，不是证据
- 复用 run_S1_golden.py 的逐条落盘 / 断点续跑做法

用法：
    python3 scripts/golden_s1/run_evidence_v2.py --limit 30
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GOLD = ROOT / "experiments/golden_standard"
OUT = GOLD / "evidence_v2"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path("/home/suntianhao/construction_agent/.env"), override=True)
from anthropic import Anthropic  # noqa: E402

from evidence_v2_prompt import build_system_prompt, build_tool, EVIDENCE_TYPES  # noqa: E402

USER_PREFIX = "请对以下事故进行 CAICS 致因编码，每个编码给出三段式可核查证据：\n\n"
_P = threading.Lock()


def pick_samples(cands, k, seed=7):
    """分层抽样：按事故类型配额（每类至少 1 条），层内按编码数均匀取。"""
    gt_n = {}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for aid, c in con.execute("select accident_id, count(*) from caics_causes group by 1"):
        gt_n[aid] = c
    con.close()

    by = defaultdict(list)
    for c in cands:
        by[c["accident_type"] or "未知"].append(c)
    for t in by:
        by[t].sort(key=lambda x: (gt_n.get(x["id"], 0), x["id"]))

    rnd = random.Random(seed)
    quota = {}
    for t, items in by.items():
        quota[t] = 1
    rest = k - sum(quota.values())
    if rest > 0:                                     # 按类型规模分配剩余名额
        tot = sum(len(v) for v in by.values())
        for t in sorted(by, key=lambda x: -len(by[x])):
            add = max(0, round(rest * len(by[t]) / tot))
            quota[t] += add
    out = []
    for t, n in quota.items():
        items = by[t]
        if n >= len(items):
            out += items
        else:                                        # 层内均匀取
            step = len(items) / n
            out += [items[min(len(items) - 1, int(i * step))] for i in range(n)]
    rnd.shuffle(out)
    return out[:k], gt_n


def call_one(client, model, system, tool, rec, max_tokens):
    user = USER_PREFIX + f"事故文本：\n{rec['raw_text']}"
    last = ""
    for attempt in range(3):
        t0 = time.time()
        try:
            req = dict(model=model, max_tokens=max_tokens, temperature=0.3,
                       system=system, tools=[tool],
                       messages=[{"role": "user", "content": user}])
            if max_tokens > 20000:
                # SDK 对可能超过 10 分钟的非流式请求会直接报错，大预算必须走流式
                with client.messages.stream(**req) as st:
                    resp = st.get_final_message()
            else:
                resp = client.messages.create(**req)
            payload, thinking, has_think = None, "", False
            for blk in resp.content:
                bt = getattr(blk, "type", "")
                if bt == "thinking":
                    has_think = True
                    thinking += getattr(blk, "thinking", "") or ""
                elif bt == "tool_use":
                    payload = dict(getattr(blk, "input", {}) or {})
                elif hasattr(blk, "text") and payload is None:
                    pass
            u = getattr(resp, "usage", None)
            return {
                "id": rec["id"], "title": rec["title"], "text_len": len(rec["raw_text"]),
                "parsed": payload, "parse_error": "" if payload else "no_tool_use",
                "has_thinking": has_think,
                "raw_reasoning": thinking,
                "raw_reasoning_is_evidence": False,
                "stop_reason": getattr(resp, "stop_reason", ""),
                "elapsed_sec": round(time.time() - t0, 1),
                "prompt_tokens": getattr(u, "input_tokens", 0) if u else 0,
                "completion_tokens": getattr(u, "output_tokens", 0) if u else 0,
                "attempts": attempt + 1,
            }
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt)
    return {"id": rec["id"], "title": rec["title"], "error": last, "attempts": 3}


def validate(payload) -> list[str]:
    errs = []
    causes = (payload or {}).get("causes")
    if not isinstance(causes, list) or not causes:
        return ["causes_missing"]
    for i, c in enumerate(causes):
        if c.get("code") not in (["M1","M2","M3","M4","M5","F1","F2","F3","F4","T1","T2","T3",
                                  "T4","H1","H2","H3","H4","C1","C2"]):
            errs.append(f"c{i}_bad_code:{c.get('code')}")
        if not isinstance(c.get("source_facts"), list):
            errs.append(f"c{i}_source_facts_not_list")
        if c.get("evidence_type") not in EVIDENCE_TYPES:
            errs.append(f"c{i}_bad_type:{c.get('evidence_type')}")
        if not c.get("inference_rule"):
            errs.append(f"c{i}_no_rule")
        # 纪律校验：空 facts 必须是 external；direct_quote 必须带 facts
        if not c.get("source_facts") and c.get("evidence_type") != "external":
            errs.append(f"c{i}_empty_facts_not_external")
        if c.get("evidence_type") == "direct_quote" and not c.get("source_facts"):
            errs.append(f"c{i}_direct_without_facts")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--model", default="")
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    cands = [json.loads(l) for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")]
    samples, gt_n = pick_samples(cands, args.limit)
    out = Path(args.out_dir) if args.out_dir else OUT
    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / "results.jsonl"
    (out / "samples.json").write_text(json.dumps(
        [{"id": s["id"], "title": s["title"], "accident_type": s["accident_type"],
          "gt_codes": gt_n.get(s["id"], 0)} for s in samples], ensure_ascii=False, indent=1),
        encoding="utf-8")

    done = set()
    if jsonl.exists():
        for line in jsonl.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["id"])
            except Exception:  # noqa: BLE001
                pass
    todo = [s for s in samples if s["id"] not in done]

    system, tool = build_system_prompt(), build_tool()
    model = args.model or os.getenv("ANTHROPIC_MODEL")
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"),
                       base_url=os.getenv("ANTHROPIC_BASE_URL"))
    print(f"模型 {model} | 抽样 {len(samples)} 条（分层） | 待跑 {len(todo)} | 提示词 {len(system)} 字符")

    with jsonl.open("a", encoding="utf-8") as fout, ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(call_one, client, model, system, tool, s, args.max_tokens): s
                for s in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            if r.get("parsed"):
                r["schema_errors"] = validate(r["parsed"])
            with _P:
                fout.write(json.dumps(r, ensure_ascii=False) + "\n")
                fout.flush()
                p = r.get("parsed") or {}
                causes = p.get("causes") or []
                types = {}
                for c in causes:
                    types[c.get("evidence_type")] = types.get(c.get("evidence_type"), 0) + 1
                print(f"[{n:02d}/{len(todo)}] {r['id'][:8]} "
                      f"{'✅' if r.get('parsed') else '❌'} codes={len(causes)} "
                      f"{types} think={'Y' if r.get('has_thinking') else 'N'} "
                      f"{r.get('elapsed_sec','-')}s", flush=True)

    rows = [json.loads(l) for l in jsonl.open(encoding="utf-8")]
    ok = sum(1 for r in rows if r.get("parsed") and not r.get("parse_error"))
    se = sum(1 for r in rows if r.get("schema_errors"))
    th = sum(1 for r in rows if r.get("has_thinking"))
    print(f"\n完成 {ok}/{len(rows)} | schema 违规 {se} | 含 thinking 的 {th}")
    print(f"产出 → {jsonl}")


if __name__ == "__main__":
    main()
