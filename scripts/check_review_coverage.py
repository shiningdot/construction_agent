#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟评审 17 项（docs/paper/reviews/01_re_review.md）→ V2 落实覆盖核验。

姊妹脚本：`check_review_compliance.py` 核「导师 14 项」（对账记录第三十二节）；
本脚本核「模拟评审 R1–R5 的 17 项」（对账记录第三十四节）。两者对象不同、不互相替代。

## 为什么需要它

`01_re_review.md`（2026-08-21，Stage 3' 复审）里那张「R&R 追溯矩阵」把 17 项意见
逐条判为 ✅（16 项）/ ⚠️（1 项），**那张表是自然语言写的、从未被机器核过**。
更要紧的是：它的「核验结果」栏（如「4.4.3 新增 Controlled ablation 段 + Table 7」、
「3.2 新增推导过程」）**描述的是 V1 那一稿的位置与措辞**，而 V2 是**另一稿**
（又经导师 14 项意见与多轮改写，章节顺延、表号顺延、措辞降调）。
⇒ **用旧判据核新稿会报一片假红**；真正要保证的是**内容在不在**。

## 判据设计：V1 当自证面

§2.5.64「退出码 0 只有在脚本有能力报 1 时才可信」。本脚本的自证方式：
**同一探针先在 V1 存档上跑一遍**——V1 是 08-21 那轮修订的落点，凡是那轮落实的内容
V1 都该有。若某项在 V1 上也命中不了，说明**探针写错了**，不是 V2 丢了。

五态（V1 命中 × V2 中英命中）：

| V1 | V2 EN/ZH | 判决 | 说明 |
|---|---|---|---|
| ✓ | 两侧都 ✓ | `kept` / `strengthened` / `changed` ✅ | 内容在（措辞/位置可能已变，见 note） |
| ✓ | **仅一侧** ✓ | **`asym`** 🔴 | **中英不对称**：另一侧 0 命中 |
| ✓ | 都 ✗ | **`regressed`** 🔴 | **V1 有、V2 无 ⇒ 真回退候选** |
| ✗ | 任一侧 ✓ | `probe-miss` ⚠️ | V1 面失效：探针在 V1 时代的形态不同（观察项，不判红） |
| ✗ | 都 ✗ | **`probe-dead`** 🔴 | **判据自身缺陷：两个面都核不到** |

`asym` / `regressed` / `probe-dead` 判红；`probe-miss` 只观察（它是 V2 增强的证据，不是缺陷）。

**`asym` 这一档是首版没有的**：首版判决写成 `n1 and (n2 or zh_ok)`——**OR**，
于是「英文有、中文无」永远静默通过。而第三十二节 A 段同时承担「中英论断句对称」的核验，
⇒ 必须把 OR 拆成 AND。首跑实测就撞上两格 `EN=0`（P0-2）与 `ZH=0`（P2-3），
**两格经查都是探针写窄了、不是漏译**（见 ITEMS 表内就地注释）——
这条记录本身就是「判据自身也要自证」的又一实例。

## 边界（明写）

- A 段核的是「特征串出现过」、**核不到「这句话是否真的回应了那条意见」**（措辞到位而语义偏离照样绿）。
- **两项之间不判「V2 更强还是更弱」的强弱分**——`strengthened` 是靠人工标注的 `expect` 字段给出，
  不是脚本算出来的；脚本只负责「在不在」与「有没有回退」。
- **`asym` 判的是「中英两侧是否都出现了探针要的那句话」，不是「中英语义是否等值」**——
  两侧各中一次不同的话也会判绿。
- C 段的残余项**用「今天的事实」判**，不用 08-21 当时的措辞判——那正是「登记过期」的所在。
- 本脚本**不核** §2 那五行「修订引入的新问题检查」的正确性（见 D 段说明：其判据对象已随
  数据重算而过期，只能登记，不能复验）。
- `--drift-test` 只打**基线为绿**的判据（打一个本来就红的判据，红的集合不变、用例恒真），
  这条前提由 `drift_test()` 里的元判据当场断言。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / 'docs' / 'paper' / 'eswa_paper.md'                     # V1 存档（勿改）
