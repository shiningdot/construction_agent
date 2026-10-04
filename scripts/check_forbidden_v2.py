#!/usr/bin/env python3
"""V2 禁用措辞核查：导师要求删除的结论性措辞是否已从正文清除。

CLAUDE.md「写作时不得违反的既定决策」第 5 条的禁用措辞：
首次 / 推翻 / 证明 / 因果机制 / 专家级 / 替代人工 / 全部编码有证据。

与 check_citations_v2.py（引用）、check_xref_v2.py（结构）、check_numbers_v2.py（数字）
并列，是第四类投稿红线检查——前三类管"数字/引用/结构对不对"，这一类管
"话能不能这么说"。

**不能裸搜词**，三个坑都在探针里实测到了：

  1. CAICS 的 "causal" 是体系术语（Causal Analysis…），只有成词组的
     "causal mechanism" / "因果机制" 才是违规表述；
  2. 英文 "prove" 会被 improve / approved / provenance **词内**命中——探针
     首轮 4 处命中全是假阳性，必须用 \\b 词边界；
  3. 多数命中是**合法的否定式**：论文恰恰要显式声明"不声称因果机制"
     （"not as an isolated causal mechanism"「而非已分离的因果机制」），
     这类是风险披露，删掉反而违背了"不得隐瞒局限"的要求。

故判定取命中前的一段窗口文字（中文 12 字 / 英文 45 字符），含否定标记即判为
合法否定式；窗口内无否定标记才是违规。

另有"修订说明"段（`*V2 修订说明…投稿时删除本段*`）——它逐字复述了被删措辞，
本身不是正文，**但必须在投稿前删除**，故单独成段报告，不计入退出码。

用法：python3 scripts/check_forbidden_v2.py [--verbose | --self-test]
退出码 0 = 正文无违规；1 = 有禁用措辞且不在否定语境中。
--self-test 用 18 条构造样本正反双向验证判定逻辑本身（不读论文文件）。
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / 'docs' / 'paper_v2'
FILES = {'EN': BASE / 'eswa_paper_v2.md', 'ZH': BASE / 'eswa_paper_v2_zh.md'}

# 中文禁用词 → 英文对应形态（正则）。英文一律带 \b，避免词内假阳性。
FORBIDDEN = [
    # ⚠ "the first" 不能裸查——"The first gate operates on titles"（第一道闸门）
    # 是流程描述，实测假阳性；只留宣称首创的几种句式。
    ('首次', [r'\bfirst-ever\b', r'\bfor the first time\b',
              r'\bthe first to\b', r'\bfirst system\b']),
    ('推翻', [r'\brefut\w*\b', r'\boverthrow\w*\b', r'\bdisprove\w*\b',
              r'\boverturn\w*\b']),
    # ⚠ 不能写 \bprov\w*\b——实测命中 provincial / province / provenance /
    # provider / provided 共 15 处假阳性；只认 prove 词族的屈折形式。
    ('证明', [r'\bprove\b', r'\bproves\b', r'\bproven\b',
              r'\bproof\b', r'\bproofs\b']),
    ('因果机制', [r'\bcausal mechanism\w*\b', r'\bcausal mechanisms\b']),
    ('专家级', [r'\bexpert-level\b', r'\bexpert level\b',
                r'\bhuman[- ]expert\b', r'\bexpert human\b']),
    ('替代人工', [r'\breplac\w+ human\w*\b', r'\breplac\w+ manual\w*\b',
                  r'\bsubstitut\w+ for human\w*\b', r'\breplac\w+ annotator\w*\b']),
    ('全部编码有证据', [r'\ball codes (are|have|were) (evidence\w*|evidenc\w*)',
                        r'\bevery code is evidenced\b',
                        r'\ball codes evidence-bound\b']),
]

# 否定语境标记。中文取字，英文取词（带 \b）。
NEG_ZH = ['不', '非', '未', '无', '没有', '拒绝', '删除', '否认', '避免',
          '排除', '摒弃', '而非', '并非']
NEG_EN = [r'\bnot\b', r"\bn't\b", r'\bno\b', r'\bnever\b', r'\bwithout\b',
          r'\brather than\b', r'\bcannot\b', r'\bneither\b', r'\bnor\b',
          r'\bbeyond\b', r'\bunable\b', r'\bfail\w* to\b', r'\bavoid\w*\b']

WIN = {'ZH': 12, 'EN': 45}
META = re.compile(r'^\*V2 (修订说明|Revision)')


def has_negation(prefix, tag):
    if tag == 'ZH':
        return any(n in prefix for n in NEG_ZH)
    return any(re.search(n, prefix, re.I) for n in NEG_EN)


def iter_hits(line, tag):
    """产出一行里的全部命中：(kind, 禁用词, 命中文本, 起点)。

    kind ∈ {violation, legal, meta}。判定逻辑只此一处，main 与 --self-test 共用。
    """
    is_meta = bool(META.match(line.strip()))
    words = ([(zh, [zh]) for zh, _ in FORBIDDEN] if tag == 'ZH' else FORBIDDEN)
    for zh, pats in words:
        for pat in pats:
            for m in re.finditer(pat, line, re.I):
                prefix = line[max(0, m.start() - WIN[tag]):m.start()]
                if is_meta:
                    kind = 'meta'
                elif has_negation(prefix, tag):
                    kind = 'legal'
                else:
                    kind = 'violation'
                yield kind, zh, m.group(0), m.start()


# ── 自测用例：(行文本, 语言, 期望分类) ──────────────────────────────────
# "退出码 0"只有在脚本确有能力报 1 时才可信（对照对账记录 10.5：
# 转录型脚本的绿可能来自覆盖面不足）。用 --self-test 正反双向验证。
SELF_TEST = [
    # 真违规：必须报 violation
    ('CAIEMS is the first-ever system to mine Chinese accident reports.',
     'EN', 'violation'),
    ('Our results prove that the pipeline generalises across provinces.',
     'EN', 'violation'),
    ('This work provides a proof of the causal mechanism behind collapses.',
     'EN', 'violation'),
    ('We replace human annotators with the LLM encoder.', 'EN', 'violation'),
    ('本文将事故报告转化率提升至专家级水平。', 'ZH', 'violation'),
    ('本文首次提出面向施工事故的多源采集框架，证明模型依赖。', 'ZH', 'violation'),
    ('CAICS 实现了全部编码有证据的自动标注。', 'ZH', 'violation'),
    # 合法否定式：报 legal，不得报 violation
    ('We report this as an observation, not as an isolated causal mechanism.',
     'EN', 'legal'),
    ('We do not claim an isolated causal mechanism.', 'EN', 'legal'),
    ('This is an agreement study, not a certification of expert-level accuracy.',
     'EN', 'legal'),
    ('No causal mechanism or expert substitution is claimed.', 'EN', 'legal'),
    ('我们将其报告为观察性现象，而非已分离的因果机制。', 'ZH', 'legal'),
    ('这是一项一致性研究，不是专家级准确性的认证。', 'ZH', 'legal'),
    ('全文对结果边界作显式声明：不声称因果机制或专家替代。', 'ZH', 'legal'),
    # 假阳性回归：一个字都不该命中（首轮 \\bprov\\w*\\b 曾误报 15 处）
    ('Provincial emergency-management bureaus and provider records were '
     'approved; provenance is recorded per province.', 'EN', 'none'),
    ('The first gate operates on titles; the second on body text.',
     'EN', 'none'),
    ('DeepSeek V4 was accessed with automatic tier routing enabled.',
     'EN', 'none'),
    # 元数据段
    ('*V2 Revision note: removed claims of first-ever and causal mechanism.*',
     'EN', 'meta'),
]


def self_test():
    """正反双向自测。返回 0 = 全过。"""
    fails = 0
    for text, tag, expect in SELF_TEST:
        kinds = {k for k, *_ in iter_hits(text, tag)}
        if expect == 'none':
            ok = not kinds
        elif expect == 'meta':
            ok = kinds == {'meta'}
        else:
            ok = kinds == {expect}
        fails += not ok
        print(f'{"✅" if ok else "❌"} [{tag}] 期望 {expect:<10} '
              f'实得 {sorted(kinds) or ["none"]}')
        print(f'     {text[:76]}')
    print('\n' + '=' * 70)
    if fails:
        print(f'❌ {fails}/{len(SELF_TEST)} 条自测失败——脚本判定逻辑有问题')
        return 1
    print(f'✅ {len(SELF_TEST)} 条自测全过：对真违规报得出，'
          f'对否定式与词内假阳性不误报')
    return 0


def main(verbose=False):
    violations, legal, meta_hits = [], [], []
    per_word = {}

    for tag, path in FILES.items():
        raw = path.read_text(encoding='utf-8').split('\n')
        for lineno, line in enumerate(raw, 1):
            for kind, zh, hit, start in iter_hits(line, tag):
                seg = line[max(0, start - 50):start + len(hit) + 50].strip()
                rec = (tag, lineno, zh, hit, seg)
                per_word[zh] = per_word.get(zh, 0) + 1
                if kind == 'meta':
                    meta_hits.append(rec)
                elif kind == 'legal':
                    ctx = line[max(0, start - WIN[tag]):start].strip()[-20:]
                    legal.append((*rec, ctx))
                else:
                    violations.append(rec)

    # ── A. 正文违规 ───────────────────────────────────────────────
    print('A. 正文禁用措辞（排除否定语境与修订说明段）')
    print('-' * 70)
    if violations:
        print(f'  ❌ {len(violations)} 处违规：')
        for tag, ln, zh, hit, seg in violations:
            print(f'    {tag} 行{ln}  「{zh}」→ "{hit}"')
            print(f'        …{seg}…')
    else:
        print('  ✅ 无违规')

    # ── B. 合法否定式（风险披露，逐条复核） ────────────────────────
    print(f'\nB. 合法否定式（命中 {len(legal)} 处，均为"不声称…"式风险披露）')
    print('-' * 70)
    for tag, ln, zh, hit, seg, ctx in legal:
        if verbose:
            print(f'    {tag} 行{ln}  「{zh}」  否定词: "{ctx}"')
            print(f'        …{seg}…')
    if not legal:
        print('  （无）')
    elif not verbose:
        print(f'  {len(legal)} 处已判定合法（--verbose 逐条查看）')

    # ── C. 修订说明段 ─────────────────────────────────────────────
    print('\nC. 修订说明段中的禁用措辞（提醒：投稿前必须删除该段）')
    print('-' * 70)
    if meta_hits:
        lines = sorted({(t, l) for t, l, *_ in meta_hits})
        print(f'  ⚠ {len(lines)} 行命中，均为元数据段复述被删措辞，非正文：'
              f'{"、".join(f"{t} 行{l}" for t, l in lines)}')
    else:
        print('  ✅ 无（或该段已删除）')

    # ── 汇总 ──────────────────────────────────────────────────────
    print('\n' + '=' * 70)
    total = sum(per_word.values())
    print(f'命中总计 {total}：违规 {len(violations)} / 合法否定式 {len(legal)} / '
          f'修订说明 {len(meta_hits)}')
    if violations:
        print(f'❌ {len(violations)} 处待处置——禁用措辞不得以肯定句出现于正文')
        return 1
    print('✅ 正文无肯定句形式的禁用措辞，可接进改稿流程')
    if meta_hits:
        print('   投稿前记得删除「V2 修订说明」段（本脚本只提醒，不代删）')
    return 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    sys.exit(main('--verbose' in sys.argv))
