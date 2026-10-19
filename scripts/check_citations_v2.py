#!/usr/bin/env python3
"""V2 引用双向对账：参考文献条目 ↔ 正文引用，中英两版各做一次。

既有的 `check_citations.py` 硬编码指向 V1（`docs/paper/eswa_paper.md`，27 条），
对已成稿的 V2 不适用，这里独立重做。

两个方向都查：
  · 文献 → 正文：列表里有、正文从未引用的**孤立文献**（审稿人会揪）
  · 正文 → 文献：正文引了、列表里查不到的**悬空引用**（更严重）

用法：
    /home/suntianhao/construction_agent/.venv/bin/python3 scripts/check_citations_v2.py
退出码 0 = 两项均为 0；1 = 有残留，需人工处置。
"""
import re
import sys
import unicodedata
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / 'docs' / 'paper_v2'

REF_HEAD = {'en': '## References', 'zh': '## 参考文献'}
APPENDIX = {'en': '## Appendix', 'zh': '## 附录'}

# 正文引用形式：(Author et al., 2024) / Author et al. (2024) / (Author and Author, 2024)
# 中文版混用全角与半角括号（"（Kong and Ahn, 2024）" 与 "Togan et al. (2025)" 并存），
# 只匹配半角会把中文版 13 条文献误报成"未被引用"。
CITE_PAREN = re.compile(r'[（(]([^()（）]{2,90}?,\s*(?:19|20)\d{2}[a-c]?'
                        r'(?:[;,]\s*(?:19|20)\d{2}[a-c]?)*)[）)]')
# 叙述式引用有三种写法，缺一不可：
#   Togan et al. (2025) / Yang and Park (2025) / Zhang, Hou, et al. (2025a)
# 最后一种（姓氏后还跟着第二作者）容易漏，漏了会把 Zhang 两条误报成孤立文献。
CITE_NARR = re.compile(r'\b([A-Z][\wÀ-ſ\'-]+)'
                       r'(?:,\s*[A-Z][\wÀ-ſ\'.-]*)*'
                       r'(?:,?\s+(?:et al\.|and\s+[A-Z][\wÀ-ſ\'-]+))?'
                       r'\s*\((?:19|20)\d{2}[a-c]?\)')


def surname_candidates(cite_text):
    """从一段引用文本里抠出首作者姓氏。

    只取每条引用的第一个名字：文献列表按首作者排序，把 "Kong and Ahn" 拆成 Kong
    和 Ahn 两个姓氏，Ahn 会因为列表里查不到而误报成"正文引用查无文献"。
    """
    out = []
    for chunk in cite_text.split(';'):
        chunk = re.sub(r'\(?(?:19|20)\d{2}[a-c]?\)?', '', chunk).strip()
        chunk = re.sub(r'\bet al\.', '', chunk)
        part = re.split(r',|\band\b', chunk)[0].strip()
        if part and part[0].isupper() and len(part) > 1:
            out.append(unicodedata.normalize('NFC', part))
    return out


def audit(tag):
    fname = 'eswa_paper_v2.md' if tag == 'en' else 'eswa_paper_v2_zh.md'
    text = open(BASE / fname, encoding='utf-8').read()
    head, app = REF_HEAD[tag], APPENDIX[tag]
    assert head in text, f'{tag}: 找不到 {head}'
    body, rest = text.split(head, 1)
    refs_block = rest.split(app, 1)[0] if app in rest else rest
    # 有的版本把 References 塞在末节里，若切出来太短就取全文尾部
    if len(refs_block.strip()) < 200 and len(rest) > 200:
        refs_block = rest

    # 条目是"作者. (年份). 标题. 期刊"这样的纯段落，空行分隔，不带编号
    ref_lines = [l.strip() for l in refs_block.split('\n')
                 if len(l.strip()) > 40
                 and re.search(r'\((?:19|20)\d{2}[a-c]?\)', l)]
    ref_keys = []
    for l in ref_lines:
        m = re.search(r'\((19|20)\d{2}[a-c]?\)', l)
        yr = m.group(0).strip('()') if m else '?'
        first = re.sub(r'^[\d\-\*\.\)\s]+', '', l)
        sur = first.split(',')[0].strip() if ',' in first else first.split('.')[0].strip()
        ref_keys.append((sur, yr, l[:70]))

    cites = {}
    for m in CITE_PAREN.finditer(body):
        for s in surname_candidates(m.group(1)):
            cites.setdefault(s, m.group(0)[:50])
    for m in CITE_NARR.finditer(body):
        cites.setdefault(m.group(1), m.group(0)[:50])

    print(f'\n===== {tag.upper()} =====')
    print(f'参考文献条目 {len(ref_keys)} 条；正文出现 {len(cites)} 个不同引用名')
    # 方向一：文献 → 正文
    orphan = []
    for sur, yr, txt in ref_keys:
        key = re.sub(r'\s+', ' ', sur).split(' ')[0]
        if not any(key.lower() in c.lower() or c.lower() in key.lower() for c in cites):
            orphan.append((key, yr, txt))
    # 方向二：正文 → 文献
    dangling = []
    refnames = [re.sub(r'\s+', ' ', s).split(' ')[0].lower() for s, _, _ in ref_keys]
    for c, ctx in cites.items():
        if not any(c.lower() in r or r in c.lower() for r in refnames):
            dangling.append((c, ctx))
    print(f'文献未被正文引用: {len(orphan)}')
    for o in orphan:
        print(f'   ⚠ {o[0]} ({o[1]}) :: {o[2]}')
    print(f'正文引用查无文献: {len(dangling)}')
    for d in dangling:
        print(f'   ⚠ "{d[0]}" :: {d[1]}')
    return len(ref_keys), len(orphan), len(dangling)


def main():
    bad = 0
    for tag in ('en', 'zh'):
        _, n_orphan, n_dangling = audit(tag)
        bad += n_orphan + n_dangling
    print(f'\n合计待处置：{bad}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
