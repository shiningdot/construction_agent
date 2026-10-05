#!/usr/bin/env python3
"""评审意见账本的完整性核验（R1–R5 原件 → 交叉评审矩阵 → 修订路线图 → 01_re_review → V2）

为什么需要这把尺子
------------------
docs/paper/reviews/ 下有五份原件（R1 方法论 / R2 领域 / R3 提示工程 / DA 魔鬼代言人 /
EIC 主编）、一份编辑决策（00_editorial_decision.md，含 17 行交叉评审矩阵与 15 项修订
路线图）、一份复审报告（01_re_review.md，17 项）。它们构成一条**账本链**：

    R 原件条目 → 交叉评审矩阵 → 修订路线图 → 01_re_review 17 项 → V2 正文

第三十四节核的是**链的最后一环**（01_re_review 的 17 项在 V2 里落实了没有）。
本节核**中间环**：意见在**归类**的时候有没有丢细节——一条意见从原件抄进矩阵时
被合并、被改写编号、或干脆没抄进去。

三种漏法（彼此不同、处置也不同）
--------------------------------
1. **完全漏**：矩阵没有、路线图也没有 ⇒ 这条意见在整条链上不存在。
2. **矩阵漏、路线图补**：编号对不上但内容在（如 DA-m3「局限性缺单模型设计」被并进
   路线图 P3-4 的四项里）⇒ 链有断点，追溯只能靠人读。
3. **进了矩阵但编号口径不可追溯**：看着像"引用了"，实际引不到（实测一处：矩阵行 11
   与路线图 P2-5 把「R1 第 4 条 minor（S2 后处理）」写作 `R1-小1`，而 R1 的 minor
   原件无编号、按位置第 1 条是「Table 4 城市精度错误分析表」——三处（矩阵/路线图/
   01_re_review）**自洽**，指的都是同一条意见，**不是抄错**；错的是矩阵按议题命名、
   本脚本按位置命名，两个体系撞上，而矩阵从未声明它的编号规则。风险：读者拿
   `R1-小1` 回原件按位置找，会找到另一条）。

判据怎么保证自己不虚
--------------------
R 原件是自然语言，条目无法自动抽取，登记表只能手抄。**手抄的表必须能被原件反证**：
每条登记项都带一段 `quote`，脚本断言它在**对应的原件里逐字存在**——「我抄错/杜撰了
一条」当场转红。另加元判据：条目总数、每份原件的条目数、矩阵编号的**双向**闭合
（矩阵引用的每个编号我都登记了；我登记为「已进矩阵」的每个编号都在矩阵里）。

D 段（V2 覆盖探针）**不判红**
------------------------------
18 条未进矩阵的意见，逐条用词形探针查 V2 中英是否已实质覆盖。词形探针只能证明
「这个词在不在」、**不能证明该意见被实质回应**，故这一段只分组打印——「这条要不要
补」是作者裁决，不是脚本能判的（与第三十二节同一种分工）。

用法
----
    python3 scripts/check_review_ledger_integrity.py            # 主检
    python3 scripts/check_review_ledger_integrity.py --marks    # 只输出机器可读行
    python3 scripts/check_review_ledger_integrity.py --self-test
    python3 scripts/check_review_ledger_integrity.py --drift-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REV = ROOT / 'docs' / 'paper' / 'reviews'
R1P = REV / 'R1_methodology.md'
R2P = REV / 'R2_domain.md'
R3P = REV / 'R3_prompt_engineering.md'
DAP = REV / 'R4_devils_advocate.md'
EICP = REV / 'R5_eic.md'
DEC = REV / '00_editorial_decision.md'
RRP = REV / '01_re_review.md'
V2 = ROOT / 'docs' / 'paper_v2' / 'eswa_paper_v2.md'
ZH = ROOT / 'docs' / 'paper_v2' / 'eswa_paper_v2_zh.md'
REC = ROOT / 'docs' / 'paper_v2' / '修订对账记录.md'

SRC_FILE = {'R1': R1P, 'R2': R2P, 'R3': R3P, 'DA': DAP, 'EIC': EICP}


def _p(env: str, default: Path) -> Path:
    """允许 --drift-test 用环境变量把判据指向副本（§2.5.95：注入改副本而判据读真文件
    ⇒ 注入等于没做，故判据侧必须能跟着走）。"""
    v = os.environ.get(env)
    return Path(v) if v else default


# ─────────────────────────── 登记表（手抄，须被原件反证） ───────────────────────────
# (来源, 编号, 一句话, 原件逐字片段, 矩阵行号(0 = 未进矩阵), EN 探针, ZH 探针)
#
# 编号口径：有编号的按原件编号（R1-M1 / R2-D1 / R3-P1 / DA-C1 / EIC-1）；
# R1/R2/R3 的 Minor Points 与 DA 的三个小标题段**原件均无编号**，按**位置顺序**
# 编小1/小2/…（这是本脚本唯一自造的编号，为的是能给"编号口径不可追溯"这件事一个可
# 引用的名字；它们**不是原件里的编号**，原件不受影响）。
ENTRIES = [
    # ── R1 方法论（6 major + 4 minor）──
    ('R1', 'M1', 'v1→v2 双重变化混淆（旗舰论断归因不成立）',
     'The v1→v2 comparison conflates two simultaneous changes', 1, '', ''),
    ('R1', 'M2', '方差估计与显著性检验缺失',
     'No variance estimates or significance tests', 12, '', ''),
    ('R1', 'M3', '模型版本不透明（auto tier routing 未记录）',
     'Model version opacity', 7, '', ''),
    ('R1', 'M4', '银标准验证流程未说明（无协议细节、无修正率）',
     'The silver standard validation process is underspecified', 8, '', ''),
    ('R1', 'M5', '跨样本比较（289 vs 100，标注者人数不同）',
     'Cross-sample comparison in the agreement study', 9, '', ''),
    ('R1', 'M6', 'Experiment 1 混淆 tier 与 vendor（22× 成本主张继承）',
     "Experiment 1's model selection confounds tier with vendor", 7, '', ''),
    ('R1', '小1', 'Table 4 城市精度须补一张小错误分析表（县级 vs 地级）',
     'A small error-analysis table (county-level vs prefecture-level matches)', 0,
     r'county[- ]level|prefecture[- ]level|administrative[- ]level',
     r'县级|地级|行政区划级'),
    ('R1', '小2', 'tie-breaking 规则未声明（模型多编码映射到单一真值维度时）',
     'state the tie-breaking rule used when a model outputs multiple codes', 0,
     r'tie[- ]?break|set[- ]level|micro[- ]averag', r'并列|集合级|多对一|一对多'),
    ('R1', '小3', 'temperature 0.0 只给点估计，须写进局限性',
     'The temperature-0.0 design gives point estimates only', 0,
     r'temperature 0\.0|single run|point estimate', r'单次运行|点估计'),
    ('R1', '小4', 'Table 6 的 S2 valid-output 85% 须澄清恢复输出是否计入 F1',
     'clarify whether the recovered outputs are included in S2', 11, '', ''),

    # ── R2 领域（5 major + 3 minor）──
    ('R2', 'D1', 'CAICS 推导方法未描述（19 类怎么产生、谁做、经过什么迭代）',
     'CAICS derivation methodology is not described', 3, '', ''),
    ('R2', 'D2', '缺 24Model / STAMP / Accimap 的定位对话',
     'Missing dialogue with existing construction-specific causation models', 4, '', ''),
    ('R2', 'D3', '12 类事故类型混两个粒度，须说明遵循哪个标准',
     'The 12-category accident type scheme mixes two granularity levels', 13, '', ''),
    ('R2', 'D4', '年代异质性未处理（1978–2026 是不同文本体裁）',
     'Era heterogeneity is unaddressed', 14, '', ''),
    ('R2', 'D5', '境外记录纳入标准未说明',
     'Foreign-location records', 15, '', ''),
    ('R2', '小1', 'F 层「Protection Failure」可提 barrier-based models 的并行',
     'consider noting the parallel to barrier-based models', 0,
     r'barrier|Swiss[- ]cheese', r'屏障|瑞士奶酪'),
    ('R2', '小2', 'C 层与事故过程模型的应急响应阶段概念重叠，须一句定位',
     'conceptually overlaps with emergency-response phases', 0,
     r'emergency[- ]response', r'应急响应'),
    ('R2', '小3', '建议引用一篇中文 HFACS-施工研究（建议类，词形探针不可判）',
     'Consider citing one Chinese-language HFACS-construction study', 0,
     '-', '-'),

    # ── R3 提示工程（5 major + 3 minor）──
    ('R3', 'P1', 'v1→v2 干预混淆，命名效应建立其上',
     'The v1→v2 intervention is confounded', 1, '', ''),
    ('R3', 'P2', '模型依赖论断与任务依赖混淆',
     'The model-dependence claim is confounded with task-dependence', 2, '', ''),
    ('R3', 'P3', '机制论证（推理模式 vs 模式匹配）无行为/表征探针',
     'The manuscript offers an attention-level explanation', 0,
     r'attention[- ]level|mechanis', r'注意力|机制'),
    ('R3', 'P4', 'S2 后处理恢复造成不公平比较（上界估计）',
     "S2's post-processed recovery creates an unfair comparison", 11, '', ''),
    ('R3', 'P5', 'temperature 0.0 单次运行，须给方差估计',
     'Temperature 0.0 throughout, single run each', 12, '', ''),
    ('R3', '小1', '「Lexical Gatekeeping Effect」须一句话界定适用范围',
     'is it specific to safety-domain prompts, Chinese text, or this model?', 0,
     r'scope boundary|does not generali|generalisab|scope of the', r'适用范围|范围界定|不作一般'),
    ('R3', '小2', '编码密度校准（S1 vs GT）值得前台化为次级发现',
     'consider foregrounding it as a secondary finding', 0,
     r'coding density|code density', r'编码密度'),
    ('R3', '小3', '§4.4.2「few-shot 是更可靠默认」从单一开源模型推广过宽',
     'is the more reliable default for the open-model family', 0,
     r'open[- ]model family|single open model|more reliable default', r'开源模型|更可靠'),

    # ── DA 魔鬼代言人（2 CRITICAL + 4 MAJOR + 3 MINOR + 3 替代解释 + 2 利益相关者）──
    ('DA', 'C1', 'v1→v2 混淆（最强表述：命名效应须无混淆操纵）',
     'a named effect requires an unconfounded manipulation', 1, '', ''),
    ('DA', 'C2', 'reversal 混淆模型依赖与任务依赖',
     'conflates model-dependence with task-dependence', 2, '', ''),
    ('DA', 'M1', '「LLM-optimized」未操作化，要么定义要么删',
     'the "LLM-optimized" qualifier is never operationalized', 5, '', ''),
    ('DA', 'M2', '「first database」声明过强，须给精确差异化或删除',
     'The "first" claim needs either a precise differentiator', 6, '', ''),
    ('DA', 'M3', '学生标注者独立性（共享训练 artifacts 会抬高 κ）',
     'The "human" side of the agreement study is one graduate student', 10, '', ''),
    ('DA', 'M4', '实践主张范围须限于基础字段（致因编码无一致验证）',
     "The claim's scope should be restricted to basic fields", 17, '', ''),
    ('DA', 'm1', 'S2 后处理须至少给表注',
     'presented in the same table as unprocessed strategies', 11, '', ''),
    ('DA', 'm2', 'S4 安全成本不对称论证只被断言、未被证据锚定',
     'the safety-cost asymmetry argument', 0,
     r'safety[- ]cost|missing a cause|regulatory', r'监管|漏掉致因|安全成本'),
    ('DA', 'm3', '局限性章缺「策略实验的单模型设计」这一编号限制',
     'omits the single-model design of the strategy experiment', 0,
     r'single[- ]model', r'单模型'),
    ('DA', 'A1', '替代解释①：v1 崩塌可能出自指令保守而非关键字门控',
     'could reflect **instruction-conservatism**', 0,
     r'instruction[- ]conservatism|not to fabricate|suppressed borderline',
     r'指令保守|保守性'),
    ('DA', 'A2', '替代解释②：S1 优势可能反映样例驱动的输出长度校准',
     'example-driven output-length calibration', 0,
     r'output[- ]length|length calibration', r'输出长度'),
    ('DA', 'A3', '替代解释③：κ 可能反映语料同质性（新闻套语）而非独立一致',
     'could reflect **corpus-homogeneity**', 0,
     r'corpus[- ]homogeneity|formulaic', r'同质|套语|程式化'),
    ('DA', 'S1', '缺监管者视角（监管者如何采纳管道、核验流程、更新节奏）',
     'Safety regulators', 0, r'regulator', r'监管者|监管机构'),
    ('DA', 'S2', '缺现场安全管理者视角（19 类 vs 其现有检查表的粒度）',
     'Site-level safety managers', 0, r'site[- ]level|safety manager', r'现场安全管理|安全管理人员'),

    # ── EIC 主编（4 条关切；第 5 条是全篇肯定的观察，不计条目）──
    ('EIC', '1', '旗舰主张建立在混淆比较上（须消融；多模型可作强烈建议）',
     'The flagship claims rest on confounded comparisons', 1, '', ''),
    ('EIC', '2', 'CAICS 推导必须文档化（操作定义不足以承载构念效度）',
     'Taxonomy derivation must be documented', 3, '', ''),
    ('EIC', '3', '主张强度须与证据对齐（摘要领先于正文；须与 §5 的 hedge 一致）',
     'Claims versus evidence discipline', 0,
     r'claim strength|overclaim|hedg', r'主张强度|降调|谨慎表述'),
    ('EIC', '4', '代码可用性声明完全缺失',
     'code availability statement is missing entirely', 16, '', ''),
]

# 每份原件应有的条目数——防「登记表手抄时整段漏掉」（§2.5.74：清单一短，
# 内部对账永远是绿的）。
EXPECT_PER_SRC = {'R1': 10, 'R2': 8, 'R3': 8, 'DA': 14, 'EIC': 4}
N_ENTRIES = sum(EXPECT_PER_SRC.values())      # 44
N_MAJOR = 26                                   # 进了矩阵的条目数（实测：矩阵出现 26 个编号）

# ─────────────────────── 登记：编号口径不可追溯（第三种漏法） ───────────────────────
# 矩阵行 11 与路线图 P2-5 都写 `R1-小1`，而 R1 的 Minor Points **无编号**，按位置第 1
# 条是「Table 4 城市精度错误分析表」；行 11 的议题是「S2 后处理不公平比较」= R1 的
# **第 4 条** minor。⇒ 三处（矩阵/路线图/01_re_review）自洽，指的是同一条意见，**不是
# 抄错**；问题是矩阵按议题命名、本脚本按位置命名，两个编号体系撞上而矩阵未声明规则，
# 于是这个编号从矩阵传递到路线图、再传递到 01_re_review 的 P2-5 出处栏，读者拿着它
# 回原件按位置找会找错一条。
#
# **登记不是豁免**（§2.5.67）：每一条都配可证伪判据——登记“成立”须同时满足 **8 个
# 条件**（见 B5 段），任一不成立即转红提示「该重新裁决」。有人若把编号改对了，这里
# 同样转红（登记过期）。
# 每条 5 元组：bad（矩阵里写的编号）／real（它实际该引的登记编号）／topic（矩阵议题列
# 原文）／occupied（该位置按位置顺序实为哪一条 minor 的片段）／real_quote（real 那条
# minor 在原件里的片段）。后两者互为反例：改对编号后 occupied 仍会在原件里、但矩阵
# 议题列会变，判据因此抓得到。
MISLABEL = [
    # ⚠️ 定性（第三十五节 35.5）：**这不是"抄错"，是编号口径不可追溯**。R1 原件的
    # Minor Points **无编号**；矩阵与路线图把「S2 后处理」写作 `小1`，按位置它实为
    # 第 4 条。三处（矩阵 / 路线图 P2-5 / 01_re_review P2-5）**完全自洽**，指的都是
    # 同一条意见——错的是"矩阵按议题命名、本脚本按位置命名"这两个体系撞上了，而
    # 矩阵从未声明它的编号规则。风险实在：读者拿 `R1-小1` 回原件按位置找，会找到
    # 另一条（Table 4 城市精度）。
    ('R1-小1', 'R1-小4', 'S2 后处理不公平比较',
     'county-level vs prefecture-level matches',
     'valid-output rate'),
]

# 01_re_review 的编号体系在路线图之外重编了两项——不是缺陷，是**必须登记的结构事实**
# （追溯时「P3-5 在路线图里找不到」会让人以为漏了）。
REREVIEW_EXTRA = {
    'P2-7': '「S5 逐层失败解释」，出处栏写「（共识性讨论）」——矩阵与路线图都没有它',
    'P3-5': '「实践主张范围」，从路线图 P3-4 里拆出来单列（路线图把 DA-M4 并进了 P3-4）',
}

# ─────────────────────────── 判据边界（明写） ───────────────────────────
BOUNDARIES = """
判据边界（明写）
  1. 条目登记表是**手抄**的：脚本能反证「我抄错/杜撰」，但**核不到「原件里还有我
     没有抄到的条目」**——护栏是每份原件的条目数常量与人读原件，不是退出码。
  2. 「一条意见」的口径由本脚本定义：编号条目 + 无编号 bullet + DA 的「替代解释」
     「利益相关者」两段（它们是可执行的修改建议）。换个口径（例如不把替代解释算
     意见）数字会变，但**未进矩阵的名单不会变**。
  3. `R1/R2/R3` 的 minor 编号是本脚本按**位置**自造的（原件无编号），只为给「编号
     口径不可追溯」这件事一个可引用的名字——它们**不是原件里的编号**。
  4. D 段的探针是**词形探针**：命中只能说明「这个词在不在」，**不能说明该意见被实质
     回应**；未命中也不必然等于「V2 缺这一条」（可能换了说法）。故 D 段不判红。
  5. **C1 只核「编号在不在矩阵编号集合里」，核不到「它挂在哪一行」**——35.5 的
     二、三（P3-4 漏引两条来源、P2-4 误引 `DA-M3`）**是人读出来的，不是判据抓出来
     的**：`DA-M3` 确实是矩阵里存在的编号，只是挂错了行。要机器化得先有「议题 ↔
     行」的可靠匹配，而议题是自然语言，本阶段不做。
  6. 本脚本核的是**账本链的结构**，核不到「这条意见值不值得采纳」——那是作者裁决。
  7. 矩阵行号是**解析出来的**（不是手抄），行序变化会自动跟随；但**议题列的语义**
     核不到（把两行的议题对调，行号判据不会响）。
