#!/usr/bin/env python3
"""V2 四格式构建：EN DOCX + EN PDF + ZH DOCX + ZH PDF
- DOCX: python-docx（中文设宋体东亚字体）
- PDF: LaTeX + tectonic（CJK 用 Noto Sans SC）
- 图片占位符 [图X占位...] 渲染为可见占位框"""
import re, os, subprocess
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH

BASE = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper_v2'
TECTONIC = '/home/suntianhao/TEM/tectonic'
FIGDIR = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper_v2/figures'
os.makedirs(FIGDIR, exist_ok=True)

SRC = {
    'en': f'{BASE}/eswa_paper_v2.md',
    'zh': f'{BASE}/eswa_paper_v2_zh.md',
}

def clean(text):
    text = re.sub(r'<!--[\s\S]*?-->', '', text)
    text = re.sub(r'^> .*\n', '', text, flags=re.M)
    return text

def cjk_runs(para, s, font_zh='宋体'):
    """按中文片段切分并设置东亚字体"""
    parts = re.split(r'([\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+)', s)
    for part in parts:
        if not part:
            continue
        r = para.add_run(part)
        if re.match(r'[\u4e00-\u9fff]', part):
            r.font.name = font_zh
            r._element.rPr.rFonts.set(qn('w:eastAsia'), font_zh)

# ══════════════ DOCX ══════════════
def build_docx(src, out, is_zh):
    text = clean(open(src, encoding='utf-8').read())
    doc = Document()
    st = doc.styles['Normal']
    st.font.name = 'Times New Roman'
    st.font.size = Pt(10.5)
    if is_zh:
        st.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')

    lines = text.split('\n')
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s == '' or s == '---':
            i += 1
            continue
        if s.startswith('|') and i + 1 < len(lines) and lines[i+1].strip().startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            header, body = rows[0], rows[2:]
            table = doc.add_table(rows=1+len(body), cols=len(header))
            table.style = 'Light Grid Accent 1'
            for j, hcell in enumerate(header):
                cell = table.rows[0].cells[j]
                cell.text = hcell.replace('**', '')
                for run in cell.paragraphs[0].runs:
                    run.bold = True
            for ri, r in enumerate(body, start=1):
                r = r + [''] * (len(header) - len(r))
                for j, c in enumerate(r[:len(header)]):
                    table.rows[ri].cells[j].text = c.replace('**', '')
            continue
        if s.startswith('# '):
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run(s[2:].strip()); r.bold = True; r.font.size = Pt(17)
            i += 1
            continue
        if s.startswith('## '):
            p = doc.add_heading(level=1)
            _add_runs(p, s[3:].strip(), is_zh)
            for run in p.runs:
                run.font.color.rgb = RGBColor(0, 0, 0)
                run.font.size = Pt(14)
            i += 1
            continue
        if s.startswith('### '):
            p = doc.add_heading(level=2)
            _add_runs(p, s[4:].strip(), is_zh)
            for run in p.runs:
                run.font.color.rgb = RGBColor(0, 0, 0)
            i += 1
            continue
        if re.match(r'^\[图\d+占位', s):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.3)
            r = p.add_run('▢ ' + s)
            r.font.color.rgb = RGBColor(0x7F, 0x8C, 0x8D)
            r.italic = True
            i += 1
            continue
        if s.startswith('**Figure') or s.startswith('**图'):
            p = doc.add_paragraph()
            _add_runs(p, s, is_zh)
            for run in p.runs:
                run.bold = True
            i += 1
            continue
        if s.startswith('- '):
            while i < len(lines) and lines[i].strip().startswith('- '):
                p = doc.add_paragraph(style='List Bullet')
                _add_runs(p, lines[i].strip()[2:], is_zh)
                i += 1
            continue
        if re.match(r'^[A-Z][\wÀ-ſ., \-]+\(\d{4}', s) and 'References' in ''.join(lines[max(0,i-3):i]):
            pass  # 参考文献条目按普通段落
        p = doc.add_paragraph()
        _add_runs(p, s, is_zh)
        i += 1
    doc.save(out)
    print(f'DOCX: {out}')

def _add_runs(p, s, is_zh):
    tokens = re.split(r'(\*\*.+?\*\*|\*[^*]+?\*)', s)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith('**') and tok.endswith('**'):
            if is_zh:
                r = p.add_run(); r.bold = True
                cjk_sub(r, tok[2:-2], '宋体')
            else:
                r = p.add_run(tok[2:-2]); r.bold = True
        elif tok.startswith('*') and tok.endswith('*') and len(tok) > 2:
            r = p.add_run(tok[1:-1]); r.italic = True
        else:
            if is_zh:
                cjk_runs(p, tok)
            else:
                p.add_run(tok)

def cjk_sub(r, s, font_zh):
    r.text = s
    r.font.name = font_zh
    r._element.rPr.rFonts.set(qn('w:eastAsia'), font_zh)

# ══════════════ LaTeX ══════════════
def esc(s):
    s = s.replace('%', r'\%').replace('&', r'\&').replace('#', r'\#').replace('_', r'\_')
    s = s.replace('κ', r'$\kappa$').replace('Δ', r'$\Delta$')
    s = s.replace('→', r'$\rightarrow$').replace('×', r'$\times$')
    s = s.replace('≈', r'$\approx$').replace('–', '--').replace('—', '---')
    s = s.replace('’', "'").replace('“', "``").replace('”', "''")
    s = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', s)
    s = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\\textit{\1}', s)
    s = re.sub(r'([一-鿿　-〿＀-￯]+)', r'{\\cjkfont \1}', s)
    return s

