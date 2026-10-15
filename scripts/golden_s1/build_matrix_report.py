"""自动生成「策略 × 模型」矩阵的数值报表（不需要人工真值）。

与 compare_strategies.py 的区别：那个是任意两策略的深挖对照，这个是**全矩阵**总览，
且对**尚未跑满的格子**留空而不报错——所以任何时候都能跑，随时把报表刷新到最新。

产出：
  1. 每格画像：有效条数、空编码率、编码数/条
  2. 同模型内 策略×策略 一致度（Jaccard，取有数据的模型平均）
  3. 同策略内 模型×模型 一致度
  4. 19 类包含率（每格一列）

用法：
    python3 scripts/golden_s1/build_matrix_report.py
    python3 scripts/golden_s1/build_matrix_report.py --md out.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from compare_strategies import ALL_CLASSES, compare, load_codes, profile  # noqa: E402

STRATEGIES = ["S0", "S1", "S2", "S3", "S4", "S5"]
LABELS = ["glm5.2", "flash", "hy3", "pro"]
TARGET = 300


def fmt(x: float | None, nd: int = 3) -> str:
    return "-" if x is None else f"{x:.{nd}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    data = {(s, lb): load_codes(s, lb) for s in STRATEGIES for lb in LABELS}
    prof = {(s, lb): profile(data[(s, lb)]) for s in STRATEGIES for lb in LABELS}

    out: list[str] = []

    def emit(s: str = ""):
        print(s)
        out.append(s)

    emit("# 策略 × 模型 矩阵数值报表（自动生成，勿手工编辑）")
    emit()
    emit(f"样本 {TARGET} 条；口径统一 `codes`（CAICS 19 类）+ 31 基础特征；"
         f"`temperature=0`、`max_tokens=16384`。取数按 id 去重、同 id 取最后一条成功行。")
    emit()

    # ---------- 1. 编码数/条 ----------
    emit("## 1. 编码数/条")
    emit()
    emit("| 策略 \\ 模型 | " + " | ".join(LABELS) + " |")
    emit("|---|" + "---:|" * len(LABELS))
    for s in STRATEGIES:
        cells = []
        for lb in LABELS:
            p = prof[(s, lb)]
            cells.append(f"{p['avg_codes']:.2f}" if p["n"] >= TARGET else
                         (f"({p['n']})" if p["n"] else "-"))
        emit(f"| {s}′ | " + " | ".join(cells) + " |")
    emit()
    emit("> 括号内数字表示该格尚未跑满的当前条数。")
    emit()

    # ---------- 2. 空编码数 ----------
    emit("## 2. 空编码条数（越低越好）")
    emit()
    emit("| 策略 \\ 模型 | " + " | ".join(LABELS) + " |")
    emit("|---|" + "---:|" * len(LABELS))
    for s in STRATEGIES:
        cells = []
        for lb in LABELS:
            p = prof[(s, lb)]
            cells.append(str(p["empty"]) if p["n"] >= TARGET else
                         (f"({p['empty']})" if p["n"] else "-"))
        emit(f"| {s}′ | " + " | ".join(cells) + " |")
    emit()

    # ---------- 3. 同模型内：策略 × 策略 一致度 ----------
    emit("## 3. 同模型内 策略×策略 一致度（逐条 Jaccard 均值，跨有数据的模型取平均）")
    emit()
    emit("| | " + " | ".join(f"{s}′" for s in STRATEGIES) + " |")
    emit("|---|" + "---:|" * len(STRATEGIES))
    for a in STRATEGIES:
        row = []
        for b in STRATEGIES:
            if a == b:
                row.append("—")
                continue
            vals = []
            for lb in LABELS:
                cm = compare(data[(a, lb)], data[(b, lb)])
                if cm["n"]:
                    vals.append(cm["jaccard_mean"])
            row.append(f"{sum(vals) / len(vals):.3f}" if vals else "-")
        emit(f"| **{a}′** | " + " | ".join(row) + " |")
    emit()
    emit("> 只统计两边都跑满的模型；未跑满的格参与计算但样本不满 300，数字会随进度变化。")
    emit()

    # ---------- 4. 同策略内：模型 × 模型 一致度 ----------
    emit("## 4. 同策略内 模型×模型 一致度（逐条 Jaccard 均值）")
    emit()
    emit("| 策略 | " + " | ".join(
        f"{LABELS[i]} vs {LABELS[j]}" for i in range(len(LABELS)) for j in range(i + 1, len(LABELS))
    ) + " |")
    emit("|---|" + "---:|" * (len(LABELS) * (len(LABELS) - 1) // 2))
    for s in STRATEGIES:
        cells = []
        for i in range(len(LABELS)):
            for j in range(i + 1, len(LABELS)):
                cm = compare(data[(s, LABELS[i])], data[(s, LABELS[j])])
                cells.append(f"{cm['jaccard_mean']:.3f}" if cm["n"] else "-")
        emit(f"| {s}′ | " + " | ".join(cells) + " |")
    emit()

    # ---------- 5. 19 类包含率 ----------
    emit("## 5. 19 类包含率（样本占比）")
    emit()
    for s in STRATEGIES:
        emit(f"### {s}′")
        emit()
        emit("| 类别 | " + " | ".join(LABELS) + " |")
        emit("|---|" + "---:|" * len(LABELS))
        for c in ALL_CLASSES:
            cells = []
            for lb in LABELS:
                p = prof[(s, lb)]
                cells.append(f"{p['class_rate'][c] * 100:.1f}%" if p["n"] else "-")
            emit(f"| {c} | " + " | ".join(cells) + " |")
        emit()

    # ---------- 6. 完成度 ----------
    done = sum(1 for s in STRATEGIES for lb in LABELS if prof[(s, lb)]["n"] >= TARGET)
    emit(f"## 完成度：{done}/{len(STRATEGIES) * len(LABELS)} 格已跑满")
    emit()

    if args.md:
        Path(args.md).write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"\n已写入 {args.md}")


if __name__ == "__main__":
    main()
