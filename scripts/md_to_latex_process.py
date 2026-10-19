#!/usr/bin/env python3
"""过程记录 MD → LaTeX → PDF（中文，CJK 字体）"""
import re, subprocess, os
from pathlib import Path

# 本仓库根：脚本在 scripts/ 下，上溯一级即仓库根（worktree 与主仓库都成立）
ROOT = Path(__file__).resolve().parents[1]

SRC = str(ROOT / 'docs' / 'paper' / 'paper_creation_process.md')
OUT = str(ROOT / 'docs' / 'paper' / 'paper_creation_process.tex')

text = open(SRC, encoding='utf-8').read()


def esc(s):
    s = s.replace('\\', r'\textbackslash{}').replace('%', r'\%').replace('&', r'\&')
    s = s.replace('#', r'\#').replace('_', r'\_').replace('$', r'\$').replace('^', r'\^{}')
    s = s.replace('→', r'$\rightarrow$').replace('×', r'$\times$').replace('κ', r'$\kappa$')
    s = s.replace('Δ', r'$\Delta$').replace('≥', r'$\geq$').replace('—', '---').replace('–', '--')
    s = s.replace('’', "'").replace('“', "``").replace('”', "''")
    s = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', s)
    # 中文汉字 + 全角标点（，。：；（）【】《》、等）用 CJK 字体
    s = re.sub(r'([一-鿿　-〿＀-￯]+)', r'{\\cjkfont \1}', s)
    return s


def md_table(lines):
    rows = [[esc(c.strip()) for c in ln.strip().strip('|').split('|')] for ln in lines]
    if len(rows) < 2:
        return ''
    header = rows[0]
    ncol = len(header)
    spec = 'l' * ncol
    out = [r'\begin{table}[htbp]', r'\centering', r'\small',
           r'\begin{tabular}{' + spec + r'}', r'\hline',
           ' & '.join(header) + r' \\', r'\hline']
    for r in rows[2:]:
        r = r + [''] * (ncol - len(r))
        out.append(' & '.join(r[:ncol]) + r' \\')
    out += [r'\hline', r'\end{tabular}', r'\end{table}']
    return '\n'.join(out)


lines = text.split('\n')
out = [r"""\documentclass[11pt]{article}
\usepackage[margin=2.2cm]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{booktabs}
\usepackage[hidelinks]{hyperref}
\usepackage{fontspec}
\newfontfamily\cjkfont{Noto Sans SC}[Scale=0.92]
\title{CAIEMS 论文创建过程记录}
\author{人机协作存档（academic-pipeline Stage 6）}
\date{2026-08-31}
\begin{document}
\maketitle
"""]

i = 0
while i < len(lines):
    s = lines[i].strip()
    if s == '' or s == '---':
        i += 1
        continue
    if s.startswith('|') and i + 1 < len(lines) and lines[i + 1].strip().startswith('|'):
        tbl = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            tbl.append(lines[i])
            i += 1
        out.append(md_table(tbl))
        continue
    if s.startswith('```'):
        out.append(r'\begin{verbatim}')
        i += 1
        while i < len(lines) and not lines[i].strip().startswith('```'):
            out.append(lines[i])
            i += 1
        i += 1
        out.append(r'\end{verbatim}')
        continue
    if s.startswith('# '):
        out.append(r'\maketitle')
        i += 1
        continue
    if s.startswith('## '):
        out.append(r'\section{' + esc(s[3:].strip()) + '}')
        i += 1
        continue
    if s.startswith('### '):
        out.append(r'\subsection{' + esc(s[4:].strip()) + '}')
        i += 1
        continue
    if s.startswith('> '):
        out.append(r'\begin{quote}' + esc(s[2:].strip()) + r'\end{quote}' + '\n')
        i += 1
        continue
    if re.match(r'^\d+\. ', s):
        out.append(r'\begin{enumerate}')
        while i < len(lines) and re.match(r'^\d+\. ', lines[i].strip()):
            out.append(r'\item ' + esc(re.sub(r'^\d+\. ', '', lines[i].strip())))
            i += 1
        out.append(r'\end{enumerate}')
        continue
    if s.startswith('- '):
        out.append(r'\begin{itemize}')
        while i < len(lines) and lines[i].strip().startswith('- '):
            out.append(r'\item ' + esc(lines[i].strip()[2:]))
            i += 1
        out.append(r'\end{itemize}')
        continue
    out.append(esc(s))
    out.append('')
    i += 1

out.append(r'\end{document}')
tex = '\n'.join(out)
open(OUT, 'w', encoding='utf-8').write(tex)
print(f'TeX 已生成: {OUT}')

# tectonic 编译
tectonic = '/home/suntianhao/TEM/tectonic'
pdf_dir = os.path.dirname(OUT)
r = subprocess.run([tectonic, OUT], capture_output=True, text=True, cwd=str(ROOT))
print('tectonic 退出码:', r.returncode)
for ln in r.stderr.split('\n')[-6:]:
    if 'error' in ln.lower() or 'warning' in ln.lower() and 'Overfull' not in ln:
        print(ln[:160])
pdf = OUT.replace('.tex', '.pdf')
print('PDF:', pdf if os.path.exists(pdf) else '未生成')