V2 = ROOT / 'docs' / 'paper_v2' / 'eswa_paper_v2.md'               # 英文主源
ZH = ROOT / 'docs' / 'paper_v2' / 'eswa_paper_v2_zh.md'            # 中文镜像
REC = ROOT / 'docs' / 'paper_v2' / '修订对账记录.md'                # 本节落点
RPT = ROOT / 'docs' / 'paper' / 'reviews' / '01_re_review.md'      # 被核的评审报告本身

# R-1 的登记句：残余项称「bootstrap CI 无法计算」，而 V2 实测已有 ⇒ 属「登记过期」。
# 处置不是去改评审报告（那是历史记录），而是**在对账记录里登记它已过期**。
# 判据因此要求这句话在 REC 里**逐字出现**——登记句本身就是可证伪的凭据：
# 哪天 V2 的 CI 真没了，上面那一支会接管；哪天登记句被删了，这里立刻转红。
R1_MARK = '`01_re_review.md` 的 R-1 残余项已过期'


def hits(text: str, pat: str) -> int:
    return len(re.findall(pat, text, re.S))


# ─────────────────────────────────────────────────────────────────────
# A 段：17 项 × (编号, 描述, V1 探针, V2-EN 探针, V2-ZH 探针, 期望态, 备注)
#   期望态取值：kept / strengthened / changed（regressed 等由实测覆盖）
# ─────────────────────────────────────────────────────────────────────
ITEMS = [
    ('P0-1', '消融段：2×2 分解措辞与输出策略的各自贡献',
     r'Controlled ablation|controlled ablation',
     r'constrained ablation',
     r'受限消融|受约束消融|措辞敏感性',
     'strengthened', 'V2 降调为 constrained 并声明「非正式因子分析」'),

    ('P0-2', '模型依赖论断：由「反转排名」改写为「排名差异 + 混淆声明」',
     r'reversing the ranking|reversal relative to',
     # EN 探针首版写 `model-dependent|task-dependent`（形容词、带连字符）而 V2 第 302 行
     # 写的是名词形 `model dependence, task dependence` ⇒ 0 命中是**探针窄了**，不是漏译。
     r'model[- ]dependence|task[- ]dependence|ranking difference',
     r'模型依赖|排名差异',
     'strengthened', "V2 英文侧 `revers` 归零，改为名词形 dependence + ranking difference"),

    ('P1-1', 'CAICS 推导协议与其范围声明',
     r'Taxonomy derivation',
     r'derived through corpus-driven induction',
     r'语料驱动归纳|语料归纳',
     'strengthened', 'V2 删小节标题，改为段落 + 主动声明「非正式内容效度研究」'),

    ('P1-2', '与 24Model / Accimap 的定位对话',
     r'24Model',
     r'24Model|Accimap',
     r'24Model|Accimap',
     'kept', ''),

    ('P1-3', '去除 `LLM-optimized` 表述（须 0 命中）',
     r'LLM-optimized',
     r'LLM-optimized',
     r'LLM-optimized',
     'kept', 'V1③V2 两侧均 0 ⇒ 双侧静默，见表末 REGRESSION 说明'),

    ('P2-1', '单次运行声明（`ran once at temperature 0.0`）',
     r'All strategies ran once',
     r'All configurations ran once',
     r'均只运行一次|各运行一次|运行一次',
     'kept', 'V2 由 strategies 扩为 configurations'),

    ('P2-2', '自动档位路由的透明声明',
     r'tier routing|auto',
     r'tier routing',
     r'档位|路由',
     'kept', 'V2 落在 §4.2 与局限性 (4)'),

    ('P2-3', '银标准如实描述 + 独立性边界声明',
     r'silver standard',
     r'not an independent human gold standard',
     # ZH 探针首版写 `并非独立的人工金标准`，中文镜像第 233 行写的是 `它不是独立人工金标准`
     # ⇒ 0 命中是**探针窄了**（多了「并非」与「的」），不是中文漏译。
     r'(?:不是|并非|非)独立(?:的)?人工金标准',
     'strengthened', 'V2 新增边界声明（02_overnight_report 记的「重大诚信修正」）'),

    ('P2-4', 'kappa 跨样本保留声明（按样本分列、不以单一标签统称）',
     r'Fleiss',
     r'Evaluation samples|Cohen',
     r'评估样本|Cohen|标注者',
     'kept', 'V2 新增 `**Evaluation samples.**` 专段'),

    ('P2-5', 'S2 后处理脚注',
     r'[Pp]ost-process',
     r'post-processing step that recovered',
     r'后处理',
     'kept', 'V2 删去「上界估计」措辞'),

    ('P2-6', '标注者独立性声明',
     r'independent',
     r"without access to the pipeline's extracted values",
     r'不接触管道|未接触管道|无法接触',
     'kept', ''),

    ('P3-1', 'Code availability 声明',
     r'Code availability',
     r'Code availability',
     r'代码可用性',
     'strengthened', 'V2 增「审稿期按需提供最小复现包」'),

    ('P3-2', '语料两时段分布 + 境外记录说明',
     r'1,925',
     r'1,895',
     r'1,895',
     'changed', 'V2 单列 30 条无日期记录（更准确），非丢失'),

    ('P3-3', '12 类事故类型的 GB 6441-86 来源与粒度解释',
     r'GB 6441-86',
     r'GB 6441-86',
     r'GB 6441-86',
     'kept', '曾于改写中回退（V1 有、V2 中英 0 命中），2026-10-05 已照搬补回，见第三十四节'),

    ('P3-4', '局限性章项数',
     r'Limitation',
     r'Limitation',
     r'局限性',
     'strengthened', 'V2 由无编号列表扩为 8 项分级'),

    ('P3-5', '人机一致性主张的字段范围限定',
     r'Cohen',
     r'six of 33 fields',
     r'33 项字段中的 6 项|六项字段',
     'strengthened', 'V2 明写「非专家级准确率认证」'),
]
N_ITEMS = 17   # 条目总数——防「加项忘了改总数」（§2.5.74）；实由元判据对 RPT 闭合核

