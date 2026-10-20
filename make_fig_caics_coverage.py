#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ESWA 论文插图：300 条 GT 事故的 CAICS 19 类致因编码正例/负例分布。

Figure contract
---------------
Core conclusion : 19 个 CAICS 类在 300 条 GT 中全部有正例，但正例率高度不均，
                  管理与人的行为层编码占主导，后果放大层（C1/C2）极少。
Evidence chain  : 单面板；每行一个编码；蓝色=该编码在 300 条事故中被标注为正例的条数，
                  橙色=剩余条数；条末标注精确数量。
Archetype       : quantitative grid（单面板）。
Backend         : Python / matplotlib（已保存的默认后端）。
Export contract : Elsevier ESWA 双栏宽 190 mm；Arial 系字体；PDF/SVG 文本可编辑；
                  PNG 600 dpi；附源数据 CSV。

数据源：experiments/golden_standard/gt_manual/cause_gt.jsonl
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

# ── 路径 ──────────────────────────────────────────────────────────────────────
ROOT = Path("/home/suntianhao/construction_agent")
SRC = ROOT / "experiments/golden_standard/gt_manual/cause_gt.jsonl"
OUT_DIR = ROOT / "TEM"
STEM = OUT_DIR / "fig_caics_code_coverage"

N_TOTAL = 300          # GT 事故总数（条长）
X_MAX = 350            # 横轴绘制长度，100 为刻度单位
BLUE = "#A7C9E8"       # 正例（present, 1）
ORANGE = "#F2A57A"     # 负例（absent, 0）

# ── 样式：Elsevier ESWA 双栏 ─────────────────────────────────────────────────
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"],
    "font.size": 7.5,
    "axes.linewidth": 0.6,
    "axes.edgecolor": "#333333",
    "axes.spines.right": False,
    "axes.spines.top": False,
    "xtick.color": "#222222",
    "ytick.color": "#222222",
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "xtick.major.size": 2.6,
    "ytick.major.size": 2.6,
    "legend.frameon": False,
    "svg.fonttype": "none",   # SVG 文本保持可编辑
    "pdf.fonttype": 42,       # PDF 内嵌 TrueType，文本可编辑
})


def load_counts(path: Path) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            for code in set(rec["codes"]):
                counts[code] = counts.get(code, 0) + 1
    if len(counts) != 19:
        raise ValueError(f"expected 19 CAICS codes, got {len(counts)}")
    # 按正例数降序；并列按编码升序
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def build() -> tuple[list[tuple[str, int]], Path]:
    data = load_counts(SRC)
    codes = [c for c, _ in data]
    present = [n for _, n in data]
    absent = [N_TOTAL - n for n in present]

    assert all(0 <= n <= N_TOTAL for n in present)

    fig, ax = plt.subplots(figsize=(190 / 25.4, 120 / 25.4))  # 190 × 120 mm
    fig.subplots_adjust(left=0.078, right=0.968, top=0.985, bottom=0.165)

    y = list(range(len(codes)))[::-1]
    bar_h = 0.72

    ax.barh(y, present, height=bar_h, color=BLUE, linewidth=0, zorder=3)
    ax.barh(y, absent, left=present, height=bar_h, color=ORANGE, linewidth=0, zorder=3)

    # 条末精确计数
    for yi, n in zip(y, present):
        ax.text(N_TOTAL + 2.5, yi, str(n), va="center", ha="left",
                fontsize=6.4, color="#555555", zorder=4)

    # 轴与刻度
    ax.set_yticks(y)
    ax.set_yticklabels(codes, fontsize=8)
    ax.set_ylim(-0.6, len(codes) - 0.4)
    ax.set_xlim(0, X_MAX)
    ax.set_xticks([0, 100, 200, 300])
    ax.set_xlabel("Number of accidents (N = 300)", fontsize=7.5, labelpad=3)

    # 纵向浅网格，位于条形之后
    ax.set_axisbelow(True)
    ax.xaxis.grid(True, color="#E4E4E4", linewidth=0.5, zorder=0)
    ax.yaxis.grid(False)
    ax.tick_params(axis="y", length=0, pad=4)

    # 图例
    handles = [
        Patch(facecolor=BLUE, edgecolor="none", label="Present (1)"),
        Patch(facecolor=ORANGE, edgecolor="none", label="Absent (0)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=2,
               bbox_to_anchor=(0.5, 0.018), fontsize=7.4,
               handlelength=0.9, handleheight=0.55, columnspacing=1.6,
               handletextpad=0.45)

    # ── 导出 ─────────────────────────────────────────────────────────────────
    # 不裁边，保证成品严格为 190 × 120 mm，字号与最终印刷尺寸一一对应
    fig.savefig(f"{STEM}.pdf")
    fig.savefig(f"{STEM}.svg")
    fig.savefig(f"{STEM}.png", dpi=600)
    plt.close(fig)

    # ── 源数据 ───────────────────────────────────────────────────────────────
    csv_path = OUT_DIR / "fig_caics_code_coverage_data.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "present", "absent", "present_rate"])
        for c, p, a in zip(codes, present, absent):
            w.writerow([c, p, a, f"{p / N_TOTAL:.4f}"])

    return data, csv_path


if __name__ == "__main__":
    rows, csv_path = build()
    for path in (f"{STEM}.pdf", f"{STEM}.svg", f"{STEM}.png", str(csv_path)):
        print("wrote", path)
    print("codes:", rows)
