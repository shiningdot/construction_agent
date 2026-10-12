"""把已有 S1 结果按修正后的候选集重新切分：保留的复用、被替换的挑出来补跑。"""
import json
from pathlib import Path

G = Path("experiments/golden_standard")
RES = G / "S1_result"

final_ids = [json.loads(l)["id"] for l in (G / "golden_candidates.jsonl").open(encoding="utf-8")]
old = [json.loads(l) for l in (RES / "S1_golden_full.jsonl").open(encoding="utf-8")]
old_ids = {r["id"] for r in old}

keep = [r for r in old if r["id"] in set(final_ids)]
new_ids = [i for i in final_ids if i not in old_ids]
dropped = [r for r in old if r["id"] not in set(final_ids)]

(RES / "S1_golden_v2_kept.jsonl").write_text(
    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
(RES / "S1_golden_v1_removed.jsonl").write_text(
    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in dropped), encoding="utf-8")
Path("/tmp/_new_ids.txt").write_text("\n".join(new_ids), encoding="utf-8")

print(f"修正后候选 {len(final_ids)} | 复用 {len(keep)} | 待补跑 {len(new_ids)} | 剔除 {len(dropped)}")
print(f"待补跑 id 写入 /tmp/_new_ids.txt")
# 复核复用条目的原文是否一致（筛选改动不应影响已选条目的文本）
cur = {json.loads(l)["id"]: len(json.loads(l)["raw_text"])
       for l in (G / "golden_candidates.jsonl").open(encoding="utf-8")}
diff = [r["id"] for r in keep if len(r.get("parsed") is not None) and cur.get(r["id"]) != r.get("text_len")]
print(f"复用条目文本长度不一致数: {len(diff)}")
