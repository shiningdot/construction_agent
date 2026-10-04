#!/usr/bin/env python3
"""四格式成品一致性核验（第九类核验）。

此前八项核验**全部跑在 md 源上**——md 全绿只说明"源没问题"，不说明
`build_v2_docs.py` 生成的 DOCX / TeX / PDF 没问题。而本项目真实发生过的
成品缺陷（纸张误为 US Letter、中文标题渲染成豆腐块、9 个四级标题整行丢失、
`$t_i$` 被转义破坏成字面下划线、圈码无字形）**全是在成品里、全是人工发现的**。

本脚本把"成品是否真的跟上了源"固化为可复跑核验。九个维度：
  A 产物齐全（六个文件非空）
  B 数字无损：md 里的数字 token 须全部出现在 DOCX 与 PDF 里
  B2 正文文本无损：md 段落首 24 字符须在 DOCX 里（抓"改了文字忘了重跑"）
  C 转义残留：不得出现裸 `$` 或未展开的 `\\command`
  D 结构对等：md 的 ##/###/#### ↔ DOCX 的 Heading1/2/3；表格数、内嵌图数、中英对称
  E 纸张：两版 PDF 均须 A4（Elsevier 要求，article 类默认 letterpaper 是坑）
  F 已登记的差异与提示（列出但不计错）：圈码表示法、DOI 断行、mtime 落差
  G TeX 排版缺陷：tectonic 日志里的 Missing character / Overfull / LaTeX 错误

G 段补的是前八段共同的盲区：A–F 全在比"文件内容对不对"，而**排版溢出与字形
缺失只体现在编译日志里**——中文标题 232pt 的 `vbox too high`、圈码 12 处
`Missing character`、表 10 末行 2.86pt 的 Overfull，都不可能从提取文本里看出来。

**mtime 只作提示、不作判据**：实测本仓库的 md 曾被后续编辑刷新过 mtime（比成品
晚 5 分钟）而内容分毫未变——"文件新旧"不可靠。真凭据是 B/B2/D 三段的内容比对。

用法：python3 scripts/check_formats_v2.py [--verbose | --self-test]
"""
import re
import sys
import time
import zipfile
from pathlib import Path

WT = Path(__file__).resolve().parent.parent
D = WT / 'docs' / 'paper_v2'
PAPERS = {
    'EN': ('eswa_paper_v2.md', 'eswa_paper_v2_en.docx',
           'eswa_paper_v2_en.tex', 'eswa_paper_v2_en.pdf'),
    'ZH': ('eswa_paper_v2_zh.md', 'eswa_paper_v2_zh.docx',
           'eswa_paper_v2_zh.tex', 'eswa_paper_v2_zh.pdf'),
}
FIG_N, TBL_N = 6, 10
FRESH_SLACK = 60.0      # git checkout 会让 mtime 齐平，故只报"明显更新"的源

# 已登记的格式差异：TeX 版有意把圈码换成 (1)-(12)（Latin Modern 无字形，会成豆腐块）
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫'
# PDF 文本层提不到的 token 只能是这种形态：DOI 前缀（排版时在 / . - 后插了断点）
DOI_LIKE = re.compile(r'^10\.\d{4,9}$')

# ── TeX 排版缺陷（G 段）────────────────────────────────────────────────
# 日志由 build_v2_docs.py 以 tectonic --keep-logs 产出后移进 .build/。
LOGD = D / '.build'
LOG_FRESH_SLACK = 120.0     # 日志与 PDF 出自同一轮编译，mtime 应几乎同时
# 已登记的 Overfull \hbox（pt）。EN 只有表 10 末行一处，作者已知并接受；
# ZH 一处都没有。任何不在基线里的 Overfull 都是新回归——**不做"小于 Npt 就放过"
# 的豁免**，否则 ZH 一旦出现溢出会被静默吞掉。
OVERFULL_BASE = {'EN': [2.85558], 'ZH': []}
OVERFULL_TOL = 0.05         # pt：同一处溢出在重新编译时末位会浮动
# Underfull 只作提示（CJK 断行天然产生大片 Underfull，不影响可读性）
UNDERFULL_BASE = {'EN': 7, 'ZH': 122}
PAGES_BASE = {'EN': 24, 'ZH': 18}