"""


# ─────────────────────────── 纯函数 ───────────────────────────

def read(p: Path) -> str:
    return p.read_text(encoding='utf-8')


def parse_matrix(text: str) -> list[tuple[str, list[str]]]:
    """从 00_editorial_decision.md 的「1. 交叉评审矩阵」解析出 [(议题, [编号…]), …]。

    编号规范化为 `R1-M1` / `R2-D1` / `R3-P1` / `DA-C1` / `EIC-1`；
    `—` 与空单元跳过；单元格里的 `**` 与逗号分隔的多个编号都处理。
    """
    rows: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith('|') or s.startswith('|--') or s.startswith('|-'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        # 矩阵是 7 列：议题 + R1 + R2 + R3 + DA + EIC + 共识度
        if len(cells) != 7:
            continue
        topic = cells[0].strip()
        if topic.startswith('议题') or '共识度' in topic:
            continue
        ids: list[str] = []
        for col, pref in zip(cells[1:6], ('R1', 'R2', 'R3', 'DA', 'EIC')):
            for tok in col.split(','):
                tok = tok.strip().strip('*').strip()
                if tok in ('—', '-', ''):
                    continue
                ids.append(f'{pref}-{tok}')
        rows.append((topic, ids))
    return rows


def matrix_ids(rows: list[tuple[str, list[str]]]) -> list[str]:
    out: list[str] = []
    for _, ids in rows:
        for i in ids:
            if i not in out:
                out.append(i)
    return out


def quote_ok(src: str, quote: str, text: str) -> bool:
    """登记项的原件片段须在该来源的原件里逐字存在。"""
    return quote in text


def probe(text: str, pat: str) -> int:
    # `''` 与 `'-'` 都表示「未配探针/不可用词形探针判定」——`'-'` 是显式登记
    # （A3 要求未进矩阵的条目必须显式表态，不能让 D 段一栏永远空着）。
    if not pat or pat == '-':
        return -1
    return len(re.findall(pat, text, re.I))


def coverage_state(n_en: int, n_zh: int) -> str:
    """三态：covered / partial / absent / manual（未配探针）。"""
    if n_en < 0 and n_zh < 0:
        return 'manual'
    if n_en > 0 and n_zh > 0:
        return 'covered'
    if n_en > 0 or n_zh > 0:
        return 'partial'
    return 'absent'


# ─────────────────────────── A 段：登记表 ↔ 原件 ───────────────────────────

def part_a() -> list[str]:
    reds: list[str] = []
    texts = {k: read(_p(f'CRL_{k}', v)) for k, v in SRC_FILE.items()}
    txt_v2 = read(_p('CRL_V2', V2))
    txt_zh = read(_p('CRL_ZH', ZH))

    # A0 元判据：条目总数与分来源条目数（防整段漏抄）
    n_by_src: dict[str, int] = {}
    for e in ENTRIES:
        n_by_src[e[0]] = n_by_src.get(e[0], 0) + 1
    if len(ENTRIES) != N_ENTRIES:
        reds.append(f'A0 条目总数 {len(ENTRIES)} ≠ 常量 {N_ENTRIES}')
    for k, want in EXPECT_PER_SRC.items():
        got = n_by_src.get(k, 0)
        if got != want:
            reds.append(f'A0 {k} 登记条目数 {got} ≠ 应有 {want}')

    # A1 每条的 quote 须在对应原件逐字命中
    for src, num, desc, quote, _row, _en, _zh in ENTRIES:
        if not quote_ok(src, quote, texts[src]):
            reds.append(f'A1 {src}-{num} 的原件片段未逐字命中：{quote[:48]}')

    # A2 编号在同一来源内唯一（防「两条共用一个名字」）
    seen: set[tuple[str, str]] = set()
    for src, num, *_ in ENTRIES:
        if (src, num) in seen:
            reds.append(f'A2 {src}-{num} 编号重复')
        seen.add((src, num))

    # A3 未进矩阵的条目须配 EN/ZH 探针（否则 D 段一栏永远空着，等于没核）
    for src, num, _d, _q, row, en, zh in ENTRIES:
        if row == 0 and not en and not zh:
            reds.append(f'A3 {src}-{num} 未进矩阵却未配探针')

    return reds


# ─────────────────────────── B 段：矩阵 ↔ 登记表 ───────────────────────────

def part_b() -> list[str]:
    reds: list[str] = []
    rows = parse_matrix(read(_p('CRL_DEC', DEC)))
    mids = matrix_ids(rows)

    # B0 元判据：矩阵须解析出 17 行
    if len(rows) != 17:
        reds.append(f'B0 矩阵解析出 {len(rows)} 行 ≠ 17')

    # B1 方向一：矩阵引用的每个编号，登记表里都要有（防矩阵引用我不认识的）
    reg_ids = {f'{s}-{n}' for s, n, *_ in ENTRIES}
    for i in mids:
        if i not in reg_ids:
            reds.append(f'B1 矩阵引用了登记表里没有的编号：{i}')

    # B2 方向二：登记为「已进矩阵」的编号集合 == 矩阵里出现的编号集合（双向闭合）
    #
    # 唯一豁免是**编号口径不一致**（矩阵把按位置第 4 条 minor 记成 `小1`）：它在闭合里天生
    # 两头都不匹配，豁免由 MISLABEL 提供、并由 B5 逐条证伪——三个条件任一失效 B5 就
    # 转红（§2.5.67：登记不是豁免）。豁免只吃掉 bad_id 出现在 extra、real_id 出现在
    # missing 这两种形态，**其余任何差集照报**。
    alias_matrix = {m[0] for m in MISLABEL}    # 矩阵里写成这样的（口径不同的编号）
    alias_entry = {m[1] for m in MISLABEL}     # 它实际指到的登记编号
    mine = {f'{s}-{n}' for s, n, _d, _q, row, _e, _z in ENTRIES if row != 0}
    extra = sorted(set(mids) - mine - alias_matrix)
    missing = sorted(mine - set(mids) - alias_entry)
    for i in extra:
        reds.append(f'B2 矩阵里有、我登记为未进：{i}（编号口径不一致，或我抄错）')
    for i in missing:
        reds.append(f'B2 我登记为已进、矩阵里没有：{i}')

    # B3 闭式：已进 + 未进 == 总条目数（当场断言）
    n_in = sum(1 for e in ENTRIES if e[4] != 0)
    n_out = sum(1 for e in ENTRIES if e[4] == 0)
    if n_in + n_out != len(ENTRIES):
        reds.append(f'B3 闭式失败：{n_in} + {n_out} ≠ {len(ENTRIES)}')

    # B4 登记为已进的条目，其来源列在矩阵该行须真出现该来源的编号——
    #    进一步：该编号须真在那一行（行号对不对，不只"在不在矩阵里"）
    #    唯一豁免同 B2：口径不一致时该行写的是另一个体系的编号，但**它必须真在这一行**
    #    （不含糊：写别的编号照样报红，豁免由 B5 证伪）。
    alias_map = {m[1]: m[0] for m in MISLABEL}
    for src, num, _d, _q, row, _e, _z in ENTRIES:
        if row == 0:
            continue
        if row > len(rows):
            reds.append(f'B4 {src}-{num} 登记行号 {row} 超出矩阵 {len(rows)} 行')
            continue
        ids_here = rows[row - 1][1]
        if f'{src}-{num}' in ids_here:
            continue
        if alias_map.get(f'{src}-{num}') in ids_here:
            continue
        reds.append(f'B4 {src}-{num} 不在矩阵第 {row} 行：{rows[row - 1][0]}')

    # B5 编号口径登记：全部条件都成立才算是"仍成立"，任一不成立即转红提示「该重新裁决」
    #    （§2.5.67：白名单只写理由不验证＝永久豁免）。六条：矩阵仍写 bad / 矩阵仍无 real /
    #    real 须登记为已进 / 01_re_review 仍写 bad / R1 原件仍无编号且两条 minor 仍在 /
    #    矩阵议题列仍是那一句。**最后一条是本判据的失败路径**——有人把编号改对后，
    #    real 会进 mids、bad 会离开 mids，两条同时转红。
    dec_txt, rr_txt = read(_p('CRL_DEC', DEC)), read(_p('CRL_RR', RRP))
    r1_txt = read(_p('CRL_R1', R1P))
    for bad_id, real_id, topic, occupied, real_quote in MISLABEL:
        if bad_id not in mids:
            reds.append(f'B5 登记「矩阵把 {real_id} 写成 {bad_id}（口径不同）」但矩阵里已无 {bad_id} ⇒ 登记过期')
        if real_id in mids:
            reds.append(f'B5 矩阵里已出现 {real_id} ⇒ 编号口径已改对，豁免应撤销（登记过期）')
        if real_id not in mine:
            reds.append(f'B5 登记 {bad_id} 实指 {real_id}，但 {real_id} 未登记为「已进矩阵」⇒ 登记自相矛盾')
        if bad_id not in rr_txt:
            reds.append(f'B5 登记「{bad_id} 编号口径不可追溯」但 01_re_review 里已无该编号 ⇒ 登记过期')
        # R1 的 Minor Points 须仍无编号（否则"按位置编号"这个前提没了）
        if re.search(r'^\s*-\s*\*\*小\d', r1_txt, re.M):
            reds.append('B5 R1 的 Minor Points 已改成带编号 ⇒ 登记前提失效，须重新裁决')
        if occupied not in r1_txt:
            reds.append(f'B5 R1 原件按位置第 1 条 minor 已不是「{occupied}」⇒ 登记前提失效')
        if real_quote not in r1_txt:
            reds.append(f'B5 R1 原件里找不到 {real_id} 那条 minor 的片段「{real_quote}」⇒ 登记前提失效')
        if topic not in dec_txt:
            reds.append(f'B5 矩阵里已找不到议题「{topic}」⇒ 登记前提失效')

    # B6 01_re_review 的编号体系在路线图之外重编了两项——须双向核
    for pid, why in REREVIEW_EXTRA.items():
        if pid not in rr_txt:
            reds.append(f'B6 登记 {pid}（{why[:20]}…）但 01_re_review 里已无 ⇒ 登记过期')
        if pid in dec_txt:
            reds.append(f'B6 {pid} 已出现在 00_editorial_decision.md ⇒ 不再"路线图之外"，登记过期')

    return reds


# ─────────────────────────── C 段：路线图 ↔ 矩阵 / 01_re_review ───────────────────────────

def parse_roadmap(text: str) -> list[tuple[str, str]]:
    r"""从 00_editorial_decision.md 的「3. 修订路线图」解析出 [(P*, 出处原文), …]。

    路线图是四个小表，行形如 `| P0-1 | 问题 | 方案 | 出处 |`。出处一栏是最后一段。
    """
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith('|') or s.startswith('|--') or s.startswith('|-'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if len(cells) != 4:
            continue
        pid = cells[0].strip().strip('*')
        if not re.fullmatch(r'P\d-\d', pid):
            continue
        out.append((pid, cells[3]))
    return out


def part_c() -> list[str]:
    reds: list[str] = []
    dec_txt, rr_txt = read(_p('CRL_DEC', DEC)), read(_p('CRL_RR', RRP))
    rows = parse_matrix(dec_txt)
    mids = set(matrix_ids(rows))
    road = parse_roadmap(dec_txt)

    # C0 元判据：路线图须解析出 15 项
    if len(road) != 15:
        reds.append(f'C0 路线图解析出 {len(road)} 项 ≠ 15')

    # C1 路线图每一项的出处编号，须都在矩阵出现过的编号集合里
    for pid, srcs in road:
        toks = [t.strip().strip('*') for t in srcs.split(',')]
        for t in toks:
            if not t or '共识' in t:
                continue
            if t not in mids:
                reds.append(f'C1 路线图 {pid} 的出处 {t} 不在矩阵编号集合里')

    # C2 路线图 ↔ 01_re_review：后者 17 项，前 15 项编号须一一对应
    rr_ids = re.findall(r'^\|\s*(P\d-\d)\s*\|', rr_txt, re.M)
    if len(rr_ids) != 17:
        reds.append(f'C2 01_re_review 解析出 {len(rr_ids)} 项 ≠ 17')
    road_ids = [p for p, _ in road]
    only_rr = [i for i in rr_ids if i not in road_ids]
    only_road = [i for i in road_ids if i not in rr_ids]
    if sorted(only_rr) != sorted(REREVIEW_EXTRA.keys()):
        reds.append(f'C2 01_re_review 独有编号 {sorted(only_rr)} ≠ 登记 {sorted(REREVIEW_EXTRA)}')
    if only_road:
        reds.append(f'C2 路线图有、01_re_review 没有：{sorted(only_road)}')

    # C3 把 R-1（GT 文件丢失）这条过期欠条本身钉住——第三十四节已登记它已过期，
    #    这里只核「01_re_review 里那句仍在」（它若被删，第三十四节的 C 段会转红；
    #    本节加一层：确认本节读的是同一份文件）。
    if 'R-1' not in rr_txt:
        reds.append('C3 01_re_review 里已找不到残余问题 R-1')

    return reds


# ─────────────────────────── D 段：未进矩阵项 ↔ V2 覆盖（不判红） ───────────────────────────

def part_d() -> tuple[list[tuple], dict[str, list[str]]]:
    txt_v2, txt_zh = read(_p('CRL_V2', V2)), read(_p('CRL_ZH', ZH))
    detail: list[tuple] = []
    groups: dict[str, list[str]] = {'covered': [], 'partial': [], 'absent': [], 'manual': []}
    for src, num, desc, _q, row, en, zh in ENTRIES:
        if row != 0:
            continue
        n_en, n_zh = probe(txt_v2, en), probe(txt_zh, zh)
        st = coverage_state(n_en, n_zh)
        key = f'{src}-{num}'
        groups[st].append(key)
        detail.append((key, desc, st, n_en, n_zh))
    return detail, groups


# ─────────────────────────── E 段：文档对账 ───────────────────────────

def part_e() -> list[str]:
    reds: list[str] = []
    rec = read(_p('CRL_REC', REC))
    if '### 35.' not in rec:
        reds.append('E1 对账记录里找不到第三十五节（`### 35.`）')
    return reds


# ─────────────────────────── 主检 ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--marks', action='store_true', help='只输出机器可读行')
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--drift-test', action='store_true')
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.drift_test:
        return drift_test()

    quiet = args.marks
    reds = part_a() + part_b() + part_c() + part_e()
    detail, groups = part_d()

    if not quiet:
        rows = parse_matrix(read(_p('CRL_DEC', DEC)))
        mids = matrix_ids(rows)
        n_in = sum(1 for e in ENTRIES if e[4] != 0)
        print('评审意见账本的完整性核验')
        print('=' * 62)
        print(f'A 登记表 {len(ENTRIES)} 条'
              f'（R1 {EXPECT_PER_SRC["R1"]} / R2 {EXPECT_PER_SRC["R2"]} / '
              f'R3 {EXPECT_PER_SRC["R3"]} / DA {EXPECT_PER_SRC["DA"]} / '
              f'EIC {EXPECT_PER_SRC["EIC"]}），原件片段逐字命中已核')
        print(f'B 矩阵 {len(rows)} 行、出现 {len(mids)} 个编号；'
              f'登记为已进 {n_in} 条、未进 {len(ENTRIES) - n_in} 条')
        road = parse_roadmap(read(_p('CRL_DEC', DEC)))
        print(f'C 路线图 {len(road)} 项、01_re_review 17 项（其中 '
              f'{len(REREVIEW_EXTRA)} 项为路线图之外的重编编号）')
        print(f'D 未进矩阵的 {len(detail)} 条在 V2 里的词形覆盖（不判红）：'
              f'covered {len(groups["covered"])} / partial {len(groups["partial"])} / '
              f'absent {len(groups["absent"])} / manual {len(groups["manual"])}')
        print()
        print('未进矩阵的条目（按来源）')
        print('-' * 62)
        for src in ('R1', 'R2', 'R3', 'DA', 'EIC'):
            items = [(k, d, st, a, b) for k, d, st, a, b in detail if k.startswith(src + '-')]
            if not items:
                continue
            print(f'  {src}：{len(items)} 条')
            for k, d, st, a, b in items:
                mark = {'covered': '✅', 'partial': '◐', 'absent': '✗', 'manual': '—'}[st]
                print(f'    {mark} {k}  EN {a:>2} / ZH {b:>2}  {d[:44]}')
        print()
        if reds:
            print('待处置：')
            for r in reds:
                print(f'  ✗ {r}')
        else:
            print('✅ A/B/C/E 四段全部通过')
        print()
        print(BOUNDARIES)

    payload = {
        'reds': reds,
        'n_entries': len(ENTRIES),
        'n_major': N_MAJOR,
        'n_unfiled': len(detail),
        'groups': {k: len(v) for k, v in groups.items()},
    }
    # MARKS 出口与人类可读输出**不共用开关**：--marks 时 quiet=True 也照印
    # （§2.5.101 的教训：出口被另一个开关顺手关掉 = 子进程 stdout 全空）。
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

    # 1. parse_matrix：正常两行
    m = ('| 议题 | R1 | R2 | R3 | DA | EIC | 共识度 |\n'
         '|------|:---:|:---:|:---:|:---:|:---:|:---:|\n'
         '| 甲 | M1 | — | P1 | **C1** | 1 | **4/5** |\n'
         '| 乙 | — | D1 | — | — | 2 | 2/5 |\n')
    rows = parse_matrix(m)
    ok(len(rows) == 2, 'parse_matrix 行数')
    ok(rows[0][1] == ['R1-M1', 'R3-P1', 'DA-C1', 'EIC-1'], 'parse_matrix 行 1 编号')
    ok(rows[1][1] == ['R2-D1', 'EIC-2'], 'parse_matrix 行 2 编号')

    # 2. parse_matrix：`—` 与省略号不产生编号
    ok(parse_matrix('| 甲 | — | — | — | — | — | 0 |\n')[0][1] == [], 'parse_matrix 全破折号')

    # 3. parse_matrix：多编号单元格
    ok(parse_matrix('| 甲 | M3,M6 | — | — | — | — | 1/5 |\n')[0][1] == ['R1-M3', 'R1-M6'],
       'parse_matrix 逗号分隔')

    # 4. parse_matrix：表头行不被当成数据
    ok('共识度' not in str(parse_matrix(m)[0][0]), 'parse_matrix 排除表头')

    # 5. matrix_ids 去重且保序
    ok(matrix_ids([('a', ['R1-M1', 'R2-D1']), ('b', ['R1-M1'])]) == ['R1-M1', 'R2-D1'],
       'matrix_ids 去重保序')

    # 6. quote_ok 正反
    ok(quote_ok('R1', 'abc', 'xx abc yy'), 'quote_ok 命中')
    ok(not quote_ok('R1', 'abc', 'xx ab yy'), 'quote_ok 未命中')

    # 7. probe：空探针返回 -1（未配），非空返回计数
    ok(probe('aXaX', 'X') == 2, 'probe 计数')
    ok(probe('aXaX', '') == -1, 'probe 空探针')

    # 8. coverage_state 四态
    ok(coverage_state(-1, -1) == 'manual', 'state manual')
    ok(coverage_state(1, 1) == 'covered', 'state covered')
    ok(coverage_state(1, 0) == 'partial', 'state partial(EN)')
    ok(coverage_state(0, 3) == 'partial', 'state partial(ZH)')
    ok(coverage_state(0, 0) == 'absent', 'state absent')
    ok(coverage_state(-1, 2) == 'partial', 'state 半配探针')

    # 9. parse_roadmap：正常行 + 非行被滤
    rm = ('| # | 问题 | 修订方案 | 出处 |\n'
          '|---|------|----------|------|\n'
          '| P0-1 | 混 | 消融 | R1-M1, R3-P1, DA-C1 |\n'
          '| P1-1 | 推 | 小节 | R2-D1, EIC-2 |\n')
    r = parse_roadmap(rm)
    ok(len(r) == 2, 'parse_roadmap 行数')
    ok(r[0] == ('P0-1', 'R1-M1, R3-P1, DA-C1'), 'parse_roadmap 内容')

    # 10. 元判据：登记表自身
    ok(len(ENTRIES) == N_ENTRIES, 'ENTRIES 条数 == 常量')
    got: dict[str, int] = {}
    for e in ENTRIES:
        got[e[0]] = got.get(e[0], 0) + 1
    ok(got == EXPECT_PER_SRC, 'ENTRIES 分来源条数 == EXPECT_PER_SRC')
    ok(sum(1 for e in ENTRIES if e[4] != 0) == N_MAJOR, 'ENTRIES 已进矩阵条数 == N_MAJOR')

    # 11. 元判据：未进矩阵者须配探针
    ok(all(e[5] or e[6] for e in ENTRIES if e[4] == 0), '未进矩阵者均有探针')

    # 12. 元判据：编号口径登记表自身（bad/real 两端都须是登记表里的编号；real 须为已进）
    #     这条不能省——B2/B4 的豁免全靠它，登记表写错会让豁免变成"凭空少报两条"。
    by_id = {f'{s}-{n}': row for s, n, _d, _q, row, _e, _z in ENTRIES}
    ok(all(m[0] in by_id and m[1] in by_id for m in MISLABEL), 'MISLABEL 两端均在登记表里')
    ok(all(by_id.get(m[1], 0) != 0 for m in MISLABEL), 'MISLABEL 的 real 端登记为已进矩阵')
    ok(all(m[0] != m[1] for m in MISLABEL), 'MISLABEL 两端不相等（相等即无口径差异可言）')

    if fails:
        for f in fails:
            print(f'✗ {f}')
        print(f'--self-test {n - len(fails)}/{n}')
        return 1
    print(f'--self-test {n}/{n} 全部通过')
    return 0


# ─────────────────────────── --drift-test ───────────────────────────

def _reds_of(env: dict[str, str]) -> tuple[list[str], dict, dict]:
    """在子环境里跑一次主检的 A/B/C 段，返回 (reds, marks, groups)。

    走子进程是为了让 `_p()` 读到新的环境变量（同进程里 os.environ 改了也能读，
    但 check_formats 那次的教训是：**同进程改文件对已加载模块无效**；这里文件是
    每次 read() 重读的，故本可以直接调——仍走子进程是为了顺带验证 MARKS 出口
    在 --marks 下真的印得出来，即「出口没被另一个开关关掉」）。
    """
    import subprocess
    e = dict(os.environ)
    e.update(env)
    out = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--marks'],
                         capture_output=True, text=True, env=e)
    marks = {}
    for line in out.stdout.splitlines():
        if line.startswith('MARKS '):
            marks = json.loads(line[6:])
    reds = list(marks.get('reds', []))
    groups = marks.get('groups', {})
    return reds, marks, groups


def _tags(reds: list[str]) -> list[str]:
    """把红条目按前缀标签归类：'A1 ...' → 'A1'；'B5 ...' → 'B5'。"""
    out = []
    for r in reds:
        m = re.match(r'^([A-E]\d)\b', r)
        out.append(m.group(1) if m else r[:2])
    return out


def drift_test() -> int:
    """注入式自证：三个注入面（R 原件面 / 矩阵面 / 文档面）各改副本，断言红的**恰好**是哪几条。"""
    tmp = Path(tempfile.mkdtemp(prefix='crl_drift_'))
    cases: list[tuple[str, dict[str, str], list[str]]] = []
    try:
        # 基线：不注入，须 rc=0、reds 空
        base_reds, base_marks, base_groups = _reds_of({})
        n_bad = 0
        if base_reds:
            print(f'✗ 基线不是绿的：{base_reds}')
            n_bad += 1
        if base_marks.get('n_entries') != N_ENTRIES:
            print(f'✗ 基线的出口不对：{base_marks}')
            n_bad += 1

        # ── 用例 1（原件面）：把 R1 原件副本里那条 quote 改掉 ⇒ A1 恰好报 R1-M1
        t = tmp / 'R1_methodology.md'
        shutil.copy2(R1P, t)
        s = t.read_text(encoding='utf-8')
        t.write_text(s.replace('The v1→v2 comparison conflates two simultaneous changes',
                               'The v1→v2 comparison mixes two simultaneous changes'),
                     encoding='utf-8')
        cases.append(('D1 改 R1 原件里的 M1 片段', {'CRL_R1': str(t)}, ['A1']))

        # ── 用例 2（原件面）：删掉 R1 原件副本里 Table 4 那条 bullet 的 key phrase ⇒ A1 报 R1-小1
        t2 = tmp / 'R1b.md'
        shutil.copy2(R1P, t2)
        s2 = t2.read_text(encoding='utf-8')
        t2.write_text(s2.replace('county-level vs prefecture-level matches', 'county vs prefecture'),
                      encoding='utf-8')
        #     连带 B5：B5 的 occupied 判据盯的是同一段 `county-level vs prefecture-level
        #     matches`（它是"按位置第 1 条 minor 仍是那句话"的前提），删掉后 A1 与 B5
        #     同时响。**这是正确的连带**（同一既成事实的两个独立视角，§2.5.86），
        #     期望集必须写全——写窄了会让这条用例变成假红（§2.5.71）。
        cases.append(('D2 删 R1 原件里 Table 4 条目的片段', {'CRL_R1': str(t2)}, ['A1', 'B5']))

        # ── 用例 3（矩阵面）：把矩阵副本里第 16 行的 EIC 列改成别的东西 ⇒ B2 双报 + C1
        t3 = tmp / 'dec.md'
        shutil.copy2(DEC, t3)
        s3 = t3.read_text(encoding='utf-8')
        t3.write_text(s3.replace('| 代码可用性声明缺失 | — | — | — | — | 4 | 1/5 |',
                                 '| 代码可用性声明缺失 | — | — | — | — | — | 1/5 |'),
                      encoding='utf-8')
        #     连带 B4：EIC-4 登记在矩阵第 16 行，抹掉后该行不再有 EIC-4 ⇒ B4（登记行号
        #     对不对）跟着响。三条判据各管一段（B2 在不在矩阵里／B4 在不在登记的那一行／
        #     C1 路线图出处找不找得到它），都对、都要写进期望（§2.5.86）。
        cases.append(('D3 抹掉矩阵里 EIC-4', {'CRL_DEC': str(t3)}, ['B2', 'B4', 'C1']))

        # ── 用例 4（矩阵面）：把矩阵副本里 R1 的 M3 行删掉 ⇒ B2 报 R1-M3 缺失 + C1
        t4 = tmp / 'dec2.md'
        shutil.copy2(DEC, t4)
        s4 = t4.read_text(encoding='utf-8')
        t4.write_text(s4.replace('| 模型版本/auto-routing 不可复现 | M3,M6 | — | — | — | — | 1/5 |\n', ''),
                      encoding='utf-8')
        # 矩阵少一行 ⇒ B0 也会红（行数 16 ≠ 17），这不是误导：行被删了本就该说。
        # 连带的 B4 一大片也不是噪音——B4 核的是「我登记的**行号**在那一位上有没有该
        # 编号」，删掉一整行会让它**之后的每一行行号整体漂移**，那正是"登记表该更新了"
        # 的信号（§2.5.74：过期登记同样报红）。四类判据各管一段，期望集写全。
        cases.append(('D4 删掉矩阵里的 M3/M6 行', {'CRL_DEC': str(t4)},
                      ['B0', 'B2', 'B4', 'C1']))

        # ── 用例 5（文档面）：第三十五节标题从对账记录副本里删掉 ⇒ E1 恰好一条
        t5 = tmp / 'rec.md'
        shutil.copy2(REC, t5)
        s5 = t5.read_text(encoding='utf-8')
        n_sub = s5.count('### 35.')
        t5.write_text(s5.replace('### 35.', '### 99.'), encoding='utf-8')
        if n_sub == 0:
            print('✗ 用例 5 的锚点 `### 35.` 在文档里 0 次 ⇒ 注入等于没做')
            n_bad += 1
        else:
            cases.append(('D5 删掉对账记录的第三十五节标题', {'CRL_REC': str(t5)}, ['E1']))

        for label, env, want in cases:
            reds, _m, _g = _reds_of(env)
            tags = sorted(set(_tags(reds)))
            if tags != sorted(want):
                print(f'✗ {label}：期望红 {sorted(want)}、实测 {tags}')
                print(f'   明细：{reds}')
                n_bad += 1
            else:
                print(f'✓ {label} → {tags}')

        # 恢复后复跑（确认注入没有残留到真实文件——本节所有注入都在 tmp 里）
        reds2, _m2, _g2 = _reds_of({})
        if reds2:
            print(f'✗ 注入结束后基线不绿：{reds2}')
            n_bad += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(cases) + 2      # 基线 + 尾检
    if n_bad:
        print(f'--drift-test {total - n_bad}/{total}')
        return 1
    print(f'--drift-test {total}/{total} 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
