#!/usr/bin/env python3
"""V2 投稿信校验：数字须在正文有据、无禁用措辞、标题一致。

投稿信是**唯一"正文之外、却直接送到编辑手里"**的文字，也是最容易出现
"正文没说的话"的地方——V1 版就有 3 处论断须处置（声称反转了 Liu et al.
的排序、称 MECE other 类由 18.9% 降到 2.9%、以及 "verified expert
applicability (Fleiss' Kappa 0.859–1.000 across layers)"）。**三者性质不同**：
前两者在 V2 正文中**已不存在**；第三处的**数字 V2 正文中英各 2 处确在**
（§3.3/§4.1），问题在把**标注者之间的一致性**（inter-annotator agreement）
说成了**体系已被验证为可用**——属过度解读（导师第 8 条），与"数字无据"
是两回事。**照「数字不存在」去处置会删掉正文里那条本身正确的专家验证说明。**

三项检查：
  A. 数字有据：投稿信里每个数字 token 都必须能在正文中找到同一值。
     4 位年份、1–2 位整数、邮箱与 DOI 片段按结构性噪声跳过。
  B. 禁用措辞：复用 check_forbidden_v2 的判定（投稿信同受既定决策第 5 条约束）。
  C. 标题一致：投稿信 Subject 行内的标题须与正文标题逐字相同。

用法：python3 scripts/check_cover_letter_v2.py
退出码 0 = 三项全合规。
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_forbidden_v2 as F  # noqa: E402

BASE = HERE.parent / 'docs' / 'paper_v2'
LETTER = BASE / 'cover_letter_v2.md'
PAPER = BASE / 'eswa_paper_v2.md'

NUM = re.compile(r'\d[\d,]*(?:\.\d+)?')
YEARLY = re.compile(r'^(?:19|20)\d{2}$')


def tokens(text):
    return {m.group(0).replace(',', '') for m in NUM.finditer(text)}


def significant(text):
    """剔除结构性噪声后的数字 token 集合。"""
    out = set()
    for m in NUM.finditer(text):
        t = m.group(0).replace(',', '')
        if YEARLY.match(t) or re.fullmatch(r'\d{1,2}', t):
            continue
        if re.fullmatch(r'0\d{4,}', t):      # DOI / 长编号片段
            continue
        out.add(t)
    return out


def main():
    if not LETTER.exists():
        print(f'⚠ 未找到 {LETTER}')
        return 1
    paper_txt = PAPER.read_text(encoding='utf-8')
    raw_lines = LETTER.read_text(encoding='utf-8').split('\n')
    # 扫描范围剔除三类元信息：
    #   - 文件头部的 `> ...` 说明块：它引用 V1 的旧数字（0.859 / 18.9 等）
    #     与 V1 的违规措辞来说明"已删除"，本身不是投稿信内容
    #     （同 check_numbers_v2.py 排除 meta 行的做法）
    #   - 联系方式行：邮箱里的数字会污染 token
    #   - DOI 行
    letter_lines = [l for l in raw_lines
                    if not l.lstrip().startswith('>')
                    and '@' not in l and 'doi.org' not in l.lower()]
    body = '\n'.join(letter_lines)

    bad = 0

    # ── A. 数字有据 ───────────────────────────────────────────────
    print('A. 投稿信数字是否在正文有据')
    print('-' * 70)
    known = tokens(paper_txt)
    used = significant(body)
    missing = sorted(t for t in used if t not in known)
    print(f'  投稿信数字 {len(used)} 个：{sorted(used)}')
    if missing:
        bad += len(missing)
        print(f'  ❌ 正文中找不到同一值：{missing}')
        print('     （投稿信不得出现正文没有的数字——这是编辑最容易核的一类）')
    else:
        print('  ✅ 全部能在正文找到')

    # ── B. 禁用措辞 ───────────────────────────────────────────────
    print('\nB. 禁用措辞（复用 check_forbidden_v2 判定）')
    print('-' * 70)
    hits = [(zh, k) for l in letter_lines for k, zh, *_ in F.iter_hits(l, 'EN')]
    if hits:
        bad += len(hits)
        for zh, kind in hits:
            print(f'  ❌ 「{zh}」  {kind}')
    else:
        print('  ✅ 无')

    # ── C. 标题一致 ───────────────────────────────────────────────
    print('\nC. 标题是否与正文一致')
    print('-' * 70)
    paper_title = next((l[2:].strip() for l in paper_txt.split('\n')
                        if l.startswith('# ')), None)
    subject = next((l for l in letter_lines if l.startswith('**Subject**')), '')
    ok = bool(paper_title) and paper_title in subject
    print(f'  正文标题：{paper_title}')
    print(f'  投稿信主题行：{subject[:96]}…' if len(subject) > 96 else f'  投稿信主题行：{subject}')
    if ok:
        print('  ✅ 一致')
    else:
        bad += 1
        print('  ❌ 投稿信标题与正文不一致')

    print('\n' + '=' * 70)
    if bad:
        print(f'❌ {bad} 项待处置')
        return 1
    print('✅ 投稿信三项全合规（数字有据 / 无禁用措辞 / 标题一致）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
