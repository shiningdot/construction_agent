#!/usr/bin/env python3
"""将论文中的图占位符替换为实际图片引用"""
import re
from pathlib import Path

# 本仓库根（脚本在 scripts/ 下，上溯一级），worktree 与主仓库都成立
ROOT = Path(__file__).resolve().parents[1]

mapping = {
    '图1占位：技术路线图英文版，Figure 1': '![](figures/fig1_architecture.png)',
    '图2占位：CAICS 五层树状图 + 示例证据链，Figure 2': '![](figures/fig2_caics_tree.png)',
    '图4占位：样本关系流程图 Figure 4': '![](figures/fig4_sampling.png)',
    '图5占位：门控效应诊断图 — v1 vs v2 F1/Recall/M1 recall/空编码率，Figure 5': '![](figures/fig5_gatekeeping.png)',
    '图6占位：Kappa 森林图 Figure 6': '![](figures/fig6_kappa.png)',
}

files = [
    str(ROOT / 'docs' / 'paper' / 'eswa_paper.md'),
    str(ROOT / 'docs' / 'paper' / 'eswa_paper_zh.md'),
]

for path in files:
    with open(path, encoding='utf-8') as f:
        text = f.read()
    # 图3 占位包含长括号内容，用正则
    text = re.sub(r'\*\*\[图3占位[^\]]*\]\*\*', '![](figures/fig3_code_dist.png)', text)
    for old, new in mapping.items():
        old_full = f'**[{old}]**'
        text = text.replace(old_full, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    remaining = len(re.findall(r'占位', text))
    print(f'{path}: 替换完成, 剩余占位 {remaining} 处')