NO_ACTION = [
    ('P2-7', 'S5 逐层失败解释：评审方作「共识性讨论」记录，处置栏与核验栏均为「—」'
             '⇒ 无改动要求'),
]
"""评审报告 17 项里**唯一没有改动要求**的一项：它没有「修订方案」，只有一句共识记录。

略去它必须有**可证伪的登记**（§2.5.67：只写理由不验证＝永久豁免），故不进 ITEMS
（ITEMS 的每一项都要求正反两面的探针），而单列在此，并由元判据与 ITEMS 一起
**对着 RPT 闭合核**——报告若给 P2-7 补上改动要求，或我漏了别的项，元判据转红。
写死「17」不如这条闭合核（§2.5.75：写死的固定值本身可能就是响应式的）。
"""
N_ITEMS_TOTAL = len(ITEMS) + len(NO_ACTION)   # 16 + 1 = 17，与 RPT 闭合


# ─────────────────────────────────────────────────────────────────────
# 判决：由三个命中数推出结论（纯函数，便于 --self-test 直接喂数验证）
# ─────────────────────────────────────────────────────────────────────
OK_VERDICTS = ('kept', 'strengthened', 'changed')
RED_VERDICTS = ('asym', 'regressed', 'probe-dead', 'REGRESSION')


def verdict_of(n1: int, n2: int, n3: int, expect: str) -> str:
    """| V1 | V2 EN/ZH | 判决 | 判红 |
    |---|---|---|---|
    | >0 | 两侧都 >0 | expect | 否（内容在） |
    | >0 | **仅一侧** >0 | `asym` | 🔴 **中英不对称** |
    | >0 | 都 =0 | `regressed` | 🔴 改写中丢失 |
    | =0 | 任一侧 >0 | `probe-miss` | 否（V1 面失效，观察项） |
    | =0 | 都 =0 | `probe-dead` | 🔴 判据自身失效 |
    """
    if n1 > 0:
        if n2 > 0 and n3 > 0:
            return expect if expect in OK_VERDICTS else 'kept'
        if n2 > 0 or n3 > 0:
            return 'asym'
        return 'regressed'
    if n2 > 0 or n3 > 0:
        return 'probe-miss'
    return 'probe-dead'


