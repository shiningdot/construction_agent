"""合并「复用的 249 条」+「补跑的 51 条」为修正后 300 条的最终结果集。"""
import json
from pathlib import Path

G = Path("experiments/golden_standard")
RES = G / "S1_result"

order = [json.loads(l)["id"] for l in (G / "golden_candidates.jsonl").open(encoding="utf-8")]
by_id = {}
for f in ("S1_golden_v2_kept.jsonl", "S1_golden_v2new.jsonl"):
    for line in (RES / f).open(encoding="utf-8"):
        r = json.loads(line)
        by_id[r["id"]] = r

missing = [i for i in order if i not in by_id]
final = [by_id[i] for i in order if i in by_id]
out = RES / "S1_golden_v2.jsonl"
out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in final), encoding="utf-8")

ok = sum(1 for r in final if not r.get("error") and not r.get("parse_error") and not r.get("schema_errors"))
print(f"合并 {len(final)} 条（缺 {len(missing)}）→ {out}")
print(f"完全合格 {ok}/{len(final)}")
print("来源分布:", {k: sum(1 for r in final if r['source_name'] == k) for k in
                    sorted({r['source_name'] for r in final})})
