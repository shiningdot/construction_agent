#!/usr/bin/env python3
"""四格式成品一致性核验（第九类核验）。

此前八项核验**全部跑在 md 源上**——md 全绿只说明"源没问题"，不说明
`build_v2_docs.py` 生成的 DOCX / TeX / PDF 没问题。而本项目真实发生过的
成品缺陷（纸张误为 US Letter、中文标题渲染成豆腐块、9 个四级标题整行丢失、
`$t_i$` 被转义破坏成字面下划线、圈码无字形）**全是在成品里、全是人工发现的**。

本脚本把"成品是否真的跟上了源"固化为可复跑核验。八个维度：
  A 产物齐全（六个文件非空）
  B 数字无损：md 里的数字 token 须全部出现在 DOCX 与 PDF 里
  B2 正文文本无损：md 段落首 24 字符须在 DOCX 里（抓"改了文字忘了重跑"）
  C 转义残留：不得出现裸 `$` 或未展开的 `\\command`
  D 结构对等：md 的 ##/###/#### ↔ DOCX 的 Heading1/2/3；表格数、内嵌图数、中英对称
  E 纸张：两版 PDF 均须 A4（Elsevier 要求，article 类默认 letterpaper 是坑）
  F 已登记的差异与提示（列出但不计错）：圈码表示法、DOI 断行、mtime 落差

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


def escapes(text):
    """未展开的 TeX 残留：裸 $ 或 \\command（本项目正文不含 LaTeX 源码）。"""
    return {'$': text.count('$'),
            r'\command': len(re.findall(r'\\[a-zA-Z]+', text))}


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

    print('\n' + '=' * 72)
    if fails:
        print(f'❌ {fails} 条自测失败——判定逻辑有问题')
        return 1
    print('✅ 自测全过：标题/数字/残留/豁免四类判据都能报错')
    return 0


def drift_test():
    """失败路径自证：把漂移注入成品，脚本必须报错。

    往 DOCX 的 document.xml 里注入四类漂移（改数字 / 插 `$` / 动标题样式 /
    改正文文字），逐例跑主流程并断言退出码转 1。DOCX 是 zip，重打包即可，
    成品不受影响（全程在临时目录）。
    """
    import shutil
    import tempfile

    global D

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
    finally:
        D = orig
        shutil.rmtree(base, ignore_errors=True)

    print('\n' + '=' * 72)
    if ok != 4:
        print(f'❌ 失败路径 {ok}/4——有漂移抓不住')
        return 1
    print('✅ 失败路径 4/4：数字 / 转义 / 结构 / 正文文字四类漂移都能抓住')
    return 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    if '--drift-test' in sys.argv:
        sys.exit(drift_test())
    sys.exit(main('--verbose' in sys.argv))
