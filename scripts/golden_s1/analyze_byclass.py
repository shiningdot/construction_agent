"""逐类分析：19 类 × 6 策略，回答两个总表看不出来的问题。

复用 `eval_matrix.py` 的 `load_cause_gt` / `load_preds` / `per_class_metrics`——
同一套取数与指标口径，不另写一份以免漂移。

## 问题一：S4′/S5′ 的过输出集中在哪些类？

总表已知 S4′/S5′ 的编码数/条达 9.0–11.1，而人工 GT 只有 7.71。但"多标了 2–3 个"是均匀
分布还是集中在少数类，决定了它在论文里怎么解释。用逐类的 **预测条数 / GT 条数** 比
（`n_pred/n_gt`）定位：>1 即该类系统性多标，<1 即漏标。

## 问题二：S3′ 略胜 S2′，差异来自哪些类？

两者均值只差 0.003（0.818 vs 0.815），但机制完全不同——S2′ 是人工给定的固定
M→F→T→H→C 因果链，S3′ 是模型自主规划。若差异集中在**非坍塌类**事故上，就能支撑
"S3′ 的自选角度比 S2′ 的固定顺序更贴合多样事故类型"这一解释（对标论文里
plan-and-solve 反而最差，我们这里相反）。故按事故类型分组再比一次。

事故类型取自 `gt_manual/gt_draft.jsonl` 的 `basic_features.accident_type`（人工 GT，
不是模型输出）。

用法：
    python3 scripts/golden_s1/analyze_byclass.py
    python3 scripts/golden_s1/analyze_byclass.py --md out.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from eval_matrix import (ALL_CODES, LABELS, STRATEGIES, TARGET,  # noqa: E402
                         load_cause_gt, load_preds, per_class_metrics)

GTM = ROOT / "experiments/golden_standard/gt_manual"
COLLAPSE = {"坍塌", "土方坍塌"}


def load_accident_types() -> dict[str, str]:
    """事故 id → accident_type（取人工 GT 的基础特征，不用模型输出）。"""
    out = {}
    for l in (GTM / "gt_draft.jsonl").open(encoding="utf-8"):
        if l.strip():
            r = json.loads(l)
            out[r["id"]] = (r.get("basic_features") or {}).get("accident_type") or "未知"
    return out


def cell_ids(gt: dict, preds: dict) -> list[str]:
    return sorted(set(gt) & set(preds))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=str(ROOT / "experiments/golden_standard/strategies/EVAL_CAUSES_BYCLASS.md"))
    args = ap.parse_args()

    gt = load_cause_gt()
    types = load_accident_types()

    # 每格：逐类指标 + 用到的 id
    cells: dict[tuple[str, str], dict] = {}
    for s in STRATEGIES:
        for lb in LABELS:
            preds = load_preds(s, lb)
            ids = cell_ids(gt, preds)
            if not ids:
                continue
            g = [gt[i] for i in ids]
            p = [{c for c in (preds[i].get("codes") or []) if c in ALL_CODES} for i in ids]
            cells[(s, lb)] = {"ids": ids, "gt": g, "pred": p,
                              "pc": per_class_metrics(g, p),
                              "complete": len(ids) >= TARGET}

    out: list[str] = []

    def emit(x: str = ""):
        print(x)
        out.append(x)

    emit("# 逐类分析（19 类 × 6 策略）")
    emit()
    emit("复用 `eval_matrix.py` 的取数与指标口径；每格取**已跑满 300 条**的格，"
         "同一策略内对四个模型求平均。事故类型取自人工 GT 的 `accident_type`。")
    emit()
    n_complete = sum(1 for v in cells.values() if v["complete"])
    emit(f"当前已跑满 {n_complete}/{len(STRATEGIES) * len(LABELS)} 格。")
    emit()

    # ---------- 问题一：过输出定位 ----------
    emit("## 一、过输出定位：逐类「预测条数 / GT 条数」")
    emit()
    emit("比值 >1 表示该类被系统性多标，<1 表示漏标。只看已跑满的格。")
    emit()
    emit("| 类别 | GT 正例 | " + " | ".join(f"{s}′" for s in STRATEGIES) + " |")
    emit("|---" + "---:|" * (len(STRATEGIES) + 1))
    for c in ALL_CODES:
        n_gt = sum(v["pc"][c]["n_gt"] for (s, lb), v in cells.items()
                   if s == STRATEGIES[0] and v["complete"])
        row = []
        for s in STRATEGIES:
            vals, ng = [], 0
            for lb in LABELS:
                v = cells.get((s, lb))
                if not v or not v["complete"]:
                    continue
                vals.append(v["pc"][c]["n_pred"])
                ng = v["pc"][c]["n_gt"]
            row.append(f"{sum(vals) / len(vals) / ng:.2f}" if vals and ng else "-")
        emit(f"| {c} | {n_gt // max(1, len(LABELS)) if n_gt else '-'} | " + " | ".join(row) + " |")
    emit()

    emit("### 过输出比最高的类（各策略平均）")
    emit()
    ratios = []
    for c in ALL_CODES:
        vals = []
        for s in STRATEGIES:
            for lb in LABELS:
                v = cells.get((s, lb))
                if v and v["complete"] and v["pc"][c]["n_gt"]:
                    vals.append(v["pc"][c]["n_pred"] / v["pc"][c]["n_gt"])
        if vals:
            ratios.append((c, sum(vals) / len(vals)))
    ratios.sort(key=lambda x: -x[1])
    for c, r in ratios[:6]:
        emit(f"- **{c}**：{r:.2f}×")
    emit()
    emit("### 漏标最严重的类")
    emit()
    for c, r in ratios[-6:]:
        emit(f"- **{c}**：{r:.2f}×")
    emit()

    # 各策略整体过输出比
    emit("### 各策略整体：预测总条数 / GT 总条数")
    emit()
    emit("| 策略 | " + " | ".join(LABELS) + " | 均值 |")
    emit("|---|" + "---:|" * (len(LABELS) + 1))
    for s in STRATEGIES:
        row, vals = [], []
        for lb in LABELS:
            v = cells.get((s, lb))
            if not v:
                row.append("-"); continue
            np_ = sum(len(x) for x in v["pred"])
            ng_ = sum(len(x) for x in v["gt"])
            ratio = np_ / ng_
            row.append(f"{ratio:.2f}")
            if v["complete"]:
                vals.append(ratio)
        mean = f"{sum(vals) / len(vals):.2f}" if vals else "-"
        emit(f"| {s}′ | " + " | ".join(row) + f" | {mean} |")
    emit()

    # ---------- 问题二：S3′ vs S2′ ----------
    emit("## 二、S3′（模型自主规划）vs S2′（人工给定因果链）")
    emit()
    emit("### 逐类 F1 之差（S3′ − S2′，四模型平均，只看两者都跑满的格）")
    emit()
    emit("| 类别 | S2′ | S3′ | 差值 |")
    emit("|---|---:|---:|---:|")
    deltas = []
    for c in ALL_CODES:
        vals = {}
        for s in ("S2", "S3"):
            acc = []
            for lb in LABELS:
                v = cells.get((s, lb))
                if v and v["complete"] and v["pc"][c]["n_gt"]:
                    acc.append(v["pc"][c]["F1"])
            vals[s] = sum(acc) / len(acc) if acc else None
        if vals["S2"] is None or vals["S3"] is None:
            emit(f"| {c} | - | - | - |"); continue
        d = vals["S3"] - vals["S2"]
        deltas.append((c, d))
        emit(f"| {c} | {vals['S2']:.3f} | {vals['S3']:.3f} | {d:+.3f} |")
    emit()
    deltas.sort(key=lambda x: -abs(x[1]))
    emit("**差异最大的类**：" + "、".join(f"{c} {d:+.3f}" for c, d in deltas[:5]))
    emit()

    emit("### 按事故类型分组（坍塌类 vs 非坍塌类）")
    emit()
    emit(f"坍塌类 = {'/'.join(sorted(COLLAPSE))}；其余归入非坍塌类。")
    emit()
    emit("| 事故类型组 | 事故数 | 策略 | 宏-F1 | 宏召回 | 宏精确 |")
    emit("|---|---:|---|---:|---:|---:|")
    for grp_name, want_collapse in (("坍塌类", True), ("非坍塌类", False)):
        for s in ("S0", "S1", "S2", "S3", "S4", "S5"):
            f1s, rs, ps, n_ids = [], [], [], 0
            for lb in LABELS:
                v = cells.get((s, lb))
                if not v or not v["complete"]:
                    continue
                idx = [k for k, i in enumerate(v["ids"])
                       if (types.get(i) in COLLAPSE) == want_collapse]
                if not idx:
                    continue
                n_ids = len(idx)
                pc = per_class_metrics([v["gt"][k] for k in idx], [v["pred"][k] for k in idx])
                present = [x for x in pc.values() if x["n_gt"] > 0]
                if not present:
                    continue
                f1s.append(sum(x["F1"] for x in present) / len(present))
                rs.append(sum(x["R"] for x in present) / len(present))
                ps.append(sum(x["P"] for x in present) / len(present))
            if not f1s:
                continue
            emit(f"| {grp_name} | {n_ids} | {s}′ | {sum(f1s) / len(f1s):.3f} | "
                 f"{sum(rs) / len(rs):.3f} | {sum(ps) / len(ps):.3f} |")
    emit()
    emit("> 该表的宏平均只在**该组内 GT 出现过的类**上求，故与总表的宏平均不可直接比。")
    emit("> 关注点：S3′−S2′ 的差在两组之间是否方向/幅度不同。")
    emit()

    Path(args.md).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n已写入 {args.md}")


if __name__ == "__main__":
    main()
