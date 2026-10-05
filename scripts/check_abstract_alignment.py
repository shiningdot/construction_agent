#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V2 摘要 ↔ 正文主体对齐核验（对账记录第三十六节）。

为什么单开一个脚本
------------------
九项核验里没有任何一格核过「摘要」——它是全文最容易被单独读走的一段
（审稿人先读摘要、检索系统只存摘要），此前却零判据。

  · check_numbers_v2.py 的 A/B 两段问的是「某个值**在全文出现过没有**」——
    摘要的 `0.91` 与正文 §4.5 的 `0.9136` 是同一个清单项的两处出现，那个脚本
    在两者之间不作区分，故「摘要的舍入值是不是正文精确值的**正确舍入**」
    它结构上装不下（清单里登记的是 0.9136，摘要的 0.91 根本不在清单上）。
  · check_xref_v2.py 的「中英对等」是**逐标题比段落数**，摘要无子标题、
    段落数恒等 ⇒ 摘要内部的句级对等不在射程。
  · check_forbidden_v2.py 只查禁用措辞的**肯定句**用法，摘要的 scope 句
    （"不是因果机制"这类否定式）它反而不该管。

于是本节补三格：①摘要数字的**出处**（是正文的哪个值 / 哪条实验）；
②摘要**舍入概括**的正确性（**计算式判据**，不是文本比对）；
③摘要三段实验的 **scope 句**在中英各版是否都在（分别判定，抓不对称）。

判据骨架
--------
A 段 摘要数字 → 正文主体。DECI（含小数点）三态 exact / rounded / unregistered🔴；
     INT（纯整数）走归属登记表（每条带一条正文探针）。
     两张登记表**双向**核（§2.5.74：登记了而摘要里没有 = 过期登记，同样报红）。
     另有闭式：中英摘要的 DECI 集合须相等（数值结论不该因语言而异）。
B 段 舍入概括的**计算式**判据——从正文**表 9 现读**六个精确值（按 metric 列
     认行，不按位置），摘要写的区间 / 单值 / 百分数必须是它们的正确舍入。
     正文任一精确值变动而摘要未跟 ⇒ 转红。这是九项里唯一覆盖这格的判据。
C 段 EIC-3 具名靶点现况（主编点名的两条 overclaim 须零命中）——把"已落实"
     这句话本身变成可证伪的（第三十五节 D 段「不判红」留下的那个口子）。
D 段 摘要三段 scope 句 + 总括句，**中英各自独立判定**。
E 段 跨脚本一致性：本条赖以成立的 EIC-3 探针修正仍在位 + 第三十五节统计已同步。
F 段 本节实测数字须逐字落在对账记录第三十六节（防"改了脚本忘改文档"）。

