#!/usr/bin/env python3
"""生成 ESWA 论文 6 张图：Fig.1 架构 / Fig.2 CAICS树 / Fig.3 编码分布 /
Fig.4 样本层级 / Fig.5 门控效应 / Fig.6 Kappa森林图
输出: docs/paper_v2/figures/ (300 DPI, 英文标注)

图号与 eswa_paper_v2.md 的 Figure 1-6 一一对应。Fig.3 的 19 类分布与
Fig.1 的致因标注总数从主库 caiems_v2.db 现读（CAIEMS_DB 可覆盖），
不写死；Fig.4/Fig.5/Fig.6 的数字取自 experiments/ 下的实验产物。"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np
import os
from pathlib import Path
import textwrap
import warnings
import logging
warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')
logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)

plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['WenQuanYi Zen Hei', 'DejaVu Sans']
plt.rcParams['svg.fonttype'] = 'none'
plt.rcParams['axes.unicode_minus'] = False
# 固定 SVG 里的 clip-path id 盐值：不设的话每次重生成 id 全变，diff 里几百行
# 噪声会把"到底哪张图真的变了"淹掉（实测改前改后 229 行全在变 id 与时间戳）
plt.rcParams['svg.hashsalt'] = 'caiems'

OUT = str(Path(__file__).resolve().parents[1] / 'docs' / 'paper_v2' / 'figures')
os.makedirs(OUT, exist_ok=True)

# 分布数字一律从主库现读，脚本里不留死数。2026-10-01：fig3 曾停在孤儿外键
# 清理前的 17,364（实测 16,323），三类数字还各差 100 上下；改成读库后不会再
# 与数据库脱节。
DATA_DIR = os.environ.get('CAIEMS_DATA_DIR', '/home/suntianhao/construction_agent/data')
DB_PATH = os.environ.get('CAIEMS_DB', os.path.join(DATA_DIR, 'caiems_v2.db'))

_COUNTS = None


def load_code_counts():
    """主库 caics_causes 的 19 类实测分布，按计数降序 → [(code, n), ...]。"""
    global _COUNTS
    if _COUNTS is None:
        import sqlite3
        con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        try:
            rows = con.execute(
                'SELECT code, COUNT(*) AS n FROM caics_causes '
                'GROUP BY code ORDER BY n DESC').fetchall()
        finally:
            con.close()
        if not rows:
            raise SystemExit(f'从 {DB_PATH} 读不到 caics_causes 分布')
        _COUNTS = [(str(c), int(n)) for c, n in rows]
    return _COUNTS


def code_total():
    return sum(n for _, n in load_code_counts())


# ── 实验产物：图 4/5/6 的数字一律现读，不在函数体里留死数 ──────────────
# 2026-10-05 之前，图 5 的 16 个值、图 6 的 15 个值、图 4 的 5 个值是**手抄**在
# 函数体里的（只有图 3 从主库现读）。逐位核对当时全中，但这正是 fig3 那次漂移
# 的同一类成因：源一变，图上数字不跟，而人不会每次都去对。
WT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.environ.get('CAIEMS_MAIN', '/home/suntianhao/construction_agent')
F1_SUMMARY = f'{WT}/experiments/ablation_original/f1_summary.json'
GATE_SUMMARY = f'{WT}/experiments/ablation_gatekeeping/summary.json'
AGREE = f'{MAIN}/experiments/experiment_kappa/Basic/289/analysis/agreement_metrics.json'
EXPERT = f'{MAIN}/experiments/experiment_kappa/kappa_old/expert_inter_rater.json'
EXP1 = f'{MAIN}/experiments/experiment-models/experiment1_metrics_20260626_102135.json'
FOURWAY = f'{MAIN}/experiments/four_way_comparison.md'

# 图 6 的行序（须与图里的 features 顺序一致）
K6_ORDER = ['accident_type', 'severity', 'province', 'deaths', 'injuries']


def _jload(path):
    import json
    if not os.path.exists(path):
        raise SystemExit(f'读不到实验产物：{path}')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def ablation_f1():
    """2×2 消融四格的 F1 / Recall / M1 recall / 空输出率，来自 f1_summary.json。

    空输出率 = empty / n_sample（图里画成 0.21 这种两位小数，故 `.3f` 会补 0）。
    """
    d = _jload(F1_SUMMARY)
    n = _jload(GATE_SUMMARY).get('n_sample')
    if not n:
        raise SystemExit(f'{GATE_SUMMARY} 里没有 n_sample，空输出率无法归一')
    return {k: {'F1': v['F1'], 'R': v['R'], 'M1R': v['M1R'], 'empty': v['empty'] / n}
            for k, v in d.items()}


def agreement_ci():
    """图 6 的五条 LLM-human 点估计与 95% CI，来自 agreement_metrics.json。"""
    d = _jload(AGREE)
    return [(d[k]['value'], d[k]['ci_lo'], d[k]['ci_hi']) for k in K6_ORDER]


def expert_band():
    """图 6 的专家带（每个指标三个两两配对的一致性 min/max），来自 expert_inter_rater.json。

    province 在该文件里没有对应键（专家只标了 4 个字段），故为 None——
    图里对 None 不画灰带，这是有意的空缺，不是漏读。
    """
    d = _jload(EXPERT)
    lo, hi = [], []
    for k in K6_ORDER:
        if k not in d:
            lo.append(None)
            hi.append(None)
            continue
        vals = [p['kappa'] if 'kappa' in p else p['icc'] for p in d[k].values()]
        lo.append(min(vals))
        hi.append(max(vals))
    return lo, hi


def expert_n():
    """专家标注集样本量：从 agreement 字段的 '97/100 (97%)' 取分母。"""
    import re
    for pairs in _jload(EXPERT).values():
        for p in pairs.values():
            m = re.search(r'/(\d+)', str(p.get('agreement', '')))
            if m:
                return int(m.group(1))
    raise SystemExit(f'{EXPERT} 里找不到 agreement 计数，专家样本量无法确定')


def sampling_n():
    """图 4 的三层实验样本量：(模型选择, 策略对比, 一致性研究)。

    模型选择取 experiment1 的 `cost.total`（键名在 cost 下，实为运行统计的
    "共处理 N 条"）；策略对比取门控消融的 n_sample；一致性研究取 κ 分析的 meta.n。
    """
    import re
    exp1 = _jload(EXP1)
    n_agree = _jload(AGREE).get('meta', {}).get('n')
    src = open(FOURWAY, encoding='utf-8').read() if os.path.exists(FOURWAY) else ''
    m = re.search(r'银标准\*\*[:：]\s*(\d+)', src)
    return {
        'model': exp1['deepseek_v4']['cost']['total'],
        'strategy': _jload(GATE_SUMMARY).get('n_sample'),
        'agreement': n_agree,
        'records': sqlite_scalar('SELECT COUNT(*) FROM accidents'),
        'silver': int(m.group(1)) if m else None,
    }


def sqlite_scalar(sql):
    import sqlite3
    con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()

C = {
    'blue':   '#1B4F72', 'midblue': '#2471A3', 'teal': '#117A65',
    'purple': '#7D3C98', 'green':  '#1E8449', 'orange': '#D35400',
    'red':    '#B03A2E', 'gray':   '#566573', 'text':   '#2C3E50',
    'light':  '#D6EAF8', 'v1': '#B03A2E', 'v2': '#1E8449',
    'bg': '#FAFAFA',
}


def save(fig, name):
    fig.savefig(f'{OUT}/{name}.png', dpi=300, bbox_inches='tight', facecolor='white')
    # Date=None 去掉 SVG 里的 <dc:date>：内容没变时重生成不该产生 diff
    fig.savefig(f'{OUT}/{name}.svg', bbox_inches='tight', facecolor='white',
                metadata={'Date': None})
    plt.close(fig)
    print(f'  ✓ {name}')


# ============================================================
# Fig. 1 系统架构（纵向流程图，英文）
# ============================================================
def fig1_architecture():
    fig, ax = plt.subplots(figsize=(9.6, 12))
    ax.set_xlim(0, 9.6); ax.set_ylim(0, 12); ax.axis('off')

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

    # 多模态扩展（独立右列；画布已加宽，框完整落在 xlim 内，不压主流程）
    box = FancyBboxPatch((7.75, 2.35), 1.5, 2.1, boxstyle="round,pad=0.05",
                         facecolor=C['orange'], edgecolor='none', alpha=0.12, zorder=1)
    ax.add_patch(box)
    ax.text(8.5, 4.36, 'Multimodal\nextension', fontsize=8, ha='center', va='top',
            color=C['orange'], fontweight='bold', zorder=3)
    ax.text(8.5, 3.78, 'search\nenrichment', fontsize=7, ha='center', va='top', color=C['text'], zorder=3)
    ax.text(8.5, 3.20, 'CLIP image\nfilter', fontsize=7, ha='center', va='top', color=C['text'], zorder=3)
    ax.text(8.5, 2.62, 'VLM\ndescription', fontsize=7, ha='center', va='top', color=C['text'], zorder=3)
    ax.annotate('', xy=(7.42, 4.3), xytext=(7.73, 3.75),
                arrowprops=dict(arrowstyle='->', color=C['orange'], lw=1.0, linestyle='--'), zorder=3)

    # 数据产物标注
    ax.text(4.0, 1.35,
            f'2,623 records | {code_total():,} causal codes | '
            '226 images | 282 VLM analyses',
            fontsize=8, ha='center', color=C['gray'], style='italic')
    save(fig, 'fig1_architecture')


# ============================================================
# Fig. 2 CAICS 五层树
# ============================================================
def fig2_caics_tree():
    """左：五层结构（每层一个宽框，码表自动换行，不用窄框——17 字中文标题在
    1.28 宽的格里必然互相重叠）。右：一条真实记录的证据链，展示同一段证据
    文本如何落到不同层的多个编码上。"""
    fig, ax = plt.subplots(figsize=(11, 6.6))
    ax.set_xlim(0, 11); ax.set_ylim(0, 6.6); ax.axis('off')

    layers = [
        ('M', 'Management Foundation', [
            'M1 Deficient supervision', 'M2 Failed hazard inspection',
            'M3 Insufficient training', 'M4 Procedural gaps',
            'M5 Irregular market conduct'], C['blue']),
        ('F', 'Protection Failure', [
            'F1 Edge/opening protection', 'F2 Support instability',
            'F3 Disabled safety devices', 'F4 Unused PPE'], C['teal']),
        ('T', 'Trigger Conditions', [
            'T1 Equipment failure', 'T2 Material state',
            'T3 Geological change', 'T4 Meteorological'], C['orange']),
        ('H', 'Human Acts', [
            'H1 Procedure violation', 'H2 Improper command',
            'H3 Failed risk perception', 'H4 Poor emergency response'], C['purple']),
        ('C', 'Consequence Amplification', [
            'C1 Delayed rescue', 'C2 Concealed reporting'], C['red']),
    ]

    ax.text(0.3, 6.44, 'CAICS — five-layer causal framework', fontsize=11,
            fontweight='bold', color=C['text'], va='top')

    x0, w, gap = 0.3, 6.0, 0.20
    top = 6.05
    for i, (key, title, codes, color) in enumerate(layers):
        lines = textwrap.wrap('   '.join(codes), width=98) or ['']
        h = 0.36 + 0.28 * len(lines)
        bottom = top - h
        ax.add_patch(FancyBboxPatch((x0, bottom), w, h, boxstyle="round,pad=0.04",
                                    facecolor=color, edgecolor='none', alpha=0.13, zorder=1))
        ax.plot([x0 + 0.08, x0 + 0.08], [bottom + 0.07, top - 0.07],
                color=color, lw=3.5, zorder=2, solid_capstyle='round')
        ax.text(x0 + 0.26, top - 0.10, f'{key} — {title}  ({len(codes)})', fontsize=9,
                fontweight='bold', color=color, va='top', zorder=3)
        ax.text(x0 + 0.26, top - 0.42, '\n'.join(lines), fontsize=7.2,
                color=C['text'], va='top', linespacing=1.45, zorder=3)
        if i:
            ax.annotate('', xy=(x0 + 0.6, top), xytext=(x0 + 0.6, top + gap),
                        arrowprops=dict(arrowstyle='-|>', color=C['gray'], lw=1.1), zorder=0)
        top = bottom - gap

    ax.text(x0 + w / 2, top + 0.02, 'Causal leak model:  M → F → T → H → C',
            fontsize=8.5, ha='center', va='top', color=C['gray'], style='italic')

    # ---- 右：一条真实记录的证据链 ----
    px, pw, py, ph = 6.65, 4.05, 1.77, 4.28
    ax.add_patch(FancyBboxPatch((px, py), pw, ph, boxstyle="round,pad=0.05",
                                facecolor=C['bg'], edgecolor=C['gray'], lw=0.8,
                                alpha=0.6, zorder=1))
    ax.text(px + 0.22, py + ph - 0.14, 'Example — one evidence text, several layers',
            fontsize=8.8, fontweight='bold', color=C['text'], va='top', zorder=3)
    ax.text(px + 0.22, py + ph - 0.42,
            'Formwork-support collapse, Sichuan Langzhong (2023)',
            fontsize=7.2, color=C['text'], va='top', zorder=3)
    ax.text(px + 0.22, py + ph - 0.76,
            '"During concrete pouring for the gate beam between\n'
            ' buildings 3 and 1, the formwork-support system\n'
            ' collapsed."',
            fontsize=7, color=C['gray'], style='italic', va='top',
            linespacing=1.55, zorder=3)

    layer_of = {code[0]: color for _, _, codes, color in layers for code in codes}
    trace = [
        ('M2 Failed hazard inspection', 'explicit'),
        ('F2 Support instability', 'explicit'),
        ('H1 Procedure violation', 'explicit'),
        ('T2 Material state', 'explicit'),
        ('M1 Deficient supervision', 'inferred'),
        ('M4 Procedural gaps', 'inferred'),
    ]
    ty = py + ph - 1.96
    for label, tier in trace:
        ax.add_patch(FancyBboxPatch((px + 0.22, ty - 0.02), 0.06, 0.22,
                                    boxstyle="square,pad=0", facecolor=layer_of[label[0]],
                                    edgecolor='none', alpha=0.9, zorder=3))
        ax.text(px + 0.38, ty, label, fontsize=7.4, color=C['text'], va='bottom', zorder=3)
        ax.text(px + pw - 0.22, ty, tier, fontsize=6.8, ha='right', va='bottom',
                color=C['gray'], style='italic', zorder=3)
        ty -= 0.32

    ax.text(px + 0.22, py + 0.16,
            'explicit = stated in the text;  inferred = authorized inference',
            fontsize=6.5, color=C['gray'], va='bottom', zorder=3)
    save(fig, 'fig2_caics_tree')


# ============================================================
# Fig. 3 编码分布
# ============================================================
def fig3_code_dist():
    pairs = load_code_counts()
    codes = [c for c, _ in pairs]
    counts = [n for _, n in pairs]
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
    ax.set_xlim(0, max(counts) * 1.18)
    handles = [mpatches.Patch(color=layer_color[k], alpha=0.8,
                              label={'M': 'Management', 'F': 'Protection', 'T': 'Trigger',
                                     'H': 'Human acts', 'C': 'Consequence'}[k])
               for k in 'MFTHC']
    ax.legend(handles=handles, fontsize=7.5, frameon=False, loc='lower right')
    ax.tick_params(axis='x', labelsize=7.5)
    ax.set_title(f'Distribution of {sum(counts):,} causal codes across '
                 f'{len(codes)} CAICS categories', fontsize=9.5)
    save(fig, 'fig3_code_dist')


# ============================================================
# Fig. 4 样本层级
# ============================================================
def fig4_sampling():
    n = sampling_n()
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.set_xlim(0, 7.5); ax.set_ylim(0, 4.5); ax.axis('off')

    def box(x, y, w, h, title, sub, color):
        b = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.05",
                           facecolor=color, edgecolor=color, alpha=0.12, lw=1.2)
        ax.add_patch(b)
        ax.text(x + w / 2, y + h - 0.18, title, fontsize=8.5, fontweight='bold',
                color=color, ha='center', va='center')
        ax.text(x + w / 2, y + 0.16, sub, fontsize=7, color=C['text'], ha='center', va='center')

    box(2.9, 3.5, 1.7, 0.7, 'Full corpus', f'{n["records"]:,} records', C['gray'])
    box(2.9, 2.3, 1.7, 0.7, 'Silver standard', f'{n["silver"]} validated', C['blue'])
    ax.annotate('', xy=(3.75, 3.0), xytext=(3.75, 3.5),
                arrowprops=dict(arrowstyle='->', color=C['gray'], lw=1.2))

    box(0.3, 0.9, 1.9, 0.9, 'Model selection',
        f'n = {n["model"]}\nstratified by type', C['teal'])
    box(2.8, 0.9, 1.9, 0.9, 'Strategy comparison',
        f'n = {n["strategy"]}\n3-expert majority vote', C['purple'])
    box(5.3, 0.9, 1.9, 0.9, 'Agreement study',
        f'n = {n["agreement"]}\n2-stage stratified', C['red'])
    for cx in [1.25, 3.75, 6.25]:
        ax.annotate('', xy=(cx, 1.8), xytext=(3.75, 2.3),
                    arrowprops=dict(arrowstyle='->', color=C['gray'], lw=1.0))
    ax.text(3.75, 4.32, 'Experiment samples', fontsize=9.5, fontweight='bold', ha='center', color=C['text'])
    save(fig, 'fig4_sampling')


# ============================================================
# Fig. 5 门控效应诊断（v1/v2 恢复 + 2×2 消融 F1 分解）
# ============================================================
def fig5_gatekeeping():
    abl = ablation_f1()
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))

    # 左面板: v1 → v2 恢复（多数投票 GT）
    ax = axes[0]
    metrics = ['F1', 'Recall', 'M1 recall', 'Empty output']
    v1 = [abl['v1'][k] for k in ('F1', 'R', 'M1R', 'empty')]
    v2 = [abl['v2'][k] for k in ('F1', 'R', 'M1R', 'empty')]
    x = np.arange(len(metrics))
    w = 0.35
    b1 = ax.bar(x - w / 2, v1, w, label='v1 conditional-check', color=C['v1'], alpha=0.85)
    b2 = ax.bar(x + w / 2, v2, w, label='v2 two-step judgment', color=C['v2'], alpha=0.85)
    # 数值标签贴柱顶；增量标注另起一行抬高，避免两行文字叠在一起
    for bars, vals in ((b1, v1), (b2, v2)):
        for bar, val in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.015,
                    f'{val:.3f}', ha='center', fontsize=7, va='bottom', color=C['text'])
    for xi, a, b in zip(x, v1, v2):
        pct = (b - a) / max(a, 1e-6) * 100
        ax.annotate(f'{pct:+.0f}%', xy=(xi, max(a, b) + 0.13), ha='center',
                    fontsize=8, color=C['text'], fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(metrics, fontsize=9)
    ax.set_ylabel('Value', fontsize=9)
    ax.set_ylim(0, 1.30)   # 给 +601% 那行让出空间，不然顶到轴顶
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.legend(fontsize=7.5, frameon=False, loc='upper left')
    ax.set_title('(a) v1→v2 recovery (majority-vote GT)', fontsize=9)

    # 右面板: 2×2 消融 F1 与 M1 召回
    ax = axes[1]
    cells = ['v1\nchecks\nconserv.', 'v2a\ntwo-step\nconserv.', 'v2b\nchecks\ncoverage', 'v2\ntwo-step\ncoverage']
    # 与 cells 同序：v1 / v2a / v2b / v2
    f1 = [abl[k]['F1'] for k in ('v1', 'v2a', 'v2b', 'v2')]
    m1r = [abl[k]['M1R'] for k in ('v1', 'v2a', 'v2b', 'v2')]
    colors = [C['v1'], C['midblue'], C['orange'], C['v2']]
    x = np.arange(4)
    ax.bar(x - 0.2, f1, 0.4, label='Ave+ F1', color=colors, alpha=0.75)
    ax.bar(x + 0.2, m1r, 0.4, label='M1 recall', color=colors, alpha=0.45,
           hatch='//', edgecolor='white')
    for xi, v in zip(x, f1):
        ax.text(xi - 0.2, v + 0.015, f'{v:.3f}', ha='center', fontsize=7.5, color=C['text'])
    for xi, v in zip(x, m1r):
        ax.text(xi + 0.2, v + 0.015, f'{v:.3f}', ha='center', fontsize=7.5, color=C['text'])
    ax.set_xticks(x)
    ax.set_xticklabels(cells, fontsize=7.5)
    ax.set_ylim(0, 1.15)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.legend(fontsize=7.5, frameon=False, loc='upper left')
    ax.set_title(f'(b) 2×2 ablation (n = {_jload(GATE_SUMMARY)["n_sample"]}, '
                 'majority-vote GT)', fontsize=9)

    save(fig, 'fig5_gatekeeping')


# ============================================================
# Fig. 6 Kappa 森林图
# ============================================================
def fig6_kappa():
    features = ['Accident type\n(κ)', 'Severity\n(κ)', 'Province\n(κ)',
                'Deaths\n(ICC)', 'Injuries\n(ICC)']
    ci = agreement_ci()
    vals = [v for v, _, _ in ci]
    los = [lo for _, lo, _ in ci]
    his = [hi for _, _, hi in ci]
    expert_lo, expert_hi = expert_band()
    n_agree = _jload(AGREE)['meta']['n']
    n_expert = expert_n()

    fig, ax = plt.subplots(figsize=(7, 4.6))
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
    ax.text(0.813, 4.68, 'Landis-Koch 0.81', fontsize=6.5, ha='left', va='top',
            color=C['gray'])
    ax.set_xlim(0.80, 1.02)
    ax.set_ylim(-1.25, 4.85)   # 底部留白给图例，不再压在 Injuries 行上
    ax.set_xlabel('Agreement (κ / ICC)', fontsize=9)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.set_title(f'LLM-human agreement (n = {n_agree}) vs. expert inter-rater '
                 'ranges (shaded)', fontsize=9)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color='#F2D7D5', alpha=0.55,
                             label=f'Expert range (n = {n_expert}, 3 experts)'),
                       Patch(color=C['blue'], label='LLM-human (95% CI)')],
              fontsize=7.5, frameon=False, loc='lower left')
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