def part_a(v1: str, v2: str, zh: str) -> tuple[list, list]:
    """返回 (rows, reds)。rows 为 [(编号, 判决)]，reds 为 [(标识, 行文本)]。"""
    rows, reds = [], []
    print('\n【A 段】17 项：V1 自证面 × V2 实测')
    print(f"  {'项':<6}{'V1':<5}{'EN':<5}{'ZH':<5}{'判决':<13}备注")
    print('  ' + '-' * 92)
    for rid, desc, p1, p2, p3, expect, note in ITEMS:
        n1, n2, n3 = hits(v1, p1), hits(v2, p2), hits(zh, p3)
        # P1-3 是「须 0 命中」项：命中数 >0 才是问题
        if rid == 'P1-3':
            verdict = 'clean' if (n1 + n2 + n3 == 0) else 'REGRESSION'
            if verdict != 'clean':
                reds.append((rid, f'{rid}（{desc}）：LLM-optimized 未清除'
                                  f' V1={n1} EN={n2} ZH={n3}'))
            print(f'  {rid:<6}{n1:<5}{n2:<5}{n3:<5}{verdict:<13}'
                  f'{desc}·须 0 命中')
            rows.append((rid, verdict))
            continue

        verdict = verdict_of(n1, n2, n3, expect)
        if verdict == 'asym':
            miss = '中文' if n3 == 0 else '英文'
            reds.append((rid, f'{rid}（{desc}）：中英不对称，{miss}侧 0 命中'
                              f'（EN={n2} ZH={n3}）—— 先查是漏译还是探针写窄了'))
        elif verdict == 'regressed':
            reds.append((rid, f'{rid}（{desc}）：V1 有 {n1} 处、V2 EN={n2} ZH={n3}'
                              f' ⇒ 改写中丢失'))
        elif verdict == 'probe-dead':
            reds.append((rid, f'{rid}（{desc}）：探针三面全不中 ⇒ 判据自身失效'))
        print(f'  {rid:<6}{n1:<5}{n2:<5}{n3:<5}{verdict:<13}{note or desc}')
        rows.append((rid, verdict))
    return rows, reds


def part_b(v1: str, v2: str, zh: str) -> list:
    """B 段：08-21 那轮明确要求「消失」的表述，V2 中英须为 0。

    只收 08-21 自己的两条具体要求（P0-2 的反转论断、P1-3 的 LLM-optimized）。
    导师第 7 类禁用措辞由 `check_forbidden_v2.py` 负责、第 8 类（排除替代解释）
    由 `check_review_compliance.py` 负责 —— **本脚本不重造**（§2.5.52 能引已有凭据就引）。
    """
    reds = []
    print('\n【B 段】须消失的表述（V2 中英须 0 命中）')
    for bid, name, pat, why in [
        ('B1', '反转排名论断', r'reversing the ranking|reversal relative',
         'P0-2：V1 用「反转 Liu et al. 的排名」，V2 改为「模型依赖的排名差异」'),
        ('B2', 'LLM 优化说法', r'LLM-optimized', 'P1-3：08-21 要求删除'),
    ]:
        n2, n3 = hits(v2, pat), hits(zh, pat)
        ok = (n2 + n3) == 0
        print(f"  {'✅' if ok else '🔴'} {name:<12} EN={n2} ZH={n3}  {why}")
        if not ok:
            reds.append((bid, f'{name}：V2 EN={n2} ZH={n3} 未清除'))
    return reds


