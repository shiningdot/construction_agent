"""补跑 S1' 中的异常条目（API 错误 / JSON 解析失败 / schema 违规）。

保留原始输出以便诊断，并把修复结果合并回 S1_golden_results.jsonl。

用法：
    python3 scripts/golden_s1/repair_S1_golden.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen")
GOLD = ROOT / "experiments/golden_standard"
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path("/home/suntianhao/construction_agent/.env"), override=True)
from anthropic import Anthropic  # noqa: E402

from prompt_build import build_system_prompt  # noqa: E402
from run_S1_golden import USER_PREFIX, extract_json, validate  # noqa: E402


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(GOLD), help="结果所在目录（可指向 pro/ 等子目录）")
    ap.add_argument("--model", default="", help="覆盖模型名；留空读 .env")
    args = ap.parse_args()
    out_dir = Path(args.dir)

    path = out_dir / "S1_golden_results.jsonl"
    rows = [json.loads(l) for l in path.open(encoding="utf-8")]
    raw_text = {json.loads(l)["id"]: json.loads(l)["raw_text"]
                for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}

    bad = [r for r in rows if r.get("error") or r.get("parse_error") or r.get("schema_errors")]
    print(f"共 {len(rows)} 条，异常 {len(bad)} 条，开始补跑")

    system = build_system_prompt()
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"),
                       base_url=os.getenv("ANTHROPIC_BASE_URL"))
    model = args.model or os.getenv("ANTHROPIC_MODEL")

    fixed = {}
    for r in bad:
        user = USER_PREFIX + f"事故文本：\n{raw_text[r['id']]}"
        try:
            resp = client.messages.create(
                model=model, max_tokens=16384, temperature=0.0, system=system,
                messages=[{"role": "user", "content": user}])
            out = "".join(getattr(b, "text", "") for b in resp.content)
            parsed = extract_json(out)
            errs = validate(parsed)
            u = getattr(resp, "usage", None)
            new = {**r, "parsed": parsed, "raw_output": out,
                   "parse_error": parsed.get("_error", ""), "schema_errors": errs,
                   "repair": True,
                   "prompt_tokens": getattr(u, "input_tokens", 0) if u else 0,
                   "completion_tokens": getattr(u, "output_tokens", 0) if u else 0}
            new.pop("error", None)
            fixed[r["id"]] = new
            flag = "✅" if not errs and not new["parse_error"] else "⚠"
            print(f"  {flag} {r['id'][:8]} codes={len(parsed.get('codes') or [])} errs={errs}")
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ {r['id'][:8]} {type(e).__name__}: {e}")

    merged = [fixed.get(r["id"], r) for r in rows]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged),
                    encoding="utf-8")
    ok = sum(1 for r in merged if not r.get("error") and not r.get("parse_error"))
    print(f"补跑完成：现在 {ok}/{len(merged)} 条 JSON 可解析")


if __name__ == "__main__":
    main()
