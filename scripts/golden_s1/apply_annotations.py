"""把人工标注页导出的标注文件合并进对应的 GT 草稿，并删除标注文件。

按标注内容自动分派：
  含 "field" 字段        → 基础特征：写入 gt_draft.jsonl 的 basic_features
  含 "code" + "decision" → 致因编码：写入 gt_causes_draft.jsonl 的 base_codes

共同保证：逐条校验（id 存在 / 该格确在待人工列表 / 值未被占），任一条不符即中止，
不写盘、不删标注文件。

用法：
    python3 scripts/golden_s1/apply_annotations.py                  # 自动找 gt/ 下的标注文件
    python3 scripts/golden_s1/apply_annotations.py --file X.jsonl    # 指定
    python3 scripts/golden_s1/apply_annotations.py --keep            # 合并但保留标注文件
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analyze_causes_threeway import ALL_CODES  # noqa: E402

GT = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen"
          "/experiments/golden_standard/gt")
BASIC_DRAFT = GT / "gt_draft.jsonl"
CAUSE_DRAFT = GT / "gt_causes_draft.jsonl"
BASIC_ANNO = GT / "gt_annotations.jsonl"
CAUSE_ANNO = GT / "cause_annotations.jsonl"


def find_anno(explicit: str | None):
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    for p in (BASIC_ANNO, CAUSE_ANNO):
        if p.exists():
            return p
    return None


def merge_basic(annos, problems):
    rows = [json.loads(l) for l in BASIC_DRAFT.open(encoding="utf-8")]
    by_id = {r["id"]: r for r in rows}
    applied = 0
    for a in annos:
        r = by_id.get(a["accident_id"])
        if r is None:
            problems.append(f"id 不存在: {a['accident_id']}")
            continue
        f = a["field"]
        if f not in r["provenance"]:
            problems.append(f"字段不在记录中: {a['accident_id']} {f}")
            continue
        if f not in r["needs_human"]:
            problems.append(f"该字段不在待人工列表: {a['accident_id']} {f}"
                            f"（现 provenance={r['provenance'][f]}）")
            continue
        if r["basic_features"].get(f) is not None:
            problems.append(f"该格已有值 {r['basic_features'][f]!r}，将被覆盖: {a['accident_id']} {f}")
        old = r["basic_features"].get(f)
        r["basic_features"][f] = a["value"]
        r["provenance"][f] = "human_decision"
        r["resolution_notes"][f] = f"人工裁决，来源 {a.get('source','?')}"
        r["needs_human"] = [x for x in r["needs_human"] if x != f]
        applied += 1
        print(f"  ✓ {a['accident_id'][:8]} {f:16s} {old!r} → {a['value']!r} ({a.get('source','?')})")
    if problems:
        return None, applied
    BASIC_DRAFT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                           encoding="utf-8")
    left = sum(len(r["needs_human"]) for r in rows)
    print(f"已写入 {applied} 条 → {BASIC_DRAFT}")
    print(f"仍待人工: {left} 格" + ("" if left == 0 else " —— 未完成"))
    return rows, applied


def merge_cause(annos, problems):
    rows = [json.loads(l) for l in CAUSE_DRAFT.open(encoding="utf-8")]
    by_id = {r["id"]: r for r in rows}
    applied = 0
    for a in annos:
        r = by_id.get(a["accident_id"])
        if r is None:
            problems.append(f"id 不存在: {a['accident_id']}")
            continue
        c = a["code"]
        if c not in r["provenance"]:
            problems.append(f"编码不在记录中: {a['accident_id']} {c}")
            continue
        src = a.get("source", "adjudicate")
        if src == "adjudicate":
            if c not in r["pending_human"]:
                problems.append(f"该编码不在待裁列表: {a['accident_id']} {c}"
                                f"（现 provenance={r['provenance'][c]}）")
                continue
            dec = a["decision"]
            if dec == "include":
                if c not in r["base_codes"]:
                    r["base_codes"].append(c)
                r["provenance"][c] = "human_include"
            elif dec == "exclude":
                r["base_codes"] = [x for x in r["base_codes"] if x != c]
                r["provenance"][c] = "human_exclude"
            else:
                problems.append(f"未知 decision: {a['accident_id']} {c} {dec}")
                continue
            r["pending_human"] = [x for x in r["pending_human"] if x != c]
        elif src == "extra":
            if c not in r["base_codes"]:
                r["base_codes"].append(c)
            r["provenance"][c] = "human_extra"
        else:
            problems.append(f"未知 source: {a['accident_id']} {c} {src}")
            continue
        applied += 1
    if problems:
        return None, applied
    rows.sort(key=lambda r: r["id"])
    for r in rows:                       # 编码按 19 类固定序输出
        r["base_codes"] = [c for c in ALL_CODES if c in set(r["base_codes"])]
    CAUSE_DRAFT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                           encoding="utf-8")
    left = sum(len(r["pending_human"]) for r in rows)
    prov = Counter(p for r in rows for p in r["provenance"].values())
    print(f"已写入 {applied} 条 → {CAUSE_DRAFT}")
    print(f"provenance: {dict(prov)}")
    print(f"仍待裁: {left} 格" + ("" if left == 0 else " —— 未完成"))
    return rows, applied


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None, help="标注文件路径；留空则自动在 gt/ 下查找")
    ap.add_argument("--keep", action="store_true", help="合并后保留标注文件")
    args = ap.parse_args()

    anno = find_anno(args.file)
    if anno is None:
        print("找不到标注文件（gt_annotations.jsonl 或 cause_annotations.jsonl）")
        sys.exit(1)

    annos = [json.loads(l) for l in anno.open(encoding="utf-8") if l.strip()]
    if not annos:
        print(f"{anno} 是空的")
        sys.exit(1)
    is_basic = "field" in annos[0]
    is_cause = "code" in annos[0] and "decision" in annos[0]
    kind = "基础特征" if is_basic else "致因编码" if is_cause else "未知"
    print(f"{anno.name}：{len(annos)} 条 → 判定为【{kind}】标注")
    if not (is_basic or is_cause):
        print("无法识别的标注格式")
        sys.exit(1)

    problems: list[str] = []
    if is_basic:
        _, applied = merge_basic(annos, problems)
    else:
        _, applied = merge_cause(annos, problems)

    if problems:
        print(f"\n⚠ 发现 {len(problems)} 处问题，已中止——未写盘、未删标注文件：")
        for p in problems[:20]:
            print("  ", p)
        sys.exit(1)

    if not args.keep:
        anno.unlink()
        print(f"已删除标注文件 {anno}")


if __name__ == "__main__":
    main()
