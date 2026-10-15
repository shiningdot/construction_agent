"""通用策略运行器：把「策略 × 模型 × golden 300 条」跑成矩阵的一格。

复用 `run_S1_golden.py` 的成熟机制（多 provider / 并发 / 逐条 append+flush /
断点续跑 / 重试退避 / schema 校验 / `--stream`），只把「system 提示词」与
「输出目录」参数化：

    system  = prompt_build.build_system_prompt(--strategy)   # 策略正文 + 31 字段口径
    out_dir = experiments/golden_standard/strategies/{策略}/{模型标签}/

这样六个策略的输出口径完全一致（`codes` + 31 基础特征），矩阵才可比。

用法：
    python3 scripts/golden_s1/run_strategy.py --strategy S0 --provider deepseek \
        --model deepseek-v4-flash --workers 3
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

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "experiments/golden_standard"
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path("/home/suntianhao/construction_agent/.env"), override=True)

from anthropic import Anthropic  # noqa: E402

import completion_repair  # noqa: E402
import prompt_build  # noqa: E402
from run_S1_golden import PROVIDERS, USER_PREFIX, extract_json, validate  # noqa: E402

# 模型名 → 归档目录标签（与 experiments/golden_standard/{flash,pro,glm5.2,hy3} 对齐）
LABELS = {"deepseek-v4-flash": "flash", "deepseek-v4-pro": "pro",
          "glm-5.2": "glm5.2", "hy3": "hy3"}


def _api_call(client, model, system, messages, max_tokens, use_stream):
    """发一次请求，返回 (raw_text, prompt_tokens, completion_tokens)。"""
    if use_stream:
        with client.messages.stream(model=model, max_tokens=max_tokens, temperature=0.0,
                                    system=system, messages=messages) as st:
            resp = st.get_final_message()
    else:
        resp = client.messages.create(model=model, max_tokens=max_tokens, temperature=0.0,
                                      system=system, messages=messages)
    raw = "".join(getattr(b, "text", "") for b in resp.content)
    u = getattr(resp, "usage", None)
    return raw, (getattr(u, "input_tokens", 0) if u else 0), (getattr(u, "output_tokens", 0) if u else 0)


def call_one(client, model, system, rec, max_tokens, use_stream, strategy) -> dict:
    """一次调用。与 run_S1_golden 的同名函数一致，额外记录 hit_cap 与补全情况。"""
    if strategy == "S5":                       # S5′ 是分层多调用，走专用适配器
        from s5_layers import call_s5
        return call_s5(client, model, system, rec, max_tokens, use_stream)

    user = USER_PREFIX + f"事故文本：\n{rec['raw_text']}"
    last_err = ""
    for attempt in range(3):
        t0 = time.time()
        try:
            raw, pt, ct = _api_call(client, model, system,
                                    [{"role": "user", "content": user}], max_tokens, use_stream)
            parsed = extract_json(raw)

            # 解析成功但字段不全 → 用模型自己的残缺输出回灌，要求补齐后重发一次
            # （原因与实测比例见 completion_repair.py 的模块注释）
            repaired = 0
            if "_error" not in parsed:
                extra = {"pt": 0, "ct": 0}

                def _repair(msgs):
                    r2, p2, c2 = _api_call(client, model, system, msgs, max_tokens, use_stream)
                    extra["pt"] += p2
                    extra["ct"] += c2
                    return r2, None

                parsed, repaired = completion_repair.try_repair(_repair, user, raw, parsed)
                pt += extra["pt"]
                ct += extra["ct"]

            # 派生信号（不存原文，只存几个廉价计数）：用于事后判断
            # 「模型是否真的先写了推理/计划」以及 S4′/S5′ 的 explicit vs inferred 比例
            cut = raw.rfind("{")                     # extract_json 取的就是这个位置起
            reason_text = raw[:cut] if cut > 0 else ""
            return {
                "id": rec["id"], "title": rec["title"], "strategy": strategy,
                "tier": rec["tier"], "source_name": rec["source_name"],
                "text_len": len(rec["raw_text"]),
                "parsed": parsed, "parse_error": parsed.get("_error", ""),
                "schema_errors": validate(parsed),
                "missing_fields": completion_repair.missing_fields(parsed),
                "repaired_fields": repaired,          # 本次补全重试补回的字段数
                "hit_cap": ct >= max_tokens,          # 撞顶：思考可能吃光预算
                "elapsed_sec": round(time.time() - t0, 1),
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "reason_chars": len(reason_text),     # JSON 之前的行文长度（计划/推理）
                "n_explicit": raw.count("[explicit]"),
                "n_inferred": raw.count("[inferred]"),
                "attempts": attempt + 1,
            }
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt)
    return {"id": rec["id"], "title": rec["title"], "strategy": strategy,
            "tier": rec["tier"], "source_name": rec["source_name"],
            "text_len": len(rec["raw_text"]), "error": last_err, "attempts": 3}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="S1",
                    help="策略编号 S0/S1/S2/S3/S4/S5（S5 待重设计）")
    ap.add_argument("--provider", default="deepseek", choices=list(PROVIDERS))
    ap.add_argument("--model", default="", help="留空则用 provider 的默认模型")
    ap.add_argument("--label", default="", help="归档目录标签；留空由模型名推导")
    ap.add_argument("--workers", type=int, default=3,
                    help="并发数；默认 3，避免触发模型限流/错误码")
    ap.add_argument("--max-tokens", type=int, default=16384,
                    help="默认 16384；若结果里 hit_cap 为真则需提高")
    ap.add_argument("--out-dir", default="", help="留空则按 strategies/{策略}/{标签}/ 推导")
    ap.add_argument("--candidates", default="golden_candidates.jsonl")
    ap.add_argument("--only-ids", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--stream", action="store_true")
    ap.add_argument("--out-tag", default="")
    args = ap.parse_args()

    recs = [json.loads(l) for l in (GOLD / args.candidates).open(encoding="utf-8")]
    if args.only_ids:
        p = Path(args.only_ids)
        ids = {x.strip() for x in (p.read_text().splitlines() if p.exists()
                                   else args.only_ids.split(","))} - {""}
        recs = [r for r in recs if r["id"] in ids]
    if args.limit:
        recs = recs[: args.limit]

    key_env, url_env, def_model = PROVIDERS[args.provider]
    model = args.model or def_model or os.getenv("ANTHROPIC_MODEL")
    api_key = os.getenv(key_env, "")
    base_url = os.getenv(url_env, "")
    if not api_key:
        sys.exit(f"缺 API key：环境变量 {key_env} 未设置")
    label = args.label or LABELS.get(model, model.replace(".", ""))
    out_dir = (Path(args.out_dir) if args.out_dir
               else GOLD / "strategies" / args.strategy / label)
    out_dir.mkdir(parents=True, exist_ok=True)

    suffix = f"_{args.out_tag}" if args.out_tag else ""
    jsonl_path = out_dir / f"results{suffix}.jsonl"

    done = set()
    if jsonl_path.exists():                      # 断点续跑：只跳过「完全成功」的
        for line in jsonl_path.open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("error") or r.get("parse_error") or r.get("schema_errors"):
                continue
            done.add(r["id"])
    todo = [r for r in recs if r["id"] not in done]

    # S5′ 的提示词由 s5_layers 逐层自行构建，这里无需（也无法）预先拼一个 system
    system = "" if args.strategy == "S5" else prompt_build.build_system_prompt(args.strategy)
    client = Anthropic(api_key=api_key, base_url=base_url)
    print(f"策略 {args.strategy} | provider={args.provider} | 模型 {model} | 标签 {label}\n"
          f"system {len(system)} 字符 | 待跑 {len(todo)} 条（已完成 {len(done)}）| "
          f"workers={args.workers} max_tokens={args.max_tokens} stream={args.stream}\n"
          f"输出 → {jsonl_path}", flush=True)

    t_start = datetime.now()
    lock = threading.Lock()
    with jsonl_path.open("a", encoding="utf-8") as fout, \
            ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(call_one, client, model, system, r,
                          args.max_tokens, args.stream, args.strategy): r for r in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            with lock:
                fout.write(json.dumps(res, ensure_ascii=False) + "\n")
                fout.flush()
                p = res.get("parsed") or {}
                flag = "❌" if res.get("error") else (
                    "⚠" if res.get("parse_error") or res.get("schema_errors") else "✅")
                cap = "🔺撞顶" if res.get("hit_cap") else ""
                el = (datetime.now() - t_start).total_seconds()
                print(f"[{n:03d}/{len(todo)}] {flag} {res['id'][:8]} "
                      f"codes={len(p.get('codes') or [])} "
                      f"type={p.get('accident_type') or '-'} "
                      f"{res.get('elapsed_sec','-')}s "
                      f"tok={res.get('prompt_tokens','-')}+{res.get('completion_tokens','-')} "
                      f"{cap} | {el:.0f}s", flush=True)

    rows = [json.loads(l) for l in jsonl_path.open(encoding="utf-8")]
    ok = sum(1 for r in rows if not (r.get("error") or r.get("parse_error") or r.get("schema_errors")))
    capped = sum(1 for r in rows if r.get("hit_cap"))
    print(f"\n策略 {args.strategy} / {label}: {ok}/{len(rows)} 完全成功 | 撞顶 {capped} 条")
    if capped:
        print(f"⚠ 有 {capped} 条撞顶（completion == {args.max_tokens}），建议提高 --max-tokens 后重跑这些条")


if __name__ == "__main__":
    main()
