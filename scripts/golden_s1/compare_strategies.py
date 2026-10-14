"""策略 × 模型 交叉矩阵的「一致度」分析（不需要人工真值）。

输出两类东西：
1. 单格画像：每个 (策略, 模型) 的有效条数、空编码率、编码数/条、19 类包含率
2. 对照：同一模型内 策略A vs 策略B 的逐条 Jaccard 均值、完全一致率；
   以及与库内 Step2 结果的一致度（若提供 --db 路径）

路径约定（与 run_strategy.py 保持一致）：
- S1 的结果在归档目录：experiments/golden_standard/{label}/S1_golden_results.jsonl
- 其余策略：experiments/golden_standard/strategies/{策略}/{label}/results.jsonl

用法：
    python3 scripts/golden_s1/compare_strategies.py --pairs S0,S1
    python3 scripts/golden_s1/compare_strategies.py --pairs S0,S1 --md out.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "experiments/golden_standard"

LABELS = ["glm5.2", "flash", "hy3", "pro"]
ALL_CLASSES = [f"M{i}" for i in range(1, 6)] + [f"F{i}" for i in range(1, 5)] + \
              [f"T{i}" for i in range(1, 5)] + [f"H{i}" for i in range(1, 5)] + ["C1", "C2"]


def results_path(strategy: str, label: str) -> Path:
    if strategy == "S1":
        return GOLD / label / "S1_golden_results.jsonl"
    return GOLD / "strategies" / strategy / label / "results.jsonl"


def load_codes(strategy: str, label: str) -> dict[str, set[str]]:
    """返回 {事故id: 编码集合}。同 id 多行时以「最后一条成功行」为准（重跑补齐即追加）。"""
    p = results_path(strategy, label)
    if not p.exists():
        return {}
    out: dict[str, set[str]] = {}
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("error") or r.get("parse_error") or r.get("schema_errors"):
            continue                       # 失败行不覆盖已有的成功行
        parsed = r.get("parsed") or {}
        out[r["id"]] = {c for c in (parsed.get("codes") or []) if c}
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def profile(codes: dict[str, set[str]]) -> dict:
    n = len(codes)
    if not n:
        return {"n": 0}
    sizes = [len(v) for v in codes.values()]
    hit = {c: sum(1 for v in codes.values() if c in v) for c in ALL_CLASSES}
    return {
        "n": n,
        "empty": sum(1 for v in codes.values() if not v),
        "empty_rate": sum(1 for v in codes.values() if not v) / n,
        "avg_codes": sum(sizes) / n,
        "max_codes": max(sizes),
        "class_rate": {c: hit[c] / n for c in ALL_CLASSES},
        "class_hit": hit,
    }


def compare(a: dict[str, set[str]], b: dict[str, set[str]]) -> dict:
    ids = sorted(set(a) & set(b))
    if not ids:
        return {"n": 0}
    js = [jaccard(a[i], b[i]) for i in ids]
    exact = sum(1 for i in ids if a[i] == b[i])
    return {
        "n": len(ids),
        "jaccard_mean": sum(js) / len(js),
        "jaccard_med": sorted(js)[len(js) // 2],
        "exact": exact,
        "exact_rate": exact / len(ids),
        "a_only_avg": sum(len(a[i] - b[i]) for i in ids) / len(ids),
        "b_only_avg": sum(len(b[i] - a[i]) for i in ids) / len(ids),
    }


def fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="S0,S1",
                    help="要对照的两个策略，如 S0,S1")
    ap.add_argument("--labels", default=",".join(LABELS))
    ap.add_argument("--md", default="", help="把报告同时写入该 markdown 文件")
    args = ap.parse_args()

    sa, sb = [s.strip() for s in args.pairs.split(",")]
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]

    lines: list[str] = []

    def emit(s: str = ""):
        print(s)
        lines.append(s)

    data = {(s, lb): load_codes(s, lb) for s in (sa, sb) for lb in labels}

    emit(f"# 策略对照：{sa}′ vs {sb}′")
    emit()
    emit("## 1. 单格画像")
    emit()
    emit(f"| 模型 | 策略 | 有效条数 | 空编码 | 编码数/条 | 编码数上限 |")
    emit("|---|---|---:|---:|---:|---:|")
    for lb in labels:
        for s in (sa, sb):
            pr = profile(data[(s, lb)])
            if not pr["n"]:
                emit(f"| {lb} | {s}′ | 0 | - | - | - |")
                continue
            emit(f"| {lb} | {s}′ | {pr['n']} | {pr['empty']} ({fmt_pct(pr['empty_rate'])}) | "
                 f"{pr['avg_codes']:.2f} | {pr['max_codes']} |")
    emit()

    emit("## 2. 同模型内两策略一致度（逐条）")
    emit()
    emit("| 模型 | 可比条数 | Jaccard 均值 | Jaccard 中位 | 完全相同 | A独有均值 | B独有均值 |")
    emit("|---|---:|---:|---:|---:|---:|---:|")
    for lb in labels:
        cm = compare(data[(sa, lb)], data[(sb, lb)])
        if not cm["n"]:
            emit(f"| {lb} | 0 | - | - | - | - | - |")
            continue
        emit(f"| {lb} | {cm['n']} | {cm['jaccard_mean']:.3f} | {cm['jaccard_med']:.3f} | "
             f"{cm['exact']} ({fmt_pct(cm['exact_rate'])}) | {cm['a_only_avg']:.2f} | "
             f"{cm['b_only_avg']:.2f} |")
    emit()

    emit("## 3. 19 类包含率（各策略在各模型上的样本占比）")
    emit()
    emit("| 类别 | " + " | ".join(f"{lb}-{sa}′ | {lb}-{sb}′" for lb in labels) + " |")
    emit("|---|" + "---:|" * (2 * len(labels)))
    for c in ALL_CLASSES:
        cells = []
        for lb in labels:
            for s in (sa, sb):
                pr = profile(data[(s, lb)])
                cells.append(fmt_pct(pr["class_rate"][c]) if pr["n"] else "-")
        emit(f"| {c} | " + " | ".join(cells) + " |")
    emit()

    # 跨模型离散度：同一策略在不同模型之间差别多大（"模型侧影响"）
    emit(f"## 4. 跨模型离散度（同一策略，不同模型两两 Jaccard 均值）")
    emit()
    emit("| 策略 | 模型对 | 可比条数 | Jaccard 均值 | 完全相同 |")
    emit("|---|---|---:|---:|---:|")
    for s in (sa, sb):
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                cm = compare(data[(s, labels[i])], data[(s, labels[j])])
                if not cm["n"]:
                    continue
                emit(f"| {s}′ | {labels[i]} vs {labels[j]} | {cm['n']} | "
                     f"{cm['jaccard_mean']:.3f} | {fmt_pct(cm['exact_rate'])} |")
    emit()

    if args.md:
        Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n已写入 {args.md}")


if __name__ == "__main__":
    main()