边界见文件尾 BOUNDARIES / 对账记录第三十六节。
一句话：**词形命中证明"这句话在"，不证明"它够不够"。**
"""

import argparse
import json
import os
import re
import sys
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# ── 路径一律仓库根派生（scripts/ 上溯一级；worktree 与主仓库同一份代码都成立）──
ROOT = Path(__file__).resolve().parents[1]
PAPER_EN = ROOT / 'docs' / 'paper_v2' / 'eswa_paper_v2.md'
PAPER_ZH = ROOT / 'docs' / 'paper_v2' / 'eswa_paper_v2_zh.md'
REC = ROOT / 'docs' / 'paper_v2' / '修订对账记录.md'
LEDGER = ROOT / 'scripts' / 'check_review_ledger_integrity.py'
# 本脚本自身——D5 注入的是 `ROUNDED`，它长在**这个文件**里，不在 LEDGER 里。
# 故 `_reds_of()` 也须能改指一份副本（`CAA_SELF`），否则那条用例锚点永远命中不了。
SELF = Path(__file__).resolve()


def _p(env: str, default: Path) -> Path:
    """允许用环境变量把任一输入指到副本上（--drift-test 的注入面靠它）。

    注入改副本、判据读真文件 ⇒ 注入等于没做（§2.5.95），故每一个被注入的
    输入都必须有这条通路。
    """
    return Path(os.environ[env]) if env in os.environ else default


def read(p: Path) -> str:
    return p.read_text(encoding='utf-8')


# ─────────────────────────── A 段：摘要数字 → 正文主体 ───────────────────────────

ABS_RE = re.compile(r'^## (?:Abstract|摘要)\n(.*?)\n---', re.S | re.M)
NUM_RE = re.compile(r'\d[\d,]*(?:\.\d+)?')
# 关键词行不算摘要正文（本项目关键词里没有数字，剥掉是为了不依赖这个巧合）
KW_RE = re.compile(r'\n\*\*(?:Keywords|关键词)\*\*.*$', re.S)


def split_abstract(text: str) -> tuple[str, str]:
    """返回 (摘要, 正文主体)。正文主体 = 全文去掉摘要那一块。"""
    m = ABS_RE.search(text)
    if not m:
        raise SystemExit('✗ 找不到摘要块（标题形态：## Abstract / ## 摘要）')
    ab = KW_RE.sub('', m.group(1))
    return ab, text[:m.start()] + text[m.end():]


def nums(text: str) -> list[str]:
    """抽数字 token，`2,623` 归一成 `2623`（千分位是排版不是数值）。"""
    return [m.group(0).replace(',', '') for m in NUM_RE.finditer(text)]


def is_deci(tok: str) -> bool:
    return '.' in tok


# 舍入登记：摘要写了、而正文没有字面形态、且**是**正文某精确值的正确舍入。
# 这一栏本身不作为"免检"——B 段用**计算式**从正文表 9 现读并复算，
# 登记表写错（或正文改了而摘要没跟）B 段立刻转红。
ROUNDED = {
    '0.91': '表 9 三个分类字段 κ（0.9136/0.9477/0.9746）两位舍入后的下界',
    '0.97': '同上上界',
    '0.985': '表 9 两个计数字段 ICC（0.9849/0.9853）三位舍入（两值舍到同一数）',
}

# 整数归属登记：每条带一条正文探针（须在**正文主体**命中，摘要自身不算）。
# 探针是「这个数在正文里以什么形态出现」，不是「这个数出现过」——
# 后者等于没核（1 与 19 在正文里到处都是）。
INT_ORIGIN = {
    'en': {
        '2623': ('全库记录总数', r'2,623'),
        '33': ('基础字段数', r'33 fields'),
        '16323': ('CAICS 致因标注总数', r'16,323'),
        '5': ('CAICS 层数（正文写作 five layers / five-layer）',
              r'five layers|five-layer'),
        '19': ('CAICS 类数（正文写作 nineteen categories）',
               r'19-category|nineteen categor'),
        '200': ('实验一/二的样本量', r'200 records'),
        '100': ('实验二的样本量', r'100 records'),
        '1': ('标识符内数字（F1 / M1 里的 1，非数量）', r'M1 recall|F1 ='),
        '289': ('一致性研究样本量', r'n = 289'),
    },
    'zh': {
        '2623': ('全库记录总数', r'2,623'),
        '33': ('基础字段数', r'33 字段|33 个'),
        '16323': ('CAICS 致因标注总数', r'16,323'),
        '5': ('CAICS 层数', r'5 层'),
        '19': ('CAICS 类数', r'19 类'),
        '4': ('实验一比较的模型数', r'4 个 LLM'),
        '200': ('实验一/二的样本量', r'200 条'),
        '100': ('实验二的样本量', r'100 条'),
        '1': ('标识符内数字（F1 / M1 里的 1，非数量）', r'M1 召回|F1='),
        '289': ('一致性研究样本量', r'n=289'),
    },
}

# 元组顺序须与 `_p(env, default)` 一致：**先环境变量名、后默认路径**。
# 写反了不会报错、只会在解包时静默把路径当环境变量名用（首版就是这样，
# `os.environ[PosixPath(...)]` 抛 TypeError 才发现）——故 self-test 有一条元判据钉住。
LANG_PATH = {'en': ('CAA_EN', PAPER_EN), 'zh': ('CAA_ZH', PAPER_ZH)}


def _abstract_of(lang: str) -> tuple[str, str]:
    return split_abstract(read(_p(*LANG_PATH[lang])))


def part_a() -> tuple[list[str], dict]:
    reds: list[str] = []
    stats: dict[str, dict] = {}
    deci_sets: dict[str, set] = {}
    for lang in ('en', 'zh'):
        ab, body = _abstract_of(lang)
        toks = nums(ab)
        dset = {t for t in toks if is_deci(t)}
        iset = {t for t in toks if not is_deci(t)}
        bset = set(nums(body))
        deci_sets[lang] = dset
        n_exact = n_round = 0

        # A1 DECI：精确命中正文，或在舍入登记表里
        for t in sorted(dset):
            if t in bset:
                n_exact += 1
            elif t in ROUNDED:
                n_round += 1
            else:
                reds.append(f'A1:{lang}:{t} 摘要的 {t} 既非正文精确值、亦不在舍入登记表')
        # A2 DECI 登记的反向：登记了而该语言摘要里没有 ⇒ 过期登记
        for t in sorted(ROUNDED):
            if t not in dset:
                reds.append(f'A2:{lang}:{t} 舍入登记「{t}」在该语言摘要里不存在（过期登记）')

        # A3 INT 归属：登记→摘要 与 摘要→登记 双向
        reg = INT_ORIGIN[lang]
        for t in sorted(iset):
            if t not in reg:
                reds.append(f'A3:{lang}:{t} 摘要整数 {t} 未登记归属（不知出自正文何处）')
        for t in sorted(reg):
            desc, probe = reg[t]
            if t not in iset:
                reds.append(f'A3:{lang}:{t} 归属登记「{t}」在该语言摘要里不存在（过期登记）')
            elif not re.search(probe, body):
                reds.append(f'A3:{lang}:{t} 归属探针在正文主体零命中：{probe}（{desc}）')

        stats[lang] = {'tokens': len(toks), 'deci': len(dset), 'int': len(iset),
                       'exact': n_exact, 'rounded': n_round, 'unreg': len(dset) - n_exact - n_round}

    # A4 闭式：中英摘要的 DECI 集合须相等——数值结论不该因语言而异
    #    （*不*要求 INT 集合相等：EN 写 four LLMs、ZH 写 4 个 LLM 是正当的词形差异）
    if deci_sets['en'] != deci_sets['zh']:
        reds.append(f"A4:deci-set 中英摘要的数值 token 不一致："
                    f"仅 EN {sorted(deci_sets['en'] - deci_sets['zh'])}、"
                    f"仅 ZH {sorted(deci_sets['zh'] - deci_sets['en'])}")
    return reds, stats


# ─────────────────────────── B 段：舍入概括的计算式判据 ───────────────────────────

def dec_round(s: str, nd: int) -> str:
    """按 nd 位小数取整（HALF_UP）。

    **不能用 round()**，两种分叉都实测过（2026-10-05）：
      · **二进制表示误差**：`round(0.985, 2)` → **0.98**（0.985 的浮点值略小于
        0.985），正确的百分位舍入是 **0.99**。表 9 的 κ 正是「三位小数取两位」
        这一档——换一组数据（比如某行 κ = 0.9850）判据就会**自己算错并报假红**，
        而那不是漏报，它会让作者去改本来正确的正文。
      · **银行家舍入**：`round(2.5)` → **2**，HALF_UP → **3**。
    注：旧注释曾断言「`0.9965*100` 在浮点里是 `99.64999999999999`、round 给 99.6」
    ——**实测是 `99.65`、round 给 99.7**，不分叉，那句话是错的、已删。
    浮点陷阱要按实测说，不能按"听起来经典"说（同 §2.5.89 的教训）。
    """
    q = Decimal(1).scaleb(-nd)
    return str(Decimal(s).quantize(q, rounding=ROUND_HALF_UP))


def read_table9(text: str) -> tuple[list[str], list[str], list[str]]:
    """从正文**现读**表 9 的六个精确值，按 **metric 列**认行（不按位置）。

    metric 列是 `Cohen's κ` / `ICC(2,1)` / `Match rate`；主值取该行第三格里的
    第一个十进制数（`0.9136 [0.8747, 0.9466]` 取 0.9136）。
    按位置取行会在表 9 上下增删一行时静默错位，故这里只认语义。
    """
    kappa: list[str] = []
    icc: list[str] = []
    date: list[str] = []
    for line in text.splitlines():
        if not line.startswith('|'):
            continue
        c = [x.strip() for x in line.strip('|').split('|')]
        if len(c) < 3:
            continue
        metric, val = c[1], c[2]
        m = re.search(r'(\d\.\d+)', val)
        if not m:
            continue
        v = m.group(1)
        if 'κ' in metric or 'kappa' in metric.lower():
            kappa.append(v)
        elif 'ICC' in metric:
            icc.append(v)
        elif 'Match' in metric:
            date.append(v)
    return kappa, icc, date


def part_b() -> tuple[list[str], dict]:
    reds: list[str] = []
    # 真值侧只取英文主源（英文是主源，中文是镜像）；摘要侧两个语言各判一次，
    # 这样"中文摘要写错了舍入"不会被英文那一遍掩盖。
    _k, _i, _d = read_table9(read(_p(*LANG_PATH['en'])))
    if not (len(_k) == 3 and len(_i) == 2 and len(_d) == 1):
        # 读不出/读歪了 → 判红并**不再往下判**：拿空集合继续判会得出"无差异"，
        # 那是把"没比"冒充成"比过且通过"（§2.5.73）。
        reds.append(f'B0:table9 表 9 现读行数不对 kappa={len(_k)} icc={len(_i)} date={len(_d)}'
                    f'（期望 3/2/1）')
        return reds, {}

    lo = min(dec_round(v, 2) for v in _k)
    hi = max(dec_round(v, 2) for v in _k)
    icc_r = sorted({dec_round(v, 3) for v in _i})
    dt = dec_round(str(Decimal(_d[0]) * 100), 1)

    for lang in ('en', 'zh'):
        ab, _ = _abstract_of(lang)
        m = re.search(r'(\d\.\d+)\s*[–—-]\s*(\d\.\d+)', ab)
        if not m:
            reds.append(f'B:{lang}:kappa 摘要里找不到 `a–b` 形态的区间')
        elif (m.group(1), m.group(2)) != (lo, hi):
            reds.append(f'B:{lang}:kappa 摘要写 {m.group(1)}–{m.group(2)}，而表 9 三值 '
                        f'{_k} 的正确两位舍入是 {lo}–{hi}')
        if len(icc_r) != 1:
            reds.append(f'B:{lang}:icc 表 9 两值 {_i} 三位舍入后不唯一：{icc_r}')
        elif not re.search(re.escape(icc_r[0]) + r'(?!\d)', ab):
            reds.append(f'B:{lang}:icc 摘要未写 {icc_r[0]}（表 9 ICC {_i} 的三位舍入）')
        if not re.search(re.escape(dt) + r'(?!\d)', ab):
            reds.append(f'B:{lang}:date 摘要未写 {dt}%（表 9 Match rate {_d[0]} 的百分数）')

    return reds, {'kappa': _k, 'icc': _i, 'date': _d,
                  'range': [lo, hi], 'icc_r': icc_r, 'date_pct': dt}


# ─────────────────────────── C 段：EIC-3 具名靶点 ───────────────────────────

OVERCLAIMS = [
    ('LLM-optimized', 'R5-小1 要求删除的「LLM 优化」式措辞'),
    ('built entirely', 'EIC 点名的「完全由 LLM 构建」式措辞'),
]


def part_c() -> list[str]:
    reds: list[str] = []
    for lang in ('en', 'zh'):
        text = read(_p(*LANG_PATH[lang]))
        for pat, desc in OVERCLAIMS:
            n = len(re.findall(re.escape(pat), text, re.I))
            if n:
                reds.append(f'C:{lang}:{pat} 命中 {n} 次（{desc}）')
    return reds


# ─────────────────────────── D 段：摘要 scope 句（中英各自独立）───────────────────────────

SCOPE = {
    'exp1': ('实验一：运行条件不对等的声明', r'unequal conditions', r'运行条件并不对等'),
    'exp2': ('实验二：不作因果机制主张的声明',
             r'not as an isolated causal mechanism', r'而非已分离的因果机制'),
    'exp3': ('实验三：单受训标注者 / 非专家认证的声明',
             r'one trained annotator', r'一名受训标注者'),
    'all': ('总括：边界已在全文显式声明',
            r'boundaries of these results are stated explicitly', r'全文对结果的边界作显式声明'),
}
SCOPE_LANG = {'en': 1, 'zh': 2}


def part_d() -> tuple[list[str], dict]:
    """**中英各自独立判定**——不写成 `n_en or n_zh`。

    第三十四节的教训：首版判决写成或关系时，「英文有、中文无」会静默通过，
    而中英不对称恰恰是镜像改写最常出的错。
    """
    reds: list[str] = []
    hit: dict[str, dict] = {}
    abs_by_lang = {lang: _abstract_of(lang)[0] for lang in ('en', 'zh')}
    for lang in ('en', 'zh'):
        ab = abs_by_lang[lang]
        probe = SCOPE_ITEM[lang]
        hit[lang] = {}
        for key, (desc, *probes) in SCOPE.items():
            n = len(re.findall(probes[SCOPE_LANG[lang] - 1], ab))
            hit[lang][key] = n
            if not n:
                reds.append(f'D:{lang}:{key} 该语言摘要里零命中「{probe[key]}」（{desc}）')
    return reds, hit


SCOPE_ITEM = {lang: {k: v[SCOPE_LANG[lang]] for k, v in SCOPE.items()} for lang in ('en', 'zh')}

# ─────────────────────────── E 段：跨脚本一致性 ───────────────────────────

LEDGER_MUST = 'system-level selection'      # 第三十五节修正后的 EIC-3 探针特征片段
LEDGER_MUST_NOT = 'claim strength'          # 修正前的元讨论词（退回即红）
REC_MUST = ['covered 7', 'absent 5']        # 第三十五节的统计（修正后）


def part_e() -> list[str]:
    reds: list[str] = []
    src = read(_p('CAA_LEDGER', LEDGER))
    if LEDGER_MUST not in src:
        reds.append(f'E1:ledger EIC-3 探针未指向实质回应句（源码里找不到 {LEDGER_MUST!r}）')
    if LEDGER_MUST_NOT in src:
        reds.append(f'E1:ledger EIC-3 探针退回元讨论词 {LEDGER_MUST_NOT!r}'
                    f'（那会让一个已回应的关切被判成零覆盖）')
    rec = read(_p('CAA_REC', REC))
    for s in REC_MUST:
        if s not in rec:
            reds.append(f'E2:rec 第三十五节统计未同步（对账记录里找不到 {s!r}）')
    return reds


# ─────────────────────────── F 段：文档对账 ───────────────────────────

SEC_TITLE_RE = re.compile(r'^### 3[6-9]\.', re.M)

def part_f(marks: dict) -> list[str]:
    """本节实测数字须逐字落在第三十六节——防「改了脚本忘改文档」。

    锚点全部来自**本次运行**算出来的值，不是写死的常数（§2.5.75：写死的
    固定值本身可能就是响应式的）。
    """
    reds: list[str] = []
    rec = read(_p('CAA_REC', REC))
    if not SEC_TITLE_RE.search(rec):
        reds.append('F1:rec 未找到第三十六节标题（### 36.）')
        return reds     # 节都还没有，下面的数字锚点无从谈起
    a = marks.get('a', {})
    b = marks.get('b', {})
    checks = [
        (f"exact {a.get('en', {}).get('exact')} ／ rounded "
         f"{a.get('en', {}).get('rounded')} ／ unregistered 0",
         'A 段 EN 三态统计（DECI 8 个的分布）'),
        (f"{b.get('range', ['', ''])[0]}–{b.get('range', ['', ''])[1]}", 'B 段 κ 两位舍入区间'),
        (str(b.get('icc_r', [''])[0]), 'B 段 ICC 三位舍入后的唯一值'),
        (str(b.get('date_pct', '')), 'B 段日期匹配率百分数'),
    ]
    for s, desc in checks:
        if s not in rec:
            reds.append(f'F2:rec 第三十六节里找不到 {s!r}（{desc}）')
    for v in b.get('kappa', []):
        if v not in rec:
            reds.append(f'F3:rec 第三十六节里找不到表 9 的精确值 {v}')
    return reds


# ─────────────────────────── main ───────────────────────────

BOUNDARIES = """判据边界
--------
① **词形命中证明"这句话在"，不证明"它够不够"**。D 段核的是摘要里有没有那句
   scope 声明，核不到"半句话的 scope 顶不顶得住整段结论"。实质判断在人。