def part_c(v1: str, v2: str, zh: str, rec: str) -> list:
    """C 段：§3「残余问题」三条 —— 用**今天的事实**判，不用 08-21 当时的措辞判。"""
    reds = []
    print('\n【C 段】残余问题 R-1 / R-2 / R-3（08-21 遗留，今天还算不算数）')

    # R-1：残余项称「bootstrap CI 无法计算（GT 文件丢失）」
    boot = hits(v2, r'bootstrap') + hits(zh, r'自举|bootstrap')
    ci = hits(v2, r'percentile bootstrap|95% CI|confidence interval') + hits(zh, r'置信区间')
    if not (boot and ci):
        print(f'  ✅ R-1 与残余项描述一致（V2 仍无 bootstrap CI：boot={boot} ci={ci}）')
    elif R1_MARK in rec:
        print(f'  ✅ R-1 已登记过期（对账记录含逐字登记句；V2 实测 bootstrap {boot} 处 /'
              f' CI {ci} 处 ⇒ CI 早已补算，GT 已由 recover_gt.py 从三专家标注恢复）')
    else:
        print(f'  🔴 R-1 登记过期：残余项称「bootstrap CI 无法计算（GT 文件已丢失）」，'
              f'而 V2 实测 bootstrap {boot} 处 / CI {ci} 处 ⇒ **CI 早已补算**；'
              f'对账记录里没有登记句 {R1_MARK!r}')
        reds.append(('R-1', 'R-1：残余项称 CI 无法计算，实测 V2 已有 bootstrap CI，'
                             '且对账记录未登记该过期状态 ⇒ 登记过期'))

    # R-2：篇幅
    w2 = len(v2.split())
    print(f'  ⚠️ R-2 需重测：残余项记「10,263 词，超上限 9,900」；'
          f'V2 全文空白分词 ≈{w2} 词（**含表格与参考文献，与期刊计词口径不同**）'
          f'⇒ 按 ESWA 口径重测前不能销账，也不能据此判红')

    # R-3：图 4 银标准描述
    sg = hits(v2, r'silver standard') + hits(zh, r'银标准')
    print(f'  ✅ R-3 无需改图：残余项称「图 4 银标准描述已按新表述理解」，'
          f'V2 silver standard {sg} 处、图 4 仍为银标准层级图 ⇒ 该判断仍成立')
    return reds


def part_d(rec: str) -> list:
    """D 段：文档对账 —— 本节写进对账记录的数字须与实测一致。"""
    reds = []
    print('\n【D 段】文档对账')
    if not REC.exists():
        print(f'  🔴 对账记录不存在：{REC}')
        return [('D', '对账记录缺失')]
    if not re.search(r'### 34\.', rec, re.M):
        print('  🔴 对账记录中未找到本节（### 34.）')
        return [('D', '对账记录未写入第三十四节')]
    print('  ✅ 对账记录含第三十四节标题')
    # §2 五行新问题检查：判据对象已过期，只能登记不能复验
    if '新问题检查' in rec:
        print('  ✅ 已登记 §2 五行「新问题检查」的过期状态')
    else:
        print('  ⚠️ 未登记 §2 五行「新问题检查」的过期状态'
              '（应写明其判据对象已随数据重算过期）')
    return reds


# ─────────────────────────────────────────────────────────────────────
def run(marks: bool = False) -> int:
    for p in (V1, V2, ZH):
        if not p.exists():
            print(f'🔴 缺文件：{p}')
            return 1
    v1 = V1.read_text(encoding='utf-8')
    v2 = V2.read_text(encoding='utf-8')
    zh = ZH.read_text(encoding='utf-8')
    rec = REC.read_text(encoding='utf-8') if REC.exists() else ''

    print('=' * 96)
    print('模拟评审 17 项（docs/paper/reviews/01_re_review.md，2026-08-21）→ V2 落实覆盖核验')
    print('=' * 96)
    rows, reds = part_a(v1, v2, zh)
    reds += part_b(v1, v2, zh)
    reds += part_c(v1, v2, zh, rec)
    reds += part_d(rec)

    cnt = Counter(v for _, v in rows)
    print('\n' + '=' * 96)
    print('判决分布：' + ' · '.join(f'{k} {v}' for k, v in sorted(cnt.items())))
    print(f'待处置：{len(reds)} 条')
    for _rid, line in reds:
        print(f'  🔴 {line}')
    print('=' * 96)
    red_ids = sorted({rid for rid, _ in reds})
    # 机器可读出口：注入式自证要断言「红的**恰好**是哪几条」，解析中文输出太脆（§2.5.68）。
    # 无条件打印——它不得被任何别的开关顺手关掉（§2.5.101 的镜像形态）。
    print(f'MARKS {json.dumps({"reds": red_ids, "n_items": len(rows)}, ensure_ascii=False)}')
    if red_ids:
        print('❌ 存在待处置项')
        return 1
    print('✅ 17 项全部覆盖 · 须消失的表述已清除 · 残余项已判定 · 文档已对账')
    return 0


