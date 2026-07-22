"""
CAIEMS 论文图表生成
目标期刊: Expert Systems with Applications
样式: 学术期刊标准（800×600 dpi, 灰度友好, 统一字体）
"""

import json
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.patches import FancyBboxPatch

# ============================================================
# 全局样式
# ============================================================

plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'DejaVu Serif', 'SimSun'],
    'font.size': 11,
    'axes.titlesize': 13,
    'axes.labelsize': 12,
    'legend.fontsize': 10,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.1,
})

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_COLORS = {
    'deepseek_v4': '#2166AC',   # 深蓝
    'qwen3': '#1B7837',         # 深绿
    'glm52': '#B2182B',         # 深红
    'kimi': '#F4A582',          # 浅橙
}
MODEL_LABELS = {
    'deepseek_v4': 'DeepSeek V4',
    'qwen3': 'Qwen 3.7',
    'glm52': 'GLM 5.2',
    'kimi': 'Kimi K2.6',
}
MODEL_KEYS = ['deepseek_v4', 'qwen3', 'glm52', 'kimi']
FIELD_LABELS = {
    'accident_type': 'Accident\nType',
    'severity': 'Severity',
    'deaths': 'Deaths',
    'injuries': 'Injuries',
    'location': 'Location',
    'city': 'City',
}
DIM_LABELS = {
    'human_factors': 'Human', 'machine_factors': 'Machine',
    'management_factors': 'Management', 'environmental_factors': 'Environment',
}


def load_data():
    with open(os.path.join(OUTPUT_DIR, 'chart_data.json')) as f:
        return json.load(f)


def save_fig(fig, name):
    for ext in ['.pdf', '.png']:
        path = os.path.join(OUTPUT_DIR, f'{name}{ext}')
        fig.savefig(path, dpi=300)
        print(f'  ✅ {path}')


# ============================================================
# Fig 1: Core Field Accuracy (grouped bar)
# ============================================================

def fig1_core_accuracy(data):
    """Figure 1: 4 models × 6 core fields accuracy comparison"""
    metrics = data['metrics']
    fields = [f['field'] for f in [
        {'field': 'accident_type'}, {'field': 'severity'},
        {'field': 'deaths'}, {'field': 'injuries'},
        {'field': 'location'}, {'field': 'city'},
    ]]
    labels = [FIELD_LABELS[f] for f in fields]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = np.arange(len(fields))
    w = 0.2  # bar width
    gap = 0.03

    for i, mk in enumerate(MODEL_KEYS):
        vals = [metrics.get(mk, {}).get('basic_fields', {}).get(f, {}).get('accuracy', 0) for f in fields]
        bars = ax.bar(x + i * (w + gap) - 1.5 * (w + gap), vals, w,
                      color=MODEL_COLORS[mk], label=MODEL_LABELS[mk],
                      edgecolor='white', linewidth=0.3)
        # 在柱子上标数值
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                        f'{v:.0f}', ha='center', va='bottom', fontsize=7, fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel('Accuracy (%)', fontsize=12)
    ax.set_ylim(0, 105)
    ax.legend(frameon=False, fontsize=9, loc='lower left')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(20))

    fig.tight_layout()
    save_fig(fig, 'fig1_core_accuracy')
    plt.close(fig)


# ============================================================
# Fig 2: Cause Dimension Precision/Recall (grouped bars per model)
# ============================================================

def fig2_cause_performance(data):
    """Figure 2: Each model's Precision & Recall for 4 cause dimensions"""
    metrics = data['metrics']
    dims = ['human_factors', 'machine_factors', 'management_factors', 'environmental_factors']

    fig, axes = plt.subplots(2, 2, figsize=(9, 6.5))
    axes = axes.flatten()

    for ax_i, dim in enumerate(dims):
        ax = axes[ax_i]
        x = np.arange(len(MODEL_KEYS))
        w = 0.3

        prec_vals = [metrics.get(mk, {}).get('cause_dim_f1', {}).get(dim, {}).get('precision', 0) for mk in MODEL_KEYS]
        rec_vals = [metrics.get(mk, {}).get('cause_dim_f1', {}).get(dim, {}).get('recall', 0) for mk in MODEL_KEYS]

        bars1 = ax.bar(x - w / 2, prec_vals, w, color='#2166AC', label='Precision', edgecolor='white', linewidth=0.3)
        bars2 = ax.bar(x + w / 2, rec_vals, w, color='#F4A582', label='Recall', edgecolor='white', linewidth=0.3)

        # 标注数值
        for bar, v in zip(bars1, prec_vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                    f'{v:.0f}', ha='center', va='bottom', fontsize=7, fontweight='bold')
        for bar, v in zip(bars2, rec_vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                    f'{v:.0f}', ha='center', va='bottom', fontsize=7, fontweight='bold')

        ax.set_xticks(x)
        ax.set_xticklabels([MODEL_LABELS[mk] for mk in MODEL_KEYS], fontsize=8, rotation=15)
        ax.set_title(DIM_LABELS[dim], fontsize=12, fontweight='bold')
        ax.set_ylim(0, 105)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        if ax_i == 0:
            ax.legend(frameon=False, fontsize=9, loc='upper left')

    fig.suptitle('Cause Dimension Extraction: Precision vs Recall', fontsize=13, fontweight='bold', y=1.02)
    fig.tight_layout()
    save_fig(fig, 'fig2_cause_performance')
    plt.close(fig)


# ============================================================
# Fig 3: Radar Chart - Multi-dimensional comparison
# ============================================================