# ── 读取器 ──────────────────────────────────────────────────────────────
def docx_xml(p):
    with zipfile.ZipFile(p) as z:
        return z.read('word/document.xml').decode('utf-8')


def docx_text(p):
    return re.sub(r'<[^>]+>', '', docx_xml(p))


def docx_media(p):
    with zipfile.ZipFile(p) as z:
        return [n for n in z.namelist() if n.startswith('word/media/')]


def pdf_text(p):
    from pypdf import PdfReader
    return '\n'.join((pg.extract_text() or '') for pg in PdfReader(str(p)).pages)


# ── 判据函数（纯逻辑，可自测） ──────────────────────────────────────────
def md_heads(src):
    """数 md 的 # / ## / ### / #### 标题（行首，代码块里的不算——本项目 md 无代码块）。"""
    h = {1: 0, 2: 0, 3: 0, 4: 0}
    for ln in src.splitlines():
        m = re.match(r'^(#{1,4}) ', ln)
        if m:
            h[len(m.group(1))] += 1
    return h


def data_tokens(src):
    """带千分位、带小数、或 ≥3 位的数字——即"像实验数据的"，避开章节号与序号。

    ⚠ 千分位逗号必须后跟【恰好 3 位】数字：宽松的 `\\d[\\d,]*` 会把中文表的
    `(397,18.0%)` 提成一个假 token `397,18.0`（逗号是句读不是千分位），
    应拆成 `397` 与 `18.0`。三种形态按序交替，左侧优先。
    """
    pat = re.compile(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?'   # 千分位
                     r'|\d+\.\d+'                        # 小数
                     r'|\d{3,}')                         # ≥3 位整数
    return set(pat.findall(src))


def body_snippets(src, n=24):
    """md 正文段落的首 n 字符 + 是否含数学。

    "改了正文文字却忘了重跑 build_v2_docs.py"只有这个判据抓得住——数字没动，
    token 判据全绿，成品却还是旧文字。含 `$` 的段落豁免：它们会被 _add_math()
    转成真下标，文本形态本就不同（那部分由 C 段的"无裸 $"反向把关）。
    """
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        if (not s or s.startswith(('#', '|', '-', '>', '![')) or s == '---'
                or s.startswith(('**Figure', '**图', '**Table'))
                or re.match(r'^\[图\d+占位', s)):
            continue
        if len(s) >= n:
            out.append((s, '$' in s))
    return out


def unmark(s):
    """去掉 markdown 标记，使 md 文本与 DOCX/PDF 提取文本可比。"""
    return re.sub(r'[*`]', '', s)


def refs_start(src):
    """参考文献区的起点（正文与文献条目在成品的排版路径完全不同）。"""
    m = re.search(r'^#+ *(References|REFERENCES|参考文献)\s*$', src, re.M)
    return m.start() if m else len(src)


def sec_refs(src):
    """TeX 侧须豁免的编号：md 标题自带的编号 + 正文的章节交叉引用。

    TeX 用 `\\section` 自动编号、用 `\\ref` 生成引用号，正文里没有字面编号
    （DOCX 侧则由构建脚本写入字面编号，故 DOCX 端能命中）。不豁免的话，
    会拿"TeX 不写死编号"这个正确设计当缺陷报——实测正文区 313 个 token
    唯一缺的就是标题编号 `2.1`。

    只豁免**编号性**的 token：作为数据出现的同一个数（如"3.2 天"）不豁免。
    """
    heads = {m.group(1)
             for m in re.finditer(r'^#{1,6}\s+(\d+(?:\.\d+)*)\b', src, re.M)}
    refs = {m.group(1) or m.group(2)
            for m in re.finditer(r'(?:Section|Sect\.|Sec\.)\s*(\d+(?:\.\d+)*)'
                                 r'|(\d+(?:\.\d+)*)\s*节', src)}
    return heads | refs


def escapes(text):
    """未展开的 TeX 残留：裸 $ 或 \\command（本项目正文不含 LaTeX 源码）。"""
    return {'$': text.count('$'),
            r'\command': len(re.findall(r'\\[a-zA-Z]+', text))}


