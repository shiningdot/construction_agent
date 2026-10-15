"""把用户手工裁决的致因 GT 合并成覆盖全部 300 条的完整版。

## 为什么需要合并

用户 2026-10-15 放入的两份文件：
- `gt_causes_full.jsonl` —— 294 条，是「导出完整 GT」的产物；
- `cause_annotations.jsonl` —— 932 条人工裁决流水。

`cause_annotation.html` 的导出页**只遍历「有待裁格」的事故**（DATA 里只有 294 条），
所以有 6 条三方完全一致、无人需要裁决的事故被漏掉了。逐条核对后，
这 6 条的编码集合可直接取自 `gt_causes_draft.jsonl` 的 `base_codes`
（其 provenance 全为 auto_include/auto_exclude，`pending_human` 为空）。

本脚本把两者并成 `cause_gt.jsonl`（300 条），并把「哪些条来自导出、哪些条来自补齐」
记在 `gt_source` 字段里，便于论文交代数据来源。

## 输入 / 输出（均在 experiments/golden_standard/gt_manual/）
输入：gt_causes_full.jsonl（294）、gt_causes_draft.jsonl（300）
输出：cause_gt.jsonl（300）

用法：
    python3 scripts/golden_s1/merge_manual_cause_gt.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "experiments/golden_standard"
GTM = GOLD / "gt_manual"

INCLUDE = {"auto_include", "human_include", "human_extra"}
ALL_CODES = [f"M{i}" for i in range(1, 6)] + [f"F{i}" for i in range(1, 5)] + \
            [f"T{i}" for i in range(1, 5)] + [f"H{i}" for i in range(1, 5)] + ["C1", "C2"]


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


def codes_from_prov(prov: dict) -> list[str]:
    """按固定序从 provenance 推出正标签集合。"""
    return [c for c in ALL_CODES if prov.get(c) in INCLUDE]


def main():
    full = {r["accident_id"]: r for r in load(GTM / "gt_causes_full.jsonl")}
    draft = {r["id"]: r for r in load(GTM / "gt_causes_draft.jsonl")}
    cand = [json.loads(l) for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8") if l.strip()]
    cand_ids = [r["id"] for r in cand]

    print(f"导出完整 GT：{len(full)} 条")
    print(f"致因草稿：   {len(draft)} 条")
    print(f"候选样本：   {len(cand_ids)} 条")

    missing = [i for i in cand_ids if i not in full]
    extra = [i for i in full if i not in draft]
    print(f"\n导出文件遗漏（需从草稿补齐）：{len(missing)} 条")
    for i in missing:
        print(f"  {i[:12]}  {draft[i]['title']}")
    if extra:
        print(f"⚠ 导出文件里有草稿中不存在的 id：{extra}")

    out = []
    for i in cand_ids:
        if i in full:
            r = full[i]
            codes = r["codes"]
            prov = r["provenance"]
            src = "annotation_export"
        else:
            d = draft[i]
            prov = d["provenance"]
            codes = codes_from_prov(prov)
            src = "draft_uncontested"
        out.append({"accident_id": i, "title": (full.get(i) or draft[i])["title"],
                    "codes": codes, "provenance": prov, "gt_source": src})

    # ---- 自检 ----
    assert len(out) == len(cand_ids) == 300, "条数不是 300"
    assert len({r["accident_id"] for r in out}) == 300, "id 有重复"
    bad = [r["accident_id"] for r in out if codes_from_prov(r["provenance"]) != r["codes"]]
    if bad:
        print(f"⚠ {len(bad)} 条的 codes 与 provenance 推导不一致：{bad[:5]}")
    unresolved = [(r["accident_id"], c) for r in out for c in ALL_CODES
                  if r["provenance"].get(c) not in INCLUDE and
                  r["provenance"].get(c) not in {"auto_exclude", "human_exclude"}]
    if unresolved:
        print(f"⚠ {len(unresolved)} 格状态未定（如 UNRESOLVED）：{unresolved[:5]}")

    sizes = [len(r["codes"]) for r in out]
    print(f"\n编码数/条：均值 {sum(sizes) / len(sizes):.2f}｜"
          f"最小 {min(sizes)}｜最大 {max(sizes)}")
    from collections import Counter
    hp = Counter()
    for r in out:
        for c in ALL_CODES:
            hp[r["provenance"].get(c)] += 1
    print("provenance 分布：", dict(hp))
    print("来源分布：", dict(Counter(r["gt_source"] for r in out)))

    dst = GTM / "cause_gt.jsonl"
    with dst.open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n已写出 {dst}（{len(out)} 条）")


if __name__ == "__main__":
    main()