② **A 段的整数不区分"数值"与"标识符内数字"**：提取器是位置无关的正则，
   `M1` / `F1` 里的 `1` 也进 INT 集合，登记表把它标明为标识符内数字。
   这是**如实登记**，不是把 `1` 当成了数量。
③ **舍入登记表（ROUNDED）不是免检通道**：B 段用表 9 的**现读值**复算，
   正文精确值一改而摘要未跟，B 段立刻转红；登记表只回答"这个数出自哪"。
④ **B 段的真值侧只有英文表 9**（英文是主源、中文是镜像），摘要侧两个语言
   各判一次——若中文表 9 与英文不一致，本脚本不负责发现（那是 check_tables_v2
   / check_xref_v2 的射程）。
⑤ **探针在正文主体里零命中即报红**，但"零命中"分两种：真没有，或探针写窄了
   （第三十四节翻过两次车）。故登记表每条都写明它指的是什么，改探针时改的是
   这一处、不是判据结构。
⑥ **C 段的靶点名单是手写的、会过期**：它盯的是 R5/EIC 当时点名的那两条措辞。
   名单增删须在第三十六节留痕，否则它会退化成"永远绿的白名单"（§2.5.67）。
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--marks', action='store_true', help='供 --drift-test 取机器可读出口')
    a = ap.parse_args()
    quiet = a.quiet or a.marks

    ra, sa = part_a()
    rb, sb = part_b()
    rc = part_c()
    rd, sd = part_d()
    re_ = part_e()
    marks_pre = {'a': sa, 'b': sb, 'd': sd}
    rf = part_f(marks_pre)

    reds = ra + rb + rc + rd + re_ + rf

    if not quiet:
        print('摘要 ↔ 正文主体对齐核验')
        print('=' * 62)
        for lang in ('en', 'zh'):
            s = sa[lang]
            print(f"A  {lang.upper()} 摘要 {s['tokens']} 个数字 token："
                  f"DECI {s['deci']}（精确 {s['exact']} / 舍入 {s['rounded']} / "
                  f"未登记 {s['unreg']}）、INT {s['int']}")
        deci_en = sorted({t for t in nums(split_abstract(read(_p(*LANG_PATH['en'])))[0])
                          if is_deci(t)})
        print(f"   摘要 DECI 全表：{' '.join(deci_en)}")
        if sb:
            print(f"B  表 9 现读：κ {sb['kappa']} / ICC {sb['icc']} / Match {sb['date']}")
            print(f"   ⇒ 两位舍入区间 {sb['range'][0]}–{sb['range'][1]}、"
                  f"三位舍入唯一值 {sb['icc_r'][0]}、日期 {sb['date_pct']}%")
        print(f"C  EIC-3 具名靶点：{len(OVERCLAIMS)} 条（须零命中）")
        for lang in ('en', 'zh'):
            h = sd[lang]
            print(f"D  {lang.upper()} 摘要 scope 句："
                  + '  '.join(f'{k}={v}' for k, v in h.items()))
        print('E  跨脚本一致性：EIC-3 探针修正 + 第三十五节统计')
        print()
        if reds:
            print('待处置：')
            for r in reds:
                print(f'  ✗ {r}')
        else:
            print('✅ A/B/C/D/E/F 六段全部通过')
        print()
        print(BOUNDARIES)

    # MARKS 出口与人类可读输出**不共用开关**（§2.5.101：出口被另一个开关
    # 顺手关掉会让子进程 stdout 全空、"注入没做"与"判据没报"分不开）。
    payload = {'reds': reds, 'a': sa, 'b': sb, 'd': sd,
               'n_deci_en': sa['en']['deci'], 'n_int_en': sa['en']['int']}
    print('MARKS ' + json.dumps(payload, ensure_ascii=False))
    return 1 if reds else 0