def tex_log_stats(text):
    """从 tectonic 日志里数排版缺陷（纯函数，可自测）。

    Overfull 的 pt 值一并取出——**只数条数不够**：同一个位置溢出量级从小变大
    （比如 2.8pt → 30pt）条数不变，却已经是一眼可见的排版事故。
    """
    m = re.search(r'Output written on .*?\((\d+) pages', text)
    return {
        'missing': len(re.findall(r'Missing character', text)),
        # 按行判、每行只记一次：`! LaTeX Error: ...` 同时含 `! ` 与 `LaTeX Error`，
        # 用替代正则会在同一行上重复计数
        'errors': sum(1 for ln in text.splitlines()
                      if ln.startswith('! ') or 'LaTeX Error' in ln),
        'vbox': len(re.findall(r'\\vbox .*too high', text)),
        'overfull': [float(x) for x in re.findall(
            r'Overfull \\[hv]box \(([\d.]+)pt too (?:wide|high)', text)],
        'underfull': len(re.findall(r'Underfull \\[hv]box', text)),
        'pages': int(m.group(1)) if m else None,
    }


def unregistered_overfull(overfull, base):
    """返回既不在登记基线里、也容不进容差的 Overfull 值（多重集，逐个消解）。"""
    rest, out = list(base), []
    for v in overfull:
        for i, b in enumerate(rest):
            if abs(v - b) <= OVERFULL_TOL:
                rest.pop(i)
                break
        else:
            out.append(v)
    return out


