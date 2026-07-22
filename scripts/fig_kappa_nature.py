#!/usr/bin/env python3
"""
Nature 风格 Kappa 系数对比图 —— LLM vs 三位专家共识。

Figure contract:
  Core conclusion: 优化后 LLM 标注在事故等级、事故类型及 HFACS 各层级上
    与三专家共识的 Cohen's Kappa 落在人类评分者间信度范围内。
  Archetype: quantitative grid
  Backend: Python / matplotlib
  Panels:
    a — LLM-共识 vs 三位专家两两 Kappa 分组柱状图
    b — LLM-共识 vs 人类评分者间范围（灰色带 + 散点）
    c — HFACS 各子编码 Kappa 热力图

数据来源:
  data/ground_truth/expertA.json, expertB.json, expertC.json
  data/ground_truth/gt_llm_labels_100.json
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import logging
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import matplotlib.ticker as ticker
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

# ============================================================
# 路径
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH = PROJECT_ROOT / "data" / "ground_truth"
FIGURES_DIR = PROJECT_ROOT / "experiments" / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 中文字体注册
# ============================================================
FONT_PATH = os.path.expanduser("~/.fonts/NotoSansSC.ttf")
CN_FONT_AVAILABLE = os.path.exists(FONT_PATH)
if CN_FONT_AVAILABLE:
    fm.fontManager.addfont(FONT_PATH)
    _fp = fm.FontProperties(fname=FONT_PATH)
    CN_FONT_NAME = _fp.get_name()
    CN_FONT_PROP = fm.FontProperties(fname=FONT_PATH, size=7)
    CN_FONT_PROP_BOLD = fm.FontProperties(fname=FONT_PATH, size=7, weight="bold")
    CN_FONT_PROP_TITLE = fm.FontProperties(fname=FONT_PATH, size=8, weight="bold")
    CN_FONT_PROP_LABEL = fm.FontProperties(fname=FONT_PATH, size=9, weight="bold")
    print(f"[字体] Noto Sans SC → {CN_FONT_NAME}")
else:
    CN_FONT_NAME = "DejaVu Sans"
    CN_FONT_PROP = None
    CN_FONT_PROP_BOLD = None
    CN_FONT_PROP_TITLE = None
    CN_FONT_PROP_LABEL = None
    print("[字体] 警告: NotoSansSC.ttf 未找到，回退到 DejaVu Sans")

# ============================================================
# Nature 风格全局 rcParams
# ============================================================
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": [CN_FONT_NAME, "Arial", "Helvetica", "DejaVu Sans"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 7,
    "axes.titlesize": 8,
    "axes.labelsize": 7,
    "xtick.labelsize": 6,
    "ytick.labelsize": 6,
    "legend.fontsize": 5.5,
    "axes.linewidth": 0.5,
    "xtick.major.width": 0.5,
    "ytick.major.width": 0.5,
    "xtick.major.size": 2.5,
    "ytick.major.size": 2.5,
    "lines.linewidth": 0.8,
    "lines.markersize": 3.5,
    # 抑制字体权重警告
    "font.weight": "normal",
})

# ============================================================
# Nature 配色
# ============================================================
C_BLUE = "#4477AA"
C_ORANGE = "#EE7733"
C_GREEN = "#228833"
C_RED = "#CC3311"
C_GRAY_BAND = "#DDDDDD"
C_GRAY_MID = "#888888"
C_GRAY_DARK = "#555555"
C_LLM = "#CC3311"  # 醒目的红色表示 LLM

# ============================================================
# 维度定义
# ============================================================
DIMS = ["accident_type", "severity", "M", "F", "T", "H", "C"]
DIM_LABELS = ["事故类型", "事故等级", "M-管理", "F-防线", "T-触发", "H-行为", "C-后果"]

CODE_LISTS = {
    "M": ["M1", "M2", "M3", "M4", "M5"],
    "F": ["F1", "F2", "F3", "F4"],
    "T": ["T1", "T2", "T3", "T4"],
    "H": ["H1", "H2", "H3", "H4"],
    "C": ["C1", "C2"],
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

# ============================================================
# 数据加载
# ============================================================


def load_expert(filepath: Path) -> List[Dict]:
    """加载 LabelStudio JSON 格式的专家标注。"""
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
    """加载 LLM 标签 JSON。"""
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
    """多数投票构建三专家共识标签。"""
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
    """二值 Cohen's Kappa。"""
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
    """多分类 Cohen's Kappa。"""
    cats = sorted(set(obs1 + obs2))
    if len(cats) <= 1:
        return 1.0 if obs1 == obs2 else 0.0
    n = len(obs1)
    c2i = {c: i for i, c in enumerate(cats)}
    mat = np.zeros((len(cats), len(cats)))
    for o1, o2 in zip(obs1, obs2):
        mat[c2i[o1], c2i[o2]] += 1
    po = np.trace(mat) / n
    rs, cs = mat.sum(axis=1), mat.sum(axis=0)
    pe = np.sum(rs * cs) / (n * n)
    if abs(pe - 1.0) < 1e-9:
        return 1.0
    return (po - pe) / (1 - pe)


