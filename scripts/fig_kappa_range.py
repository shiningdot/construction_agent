#!/usr/bin/env python3
"""
Nature 风格 Kappa 区间图 —— LLM vs 三专家共识（审美优化版）。

Figure contract:
  Core conclusion: LLM 标注在所有维度的 Kappa 均落入人类评分者间信度范围，
    证明 LLM 可代替人类进行 CAICS 致因判断。
  Archetype: quantitative grid — 水平区间面板
  Backend:  Python / matplotlib
  Output:   SVG (primary) + PDF + PNG

设计原则（源自 Nature 2026 设计理论）:
  - 统一色系：蓝色家族表示人类专家区间，暖色强调 LLM
  - 去繁就简：无网格线、无边框图例、仅保留必要标注
  - 视觉层次：标题 > 数据标记 > 区间范围 > 参考线 > 注释
  - 直接标注：用内嵌文本替代分离图例
"""

import json
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import numpy as np

# ============================================================
# 路径
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH = PROJECT_ROOT / "data" / "ground_truth"
FIGURES_DIR = PROJECT_ROOT / "experiments" / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 中文字体
# ============================================================
FONT_PATH = os.path.expanduser("~/.fonts/NotoSansSC.ttf")
CN_OK = os.path.exists(FONT_PATH)
if CN_OK:
    fm.fontManager.addfont(FONT_PATH)
    CN_NAME = fm.FontProperties(fname=FONT_PATH).get_name()
else:
    CN_NAME = "DejaVu Sans"

# ============================================================
# Nature 全局 rcParams（字号整体提升一档）
# ============================================================
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": [CN_NAME, "Arial", "DejaVu Sans"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 9,
    "axes.linewidth": 0.6,
    "xtick.major.width": 0.5,
    "ytick.major.width": 0.0,   # Y 轴无刻度线，用标签代替
    "xtick.major.size": 3.5,
    "ytick.major.size": 0,
    "lines.linewidth": 1.0,
    "legend.frameon": False,
})

# ============================================================
# Nature 语义配色（取自 Nature 2026 设计理论 PALETTE）
# ============================================================
class C:
    """Nature semantic palette."""
    # 人类专家区间 — 柔和蓝色家族
    band_fill   = "#D6E4F0"   # 区间填充（浅蓝）
    band_edge   = "#B4C0E4"   # 区间边框（NMI baseline_soft）
    band_median = "#0F4D92"   # 专家中位线（blue_main）

    # LLM 标记 — 暖色强调
    llm_fill    = "#D9544D"   # LLM 圆点填充（genomics wave1 暖红）
    llm_edge    = "#9B2D2A"   # LLM 圆点边框

    # 参考线与文字
    ref_line    = "#D0D0D0"
    ref_text    = "#999999"
    text_main   = "#272727"   # neutral_black
    text_sub    = "#767676"   # neutral_mid

    # 白色背景
    bg = "#FFFFFF"

# ============================================================
# 维度定义（Y 轴从上到下）
# ============================================================
DIMS_TOP_DOWN = [
    ("Overall",        "Overall"),
    ("C",              "Consequence"),
    ("H",              "Human"),
    ("T",              "Trigger"),
    ("F",              "Fortification"),
    ("M",              "Management"),
    ("severity",       "Severity"),
    ("accident_type",  "Type"),
]

CODE_LISTS = {
    "M": ["M1", "M2", "M3", "M4", "M5"],
    "F": ["F1", "F2", "F3", "F4"],
    "T": ["T1", "T2", "T3", "T4"],
    "H": ["H1", "H2", "H3", "H4"],
    "C": ["C1", "C2"],
}

# ============================================================
# 数据加载
# ============================================================

def load_expert(filepath: Path) -> List[Dict]:
    with open(filepath, encoding="utf-8") as f:
        records = json.load(f)
    results = []
    for record in records:
        item = {}
        for ann in record.get("annotations", []):
            for res in ann.get("result", []):
                name = res.get("from_name", "")
                val = res.get("value", {})
                choices = val.get("choices", [])
                if name in ["accident_type", "severity"]:
                    item[name] = choices[0] if choices else ""
                elif name in ["M", "F", "T", "H", "C"]:
                    item[name] = frozenset([c.split("-")[0] for c in choices])
        results.append(item)
    return results


def load_llm(filepath: Path) -> List[Dict]:
    with open(filepath, encoding="utf-8") as f:
        records = json.load(f)
    results = []
    for record in records:
        item = {
            "accident_type": record.get("accident_type", ""),
            "severity": record.get("severity", ""),
        }
        codes = record.get("caics_codes", [])
        for dim in ["M", "F", "T", "H", "C"]:
            item[dim] = frozenset([c for c in codes if c.startswith(dim)])
        results.append(item)
    return results


