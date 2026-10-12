"""把人工标注页导出的 gt_annotations.jsonl 合并进 gt_draft.jsonl。

行为：
  - 按 accident_id + field 定位，把人工裁决值写入 basic_features
  - provenance 记为 human_decision，来源（flash/pro/db/custom）写入 resolution_notes 便于审计
  - 该字段从 needs_human 移除
  - 全部合并成功后删除标注文件（--keep 可保留）

用法：
    python3 scripts/golden_s1/apply_annotations.py            # 合并并删除标注文件
    python3 scripts/golden_s1/apply_annotations.py --keep      # 合并但保留标注文件
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GOLD = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen"
            "/experiments/golden_standard")
GT = GOLD / "gt"
ANNO = GT / "gt_annotations.jsonl"
DRAFT = GT / "gt_draft.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="合并后保留标注文件")
    args = ap.parse_args()

    if not ANNO.exists():
        print(f"找不到标注文件 {ANNO}")
        sys.exit(1)

    annos = [json.loads(l) for l in ANNO.open(encoding="utf-8") if l.strip()]
    rows = [json.loads(l) for l in DRAFT.open(encoding="utf-8")]
    by_id = {r["id"]: r for r in rows}
    print(f"标注 {len(annos)} 条 | 草稿 {len(rows)} 条")

    applied, problems = 0, []
    for a in annos:
        r = by_id.get(a["accident_id"])
        if r is None:
            problems.append(f"id 不存在: {a['accident_id']} {a['title'][:20]}")
            continue
        f = a["field"]
        if f not in r["provenance"]:
            problems.append(f"字段不在记录中: {a['accident_id']} {f}")
            continue
        if f not in r["needs_human"]:
            problems.append(f"该字段本不在待人工列表: {a['accident_id']} {f}"
                            f"（当前 provenance={r['provenance'][f]}）")
            continue
        if r["basic_features"].get(f) is not None:
            problems.append(f"该格已有值 {r['basic_features'][f]!r}，将被覆盖: "
                            f"{a['accident_id']} {f}")

        old = r["basic_features"].get(f)
        r["basic_features"][f] = a["value"]
        r["provenance"][f] = "human_decision"
        r["resolution_notes"][f] = f"人工裁决，来源 {a.get('source','?')}"
        r["needs_human"] = [x for x in r["needs_human"] if x != f]
        applied += 1
        print(f"  ✓ {a['accident_id']} {f:16s} {old!r} → {a['value']!r} "
              f"({a.get('source','?')})  {a['title'][:24]}")

    if problems:
        print("\n⚠ 异常：")
        for p in problems:
            print("  ", p)
        print("\n发现问题，未写入、未删除标注文件。")
        sys.exit(1)

    DRAFT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                     encoding="utf-8")

    left = [(r["id"], r["needs_human"]) for r in rows if r["needs_human"]]
    from collections import Counter
    prov = Counter(p for r in rows for p in r["provenance"].values())
    print(f"\n已写入 {applied} 条 → {DRAFT}")
    print(f"provenance 分布: {dict(prov)}")
    print(f"仍待人工: {sum(len(f) for _, f in left)} 格"
          + (f"（{left}）" if left else " —— 全部完成"))

    if not args.keep:
        ANNO.unlink()
        print(f"已删除标注文件 {ANNO}")


if __name__ == "__main__":
    main()