# ─────────────────────────── --self-test ───────────────────────────

def self_test() -> int:
    fails: list[str] = []
    n = 0

    def ok(cond: bool, label: str) -> None:
        nonlocal n
        n += 1
        if not cond:
            fails.append(label)

    # 1. split_abstract：切出摘要、正文不含摘要、关键词行被剥掉
    t = ('# T\n\n## Abstract\nThis is A. 2,623 records.\n'
         '\n**Keywords**: a; b\n\n---\n\n## 1. Intro\nBody here.\n')
    ab, body = split_abstract(t)
    ok('2,623 records' in ab, 'split_abstract 摘要内容')
    ok('Keywords' not in ab, 'split_abstract 剥关键词行')
    ok('2,623 records' not in body, 'split_abstract 正文不含摘要')
    ok('Body here' in body, 'split_abstract 正文保留')

    # 2. nums：千分位归一、区间拆两个、百分数取数
    ok(nums('2,623 与 16,323') == ['2623', '16323'], 'nums 千分位')
    ok(nums('0.91–0.97') == ['0.91', '0.97'], 'nums 破折号区间')
    ok(nums('99.7%') == ['99.7'], 'nums 百分数')
    ok(nums('n = 289') == ['289'], 'nums 等号')
    ok(nums('F1 = 0.669') == ['1', '0.669'], 'nums 标识符内数字也被抽出（如实）')

    # 3. is_deci
    ok(is_deci('0.91') and not is_deci('2623'), 'is_deci')

    # 4. dec_round：**两种分叉都实测**——这条是 B 段存在的理由之一。
    #    ⚠️ 本组首版断言的是 `round(0.9965*100,1) == 99.6`，实测给 99.7 ⇒ 用例自己错了，
    #    连带查出 dec_round 的 docstring 写了同一条错论断。**判据自己算错 = 报假红 =
    #    让人去改本来正确的正文**，比漏报更糟，故这条自检留着当回归保护。
    ok(round(0.985, 2) == 0.98, '前提：float 路径在 0.985 上确实给 0.98（表示误差）')
    ok(dec_round('0.985', 2) == '0.99', 'dec_round 避开二进制表示误差')
    ok(round(2.5) == 2, '前提：Python round 是银行家舍入（2.5→2）')
    ok(dec_round('2.5', 0) == '3', 'dec_round 是 HALF_UP（不是银行家舍入）')
    #     负向用例：把「旧注释那格不分叉」钉住——将来有人照旧注释改回来，这条立刻响
    ok(round(0.9965 * 100, 1) == 99.7, '反例：0.9965*100 实测不分叉（旧注释是错的）')
    ok(dec_round('0.9136', 2) == '0.91', 'dec_round 两位')
    ok(dec_round('0.9849', 3) == '0.985', 'dec_round 三位')
    ok(dec_round('0.9477', 2) == '0.95', 'dec_round 进位')

    # 5. read_table9：按 metric 列认行、主值取第一个十进制数
    txt = ("| Accident type | Cohen's κ | 0.9136 [0.8747, 0.9466] | 93.4% |\n"
           "| Severity | Cohen's κ | 0.9477 [0.9024, 0.9841] | 97.6% |\n"
           "| Province | Cohen's κ | 0.9746 [0.9563, 0.9927] | 97.6% |\n"
           "| Deaths | ICC(2,1) | 0.9849 [0.9292, 0.9991] | MAE 0.118 |\n"
           "| Injuries | ICC(2,1) | 0.9853 [0.9426, 0.9995] | MAE 0.087 |\n"
           "| Date (year-month) | Match rate | 0.9965 | 99.7% |\n")
    k, i, d = read_table9(txt)
    ok(k == ['0.9136', '0.9477', '0.9746'], 'read_table9 κ 组')
    ok(i == ['0.9849', '0.9853'], 'read_table9 ICC 组')
    ok(d == ['0.9965'], 'read_table9 日期组')
    #     表头行不带小数 ⇒ 被 `if not m: continue` 滤掉（不靠位置）
    ok(read_table9('| Metric | Value | Note |\n' + txt)[0] == k, 'read_table9 表头不产生值')
    #     组判据由 metric 决定：把 ICC 行改成 κ 会换组（证明不是按行号）
    ok(read_table9(txt.replace('ICC(2,1)', "Cohen's κ"))[0] == k + i, 'read_table9 按 metric 认组')

    # 6. _p 与 LANG_PATH 的元组顺序（首版写反、解包时静默把路径当环境变量名用）
    os.environ['CAA_SELFTEST'] = '/tmp/x.md'
    try:
        ok(_p('CAA_SELFTEST', PAPER_EN) == Path('/tmp/x.md'), '_p 有环境变量时读它')
    finally:
        del os.environ['CAA_SELFTEST']
    ok(_p('CAA_ABSENT_XYZ', PAPER_EN) == PAPER_EN, '_p 缺环境变量时回默认')
    ok(set(LANG_PATH) == {'en', 'zh'}, 'LANG_PATH 只有 en/zh')
    ok(all(isinstance(v[0], str) and isinstance(v[1], Path) for v in LANG_PATH.values()),
       'LANG_PATH 元组顺序 == (环境变量名 str, 默认路径 Path)')

    # 7. 元判据：两张登记表自身
    ok(set(ROUNDED) and all(is_deci(t) for t in ROUNDED), 'ROUNDED 全是 DECI')
    ok(set(INT_ORIGIN['en']) == set(INT_ORIGIN['zh']) - {'4'},
       '两语言 INT 登记键集只差 ZH 的 4（EN 写作 four）')
    ok(all(not is_deci(t) for t in INT_ORIGIN['en']), 'INT_ORIGIN 全是整数')
    ok(all(v[1] for v in INT_ORIGIN['en'].values()), 'INT_ORIGIN 每条都配了探针')
    #     元判据：SCOPE 每条两语言都有探针，且 SCOPE_ITEM 取对了下标
    ok(all(len(v) == 3 for v in SCOPE.values()), 'SCOPE 每条含描述 + 两语言探针')
    ok(SCOPE_ITEM['en']['exp1'] == r'unequal conditions'
       and SCOPE_ITEM['zh']['exp1'] == r'运行条件并不对等', 'SCOPE_ITEM 取下标正确')
    ok(SCOPE_LANG == {'en': 1, 'zh': 2}, 'SCOPE_LANG 下标表')
    ok(len(OVERCLAIMS) == 2, 'OVERCLAIMS 条数（增删须同步第三十六节）')
    ok(bool(SEC_TITLE_RE.search('### 36. 摘要对齐')), '节标题正则命中')
    ok(not SEC_TITLE_RE.search('#### 36.1 子节'), '节标题正则不中四级标题')

    # 8. _match_reds：期望集写稳定 id（不抄说明），条数必须相等，右边界只认
    #    空格与全角左括号。这条断言是 --drift-test 的命门——它一旦退化成子集
    #    包含，「注入没做」与「判据没报」就再也分不开（§2.5.89）。
    ok(_match_reds(['A1:en:0.91 摘要的 0.91 既非正文精确值'], ['A1:en:0.91']),
       'match id + 半角空格')
    ok(_match_reds(["F2:rec 第三十六节里找不到 'x'（A 段 EN 三态统计）"], ['F2:rec']),
       'match id + 全角左括号')
    ok(_match_reds(['A1:en:0.91'], ['A1:en:0.91']), 'match 整串相等')
    ok(_match_reds(['B:zh:kappa 表 9 …', 'B:en:kappa 表 9 …'],
                   ['B:en:kappa', 'B:zh:kappa']), 'match 与顺序无关')
    ok(not _match_reds(['A1:en:0.91'], []),
       '不配：实测多出未列出的红（不许退化成子集包含）')
    ok(not _match_reds([], ['A1:en:0.91']), '不配：期望的红没出现')
    ok(not _match_reds(['A1:en:0.985 摘要的 …'], ['A1:en:0.98']),
       '不配：id 右边界不含数字（0.98 不得吃掉 0.985）')
    ok(not _match_reds(['A1:en:0.91x'], ['A1:en:0.91']),
       '不配：id 后紧跟非分隔符字符')
    ok(not _match_reds(['A1:en:0.91 x', 'A2:en:0.91 x'],
                       ['A1:en:0.91', 'A1:en:0.91']),
       '不配：同一条期望不得复用同一条实测红')

    if fails:
        for f in fails:
            print(f'✗ {f}')
        print(f'--self-test {n - len(fails)}/{n}')
        return 1
    print(f'--self-test {n}/{n} 全部通过')
    return 0