def build_latex(src, out_tex, is_zh):
    text = clean(open(src, encoding='utf-8').read())
    lines = text.split('\n')
    out = [r"""\documentclass[11pt]{article}
\usepackage[margin=2.4cm]{geometry}
\usepackage{graphicx}
\usepackage{amsmath,amssymb}
\usepackage{booktabs}
\usepackage[hidelinks]{hyperref}
\usepackage{microtype}
\usepackage{float}
\usepackage{fontspec}
\newfontfamily\cjkfont{Noto Sans SC}[Scale=0.92]
""" + (r'\title{CAIEMS：面向施工事故叙述结构化处理的探索性 LLM 数据工程管道}' if is_zh
       else r'\title{CAIEMS: An Exploratory LLM-Driven Pipeline for Structuring Construction Accident Narratives into a Queryable Database}')
     + r"""
\author{Tianhao Sun\textsuperscript{1}, [Supervisor name]\textsuperscript{1,*}}
\date{2026-09-14}
\begin{document}
\maketitle
"""]
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s == '' or s == '---':
            i += 1
            continue
        if s.startswith('|') and i + 1 < len(lines) and lines[i+1].strip().startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append([esc(c.strip()) for c in lines[i].strip().strip('|').split('|')])
                i += 1
            header = rows[0]
            ncol = len(header)
            out.append(r'\begin{table}[htbp]' + '\n' + r'\centering' + '\n' + r'\small' + '\n'
                       + r'\begin{tabular}{' + 'l' * ncol + r'}' + '\n' + r'\hline' + '\n'
                       + ' & '.join(header) + r' \\' + '\n' + r'\hline')
            for r in rows[2:]:
                r = r + [''] * (ncol - len(r))
                out.append(' & '.join(r[:ncol]) + r' \\')
            out.append(r'\hline' + '\n' + r'\end{tabular}' + '\n' + r'\end{table}')
            continue
        if s.startswith('# '):
            i += 1
            continue
        if s.startswith('## '):
            sec = s[3:].strip()
            if sec.startswith('References') or sec.startswith('参考文献'):
                out.append(r'\section*{' + esc(sec) + '}' + '\n' + r'\begin{enumerate}')
                i += 1
                while i < len(lines) and lines[i].strip() == '':
                    i += 1
                while i < len(lines) and lines[i].strip() != '':
                    ref = lines[i].strip()
                    if not re.match(r'^(\*V2|V2)', ref):
                        out.append(r'\item ' + esc(ref))
                    i += 1
                out.append(r'\end{enumerate}')
                continue
            if sec.startswith('Appendix') or sec.startswith('附录'):
                out.append(r'\appendix')
            out.append(r'\section{' + esc(sec) + '}')
            i += 1
            continue
        if s.startswith('### '):
            out.append(r'\subsection{' + esc(s[4:].strip()) + '}')
            i += 1
            continue
        if re.match(r'^\[图\d+占位', s):
            out.append(r'\begin{center}\fbox{\parbox{0.8\textwidth}{\color{gray}' + esc(s) + r'}}\end{center}')
            out.append(r'\usepackage{xcolor}' if False else '')
            i += 1
            continue
        if s.startswith('**Figure') or s.startswith('**图'):
            out.append(r'\textbf{' + esc(s.strip('*')) + '}')
            i += 1
            continue
        if s.startswith('*Notes.*') or s.startswith('*注。*'):
            out.append(r'\textit{' + esc(s) + r'}\\')
            i += 1
            continue
        if s.startswith('- '):
            out.append(r'\begin{itemize}')
            while i < len(lines) and lines[i].strip().startswith('- '):
                out.append(r'\item ' + esc(lines[i].strip()[2:]))
                i += 1
            out.append(r'\end{itemize}')
            continue
        para = []
        while i < len(lines) and lines[i].strip() not in ('', '---') \
                and not lines[i].strip().startswith(('#', '|', '**Figure', '**图', '- ')) \
                and not re.match(r'^\[图\d+占位', lines[i].strip()):
            para.append(lines[i])
            i += 1
        if para:
            out.append(esc(' '.join(para).strip()))
            out.append('')
        else:
            i += 1
    out.append(r'\end{document}')
    tex = '\n'.join(out)
    # 补充 xcolor
    tex = tex.replace(r'\usepackage{fontspec}', r'\usepackage{fontspec}' + '\n' + r'\usepackage{xcolor}')
    open(out_tex, 'w', encoding='utf-8').write(tex)
    print(f'TeX: {out_tex}')
    r = subprocess.run([TECTONIC, out_tex], capture_output=True, text=True,
                       cwd='/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod')
    pdf = out_tex.replace('.tex', '.pdf')
    ok = os.path.exists(pdf)
    if not ok:
        for ln in r.stderr.split('\n')[-8:]:
            if 'error' in ln.lower():
                print(ln[:200])
    print(f'PDF: {pdf} {"✓" if ok else "✗ 失败"}')
    return ok

if __name__ == '__main__':
    build_docx(SRC['en'], f'{BASE}/eswa_paper_v2_en.docx', False)
    build_docx(SRC['zh'], f'{BASE}/eswa_paper_v2_zh.docx', True)
    build_latex(SRC['en'], f'{BASE}/eswa_paper_v2_en.tex', False)
    build_latex(SRC['zh'], f'{BASE}/eswa_paper_v2_zh.tex', True)
    print('全部完成')
