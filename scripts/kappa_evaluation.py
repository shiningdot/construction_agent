#!/usr/bin/env python3
"""
Kappa 系数评测与科研图表生成脚本。
评测三个专家（A/B/C）与 LLM 标注的评分者间信度，并生成 Nature 风格图表。

使用方法：
  python scripts/kappa_evaluation.py          # 计算 + 生成图表
  python scripts/kappa_evaluation.py --report # 仅打印报告
  python scripts/kappa_evaluation.py --figs   # 仅生成图表
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

# ============================================================
# 项目路径
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH_DIR = PROJECT_ROOT / "data" / "ground_truth"
FIGURES_DIR = PROJECT_ROOT / "experiments" / "figures"

# HFACS 层级编码
CODE_LISTS = {
    'M': ['M1', 'M2', 'M3', 'M4', 'M5'],
    'F': ['F1', 'F2', 'F3', 'F4'],
    'T': ['T1', 'T2', 'T3', 'T4'],
    'H': ['H1', 'H2', 'H3', 'H4'],
    'C': ['C1', 'C2'],
}

CODE_NAMES = {
    'M1': '现场监管缺失', 'M2': '隐患排查失效', 'M3': '安全培训不足',
    'M4': '制度规程漏洞', 'M5': '市场行为违规',
    'F1': '临边洞口防护缺失', 'F2': '支撑体系失稳', 'F3': '设备安全装置失效',
    'F4': '个体防护未使用',
    'T1': '设备机具故障', 'T2': '物料状态异常', 'T3': '地质环境突变', 'T4': '气象水文触发',
    'H1': '违反操作规程', 'H2': '违规指挥决策', 'H3': '风险辨识失败', 'H4': '应急行为失当',
    'C1': '救援响应迟缓', 'C2': '信息迟报瞒报',
}

DIM_LABELS_CN = {
    'accident_type': '事故类型',
    'severity': '事故等级',
    'M': 'M-管理', 'F': 'F-防线', 'T': 'T-触发', 'H': 'H-行为', 'C': 'C-后果',
}

# ============================================================
# 数据加载
# ============================================================

def load_expert(filepath: Path) -> List[Dict]:
    """加载 LabelStudio JSON 格式的专家标注，返回 labels 列表。"""
    with open(filepath, 'r', encoding='utf-8') as f:
        records = json.load(f)
    results = []
    for record in records:
        item = {}
        for ann in record.get('annotations', []):
            for res in ann.get('result', []):
                name = res.get('from_name', '')
                val = res.get('value', {})
                choices = val.get('choices', [])
                if name in ['accident_type', 'severity']:
                    item[name] = choices[0] if choices else ''
                elif name in ['M', 'F', 'T', 'H', 'C']:
                    item[name] = frozenset([c.split('-')[0] for c in choices])
        results.append(item)
    return results


def load_llm(filepath: Path) -> List[Dict]:
    """加载 LLM 标签 JSON，返回 labels 列表。"""
    with open(filepath, 'r', encoding='utf-8') as f:
        records = json.load(f)
    results = []
    for record in records:
        item = {
            'accident_type': record.get('accident_type', ''),
            'severity': record.get('severity', ''),
        }
        codes = record.get('caics_codes', [])
        for dim in ['M', 'F', 'T', 'H', 'C']:
            item[dim] = frozenset([c for c in codes if c.startswith(dim)])
        results.append(item)
    return results


def build_consensus(ea, eb, ec):
    """多数投票构建三专家共识标签。"""
    consensus = []
    for i in range(len(ea)):
        item = {}
        for dim in ['accident_type', 'severity']:
            votes = [ea[i].get(dim, ''), eb[i].get(dim, ''), ec[i].get(dim, '')]
            item[dim] = Counter(votes).most_common(1)[0][0]
        for dim in ['M', 'F', 'T', 'H', 'C']:
            all_codes = ea[i].get(dim, frozenset()) | eb[i].get(dim, frozenset()) | ec[i].get(dim, frozenset())
            cc = set()
            for code in all_codes:
                vote = (1 if code in ea[i].get(dim, frozenset()) else 0) + \
                       (1 if code in eb[i].get(dim, frozenset()) else 0) + \
                       (1 if code in ec[i].get(dim, frozenset()) else 0)
                if vote >= 2:
                    cc.add(code)
            item[dim] = frozenset(cc)
        consensus.append(item)
    return consensus


# ============================================================
# Kappa 计算
# ============================================================

def cohen_kappa_binary(obs1: List[bool], obs2: List[bool]) -> float:
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


def cohen_kappa_multiclass(obs1: List[str], obs2: List[str]) -> float:
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
    if dim in ['accident_type', 'severity']:
        return cohen_kappa_multiclass(
            [r.get(dim, '') for r in e1],
            [r.get(dim, '') for r in e2])
    codes = CODE_LISTS[dim]
    kappas = [cohen_kappa_binary(
        [code in r.get(dim, frozenset()) for r in e1],
        [code in r.get(dim, frozenset()) for r in e2]) for code in codes]
    return np.mean(kappas)


# ============================================================
# 全面评测
# ============================================================

def run_full_evaluation(eA, eB, eC, llm, consensus):
    """运行全部 Kappa 评测。"""
    dims = ['accident_type', 'severity', 'M', 'F', 'T', 'H', 'C']
    evaluators = {'A': eA, 'B': eB, 'C': eC, 'LLM': llm, '共识': consensus}

    # === 1. 人类专家两两 Kappa ===
    human_pairs = [('A', 'B'), ('A', 'C'), ('B', 'C')]
    human_kappa = {}
    for dim in dims:
        human_kappa[dim] = {}
        for e1k, e2k in human_pairs:
            human_kappa[dim][f'{e1k}-{e2k}'] = compute_kappa(evaluators[e1k], evaluators[e2k], dim)

    # 各维度人类 kappa 范围
    human_ranges = {}
    for dim in dims:
        vals = list(human_kappa[dim].values())
        human_ranges[dim] = (min(vals), max(vals))

    # === 2. LLM vs 每位专家 ===
    llm_vs_expert = {}
    for dim in dims:
        llm_vs_expert[dim] = {}
        for ek in ['A', 'B', 'C']:
            llm_vs_expert[dim][f'llm-{ek}'] = compute_kappa(llm, evaluators[ek], dim)

    # === 3. LLM vs 共识 ===
    llm_vs_consensus = {}
    for dim in dims:
        llm_vs_consensus[dim] = compute_kappa(llm, consensus, dim)

    # === 4. 每位专家 vs 共识 ===
    expert_vs_consensus = {}
    for dim in dims:
        expert_vs_consensus[dim] = {}
        for ek in ['A', 'B', 'C']:
            expert_vs_consensus[dim][f'{ek}-共识'] = compute_kappa(evaluators[ek], consensus, dim)

    # === 5. 各编码的具体 Kappa（HFACS 层级下每个编码的二值 Kappa） ===
    code_level_kappa = {}
    for dim in ['M', 'F', 'T', 'H', 'C']:
        code_level_kappa[dim] = {}
        for code in CODE_LISTS[dim]:
            code_level_kappa[dim][code] = {
                'A-B': cohen_kappa_binary(
                    [code in eA[i].get(dim, frozenset()) for i in range(len(eA))],
                    [code in eB[i].get(dim, frozenset()) for i in range(len(eB))]),
                'A-C': cohen_kappa_binary(
                    [code in eA[i].get(dim, frozenset()) for i in range(len(eA))],
                    [code in eC[i].get(dim, frozenset()) for i in range(len(eC))]),
                'B-C': cohen_kappa_binary(
                    [code in eB[i].get(dim, frozenset()) for i in range(len(eB))],
                    [code in eC[i].get(dim, frozenset()) for i in range(len(eC))]),
                'LLM-共识': cohen_kappa_binary(
                    [code in llm[i].get(dim, frozenset()) for i in range(len(llm))],
                    [code in consensus[i].get(dim, frozenset()) for i in range(len(consensus))]),
            }

    # === 6. 总体统计 ===
    overall_human_mean = np.mean([
        np.mean(list(human_kappa[dim].values())) for dim in dims
    ])
    overall_llm_consensus_mean = np.mean(list(llm_vs_consensus.values()))

    return {
        'human_kappa': human_kappa,
        'human_ranges': human_ranges,
        'llm_vs_expert': llm_vs_expert,
        'llm_vs_consensus': llm_vs_consensus,
        'expert_vs_consensus': expert_vs_consensus,
        'code_level_kappa': code_level_kappa,
        'summary': {
            'overall_human_mean': overall_human_mean,
            'overall_llm_consensus_mean': overall_llm_consensus_mean,
            'n_samples': len(eA),
        }
    }


def print_report(result):
    """打印详细评测报告。"""
    dims = ['accident_type', 'severity', 'M', 'F', 'T', 'H', 'C']

    print("=" * 85)
    print("  建筑施工事故 CAICS 标注信度评测报告")
    print(f"  评测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  样本数量: {result['summary']['n_samples']}")
    print("=" * 85)

    # --- 表1: 人类专家两两 Kappa ---
    print("\n" + "─" * 85)
    print("  表1: 人类专家评分者间信度 (Cohen's Kappa)")
    print("─" * 85)
    header = f"  {'维度':<16} {'A-B':>8} {'A-C':>8} {'B-C':>8} {'人类均值':>10} {'人类范围':>16}"
    print(header)
    print("  " + "-" * 72)
    for dim in dims:
        hk = result['human_kappa'][dim]
        vals = [hk['A-B'], hk['A-C'], hk['B-C']]
        mean_val = np.mean(vals)
        lo, hi = min(vals), max(vals)
        print(f"  {DIM_LABELS_CN[dim]:<16} {hk['A-B']:>8.4f} {hk['A-C']:>8.4f} "
              f"{hk['B-C']:>8.4f} {mean_val:>10.4f}  [{lo:.3f}, {hi:.3f}]")

    # --- 表2: LLM vs 专家 ---
    print("\n" + "─" * 85)
    print("  表2: LLM 与各专家的信度 (Cohen's Kappa)")
    print("─" * 85)
    header = f"  {'维度':<16} {'LLM-A':>8} {'LLM-B':>8} {'LLM-C':>8} {'LLM-共识':>10}"
    print(header)
    print("  " + "-" * 58)
    for dim in dims:
        lk = result['llm_vs_expert'][dim]
        lc = result['llm_vs_consensus'][dim]
        print(f"  {DIM_LABELS_CN[dim]:<16} {lk['llm-A']:>8.4f} {lk['llm-B']:>8.4f} "
              f"{lk['llm-C']:>8.4f} {lc:>10.4f}")

    # --- 表3: LLM 与人类范围的比较 ---
    print("\n" + "─" * 85)
    print("  表3: LLM-共识 vs 人类评分者间范围")
    print("─" * 85)
    header = f"  {'维度':<16} {'LLM-共识':>10} {'人类范围':>16} {'人类上限':>10} {'判定':>14}"
    print(header)
    print("  " + "-" * 72)
    all_within = True
    for dim in dims:
        lc = result['llm_vs_consensus'][dim]
        lo, hi = result['human_ranges'][dim]
        if lc <= hi + 0.02:
            verdict = "✅ 在人类范围内"
        elif lc > hi:
            verdict = "⚠ 超出人类上限"
            all_within = False
        else:
            verdict = "⚠ 低于人类下限"
            all_within = False
        print(f"  {DIM_LABELS_CN[dim]:<16} {lc:>10.4f} [{lo:.3f}, {hi:.3f}] {hi:>10.4f} {verdict}")

    print("\n" + "─" * 85)
    print(f"  人类专家总体平均 κ: {result['summary']['overall_human_mean']:.4f}")
    print(f"  LLM-共识总体平均 κ:  {result['summary']['overall_llm_consensus_mean']:.4f}")
    if all_within:
        print("  ✅ LLM 在所有维度的信度均不超出人类评分者间范围")
    else:
        print("  ⚠ 部分维度 LLM 信度超出人类评分者间范围")

    # --- 表4: 各编码级 Kappa ---
    print("\n" + "─" * 85)
    print("  表4: 各 CAICS 编码的评分者间信度（二值 Kappa）")
    print("─" * 85)
    header = f"  {'编码':<6} {'编码名称':<16} {'A-B':>8} {'A-C':>8} {'B-C':>8} {'LLM-共识':>10}"
    print(header)
    print("  " + "-" * 68)
    for dim in ['M', 'F', 'T', 'H', 'C']:
        for code in CODE_LISTS[dim]:
            kk = result['code_level_kappa'][dim][code]
            print(f"  {code:<6} {CODE_NAMES[code]:<16} {kk['A-B']:>8.4f} {kk['A-C']:>8.4f} "
                  f"{kk['B-C']:>8.4f} {kk['LLM-共识']:>10.4f}")

    print("\n" + "=" * 85)
    return result


# ============================================================
# Nature 风格图表生成
# ============================================================

def generate_figures(result, output_dir: Path):
    """生成整套 Nature 风格科研图表。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    from matplotlib.patches import FancyBboxPatch
    import matplotlib.patches as mpatches

    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- 中文字体注册 ----
    import matplotlib.font_manager as fm
    font_path = os.path.expanduser('~/.fonts/NotoSansSC.ttf')
    if os.path.exists(font_path):
        fm.fontManager.addfont(font_path)
        font_prop = fm.FontProperties(fname=font_path)
        font_name = font_prop.get_name()
        # 重建字体缓存
        fm._load_fontmanager(try_read_cache=False)
        fm.fontManager.addfont(font_path)
        font_name = fm.FontProperties(fname=font_path).get_name()
        print(f"  [字体] 使用: {font_name} ({font_path})")
    else:
        font_name = 'DejaVu Sans'
        print(f"  [字体] 警告: 未找到 NotoSansSC.ttf，回退到 DejaVu Sans")

    # ---- Nature 风格全局设置 ----
    # Nature 要求：sans-serif 字体，使用 Noto Sans SC 支持中文
    plt.rcParams.update({
        'font.family': 'sans-serif',
        'font.sans-serif': [font_name, 'Arial', 'Helvetica', 'DejaVu Sans'],
        'font.size': 7,
        'axes.titlesize': 8,
        'axes.labelsize': 7,
        'xtick.labelsize': 6,
        'ytick.labelsize': 6,
        'legend.fontsize': 6,
        'figure.dpi': 300,
        'savefig.dpi': 300,
        'savefig.bbox': 'tight',
        'savefig.pad_inches': 0.05,
        'axes.linewidth': 0.5,
        'xtick.major.width': 0.5,
        'ytick.major.width': 0.5,
        'xtick.major.size': 2.5,
        'ytick.major.size': 2.5,
        'xtick.minor.width': 0.3,
        'ytick.minor.width': 0.3,
        'lines.linewidth': 0.8,
        'lines.markersize': 3.5,
    })

    dims = ['accident_type', 'severity', 'M', 'F', 'T', 'H', 'C']
    dim_labels = ['事故类型', '事故等级', 'M-管理', 'F-防线', 'T-触发', 'H-行为', 'C-后果']

    # ================================================================
    # Figure 1: 人类专家两两 Kappa + LLM-共识 vs 人类范围
    # 单栏图，Nature 标准宽度 89mm
    # ================================================================
    fig, axes = plt.subplots(1, 2, figsize=(7.09, 3.15), dpi=300)
    # Nature 单栏 = 89mm ≈ 3.5in，双栏 = 183mm ≈ 7.2in
    # 这里使用 1.5 栏宽度

    ax1, ax2 = axes

    # --- Panel a: 人类专家两两 Kappa 热力图/分组柱状图 ---
    x = np.arange(len(dims))
    width = 0.25

    ab_vals = [result['human_kappa'][d]['A-B'] for d in dims]
    ac_vals = [result['human_kappa'][d]['A-C'] for d in dims]
    bc_vals = [result['human_kappa'][d]['B-C'] for d in dims]

    # Nature 配色: 柔和、低饱和、色盲友好
    c_ab = '#4477AA'   # 蓝
    c_ac = '#EE7733'   # 橙
    c_bc = '#228833'   # 绿

    bars1 = ax1.bar(x - width, ab_vals, width, color=c_ab, edgecolor='white', linewidth=0.3, label='A–B')
    bars2 = ax1.bar(x, ac_vals, width, color=c_ac, edgecolor='white', linewidth=0.3, label='A–C')
    bars3 = ax1.bar(x + width, bc_vals, width, color=c_bc, edgecolor='white', linewidth=0.3, label='B–C')

    ax1.set_xticks(x)
    ax1.set_xticklabels(dim_labels, rotation=25, ha='right', fontsize=6)
    ax1.set_ylabel("Cohen's Kappa", fontsize=7)
    ax1.set_ylim(0.4, 1.05)
    ax1.axhline(y=0.8, color='#888888', linestyle='--', linewidth=0.4, alpha=0.6)
    ax1.axhline(y=0.6, color='#AAAAAA', linestyle=':', linewidth=0.4, alpha=0.6)
    ax1.text(6.4, 0.805, '0.8', fontsize=5, color='#888888', ha='left', va='bottom')
    ax1.text(6.4, 0.605, '0.6', fontsize=5, color='#AAAAAA', ha='left', va='bottom')
    ax1.legend(loc='lower left', frameon=True, fancybox=False, edgecolor='#CCCCCC',
               fontsize=5.5, handlelength=1.5, handleheight=0.8, borderpad=0.3)
    ax1.set_title('a  人类专家评分者间信度', fontsize=8, fontweight='bold', loc='left', pad=8)
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)
    ax1.tick_params(axis='both', length=2.5, pad=1.5)

    # --- Panel b: LLM-共识 vs 人类范围 ---
    llm_vals = [result['llm_vs_consensus'][d] for d in dims]
    human_lo = [result['human_ranges'][d][0] for d in dims]
    human_hi = [result['human_ranges'][d][1] for d in dims]

    # 绘制人类范围灰色带
    for i in range(len(dims)):
        ax2.fill_between([i - 0.35, i + 0.35], human_lo[i], human_hi[i],
                          color='#DDDDDD', alpha=0.6, edgecolor='none')
    # 人类均值线
    human_mean = [(lo + hi) / 2 for lo, hi in zip(human_lo, human_hi)]
    ax2.plot(x, human_mean, 'D', color='#666666', markersize=4, label='人类均值',
             markeredgecolor='white', markeredgewidth=0.3)

    # LLM-共识 散点
    # 判断是否在人类范围内
    colors_llm = []
    for i, dim in enumerate(dims):
        if human_lo[i] <= llm_vals[i] <= human_hi[i] + 0.02:
            colors_llm.append('#228833')  # 绿 - 在范围内
        else:
            colors_llm.append('#CC3311')  # 红 - 超出范围
    ax2.scatter(x, llm_vals, c=colors_llm, s=35, marker='o', zorder=5,
                edgecolors='white', linewidths=0.4, label='LLM-共识')

    ax2.set_xticks(x)
    ax2.set_xticklabels(dim_labels, rotation=25, ha='right', fontsize=6)
    ax2.set_ylabel("Cohen's Kappa", fontsize=7)
    ax2.set_ylim(0.4, 1.05)
    ax2.set_title('b  LLM-共识 vs 人类评分者间范围', fontsize=8, fontweight='bold', loc='left', pad=8)
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.tick_params(axis='both', length=2.5, pad=1.5)

    # 图例
    legend_elements = [
        mpatches.Patch(color='#DDDDDD', alpha=0.6, label='人类评分者间范围'),
        plt.Line2D([0], [0], marker='D', color='w', markerfacecolor='#666666',
                    markersize=4, label='人类均值', markeredgecolor='white', markeredgewidth=0.3),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='#228833',
                    markersize=4, label='LLM 在范围内', markeredgecolor='white', markeredgewidth=0.3),
    ]
    ax2.legend(handles=legend_elements, loc='lower left', frameon=True, fancybox=False,
               edgecolor='#CCCCCC', fontsize=5.5, handlelength=1.5, borderpad=0.3)

    plt.tight_layout(pad=0.8, w_pad=1.5)
    fig1_path = output_dir / 'fig1_human_llm_kappa.pdf'
    fig.savefig(fig1_path, format='pdf', dpi=300)
    fig.savefig(str(fig1_path).replace('.pdf', '.png'), format='png', dpi=300)
    plt.close(fig)
    print(f"  ✓ Figure 1 已保存: {fig1_path}")

    # ================================================================
    # Figure 2: HFACS 各编码级 Kappa 热力图
    # ================================================================
    all_codes = []
    for dim in ['M', 'F', 'T', 'H', 'C']:
        all_codes.extend(CODE_LISTS[dim])
    code_labels = [f'{c}\n{CODE_NAMES[c][:4]}' for c in all_codes]

    # 构建矩阵: rows=codes, cols=4 (A-B, A-C, B-C, LLM-共识)
    heatmap_data = np.zeros((len(all_codes), 4))
    for i, dim in enumerate(['M', 'F', 'T', 'H', 'C']):
        for j, code in enumerate(CODE_LISTS[dim]):
            row_idx = sum(len(CODE_LISTS[d]) for d in ['M', 'F', 'T', 'H', 'C'][:['M','F','T','H','C'].index(dim)]) + j
            kk = result['code_level_kappa'][dim][code]
            heatmap_data[row_idx, 0] = kk['A-B']
            heatmap_data[row_idx, 1] = kk['A-C']
            heatmap_data[row_idx, 2] = kk['B-C']
            heatmap_data[row_idx, 3] = kk['LLM-共识']

    fig, ax = plt.subplots(figsize=(5.5, 5.0), dpi=300)

    # Nature 风格配色：蓝-白-红 diverging
    from matplotlib.colors import LinearSegmentedColormap
    # 使用柔和 diverging colormap
    colors_div = ['#4477AA', '#EEEEEE', '#EE7733']
    cmap = LinearSegmentedColormap.from_list('nature_div', colors_div, N=100)

    im = ax.imshow(heatmap_data.T, aspect='auto', cmap='RdYlGn', vmin=0.45, vmax=1.0)

    # 标注数值
    for i in range(len(all_codes)):
        for j in range(4):
            val = heatmap_data[i, j]
            text_color = 'white' if val < 0.55 or val > 0.92 else '#333333'
            ax.text(i, j, f'{val:.2f}', ha='center', va='center', fontsize=5.5,
                    color=text_color, fontweight='bold' if val > 0.9 else 'normal')

    ax.set_xticks(range(len(all_codes)))
    ax.set_xticklabels(code_labels, rotation=50, ha='right', fontsize=5.5)
    ax.set_yticks([0, 1, 2, 3])
    ax.set_yticklabels(['A–B', 'A–C', 'B–C', 'LLM-共识'], fontsize=6)
    ax.set_title('各 CAICS 编码的评分者间信度', fontsize=8, fontweight='bold', loc='left', pad=8)

    # 添加维度分隔线
    cum = 0
    for dim in ['M', 'F', 'T', 'H']:
        cum += len(CODE_LISTS[dim])
        ax.axvline(x=cum - 0.5, color='#333333', linewidth=0.6, linestyle='-')

    cbar = plt.colorbar(im, ax=ax, fraction=0.02, pad=0.02, aspect=30)
    cbar.set_label("Cohen's Kappa", fontsize=6, labelpad=2)
    cbar.ax.tick_params(labelsize=5, length=2, pad=1)

    plt.tight_layout(pad=0.8)
    fig2_path = output_dir / 'fig2_code_level_heatmap.pdf'
    fig.savefig(fig2_path, format='pdf', dpi=300)
    fig.savefig(str(fig2_path).replace('.pdf', '.png'), format='png', dpi=300)
    plt.close(fig)
    print(f"  ✓ Figure 2 已保存: {fig2_path}")

    # ================================================================
    # Figure 3: LLM vs 每位专家 对比图 + 专家vs共识
    # ================================================================
    fig, axes = plt.subplots(1, 2, figsize=(7.09, 3.15), dpi=300)
    ax1, ax2 = axes

    # --- Panel a: LLM vs 各专家 ---
    x = np.arange(len(dims))
    width = 0.25
    for i, (ek, color, hatch) in enumerate([
        ('A', '#4477AA', ''), ('B', '#EE7733', ''), ('C', '#228833', '')
    ]):
        vals = [result['llm_vs_expert'][d][f'llm-{ek}'] for d in dims]
        bars = ax1.bar(x + (i - 1) * width, vals, width, color=color, edgecolor='white',
                       linewidth=0.3, label=f'LLM–{ek}', alpha=0.85)
    ax1.set_xticks(x)
    ax1.set_xticklabels(dim_labels, rotation=25, ha='right', fontsize=6)
    ax1.set_ylabel("Cohen's Kappa", fontsize=7)
    ax1.set_ylim(0.4, 1.05)
    ax1.legend(loc='lower left', frameon=True, fancybox=False, edgecolor='#CCCCCC',
               fontsize=5.5, handlelength=1.5, borderpad=0.3)
    ax1.set_title('a  LLM 与各专家的一致性', fontsize=8, fontweight='bold', loc='left', pad=8)
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)
    ax1.tick_params(axis='both', length=2.5, pad=1.5)

    # --- Panel b: 各专家 vs 共识 ---
    x = np.arange(len(dims))
    width = 0.25
    for i, (ek, color) in enumerate([('A', '#4477AA'), ('B', '#EE7733'), ('C', '#228833')]):
        vals = [result['expert_vs_consensus'][d][f'{ek}-共识'] for d in dims]
        ax2.bar(x + (i - 1) * width, vals, width, color=color, edgecolor='white',
                linewidth=0.3, label=f'{ek}–共识', alpha=0.85)
    ax2.set_xticks(x)
    ax2.set_xticklabels(dim_labels, rotation=25, ha='right', fontsize=6)
    ax2.set_ylabel("Cohen's Kappa", fontsize=7)
    ax2.set_ylim(0.4, 1.05)
    ax2.legend(loc='lower left', frameon=True, fancybox=False, edgecolor='#CCCCCC',
               fontsize=5.5, handlelength=1.5, borderpad=0.3)
    ax2.set_title('b  各专家与共识标签的一致性', fontsize=8, fontweight='bold', loc='left', pad=8)
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.tick_params(axis='both', length=2.5, pad=1.5)

    plt.tight_layout(pad=0.8, w_pad=1.5)
    fig3_path = output_dir / 'fig3_llm_expert_consensus.pdf'
    fig.savefig(fig3_path, format='pdf', dpi=300)
    fig.savefig(str(fig3_path).replace('.pdf', '.png'), format='png', dpi=300)
    plt.close(fig)
    print(f"  ✓ Figure 3 已保存: {fig3_path}")

    # ================================================================
    # Figure 4: 综合仪表盘 —— 所有配对在所有维度的信度总览
    # ================================================================
    fig, ax = plt.subplots(figsize=(7.09, 4.5), dpi=300)

    # 构建完整矩阵: 7 dims × 7 rater pairs
    pairs = ['A-B', 'A-C', 'B-C', '专家均值', 'LLM-共识', 'LLM-A', 'LLM-B', 'LLM-C']
    matrix = np.zeros((len(dims), len(pairs)))
    for i, dim in enumerate(dims):
        hk = result['human_kappa'][dim]
        matrix[i, 0] = hk['A-B']
        matrix[i, 1] = hk['A-C']
        matrix[i, 2] = hk['B-C']
        matrix[i, 3] = np.mean([hk['A-B'], hk['A-C'], hk['B-C']])
        matrix[i, 4] = result['llm_vs_consensus'][dim]
        lk = result['llm_vs_expert'][dim]
        matrix[i, 5] = lk['llm-A']
        matrix[i, 6] = lk['llm-B']
        matrix[i, 7] = lk['llm-C']

    im = ax.imshow(matrix, aspect='auto', cmap='RdYlGn', vmin=0.45, vmax=1.0)

    for i in range(len(dims)):
        for j in range(len(pairs)):
            val = matrix[i, j]
            # 根据值选择文本颜色
            text_color = 'white' if val < 0.55 or val > 0.92 else '#222222'
            ax.text(j, i, f'{val:.3f}', ha='center', va='center', fontsize=5.5,
                    color=text_color, fontweight='bold')

    ax.set_xticks(range(len(pairs)))
    ax.set_xticklabels(pairs, rotation=35, ha='right', fontsize=6)
    ax.set_yticks(range(len(dims)))
    ax.set_yticklabels(dim_labels, fontsize=6)
    ax.set_title('CAICS 标注信度总览', fontsize=8, fontweight='bold', loc='left', pad=8)

    # 分隔线
    ax.axvline(x=2.5, color='#333333', linewidth=0.8)
    ax.axvline(x=4.5, color='#333333', linewidth=0.8)

    # 区域标注
    ax.text(1, -0.9, '人类评分者间', ha='center', fontsize=5.5, color='#555555')
    ax.text(4, -0.9, 'LLM', ha='center', fontsize=5.5, color='#555555')
    ax.text(6, -0.9, 'LLM vs 专家', ha='center', fontsize=5.5, color='#555555')

    cbar = plt.colorbar(im, ax=ax, fraction=0.015, pad=0.02, aspect=35)
    cbar.set_label("Cohen's Kappa", fontsize=6, labelpad=2)
    cbar.ax.tick_params(labelsize=5, length=2, pad=1)

    plt.tight_layout(pad=1.2)
    fig4_path = output_dir / 'fig4_kappa_overview.pdf'
    fig.savefig(fig4_path, format='pdf', dpi=300)
    fig.savefig(str(fig4_path).replace('.pdf', '.png'), format='png', dpi=300)
    plt.close(fig)
    print(f"  ✓ Figure 4 已保存: {fig4_path}")

    # ================================================================
    # 输出 JSON 数据用于其他工具
    # ================================================================
    chart_data = {
        'dims': dims,
        'dim_labels': dim_labels,
        'human_ab': ab_vals,
        'human_ac': ac_vals,
        'human_bc': bc_vals,
        'llm_consensus': llm_vals,
        'human_lo': human_lo,
        'human_hi': human_hi,
        'code_level': {
            dim: {
                code: {
                    k: float(v) for k, v in result['code_level_kappa'][dim][code].items()
                }
                for code in CODE_LISTS[dim]
            }
            for dim in ['M', 'F', 'T', 'H', 'C']
        },
    }
    chart_data_path = output_dir / 'kappa_chart_data.json'
    with open(chart_data_path, 'w', encoding='utf-8') as f:
        json.dump(chart_data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 图表数据: {chart_data_path}")

    return {
        'fig1': str(fig1_path),
        'fig2': str(fig2_path),
        'fig3': str(fig3_path),
        'fig4': str(fig4_path),
        'chart_data': str(chart_data_path),
    }


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Kappa 评测 + Nature 风格科研图表")
    parser.add_argument("--report", action="store_true", help="仅打印报告")
    parser.add_argument("--figs", action="store_true", help="仅生成图表")
    args = parser.parse_args()

    do_report = args.report or not args.figs
    do_figs = args.figs or not args.report

    # 加载数据
    print("📂 加载标注数据...")
    eA = load_expert(GROUND_TRUTH_DIR / "expertA.json")
    eB = load_expert(GROUND_TRUTH_DIR / "expertB.json")
    eC = load_expert(GROUND_TRUTH_DIR / "expertC.json")
    llm = load_llm(GROUND_TRUTH_DIR / "gt_llm_labels_100.json")
    consensus = build_consensus(eA, eB, eC)
    print(f"  Expert A: {len(eA)} 条, Expert B: {len(eB)} 条, Expert C: {len(eC)} 条")
    print(f"  LLM: {len(llm)} 条, 共识: {len(consensus)} 条")

    # 运行评测
    print("\n🔬 运行 Kappa 评测...")
    result = run_full_evaluation(eA, eB, eC, llm, consensus)

    if do_report:
        print_report(result)

    if do_figs:
        print("\n📊 生成 Nature 风格科研图表...")
        fig_paths = generate_figures(result, FIGURES_DIR)
        print(f"\n✅ 图表已生成:")
        for k, v in fig_paths.items():
            if k.startswith('fig'):
                print(f"   {k}: {v}")

    # 保存完整评测结果
    eval_result_path = GROUND_TRUTH_DIR / "kappa_full_evaluation.json"
    eval_output = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'n_samples': result['summary']['n_samples'],
        'human_kappa': {
            dim: {k: float(v) for k, v in result['human_kappa'][dim].items()}
            for dim in result['human_kappa']
        },
        'human_ranges': {
            dim: [float(v) for v in result['human_ranges'][dim]]
            for dim in result['human_ranges']
        },
        'llm_vs_expert': {
            dim: {k: float(v) for k, v in result['llm_vs_expert'][dim].items()}
            for dim in result['llm_vs_expert']
        },
        'llm_vs_consensus': {dim: float(v) for dim, v in result['llm_vs_consensus'].items()},
        'expert_vs_consensus': {
            dim: {k: float(v) for k, v in result['expert_vs_consensus'][dim].items()}
            for dim in result['expert_vs_consensus']
        },
        'summary': result['summary'],
    }
    with open(eval_result_path, 'w', encoding='utf-8') as f:
        json.dump(eval_output, f, ensure_ascii=False, indent=2)
    print(f"\n📋 评测结果已保存: {eval_result_path}")


if __name__ == "__main__":
    main()
