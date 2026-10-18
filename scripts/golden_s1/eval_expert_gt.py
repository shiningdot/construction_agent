"""以专家审核导出作为 GT，生成「四模型 × 六策略 × 19 类」指标表。

## GT

`experiments/golden_standard/CAICS致因审核_孙天浩_2026-10-18.json`
（审核页导出，`schema=caics-expert-review-v1`，300 条全部 `reviewed`）。
取每条 `reviewed_codes` 作为真值。

## 指标

- **F1 / 召回 / 精确**：与 `eval_matrix.per_class_metrics()` 完全同口径
- **准确率**：逐类 `(TP + TN) / N`。
  注意**不能**用 runner `compute_metrics()` 里的 `Acc`——那是 `TP / N`，等同召回，
  与本表要的 accuracy 不是一回事（论文 Table 3 的 Accuracy 也是 `(TP+TN)/N`）
- **Ave**：19 类未加权宏平均
- **Ave+**：GT 正例数 ≥5 的类（新 GT 下 C2 仅 4 条被排除，共 18 类）

## 输出

Markdown 宽表：每个指标一张，行 = 4 模型 × 6 策略，列 = 19 类 + Ave + Ave+。
每列在 24 行中的最优值加粗、次优值加下划线（论文 Table 3 样式）。

用法：
    python3 scripts/golden_s1/eval_expert_gt.py
    python3 scripts/golden_s1/eval_expert_gt.py --gt path/to/review.json --md out.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from eval_matrix import ALL_CODES, LABELS, STRATEGIES, load_preds, per_class_metrics  # noqa: E402

GOLD = ROOT / "experiments/golden_standard"
DEFAULT_GT = GOLD / "CAICS致因审核_孙天浩_2026-10-18.json"
DEFAULT_MD = GOLD / "strategies" / "专家GT_指标表.md"

STRATEGY_LABELS = {
    "S0": "S0′ 零样本", "S1": "S1′ 少样本", "S2": "S2′ 逐层推理",
    "S3": "S3′ 计划与求解", "S4": "S4′ CAICS-CoT", "S5": "S5′ CAICS-CoT+",
}
MODEL_LABELS = {
    "glm5.2": "GLM-5.2", "flash": "DeepSeek Flash",
    "hy3": "混元 hy3", "pro": "DeepSeek Pro",
}
CODE_NAMES = {
    "M1": "现场监管缺失", "M2": "隐患排查失效", "M3": "安全培训不足",
    "M4": "制度规程漏洞", "M5": "市场行为违规",
    "F1": "临边洞口防护缺失", "F2": "支撑体系失稳", "F3": "设备安全装置失效",
    "F4": "个体防护未使用",
    "T1": "设备机具故障", "T2": "物料状态异常", "T3": "地质环境突变", "T4": "气象水文触发",
    "H1": "违反操作规程", "H2": "违规指挥决策", "H3": "风险辨识失败", "H4": "应急行为失当",
    "C1": "救援响应迟缓", "C2": "信息迟报瞒报",
}
LAYERS = [
    ("M", "管理根基", [f"M{i}" for i in range(1, 6)]),
    ("F", "防线失效", [f"F{i}" for i in range(1, 5)]),
    ("T", "触发条件", [f"T{i}" for i in range(1, 5)]),
    ("H", "人的行为", [f"H{i}" for i in range(1, 5)]),
    ("C", "后果放大", ["C1", "C2"]),
]
METRICS = [
    ("F1", "F1 分数"),
    ("R", "召回率"),
    ("P", "精确率"),
    ("Acc", "准确率"),
]
AVG_PLUS_MIN = 5


def load_expert_gt(path: Path) -> dict[str, set[str]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema") != "caics-expert-review-v1":
        sys.exit(f"{path} 不是审核导出文件（schema={obj.get('schema')!r}）")
    gt = {}
    for it in obj.get("items") or []:
        aid = it.get("accident_id")
        if aid:
            gt[aid] = set(it.get("reviewed_codes") or [])
    return gt


def per_class_accuracy(gt: list[set], pred: list[set]) -> dict[str, float]:
    """逐类 (TP + TN) / N。"""
    n = len(gt)
    out = {}
    for c in ALL_CODES:
        tp = sum(1 for g, p in zip(gt, pred) if c in g and c in p)
        tn = sum(1 for g, p in zip(gt, pred) if c not in g and c not in p)
        out[c] = (tp + tn) / n if n else 0.0
    return out


def compute(gt: dict[str, set[str]]) -> tuple[dict, dict]:
    """返回 (cells, meta)。

    cells[(s, m)] = {
        metric: {code: value},  # F1/R/P/Acc 各自 19 类
        "ave": {metric: value}, "ave_plus": {metric: value},
        "n": int, "avg_codes": float,
    }
    """
    preds = {(s, lb): load_preds(s, lb) for s in STRATEGIES for lb in LABELS}
    ids = sorted(set(gt))
    present = [c for c in ALL_CODES if any(c in gt[i] for i in ids)]
    plus = [c for c in ALL_CODES if sum(1 for i in ids if c in gt[i]) >= AVG_PLUS_MIN]

    cells = {}
    for s in STRATEGIES:
        for lb in LABELS:
            pr = preds[(s, lb)]
            use = [i for i in ids if i in pr]
            g = [gt[i] for i in use]
            p = [{c for c in (pr[i].get("codes") or []) if c in ALL_CODES} for i in use]
            pc = per_class_metrics(g, p)
            acc = per_class_accuracy(g, p)
            cell = {
                "n": len(use),
                "avg_codes": sum(len(x) for x in p) / len(p) if p else 0.0,
                "per": {},
                "ave": {},
                "ave_plus": {},
            }
            for mk in ("F1", "R", "P"):
                cell["per"][mk] = {c: pc[c][mk] for c in ALL_CODES}
            cell["per"]["Acc"] = acc
            for mk in ("F1", "R", "P", "Acc"):
                cell["ave"][mk] = sum(cell["per"][mk][c] for c in present) / len(present)
                cell["ave_plus"][mk] = sum(cell["per"][mk][c] for c in plus) / len(plus)
            cell["n_gt"] = {c: pc[c]["n_gt"] for c in ALL_CODES}
            cells[(s, lb)] = cell

    meta = {
        "n_ids": len(ids),
        "present": present,
        "plus": plus,
        "n_gt": {c: sum(1 for i in ids if c in gt[i]) for c in ALL_CODES},
        "avg_gt_codes": sum(len(gt[i]) for i in ids) / len(ids),
    }
    return cells, meta


def rank_marks(values: list[float]) -> list[tuple[str, str]]:
    """给一列 24 个值打标记：最优加粗、次优下划线。并列同标记。"""
    uniq = sorted({round(v, 10) for v in values}, reverse=True)
    best = uniq[0] if uniq else None
    second = uniq[1] if len(uniq) > 1 else None
    out = []
    for v in values:
        r = round(v, 10)
        if r == best:
            out.append(("**", "**"))
        elif second is not None and r == second:
            out.append(("<u>", "</u>"))
        else:
            out.append(("", ""))
    return out


def fmt(v: float) -> str:
    return f"{v * 100:.2f}"


def render_md(cells: dict, meta: dict, gt_path: Path) -> str:
    cols = ALL_CODES + ["Ave", "Ave+"]
    # 与渲染顺序一致：模型为块，块内按策略排列
    samples = [(s, lb) for lb in LABELS for s in STRATEGIES]

    def value(s, lb, col, mk):
        c = cells[(s, lb)]
        if col == "Ave":
            return c["ave"][mk]
        if col == "Ave+":
            return c["ave_plus"][mk]
        return c["per"][mk][col]

    out: list[str] = []
    out.append("# 专家审核 GT 下的六策略 × 四模型致因编码结果")
    out.append("")
    out.append(f"- GT：`{gt_path.name}`（{meta['n_ids']} 条，"
               f"平均 {meta['avg_gt_codes']:.2f} 编码/条）")
    out.append(f"- Ave：{len(meta['present'])} 类宏平均；"
               f"Ave+：GT 正例 ≥{AVG_PLUS_MIN} 的 {len(meta['plus'])} 类"
               f"（排除 {', '.join(c for c in ALL_CODES if c not in meta['plus'])}）")
    out.append("- 准确率 = 逐类 (TP+TN)/N；数值为百分比（%），每列最优加粗、次优加下划线")
    out.append("")

    # 总览
    out.append("## 总览：Ave+ F1（%）")
    out.append("")
    out.append("| 策略 \\ 模型 | " + " | ".join(MODEL_LABELS[m] for m in LABELS) + " | 均值 |")
    out.append("|---|" + "---:|" * (len(LABELS) + 1))
    for s in STRATEGIES:
        vals = [cells[(s, lb)]["ave_plus"]["F1"] for lb in LABELS]
        out.append(f"| {STRATEGY_LABELS[s]} | " + " | ".join(fmt(v) for v in vals)
                   + f" | {fmt(sum(vals) / len(vals))} |")
    out.append("")

    # 四张表
    for mk, title in METRICS:
        out.append(f"## 表 {metrics_index(mk)}　{title}（%）")
        out.append("")
        # 先按列算标记
        marks = {}
        for col in cols:
            vals = [value(s, lb, col, mk) for s, lb in samples]
            marks[col] = rank_marks(vals)
        header = "| 模型 | 策略 | " + " | ".join(cols) + " |"
        align = "|---|---|" + "---:|" * len(cols)
        out.append(header)
        out.append(align)
        i = 0
        for lb in LABELS:
            for s in STRATEGIES:
                name = MODEL_LABELS[lb] if s == STRATEGIES[0] else ""
                cells_txt = []
                for col in cols:
                    v = value(s, lb, col, mk)
                    o, c = marks[col][i]
                    txt = fmt(v)
                    cells_txt.append(f"{o}{txt}{c}" if o else txt)
                out.append(f"| {name} | {STRATEGY_LABELS[s]} | " + " | ".join(cells_txt) + " |")
                i += 1
        out.append("")

    # 类别对照
    out.append("## 附：19 类编码对照")
    out.append("")
    out.append("| 层 | 编码 | 名称 | GT 正例 |")
    out.append("|---|---|---|---:|")
    for key, name, codes in LAYERS:
        for j, c in enumerate(codes):
            layer = f"{key} 层 · {name}" if j == 0 else ""
            out.append(f"| {layer} | {c} | {CODE_NAMES[c]} | {meta['n_gt'][c]} |")
    out.append("")
    return "\n".join(out) + "\n"


def metrics_index(mk: str) -> int:
    return [m for m, _ in METRICS].index(mk) + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(DEFAULT_GT))
    ap.add_argument("--md", default=str(DEFAULT_MD))
    args = ap.parse_args()

    gt_path = Path(args.gt)
    gt = load_expert_gt(gt_path)
    print(f"GT：{gt_path.name} | {len(gt)} 条 | 平均 "
          f"{sum(len(v) for v in gt.values()) / len(gt):.2f} 编码/条")

    cells, meta = compute(gt)
    for s in STRATEGIES:
        for lb in LABELS:
            c = cells[(s, lb)]
            print(f"  {s}′ × {lb:8s} n={c['n']}  Ave+ F1={c['ave_plus']['F1']*100:.2f}%")

    md = render_md(cells, meta, gt_path)
    out = Path(args.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
