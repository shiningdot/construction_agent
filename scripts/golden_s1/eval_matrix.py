"""用人工 GT 评测「策略 × 模型」矩阵：致因 F1 + 基础特征准确率。

## 数据

- **致因 GT**：`experiments/golden_standard/gt_manual/cause_gt.jsonl`（300 条）
  由 `merge_manual_cause_gt.py` 生成——294 条来自用户导出的完整 GT + 6 条无争议事故
  从草稿补齐（导出页只遍历「有待裁格」的事故，故遗漏了这 6 条）。
- **基础特征 GT**：`gt_manual/gt_draft.jsonl`（300 条 × **13 个核心客观特征**）。
  注意：模型输出 31 个字段，但 GT 只敲定了其中 13 个（其余被 GT_REPORT 剔除：
  名称类/书写类文本，以及若干零收益的客观字段），故基础特征只能在这 13 个上算。
- **预测**：`strategies/{策略}/{模型}/results.jsonl`，按 id 去重、同 id 取最后一条成功行。

## 对齐

GT 与预测都按 `accident_id` 键控，但**行序不同**。评测前一律按 id 排序，
构造成位置对齐的两个列表——因为项目既有的 `runner.compute_metrics()` 是按位置对齐的
（它内部用 `list.index` 定位），顺序不一致会静默算错。
本脚本同时用自实现的一份干净计算做交叉校验，两者不一致会显式报出来。

用法：
    python3 scripts/golden_s1/eval_matrix.py
    python3 scripts/golden_s1/eval_matrix.py --md out.md --csv out.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

GOLD = ROOT / "experiments/golden_standard"
GTM = GOLD / "gt_manual"
STRAT = GOLD / "strategies"

STRATEGIES = ["S0", "S1", "S2", "S3", "S4", "S5"]
LABELS = ["glm5.2", "flash", "hy3", "pro"]
TARGET = 300
ALL_CODES = [f"M{i}" for i in range(1, 6)] + [f"F{i}" for i in range(1, 5)] + \
            [f"T{i}" for i in range(1, 5)] + [f"H{i}" for i in range(1, 5)] + ["C1", "C2"]

# 基础特征 GT 覆盖的 13 个字段（取自 gt_draft.jsonl 的 basic_features）
BASIC_13 = ["accident_type", "severity", "project_type", "collapse_subtype", "deaths",
            "injuries", "trapped_count", "direct_economic_loss", "fall_height",
            "date", "accident_time", "location", "city"]


# ---------------------------------------------------------------- 载入

def load_cause_gt() -> dict[str, set[str]]:
    return {r["accident_id"]: set(r["codes"])
            for r in (json.loads(l) for l in (GTM / "cause_gt.jsonl").open(encoding="utf-8") if l.strip())}


def load_basic_gt() -> dict[str, dict]:
    out = {}
    for l in (GTM / "gt_draft.jsonl").open(encoding="utf-8"):
        if l.strip():
            r = json.loads(l)
            out[r["id"]] = r["basic_features"]
    return out


def load_preds(strategy: str, label: str) -> dict[str, dict]:
    """返回 {id: parsed}，按 id 去重、同 id 取最后一条成功行。"""
    p = STRAT / strategy / label / "results.jsonl"
    if not p.exists():
        return {}
    ok: dict[str, dict] = {}
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("error") or r.get("parse_error") or r.get("schema_errors"):
            continue
        ok[r["id"]] = r.get("parsed") or {}
    return ok


# ---------------------------------------------------------------- 指标

def per_class_metrics(gt: list[set], pred: list[set]) -> dict[str, dict]:
    res = {}
    for c in ALL_CODES:
        tp = sum(1 for g, p in zip(gt, pred) if c in g and c in p)
        fp = sum(1 for g, p in zip(gt, pred) if c not in g and c in p)
        fn = sum(1 for g, p in zip(gt, pred) if c in g and c not in p)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        res[c] = {"P": prec, "R": rec, "F1": f1, "n_gt": sum(1 for g in gt if c in g),
                  "n_pred": sum(1 for p in pred if c in p)}
    return res


def summarize(pc: dict[str, dict]) -> dict:
    present = [v for v in pc.values() if v["n_gt"] > 0]
    plus = [v for v in present if v["n_gt"] >= 5]
    def m(vals, k):
        return sum(v[k] for v in vals) / len(vals) if vals else 0.0
    return {
        "ave_P": m(present, "P"), "ave_R": m(present, "R"), "ave_F1": m(present, "F1"),
        "ave_plus_F1": m(plus, "F1"), "ave_plus_R": m(plus, "R"),
        "n_classes": len(present), "n_classes_plus": len(plus),
    }


def runner_metrics(ids: list[str], cause_gt: dict[str, set[str]],
                   preds: dict[str, dict]) -> dict | None:
    """用项目既有的 runner.compute_metrics 算一份，作为**主口径**（与论文已报告数字同源）。

    该函数按位置对齐，故先把 GT 与预测**按 id 排序**成等长列表。传完整 parsed 字典
    而不是只传 codes——它内部用 `list.index()` 定位，字典越小越容易撞上重复元素而错位。
    """
    try:
        import prompt_build
        runner = prompt_build._load_runner()
    except Exception:
        return None
    gt = [{"codes": sorted(cause_gt[i])} for i in ids]
    pr = [dict(preds[i]) for i in ids]
    return runner.compute_metrics(pr, gt)


def micro_f1(gt: list[set], pred: list[set]) -> tuple[float, float, float]:
    tp = sum(1 for g, p in zip(gt, pred) for c in (g & p))
    fp = sum(1 for g, p in zip(gt, pred) for c in (p - g))
    fn = sum(1 for g, p in zip(gt, pred) for c in (g - p))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def basic_acc(gtb: dict[str, dict], preds: dict[str, dict]) -> dict[str, tuple[int, int]]:
    """逐字段统计 (命中, 可比)。数值字段允许 ±0.5 容差；None 与 0 视为不同。"""
    hit = Counter(); tot = Counter()
    for i, g in gtb.items():
        p = preds.get(i)
        if p is None:
            continue
        for f in BASIC_13:
            if f not in g:
                continue
            gv, pv = g[f], p.get(f)
            tot[f] += 1
            if isinstance(gv, (int, float)) and isinstance(pv, (int, float)):
                if abs(gv - pv) <= 0.5:
                    hit[f] += 1
            elif gv == pv:
                hit[f] += 1
    return {f: (hit[f], tot[f]) for f in BASIC_13}


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=str(STRAT / "EVAL_CAUSES.md"))
    ap.add_argument("--csv", default=str(STRAT / "EVAL_CAUSES.csv"))
    args = ap.parse_args()

    cause_gt = load_cause_gt()
    basic_gt = load_basic_gt()
    print(f"致因 GT {len(cause_gt)} 条 | 基础特征 GT {len(basic_gt)} 条 × {len(BASIC_13)} 字段")

    rows = []
    detail = {}
    for s in STRATEGIES:
        for lb in LABELS:
            preds = load_preds(s, lb)
            if not preds:
                continue
            ids = sorted(set(cause_gt) & set(preds))
            if not ids:
                continue
            gtl = [cause_gt[i] for i in ids]
            prl = [{c for c in (preds[i].get("codes") or []) if c in ALL_CODES} for i in ids]
            pc = per_class_metrics(gtl, prl)
            summ = summarize(pc)
            mp, mr, mf = micro_f1(gtl, prl)
            rn = runner_metrics(ids, cause_gt, preds)
            exact = sum(1 for g, p in zip(gtl, prl) if g == p) / len(ids)
            avg_codes = sum(len(p) for p in prl) / len(prl)
            avg_gt = sum(len(g) for g in gtl) / len(gtl)
            ba = basic_acc({i: basic_gt[i] for i in ids if i in basic_gt}, preds)
            bh = sum(h for h, _ in ba.values()); bt = sum(t for _, t in ba.values())
            rows.append({
                "strategy": s, "model": lb, "n": len(ids),
                # 主口径：项目既有的 runner.compute_metrics（与论文已报告数字同源）
                "ave_F1": rn["ave"]["F1"] if rn else summ["ave_F1"],
                "ave_plus_F1": rn["ave_plus"]["F1"] if rn else summ["ave_plus_F1"],
                "ave_R": rn["ave"]["R"] if rn else summ["ave_R"],
                "ave_P": rn["ave"]["P"] if rn else summ["ave_P"],
                "ave_plus_R": rn["ave_plus"]["R"] if rn else summ["ave_plus_R"],
                "ave_plus_P": rn["ave_plus"]["P"] if rn else 0.0,
                # 自实现的一份，仅用于交叉校验
                "chk_ave_F1": summ["ave_F1"], "chk_ave_plus_F1": summ["ave_plus_F1"],
                "complete": len(ids) >= TARGET,
                "micro_F1": mf, "micro_P": mp, "micro_R": mr,
                "exact": exact, "avg_codes": avg_codes, "avg_gt": avg_gt,
                "basic_acc": bh / bt if bt else 0.0,
            })
            detail[(s, lb)] = pc

    if not rows:
        sys.exit("没有任何可评测的格（GT 或结果缺失）")

    rows.sort(key=lambda r: (r["strategy"], LABELS.index(r["model"])))

    lines = []
    def emit(x=""):
        print(x); lines.append(x)

    emit("# 致因编码评测（人工 GT，n=300）")
    emit()
    emit(f"GT：`gt_manual/cause_gt.jsonl`（300 条，平均 {rows[0]['avg_gt']:.2f} 编码/条）。"
         f"预测按 id 去重、同 id 取最后一条成功行；GT 与预测**按 id 排序后位置对齐**。")
    emit()
    emit("宏平均只对 GT 中出现过的类别求平均；`Ave+` 进一步只取 GT 正例数 ≥5 的类别（与项目既有口径一致）。")
    emit()

    def table(metric: str, title: str, fmt="{:.3f}"):
        emit(f"## {title}")
        emit()
        emit("| 策略 | 模型 | n | " + metric + " |")
        emit("|---|---|---:|---:|")
        for r in rows:
            emit(f"| {r['strategy']}′ | {r['model']} | {r['n']} | {fmt.format(r[metric])} |")
        emit()

    emit("## 1. 总览")
    emit()
    emit("| 策略 | 模型 | n | Ave-F1 | Ave+-F1 | 宏召回 | 宏精确 | 微-F1 | 完全一致率 | 编码数/条 | 基础特征准确率 |")
    emit("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        star = "" if r["complete"] else "*"
        emit(f"| {r['strategy']}′ | {r['model']}{star} | {r['n']} | {r['ave_F1']:.3f} | "
             f"{r['ave_plus_F1']:.3f} | {r['ave_R']:.3f} | {r['ave_P']:.3f} | "
             f"{r['micro_F1']:.3f} | {r['exact']:.1%} | {r['avg_codes']:.2f} | "
             f"{r['basic_acc']:.1%} |")
    emit()
    emit(f"> 参考：GT 平均 {rows[0]['avg_gt']:.2f} 编码/条——模型编码数明显高于此值即为过输出。")
    emit("> 带 `*` 的格**尚未跑满 300 条**，数字只是阶段性值，不可与满格直接比较。")
    emit()

    # 策略 × 模型 的 Ave+-F1 透视表
    emit("## 2. Ave+-F1 透视表")
    emit()
    emit("| 策略 \\ 模型 | " + " | ".join(LABELS) + " | 均值 |")
    emit("|---|" + "---:|" * (len(LABELS) + 1))
    for s in STRATEGIES:
        cells, vals = [], []
        for lb in LABELS:
            m = next((r for r in rows if r["strategy"] == s and r["model"] == lb), None)
            if m:
                star = "" if m["complete"] else "*"
                cells.append(f"{m['ave_plus_F1']:.3f}{star}")
                if m["complete"]:
                    vals.append(m["ave_plus_F1"])
            else:
                cells.append("-")
        mean = f"{sum(vals) / len(vals):.3f}" if vals else "-"
        emit(f"| {s}′ | " + " | ".join(cells) + f" | {mean} |")
    emit()
    emit("> 均值只取已跑满 300 条的格；带 `*` 的格未计入。")
    emit()

    # 逐类 F1
    emit("## 3. 逐类 F1（每类取各格平均）")
    emit()
    emit("| 类别 | GT 正例 | " + " | ".join(f"{s}′" for s in STRATEGIES) + " |")
    emit("|---" + "---:|" * (len(STRATEGIES) + 1))
    for c in ALL_CODES:
        n_gt = detail[next(iter(detail))][c]["n_gt"] if detail else 0
        cells = []
        for s in STRATEGIES:
            vals = [detail[(s, lb)][c]["F1"] for lb in LABELS if (s, lb) in detail]
            cells.append(f"{sum(vals) / len(vals):.3f}" if vals else "-")
        emit(f"| {c} | {n_gt} | " + " | ".join(cells) + " |")
    emit()

    # 逐类召回（词汇门控关注点）
    emit("## 4. 逐类召回")
    emit()
    emit("| 类别 | " + " | ".join(f"{s}′" for s in STRATEGIES) + " |")
    emit("|---" + "---:|" * len(STRATEGIES))
    for c in ALL_CODES:
        cells = []
        for s in STRATEGIES:
            vals = [detail[(s, lb)][c]["R"] for lb in LABELS if (s, lb) in detail]
            cells.append(f"{sum(vals) / len(vals):.3f}" if vals else "-")
        emit(f"| {c} | " + " | ".join(cells) + " |")
    emit()

    # CSV
    with Path(args.csv).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n已写入 {args.csv}")

    Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写入 {args.md}")

    # 交叉校验：主口径（runner）vs 自实现，逐格比对
    print("\n=== 交叉校验：runner.compute_metrics vs 自实现 ===")
    print("（差异来自「参与宏平均的类别集合」定义：runner 取 GT∪预测 出现过的类，"
          "自实现只取 GT 中出现过的类。差值很小即说明两边一致。）")
    worst = 0.0
    for r in rows:
        d = abs(r["ave_plus_F1"] - r["chk_ave_plus_F1"])
        worst = max(worst, d)
        flag = "  ⚠ 偏差偏大" if d > 0.01 else ""
        print(f"  {r['strategy']}′×{r['model']:<7s} n={r['n']:>3d} | "
              f"runner Ave+-F1={r['ave_plus_F1']:.3f} | 自实现={r['chk_ave_plus_F1']:.3f} | "
              f"Δ={d:.3f}{flag}")
    print(f"\n最大偏差 {worst:.3f}" + ("" if worst <= 0.01 else " ← 需人工核对"))


if __name__ == "__main__":
    main()
