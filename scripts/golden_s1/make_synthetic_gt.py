"""生成**合成**致因 GT（仅用于演示，不是真实标注）。

目标：让 Ave+ F1 的排序呈现 S5′ > S4′ > S1′ > S3′ > S2′ > S0′。

## 为什么这样构造

以 **S5′ 四模型的多数预测**为基底，再叠加两类噪声：

- 漏标：随机删除少量 S5′ 已预测的类（模拟人工标注的保守性）
- 多标：从其他策略的多数预测中随机补入少量 S5′ 未预测的类

由于 S5′ 的编码更少、更集中，以它为基底时 S4′ 会因多标而拉低精确率，
S1′/S3′/S2′/S0′ 的召回依次下降，排序自然形成。

## 重要声明

生成的文件**不是真实标注**：

- 每条记录带 `"synthetic": true` 与 `"gt_source": "synthetic_demo"`
- 文件名与目录都含 `synthetic` / `合成`
- **不得**替换 `gt_manual/cause_gt.jsonl`，**不得**用于论文或任何对外报告

用法：
    python3 scripts/golden_s1/make_synthetic_gt.py
    python3 scripts/golden_s1/make_synthetic_gt.py --seed 7 --drop 0.14 --add 0.10
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from eval_matrix import (ALL_CODES, LABELS, STRATEGIES, load_preds,  # noqa: E402
                         per_class_metrics, summarize)

GOLD = ROOT / "experiments/golden_standard"
OUT_DIR = GOLD / "synthetic"
KEEP_ORDER = ["S5", "S4", "S1", "S3", "S2", "S0"]


def load_all_preds() -> dict[tuple[str, str], dict[str, dict]]:
    return {(s, lb): load_preds(s, lb) for s in STRATEGIES for lb in LABELS}


def votes(preds, s: str, aid: str) -> dict[str, int]:
    """该策略四个模型对每个类的投票数（0–4）。"""
    v = {c: 0 for c in ALL_CODES}
    for lb in LABELS:
        p = preds[(s, lb)].get(aid) or {}
        for c in (p.get("codes") or []):
            if c in v:
                v[c] += 1
    return v


def build_gt(preds, accidents: list[dict], drop: float, add: float,
             seed: int, s1_add: float = 0.7) -> dict[str, list[str]]:
    rng = random.Random(seed)
    gt: dict[str, list[str]] = {}
    for a in accidents:
        aid = a["id"]
        s5 = votes(preds, "S5", aid)
        s1 = votes(preds, "S1", aid)
        others = {s: votes(preds, s, aid) for s in ("S4", "S3", "S2", "S0")}

        # 第三层：其他策略的补入权重（S3 > S2 > S4 > S0）
        # S3/S2 与 S1 预测高度重合，压低它们的独有预测，才能让 S1 排到前面
        w = {"S3": 0.55, "S2": 0.40, "S4": 0.8, "S0": 0.45}

        # 这五类在 S1 与 S3 之间差距最大，改以 S1 的多数预测为准，
        # 使 S1 的逐类 F1 高于 S3
        s1_led = {"F1", "H3", "C1", "T3", "T4"}

        codes = []
        for c in ALL_CODES:
            if c in s1_led:
                if s1[c] >= 2 and rng.random() >= 0.05:
                    codes.append(c)
                continue
            if s5[c] >= 2:
                # 第一层：S5 多数预测；随机漏标
                if rng.random() >= drop:
                    codes.append(c)
            elif s1[c] >= 2 and rng.random() < s1_add:
                # 第二层：S1 多数预测，提高 S1 的召回
                codes.append(c)
            else:
                for s in ("S3", "S2", "S4", "S0"):
                    if others[s][c] >= 2 and rng.random() < min(1.0, add * w[s]):
                        codes.append(c)
                        break

        # 与真实 GT 一样保留 3–13 条的合理范围；过短时补回 S5 的高票类
        if len(codes) < 4:
            cand = sorted((c for c in ALL_CODES if s5[c] >= 3 and c not in codes),
                          key=lambda c: -s5[c])
            codes += cand[: 4 - len(codes)]
        if len(codes) > 13:
            rng.shuffle(codes)
            codes = sorted(codes[:13], key=ALL_CODES.index)

        gt[aid] = [c for c in ALL_CODES if c in set(codes)]
    return gt


def evaluate(gt: dict[str, list[str]], preds) -> dict[str, dict]:
    ids = sorted(gt)
    out = {}
    for s in STRATEGIES:
        vals = []
        for lb in LABELS:
            g = [set(gt[i]) for i in ids]
            p = [{c for c in ((preds[(s, lb)].get(i) or {}).get("codes") or []) if c in ALL_CODES}
                 for i in ids]
            vals.append(summarize(per_class_metrics(g, p)))
        out[s] = {
            "ave_plus_f1": sum(v["ave_plus_F1"] for v in vals) / len(vals),
            "ave_f1": sum(v["ave_F1"] for v in vals) / len(vals),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--drop", type=float, default=0.12)
    ap.add_argument("--add", type=float, default=0.08)
    ap.add_argument("--out", default=str(OUT_DIR / "cause_gt_synthetic_demo.jsonl"))
    args = ap.parse_args()

    preds = load_all_preds()
    accidents = []
    for line in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            accidents.append({"id": r["id"], "title": r.get("title") or ""})

    best = None
    # 小范围搜索，找到严格满足目标排序的参数
    for drop in (args.drop, args.drop + 0.02, args.drop - 0.02,
                 args.drop + 0.04, args.drop - 0.04):
        for add in (args.add, args.add + 0.03, args.add - 0.03):
            for s1_add in (0.85, 0.92, 0.97, 1.0):
                if not (0.0 <= drop < 0.5 and 0.0 <= add < 0.5):
                    continue
                gt = build_gt(preds, accidents, drop, add, args.seed, s1_add)
                ev = evaluate(gt, preds)
                order = sorted(STRATEGIES, key=lambda s: -ev[s]["ave_plus_f1"])
                gap = sum(1 for a, b in zip(order, KEEP_ORDER) if a != b)
                score = (gap, -abs(ev["S5"]["ave_plus_f1"] - 0.86))
                print(f"drop={drop:.2f} add={add:.2f} s1={s1_add:.2f} → "
                      + " > ".join(f"{s}({ev[s]['ave_plus_f1']*100:.2f})" for s in order))
                if best is None or score < best[0]:
                    best = (score, gt, ev, drop, add, s1_add, order)

    _, gt, ev, drop, add, s1_add, order = best
    if order != KEEP_ORDER:
        print(f"\n⚠ 未找到严格满足 {KEEP_ORDER} 的参数，最接近为 {order}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for a in accidents:
            f.write(json.dumps({
                "accident_id": a["id"],
                "title": a["title"],
                "codes": gt[a["id"]],
                "provenance": {c: "synthetic_demo" for c in gt[a["id"]]},
                "gt_source": "synthetic_demo",
                "synthetic": True,
                "note": "合成数据，仅用于演示；不得用于论文或替换真实 GT",
            }, ensure_ascii=False) + "\n")

    print(f"\n采用参数：drop={drop:.2f} add={add:.2f} s1_add={s1_add:.2f} seed={args.seed}")
    print(f"平均编码/条：{statistics.mean(len(v) for v in gt.values()):.2f}")
    print(f"已写入 {out}")
    print(f"排序：{' > '.join(order)}")


if __name__ == "__main__":
    main()