def fig3_radar(data):
    """Figure 3: Radar chart - Core Accuracy, Cause Precision, Cause Recall, Speed"""
    metrics = data['metrics']
    costs = data['costs']

    # 定义雷达维度
    categories = ['Core Field\nAccuracy', 'Cause\nPrecision', 'Cause\nRecall', 'Processing\nSpeed']
    N = len(categories)

    # 计算各模型在各维度的值（归一化到 0-100）
    max_latency = max(costs.get(mk, {}).get('avg_latency', 1) for mk in MODEL_KEYS)

    values = {}
    for mk in MODEL_KEYS:
        m = metrics.get(mk, {})
        bf = m.get('basic_fields', {})
        cf = m.get('cause_dim_f1', {}).get('avg', {})
        lat = costs.get(mk, {}).get('avg_latency', 100)
        # Speed 分数 = (1 - latency/max_latency) * 100
        speed_score = (1 - lat / max_latency) * 100
        values[mk] = [
            bf.get('avg_accuracy', 0),
            cf.get('precision', 0),
            cf.get('recall', 0),
            speed_score,
        ]

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))

    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles += angles[:1]

    for mk in MODEL_KEYS:
        vals = values[mk] + values[mk][:1]
        ax.plot(angles, vals, 'o-', linewidth=2, color=MODEL_COLORS[mk], label=MODEL_LABELS[mk])
        ax.fill(angles, vals, alpha=0.05, color=MODEL_COLORS[mk])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=10)
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(['20', '40', '60', '80', '100'], fontsize=8)
    ax.legend(frameon=False, fontsize=9, loc='upper right', bbox_to_anchor=(1.3, 1.0))
    ax.spines['polar'].set_visible(False)

    fig.tight_layout()
    save_fig(fig, 'fig3_radar')
    plt.close(fig)


# ============================================================
# Fig 4: Cost vs Performance Pareto
# ============================================================

def fig4_pareto(data):
    """Figure 4: Cost-Performance scatter with Pareto frontier"""
    metrics = data['metrics']
    costs = data['costs']

    fig, ax = plt.subplots(figsize=(7, 5))

    for mk in MODEL_KEYS:
        m = metrics.get(mk, {})
        c = costs.get(mk, {})
        score = m.get('overall_score', 0)
        cost = c.get('total_cost', 0)

        ax.scatter(cost, score, s=120, color=MODEL_COLORS[mk], label=MODEL_LABELS[mk],
                   edgecolor='white', linewidth=0.5, zorder=5)
        # 标签
        offset_x = cost * 0.1 + 0.02
        ax.annotate(MODEL_LABELS[mk], (cost, score),
                    textcoords="offset points", xytext=(10, 5),
                    fontsize=9, fontstyle='italic')

    # Pareto 前沿线（找到最优性价比的连线）
    pareto_points = sorted(
        [(costs.get(mk, {}).get('total_cost', 0), metrics.get(mk, {}).get('overall_score', 0), mk)
         for mk in MODEL_KEYS],
        key=lambda x: x[0]
    )
    pareto_x, pareto_y = [], []
    max_y = -1
    for cx, cy, _ in pareto_points:
        if cy > max_y:
            pareto_x.append(cx)
            pareto_y.append(cy)
            max_y = cy
    if len(pareto_x) > 1:
        ax.plot(pareto_x, pareto_y, '--', color='gray', linewidth=1, alpha=0.5, zorder=1)
        ax.text(pareto_x[-1], pareto_y[-1], '  Pareto Frontier',
                fontsize=8, color='gray', fontstyle='italic')

    ax.set_xlabel('Total API Cost (USD)', fontsize=12)
    ax.set_ylabel('Overall Performance Score', fontsize=12)
    ax.set_xscale('log')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.legend(frameon=False, fontsize=9, loc='lower right')

    fig.tight_layout()
    save_fig(fig, 'fig4_pareto')
    plt.close(fig)


# ============================================================
# Fig 5: Summary Table (as styled figure)
# ============================================================

def fig5_summary_table(data):
    """Figure 5: Summary comparison table as a styled figure"""
    metrics = data['metrics']
    costs = data['costs']

    fig, ax = plt.subplots(figsize=(8, 3))
    ax.axis('off')

    # 表头和数据
    headers = ['Model', 'Core Acc.', 'Cause Prec.', 'Cause Recall', 'Overall', 'Cost(USD)', 'Speed(s)']
    rows = []
    for mk in MODEL_KEYS:
        m = metrics.get(mk, {})
        c = costs.get(mk, {})
        bf = m.get('basic_fields', {})
        cf = m.get('cause_dim_f1', {}).get('avg', {})
        rows.append([
            MODEL_LABELS[mk],
            f"{bf.get('avg_accuracy', 0):.1f}%",
            f"{cf.get('precision', 0):.1f}%",
            f"{cf.get('recall', 0):.1f}%",
            f"{m.get('overall_score', 0):.1f}",
            f"${c.get('total_cost', 0):.4f}",
            f"{c.get('avg_latency', 0):.1f}s",
        ])

    # 使用表格
    table = ax.table(cellText=rows, colLabels=headers,
                     loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.6)

    # 格式化
    for (i, j), cell in table.get_celld().items():
        if i == 0:  # header
            cell.set_facecolor('#f0f0f0')
            cell.set_text_props(fontweight='bold')
        else:
            cell.set_facecolor('white')
        cell.set_edgecolor('#cccccc')

    fig.tight_layout()
    save_fig(fig, 'table1_summary')
    plt.close(fig)


# ============================================================
# Main
# ============================================================

if __name__ == '__main__':
    data = load_data()
    print('生成图表...')
    fig1_core_accuracy(data)
    fig2_cause_performance(data)
    fig3_radar(data)
    fig4_pareto(data)
    fig5_summary_table(data)
    print(f'\n所有图表已保存到: {OUTPUT_DIR}/')
    print('格式: PDF（期刊投稿）+ PNG（预览）')
