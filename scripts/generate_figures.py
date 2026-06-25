"""生成科研图表 — Nature 期刊风格。

从 accidents_merged.json 生成以下图表：
1. 事故类型分布（柱状图）
2. HFACS 层级分布（柱状图）
3. 事故等级分布（饼图）
4. 事故年份趋势（折线图）
5. 5M 致因维度雷达图
6. HFACS 逐编码热力图
"""

import json
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
# Fonts: DejaVu Sans covers English; CJK fallback handled by system
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import rcParams

# ── Nature 期刊风格配置 ──
rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Ubuntu Sans", "Arial"],
    "font.size": 8,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5,
    "legend.fontsize": 7.5,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "axes.linewidth": 0.8,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "xtick.minor.width": 0.4,
    "ytick.minor.width": 0.4,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

OUTPUT_DIR = Path("experiments/figures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 加载数据 ──
with open("data/accidents_merged.json") as f:
    records = json.load(f)

# 颜色方案（Nature 风格 — 灰度友好 + 有限彩色）
NATURE_COLORS = ["#3B6EA5", "#DF7D46", "#5E9B5E", "#B55555", "#8B6FAF", "#D4A843", "#6DB3B3", "#B07A5A", "#7B8EA0", "#C97D7D"]
NATURE_GRADIENT = ["#2B3A4A", "#3B6EA5", "#5E9B5E", "#DF7D46", "#B55555"]
SEQ_COLORS = ["#E8F0F8", "#B8D0E8", "#6B9FC7", "#3B6EA5", "#1E3A5F"]
# ── 中文→英文翻译映射 ──
TYPE_EN = {
    "坍塌": "Collapse", "高处坠落": "Fall from height", "起重伤害": "Crane injury",
    "土方坍塌": "Earthwork collapse", "物体打击": "Struck by object",
    "火灾爆炸": "Fire/explosion", "机械伤害": "Machinery injury",
    "中毒窒息": "Poisoning/suffocation", "触电": "Electric shock",
    "淹溺": "Drowning", "其他": "Others",
}
SEV_EN = {
    "一般事故": "General", "较大事故": "Major", "重大事故": "Serious",
    "特别重大事故": "Catastrophic", "未知": "Unknown",
}
LEVEL_EN = {
    1: "L1 Organizational", 2: "L2 Supervision", 3: "L3 Preconditions",
    4: "L4 Unsafe Acts", 5: "L5 Site Conditions", 6: "L6 Physical Env.",
}
DIM_EN = {
    "human_factors": "Human", "machine_factors": "Machine",
    "management_factors": "Management", "environmental_factors": "Environment",
}



# ── 图表 1: 事故类型分布 ──
def fig1_accident_types():
    type_counts = Counter(r.get("accident_type", "未知") for r in records)
    types = sorted(type_counts, key=type_counts.get, reverse=True)
    types_en = [TYPE_EN.get(t, t) for t in types]
    counts = [type_counts[t] for t in types]

    fig, ax = plt.subplots(figsize=(5.5, 3.2))
    bars = ax.barh(range(len(types)), counts, color=NATURE_COLORS[0], height=0.65, zorder=2)
    ax.set_yticks(range(len(types)))
    ax.set_yticklabels(types_en, fontsize=7.5)
    ax.set_xlabel("Number of accidents", fontsize=9)
    ax.set_title("a  Distribution of accident types", loc="left", fontsize=10, fontweight="bold")
    for i, (bar, c) in enumerate(zip(bars, counts)):
        ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height()/2,
                str(c), va="center", fontsize=7, color="#555555")
    ax.margins(x=0.12)
    ax.grid(axis="x", alpha=0.3, zorder=0)
    fig.savefig(OUTPUT_DIR / "fig1_accident_types.png")
    plt.close(fig)
    print("  ✅ fig1_accident_types.png")


