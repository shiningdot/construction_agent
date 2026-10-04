"""E2: Interaction Effect Analysis — heatmap + Friedman test + ranking stability."""

from __future__ import annotations

import json, sys
from pathlib import Path
from collections import defaultdict

import numpy as np
from scipy import stats

E2_OUTPUT_DIR = Path("data/experiments/e2/results")


def load_all_results() -> dict:
    """Load all {model}_{strategy}.json files."""
    results = {}
    for f in sorted(E2_OUTPUT_DIR.glob("*.json")):
        key = f.stem  # e.g. "DeepSeek V4_V2"
        data = json.loads(f.read_text(encoding="utf-8"))
        results[key] = data
    return results


def build_matrix(results: dict, metric: str) -> dict:
    """Build a model×strategy matrix for a given metric.

    metric: 'basic_acc', 'cause_f1', 'overall'
    """
    models = set()
    strategies = set()
    values = {}

    for key, data in results.items():
        model, strategy = key.rsplit("_", 1)
        models.add(model)
        strategies.add(strategy)
        m = data.get("metrics", {})

        if metric == "basic_acc":
            val = m.get("basic_fields", {}).get("avg_accuracy", 0) / 100.0
        elif metric == "cause_f1":
            val = m.get("cause_dim_f1", {}).get("avg", {}).get("f1", 0) / 100.0
        elif metric == "overall":
            val = m.get("overall_score", 0) / 100.0
        else:
            val = 0
        values[(model, strategy)] = val

    return {
        "models": sorted(models),
        "strategies": sorted(strategies),
        "values": values,
    }


def print_heatmap(matrix: dict, title: str):
    """Print ASCII heatmap."""
    models = matrix["models"]
    strategies = matrix["strategies"]
    values = matrix["values"]

    # Find max per row
    row_best = {}
    for model in models:
        best_val = -1
        best_strat = ""
        for strat in strategies:
            v = values.get((model, strat), 0)
            if v > best_val:
                best_val = v
                best_strat = strat
        row_best[model] = best_strat

    # Find max per column
    col_best = {}
    for strat in strategies:
        best_val = -1
        best_model = ""
        for model in models:
            v = values.get((model, strat), 0)
            if v > best_val:
                best_val = v
                best_model = model
        col_best[strat] = best_model

    print(f"\n{'='*70}")
    print(f"📊 {title}")
    print(f"{'='*70}")

    # Header
    header = f"{'Model':<18}"
    for s in strategies:
        header += f" {s:>10}"
    header += "  Best"
    print(header)
    print("-" * (18 + 11 * len(strategies) + 6))

    for model in models:
        line = f"{model:<18}"
        best = row_best[model]
        for strat in strategies:
            v = values.get((model, strat), 0)
            marker = " *" if strat == best else ""
            line += f" {v:>8.3f}{marker}"
        line += f"  → {best}"
        print(line)

    print(f"\n  * = 该模型的最优策略")
    print(f"  列最优: {col_best}")

    # Check interaction
    all_same = len(set(row_best.values())) == 1
    print(f"\n  交互效应: {'❌ 无（所有模型最优策略相同）' if all_same else '✅ 存在（不同模型最优策略不同）'}")


def friedman_test(results: dict, metric: str = "basic_acc"):
    """Friedman test per model: do strategies differ significantly?"""
    print(f"\n{'='*70}")
    print(f"📊 Friedman 检验: 策略间是否存在显著差异")
    print(f"{'='*70}")

    # Build per-model per-sample data
    # We need predictions per sample — only available in full results
    for key, data in sorted(results.items()):
        model, strategy = key.rsplit("_", 1)
        m = data.get("metrics", {})
        bf = m.get("basic_fields", {})
        cf = m.get("cause_dim_f1", {})
        print(f"\n  {key}:")
        print(f"    Basic Acc: {bf.get('avg_accuracy', 0):.3f}")
        print(f"    Cause F1:  {cf.get('avg', {}).get('f1', 0):.3f}")
        print(f"    Overall:   {m.get('overall_score', 0):.3f}")


def print_summary_table(results: dict):
    """Print a comprehensive summary table."""
    strategies = set()
    models = set()
    for key in results:
        model, strat = key.rsplit("_", 1)
        models.add(model)
        strategies.add(strat)

    print(f"\n{'='*70}")
    print(f"📊 E2 综合评分表 (Overall Score)")
    print(f"{'='*70}")
    header = "| 模型 |"
    sep = "|------|"
    for s in sorted(strategies):
        header += f" {s} |"
        sep += ":--:|"
    header += " 最优 |"
    sep += "---|"
    print(header)
    print(sep)

    for model in sorted(models):
        row = f"| {model} |"
        best_val = -1
        best_strat = ""
        for s in sorted(strategies):
            key = f"{model}_{s}"
            if key in results:
                v = results[key]["metrics"].get("overall_score", 0) / 100.0
                row += f" {v:.3f} |"
                if v > best_val:
                    best_val = v
                    best_strat = s
            else:
                row += " — |"
        row += f" **{best_strat}** ({best_val:.3f}) |"
        print(row)

    print(f"\n💡 交互效应判断:")
    all_same = all(
        max(
            (results.get(f"{m}_{s}", {}).get("metrics", {}).get("overall_score", 0) for s in strategies),
            default=0,
        )
        for m in models
    )
    # Actually need the proper check
    best_per_model = {}
    for model in sorted(models):
        best_s = ""
        best_v = -1
        for s in sorted(strategies):
            key = f"{model}_{s}"
            if key in results:
                v = results[key]["metrics"].get("overall_score", 0)
                if v > best_v:
                    best_v = v
                    best_s = s
        best_per_model[model] = best_s

    unique_bests = len(set(best_per_model.values()))
    if unique_bests == 1:
        print(f"   所有模型最优策略相同 → 无交互效应")
    else:
        print(f"   不同模型有不同的最优策略 ({unique_bests}种) → 交互效应存在")


if __name__ == "__main__":
    results = load_all_results()
    if not results:
        print(f"❌ 无结果文件，请先运行 runner.py")
        print(f"   期望路径: {E2_OUTPUT_DIR}/*.json")
        sys.exit(1)

    print(f"📂 加载 {len(results)} 个实验结果")
    for key in sorted(results.keys()):
        print(f"   {key}")

    # Heatmaps
    for metric, title in [
        ("basic_acc", "核心字段 Accuracy"),
        ("cause_f1", "致因维度 F1"),
        ("overall", "综合得分"),
    ]:
        m = build_matrix(results, metric)
        print_heatmap(m, title)

    # Summary
    print_summary_table(results)
    friedman_test(results)