# ─────────────────────────────────────────────────────────────────────
# 自证 1：--self-test（判据与探针的正反用例，不碰文件）
# ─────────────────────────────────────────────────────────────────────
def self_test() -> int:
    v1 = V1.read_text(encoding='utf-8')
    v2 = V2.read_text(encoding='utf-8')
    zh = ZH.read_text(encoding='utf-8')
    # 闭合核的两端：报告自己列的项编号 vs 我登记的（ITEMS ∪ NO_ACTION）。
    # 不用写死的 17——报告若增删项，写死的数不会变（§2.5.75：写死的固定值
    # 本身可能就是响应式的）；对称差同时抓「我漏了」与「我杜撰了」。
    rpt_ids = re.findall(r'^\|\s*(P\d-\d)\s*\|',
                         RPT.read_text(encoding='utf-8'), re.M)
    mine = [t[0] for t in ITEMS] + [t[0] for t in NO_ACTION]
    cases = [
        # ── 判决逻辑五态（正反成对）
        ('判决·三面全中→kept',            verdict_of(1, 1, 1, 'kept'), 'kept'),
        ('判决·三面全中→strengthened',    verdict_of(2, 3, 4, 'strengthened'), 'strengthened'),
        ('判决·三面全中→changed',         verdict_of(1, 1, 1, 'changed'), 'changed'),
        ('判决·非法 expect 兜底 kept',     verdict_of(1, 1, 1, 'bogus'), 'kept'),
        ('判决·EN 缺→asym',               verdict_of(1, 0, 1, 'kept'), 'asym'),
        ('判决·ZH 缺→asym',               verdict_of(1, 1, 0, 'kept'), 'asym'),
        ('判决·中英都缺→regressed',        verdict_of(1, 0, 0, 'kept'), 'regressed'),
        ('判决·仅 EN 有、V1 无→probe-miss', verdict_of(0, 1, 0, 'kept'), 'probe-miss'),
        ('判决·仅 ZH 有、V1 无→probe-miss', verdict_of(0, 0, 1, 'kept'), 'probe-miss'),
        ('判决·三面全空→probe-dead',       verdict_of(0, 0, 0, 'kept'), 'probe-dead'),
        # ── hits 基本行为（含边界：大小写敏感、re.S）
        ('hits·基本计数',                 hits('aXaXa', 'X'), 2),
        ('hits·无命中',                   hits('aaa', 'X'), 0),
        ('hits·re.S 跨行',                hits('a\nb', 'a.b'), 1),
        ('hits·无 (?i) 则大小写敏感',       hits('AB', 'ab'), 0),
        # ── B 段判据能命中（合成正例）与它的边界（英式拼写不命中，如实登记）
        ('B 段探针命中合成正例',           hits('a LLM-optimized pipeline', r'LLM-optimized'), 1),
        ('B 段探针不认英式拼写（登记边界）', hits('LLM-optimised', r'LLM-optimized'), 0),
        ('C 段 bootstrap 探针能命中',      hits('percentile bootstrap', r'bootstrap'), 1),
        ('C 段 R1 登记句可被逐字核',        R1_MARK in ('x ' + R1_MARK + ' y'), True),
        # ── 探针表结构 + 与评审报告的**闭合核**
        ('ITEMS 每项 7 元组',             sorted({len(t) for t in ITEMS}), [7]),
        ('ITEMS + NO_ACTION 项数 == 17',  len(ITEMS) + len(NO_ACTION), N_ITEMS),
        ('元判据·编号集合与 RPT 完全一致',
         sorted(set(rpt_ids) ^ set(mine)), []),   # 对称差：多出的与漏掉的都在这里
    ]
    # 元判据（①）：V1 自证面的有效性 —— 除 P1-3（须 0 命中项）外，
    # 16 项 V1 探针须全部 >0。这是「V1 当自证面」这条设计的前提：
    # 若某项在 V1 上也命中不了，它的 regressed 判决就是探针错、不是 V2 丢（§2.5.64）。
    dead = [rid for rid, _d, p1, _p2, _p3, _e, _n in ITEMS
            if rid != 'P1-3' and hits(v1, p1) == 0]
    cases.append(('元判据·16 项 V1 探针须全部有效', dead, []))
    # 元判据（②）：每项的 V2 中英探针须能命中该稿本身（否则该探针是死的）——
    # 但 P3-3 是实测回退项、P1-3 是须 0 命中项，两者豁免。
    both = [rid for rid, _d, _p1, p2, p3, _e, _n in ITEMS
            if rid not in ('P1-3', 'P3-3')
            and not (hits(v2, p2) > 0 and hits(zh, p3) > 0)]
    cases.append(('元判据·除 P3-3/P1-3 外中英探针双面有效', both, []))

    bad = 0
    print('【--self-test】判据与探针的正反用例')
    for name, got, want in cases:
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✅' if ok else '🔴'} {name:<34} got={got!r}"
              + ('' if ok else f'  want={want!r}'))
    print(f'\n--self-test {len(cases) - bad}/{len(cases)}')
    return 0 if bad == 0 else 1


