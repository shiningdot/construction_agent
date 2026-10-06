#!/usr/bin/env python3
"""V2 数字对账：修订对账记录「数字对账清单」 ↔ 正文实际数字。

CLAUDE.md 改稿流程第 4 条：正文任何数字必须与对账清单的"数字对账清单"一致。
与 check_citations_v2.py（文献 ↔ 正文）、check_xref_v2.py（图表/章节）并列，
补上第三类最容易出错、也最难靠肉眼发现的缺陷：数字。

三项检查：

  A. 清单 → 正文：每个统一值在中英正文各出现几次。
     仅一侧出现 = 某一版漏写；两版均缺 = 该值没进论文（除非在 EXEMPT 里
     说明是推导中间值）。这一项决定退出码。

  B. 正文 → 清单：正文里"像实验数据但不在清单上"的数字 token。
     年份、编号、DOI、页码属结构性噪声，自动归类后不列出；真正可疑的
     （三位以上整数、非编号小数）列出来供人工裁决。

  C. 邻近值扫描：与清单值极像却不相等的小数——笔误的典型形态。
     0.847 若在别处写成 0.845 就是错，而方向 A 照样全绿。
     假配较多（表格里其他模型/策略的值），故只报告、不参与退出码。

清单值硬编码自 修订对账记录.md 第二节；改动那里时须同步改这里。

用法：python3 scripts/check_numbers_v2.py [--verbose]
退出码 0 = 清单值中英对称且无缺失，可接进改稿流程。
"""
import re
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / 'docs' / 'paper_v2'
FILES = {'EN': BASE / 'eswa_paper_v2.md', 'ZH': BASE / 'eswa_paper_v2_zh.md'}

# ── 数字对账清单（转录自 修订对账记录.md 第二节） ───────────────────────
CHECKLIST = {
    'M1 总恢复量': ['0.847'], 'M1 措辞贡献': ['0.800'],
    'S1 F1': ['0.669'], 'S4 F1': ['0.656'], 'S5 F1': ['0.665'],
    'v1 F1': ['0.503'], 'v1 召回': ['0.423'], 'v1 的 M1 召回': ['0.141'],
    'explicit': ['14784'], 'inferred': ['1117'], 'prior': ['422'],
    '证据合计': ['16323'],
    'explicit 占比': ['90.6'], 'inferred 占比': ['6.8'], 'prior 占比': ['2.6'],
    '类型 12 类条数': ['2617'], '事故总数': ['2623'],
    '等级 一般': ['1889'], '等级 较大': ['672'], '等级 重大': ['53'],
    '等级 特重': ['8'], '等级已标注合计': ['2622'],
    'WT 非空': ['2202'], 'EQP 非空': ['1373'], 'MAT 非空': ['1465'],
    'WKR 非空': ['1905'], 'BLD 非空': ['359'],
    '网络采集条数': ['1728'], '网络采集占比': ['65.9'],
    '文献条数': ['895'], '文献占比': ['34.1'],
    'safehoo': ['1247'], 'esafety': ['389'], '省级应急厅': ['89'],
    '樊锡仁': ['703'], '住建部汇编': ['192'],
    # §3.2.1 标注层来源构成：2026-10-05 结清「34% 书源」待办时新增。
    # 数字源 = 主库 caics_causes ⋈ accidents.source_name（895 条书源下的编码与逐字证据），
    # 中英各出现 1 次，登记以保证将来改一处必同步另一处。
    '书源致因标注数': ['6270'], '书源致因标注占比': ['38.4'],
    '书源逐字证据数': ['6081'], '书源逐字证据占比': ['41.1'],
    '1978-1990 条数': ['698'], '1978-1990 占比': ['26.6'],
    '2005-2026 条数': ['1895'], '2005-2026 占比': ['72.2'],
    '无日期条数': ['30'], '无日期占比': ['1.1'],
    '多源合并记录': ['45'],
    # CLIP 过滤图片数：§3 正文那句与表 4 都写 226，须两处一致。
    # 源 = 磁盘 imageV3/ 目录（不在主库），由 check_tables_v2/check_figures_v2 核
    'CLIP 过滤图片': ['226'],
    # 两个行业媒体门户的记录数（2026-10-05 ★A1 落位时新增）：§3.2.2 描述采集四族
    # 构成、§3.5 描述搜索增强、表 4 第三行，中英各 3 处。此前三处都在清单外——
    # 改一处不跟另两处，核验发现不了。实测（主库 source_name 统计 + search_logs
    # 交叉核）两处指同一集合：safehoo 1,247 + esafety 389 = 1,636，门户独有 0 条、
    # 搜索日志独有 0 条 ⇒「搜索增强覆盖的记录」恰好就是「两个门户的全部记录」。
    # ⚠ 本条判据的边界（2026-10-05 注入实测，非推理）：A 段判的是「中英两版都出现过」，
    #   不是「出现次数相等」。把 EN 三处 1,636 全改掉 → 报「仅ZH版有」、退出码 1；
    #   只改 EN 其中一处（3 处→2 处）→ 仍判 ✅、退出码 0。⇒ 同一版内多处写不一致，
    #   本脚本抓不到，须人工复核三处同版一致（B 段只给"待裁决"提示、不判红）。
    '门户记录数': ['1636'], '门户占比': ['62.4'],
    # §4.4.2 编码密度：2026-10-04 源数据核验时修正（原 V1 旧稿值 5.0/3.6/6.5 全错）
    '真值编码密度': ['5.1'], 'S1 编码密度': ['4.2'],
    'S0 编码密度': ['2.8'], 'S4 编码密度': ['9.2'],
    # 以下三项在对账清单上是独立条目，但值都是个位数：
    '未标注类型': ['6'], '未标注等级': ['1'], 'mem': ['3'],
}

