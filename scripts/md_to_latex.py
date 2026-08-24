#!/usr/bin/env python3
"""Markdown(编号引用版) → LaTeX 投稿源文件"""
import re

SRC = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper_numbered.md'
OUT = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper.tex'

text = open(SRC, encoding='utf-8').read()

# 去掉工作稿注记与 HTML 注释
text = re.sub(r'^> .*\n', '', text, flags=re.M)
text = re.sub(r'<!--[\s\S]*?-->', '', text)

# 图片路径
FIGDIR = 'figures/'

def md_table_to_latex(lines):
    """Markdown 表格 → LaTeX tabular"""
    rows = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip('|').split('|')]
        rows.append(cells)
    if len(rows) < 2:
        return ''
    header, sep = rows[0], rows[1]
    ncol = len(header)
    aligns = []
    for s in sep:
        s = s.strip()
        if s.startswith(':') and s.endswith(':'):
            aligns.append('c')
        elif s.endswith(':'):
            aligns.append('r')
        elif s.startswith(':'):
            aligns.append('l')
        else:
            aligns.append('c')
    aligns = aligns[:ncol] + ['l'] * (ncol - len(aligns))
    spec = ''.join(aligns)
    body_rows = rows[2:]
    out_lines = [r'\begin{table}[htbp]', r'\centering', r'\small',
                 r'\begin{tabular}{' + spec + r'}', r'\hline']
    out_lines.append(' & '.join(header) + r' \\' + '\n' + r'\hline')
    for r in body_rows:
        r = r + [''] * (ncol - len(r))
        r = [cell.replace('**', '') for cell in r]
        out_lines.append(' & '.join(r) + r' \\')
    out_lines.append(r'\hline' + '\n' + r'\end{tabular}')
    out_lines.append(r'\end{table}')
    return '\n'.join(out_lines)


def inline(s):
    # 数学与特殊字符
    s = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', s)
    s = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\\textit{\1}', s)
    s = s.replace('%', r'\%').replace('&', r'\&').replace('#', r'\#')
    s = s.replace('κ', r'$\kappa$').replace('Δ', r'$\Delta$')
    # 保留 $...$ 数学
    return s


out = []
lines = text.split('\n')
i = 0
in_table = False
table_lines = []
preamble = r"""\documentclass[11pt]{article}
\usepackage[margin=2.5cm]{geometry}
\usepackage{graphicx}
\usepackage{amsmath,amssymb}
\usepackage{booktabs}
\usepackage[hidelinks]{hyperref}
\usepackage{microtype}
\usepackage{float}
\title{CAIEMS: An LLM-Driven Pipeline for Construction Accident Information Extraction, Causal Coding, and Online Database Construction}
\author{Tianhao Sun\textsuperscript{1}, [Supervisor name]\textsuperscript{1,*}}
\date{}
\begin{document}
\maketitle
"""
out.append(preamble)

while i < len(lines):
    ln = lines[i]
    s = ln.strip()
    if s == '' or s == '---':
        i += 1
        continue
    if s.startswith('|') and i + 1 < len(lines) and lines[i + 1].strip().startswith('|'):
        table_lines = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            table_lines.append(lines[i])
            i += 1
        out.append(md_table_to_latex(table_lines))
        continue
    if s.startswith('# '):
        # 标题已在 preamble
        i += 1
        continue
    if s.startswith('## '):
        if s.startswith('## Appendix'):
            out.append(r'\appendix')
        elif s.startswith('## References'):
            out.append(r'\section*{References}' + '\n' + r'\begin{enumerate}')
            i += 1
            # 收集参考文献条目
            while i < len(lines) and lines[i].strip() != '':
                ref = lines[i].strip()
                if ref.startswith('['):
                    m = re.match(r'^\[(\d+)\]\s*(.*)', ref)
                    if m:
                        out.append(r'\item ' + inline(m.group(2)))
                i += 1
            out.append(r'\end{enumerate}')
            continue
        else:
            sec = s[3:].strip()
            sec = sec.replace('## ', '')
            out.append(r'\section{' + inline(sec) + '}')
            i += 1
            continue
    if s.startswith('### '):
        out.append(r'\subsection{' + inline(s[4:].strip()) + '}')
        i += 1
        continue
    if s.startswith('!['):
        m = re.match(r'^!\[.*?\]\(([^)]+)\)', s)
        if m:
            fname = m.group(1).split('/')[-1].replace('.png', '')
            out.append(r'\begin{figure}[H]' + '\n' + r'\centering' + '\n'
                       + r'\includegraphics[width=0.85\textwidth]{' + FIGDIR + fname + '.png}' + '\n'
                       + r'\end{figure}')
            i += 1
            continue
    if s.startswith('**Figure'):
        out.append(r'\textbf{' + inline(s.strip('*')) + '}')
        i += 1
        continue
    if s.startswith('**Table'):
        out.append(r'\textbf{' + inline(s.strip('*')) + '}')
        i += 1
        continue
    if s.startswith('*Notes.*') or s.startswith('*注'):
        out.append(r'\textit{' + inline(s) + r'}\\')
        i += 1
        continue
    if s.startswith('- ') or s.startswith('* '):
        # 列表项
        out.append(r'\begin{itemize}')
        while i < len(lines) and (lines[i].strip().startswith('- ') or lines[i].strip().startswith('* ')):
            item = lines[i].strip()[2:]
            item = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', item)
            out.append(r'\item ' + inline(item))
            i += 1
        out.append(r'\end{itemize}')
        continue
    # 普通段落
    para = []
    while i < len(lines) and lines[i].strip() not in ('', '---') \
            and not lines[i].strip().startswith(('#', '|', '!', '**Figure', '**Table', '- ', '* ')):
        para.append(lines[i])
        i += 1
    if para:
        out.append(inline(' '.join(para).strip()))
        out.append('')
    else:
        i += 1

out.append(r'\end{document}')
tex = '\n'.join(out)
tex = tex.replace(r'\item \\item', r'\item')
open(OUT, 'w', encoding='utf-8').write(tex)
print(f'LaTeX 已生成: {OUT} ({len(tex)} 字符)')