# ─────────────────────────── --drift-test ───────────────────────────

def _match_reds(reds: list[str], want: list[str]) -> bool:
    """期望集写的是**稳定 id**（或 id + 关键内容），实测红是「id + 人类可读说明」的整串。

    按 id 一一配对，且**条数必须相等**——「红的恰好是哪几条」这条断言
    （§2.5.68）不许退化成子集包含：期望 2 条而实测 3 条同样是错，
    哪怕那 3 条里确实包含期望的 2 条。

    id 的右边界只认两种：整串到此为止、或后面紧跟一个空格 / 全角左括号
    （说明与括号注的分隔符）。这样期望既可写整串、也可只写到关键内容为止，
    **不必把说明抄进期望**（说明里含现读值，抄进去等于把注入后的连带值写死，
    §2.5.75）。

    放在模块级（而非 drift_test 内部）是为了让 `--self-test` 够得着它——
    §2.5.64：判据自身没有失败路径，"退出码 0"就只是一句自述。
    """
    if len(reds) != len(want):
        return False
    pool = list(reds)
    for w in want:
        hit = next((r for r in pool
                    if r == w or (r.startswith(w) and r[len(w)] in (' ', '（'))), None)
        if hit is None:
            return False
        pool.remove(hit)
    return True


def _reds_of(env: dict[str, str]) -> list[str]:
    """在子环境里跑一次主检，从 MARKS 取红集合。

    走子进程是为了让 `_p()` 读到新的环境变量；顺带验证 MARKS 出口在
    `--marks`（quiet=True）下真的印得出来。

    ⚠ **跑哪个脚本文件必须取自传入的 `env`、不能只走 `_p()`**：`_p()` 读的是
    **本进程的 `os.environ`**，而本函数把覆盖值放在 `e` 里、只交给子进程。
    D1–D4 注入的是论文正文与文档（脚本文件不变）故看不出差别；D5 注入的
    恰恰是**本脚本自己**——`_p()` 没看见 `CAA_SELF`、照旧跑真脚本 ⇒ 真脚本里
    那条登记还在 ⇒ 全绿 ⇒ 红集合为空。而「注入没做」与「判据没报」在输出上
    一模一样（§2.5.89），只有「红的恰好是哪几条」这条断言能把它露出来。
    """
    import subprocess
    e = dict(os.environ)
    e.update(env)
    script = env.get('CAA_SELF') or str(SELF)
    if not Path(script).exists():
        return [f'<CAA_SELF 指向的脚本不存在：{script}>']
    out = subprocess.run([sys.executable, script, '--marks'],
                         capture_output=True, text=True, env=e)
    for line in out.stdout.splitlines():
        if line.startswith('MARKS '):
            return list(json.loads(line[6:]).get('reds', []))
    return ['<MARKS 出口没印出来>']


