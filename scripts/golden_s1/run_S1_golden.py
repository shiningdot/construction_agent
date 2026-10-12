"""对 golden standard 300 条候选运行 S1'(FewShot) 策略。

S1' = S1 FewShot 提示词（CAICS 5 层 19 类 + 3 个少样本示例，取自 experiment3/runner.py）
      扩展输出 schema 至【基础特征 31 字段 + CAICS 编码】。

用法：
    python3 scripts/golden_s1/run_S1_golden.py --limit 3      # 试点
    python3 scripts/golden_s1/run_S1_golden.py                # 全量 300
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen")
GOLD = ROOT / "experiments/golden_standard"
OUT_DIR = GOLD / "S1_result"
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path("/home/suntianhao/construction_agent/.env"), override=True)

from anthropic import Anthropic  # noqa: E402

from prompt_build import build_system_prompt  # noqa: E402

CAICS_CODES = {f"M{i}" for i in range(1, 6)} | {f"F{i}" for i in range(1, 5)} | \
              {f"T{i}" for i in range(1, 5)} | {f"H{i}" for i in range(1, 5)} | {"C1", "C2"}

USER_PREFIX = "请参照示例，为以下事故同时提取【基础特征】与【CAICS 致因编码】：\n\n"

_print_lock = threading.Lock()


def extract_json(text: str) -> dict:
    """从混合输出中提取 JSON（与 runner.py 同款策略，独立实现避免跨 worktree 依赖）。"""
    try:
        return json.loads(text)
    except Exception:
        pass
    import re
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    last = text.rfind("{")
    if last >= 0:
        depth = 0
        for i in range(last, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[last:i + 1])
                    except Exception:
                        break
    return {"_error": "json_parse_failed"}


def validate(parsed: dict) -> list[str]:
    errs = []
    codes = parsed.get("codes")
    if not isinstance(codes, list) or not codes:
        errs.append("codes_missing")
    else:
        bad = [c for c in codes if c not in CAICS_CODES]
        if bad:
            errs.append(f"codes_invalid:{bad}")
    if not parsed.get("accident_type"):
        errs.append("accident_type_missing")
    if not parsed.get("severity"):
        errs.append("severity_missing")
    return errs


def call_one(client, model, system, rec, max_tokens) -> dict:
    user = USER_PREFIX + f"事故文本：\n{rec['raw_text']}"
    last_err = ""
    for attempt in range(3):
        t0 = time.time()
        try:
            resp = client.messages.create(
                model=model, max_tokens=max_tokens, temperature=0.0,
                system=system, messages=[{"role": "user", "content": user}],
            )
            raw = "".join(getattr(b, "text", "") for b in resp.content)
            parsed = extract_json(raw)
            u = getattr(resp, "usage", None)
            return {
                "id": rec["id"], "title": rec["title"], "tier": rec["tier"],
                "source_name": rec["source_name"], "text_len": len(rec["raw_text"]),
                "parsed": parsed, "parse_error": parsed.get("_error", ""),
                "schema_errors": validate(parsed),
                "elapsed_sec": round(time.time() - t0, 1),
                "prompt_tokens": getattr(u, "input_tokens", 0) if u else 0,
                "completion_tokens": getattr(u, "output_tokens", 0) if u else 0,
                "attempts": attempt + 1,
            }
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt)
    return {"id": rec["id"], "title": rec["title"], "tier": rec["tier"],
            "source_name": rec["source_name"], "text_len": len(rec["raw_text"]),
            "error": last_err, "attempts": 3}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--out-tag", default="")
    ap.add_argument("--only-ids", default="",
                    help="逗号分隔的事故 id，或指向每行一个 id 的文件；只跑这些")
    ap.add_argument("--candidates", default="golden_candidates.jsonl",
                    help="候选集文件名（相对 experiments/golden_standard/）")
    args = ap.parse_args()

    recs = [json.loads(l) for l in (GOLD / args.candidates).open(encoding="utf-8")]
    if args.only_ids:
        p = Path(args.only_ids)
        ids = {x.strip() for x in (p.read_text().splitlines() if p.exists()
                                   else args.only_ids.split(","))} - {""}
        recs = [r for r in recs if r["id"] in ids]
        print(f"按 id 清单过滤 → {len(recs)} 条")
    if args.limit:
        recs = recs[: args.limit]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = args.out_tag or ("pilot" if args.limit else "full")
    jsonl_path = OUT_DIR / f"S1_golden_{tag}.jsonl"
    json_path = OUT_DIR / f"S1_golden_{tag}_results.json"

    done = set()
    if jsonl_path.exists():                      # 断点续跑
        for line in jsonl_path.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                pass
    todo = [r for r in recs if r["id"] not in done]

    system = build_system_prompt()
    model = os.getenv("ANTHROPIC_MODEL")
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"),
                       base_url=os.getenv("ANTHROPIC_BASE_URL"))
    print(f"模型 {model} | system {len(system)} 字符 | 待跑 {len(todo)} 条 "
          f"(已完成 {len(done)}) | workers={args.workers} max_tokens={args.max_tokens}", flush=True)

    t_start = datetime.now()
    results = []
    lock = threading.Lock()
    with jsonl_path.open("a", encoding="utf-8") as fout, \
            ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(call_one, client, model, system, r, args.max_tokens): r for r in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            results.append(res)
            with lock:
                fout.write(json.dumps(res, ensure_ascii=False) + "\n")
                fout.flush()
                p = res.get("parsed") or {}
                flag = "❌" if res.get("error") else ("⚠" if res.get("parse_error") or res.get("schema_errors") else "✅")
                with _print_lock:
                    el = (datetime.now() - t_start).total_seconds()
                    print(f"[{n:03d}/{len(todo)}] {flag} {res['id'][:8]} "
                          f"codes={len(p.get('codes') or [])} "
                          f"type={p.get('accident_type') or '-'} "
                          f"{res.get('elapsed_sec','-')}s "
                          f"tok={res.get('prompt_tokens','-')}+{res.get('completion_tokens','-')} "
                          f"| {el:.0f}s", flush=True)

    # 汇总（含历史已完成项）
    all_rows = [json.loads(l) for l in jsonl_path.open(encoding="utf-8")]
    ok = sum(1 for r in all_rows if not r.get("error") and not r.get("parse_error"))
    summary = {
        "strategy": "S1_FewShot+basic_features",
        "model": model, "timestamp": t_start.isoformat(),
        "total": len(all_rows), "success": ok,
        "failed": len(all_rows) - ok,
        "schema_error_count": sum(1 for r in all_rows if r.get("schema_errors")),
        "total_prompt_tokens": sum(r.get("prompt_tokens", 0) for r in all_rows),
        "total_completion_tokens": sum(r.get("completion_tokens", 0) for r in all_rows),
        "avg_sec_per_case": round(
            sum(r.get("elapsed_sec", 0) for r in all_rows) / max(1, len(all_rows)), 1),
    }
    json_path.write_text(json.dumps({"summary": summary, "results": all_rows},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ {ok}/{len(all_rows)} | {summary['avg_sec_per_case']}s/条 | "
          f"tok={summary['total_prompt_tokens']}+{summary['total_completion_tokens']}")
    print(f"📁 {json_path}")


if __name__ == "__main__":
    main()