def compute_kappa(e1, e2, dim):
    """计算两个标注者在某维度的 Kappa。"""
    if dim in ["accident_type", "severity"]:
        return cohen_kappa_multiclass(
            [r.get(dim, "") for r in e1], [r.get(dim, "") for r in e2]
        )
    codes = CODE_LISTS[dim]
    kappas = [
        cohen_kappa_binary(
            [code in r.get(dim, frozenset()) for r in e1],
            [code in r.get(dim, frozenset()) for r in e2],
        )
        for code in codes
    ]
    return float(np.mean(kappas))


# ============================================================
# 导出辅助
# ============================================================


def save_pub(fig, stem, formats=("svg", "pdf", "png"), dpi=600):
    """保存为 SVG（主格式）、PDF 和高 DPI PNG。"""
    saved = []
    for fmt in formats:
        p = str(stem) + f".{fmt}"
        kw = {"dpi": dpi} if fmt == "png" else {}
        fig.savefig(p, bbox_inches="tight", **kw)
        saved.append(p)
    return saved


# ============================================================
# 主流程
# ============================================================


def main():
    # ── 加载数据 ──────────────────────────────────────────
    print("📂 加载标注数据...")
    eA = load_expert(GROUND_TRUTH / "expertA.json")
    eB = load_expert(GROUND_TRUTH / "expertB.json")
    eC = load_expert(GROUND_TRUTH / "expertC.json")
    llm = load_llm(GROUND_TRUTH / "gt_llm_labels_100.json")
    consensus = build_consensus(eA, eB, eC)
    print(f"  Expert A={len(eA)}, B={len(eB)}, C={len(eC)}, LLM={len(llm)}, 共识={len(consensus)}")

    # ── 计算所有 Kappa ────────────────────────────────────
    print("🔬 计算 Kappa 系数...")

    # 人类两两
    human_ab = [compute_kappa(eA, eB, d) for d in DIMS]
    human_ac = [compute_kappa(eA, eC, d) for d in DIMS]
    human_bc = [compute_kappa(eB, eC, d) for d in DIMS]

    # 人类范围
    human_lo = [min(ab, ac, bc) for ab, ac, bc in zip(human_ab, human_ac, human_bc)]
    human_hi = [max(ab, ac, bc) for ab, ac, bc in zip(human_ab, human_ac, human_bc)]
    human_mean = [(lo + hi) / 2 for lo, hi in zip(human_lo, human_hi)]

    # LLM vs 各专家
    llm_a = [compute_kappa(llm, eA, d) for d in DIMS]
    llm_b = [compute_kappa(llm, eB, d) for d in DIMS]
    llm_c = [compute_kappa(llm, eC, d) for d in DIMS]

    # LLM vs 共识
    llm_consensus = [compute_kappa(llm, consensus, d) for d in DIMS]

    # 专家 vs 共识
    ea_cons = [compute_kappa(eA, consensus, d) for d in DIMS]
    eb_cons = [compute_kappa(eB, consensus, d) for d in DIMS]
    ec_cons = [compute_kappa(eC, consensus, d) for d in DIMS]

    # HFACS 各编码级 Kappa
    code_level = {}
    for dim in ["M", "F", "T", "H", "C"]:
        code_level[dim] = {}
        for code in CODE_LISTS[dim]:
            idx = list(range(len(eA)))
            code_level[dim][code] = {
                "A-B": cohen_kappa_binary(
                    [code in eA[i].get(dim, frozenset()) for i in idx],
                    [code in eB[i].get(dim, frozenset()) for i in idx],
                ),
                "A-C": cohen_kappa_binary(
                    [code in eA[i].get(dim, frozenset()) for i in idx],
                    [code in eC[i].get(dim, frozenset()) for i in idx],
                ),
                "B-C": cohen_kappa_binary(
                    [code in eB[i].get(dim, frozenset()) for i in idx],
                    [code in eC[i].get(dim, frozenset()) for i in idx],
                ),
                "LLM-共识": cohen_kappa_binary(
                    [code in llm[i].get(dim, frozenset()) for i in idx],
                    [code in consensus[i].get(dim, frozenset()) for i in idx],
                ),
            }

    # ── 打印报告 ──────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  LLM vs 三专家共识 Kappa 系数汇总")
    print("=" * 80)
    header = f"  {'维度':<14} {'人类A-B':>8} {'人类A-C':>8} {'人类B-C':>8} {'人类范围':>18} {'LLM-共识':>10} {'判定':>12}"
    print(header)
    print("  " + "-" * 76)
    for i, dim in enumerate(DIMS):
        h_range = f"[{human_lo[i]:.3f}, {human_hi[i]:.3f}]"
        in_range = "✅ 范围内" if human_lo[i] <= llm_consensus[i] <= human_hi[i] + 0.02 else "⚠ 超出"
        print(
            f"  {DIM_LABELS[i]:<14} {human_ab[i]:>8.4f} {human_ac[i]:>8.4f} "
            f"{human_bc[i]:>8.4f} {h_range:>18} {llm_consensus[i]:>10.4f} {in_range:>12}"
        )
    print("=" * 80)

    # ================================================================
    # Figure: 三面板 Kappa 对比图 (Nature 双栏宽度 183mm ≈ 7.2in)
    # ================================================================
    print("\n📊 生成 Nature 风格图表...")

    fig = plt.figure(figsize=(7.2, 8.0), dpi=300)

    # ── 面板 a: LLM-共识 + 人类两两 Kappa 分组柱状图 ──────────
    ax_a = fig.add_subplot(3, 1, 1)
    x = np.arange(len(DIMS))
    width = 0.2

    # 人类两两 Kappa（柔和色）
    ax_a.bar(x - 1.2 * width, human_ab, width * 0.85, color=C_BLUE,
             edgecolor="white", linewidth=0.3, label="专家 A–B", alpha=0.8)
    ax_a.bar(x, human_ac, width * 0.85, color=C_ORANGE,
             edgecolor="white", linewidth=0.3, label="专家 A–C", alpha=0.8)
    ax_a.bar(x + 1.2 * width, human_bc, width * 0.85, color=C_GREEN,
             edgecolor="white", linewidth=0.3, label="专家 B–C", alpha=0.8)

    # LLM-共识（深色突出）
    ax_a.bar(x - 1.2 * width, llm_consensus, width * 0.85, color="none",
             edgecolor=C_LLM, linewidth=1.2, linestyle="--", label="LLM–共识 (轮廓)")
    # LLM-共识 用散点叠加
    ax_a.scatter(x, llm_consensus, s=35, c=C_LLM, marker="D", zorder=5,
                 edgecolors="white", linewidths=0.4, label="LLM–共识")

    # 参考线
    ax_a.axhline(y=0.8, color=C_GRAY_MID, linestyle="--", linewidth=0.4, alpha=0.6)
    ax_a.axhline(y=0.6, color="#AAAAAA", linestyle=":", linewidth=0.4, alpha=0.6)
    ax_a.text(len(DIMS) - 0.35, 0.805, "κ=0.8", fontsize=5, color=C_GRAY_MID, va="bottom")
    ax_a.text(len(DIMS) - 0.35, 0.605, "κ=0.6", fontsize=5, color="#AAAAAA", va="bottom")

    ax_a.set_xticks(x)
    ax_a.set_xticklabels(DIM_LABELS, rotation=20, ha="right", fontsize=6)
    ax_a.set_ylabel("Cohen's Kappa", fontsize=7)
    ax_a.set_ylim(0.35, 1.08)
    ax_a.spines["top"].set_visible(False)
    ax_a.spines["right"].set_visible(False)
    ax_a.tick_params(axis="both", length=2.5, pad=1.5)

    # 图例：分两行
    leg1 = ax_a.legend(
        loc="lower left", frameon=True, fancybox=False, edgecolor="#CCCCCC",
        fontsize=5, handlelength=1.5, borderpad=0.3, ncol=2,
        title="评分者间信度", title_fontsize=5.5,
    )
    leg1.get_title().set_fontweight("bold")

    # 面板标签
    ax_a.text(-0.04, 1.02, "a", transform=ax_a.transAxes, fontsize=9,
              fontweight="bold", va="bottom", ha="left")
    ax_a.set_title("LLM–共识 与 人类专家两两 Kappa 对比",
                   fontsize=8, fontweight="bold", loc="left", pad=6)

    # ── 面板 b: LLM-共识 vs 人类评分者间范围 ──────────────────
    ax_b = fig.add_subplot(3, 1, 2)

    # 人类范围灰色带
    for i in range(len(DIMS)):
        ax_b.fill_between(
            [i - 0.38, i + 0.38], human_lo[i], human_hi[i],
            color=C_GRAY_BAND, alpha=0.7, edgecolor="none", zorder=1,
        )

    # 人类均值
    ax_b.scatter(x, human_mean, s=25, c=C_GRAY_DARK, marker="s", zorder=3,
                 label="人类专家均值", edgecolors="white", linewidths=0.3)

    # LLM-共识 散点（颜色根据是否在范围内）
    colors_llm = []
    for i in range(len(DIMS)):
        if human_lo[i] <= llm_consensus[i] <= human_hi[i] + 0.02:
            colors_llm.append(C_GREEN)
        else:
            colors_llm.append(C_RED)
    ax_b.scatter(x, llm_consensus, s=45, c=colors_llm, marker="o", zorder=5,
                 edgecolors="white", linewidths=0.5, label="LLM–共识")

    # 连接线（引导视觉）
    for i in range(len(DIMS)):
        ax_b.plot([i, i], [human_mean[i], llm_consensus[i]],
                  color=C_GRAY_MID, linewidth=0.4, linestyle=":", alpha=0.5, zorder=2)

    ax_b.set_xticks(x)
    ax_b.set_xticklabels(DIM_LABELS, rotation=20, ha="right", fontsize=6)
    ax_b.set_ylabel("Cohen's Kappa", fontsize=7)
    ax_b.set_ylim(0.35, 1.08)
    ax_b.spines["top"].set_visible(False)
    ax_b.spines["right"].set_visible(False)
    ax_b.tick_params(axis="both", length=2.5, pad=1.5)

    legend_elements = [
        mpatches.Patch(color=C_GRAY_BAND, alpha=0.7, label="人类评分者间范围"),
        plt.Line2D([0], [0], marker="s", color="w", markerfacecolor=C_GRAY_DARK,
                    markersize=4, label="人类专家均值"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=C_GREEN,
                    markersize=5, label="LLM 在范围内"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=C_RED,
                    markersize=5, label="LLM 超出范围"),
    ]
    ax_b.legend(
        handles=legend_elements, loc="lower left", frameon=True,
        fancybox=False, edgecolor="#CCCCCC", fontsize=5,
        handlelength=1.5, borderpad=0.3, ncol=2,
    )

    ax_b.text(-0.04, 1.02, "b", transform=ax_b.transAxes, fontsize=9,
              fontweight="bold", va="bottom", ha="left")
    ax_b.set_title("LLM–共识 vs 人类评分者间信度范围",
                   fontsize=8, fontweight="bold", loc="left", pad=6)

    # ── 面板 c: HFACS 各编码级 Kappa 热力图 ──────────────────
    ax_c = fig.add_subplot(3, 1, 3)

    # 构建编码列表和热力图矩阵
    all_codes = []
    for dim in ["M", "F", "T", "H", "C"]:
        all_codes.extend(CODE_LISTS[dim])

    # 短标签
    code_labels = [f"{c}\n{CODE_NAMES[c][:5]}" for c in all_codes]

    heatmap_data = np.zeros((4, len(all_codes)))
    for ci, code in enumerate(all_codes):
        dim = code[0]  # M, F, T, H, C
        kk = code_level[dim][code]
        heatmap_data[0, ci] = kk["A-B"]
        heatmap_data[1, ci] = kk["A-C"]
        heatmap_data[2, ci] = kk["B-C"]
        heatmap_data[3, ci] = kk["LLM-共识"]

    # 自定义 diverging colormap
    colors_div = ["#4477AA", "#EEEEEE", "#EE7733"]
    cmap = LinearSegmentedColormap.from_list("nature_div", colors_div, N=100)

    im = ax_c.imshow(heatmap_data, aspect="auto", cmap="RdYlGn", vmin=0.35, vmax=1.0)

    # 标注数值
    for i in range(len(all_codes)):
        for j in range(4):
            val = heatmap_data[j, i]
            text_color = "white" if val < 0.5 or val > 0.92 else "#222222"
            ax_c.text(i, j, f"{val:.2f}", ha="center", va="center", fontsize=5,
                      color=text_color, fontweight="bold" if val >= 0.90 else "normal")

    ax_c.set_xticks(range(len(all_codes)))
    ax_c.set_xticklabels(code_labels, rotation=50, ha="right", fontsize=5.5)
    ax_c.set_yticks([0, 1, 2, 3])
    ax_c.set_yticklabels(["A–B", "A–C", "B–C", "LLM-\n共识"], fontsize=5.5)

    # 维度分隔线
    cum = 0
    for dim in ["M", "F", "T", "H"]:
        cum += len(CODE_LISTS[dim])
        ax_c.axvline(x=cum - 0.5, color="#333333", linewidth=0.8, linestyle="-")

    # 维度区域标注
    cum_start = 0
    for dim in ["M", "F", "T", "H", "C"]:
        n = len(CODE_LISTS[dim])
        mid = cum_start + (n - 1) / 2
        ax_c.text(mid, -0.85, f"M-管理" if dim == "M" else
                               f"F-防线" if dim == "F" else
                               f"T-触发" if dim == "T" else
                               f"H-行为" if dim == "H" else "C-后果",
                  ha="center", fontsize=5.5, color=C_GRAY_DARK)
        cum_start += n

    # 颜色条
    cbar = plt.colorbar(im, ax=ax_c, fraction=0.018, pad=0.02, aspect=35)
    cbar.set_label("Cohen's Kappa", fontsize=6, labelpad=2)
    cbar.ax.tick_params(labelsize=5, length=2, pad=1)

    ax_c.text(-0.04, 1.02, "c", transform=ax_c.transAxes, fontsize=9,
              fontweight="bold", va="bottom", ha="left")
    ax_c.set_title("各 CAICS 编码的评分者间信度（二值 Kappa）",
                   fontsize=8, fontweight="bold", loc="left", pad=6)

    # ── 全局调整 ──────────────────────────────────────────
    plt.subplots_adjust(hspace=0.55, left=0.10, right=0.96, top=0.97, bottom=0.05)

    # ── 导出 ──────────────────────────────────────────────
    stem = FIGURES_DIR / "fig_kappa_llm_vs_experts"
    paths = save_pub(fig, stem)
    for p in paths:
        print(f"  ✓ {p}")

    plt.close(fig)

    # ── 保存 Kappa 数据 JSON ────────────────────────────────
    chart_data = {
        "dims": DIMS,
        "dim_labels": DIM_LABELS,
        "human_ab": human_ab,
        "human_ac": human_ac,
        "human_bc": human_bc,
        "human_lo": human_lo,
        "human_hi": human_hi,
        "human_mean": human_mean,
        "llm_consensus": llm_consensus,
        "llm_a": llm_a,
        "llm_b": llm_b,
        "llm_c": llm_c,
        "ea_cons": ea_cons,
        "eb_cons": eb_cons,
        "ec_cons": ec_cons,
        "code_level": {
            dim: {
                code: {k: float(v) for k, v in code_level[dim][code].items()}
                for code in CODE_LISTS[dim]
            }
            for dim in ["M", "F", "T", "H", "C"]
        },
    }
    data_path = FIGURES_DIR / "kappa_chart_data.json"
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(chart_data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 数据: {data_path}")

    print("\n✅ 全部完成")


if __name__ == "__main__":
    main()