# 两版均不出现也属正常的值：正文只报各项与终值，不写推导中间值。
EXEMPT = {
    '2622': '计数闭合的推导中间值（1,889+672+53+8），正文只报各项与终值 2,623',
}

# 个位数条目：正文里裸 "3" 会命中几十次（页码、编号、类目计数），
# 做存在性判罚只会误报。登记在案以保证与对账清单逐条对应，但不参与判定。
DIGIT_ONLY = {'6': '未标注类型数', '1': '未标注等级数', '3': 'mem 来源条数'}

# 正文用拼写数字写出、数字 token 抓不到的清单值：由人工确认后登记。
WORD_FORM = {
    '45': 'EN 写作 "Forty-five records"（行 125）',
}

NUM = re.compile(r'\d[\d,]*(?:\.\d+)?')
DECI = re.compile(r'\d+\.\d+')
YEARLY = re.compile(r'^(?:19|20)\d{2}$')


def norm(text):
    """全角 → 半角，使正文的 （90.6％） 与清单的 90.6 可比。"""
    out = []
    for ch in text:
        if 0xFF01 <= ord(ch) <= 0xFF5E:
            out.append(chr(ord(ch) - 0xFEE0))
        elif ch == '％':
            out.append('%')
        else:
            out.append(ch)
    return ''.join(out)


def is_meta(line):
    s = line.strip()
    return s.startswith('*V2 修订说明') or s.startswith('*V2 Revision')


def load(path):
    lines = path.read_text(encoding='utf-8').split('\n')
    body = '\n'.join(l for l in lines if not is_meta(l))
    return lines, norm(body)


def tokens(text):
    return Counter(m.group(0).replace(',', '') for m in NUM.finditer(text))


