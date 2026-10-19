#!/usr/bin/env python3
"""Markdown(编号引用版) → DOCX（投稿工作稿）"""
import re
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

SRC = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper_numbered.md'
OUT = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper.docx'
FIGDIR = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/figures/'

text = open(SRC, encoding='utf-8').read()
text = re.sub(r'^> .*\n', '', text, flags=re.M)
text = re.sub(r'<!--[\s\S]*?-->', '', text)

doc = Document()
style = doc.styles['Normal']
style.font.name = 'Times New Roman'
style.font.size = Pt(11)

def add_inline_runs(p, s):
    """粗体/斜体/数学内联处理"""
    # 切分 **bold** 与 *italic*
    tokens = re.split(r'(\*\*.+?\*\*|\*[^*]+?\*)', s)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith('**') and tok.endswith('**'):
            r = p.add_run(tok[2:-2]); r.bold = True
        elif tok.startswith('*') and tok.endswith('*') and len(tok) > 2:
            r = p.add_run(tok[1:-1]); r.italic = True
        else:
            p.add_run(tok)

lines = text.split('\n')
i = 0
while i < len(lines):
    s = lines[i].strip()
    if s == '' or s == '---':
        i += 1
        continue
    if s.startswith('|') and i + 1 < len(lines) and lines[i + 1].strip().startswith('|'):
        rows = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
            i += 1
        header, body_rows = rows[0], rows[2:]
        table = doc.add_table(rows=1 + len(body_rows), cols=len(header))
        table.style = 'Light Grid Accent 1'
        for j, h in enumerate(header):
            cell = table.rows[0].cells[j]
            cell.text = h.replace('**', '')
            for run in cell.paragraphs[0].runs:
                run.bold = True
        for ri, r in enumerate(body_rows, start=1):
            r = r + [''] * (len(header) - len(r))
            for j, c in enumerate(r[:len(header)]):
                table.rows[ri].cells[j].text = c.replace('**', '')
        continue
    if s.startswith('# '):
        title = s[2:].strip()
        p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(title); r.bold = True; r.font.size = Pt(18)
        i += 1
        continue
    if s.startswith('## '):
        sec = s[3:].strip()
        level = 1 if not sec.startswith('Appendix') and sec != 'References' else 1
        p = doc.add_heading(level=level)
        add_inline_runs(p, sec)
        for run in p.runs:
            run.font.color.rgb = RGBColor(0, 0, 0)
            run.font.size = Pt(15 if level == 1 else 13)
        i += 1
        continue
    if s.startswith('### '):
        p = doc.add_heading(level=2)
        add_inline_runs(p, s[4:].strip())
        for run in p.runs:
            run.font.color.rgb = RGBColor(0, 0, 0)
        i += 1
        continue
    if s.startswith('!['):
        m = re.match(r'^!\[.*?\]\(([^)]+)\)', s)
        if m:
            fname = m.group(1).split('/')[-1]
            doc.add_picture(FIGDIR + fname, width=Inches(5.8))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            i += 1
            continue
    if s.startswith('**Figure') or s.startswith('**Table'):
        p = doc.add_paragraph()
        add_inline_runs(p, s)
        for run in p.runs:
            run.bold = True
        i += 1
        continue
    if s.startswith('- '):
        while i < len(lines) and lines[i].strip().startswith('- '):
            p = doc.add_paragraph(style='List Bullet')
            add_inline_runs(p, lines[i].strip()[2:])
            i += 1
        continue
    if s.startswith('[') and ']' in s and re.match(r'^\[\d+\]', s):
        # 参考文献条目
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Inches(0.3)
        p.paragraph_format.first_line_indent = Inches(-0.3)
        add_inline_runs(p, s)
        i += 1
        continue
    # 普通段落
    p = doc.add_paragraph()
    add_inline_runs(p, s)
    i += 1

doc.save(OUT)
print(f'DOCX 已生成: {OUT}')