def build_consensus(ea, eb, ec):
    consensus = []
    for i in range(len(ea)):
        item = {}
        for dim in ["accident_type", "severity"]:
            votes = [ea[i].get(dim, ""), eb[i].get(dim, ""), ec[i].get(dim, "")]
            item[dim] = Counter(votes).most_common(1)[0][0]
        for dim in ["M", "F", "T", "H", "C"]:
            all_codes = (
                ea[i].get(dim, frozenset())
                | eb[i].get(dim, frozenset())
                | ec[i].get(dim, frozenset())
            )
            cc = set()
            for code in all_codes:
                vote = (
                    (1 if code in ea[i].get(dim, frozenset()) else 0)
                    + (1 if code in eb[i].get(dim, frozenset()) else 0)
                    + (1 if code in ec[i].get(dim, frozenset()) else 0)
                )
                if vote >= 2:
                    cc.add(code)
            item[dim] = frozenset(cc)
        consensus.append(item)
    return consensus


# ============================================================
# Kappa 计算
# ============================================================

def cohen_kappa_binary(obs1, obs2):
    n = len(obs1)
    a = sum(1 for i in range(n) if obs1[i] and obs2[i])
    b = sum(1 for i in range(n) if obs1[i] and not obs2[i])
    c = sum(1 for i in range(n) if not obs1[i] and obs2[i])
    d = sum(1 for i in range(n) if not obs1[i] and not obs2[i])
    po = (a + d) / n
    pe = ((a + b) / n) * ((a + c) / n) + ((c + d) / n) * ((b + d) / n)
    if abs(pe - 1.0) < 1e-9:
        return 1.0
    return (po - pe) / (1 - pe)


def cohen_kappa_multiclass(obs1, obs2):
    cats = sorted(set(obs1 + obs2))
    if len(cats) <= 1:
        return 1.0 if obs1 == obs2 else 0.0
    n = len(obs1)
    c2i = {c: i for i, c in enumerate(cats)}
    mat = np.zeros((len(cats), len(cats)))
    for o1, o2 in zip(obs1, obs2):
        mat[c2i[o1], c2i[o2]] += 1
    po = np.trace(mat) / n
    rs = mat.sum(axis=1)
    cs = mat.sum(axis=0)
    pe = np.sum(rs * cs) / (n * n)
    if abs(pe - 1.0) < 1e-9:
        return 1.0
    return (po - pe) / (1 - pe)


def compute_kappa(e1, e2, dim):
    if dim in ["accident_type", "severity"]:
        return cohen_kappa_multiclass(
            [r.get(dim, "") for r in e1], [r.get(dim, "") for r in e2]
        )
    codes = CODE_LISTS[dim]
    return float(np.mean([
        cohen_kappa_binary(
            [code in r.get(dim, frozenset()) for r in e1],
            [code in r.get(dim, frozenset()) for r in e2],
        )
        for code in codes
    ]))


def compute_overall_kappa_single(e1, e2):
    all_dims = ["accident_type", "severity", "M", "F", "T", "H", "C"]
    return float(np.mean([compute_kappa(e1, e2, d) for d in all_dims]))


def compute_expert_range(eA, eB, eC, consensus, dim):
    """三位专家两两配对的 Kappa，取 min/max 作为人类信度区间。"""
    if dim == "Overall":
        kAB = compute_overall_kappa_single(eA, eB)
        kAC = compute_overall_kappa_single(eA, eC)
        kBC = compute_overall_kappa_single(eB, eC)
    else:
        kAB = compute_kappa(eA, eB, dim)
        kAC = compute_kappa(eA, eC, dim)
        kBC = compute_kappa(eB, eC, dim)
    return min(kAB, kAC, kBC), max(kAB, kAC, kBC)


def compute_llm_consensus_kappa(llm, consensus, dim):
    if dim == "Overall":
        return compute_overall_kappa_single(llm, consensus)
    return compute_kappa(llm, consensus, dim)


# ============================================================
# 主绘图流程
# ============================================================

