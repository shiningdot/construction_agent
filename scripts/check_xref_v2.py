#!/usr/bin/env python3
"""V2 结构对账：图表交叉引用、章节交叉引用、中英段落对等。

与 `check_citations_v2.py`（文献 ↔ 正文）互补，这三项覆盖评审会揪的其余结构缺陷：

  1. **图表交叉引用**——图注/表注编号与图片文件、表格本体是否一一对应；编号有无
     跳号重号；正文引用了不存在的图表（悬空）；图表从未被正文引用（孤儿，Elsevier
     明确要求"所有表都必须被正文引用"）。
  2. **章节交叉引用**——正文的 "Section 3.2"/"3.2 节" 是否都指向存在的章节。V2 曾
     把"原 3.2–3.5 顺延为 3.3–3.6、表 1–9 顺延为表 2–10"，这类重排最容易留下错误引用。
  3. **中英段落对等**——镜像最怕的不是错译而是**漏译一整段**。逐标题统计段落数，
     中英按层级序列配对比较。

用法：
    /home/suntianhao/construction_agent/.venv/bin/python3 scripts/check_xref_v2.py
退出码 0 = 无待处置项。
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / 'docs' / 'paper_v2'

FIG_FILE = re.compile(r'!\[\]\(figures/fig(\d+)_')
# 图注/表注：英文 `**Figure 1.** 标题`，中文 `**图 1. 标题**`（粗体范围不同），
# 所以不要求句点后紧跟 `**`，只认 `**(Figure|图|Table|表) N.` 这个开头。
FIG_CAP = re.compile(r'^\*\*(?:Figure|图) (\d+)\.')
TAB_CAP = re.compile(r'^\*\*(?:Table|表) (\d+)\.')
FIG_REF_EN = re.compile(r'\bFigure (\d+)\b')
TAB_REF_EN = re.compile(r'\bTable (\d+)\b')
FIG_REF_ZH = re.compile(r'图\s?(\d+)')
TAB_REF_ZH = re.compile(r'表\s?(\d+)')
HEAD = re.compile(r'^(#{2,4})\s+(.*)')
# 章节引用：英文 Section 3.2 / Sections 4.1 and 4.2；中文 3.2 节 / 第 7 节。
# "Sections 4.1 and 4.2" 的第二个号要一并收，否则英文会比中文少报一个被引章节。
SEC_REF_EN = re.compile(r'\bSections?\s(\d+(?:\.\d+)*)(?:\s+and\s+(\d+(?:\.\d+)*))?')
SEC_REF_ZH = re.compile(r'(?:第\s?)?(\d+(?:\.\d+)*)\s?节')

FILES = {'en': 'eswa_paper_v2.md', 'zh': 'eswa_paper_v2_zh.md'}


def read_lines(tag):
    return (BASE / FILES[tag]).read_text(encoding='utf-8').split('\n')


def is_meta(s):
    """中文版末尾的「V2 修订说明」是给作者与导师看的元文本（投稿时删除），
    里面提到"表 1–9 顺延为表 2–10""原 3.2–3.5 节顺延"等，会被误算成正文引用。"""
    return s.startswith('*V2 修订说明') or s.startswith('*V2 Revision')


def scan(tag):
    """一趟扫完，返回该版的结构信息。"""
    fig_ref_re = FIG_REF_ZH if tag == 'zh' else FIG_REF_EN
    tab_ref_re = TAB_REF_ZH if tag == 'zh' else TAB_REF_EN
    sec_ref_re = SEC_REF_ZH if tag == 'zh' else SEC_REF_EN

    d = dict(fig_files=[], fig_caps=[], tab_caps=[], tab_blocks=[],
             fig_refs={}, tab_refs={}, sec_defs=[], sec_refs={}, heads=[])
    in_table = False
    cur_head = None
    for i, l in enumerate(read_lines(tag), 1):
        s = l.strip()
        if not s:
            in_table = False
            continue
        if is_meta(s):
            continue
        if s.startswith('|'):
            if not in_table:
                d['tab_blocks'].append(i)
                in_table = True
        else:
            in_table = False

        m = HEAD.match(s)
        if m:
            cur_head = (len(m.group(1)), m.group(2).strip(), [])
            d['heads'].append(cur_head)
            n = re.match(r'^(\d+(?:\.\d+)*)\.?\s', m.group(2).strip())
            if n:
                d['sec_defs'].append(n.group(1))
            continue

        m = FIG_FILE.search(s)
        if m:
            d['fig_files'].append((int(m.group(1)), i))
        m = FIG_CAP.match(s)
        if m:
            d['fig_caps'].append((int(m.group(1)), i))
            continue                      # 图注是定义，不计入"正文引用"
        m = TAB_CAP.match(s)
        if m:
            d['tab_caps'].append((int(m.group(1)), i))
            continue                      # 表注同理

        if cur_head is not None:
            cur_head[2].append(s)
        for m in fig_ref_re.finditer(s):
            d['fig_refs'].setdefault(int(m.group(1)), []).append(i)
        for m in tab_ref_re.finditer(s):
            d['tab_refs'].setdefault(int(m.group(1)), []).append(i)
        for m in sec_ref_re.finditer(s):
            for g in m.groups():          # 中文正则只有 1 组，英文有 2 组
                if g:
                    d['sec_refs'].setdefault(g, []).append(i)
    return d


def check_figtabs(tag, d):
    problems = []
    if [n for n, _ in d['fig_files']] != [n for n, _ in d['fig_caps']]:
        problems.append(f'图文件编号 {[n for n, _ in d["fig_files"]]} '
                        f'≠ 图注编号 {[n for n, _ in d["fig_caps"]]}')
    if len(d['tab_caps']) != len(d['tab_blocks']):
        problems.append(f'表注 {len(d["tab_caps"])} 个 ≠ 表格本体 {len(d["tab_blocks"])} 个')
    for label, caps in (('图注', d['fig_caps']), ('表注', d['tab_caps'])):
        nums = sorted(n for n, _ in caps)
        if nums and nums != list(range(1, len(nums) + 1)):
            problems.append(f'{label}编号不连续或有重号：{nums}')
    for n in sorted(d['fig_refs']):
        if n not in {x for x, _ in d['fig_caps']}:
            problems.append(f'正文引用 Figure {n} 但无此图（行 {d["fig_refs"][n]}）')
    for n in sorted(d['tab_refs']):
        if n not in {x for x, _ in d['tab_caps']}:
            problems.append(f'正文引用 Table {n} 但无此表（行 {d["tab_refs"][n]}）')
    for n in sorted({x for x, _ in d['fig_caps']}):
        if n not in d['fig_refs']:
            problems.append(f'Figure {n} 从未被正文引用')
    for n in sorted({x for x, _ in d['tab_caps']}):
        if n not in d['tab_refs']:
            problems.append(f'Table {n} 从未被正文引用')
    print(f'  图注 {[n for n, _ in d["fig_caps"]]}  引用 '
          + ' '.join(f'F{n}×{len(d["fig_refs"].get(n, []))}'
                     for n in sorted(x for x, _ in d['fig_caps'])))
    print(f'  表注 {[n for n, _ in d["tab_caps"]]}  引用 '
          + ' '.join(f'T{n}×{len(d["tab_refs"].get(n, []))}'
                     for n in sorted(x for x, _ in d['tab_caps'])))
    return problems


def check_sections(tag, d):
    defined = set(d['sec_defs'])
    problems = []
    for n in sorted(d['sec_refs'], key=lambda s: [int(x) for x in s.split('.')]):
        if n not in defined:
            problems.append(f'正文引用 {n} 节但无此节（行 {d["sec_refs"][n]}）')
    print(f'  章节定义 {len(defined)} 个；被引用 '
          f'{len(d["sec_refs"])} 个：{sorted(d["sec_refs"], key=lambda s: [int(x) for x in s.split(".")])}')
    return problems


scanned = {}
for tag in ('en', 'zh'):
    scanned[tag] = scan(tag)

total = []
for tag in ('en', 'zh'):
    d = scanned[tag]
    print(f'\n===== {tag.upper()} =====')
    probs = check_figtabs(tag, d) + check_sections(tag, d)
    if probs:
        print(f'  ❌ {len(probs)} 项：')
        for p in probs:
            print(f'     · {p}')
    else:
        print('  ✅ 图表与章节引用无问题')
    total += [(tag, p) for p in probs]

# 中英对称性：每个图表的中英引用次数应一致，标题层级序列应一致
print('\n===== 中英对称性 =====')
for i, kind in ((1, '图'), (2, '表')):
    key = 'fig_refs' if i == 1 else 'tab_refs'
    en, zh = scanned['en'][key], scanned['zh'][key]
    diff = [(n, len(en.get(n, [])), len(zh.get(n, [])))
            for n in sorted(set(en) | set(zh))
            if len(en.get(n, [])) != len(zh.get(n, []))]
    if diff:
        for n, a, b in diff:
            print(f'  ⚠ {kind} {n}：英文 {a} 次 / 中文 {b} 次')
        total += [('both', f'{kind} {n} 中英引用次数不一致') for n, _, _ in diff]
    else:
        print(f'  ✅ 每个{kind}的中英引用次数完全一致')

lv_en = [l for l, _, _ in scanned['en']['heads']]
lv_zh = [l for l, _, _ in scanned['zh']['heads']]
if lv_en != lv_zh:
    print(f'  ⚠ 标题层级序列不一致：EN {lv_en} / ZH {lv_zh}')
    total.append(('both', '中英标题层级序列不一致'))
else:
    print(f'  ✅ 标题层级序列一致（{len(lv_en)} 个标题）')

# 中英段落对等：逐标题比段落数
print('\n===== 中英段落对等 =====')
pe = [len(p) for _, _, p in scanned['en']['heads']]
pz = [len(p) for _, _, p in scanned['zh']['heads']]
bad = [(scanned['en']['heads'][i][1], a, b)
       for i, (a, b) in enumerate(zip(pe, pz)) if abs(a - b) >= 2]
if bad:
    for t, a, b in bad:
        print(f'  ⚠ {t[:52]:<54} EN {a} / ZH {b} 段')
        total.append(('both', f'标题「{t}」中英段落数出入 {b - a:+d}'))
else:
    print('  ✅ 无标题的段落数出入 ≥2（不存在整段漏译）')

print(f'\n合计待处置：{len(total)}')
sys.exit(1 if total else 0)