# ── 核验 ────────────────────────────────────────────────────────────────
def main(verbose=False):
    problems, notes = [], []
    heads = {}

    print('A. 产物齐全')
    print('-' * 72)
    for tag, (md, dx, tx, pf) in PAPERS.items():
        for name in (md, dx, tx, pf):
            p = D / name
            if not p.exists() or p.stat().st_size == 0:
                problems.append(f'{tag}: 成品缺失或为空 —— {name}')
        md_t = (D / md).stat().st_mtime
        for name in (dx, tx, pf):
            p = D / name
            if p.exists() and md_t - p.stat().st_mtime > FRESH_SLACK:
                gap = (md_t - p.stat().st_mtime) / 60
                notes.append(f'{tag}: {name} 的 mtime 比 {md} 早 {gap:.0f} 分钟')
        ok = not any(x.startswith(tag) for x in problems)
        print(f'  {"✅" if ok else "❌"} {tag}: {md} → docx / tex / pdf')

    print('\nB. 数字无损（md 的数字 token 须全部出现在成品里）')
    print('-' * 72)
    for tag, (md, dx, tx, pf) in PAPERS.items():
        src = (D / md).read_text(encoding='utf-8')
        toks = data_tokens(src)
        # TeX 侧只核正文区、且只能核数字 token：中文在 LaTeX 里逐字断行，
        # 段落首 24 字符几乎必然跨行（实测 ZH 129 段缺 101 段），比不得。
        # 参考文献区一律不比——DOI 与卷期号在那里被 \allowbreak 断开
        # （实测正文区 313 个 token 只缺 1 个，全量则缺 21 个，20 个是文献区）。
        tex_note = ''
        if (D / tx).exists():
            tex = (D / tx).read_text(encoding='utf-8')
            body = src[:refs_start(src)]
            secs = sec_refs(src)
            miss = sorted(t for t in data_tokens(body) if t not in tex)
            real = [t for t in miss if not DOI_LIKE.match(t) and t not in secs]
            if real:
                problems.append(f'{tag} TeX: {len(real)} 个正文数字未出现在 .tex 里 '
                                f'—— {real[:10]}')
            tex_note = (f'，TeX 正文区缺 {len(real)}'
                        + (f'（豁免章节号 {len([t for t in miss if t in secs])}）'
                           if any(t in secs for t in miss) else ''))
            print(f'  {"✅" if not real else "❌"} {tag} TeX: 正文区 '
                  f'{len(data_tokens(body))} 个 token{tex_note}')
        if not (D / dx).exists() or not (D / pf).exists():
            continue
        for label, text in (('DOCX', docx_text(D / dx)), ('PDF', pdf_text(D / pf))):
            miss = sorted(t for t in toks if t not in text)
            waived = [t for t in miss if DOI_LIKE.match(t)]
            real = [t for t in miss if t not in waived]
            if waived:
                notes.append(f'{tag} {label}: {len(waived)} 个 DOI 前缀因排版断行'
                             f'不可提取（{", ".join(waived)}），已登记')
            if real:
                problems.append(f'{tag} {label}: {len(real)}/{len(toks)} 个数字丢失 '
                                f'—— {real[:10]}')
            print(f'  {"✅" if not real else "❌"} {tag} {label}: {len(toks)} 个 token，'
                  f'缺 {len(real)}' + (f'（豁免 DOI {len(waived)}）' if waived else ''))

    print('\nB2. 正文文本无损（md 段落首 24 字符须在 DOCX 里；数学段豁免）')
    print('-' * 72)
    print('    这一项抓"改了正文文字却忘了重跑"——数字没动时 B 段会全绿，'
          '成品却还是旧文字。')
    for tag, (md, dx, tx, pf) in PAPERS.items():
        if not (D / dx).exists():
            continue
        src = (D / md).read_text(encoding='utf-8')
        dt = unmark(docx_text(D / dx))
        snips = body_snippets(src)
        math = [s for s, m in snips if m]
        miss = [s[:24] for s, m in snips if not m and unmark(s[:24]) not in dt]
        if miss:
            problems.append(f'{tag}: {len(miss)}/{len(snips) - len(math)} 个正文段落'
                            f'不在 DOCX 里 —— {miss[:3]}')
        print(f'  {"✅" if not miss else "❌"} {tag}: 正文段落 {len(snips)} 段'
              f'（数学段豁免 {len(math)}），缺 {len(miss)}')

    print('\nC. 转义残留（裸 $ / 未展开 \\command）')
    print('-' * 72)
    for tag, (md, dx, tx, pf) in PAPERS.items():
        for label, path, rd in (('DOCX', D / dx, docx_text), ('PDF', D / pf, pdf_text)):
            if not path.exists():
                continue
            e = escapes(rd(path))
            bad = {k: v for k, v in e.items() if v}
            if bad:
                problems.append(f'{tag} {label}: 转义残留 {bad}')
            print(f'  {"✅" if not bad else "❌"} {tag} {label}: '
                  + ('无残留' if not bad else str(bad)))

    print('\nD. 结构对等')
    print('-' * 72)
    for tag, (md, dx, tx, pf) in PAPERS.items():
        src = (D / md).read_text(encoding='utf-8')
        h = md_heads(src)
        heads[tag] = h
        if not (D / dx).exists():
            continue
        xml = docx_xml(D / dx)
        got = {n: xml.count(f'w:val="Heading{n}"') for n in (1, 2, 3)}
        want = {1: h[2], 2: h[3], 3: h[4]}     # md ## / ### / #### → DOCX H1/H2/H3
        for n in (1, 2, 3):
            if got[n] != want[n]:
                problems.append(f'{tag}: md 有 {want[n]} 个 {"#"*(n+1)} 标题，'
                                f'DOCX 有 {got[n]} 个 Heading{n}')
        tbl = xml.count('<w:tbl>')
        md_tbl = sum(1 for ln in src.splitlines()
                     if re.match(r'^\|[\s:|-]+\|$', ln.strip()))
        media = len(docx_media(D / dx))
        if tbl != md_tbl:
            problems.append(f'{tag}: md 有 {md_tbl} 张表，DOCX 有 {tbl} 张')
        if media != FIG_N:
            problems.append(f'{tag}: DOCX 内嵌图 {media} 张，应为 {FIG_N} 张')
        print(f'  ✅ {tag}: 标题 H1/H2/H3 = {got[1]}/{got[2]}/{got[3]}（md '
              f'##/###/#### = {h[2]}/{h[3]}/{h[4]}）、表 {tbl}、图 {media}')
    if 'EN' in heads and 'ZH' in heads:
        for k, nm in ((2, '##'), (3, '###'), (4, '####')):
            if heads['EN'][k] != heads['ZH'][k]:
                problems.append(f'中英标题不对称：{nm} EN {heads["EN"][k]} '
                                f'≠ ZH {heads["ZH"][k]}')
        print(f'  ✅ 中英标题层级对称：## {heads["EN"][2]}、### {heads["EN"][3]}、'
              f'#### {heads["EN"][4]}')

    print('\nE. 纸张尺寸（须 A4）')
    print('-' * 72)
    for tag, (md, dx, tx, pf) in PAPERS.items():
        if not (D / pf).exists():
            continue
        from pypdf import PdfReader
        box = PdfReader(str(D / pf)).pages[0].mediabox
        w, h = float(box.width) / 72, float(box.height) / 72
        a4 = abs(w - 8.27) < 0.06 and abs(h - 11.69) < 0.06
        if not a4:
            problems.append(f'{tag} PDF 纸张 {w:.2f}×{h:.2f} in，非 A4'
                            + ('（US Letter）' if abs(w - 8.5) < 0.06 else ''))
        print(f'  {"✅" if a4 else "❌"} {tag} PDF: {w:.2f}×{h:.2f} in'
              + ('' if a4 else ' —— 须显式 a4paper'))

    print('\nF. 已登记的格式差异（有意为之，不计错）')
    print('-' * 72)
    for tag, (md, dx, tx, pf) in PAPERS.items():
        n_docx = sum(docx_text(D / dx).count(c) for c in CIRCLED) if (D / dx).exists() else 0
        n_tex = (D / tx).read_text(encoding='utf-8').count('①') if (D / tx).exists() else 0
        print(f'  {tag}: DOCX 保留圈码 {n_docx} 个 ↔ TeX 转 (1)-(12) '
              f'（原圈码剩 {n_tex} 个，字体缺字形）')
    for n in notes:
        print(f'  {n}')

    print('\nG. TeX 排版缺陷（tectonic 日志）')
    print('-' * 72)
    for tag, (md, dx, tx, pf) in PAPERS.items():
        logp = LOGD / (Path(tx).stem + '.log')
        if not logp.exists():
            problems.append(f'{tag}: 无编译日志 {logp.name} —— 重跑 build_v2_docs.py')
            print(f'  ❌ {tag}: 无 {logp.name}（须重跑构建，日志才有）')
            continue
        st = tex_log_stats(logp.read_text(encoding='utf-8', errors='replace'))
        # 日志与 PDF 应出自同一轮编译；差得远说明日志是上一轮的，判据会失真
        pdf_p = D / pf
        if pdf_p.exists():
            gap = (pdf_p.stat().st_mtime - logp.stat().st_mtime) / 60
            if abs(gap) > LOG_FRESH_SLACK / 60:
                problems.append(f'{tag}: 编译日志与 PDF 相差 {gap:.0f} 分钟 —— 已过期')
        bad = []
        if st['missing']:
            bad.append(f'Missing character ×{st["missing"]}（字形缺失，会成豆腐块）')
        if st['errors']:
            bad.append(f'LaTeX 错误 ×{st["errors"]}')
        if st['vbox']:
            bad.append(f'vbox too high ×{st["vbox"]}')
        unreg = unregistered_overfull(st['overfull'], OVERFULL_BASE[tag])
        if unreg:
            bad.append('Overfull 未登记 ' + '、'.join(f'{v:.1f}pt' for v in unreg))
        problems += [f'{tag}: {x}' for x in bad]
        print(f'  {"❌" if bad else "✅"} {tag}: {st["pages"]} 页，'
              f'Overfull {len(st["overfull"])}（登记基线 {len(OVERFULL_BASE[tag])}）、'
              f'Missing {st["missing"]}、LaTeX 错误 {st["errors"]}、'
              f'vbox 过高 {st["vbox"]}'
              + ('  —— ' + '；'.join(bad) if bad else ''))
        # Underfull 与页数只提示：不影响可读性，但数量级变化往往意味着排版回归
        ub = UNDERFULL_BASE[tag]
        if abs(st['underfull'] - ub) > max(3, ub * 0.5):
            print(f'  ℹ️ {tag}: Underfull {st["underfull"]} 处（基线 {ub}）')
        if st['pages'] and st['pages'] != PAGES_BASE[tag]:
            print(f'  ℹ️ {tag}: PDF {st["pages"]} 页（基线 {PAGES_BASE[tag]} 页）')

    print('\n' + '=' * 72)
    if problems:
        print(f'❌ {len(problems)} 项待处置：')
        for x in problems:
            print(f'   {x}')
        return 1
    print('✅ 四格式成品与源一致，可投稿')
    return 0


