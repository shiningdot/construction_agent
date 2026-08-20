#!/usr/bin/env python3
"""生成 ESWA 论文 6 张图：Fig.1 架构 / Fig.2 CAICS树 / Fig.3 编码分布 /
Fig.4 样本层级 / Fig.5 门控效应 / Fig.6 Kappa森林图
输出: docs/paper/figures/ (300 DPI, 英文标注)"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np
import os
import warnings
import logging
warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')
logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)

plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['WenQuanYi Zen Hei', 'DejaVu Sans']
plt.rcParams['svg.fonttype'] = 'none'
plt.rcParams['axes.unicode_minus'] = False

OUT = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/figures'
os.makedirs(OUT, exist_ok=True)

C = {
    'blue':   '#1B4F72', 'midblue': '#2471A3', 'teal': '#117A65',
    'purple': '#7D3C98', 'green':  '#1E8449', 'orange': '#D35400',
    'red':    '#B03A2E', 'gray':   '#566573', 'text':   '#2C3E50',
    'light':  '#D6EAF8', 'v1': '#B03A2E', 'v2': '#1E8449',
    'bg': '#FAFAFA',
}


def save(fig, name):
    fig.savefig(f'{OUT}/{name}.png', dpi=300, bbox_inches='tight', facecolor='white')
    fig.savefig(f'{OUT}/{name}.svg', bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(f'  ✓ {name}')


# ============================================================
# Fig. 1 系统架构（纵向流程图，英文）
# ============================================================
def fig1_architecture():
    fig, ax = plt.subplots(figsize=(8, 12))
    ax.set_xlim(0, 8); ax.set_ylim(0, 12); ax.axis('off')

    stages = [
        ('Stage 1: Multi-source collection', 'Crawlers (safehoo, esafety, bureaus)', C['blue']),
        ('Stage 2: LLM text classification', 'single / multi / other (few-shot)', C['teal']),
        ('Stage 3: Basic field extraction (Step 1)', '33 fields, tool-call structured output', C['midblue']),
        ('Stage 4: CAICS causal coding (Step 2)', 'evidence-bound codes + inference chains', C['midblue']),
        ('Stage 5: MECE enum standardization', 'LLM-first, 5 fields (WT/EQP/MAT/WKR/BLD)', C['purple']),
        ('Stage 6: Storage and access', 'SQLite (9 tables), Web platform, REST API', C['gray']),
    ]
    h = 1.15
    ys = [10.6, 8.9, 7.2, 5.5, 3.8, 2.1]
    for (title, sub, color), y in zip(stages, ys):
        box = FancyBboxPatch((0.6, y), 6.8, h, boxstyle="round,pad=0.06",
                             facecolor=color, edgecolor='none', alpha=0.10, zorder=1)
        ax.add_patch(box)
        ax.plot([0.75, 0.75], [y + 0.1, y + h - 0.1], color=color, lw=4, zorder=2)
        ax.text(1.05, y + h - 0.28, title, fontsize=10.5, fontweight='bold', color=color, va='top', zorder=3)
        ax.text(1.05, y + 0.22, sub, fontsize=8.5, color=C['text'], va='bottom', zorder=3)
        # 箭头
        if y < 10.6:
            ax.annotate('', xy=(4.0, y + h + 0.06), xytext=(4.0, y + h + 0.30),
                        arrowprops=dict(arrowstyle='->', color=C['gray'], lw=1.4), zorder=0)

    # 多模态扩展（右侧小框）
    ax.text(7.45, 4.1, 'Multimodal\nextension', fontsize=8, ha='center', color=C['orange'], fontweight='bold', rotation=0)
    box = FancyBboxPatch((6.75, 2.6), 1.35, 1.35, boxstyle="round,pad=0.05",
                         facecolor=C['orange'], edgecolor='none', alpha=0.12, zorder=1)
    ax.add_patch(box)
    ax.text(7.42, 3.75, 'search\nenrichment', fontsize=7, ha='center', color=C['text'])
    ax.text(7.42, 3.25, 'CLIP image\nfilter', fontsize=7, ha='center', color=C['text'])
    ax.text(7.42, 2.85, 'VLM\ndescription', fontsize=7, ha='center', color=C['text'])
    ax.annotate('', xy=(6.72, 3.35), xytext=(7.15, 4.55),
                arrowprops=dict(arrowstyle='->', color=C['orange'], lw=1.0, linestyle='--'), zorder=0)

    # 数据产物标注
    ax.text(4.0, 1.35, '2,623 records | 17,364 causal codes | 226 images | 282 VLM analyses',
            fontsize=8, ha='center', color=C['gray'], style='italic')
    save(fig, 'fig1_architecture')


# ============================================================
# Fig. 2 CAICS 五层树
# ============================================================
def fig2_caics_tree():
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.set_xlim(0, 9); ax.set_ylim(0, 5); ax.axis('off')

    layers = [
        ('M — Management Foundation', ['M1 Deficient supervision', 'M2 Failed hazard inspection',
                                        'M3 Insufficient training', 'M4 Procedural gaps',
                                        'M5 Irregular market conduct'], C['blue'], 0.85),
        ('F — Protection Failure', ['F1 Edge/opening protection', 'F2 Support instability',
                                    'F3 Disabled safety devices', 'F4 Unused PPE'], C['teal'], 2.15),
        ('T — Trigger Conditions', ['T1 Equipment failure', 'T2 Material state', 'T3 Geological change',
                                    'T4 Meteorological'], C['orange'], 3.45),
        ('H — Human Acts', ['H1 Procedure violation', 'H2 Improper command', 'H3 Failed risk perception',
                            'H4 Poor emergency response'], C['purple'], 1.75),
        ('C — Consequence Amplification', ['C1 Delayed rescue', 'C2 Concealed reporting'], C['red'], 3.05),
    ]
    root_x, root_y = 4.5, 4.75
    for label, codes, color, cx in layers:
        ly = {'M': 3.9, 'F': 3.0, 'T': 2.1, 'H': 1.2, 'C': 0.35}[label[0]]
        # 层标题
        ax.text(cx, ly + 0.78, label, fontsize=9, fontweight='bold', color=color, ha='center')
        n = len(codes)
        w = 6.4 / n
        for i, code in enumerate(codes):
            x0 = 0.55 + i * w
            box = FancyBboxPatch((x0, ly), w - 0.12, 0.62, boxstyle="round,pad=0.03",
                                 facecolor=color, edgecolor='none', alpha=0.16)
            ax.add_patch(box)
            ax.text(x0 + (w - 0.12) / 2, ly + 0.31, code, fontsize=6.6, ha='center', va='center', color=C['text'])
        # 层与根连线
        ax.annotate('', xy=(cx, ly + 0.62), xytext=(root_x, root_y),
                    arrowprops=dict(arrowstyle='->', color=color, lw=0.9, alpha=0.5))
    ax.text(root_x, 4.92, 'CAICS', fontsize=11, fontweight='bold', color=C['text'], ha='center')
    ax.text(4.5, 0.02, 'Causal leak model: M → F → T → H → C', fontsize=8.5,
            ha='center', color=C['gray'], style='italic')
    save(fig, 'fig2_caics_tree')


# ============================================================
# Fig. 3 编码分布
# ============================================================
def fig3_code_dist():
    codes = ['M1', 'H1', 'H3', 'M2', 'F2', 'M4', 'M3', 'T2', 'F4', 'M5',
             'H2', 'T1', 'F1', 'F3', 'T3', 'T4', 'H4', 'C1', 'C2']
    counts = [2331, 2217, 2144, 1883, 1588, 1500, 1284, 662, 615, 588,
              524, 474, 391, 349, 346, 160, 109, 106, 93]
    layer_color = {'M': C['blue'], 'F': C['teal'], 'T': C['orange'],
                   'H': C['purple'], 'C': C['red']}
    colors = [layer_color[c[0]] for c in codes]

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    y = np.arange(len(codes))[::-1]
    ax.barh(y, counts, color=colors, alpha=0.8, edgecolor='white', height=0.7)
    ax.set_yticks(y); ax.set_yticklabels(codes, fontsize=7)
    ax.set_xlabel('Number of evidence-bound codes', fontsize=9)
    for yi, ct in zip(y, counts):
        ax.text(ct + 30, yi, f'{ct:,}', fontsize=6.5, va='center', color=C['text'])
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.set_xlim(0, 2700)
    handles = [mpatches.Patch(color=layer_color[k], alpha=0.8,
                              label={'M': 'Management', 'F': 'Protection', 'T': 'Trigger',
                                     'H': 'Human acts', 'C': 'Consequence'}[k])
               for k in 'MFTHC']
    ax.legend(handles=handles, fontsize=7.5, frameon=False, loc='lower right')
    ax.tick_params(axis='x', labelsize=7.5)
    ax.set_title('Distribution of 17,364 causal codes across 19 CAICS categories', fontsize=9.5)
    save(fig, 'fig3_code_dist')


# ============================================================
# Fig. 4 样本层级
# ============================================================
def fig4_sampling():
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.set_xlim(0, 7.5); ax.set_ylim(0, 4.5); ax.axis('off')

    def box(x, y, w, h, title, sub, color):
        b = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.05",
                           facecolor=color, edgecolor=color, alpha=0.12, lw=1.2)
        ax.add_patch(b)
        ax.text(x + w / 2, y + h - 0.18, title, fontsize=8.5, fontweight='bold',
                color=color, ha='center', va='center')
        ax.text(x + w / 2, y + 0.16, sub, fontsize=7, color=C['text'], ha='center', va='center')

    box(2.9, 3.5, 1.7, 0.7, 'Full corpus', '2,623 records', C['gray'])
    box(2.9, 2.3, 1.7, 0.7, 'Silver standard', '972 validated', C['blue'])
    ax.annotate('', xy=(3.75, 3.0), xytext=(3.75, 3.5),
                arrowprops=dict(arrowstyle='->', color=C['gray'], lw=1.2))

    box(0.3, 0.9, 1.9, 0.9, 'Model selection', 'n = 200\nstratified by type', C['teal'])
    box(2.8, 0.9, 1.9, 0.9, 'Strategy comparison', 'n = 100\n3-expert majority vote', C['purple'])
    box(5.3, 0.9, 1.9, 0.9, 'Agreement study', 'n = 289\n2-stage stratified', C['red'])
    for cx in [1.25, 3.75, 6.25]:
        ax.annotate('', xy=(cx, 1.8), xytext=(3.75, 2.3),
                    arrowprops=dict(arrowstyle='->', color=C['gray'], lw=1.0))
    ax.text(3.75, 4.32, 'Experiment samples', fontsize=9.5, fontweight='bold', ha='center', color=C['text'])
    save(fig, 'fig4_sampling')


# ============================================================
# Fig. 5 门控效应诊断
# ============================================================
def fig5_gatekeeping():
    metrics = ['F1', 'Recall', 'M1 recall', 'Empty output']
    v1 = [0.425, 0.319, 0.143, 0.30]
    v2 = [0.676, 0.772, 0.941, 0.01]
    x = np.arange(len(metrics))
    w = 0.35

    fig, ax = plt.subplots(figsize=(6.5, 4))
    b1 = ax.bar(x - w / 2, v1, w, label='v1 conditional-check', color=C['v1'], alpha=0.85)
    b2 = ax.bar(x + w / 2, v2, w, label='v2 two-step judgment', color=C['v2'], alpha=0.85)
    for xi, a, b in zip(x, v1, v2):
        delta = b - a
        pct = delta / max(a, 1e-6) * 100
        ax.annotate(f'+{pct:.0f}%', xy=(xi, max(a, b) + 0.03), ha='center',
                    fontsize=8, color=C['text'], fontweight='bold')
    for bars in (b1, b2):
        for bar in bars:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.008,
                    f'{bar.get_height():.3f}'.rstrip('0').rstrip('.') if bar.get_height() < 0.9 else f'{bar.get_height():.0%}',
                    ha='center', fontsize=7, va='bottom', color=C['text'])
    ax.set_xticks(x); ax.set_xticklabels(metrics, fontsize=9)
    ax.set_ylabel('Value', fontsize=9)
    ax.set_ylim(0, 1.12)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.legend(fontsize=8, frameon=False, loc='upper left')
    ax.set_title('The Lexical Gatekeeping Effect and its two-step judgment remedy', fontsize=9.5)
    save(fig, 'fig5_gatekeeping')


# ============================================================
# Fig. 6 Kappa 森林图
# ============================================================
def fig6_kappa():
    features = ['Accident type\n(κ)', 'Severity\n(κ)', 'Province\n(κ)',
                'Deaths\n(ICC)', 'Injuries\n(ICC)']
    vals = [0.9136, 0.9477, 0.9746, 0.9849, 0.9853]
    los = [0.8747, 0.9024, 0.9563, 0.9292, 0.9426]
    his = [0.9466, 0.9841, 0.9927, 0.9991, 0.9995]
    expert_lo = [0.8584, 0.8799, None, 0.9951, 0.9738]
    expert_hi = [0.9642, 0.9822, None, 1.0, 1.0]

    fig, ax = plt.subplots(figsize=(7, 4))
    y = np.arange(len(features))[::-1]
    for yi, (lo, hi) in zip(y, zip(expert_lo, expert_hi)):
        if lo is not None:
            ax.axhspan(yi - 0.32, yi + 0.32, color='#F2D7D5', alpha=0.55, zorder=1)
    ax.errorbar(vals, y, xerr=[np.array(vals) - np.array(los), np.array(his) - np.array(vals)],
                fmt='o', color=C['blue'], ecolor=C['midblue'], elinewidth=1.8,
                capsize=4, capthick=1.8, ms=7, zorder=3)
    for yi, (v, lo, hi) in zip(y, zip(vals, los, his)):
        ax.text(v, yi + 0.30, f'{v:.4f} [{lo:.4f}, {hi:.4f}]', fontsize=7,
                ha='center', color=C['text'])
    ax.set_yticks(y); ax.set_yticklabels(features, fontsize=8.5)
    ax.axvline(0.81, color=C['gray'], ls='--', lw=1, alpha=0.7)
    ax.text(0.81, -0.55, 'Landis-Koch\n0.81', fontsize=6.5, ha='center', color=C['gray'])
    ax.set_xlim(0.80, 1.02)
    ax.set_xlabel('Agreement (κ / ICC)', fontsize=9)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.set_title('LLM-human agreement (n = 289) vs. expert inter-rater ranges (shaded)', fontsize=9)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color='#F2D7D5', alpha=0.55, label='Expert range (n = 100, 3 experts)'),
                       Patch(color=C['blue'], label='LLM-human (95% CI)')],
              fontsize=7.5, frameon=False, loc='lower right')
    save(fig, 'fig6_kappa')


if __name__ == '__main__':
    print('生成论文图:')
    fig1_architecture()
    fig2_caics_tree()
    fig3_code_dist()
    fig4_sampling()
    fig5_gatekeeping()
    fig6_kappa()
    print('完成 →', OUT)