def drift_test() -> int:
    """注入式自证：三个注入面（论文正文面 / 脚本源码面 / 文档面），
    每例断言红的**恰好**是哪几条。"""
    tmp = Path(tempfile.mkdtemp(prefix='caa_drift_'))
    cases: list[tuple[str, dict[str, str], list[str]]] = []
    n_bad = 0
    try:
        base = _reds_of({})
        if base:
            print(f'✗ 基线不是绿的：{base}')
            n_bad += 1

        def copy_to(name: str, src: Path, pairs: list[tuple[str, str]]) -> tuple[Path, bool]:
            """把 src 复制到 tmp 并做若干处替换；返回 (副本路径, 是否真的改到了)。"""
            dst = tmp / name
            dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
            s = dst.read_text(encoding='utf-8')
            done = True
            for old, new in pairs:
                if old not in s:
                    print(f'✗ 锚点未命中（注入等于没做）：{old[:60]!r}')
                    done = False
                s = s.replace(old, new)
            dst.write_text(s, encoding='utf-8')
            return dst, done

        # ── D1（论文面）：把正文表 9 的 0.9746 改成 0.9646 ⇒ 两语言摘要的区间都不再对
        #     连带写全：B 段对 EN 与 ZH 各判一次，两侧的区间都取自同一张表 9，
        #     ⇒ 两条（§2.5.86：同一事实的多个独立视角，写窄了反而是假红）。
        #     **第三条连带是 F 段**：§36 的区间锚点取自**现读值**（§2.5.75，
        #     写死的固定值本身可能就是响应式的），正文一改、F2 立刻找不到新区间
        #     ⇒ 同一次注入同时命中 B 段与 F 段。新区间**算出来**、不写死。
        t1, ok1 = copy_to('p1.md', PAPER_EN, [('| Province | Cohen\'s κ | 0.9746',
                                               "| Province | Cohen's κ | 0.9646")])
        if ok1:
            _after = ['0.9136', '0.9477', '0.9646']     # 注入后表 9 的三个 κ
            _rng = '–'.join([min(dec_round(v, 2) for v in _after),
                             max(dec_round(v, 2) for v in _after)])
            cases.append(('D1 改正文表 9 的 Province κ（0.9746→0.9646）',
                          {'CAA_EN': str(t1)},
                          ['B:en:kappa', 'B:zh:kappa',
                           f"F2:rec 第三十六节里找不到 '{_rng}'（B 段 κ 两位舍入区间）"]))

        # ── D2（论文面）：往英文摘要插一条被点名的 overclaim ⇒ 恰好 C 段一条
        t2, ok2 = copy_to('p2.md', PAPER_EN,
                          [('This paper presents CAIEMS, an exploratory',
                            'This paper presents CAIEMS, an LLM-optimized exploratory')])
        if ok2:
            cases.append(('D2 往英文摘要插 LLM-optimized', {'CAA_EN': str(t2)},
                          ['C:en:LLM-optimized']))

        # ── D3（论文面）：删掉英文摘要的 scope 句 ⇒ 恰好 D 段 EN 一条
        t3, ok3 = copy_to('p3.md', PAPER_EN, [('the compared models ran under unequal conditions',
                                               'the compared models ran under differing')])
        if ok3:
            cases.append(('D3 删英文摘要的 unequal conditions', {'CAA_EN': str(t3)},
                          ['D:en:exp1']))

        # ── D4（论文面）：删掉**中文**摘要的 scope 句 ⇒ 恰好 D 段 ZH 一条。
        #     这一条专测「中英各自独立判定」：若判据写成 `n_en or n_zh`，D3 绿 D4 也绿。
        t4, ok4 = copy_to('p4.md', PAPER_ZH, [('各模型运行条件并不对等', '各模型运行条件并不相同')])
        if ok4:
            cases.append(('D4 删中文摘要的运行条件并不对等', {'CAA_ZH': str(t4)},
                          ['D:zh:exp1']))

        # ── D5（**本脚本源码面**）：把 ROUNDED 里的 0.985 那条删掉 ⇒ 两语言摘要的
        #     0.985 都失去登记 ⇒ A1 两条。A2 不报（条目已删、无过期可言——
        #     "摘要里有而登记里没有"与"登记里没有而摘要里有"看似同一事实的两面，
        #     实测只有前者成立）。
        #     **第三条连带是 F 段**：F2 的锚点取自 A 段的**现读统计**（§2.5.75），
        #     注入后 EN 的 rounded 由 3 变 2 ⇒ §36 里那句统计不再是现读值 ⇒ F2 报。
        #     与 D1 同型：一次注入同时命中两段，两条都是同一既成事实的独立视角。
        #     ⚠ 注入的是**本脚本自己**（ROUNDED 长在这个文件里，不在 LEDGER 里），
        #     故副本落 tmp、`ROOT = parents[1]` 派生不出仓库根、四个默认路径全错
        #     ⇒ 必须把四个输入一并显式传给子进程（§2.5.95 的反向形态：那节防的是
        #     "判据读真文件"，这里防的是"副本读不到输入"）。
        t5, ok5 = copy_to('self_copy.py', SELF,
                          [("    '0.985': '表 9 两个计数字段 ICC（0.9849/0.9853）三位舍入（两值舍到同一数）',\n", '')])
        if ok5:
            _after_a = ('exact 5 ／ rounded 2 ／ unregistered 0')   # 删一条登记后 EN 的现读统计
            cases.append(('D5 删掉本脚本 ROUNDED 里的 0.985 登记',
                          {'CAA_SELF': str(t5), 'CAA_EN': str(PAPER_EN),
                           'CAA_ZH': str(PAPER_ZH), 'CAA_REC': str(REC),
                           'CAA_LEDGER': str(LEDGER)},
                          ['A1:en:0.985', 'A1:zh:0.985',
                           f'F2:rec 第三十六节里找不到 {_after_a!r}']))

        # ── D6（文档面）：删掉对账记录第三十六节标题 ⇒ F1 一条
        t6 = tmp / 'rec.md'
        s6 = REC.read_text(encoding='utf-8')
        n_sec = len(SEC_TITLE_RE.findall(s6))
        s6 = SEC_TITLE_RE.sub('### 99.', s6)
        t6.write_text(s6, encoding='utf-8')
        if n_sec == 0:
            print('✗ D6 锚点 `### 36.` 在文档里 0 次 ⇒ 注入等于没做（先写第三十六节）')
            n_bad += 1
        else:
            cases.append(('D6 删掉对账记录第三十六节标题', {'CAA_REC': str(t6)}, ['F1:rec 未找到第三十六节标题（### 36.）']))

        # ── D7/D8（源码面）：E1 的两条判据**方向相反**——一条要求探针里**有**实质回应
        #     形态、一条要求探针里**没有**元讨论词。若只写一条，另一条的失败路径就
        #     从没被走过（§2.5.93：两条判据方向相同则归因不唯一，方向相反才互相兜底）。
        #     D7 把探针退回元讨论词（该词是本判据的靶点，故注入串里必须真的写进去）。
        _probe_old = (r"     r'unequal conditions|system-level selection"
                      r"|not as an isolated causal mechanism',")
        t7, ok7 = copy_to('ledger_rev.py', LEDGER,
                          [(_probe_old,
                            r"     r'claim strength|system-level selection"
                            r"|not as an isolated causal mechanism',")])
        if ok7:
            cases.append(('D7 把 ledger 的 EIC-3 探针退回元讨论词',
                          {'CAA_LEDGER': str(t7)},
                          [f'E1:ledger EIC-3 探针退回元讨论词 {LEDGER_MUST_NOT!r}'
                           f'（那会让一个已回应的关切被判成零覆盖）']))

        # D8 抽掉探针里唯一那截具名形态（`system-level selection` 在本文件只此一处，
        # 实测 grep -c == 1）⇒ 必须报「未指向实质回应句」，且**不**报退回（元讨论词仍不在）。
        t8, ok8 = copy_to('ledger_narrow.py', LEDGER,
                          [(_probe_old,
                            r"     r'unequal conditions"
                            r"|not as an isolated causal mechanism',")])
        if ok8:
            cases.append(('D8 抽掉 ledger 探针里的实质回应形态',
                          {'CAA_LEDGER': str(t8)},
                          [f'E1:ledger EIC-3 探针未指向实质回应句（源码里找不到 {LEDGER_MUST!r}）']))

        for label, env, want in cases:
            reds = _reds_of(env)
            if not _match_reds(reds, want):
                print(f'✗ {label}：期望红 {sorted(want)}、实测 {sorted(reds)}')
                n_bad += 1
            else:
                print(f'✓ {label} → {sorted(want)}')

        # 尾检：注入全部在 tmp 副本里，真文件应分毫未动
        if _reds_of({}):
            print('✗ 注入结束后基线不绿（可能有注入落到了真文件）')
            n_bad += 1
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(cases) + 2
    if n_bad:
        print(f'--drift-test {total - n_bad}/{total}')
        return 1
    print(f'--drift-test {total}/{total} 全部通过')
    return 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    if '--drift-test' in sys.argv:
        sys.exit(drift_test())
    sys.exit(main())