# ── 自测 ────────────────────────────────────────────────────────────────
def self_test():
    fails = 0

    def ck(name, got, want):
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f'{"✅" if ok else "❌"} {name}: {got} 期望 {want}')

    ck('md 标题计数', md_heads('# T\n## A\n### B\n#### C\n#### D\n正文'),
       {1: 1, 2: 1, 3: 1, 4: 2})
    ck('非行首 # 不算标题', md_heads('文字 # 井号\n## 真标题'), {1: 0, 2: 1, 3: 0, 4: 0})
    # 数字 token：负数与短序号须排除，千分位/小数/长整数须保留
    ck('数字 token', sorted(data_tokens('2,623 条 / 0.847 / 16,323 / 12 / 5 / §3.1')),
       sorted(['2,623', '0.847', '16,323', '3.1']))
    ck('尾逗号清理', sorted(data_tokens('(397,18.0%)')), sorted(['397', '18.0']))
    # 转义残留：干净文本 0，脏文本必报
    ck('无残留', escapes('正常文本 2,623 条'), {'$': 0, r'\command': 0})
    ck('残留能被抓住', escapes(r'求 $t_i$ 与 \textbf{值}'),
       {'$': 2, r'\command': 1})
    # DOI 豁免只认 DOI 前缀形态，普通数字缺失不得豁免
    ck('DOI 豁免', [bool(DOI_LIKE.match(t)) for t in ('10.17868', '10.1016', '972', '2,623')],
       [True, True, False, False])
    # 正文段落：标题/表格/图注/占位须排除，正文保留，数学段须打标
    src = ('# 标题\n## 小节\n| a | b |\n**Figure 1.** 图注\n[图1占位]\n'
           '这是一段足够长的正文句子用于测试段落提取是否正常工作。\n'
           'Let $T = \\{t_1\\}$ be the set.')
    sn = body_snippets(src)
    ck('正文段落条数', len(sn), 2)
    ck('数学段被标记', [m for _, m in sn], [False, True])
    ck('去 markdown 标记', unmark('**粗** 与 `码`'), '粗 与 码')
    # TeX 日志：干净样本四类缺陷全 0 且能提页数；脏样本逐类都能数出来
    clean = ('Output written on x.xdv (24 pages, 753352 bytes).\n'
             'Underfull \\hbox (badness 10000) in paragraph at lines 1--2\n')
    ck('日志·干净', {k: tex_log_stats(clean)[k]
                     for k in ('missing', 'errors', 'vbox', 'overfull', 'pages')},
       {'missing': 0, 'errors': 0, 'vbox': 0, 'overfull': [], 'pages': 24})
    dirty = ('Missing character: There is no ① in font\n'
             '! Undefined control sequence.\n'
             'Overfull \\hbox (2.85558pt too wide) in paragraph at lines 4--4\n'
             'Overfull \\vbox (232.0pt too high) has occurred while \\output is active\n'
             '\\vbox(100.0pt) too high\n')
    st = tex_log_stats(dirty)
    ck('日志·脏样本计数', [st['missing'], st['errors'], st['overfull'], st['vbox']],
       [1, 1, [2.85558, 232.0], 1])
    # Overfull 基线：容差内算登记，超出即未登记；ZH 基线为空故任何溢出都报
    ck('Overfull 基线', unregistered_overfull([2.856, 12.3], [2.85558]), [12.3])
    ck('Overfull 空基线', unregistered_overfull([0.4], []), [0.4])
    ck('Overfull 配对不重复用', unregistered_overfull([2.856, 2.856], [2.85558]),
       [2.856])
    # TeX 豁免：标题编号与章节交叉引用须豁，作为数据的同一个数不得豁
    ck('TeX 豁免编号', sorted(sec_refs('### 2.1 小节\n见 Section 3.2 与 4.4.2 节\n'
                                        '共 3.2 万条')),
       ['2.1', '3.2', '4.4.2'])
    ck('参考文献区起点', refs_start('正文\n## References\n[1] x') > 0, True)
    ck('无 References 时取全文', refs_start('正文只有这些'), 6)

    print('\n' + '=' * 72)
    if fails:
        print(f'❌ {fails} 条自测失败——判定逻辑有问题')
        return 1
    print('✅ 自测全过：标题/数字/残留/豁免/日志/TeX豁免六类判据都能报错')
    return 0


