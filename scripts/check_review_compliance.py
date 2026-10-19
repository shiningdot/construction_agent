#!/usr/bin/env python3
"""导师 14 项评阅意见 → V2 修订动作的落实核验。

## 这份脚本核的不是论文，是「改稿流程自己」

既有九项（check_v2_all.py）全部把论文**自身**当被核对象：引用对不对、
结构齐不齐、数字准不准、措辞越不越线、表格/图/成品/Highlights/投稿信。
而 `docs/paper_v2/修订对账记录.md` 第一节那张「评阅意见 → 修订动作对照表」
是**改稿流程的欠条**——它声称 14 条意见都已落实，但那张表是**自然语言写的、
从未被机器核过**，写下的那一刻为真，之后不会自我更新。

本轮（2026-10-05）核它，当场抓到两个实例：

  · **错误断言**：「V2 通篇未使用 Fleiss κ」这句话写在五处副本里
    （CLAUDE.md / V1 投稿信存档块 / cover_letter_v2.md / 本目录脚本 docstring /
    对账记录第十五节），而正文中英两版**各有 2 处** Fleiss' kappa 0.859–1.000
    （§3.3 与 §4.1）。`git log -S` 证明正文那句属于 V2 初版 `0042e9b`、
    比写下断言的 `bace99e` **更早**——即断言写下的当时就是错的，不是事实后来翻转。
    真问题是别的东西（见 B 段：`verified expert applicability` 把**标注者之间
    的一致性**说成了**体系适用性验证**，过度解读，对应导师第 8 条）。

  · **陈旧数字**：第一节表第 3 行写「对应 DB evidence_quality 字段：
    15,809/1,131/424」，而正文是 14,784/1,117/422（孤儿外键清理后）。
    同一份文件第 128、155 行**写法正确**（那是"改前→改后"对照语境），
    说明这是誊抄疏漏而非理解错误——**该表不会自我更新**（C 段抓的就是这一类）。

## 三段对应 14 条意见的三种落实形态

  A. **须存在的表述**（14 条 × 中英各 1 条特征串，各须出现 ≥1 次）
     直接证据：「修订动作真写进正文了」。同时是**中英论断句对称**的首次核验
     ——既有脚本只核数字 token 对称，没核过论断句。

  B. **须消失的表述**（第 8 类禁用措辞：`排除替代解释` / ruling out alternative
     explanations）。按 §2.5.52「能引已有凭据就引」，导师 7 类禁用措辞
     （首次/推翻/证明/因果机制/专家级/替代人工/全部编码有证据）**由
     `check_forbidden_v2.py` 负责，本脚本不重造**；这里只补它名单上**没有的**
     那一类——第一节表第 6 行明确声称「删除"排除替代解释"」，那是导师第 6 条
     的原文要求，却不在 7 类名单上。

  C. **须对账的数字**（第一节表「修订动作」列引用的数字 ↔ 正文）
     全新的一类核验：**核修订对账表自己引的数字对不对**。判据带一个精确的
     豁免口——标注为「改前值」的数字可不在正文（`修正…残留的 0.64/0.62`、
     `52.3%→83.9%` 这类"改前→改后"对照），其余不在正文者判红。
     这不是白名单（白名单是"只写理由不验证"，§2.5.67），豁免取**结构性依据**：
     数字邻近窗口内有无改前值标记。同一行内带标记与不带标记的数字走两条路。

## 判据边界（明写）

  · A 段核的是「这句特征串在不在」，**核不到它在上下文中是否承担了正确的论证角色**
    （把「我们不声称因果机制」写进一个前后矛盾的段落，A 段照样绿）。
  · B 段只覆盖第 8 类；**前 7 类不在本脚本射程**，别把它读成"禁用措辞全查过了"。
  · C 段只核**第一节表「修订动作」列**的数字；`位置` 列（`§3.1/表4` 这类）与
    第二至 N 节的其他表格**不在射程**——那些节的过期风险同样存在，只是本轮
    没有对应的判据（如实登记，不假装核过）。
  · C 段的豁免是**窗口式**的：数字前 12 字 / 后 3 字内有改前值标记即豁免。
    把 `15,809` 改写成「修正后的 15,809」会让它被豁免——**豁免即放弃核验**，
    这是判据的已知薄弱面，靠 A 段的中英特征串（第 3 条含精确值）另行兜底。

用法：
  python3 scripts/check_review_compliance.py            # 正常核验
  python3 scripts/check_review_compliance.py --self-test # 判据自证
  python3 scripts/check_review_compliance.py --drift-test # 注入式自证（三注入面）
退出码 0 = 14 条落实、第 8 类措辞已清除、第一节表数字无悬空。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_numbers_v2 as CN  # noqa: E402  （复用其豁免表，§2.5.52 能引已有凭据就引）

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'docs' / 'paper_v2'
EN_PAPER = BASE / 'eswa_paper_v2.md'
ZH_PAPER = BASE / 'eswa_paper_v2_zh.md'
RECORD = BASE / '修订对账记录.md'

# ── A 段：14 条意见 × 中英特征串（各须出现 ≥1 次） ─────────────────────
# 特征串取自正文原文，逐字复制；改动正文论断句时须同步改这里。
# 每条对应第一节表的一行，**条目总数须 == 14**（元判据见 part_a）。
COMPLIANCE = [
    ('1 主线探索性定位',
     'an exploratory data engineering effort rather than a theory-confirming study',
     '一项探索性数据工程工作，而非理论证实性研究'),
    ('2 描述性对比（非排他性新颖）',
     'We present this as a descriptive comparison, not as evidence of exclusive novelty',
     '我们将其呈现为描述性对比，而非排他性新颖性的证据'),
    ('3 三层证据分级计数',
     '14,784 annotations (90.6%) are explicit, 1,117 (6.8%) are inferred, and 422 (2.6%) are prior',
     'explicit 14,784 条（90.6%）、inferred 1,117 条（6.8%）、prior 422 条（2.6%）'),
    ('4 非独立人工金标准',
     'it is not an independent human gold standard',
     '完全独立的人工金标准'),
    ('5 开发集复用风险',
     'the strategy results carry a development-set reuse risk',
     '策略结果因此带有开发集复用风险'),
    ('6 不声称因果机制',
     'We do not claim an isolated causal mechanism.',
     '我们不声称已分离因果机制'),
    ('7 不声称分离模型依赖',
     'we do not claim to have isolated model dependence',
     '我们不声称分离出了模型依赖'),
    ('8 协议一致性限定',
     'not a certification of expert-level accuracy',
     '不是专家级准确性的认证'),
    ('9 未与既有体系对比',
     'no comparative validation against 24Model',
     '尚未在独立数据上与 24Model'),
    ('10 分类器残留错误披露',
     '11 records were excluded at annotation time as multi-accident texts',
     '原 300 条中 11 条因多事故文本被排除'),
    ('11 M1 数字全文统一',
     '0.800 of the total 0.847',
     'M1 总恢复量 0.847 中的 0.800'),
    ('12 三处运行条件不对等',
     'Kimi K2.6 enforces temperature 1.0 by API specification',
     'Kimi K2.6 按 API 规范强制 temperature 1.0'),
    ('13 评审期可核查',
     'provided for peer review upon request and released publicly upon publication',
     '将在审稿期间应要求提供，并于论文发表后公开'),
    ('14 数据治理撤回机制',
     'a takedown and deletion mechanism on request',
     '应请求提供撤回删除机制'),
]
EXPECTED_ROWS = 14

# ── B 段：第 8 类禁用措辞（前 7 类归 check_forbidden_v2.py） ───────────
# 第一节表第 6 行声称「删除"排除替代解释"」，但该措辞不在 check_forbidden_v2
# 的 7 类名单上；导师第 6 条原文要求删。故本脚本只补这一类。
DELETED_TERMS = [
    ('第6条 排除替代解释（EN）', r'ruling?\s+out\s+alternative'),
    ('第6条 排除替代解释（ZH）', r'排除替代解释'),
]
FORBIDDEN_OWNER = 'check_forbidden_v2.py'   # 前 7 类的负责脚本，仅用于输出说明

# ── C 段：第一节表数字 ↔ 正文 ────────────────────────────────────────
NUM = re.compile(r'\d[\d,]*(?:\.\d+)?')
# 改前值标记：出现即豁免（该数不需要在正文有据）
BEFORE_MARK = re.compile(r'(?:修正|勘误|残留|删除|改为|→)')
# 数字后紧跟 → 也算改前值（`52.3%→83.9%` 里 52.3 的右邻是 `%→`）
AFTER_MARK = re.compile(r'\s*%?\s*→')
BEFORE_WIN, AFTER_WIN = 12, 3

SECTION1_HEAD = '## 一、评阅意见'
META_PREFIX = ('*V2 修订说明', '*V2 Revision')


def is_meta(line):
    """V2 修订说明块是元信息（投稿时删除），不算正文。"""
    s = line.strip()
    return s.startswith(META_PREFIX)


def strip_meta(text):
    return '\n'.join(l for l in text.split('\n') if not is_meta(l))


def decomma(text):
    """去掉数字千分位逗号，使正文的 2,617 与清单 token 2617 可比。

    ⚠ 只去**半角**逗号。首版这里误写成「全角→半角」的通用归一，
    把中文的行内逗号 ，（U+FF0C）一并转成了 ,，于是**正文被归一、期望
    特征串没有** ⇒ 四条中文特征串全部假红（英文串不含全角字符故不受影响）。
    判据对语料做归一化时，**期望值必须走同一条路**，否则错误的归一
    会伪装成「正文缺失」——与 §2.5.89「注入没做与判据没报在输出上
    一模一样」同族：方向性错误在输出上不可分辨。
    """
    return text.replace(',', '')


def section1_rows(record_text):
    """取第一节表的 (序号, 修订动作) 列表；表头与分隔行排除。"""
    lines = record_text.split('\n')
    start = next((i for i, l in enumerate(lines) if l.startswith(SECTION1_HEAD)), None)
    if start is None:
        return None
    rows = []
    for l in lines[start + 1:]:
        if l.startswith('## '):
            break                      # 下一节，第一节表结束
        if not l.startswith('|'):
            continue
        cells = [c.strip() for c in l.strip('|').split('|')]
        if len(cells) < 4:
            continue
        if set(cells[0]) <= set('-: ') or cells[0] == '#':
            continue                   # 分隔行 / 表头
        rows.append((cells[0], cells[2]))
    return rows


def is_exempt(cell, m):
    """该数字是否被「改前值」标记豁免。"""
    before = cell[max(0, m.start() - BEFORE_WIN):m.start()]
    after = cell[m.end():m.end() + AFTER_WIN]
    return bool(BEFORE_MARK.search(before) or AFTER_MARK.search(after))


def part_a(en_body, zh_body):
    """A 段：14 条特征串中英各须出现 ≥1 次。返回 (reds, 元判据错误)。"""
    reds, meta_err = [], []
    if len(COMPLIANCE) != EXPECTED_ROWS:
        meta_err.append(f'COMPLIANCE 有 {len(COMPLIANCE)} 条，应为 {EXPECTED_ROWS} 条'
                        f'（与第一节表行数不符 = 漏录或重录）')
    for name, e, z in COMPLIANCE:
        ne, nz = en_body.count(e), zh_body.count(z)
        if not ne or not nz:
            missing = '中英均缺' if not (ne or nz) else ('缺英文' if not ne else '缺中文')
            reds.append((name, f'{missing}（EN={ne} ZH={nz}）'))
    return reds, meta_err


def part_b(en_line_map, zh_line_map):
    """B 段：第 8 类禁用措辞须 0 次（元信息块不算）。"""
    reds = []
    for label, pat in DELETED_TERMS:
        rx = re.compile(pat, re.I)
        src = en_line_map if '（EN）' in label else zh_line_map
        for i, l in enumerate(src, 1):
            if is_meta(l):
                continue
            for m in rx.finditer(l):
                reds.append((label, f'行{i}: …{l[max(0,m.start()-40):m.end()+40]}…'))
    return reds


def part_c(rows, en_body, zh_body):
    """C 段：第一节表数字须在正文有据，或带改前值标记。"""
    reds = []
    if rows is None:
        return [('第一节表', '锚点未命中：找不到 "## 一、评阅意见…" 标题行')]
    en_nc, zh_nc = decomma(en_body), decomma(zh_body)
    for no, cell in rows:
        for m in NUM.finditer(cell):
            tok = m.group(0).replace(',', '')
            if tok in en_nc or tok in zh_nc:
                continue
            # 数字清单已登记的豁免（推导中间值 / 个位数 / 拼写形式）——
            # 统一引自 check_numbers_v2，不在这里另立一份（§2.5.52）。
            if tok in CN.EXEMPT or tok in CN.DIGIT_ONLY or tok in CN.WORD_FORM:
                continue
            if is_exempt(cell, m):
                continue
            reds.append((f'第{no}条', f'{tok} 不在正文，且未标为改前值'))
    return reds


def run(verbose=True):
    en_raw = EN_PAPER.read_text(encoding='utf-8')
    zh_raw = ZH_PAPER.read_text(encoding='utf-8')
    rec_raw = RECORD.read_text(encoding='utf-8')

    en_body, zh_body = strip_meta(en_raw), strip_meta(zh_raw)
    en_lm, zh_lm = en_raw.split('\n'), zh_raw.split('\n')
    rows = section1_rows(rec_raw)

    ra, ma = part_a(en_body, zh_body)
    rb = part_b(en_lm, zh_lm)
    rc = part_c(rows, en_body, zh_body)

    if verbose:
        print('A. 14 条意见的落实面（特征串中英各须 ≥1 次）')
        for name, e, z in COMPLIANCE:
            ne, nz = en_body.count(e), zh_body.count(z)
            print(f'  {"✅" if (ne and nz) else "❌"} {name:<24} EN={ne} ZH={nz}')
        if ma:
            for x in ma:
                print(f'  ❌ 元判据：{x}')
        print(f'  → A 段 {len(ra)} 条待处置')

        print(f'\nB. 第 8 类禁用措辞（前 7 类由 {FORBIDDEN_OWNER} 负责，本段不重造）')
        for label, pat in DELETED_TERMS:
            print(f'  {label:<28} 正则 {pat}')
        print(f'  → B 段 {len(rb)} 条命中')
        for label, detail in rb[:10]:
            print(f'     ❌ {label}  {detail}')

        print('\nC. 第一节表「修订动作」列的数字 ↔ 正文')
        if rows is None:
            print('  ❌ 锚点未命中')
        else:
            total = sum(1 for _, c in rows for _ in NUM.finditer(c))
            print(f'  表 {len(rows)} 行 / {total} 个数字 token')
        print(f'  → C 段 {len(rc)} 条悬空')
        for no, detail in rc:
            print(f'     ❌ {no}  {detail}')

    print('\n' + '=' * 62)
    reds = ma + ra + rb + rc
    if reds:
        print(f'❌ 合计 {len(reds)} 项待处置'
              f'（A {len(ra)} / B {len(rb)} / C {len(rc)}'
              + (f' + 元判据 {len(ma)}' if ma else '') + '）')
        return 1
    print(f'✅ 14 条意见全部落实 · 第 8 类措辞已清除 · 第一节表数字无悬空')
    return 0


# ── 自证 ─────────────────────────────────────────────────────────────
SELF_CASES = [
    # (说明, 断言函数, 期望 True)
    ('数字去千分位：15,809 → 15809',
     lambda: NUM.search('15,809').group(0).replace(',', '') == '15809', True),
    ('decomma 只去半角逗号，不动中文全角逗号',
     lambda: decomma('a，b,c') == 'a，bc', True),
    ('中文特征串能在正文命中（本次假红的回归保护）',
     lambda: COMPLIANCE[0][2] in strip_meta(ZH_PAPER.read_text(encoding='utf-8')), True),
    ('改前值豁免：前窗含"修正"',
     lambda: _exempt_probe('（修正残留的 0.64）', '0.64'), True),
    ('改前值豁免：后邻是 →',
     lambda: _exempt_probe('52.3%→83.9%', '52.3'), True),
    ('非豁免：无标记的孤立数字',
     lambda: _exempt_probe('对应 DB evidence_quality 字段：15,809', '15,809'), False),
    ('非豁免：紧跟别数也无标记',
     lambda: _exempt_probe('字段：424', '424'), False),
    ('meta 行识别：修订说明块',
     lambda: is_meta('*V2 修订说明（供作者与导师查阅…删除"排除替代解释"表述）'), True),
    ('meta 行识别：正文行不算 meta',
     lambda: is_meta('本文报告了 CAIEMS——一条探索性 LLM 管道'), False),
    ('B 段正则命中「排除替代解释」',
     lambda: bool(re.search(DELETED_TERMS[1][1], '删除"排除替代解释"表述')), True),
    ('B 段正则命中 ruling out alternative',
     lambda: bool(re.search(DELETED_TERMS[0][1], 'ruling out alternative explanations', re.I)), True),
    ('B 段正则不误伤 rule of thumb',
     lambda: bool(re.search(DELETED_TERMS[0][1], 'a good rule of thumb', re.I)), False),
    ('第一节表行数 == 14（与 14 条意见一一对应）',
     lambda: len(section1_rows(RECORD.read_text(encoding='utf-8')) or []) == 14, True),
    ('第一节表首行序号为 1',
     lambda: (section1_rows(RECORD.read_text(encoding='utf-8')) or [('?',)])[0][0] == '1', True),
]


def _exempt_probe(cell, tok):
    m = NUM.search(cell)
    assert m.group(0) == tok, f'探针假设失败：{cell} 首数字是 {m.group(0)} 不是 {tok}'
    return is_exempt(cell, m)


def self_test():
    bad = 0
    for name, fn, want in SELF_CASES:
        try:
            got = fn()
        except Exception as e:                                   # noqa: BLE001
            got, name = f'异常 {e}', name + '（抛错）'
        ok = got is want
        if not ok:
            bad += 1
        print(f'  {"✅" if ok else "❌"} {name}  → {got!r}（期望 {want!r}）')
    print(f'\n{"✅" if not bad else "❌"} --self-test {len(SELF_CASES) - bad}/{len(SELF_CASES)}')
    return 1 if bad else 0


# ── 注入式自证：三个注入面，每例断言红的恰好是哪几条 ──────────────────
DRIFTS = [
    # (标签, 目标文件, 锚点串, 替换串, 期望变红的集合标识)
    ('D1 对账表引旧值 → C 段报第3条',
     RECORD, '对应 DB evidence_quality 字段：14,784/1,117/422',
     '对应 DB evidence_quality 字段：9,999', {'C:第3条'}),
    ('D2 正文删一条特征串 → A 段报 1 条',
     EN_PAPER, 'We do not claim an isolated causal mechanism.',
     'The mechanism is not isolated.', {'A:6 不声称因果机制'}),
    # ⚠ D3 的锚点是 A 段第 1 条特征串的**前缀**，替换掉它必然连带 A 段转红。
    #   这不是「归因不唯一」——是同一个注入点同时命中两条判据，
    #   期望集合须**如实照抄两条**（§2.5.68：断言红的恰好是哪几条）。
    ('D3 正文混入第8类措辞 → B 段报 1 条 + A 段连带',
     EN_PAPER, 'an exploratory data engineering effort',
     'ruling out alternative explanations',
     {'B:第6条 排除替代解释（EN）', 'A:1 主线探索性定位'}),
]


def red_ids():
    """当前工作区（未注入）的红集合标识。A/B/C 三段各带前缀，便于断言子集。"""
    en = strip_meta(EN_PAPER.read_text(encoding='utf-8'))
    zh = strip_meta(ZH_PAPER.read_text(encoding='utf-8'))
    ra, _ = part_a(en, zh)
    rb = part_b(EN_PAPER.read_text(encoding='utf-8').split('\n'),
                ZH_PAPER.read_text(encoding='utf-8').split('\n'))
    rc = part_c(section1_rows(RECORD.read_text(encoding='utf-8')), en, zh)
    return {f'A:{n}' for n, _ in ra} | {f'B:{l}' for l, _ in rb} | {f'C:{n}' for n, _ in rc}


def drift_test():
    # ⚠ 基线前置断言（§2.5.67：漂移自证的基线必须是绿的）。
    #   若基线本来就红，下面那些 `got == expect` 会把「已有的红」与
    #   「注入产生的红」混在一起——集合恰好的断言就失去了分辨力。
    base = red_ids()
    if base:
        print(f'  ❌ 基线不是绿的：未注入时已有 {sorted(base)}')
        print('     （先修掉基线缺陷再跑 --drift-test，否则注入看不出来）')
        return 1
    print('  ✅ 基线前置断言：未注入时红集合为空')

    bad = 0
    for label, path, old, new, expect in DRIFTS:
        orig = path.read_text(encoding='utf-8')
        if orig.count(old) != 1:
            print(f'  ❌ {label}：锚点命中 {orig.count(old)} 次（应为 1）')
            bad += 1
            continue
        path.write_text(orig.replace(old, new), encoding='utf-8')
        try:
            got = red_ids()
        finally:
            path.write_text(orig, encoding='utf-8')
        ok = got == expect
        if not ok:
            bad += 1
        print(f'  {"✅" if ok else "❌"} {label}\n      期望 {sorted(expect)}\n      实得 {sorted(got)}')
    print(f'\n{"✅" if not bad else "❌"} --drift-test {len(DRIFTS) - bad}/{len(DRIFTS)}')
    return 1 if bad else 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    if '--drift-test' in sys.argv:
        sys.exit(drift_test())
    sys.exit(run())