# ── 图表 2: HFACS 层级分布 ──
def fig2_hfacs_levels():
    level_counts = Counter()
    for r in records:
        for d in (r.get("hfacs_details") or []):
            level_counts[d.get("level", 0)] += 1

    level_names = LEVEL_EN
    levels = list(range(1, 7))
    counts = [level_counts.get(l, 0) for l in levels]
    labels = [level_names[l] for l in levels]

    fig, ax = plt.subplots(figsize=(5.5, 3.2))
    colors = [NATURE_GRADIENT[min(i, len(NATURE_GRADIENT)-1)] for i in range(6)]
    bars = ax.bar(range(6), counts, color=colors, width=0.6, zorder=2)
    ax.set_xticks(range(6))
    ax.set_xticklabels(labels, fontsize=7)
    ax.set_ylabel("Code frequency", fontsize=9)
    ax.set_title("b  HFACS-CS level distribution", loc="left", fontsize=10, fontweight="bold")
    for bar, c in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 15,
                str(c), ha="center", va="bottom", fontsize=7.5, color="#333333")
    ax.margins(y=0.12)
    ax.grid(axis="y", alpha=0.3, zorder=0)
    fig.savefig(OUTPUT_DIR / "fig2_hfacs_levels.png")
    plt.close(fig)
    print("  ✅ fig2_hfacs_levels.png")


# ── 图表 3: 事故等级分布（饼图） ──
def fig3_severity():
    sev_counts = Counter(r.get("severity", "未知") for r in records)
    labels = list(sev_counts.keys())
    sizes = list(sev_counts.values())
    colors_pie = ["#3B6EA5", "#DF7D46", "#B55555", "#8B6FAF"]

    fig, ax = plt.subplots(figsize=(4, 3.2))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=None, autopct="%1.1f%%",
        colors=colors_pie[:len(labels)],
        startangle=90, pctdistance=0.75,
        wedgeprops={"linewidth": 0.8, "edgecolor": "white"},
    )
    for at in autotexts:
        at.set_fontsize(7.5)
        at.set_color("white")
        at.set_fontweight("bold")
    total = sum(sizes)
    legend_labels = [f"{SEV_EN.get(l,l)}  ({s}, {s/total*100:.1f}%)" for l, s in zip(labels, sizes)]
    ax.legend(wedges, legend_labels, loc="lower center", ncol=2,
              fontsize=7, frameon=False, bbox_to_anchor=(0.5, -0.15))
    ax.set_title("c  Severity distribution", loc="left", fontsize=10, fontweight="bold", pad=12)
    fig.savefig(OUTPUT_DIR / "fig3_severity.png")
    plt.close(fig)
    print("  ✅ fig3_severity.png")


# ── 图表 4: 年份趋势 ──
def fig4_year_trend():
    year_counts = Counter()
    for r in records:
        d = r.get("date", "")
        if d and len(d) >= 4:
            year_counts[d[:4]] += 1

    years = sorted(y for y in year_counts if y.isdigit())
    counts = [year_counts[y] for y in years]

    fig, ax = plt.subplots(figsize=(5.5, 3))
    ax.fill_between(range(len(years)), counts, alpha=0.25, color=NATURE_COLORS[0])
    ax.plot(range(len(years)), counts, color=NATURE_COLORS[0], linewidth=1.5,
            marker="o", markersize=4, zorder=3)
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels(years, fontsize=7, rotation=45)
    ax.set_ylabel("Number of accidents", fontsize=9)
    ax.set_xlabel("Year", fontsize=9)
    ax.set_title("d  Annual trend of accidents", loc="left", fontsize=10, fontweight="bold")
    ax.margins(y=0.1)
    ax.grid(axis="y", alpha=0.3, zorder=0)
    fig.savefig(OUTPUT_DIR / "fig4_year_trend.png")
    plt.close(fig)
    print("  ✅ fig4_year_trend.png")


# ── 图表 5: 5M 致因维度雷达图 ──
def fig5_5m_radar():
    dims = ["Human\n(Man)", "Machine\n(Machine)", "Management\n(Method)", "Environment\n(Env.)"]
    dim_keys = ["human_factors", "machine_factors", "management_factors", "environmental_factors"]

    fill_rates = []
    for key in dim_keys:
        filled = sum(1 for r in records if r.get(key) and len(r[key]) > 0)
        fill_rates.append(filled / len(records) * 100)

    angles = np.linspace(0, 2 * np.pi, len(dims), endpoint=False).tolist()
    angles += angles[:1]
    values = fill_rates + fill_rates[:1]

    fig, ax = plt.subplots(figsize=(3.5, 3.5), subplot_kw={"projection": "polar"})
    ax.plot(angles, values, "o-", linewidth=1.5, color=NATURE_COLORS[0], zorder=2)
    ax.fill(angles, values, alpha=0.15, color=NATURE_COLORS[0])
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(dims, fontsize=7.5)
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20%", "40%", "60%", "80%", "100%"], fontsize=6, color="#888888")
    ax.set_title("e  Causation factor coverage (%)", loc="left", fontsize=10, fontweight="bold", pad=12)
    for a, v, d in zip(angles[:-1], fill_rates, dims):
        ax.text(a, v + 5, f"{v:.0f}%", ha="center", va="bottom", fontsize=7, fontweight="bold", color=NATURE_COLORS[0])
    ax.grid(alpha=0.3)
    fig.savefig(OUTPUT_DIR / "fig5_5m_radar.png")
    plt.close(fig)
    print("  ✅ fig5_5m_radar.png")