def drift_test():
    """失败路径自证：把漂移注入成品，脚本必须报错。

    往 DOCX 的 document.xml 里注入四类漂移（改数字 / 插 `$` / 动标题样式 /
    改正文文字），逐例跑主流程并断言退出码转 1。DOCX 是 zip，重打包即可，
    成品不受影响（全程在临时目录）。另三类走不同的注入面：B 段 TeX 行改
    临时目录里的 `.tex`；B 段 PDF 行用 PyMuPDF 把 PDF 里目标数字的**全部**
    出现处 redact 掉（只擦一处不够——判据是"token 出现过没有"，剩下几处
    照样命中，用例会静默失效）；G 段换日志目录（成品文件一个不动，A–F
    照常全绿）——每一类都必须有一条"改了就该报"的失败路径，否则该判据
    等于没接。
    """
    import shutil
    import tempfile

    global D, LOGD

    def repack(src, dst, fn):
        with zipfile.ZipFile(src) as z:
            items = [(i, z.read(i.filename)) for i in z.infolist()]
        with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as z:
            for info, data in items:
                if info.filename == 'word/document.xml':
                    data = fn(data.decode('utf-8')).encode('utf-8')
                z.writestr(info, data)

    # "改文字"用例的目标必须是**单个 `<w:t>` 内连续**的正文片段，否则替换会在
    # XML 标签间隔处落空、用例静默失效（第一版就踩了这个坑，报"没抓住"）
    src_md = (D / 'eswa_paper_v2.md').read_text(encoding='utf-8')
    xml_en = docx_xml(D / 'eswa_paper_v2_en.docx')
    nodes = re.findall(r'<w:t[^>]*>([^<]+)</w:t>', xml_en)
    snips = [unmark(s[:24]) for s, m in body_snippets(src_md)
             if not m and not any(c in s[:24] for c in '&<>')]
    hit = next((sn for sn in snips if any(sn in t for t in nodes)), None)

    cases = [
        ('B  数字丢失', lambda s: s.replace('16,323', '16,333'),
         '16,323' in docx_xml(D / 'eswa_paper_v2_en.docx')),
        (r'C  裸 $ 残留', lambda s: s.replace('</w:t>', ' $</w:t>', 1), True),
        ('D  标题层级', lambda s: s.replace('w:val="Heading2"', 'w:val="Heading9"', 1),
         'w:val="Heading2"' in docx_xml(D / 'eswa_paper_v2_en.docx')),
        ('B2 正文文字', (lambda w: lambda s: s.replace(w, 'X' * len(w), 1))(hit)
         if hit else None, hit is not None),
    ]

    orig = D
    base = Path(tempfile.mkdtemp(prefix='fmtdrift_'))
    ok = 0
    try:
        for name, fn, usable in cases:
            if not usable:
                print(f'❓ {name}: 注入目标不在成品里，用例失效')
                continue
            tmp = base / 'p'
            shutil.copytree(orig, tmp)
            repack(orig / 'eswa_paper_v2_en.docx',
                   tmp / 'eswa_paper_v2_en.docx', fn)
            D = tmp
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main()
            out = buf.getvalue()
            caught = rc == 1 and '❌' in out
            ok += caught
            # 报出的须是 problems 区那一行（缩进 3 空格），不是 F 段的备注
            line = next((l.strip() for l in out.splitlines()
                         if l.startswith('   ') and l.strip()[:2] in ('EN', 'ZH')
                         and 'mtime' not in l and '圈码' not in l), '')
            print(f'{"✅" if caught else "❌"} {name}：退出码 {rc}'
                  + (f'，报出 {line[:70]}' if caught else ' —— 没报出来！'))
            shutil.rmtree(tmp, ignore_errors=True)

        # B 段 TeX 行用例：只动 .tex 里的一个数（DOCX / PDF 与日志都不动，
        # 故 A–F 其余各行照常全绿），断言 B 段的 TeX 行能把这处"TeX 与源脱节"
        # 单独抓出来——否则 TeX 行是只核了个必然为真的空集
        D = orig
        tmp = base / 'tex'
        shutil.copytree(orig, tmp)
        texp = tmp / 'eswa_paper_v2_en.tex'
        if texp.exists() and '16,323' in texp.read_text(encoding='utf-8'):
            texp.write_text(texp.read_text(encoding='utf-8')
                            .replace('16,323', '16,333'), encoding='utf-8')
            D = tmp
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main()
            out = buf.getvalue()
            caught = rc == 1 and '个正文数字未出现在 .tex 里' in out
            ok += caught
            line = next((l.strip() for l in out.splitlines()
                         if '个正文数字未出现在 .tex 里' in l), '')
            print(f'{"✅" if caught else "❌"} B  TeX 数字脱节：退出码 {rc}'
                  + (f'，报出 {line[:70]}' if caught else ' —— 没报出来！'))
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print('❓ B  TeX 数字脱节：.tex 里没有 16,323，用例失效')

        # B 段 PDF 行用例：PDF 是二进制的，无法像 DOCX 那样重打包，改用
        # PyMuPDF 把 `16,323` 的**全部 5 处** redact 掉（只擦一处不够——
        # 判据是"token 出现过没有"，剩下 4 处照样命中，用例会静默失效）。
        # DOCX / TeX / 日志都不动，故 A–F 其余各行照常全绿
        D = orig
        tmp = base / 'pdf'
        shutil.copytree(orig, tmp)
        pdfp = tmp / 'eswa_paper_v2_en.pdf'
        hits = 0
        if pdfp.exists():
            try:
                import fitz
                doc = fitz.open(str(pdfp))
                for page in doc:
                    rects = page.search_for('16,323')
                    if not rects:
                        continue
                    hits += len(rects)
                    for r in rects:
                        page.add_redact_annot(r)
                    page.apply_redactions()
                tmp_pdf = tmp / '_r.pdf'
                doc.save(str(tmp_pdf), garbage=4, clean=True)
                doc.close()
                tmp_pdf.replace(pdfp)
            except ImportError:
                pass          # 未装 PyMuPDF 时 hits 保持 0，由下面统一提示
        if hits:
            D = tmp
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main()
            out = buf.getvalue()
            # 报出行须同时是 `EN PDF: ` 开头**且**含"个数字丢失"——只按前缀找会
            # 命中 F 段的 `EN PDF: 1 个 DOI 前缀…已登记`（它排在 B 段报错行之前）
            line = next((l.strip() for l in out.splitlines()
                         if l.strip().startswith('EN PDF: ') and '个数字丢失' in l), '')
            caught = rc == 1 and bool(line)
            ok += caught
            print(f'{"✅" if caught else "❌"} B  PDF 数字擦除（{hits} 处）：'
                  f'退出码 {rc}' + (f'，报出 {line[:60]}' if caught else ' —— 没报出来！'))
            shutil.rmtree(tmp, ignore_errors=True)
        elif pdfp.exists():
            print('❓ B  PDF 数字擦除：PDF 里搜不到 16,323，用例失效')

        # G 用例：只把日志目录换到注入过的副本（D 不动，A–F 段照常全绿），
        # 断言 G 段能把字形缺失抓出来——否则 G 段是唯一没有失败路径自证的判据
        D = orig        # 上一轮的 tmp 已删，须先摆回，否则 A–F 段读不到文件
        logdir = base / 'logs'
        logdir.mkdir()
        n_log = 0
        for tag, (md, dx, tx, pf) in PAPERS.items():
            src = orig / '.build' / (Path(tx).stem + '.log')
            if src.exists():
                (logdir / src.name).write_text(
                    src.read_text(encoding='utf-8', errors='replace')
                    + '\nMissing character: There is no ① in font\n',
                    encoding='utf-8')
                n_log += 1
        if n_log == 2:
            LOGD = logdir
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main()
            out = buf.getvalue()
            caught = rc == 1 and 'Missing character' in out
            ok += caught
            print(f'{"✅" if caught else "❌"} G  TeX 日志字形缺失：退出码 {rc}'
                  + ('' if caught else ' —— 没报出来！'))
        else:
            print(f'❓ G  日志副本只有 {n_log}/2 份，用例失效')
    finally:
        D = orig
        LOGD = orig / '.build'
        shutil.rmtree(base, ignore_errors=True)

    print('\n' + '=' * 72)
    if ok != 7:
        print(f'❌ 失败路径 {ok}/7——有漂移抓不住')
        return 1
    print('✅ 失败路径 7/7：数字 / 转义 / 结构 / 正文文字 / TeX 数字 / '
          'PDF 数字 / TeX 日志七类漂移都能抓住')
    return 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    if '--drift-test' in sys.argv:
        sys.exit(drift_test())
    sys.exit(main('--verbose' in sys.argv))
