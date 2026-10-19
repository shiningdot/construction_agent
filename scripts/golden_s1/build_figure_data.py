"""构建论文核心三图的中间数据。

本脚本只做三件事：
1. 从专家 GT 与 24 份 results.jsonl 重算「6 策略 × 4 模型」的指标；
2. 与既有《专家GT_指标表.md》及 eval_expert_gt.compute() 对账；
3. 输出 figure_data.json，供 plot_paper_figures.py 使用。

数据口径（与仓库既有脚本一致）：
- 致因指标：逐类 P/R/F1；Ave = 19 类宏平均；Ave+ = GT 正例 ≥5 的 18 类；
- 准确率 = (TP + TN) / N；
- 基础特征只评 13 个字段，数值字段允许 ±0.5 容差；
- 旧 GT（cause_gt / 最终说明.md）不参与本脚本。

用法：
    python scripts/golden_s1/build_figure_data.py --strict
    python scripts/golden_s1/build_figure_data.py \
        --data-root /path/to/golden_300 --out-dir /tmp/figures --strict
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from eval_matrix import (  # noqa: E402
    ALL_CODES,
    BASIC_13,
    LABELS,
    STRATEGIES,
    basic_acc,
    micro_f1,
    per_class_metrics,
)
from eval_expert_gt import LAYERS, load_expert_gt, per_class_accuracy  # noqa: E402

EXPERT_NAME = "CAICS致因审核_孙天浩_2026-10-18.json"
ATOL_PP = 0.011  # Markdown 保留 2 位小数，留出解析与浮点余量

# 图内使用的英文标签（类别名为初译，可在审校后直接替换）
STRATEGY_DISPLAY = {
    "S0": "S0' Zero-shot",
    "S1": "S1' Few-shot",
    "S2": "S2' Layer-wise",
    "S3": "S3' Plan-and-solve",
    "S4": "S4' CAICS-CoT",
    "S5": "S5' CAICS-CoT+",
}
MODEL_DISPLAY = {
    "glm5.2": "GLM-5.2",
    "flash": "DeepSeek V4 Flash",
    "hy3": "Hunyuan hy3",
    "pro": "DeepSeek V4 Pro",
}
CLASS_DISPLAY = {
    "M1": "On-site supervision",
    "M2": "Hazard inspection",
    "M3": "Safety training",
    "M4": "Rules/procedures",
    "M5": "Market compliance",
    "F1": "Edge/opening protection",
    "F2": "Support stability",
    "F3": "Safety devices",
    "F4": "PPE use",
    "T1": "Equipment failure",
    "T2": "Material state",
    "T3": "Geological anomaly",
    "T4": "Weather/hydrology",
    "H1": "Procedure violation",
    "H2": "Command decision",
    "H3": "Risk identification",
    "H4": "Emergency response",
    "C1": "Rescue response",
    "C2": "Information reporting",
}
LAYER_DISPLAY = {
    "M": "Management",
    "F": "Defense",
    "T": "Trigger",
    "H": "Human",
    "C": "Consequence",
}
METRIC_PATHS = {
    "ave_plus_F1": ("ave_plus", "F1"),
    "ave_plus_R": ("ave_plus", "R"),
    "ave_plus_P": ("ave_plus", "P"),
    "micro_F1": ("micro", "F1"),
    "exact": ("exact",),
    "basic_acc": ("basic", "acc"),
}
MODEL_MAP = {
    "GLM-5.2": "glm5.2",
    "DeepSeek Flash": "flash",
    "混元 hy3": "hy3",
    "DeepSeek Pro": "pro",
}


@dataclass(frozen=True)
class DataPaths:
    """一次构建所需的所有路径。"""

    mode: str  # worktree | snapshot
    root: Path
    expert_gt: Path
    basic_gt: Path
    results_root: Path


def resolve_data_paths(data_root: str | Path | None = None) -> DataPaths:
    """识别工作树布局或 golden_300 只读快照布局。"""
    candidates: list[Path] = []
    if data_root is not None:
        candidates.append(Path(data_root).expanduser())
    else:
        candidates.extend(
            [
                ROOT / "experiments/golden_standard",
                ROOT.parents[2] / "experiments/experiment_6strategies/golden_300",
                Path.cwd() / "experiments/golden_standard",
                Path.cwd() / "golden_300",
            ]
        )

    for candidate in candidates:
        root = candidate.resolve()
        # 工作树布局：GT 在根目录，预测在 strategies/ 下
        if (root / EXPERT_NAME).is_file():
            return DataPaths(
                mode="worktree",
                root=root,
                expert_gt=root / EXPERT_NAME,
                basic_gt=root / "gt_manual/gt_draft.jsonl",
                results_root=root / "strategies",
            )
        # 快照布局：GT 在 gt/ 下，预测在 results/ 下
        if (root / "gt/expert_review_300.json").is_file():
            return DataPaths(
                mode="snapshot",
                root=root,
                expert_gt=root / "gt/expert_review_300.json",
                basic_gt=root / "gt/gt_draft_300.jsonl",
                results_root=root / "results",
            )

    raise SystemExit(
        "无法识别数据根目录。请用 --data-root 指定 "
        "experiments/golden_standard 或 golden_300/"
    )


def load_basic_gt_at(path: Path) -> dict[str, dict]:
    """读取 13 个基础特征的真值。"""
    out: dict[str, dict] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = row.get("id") or row.get("accident_id")
            basic = row.get("basic_features")
            if rid and isinstance(basic, dict):
                out[str(rid)] = basic
    return out


def load_preds_at(results_root: Path, strategy: str, model: str) -> tuple[dict[str, dict], dict]:
    """按 id 去重、同 id 取最后一条成功行；返回预测与加载统计。

    与 eval_matrix.load_preds() 语义一致，但接受任意根目录，便于读取快照。
    """
    path = results_root / strategy / model / "results.jsonl"
    stats = {
        "path": str(path),
        "resolved": None,
        "lines": 0,
        "valid_lines": 0,
        "bad_lines": 0,
        "ok": 0,
        "missing": False,
    }
    if not path.is_file():
        stats["missing"] = True
        return {}, stats

    stats["resolved"] = str(path.resolve())
    ok: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            stats["lines"] += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                stats["bad_lines"] += 1
                continue
            # 失败行直接跳过；即使同一 id 后面还有成功行，也不会被它覆盖
            if row.get("error") or row.get("parse_error") or row.get("schema_errors"):
                stats["bad_lines"] += 1
                continue
            rid = row.get("id")
            if not rid:
                stats["bad_lines"] += 1
                continue
            stats["valid_lines"] += 1
            ok[str(rid)] = row.get("parsed") or {}

    stats["ok"] = len(ok)
    return ok, stats


def _mean_sem(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    sem = statistics.stdev(values) / math.sqrt(n) if n > 1 else 0.0
    return mean, sem


def _get_metric(cell: dict, path: tuple[str, ...]) -> float:
    value = cell
    for key in path:
        value = value[key]
    return float(value)


def compute_all(paths: DataPaths) -> dict:
    """重算 24 格指标，返回完整的中间数据结构。"""
    gt = load_expert_gt(paths.expert_gt)
    ids = sorted(gt)
    if not ids:
        raise SystemExit(f"专家 GT 为空：{paths.expert_gt}")

    n_gt = {c: sum(1 for i in ids if c in gt[i]) for c in ALL_CODES}
    present = [c for c in ALL_CODES if n_gt[c] > 0]
    plus = [c for c in ALL_CODES if n_gt[c] >= 5]
    basic_gt = load_basic_gt_at(paths.basic_gt)

    cells: dict[str, dict] = {}
    for strategy in STRATEGIES:
        cells[strategy] = {}
        for model in LABELS:
            preds, stats = load_preds_at(paths.results_root, strategy, model)
            use = [i for i in ids if i in preds]
            g = [gt[i] for i in use]
            p = [
                {c for c in (preds[i].get("codes") or []) if c in ALL_CODES}
                for i in use
            ]

            per_class = per_class_metrics(g, p)
            acc = per_class_accuracy(g, p)

            per = {mk: {c: per_class[c][mk] for c in ALL_CODES} for mk in ("F1", "R", "P")}
            per["Acc"] = acc

            ave = {
                mk: sum(per[mk][c] for c in present) / len(present)
                for mk in ("F1", "R", "P", "Acc")
            }
            ave_plus = {
                mk: sum(per[mk][c] for c in plus) / len(plus)
                for mk in ("F1", "R", "P", "Acc")
            }

            micro_p, micro_r, micro_f1_value = micro_f1(g, p)
            exact = (
                sum(1 for a, b in zip(g, p) if a == b) / len(use) if use else 0.0
            )

            basic = basic_acc(
                {i: basic_gt[i] for i in use if i in basic_gt},
                {i: preds[i] for i in use},
            )
            hit = sum(h for h, _ in basic.values())
            total = sum(t for _, t in basic.values())

            cells[strategy][model] = {
                "n": len(use),
                "n_missing": len(ids) - len(use),
                "avg_codes": sum(len(x) for x in p) / len(p) if p else 0.0,
                "per": per,
                "ave": ave,
                "ave_plus": ave_plus,
                "micro": {"P": micro_p, "R": micro_r, "F1": micro_f1_value},
                "exact": exact,
                "basic": {
                    "acc": hit / total if total else 0.0,
                    "hit": hit,
                    "total": total,
                    "per_field": {
                        f: {"hit": h, "total": t} for f, (h, t) in basic.items()
                    },
                },
                "load": stats,
            }

    # 策略级汇总：4 个模型的均值与 SEM
    strategy_summary: dict[str, dict] = {}
    for strategy in STRATEGIES:
        mean: dict[str, float] = {}
        sem: dict[str, float] = {}
        for key, path in METRIC_PATHS.items():
            values = [_get_metric(cells[strategy][m], path) for m in LABELS]
            mean[key], sem[key] = _mean_sem(values)
        strategy_summary[strategy] = {"mean": mean, "sem": sem, "n_models": len(LABELS)}

    # 逐类 F1：4 模型均值、S0–S3 基线、S4/S5 相对基线的变化
    per_class_out: dict[str, dict] = {
        "mean_F1": {},
        "baseline_S0_S3_F1": {},
        "delta_S4_S5_vs_baseline": {},
    }
    baseline_strategies = ["S0", "S1", "S2", "S3"]
    for code in ALL_CODES:
        per_class_out["mean_F1"][code] = {
            strategy: statistics.mean(
                cells[strategy][m]["per"]["F1"][code] for m in LABELS
            )
            for strategy in STRATEGIES
        }
        baseline = statistics.mean(
            cells[strategy][m]["per"]["F1"][code]
            for strategy in baseline_strategies
            for m in LABELS
        )
        per_class_out["baseline_S0_S3_F1"][code] = baseline
        per_class_out["delta_S4_S5_vs_baseline"][code] = {
            strategy: statistics.mean(
                cells[strategy][m]["per"]["F1"][code] for m in LABELS
            )
            - baseline
            for strategy in ("S4", "S5")
        }

    gt_info = {
        "n_ids": len(ids),
        "avg_codes": sum(len(gt[i]) for i in ids) / len(ids),
        "present": present,
        "plus": plus,
        "excluded_from_ave_plus": [c for c in ALL_CODES if c not in plus],
        "n_gt": n_gt,
    }

    return {
        "cells": cells,
        "strategy_summary": strategy_summary,
        "per_class": per_class_out,
        "gt": gt_info,
        "_internal": {"gt_sets": gt, "ids": ids, "basic_gt": basic_gt},
    }


def _clean_md_cell(text: str) -> str:
    return (
        text.replace("**", "")
        .replace("<u>", "")
        .replace("</u>", "")
        .strip()
    )


def _split_md_row(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def _strategy_key(label: str) -> str | None:
    """把 'S0′ 零样本' 映射回 'S0'。"""
    for strategy in STRATEGIES:
        if label.startswith(strategy + "′") or label.startswith(strategy + "'"):
            return strategy
    return None


def _next_table_start(lines: list[str], start: int) -> int:
    """从 start 起找到第一行以 | 开头的表头。"""
    i = start
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        i += 1
    return i


def parse_reference_md(path: Path) -> dict:
    """解析《专家GT_指标表.md》的总览表与表 1–4，用于对账。

    返回值：
        {"overview": {strategy: [4 模型 + 均值]},
         "tables": {(metric, strategy, model): (Ave, Ave+)}}
    """
    lines = path.read_text(encoding="utf-8").splitlines()

    # 总览表
    overview: dict[str, list[float]] = {}
    try:
        idx = next(i for i, line in enumerate(lines) if line.startswith("## 总览"))
    except StopIteration:
        idx = -1
    if idx >= 0:
        header = _next_table_start(lines, idx)
        for line in lines[header + 2 :]:
            if not line.lstrip().startswith("|"):
                break
            cols = _split_md_row(line)
            if len(cols) < 6:
                continue
            strategy = _strategy_key(cols[0])
            if not strategy:
                continue
            try:
                overview[strategy] = [float(_clean_md_cell(x)) for x in cols[1:6]]
            except ValueError:
                continue

    # 表 1–4
    tables: dict[tuple[str, str, str], tuple[float, float]] = {}
    for metric_index, metric in enumerate(("F1", "R", "P", "Acc"), start=1):
        marker = f"## 表 {metric_index}"
        try:
            idx = next(i for i, line in enumerate(lines) if line.startswith(marker))
        except StopIteration:
            continue
        header_idx = _next_table_start(lines, idx)
        if header_idx >= len(lines):
            continue
        header = _split_md_row(lines[header_idx])
        if "Ave" not in header or "Ave+" not in header:
            continue
        ave_idx = header.index("Ave")
        ave_plus_idx = header.index("Ave+")
        current_model: str | None = None
        for line in lines[header_idx + 2 :]:
            if not line.lstrip().startswith("|"):
                break
            cols = _split_md_row(line)
            if len(cols) <= max(ave_idx, ave_plus_idx):
                continue
            if cols[0]:
                current_model = MODEL_MAP.get(cols[0])
            strategy = _strategy_key(cols[1]) if len(cols) > 1 else None
            if not current_model or not strategy:
                continue
            try:
                ave = float(_clean_md_cell(cols[ave_idx]))
                ave_plus = float(_clean_md_cell(cols[ave_plus_idx]))
            except ValueError:
                continue
            tables[(metric, strategy, current_model)] = (ave, ave_plus)

    return {"overview": overview, "tables": tables}


def check_reference_md(
    md_path: Path,
    cells: dict,
    strategy_summary: dict,
) -> dict:
    """把重算结果与 Markdown 表逐项比较，返回校验摘要。"""
    if not md_path.is_file():
        return {"performed": False, "reason": f"文件不存在：{md_path}"}

    try:
        reference = parse_reference_md(md_path)
    except Exception as exc:  # 解析失败不应阻断非 strict 模式
        return {"performed": False, "reason": f"解析失败：{exc}"}

    diffs: list[dict] = []
    max_diff = 0.0

    def compare(label: str, got: float, ref: float) -> None:
        nonlocal max_diff
        diff = abs(got - ref)
        max_diff = max(max_diff, diff)
        if diff > ATOL_PP:
            diffs.append({"item": label, "computed": round(got, 4), "reference": ref,
                          "abs_diff_pp": round(diff, 4)})

    for strategy, values in reference["overview"].items():
        if strategy not in cells or len(values) < 5:
            continue
        for index, model in enumerate(LABELS):
            compare(
                f"overview.{strategy}.{model}",
                cells[strategy][model]["ave_plus"]["F1"] * 100,
                values[index],
            )
        compare(
            f"overview.{strategy}.mean",
            strategy_summary[strategy]["mean"]["ave_plus_F1"] * 100,
            values[4],
        )

    for (metric, strategy, model), (ave, ave_plus) in reference["tables"].items():
        if strategy not in cells or model not in cells[strategy]:
            continue
        compare(f"table.{metric}.{strategy}.{model}.Ave",
                cells[strategy][model]["ave"][metric] * 100, ave)
        compare(f"table.{metric}.{strategy}.{model}.Ave+",
                cells[strategy][model]["ave_plus"][metric] * 100, ave_plus)

    checks = len(reference["overview"]) * 5 + len(reference["tables"]) * 2
    return {
        "performed": True,
        "path": str(md_path),
        "checks": checks,
        "max_abs_diff_pp": round(max_diff, 6),
        "atol_pp": ATOL_PP,
        "ok": not diffs,
        "failures": diffs[:20],
    }


def crosscheck_eval_expert_gt(gt: dict[str, set[str]], paths: DataPaths, cells: dict) -> dict:
    """在工作树模式下与 eval_expert_gt.compute() 交叉校验。

    该函数内部走 eval_matrix 的模块级路径，因此只在数据根就是当前
    工作树的 experiments/golden_standard 时执行。
    """
    expected_root = (ROOT / "experiments/golden_standard").resolve()
    if paths.mode != "worktree" or paths.root != expected_root:
        return {
            "performed": False,
            "reason": "仅在当前工作树的 experiments/golden_standard 下执行",
        }

    import eval_expert_gt as expert_eval

    reference_cells, _ = expert_eval.compute(gt)
    diffs: list[float] = []
    for strategy in STRATEGIES:
        for model in LABELS:
            computed = cells[strategy][model]
            reference = reference_cells[(strategy, model)]
            for key in ("ave", "ave_plus"):
                for metric in ("F1", "R", "P", "Acc"):
                    diffs.append(abs(computed[key][metric] - reference[key][metric]))
            for metric in ("F1", "R", "P", "Acc"):
                for code in ALL_CODES:
                    diffs.append(
                        abs(computed["per"][metric][code] - reference["per"][metric][code])
                    )

    max_diff = max(diffs, default=0.0)
    return {
        "performed": True,
        "max_abs_diff": max_diff,
        "ok": max_diff < 1e-12,
    }


def md5_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_provenance(paths: DataPaths, include_md5: bool) -> dict:
    provenance = {
        "mode": paths.mode,
        "root": str(paths.root),
        "expert_gt": str(paths.expert_gt),
        "basic_gt": str(paths.basic_gt),
        "results_root": str(paths.results_root),
    }
    if include_md5:
        provenance["md5"] = {
            "expert_gt": md5_file(paths.expert_gt),
            "basic_gt": md5_file(paths.basic_gt),
            "results": {
                f"{strategy}/{model}": md5_file(
                    paths.results_root / strategy / model / "results.jsonl"
                )
                for strategy in STRATEGIES
                for model in LABELS
            },
        }
    return provenance


def _digest_payload(doc: dict) -> str:
    """计算稳定 digest；排除 load 中的路径，保证工作树/快照可比。"""
    normalized_cells = {
        strategy: {
            model: {key: value for key, value in cell.items() if key != "load"}
            for model, cell in models.items()
        }
        for strategy, models in doc["cells"].items()
    }
    payload = {
        "gt": doc["gt"],
        "cells": normalized_cells,
        "strategy_summary": doc["strategy_summary"],
        "per_class": doc["per_class"],
    }
    raw = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def coverage_checks(doc: dict) -> dict:
    cells = doc["cells"]
    cell_counts = {f"{s}/{m}": cells[s][m]["n"] for s in STRATEGIES for m in LABELS}
    return {
        "n_ids": doc["gt"]["n_ids"],
        "n_present": len(doc["gt"]["present"]),
        "n_plus": len(doc["gt"]["plus"]),
        "excluded_from_ave_plus": doc["gt"]["excluded_from_ave_plus"],
        "n_cells": len(STRATEGIES) * len(LABELS),
        "all_cells_n300": all(v == 300 for v in cell_counts.values()),
        "cell_counts": cell_counts,
    }


def environment_info() -> dict:
    env = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "executable": sys.executable,
    }
    for package in ("numpy", "matplotlib"):
        try:
            env[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            env[package] = None
    return env


def main() -> None:
    parser = argparse.ArgumentParser(description="构建论文三图的中间数据")
    parser.add_argument("--data-root", default=None, help="数据根目录；缺省自动探测")
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "experiments/golden_standard/figures"),
        help="输出目录",
    )
    parser.add_argument("--data-json", default=None, help="中间 JSON 路径")
    parser.add_argument("--check-md", default=None, help="用于对账的指标表 Markdown")
    parser.add_argument("--strict", action="store_true", help="任一校验失败即非零退出")
    parser.add_argument("--no-md5", action="store_true", help="跳过结果文件 md5")
    parser.add_argument(
        "--allow-snapshot-write",
        action="store_true",
        help="允许向只读快照目录写文件",
    )
    args = parser.parse_args()

    paths = resolve_data_paths(args.data_root)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if (
        paths.mode == "snapshot"
        and out_dir.is_relative_to(paths.root)
        and not args.allow_snapshot_write
    ):
        raise SystemExit("拒绝写入只读快照目录，请用 --out-dir 指定其他位置")

    data_json = (
        Path(args.data_json).expanduser().resolve()
        if args.data_json
        else out_dir / "figure_data.json"
    )

    print(f"数据模式：{paths.mode}")
    print(f"数据根目录：{paths.root}")
    print(f"专家 GT：{paths.expert_gt.name}")

    computed = compute_all(paths)
    cells = computed["cells"]
    strategy_summary = computed["strategy_summary"]
    per_class = computed["per_class"]
    gt_info = computed["gt"]
    gt_sets = computed["_internal"]["gt_sets"]

    if args.check_md:
        md_path = Path(args.check_md).expanduser().resolve()
    elif paths.mode == "worktree":
        md_path = paths.root / "strategies/专家GT_指标表.md"
    else:
        md_path = paths.root / "strategies/专家GT_指标表.md"  # 快照通常没有，会记录 skipped

    reference_check = check_reference_md(md_path, cells, strategy_summary)
    crosscheck = crosscheck_eval_expert_gt(gt_sets, paths, cells)

    doc = {
        "schema": "caics-paper-figures-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": environment_info(),
        "provenance": build_provenance(paths, include_md5=not args.no_md5),
        "labels": {
            "strategies": STRATEGIES,
            "models": LABELS,
            "strategy_display": STRATEGY_DISPLAY,
            "model_display": MODEL_DISPLAY,
            "class_display": CLASS_DISPLAY,
            "layers": [
                {
                    "key": key,
                    "name": LAYER_DISPLAY.get(key, name),
                    "native_name": name,
                    "codes": codes,
                }
                for key, name, codes in LAYERS
            ],
            "basic_fields": BASIC_13,
        },
        "gt": gt_info,
        "cells": cells,
        "strategy_summary": strategy_summary,
        "per_class": per_class,
    }

    coverage = coverage_checks(doc)
    digest = _digest_payload(doc)
    doc["checks"] = {
        "coverage": coverage,
        "reference_md": reference_check,
        "eval_expert_gt_crosscheck": crosscheck,
        "digest_cells_sha256": digest,
    }

    data_json.parent.mkdir(parents=True, exist_ok=True)
    with data_json.open("w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    # 控制台摘要
    print(
        f"GT：{gt_info['n_ids']} 条 | 平均 {gt_info['avg_codes']:.2f} 编码/条 | "
        f"present={len(gt_info['present'])} | plus={len(gt_info['plus'])}"
    )
    for strategy in STRATEGIES:
        row = "  ".join(
            f"{model}:{cells[strategy][model]['ave_plus']['F1'] * 100:5.2f}"
            for model in LABELS
        )
        print(f"  {strategy}′  Ave+ F1  {row}  mean="
              f"{strategy_summary[strategy]['mean']['ave_plus_F1'] * 100:5.2f}")

    print(f"覆盖率：{coverage['n_cells']} 格 | all_cells_n300={coverage['all_cells_n300']}")
    if reference_check.get("performed"):
        print(
            f"Markdown 对账：checks={reference_check['checks']} "
            f"max_diff={reference_check['max_abs_diff_pp']:.6f} pp "
            f"{'PASS' if reference_check['ok'] else 'FAIL'}"
        )
        for failure in reference_check.get("failures", [])[:5]:
            print(f"  FAIL {failure}")
    else:
        print(f"Markdown 对账：跳过（{reference_check.get('reason')}）")
    if crosscheck.get("performed"):
        print(
            f"eval_expert_gt 交叉校验：max_diff={crosscheck['max_abs_diff']:.2e} "
            f"{'PASS' if crosscheck['ok'] else 'FAIL'}"
        )
    else:
        print(f"eval_expert_gt 交叉校验：跳过（{crosscheck.get('reason')}）")
    print(f"digest={digest}")
    print(f"已写入 {data_json}")

    failures: list[str] = []
    if gt_info["n_ids"] != 300:
        failures.append(f"GT 条数 {gt_info['n_ids']} != 300")
    if len(gt_info["present"]) != 19:
        failures.append(f"present 类别数 {len(gt_info['present'])} != 19")
    if len(gt_info["plus"]) != 18:
        failures.append(f"plus 类别数 {len(gt_info['plus'])} != 18")
    if not coverage["all_cells_n300"]:
        failures.append("存在有效样本数不等于 300 的格")
    if reference_check.get("performed") and not reference_check["ok"]:
        failures.append("与 Markdown 指标表不一致")
    if crosscheck.get("performed") and not crosscheck["ok"]:
        failures.append("与 eval_expert_gt.compute() 不一致")

    if failures:
        print("\n校验失败：")
        for item in failures:
            print(f"  - {item}")
        if args.strict:
            raise SystemExit(1)
    elif args.strict:
        print("\n全部校验通过。")


if __name__ == "__main__":
    main()