# ── 图表 6: HFACS 逐编码热力图 ──
def fig6_hfacs_heatmap():
    # 统计每个事故类型下各 HFACS 编码的出现次数
    hfacs_codes_all = [
        "O1","O2","O3","O4","S1","S2","S3","S4","S5",
        "P1","P2","P3","P4","A1","A2","A3","A4","A5",
        "W1","W2","W3","W4","W5","W6","E1","E2","E3","E4","E5"
    ]
    type_order_en = ["Collapse", "Fall from height", "Crane injury", "Earthwork collapse", "Struck by object",
                      "Fire/explosion", "Machinery injury", "Poisoning/suffoc.", "Electric shock", "Drowning", "Others"]
    type_order = list(TYPE_EN.keys())

    matrix = {}
    for at in type_order:
        matrix[at] = Counter()
    for r in records:
        at = TYPE_EN.get(r.get("accident_type", "Others"), "Others")
        if at not in matrix:
            at = "Others"
        for d in (r.get("hfacs_details") or []):
            code = d.get("code", "")
            if code in hfacs_codes_all:
                matrix[at][code] += 1

    data = np.zeros((len(type_order), len(hfacs_codes_all)))
    for i, at in enumerate(type_order):
        for j, code in enumerate(hfacs_codes_all):
            data[i, j] = matrix[at].get(code, 0)

    # 归一化：每行（事故类型）归一化
    row_sums = data.sum(axis=1, keepdims=True)
    row_sums = np.where(row_sums == 0, 1, row_sums)
    data_norm = data / row_sums * 100

    fig, ax = plt.subplots(figsize=(7, 3.8))
    im = ax.imshow(data_norm, aspect="auto", cmap="Blues", vmin=0, vmax=40)

    ax.set_xticks(range(len(hfacs_codes_all)))
    ax.set_xticklabels(hfacs_codes_all, fontsize=6.5, rotation=90)
    ax.set_yticks(range(len(type_order)))
    ax.set_yticklabels(type_order_en, fontsize=7)
    ax.set_xlabel("HFACS-CS 编码", fontsize=9)
    ax.set_ylabel("Accident type", fontsize=9)
    ax.set_title("f  Accident type × HFACS code heatmap (row-normalized %)", loc="left", fontsize=10, fontweight="bold")

    cbar = fig.colorbar(im, ax=ax, shrink=0.7, pad=0.02)
    cbar.set_label("Proportion (%)", fontsize=7.5)
    cbar.ax.tick_params(labelsize=6.5)

    # 标注数值（只标 ≥5 的）
    for i in range(len(type_order)):
        for j in range(len(hfacs_codes_all)):
            v = data_norm[i, j]
            if v >= 8:
                color = "white" if v > 25 else "#333333"
                ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=5.5, color=color)

    fig.savefig(OUTPUT_DIR / "fig6_hfacs_heatmap.png")
    plt.close(fig)
    print("  ✅ fig6_hfacs_heatmap.png")


if __name__ == "__main__":
    print("正在生成 Nature 风格科研图表...\n")
    fig1_accident_types()
    fig2_hfacs_levels()
    fig3_severity()
    fig4_year_trend()
    fig5_5m_radar()
    fig6_hfacs_heatmap()

    from datetime import datetime
    print(f"\n✅ 全部图表已保存到 {OUTPUT_DIR}/")
    print(f"   生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    for f in sorted(OUTPUT_DIR.glob("fig*.png")):
        size_kb = f.stat().st_size / 1024
        print(f"   - {f.name} ({size_kb:.0f} KB)")