# ─────────────────────────────────────────────────────────────────────
# 自证 2：--drift-test（注入式——往真实文件里改一处，断言红的**恰好**是哪几条）
# ─────────────────────────────────────────────────────────────────────
BASELINE_REDS = set()
"""**建脚本当天的实测基线是 `{'P3-3', 'R-1', 'D'}`**，随后两条真缺陷各自修掉
（P3-3 照搬 V1 原文补回、R-1 在对账记录里逐字登记过期）、D 段随第三十四节写入
转绿 ⇒ 今日基线为**空集**。这里不放宽判据：**保留空集而不是删掉这条断言**，
因为「基线为空」同样是一个会过期的事实——哪天正文改动让某项回退，实测基线
不再为空，前置断言当场转红（§2.5.75：写死的固定值本身可能就是响应式的）。"""

DRIFTS = [
    ('I1', '删 V2 EN 的 `constrained ablation`（P0-1 的 EN 探针）',
     V2, 'constrained ablation', 'c-abl', {'P0-1'}),
    ('I2', '改写 ZH 的独立性声明（P2-3 的 ZH 探针）',
     ZH, '它不是独立人工金标准', '它不是独立金标准', {'P2-3'}),
    # I3 的红集合**必然含两项**：B2（08-21 要求删除）与 P1-3（A 段「须 0 命中」）。
    # 二者盯的是同一个字符串，是同一事实的两个独立视角——注入一次两条都响，
    # 这是设计如此。首版只写 {'B2'} 是用例的期望写窄了（§2.5.71：加一处时别处
    # 的期望集合要跟着走），实测多红一条 P1-3 当场抓到。
    ('I3', '往 V2 插入 `LLM-optimized`（B 段 + A 段 P1-3 两条判据）',
     V2, '## References', 'LLM-optimized\n## References', {'B2', 'P1-3'}),
    # I4 是 **D 段判据唯一的失败路径**。此前第三十四节尚未写入、`### 34.` 本就不在
    # 文档里，注入等于没做（§2.5.89：注入没做与判据没报在输出上一模一样），故先注释
    # 掉；第三十四节写入后基线转绿，这条才真正有意义——它证明 D 段不是恒真。
    # 注入会把 `### 34.x` 全部 11 处小节标题一起改掉（`replace` 是全局的），
    # 这正是我们要的：D 段核的是「本文件有没有第三十四节」。
    ('I4', '改对账记录的节标题（D 段判据）',
     REC, '### 34.', '### 99.', {'D'}),
]