def main(verbose=False):
    toks, lines = {}, {}
    for tag, path in FILES.items():
        lines[tag], body = load(path)
        toks[tag] = tokens(body)

    known = {v for vals in CHECKLIST.values() for v in vals}
    problems = []

    # ── A. 清单 → 正文 ────────────────────────────────────────────
    print('A. 清单 → 正文（每个统一值在中英正文的出现次数）')
    print(f'{"清单项":<20}{"值":<9}{"EN":>4}{"ZH":>4}  判读')
    print('-' * 62)
    for label, vals in CHECKLIST.items():
        for v in vals:
            e, z = toks['EN'].get(v, 0), toks['ZH'].get(v, 0)
            if v in DIGIT_ONLY:
                verdict = f'· 个位数（{DIGIT_ONLY[v]}），仅登记'
            elif e and z:
                verdict = '✅'
            elif v in EXEMPT:
                verdict = '✅ ' + EXEMPT[v]
            elif v in WORD_FORM and e + z:
                verdict = '✅ ' + WORD_FORM[v]
            elif e or z:
                verdict = f'⚠ 仅{"EN" if e else "ZH"}版有'
                problems.append((label, v, verdict))
            else:
                verdict = '⚠ 两版均未出现'
                problems.append((label, v, verdict))
            print(f'{label:<20}{v:<9}{e:>4}{z:>4}  {verdict}')

    # ── B. 正文 → 清单 ────────────────────────────────────────────
    print('\nB. 正文中"像实验数据但不在清单上"的数字')
    noise = 0
    for tag in ('EN', 'ZH'):
        odd = []
        for tok, n in toks[tag].items():
            if tok in known or YEARLY.match(tok):
                continue
            if re.fullmatch(r'\d{1,2}', tok) or re.fullmatch(r'\d{1,2}\.\d+', tok):
                noise += 1
                continue
            # DOI 片段（10.1016…）与页码/卷号不计
            if re.fullmatch(r'10\.\d+', tok) or re.fullmatch(r'0\d{4,}', tok):
                noise += 1
                continue
            odd.append((tok, n))
        if odd:
            print(f'  {tag}: {len(odd)} 个待裁决')
            for tok, n in sorted(odd, key=lambda x: -x[1])[:30]:
                print(f'    {tok:<14} ×{n}')
        else:
            print(f'  {tag}: ✅ 无')

    # ── C. 邻近值扫描 ─────────────────────────────────────────────
    print('\nC. 邻近值扫描（小数末位笔误；假配多，仅供人工过目）')
    hits = 0
    for tag in ('EN', 'ZH'):
        raw = norm('\n'.join(lines[tag])).split('\n')
        for i, l in enumerate(raw, 1):
            if is_meta(l) or 'doi.org' in l.lower():
                continue
            for m in DECI.finditer(l):
                tok = m.group(0)
                v = float(tok)
                for label, vals in CHECKLIST.items():
                    for rv in vals:
                        ref = float(rv)
                        if v == ref or '.' not in rv:
                            continue
                        if len(tok.split('.')[1]) != len(rv.split('.')[1]):
                            continue
                        if abs(v - ref) / abs(ref) < 0.005:
                            print(f'  {tag} 行{i}: {tok} ≈ {label}({rv})')
                            print(f'      …{l.strip()[:110]}…')
                            hits += 1
                            break
                    else:
                        continue
                    break
    print(f'  → {hits} 处' if hits else '  → ✅ 无')

    # ── 汇总 ──────────────────────────────────────────────────────
    print('\n' + '=' * 62)
    if problems:
        print(f'❌ {len(problems)} 项待处置：')
        for label, v, verdict in problems:
            print(f'   {label} = {v}  {verdict}')
        return 1
    total = sum(len(v) for v in CHECKLIST.values())
    print(f'✅ 清单 {total} 个值全部合规：中英对称、无缺失'
          f'（{len(EXEMPT)} 项推导中间值 + {len(WORD_FORM)} 项拼写形式 + '
          f'{len(DIGIT_ONLY)} 项个位数已登记）')
    return 0


if __name__ == '__main__':
    sys.exit(main('--verbose' in sys.argv))