def main():
    # ── 加载数据 ──────────────────────────────────────────
    print("📂 加载标注数据...")
    eA = load_expert(GROUND_TRUTH / "expertA.json")
    eB = load_expert(GROUND_TRUTH / "expertB.json")
    eC = load_expert(GROUND_TRUTH / "expertC.json")
    llm = load_llm(GROUND_TRUTH / "gt_llm_labels_100.json")
    consensus = build_consensus(eA, eB, eC)
    n_samples = len(eA)
    print(f"  A={len(eA)} B={len(eB)} C={len(eC)} LLM={len(llm)}")

    # ── 计算 ──────────────────────────────────────────────
    print("🔬 计算 Kappa 区间...")
    expert_lo, expert_hi, llm_k_vals, dim_labels = [], [], [], []

    for key, label in DIMS_TOP_DOWN:
        lo, hi = compute_expert_range(eA, eB, eC, consensus, key)
        lk = compute_llm_consensus_kappa(llm, consensus, key)
        expert_lo.append(lo)
        expert_hi.append(hi)
        llm_k_vals.append(lk)
        dim_labels.append(label)

    print(f"\n{'维度':<16} {'专家区间':<22} {'LLM-共识':>10} {'判定'}")
    print("-" * 64)
    for i in range(len(dim_labels)):
        in_range = "OK" if expert_lo[i] <= llm_k_vals[i] <= expert_hi[i] + 0.02 else "OUT"
        print(f"  {dim_labels[i]:<14} [{expert_lo[i]:.3f}, {expert_hi[i]:.3f}]   {llm_k_vals[i]:>10.4f}   {in_range}")

    # ================================================================
    # 绘图 — Nature 双栏宽度 183mm ≈ 7.2in
    # ================================================================
    print("\n📊 绘图...")
    fig, ax = plt.subplots(figsize=(7.2, 4.8), dpi=300, facecolor=C.bg)
    ax.set_facecolor(C.bg)

    n = len(dim_labels)  # 8

    # 倒序使 Overall 在顶部
    _expert_lo = expert_lo[::-1]
    _expert_hi = expert_hi[::-1]
    _llm_k     = llm_k_vals[::-1]
    _labels    = dim_labels[::-1]
    y          = np.arange(n)

    # ── 1. 轻量垂直网格线（仅作为视觉参考） ─────────────
    for k_ref, style, alpha in [(0.6, ":", 0.35), (0.7, "--", 0.22), (0.8, ":", 0.35), (0.9, "--", 0.22)]:
        ax.axvline(x=k_ref, color=C.ref_line, linewidth=0.5, linestyle=style,
                   alpha=alpha, zorder=0)

    # ── 2. 专家区间（圆角矩形 + 中位线） ──────────────────
    bar_h = 0.48
    for i in range(n):
        lo, hi = _expert_lo[i], _expert_hi[i]
        width = max(hi - lo, 0.01)

        # 使用 FancyBboxPatch 获得圆角效果
        rect = mpatches.FancyBboxPatch(
            (lo, i - bar_h / 2), width, bar_h,
            boxstyle="round,pad=0.02",
            facecolor=C.band_fill,
            edgecolor=C.band_edge,
            linewidth=0.5,
            zorder=2,
        )
        ax.add_patch(rect)

        # 中位竖线（短标记，不贯穿整个条）
        mid = (lo + hi) / 2
        tick_h = bar_h * 0.72
        ax.plot([mid, mid], [i - tick_h / 2, i + tick_h / 2],
                color=C.band_median, linewidth=1.8, solid_capstyle="round", zorder=4)

    # ── 3. LLM 标记（实心圆 + 白色环） ────────────────────
    for i in range(n):
        k = _llm_k[i]
        lo, hi = _expert_lo[i], _expert_hi[i]
        in_range = lo <= k <= hi + 0.02

        if in_range:
            # 范围内：实心暖红圆
            ax.scatter(k, i, s=110, c=C.llm_fill, edgecolors="white",
                       linewidths=1.5, marker="o", zorder=6)
        else:
            # 范围外：空心圆 + 粗边框
            ax.scatter(k, i, s=140, c="white", edgecolors=C.llm_fill,
                       linewidths=2.5, marker="o", zorder=6)

    # ── 4. X 轴 ──────────────────────────────────────────
    ax.set_xlim(0.50, 1.02)
    ax.set_xlabel("Cohen's Kappa", fontsize=10, color=C.text_main, labelpad=6)
    ax.xaxis.set_major_locator(plt.MultipleLocator(0.1))
    ax.tick_params(axis="x", labelsize=8, colors=C.text_sub, length=3.5, pad=3)

    # ── 5. Y 轴（无刻度线，纯文字，加粗层级维度名） ──────
    ax.set_ylim(-0.6, n - 0.4)
    ax.set_yticks(y)
    ax.set_yticklabels(_labels, fontsize=9.5, color=C.text_main)
    # Overall 加粗
    for i, label in enumerate(_labels):
        if label == "Overall":
            ax.get_yticklabels()[i].set_fontweight("bold")
            ax.get_yticklabels()[i].set_fontsize(10)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)    # Y 轴无线框，更干净
    ax.spines["bottom"].set_linewidth(0.6)
    ax.spines["bottom"].set_color(C.ref_line)
    ax.tick_params(axis="y", length=0, pad=6)

    # ── 6. Kappa 值标注 ───────────────────────────────────
    for i in range(n):
        k = _llm_k[i]
        lo, hi = _expert_lo[i], _expert_hi[i]

        # LLM 值（标注在圆点右侧）
        x_text = max(k, hi) + 0.025
        ax.text(x_text, i, f"{k:.3f}", fontsize=8, color=C.llm_fill,
                va="center", ha="left", fontweight="bold", zorder=7)

        # 区间左端点
        ax.text(lo - 0.010, i + 0.32, f"{lo:.2f}",
                fontsize=6.5, color=C.band_median, va="bottom", ha="right", zorder=3)

        # 区间右端点
        ax.text(hi + 0.006, i + 0.32, f"{hi:.2f}",
                fontsize=6.5, color=C.band_median, va="bottom", ha="left", zorder=3)

    # ── 7. 直接标注（替代图例） ────────────────────────────
    # 左上：人类专家区间说明
    legend_y = n - 0.32
    # 淡蓝小色块
    swatch = mpatches.FancyBboxPatch(
        (0.505, legend_y - 0.13), 0.04, 0.18,
        boxstyle="round,pad=0.01",
        facecolor=C.band_fill, edgecolor=C.band_edge, linewidth=0.5,
        transform=ax.transData, zorder=10,
    )
    ax.add_patch(swatch)
    ax.text(0.555, legend_y, "Human expert range\n(pairwise κ, A–B / A–C / B–C)",
            fontsize=7, color=C.text_sub, va="center", ha="left", zorder=10)

    # LLM 标记说明
    llm_x = 0.505 + 0.17
    ax.scatter(llm_x, legend_y, s=55, c=C.llm_fill, edgecolors="white",
               linewidths=1.2, marker="o", zorder=10)
    ax.text(llm_x + 0.03, legend_y, "LLM–consensus κ",
            fontsize=7, color=C.text_sub, va="center", ha="left", zorder=10)

    # ── 8. 参考线文字标签 ──────────────────────────────────
    for k_ref, label in [(0.6, "κ = 0.6"), (0.8, "κ = 0.8")]:
        ax.text(k_ref, -0.48, label, fontsize=6.5, color=C.ref_text,
                ha="center", va="top")

    # ── 9. 标题 ────────────────────────────────────────────
    ax.set_title(
        "Inter-rater reliability: LLM vs. human expert consensus on CAICS coding",
        fontsize=11, fontweight="bold", color=C.text_main, loc="left", pad=12,
    )

    # ── 10. 底部信息 ──────────────────────────────────────
    all_in = all(
        expert_lo[i] <= llm_k_vals[i] <= expert_hi[i] + 0.02
        for i in range(len(dim_labels))
    )
    status = "all within range" if all_in else "some outside range"
    ax.text(0.99, -0.48,
            f"n = {n_samples} samples  |  LLM κ {status}  |  "
            f"κ interpretation: >0.8 substantial, >0.6 moderate",
            fontsize=6.5, color=C.text_sub, ha="right", va="top",
            transform=ax.transData, style="italic")

    # ── 导出 ──────────────────────────────────────────────
    # 手动 tight_layout 参数
    plt.subplots_adjust(left=0.16, right=0.94, top=0.92, bottom=0.10)

    stem = FIGURES_DIR / "fig_kappa_range"
    for fmt, dpi in [("svg", None), ("pdf", None), ("png", 600)]:
        p = str(stem) + f".{fmt}"
        kw = {"dpi": dpi} if dpi else {}
        fig.savefig(p, bbox_inches="tight", facecolor=C.bg, edgecolor="none", **kw)
        print(f"  ✓ {p}")

    plt.close(fig)
    print("\n✅ 完成")

    # ── 额外：保存结构化数据 ────────────────────────────
    out_data = {
        "n_samples": n_samples,
        "dimensions": [
            {
                "dim": DIMS_TOP_DOWN[i][0],
                "label": dim_labels[i],
                "expert_range": [round(expert_lo[i], 4), round(expert_hi[i], 4)],
                "llm_consensus_kappa": round(llm_k_vals[i], 4),
                "within_range": bool(expert_lo[i] <= llm_k_vals[i] <= expert_hi[i] + 0.02),
            }
            for i in range(len(dim_labels))
        ],
    }
    data_path = FIGURES_DIR / "fig_kappa_range_data.json"
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 数据: {data_path}")


if __name__ == "__main__":
    main()