def _run_marks() -> set:
    """另起进程跑本脚本，取 MARKS 里的红集合。

    **必须另起进程**：同进程里改文件后 `run()` 会重新读文件、看着像能用，
    但那样验证的是「函数读得到改动」而不是「整条链路读得到改动」；
    另起进程还顺带证明脚本真去读了那个路径（§2.5.95 的教训：
    注入改副本而判据读真文件 ⇒ 注入等于没做）。
    """
    r = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith('MARKS '):
            return set(json.loads(line[6:])['reds'])
    raise RuntimeError(f'子进程未输出 MARKS 行（rc={r.returncode}）\n{r.stdout[-2000:]}')


def _inject(path: Path, old: str, new: str) -> str:
    """把 old 换成 new，返回原文（调用方负责 finally 恢复）。

    替换 0 次直接抛——「注入没做」与「判据没报」在输出上一模一样（§2.5.89）。
    """
    orig = path.read_text(encoding='utf-8')
    if old not in orig:
        raise RuntimeError(f'锚点未命中：{old!r} in {path}')
    mutated = orig.replace(old, new)
    if mutated == orig:
        raise RuntimeError(f'注入未改变文本：{old!r} → {new!r}')
    path.write_text(mutated, encoding='utf-8')
    return orig


def drift_test() -> int:
    # 元判据：漂移用例只能打**基线为绿**的判据 —— 打一个本来就红的判据，
    # 红的集合不变、用例恒真（§2.5.89「注入没做与判据没报在输出上一模一样」的镜像）。
    bad_expect = [(d[0], sorted(d[5] & BASELINE_REDS)) for d in DRIFTS
                  if d[5] & BASELINE_REDS]
    if bad_expect:
        print(f'🔴 元判据失败：期望集与基线红集合相交 ⇒ 用例恒真：{bad_expect}')
        return 1
    print(f'✅ 元判据：{len(DRIFTS)} 条用例的期望集与基线红集合 {sorted(BASELINE_REDS)} 不相交')

    base = _run_marks()
    if base != BASELINE_REDS:
        print(f'🔴 前置断言失败：基线红集合实测 {sorted(base)}'
              f' ≠ 登记的 {sorted(BASELINE_REDS)} ⇒ 登记已过期，请更新 BASELINE_REDS'
              f'（或先修掉新出现的红）')
        return 1
    print(f'✅ 前置断言：基线红集合 == {sorted(base)}')

    bad = 0
    for did, name, target, old, new, expect in DRIFTS:
        orig = None
        try:
            orig = _inject(target, old, new)
            got = _run_marks()
            want = base | expect
            ok = got == want
        finally:
            if orig is not None:
                target.write_text(orig, encoding='utf-8')
            else:
                raise RuntimeError(f'{did} 未注入成功，现场未恢复')
        got_after = _run_marks()
        restored = got_after == base
        ok = ok and restored
        bad += 0 if ok else 1
        print(f"  {'✅' if ok else '🔴'} {did} {name}")
        print(f"      红的恰好：{sorted(got)}"
              + ('' if ok else f'  want={sorted(want)}  恢复后={sorted(got_after)}'))
    print(f'\n--drift-test {len(DRIFTS) - bad}/{len(DRIFTS)}')
    return 0 if bad == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='模拟评审 17 项 → V2 落实覆盖核验')
    ap.add_argument('--self-test', action='store_true', help='判据与探针的正反用例')
    ap.add_argument('--drift-test', action='store_true', help='注入式自证（改真实文件后恢复）')
    ap.add_argument('--marks', action='store_true', help='（保留）MARKS 行本就无条件打印')
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.drift_test:
        return drift_test()
    return run(marks=args.marks)


if __name__ == '__main__':
    sys.exit(main())
