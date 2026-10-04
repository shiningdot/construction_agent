#!/usr/bin/env python3
"""V2 表格数字 ↔ 源数据对账（第五类核验）。

前四类核验管的是"正文与自身的另一份记录对不对"：
  check_citations_v2.py  文献 ↔ 正文引用
  check_xref_v2.py       图表/章节编号 ↔ 正文引用
  check_numbers_v2.py    数字对账清单 ↔ 正文
  check_forbidden_v2.py  禁用措辞

但**数字对账清单只登记"全文统一值"**（2,623、16,323 这类要反复出现的数），
表 5/6/7/8/9 里几百个只在表格里出现一次的数字**从来不在清单上**——所以第十节
的"清单↔正文"报零缺陷，对它们而言并不构成保护。本脚本补的就是这个盲区：
把每张表逐格核到产生它的源文件。

五张表的源（2026-10-04 核实）：
  表 5/6  experiments/experiment-models/experiment1_metrics_*.json   主仓库
  表 7    六策略原始输出 + gt_recovered.json（**由本脚本复现**，见下）
  表 8    experiments/ablation_original/f1_summary.json               本 worktree
  表 9    experiments/experiment_kappa/Basic/289/analysis/agreement_metrics.json  主仓库

表 7 是五张表里唯一**没有落盘产物**的：它的 F1 与 bootstrap CI 来自一次
`eval_with_gt.py` 的 stdout，而该脚本依赖的 `construction_agent/evaluation/
experiment3/runner.py` 已不在主仓库生产路径（只剩 .pyc，且未进归档；唯一
完整副本在 fix-collectors worktree）。故本脚本内联一份**按索引对齐**的
compute_metrics 复现它。

内联版与被封存版的差别（实测）：runner.py 用 `list.index(pred)` 反查预测
位置，预测列表里存在内容相同的重复条目时（各策略 3–13 条）恒返回首个匹配，
fp/fn 统计错位，**系统性抬高 F1 与 Precision**；正文表 7 用的是对齐版，
改用对齐版后 6 策略的 F1/Precision 有 4 个精确吻合、其余差 ≤0.003。

表 7 的「有效输出率」与「耗时」两列**在现有源里找不到对应字段**（summary
的 elapsed_total_sec 与表中的 Time 不符），按"源未落盘"单列报告，不计入
退出码——它们不可能被自动核验，写在这里是为了不让它们悄悄混进"全绿"。

用法：python3 scripts/check_tables_v2.py [--verbose | --self-test]
退出码 0 = 五表逐格与源一致；1 = 有格不符或源不可达。
"""
import json
import random
import re
import sys
from pathlib import Path

WT = Path(__file__).resolve().parent.parent
PAPER = WT / 'docs' / 'paper_v2'
FILES = {'EN': PAPER / 'eswa_paper_v2.md', 'ZH': PAPER / 'eswa_paper_v2_zh.md'}

MAIN = Path('/home/suntianhao/construction_agent')
# ⚠ 六策略原始输出只在 fix-collectors worktree，主仓库与归档都没有
FC = MAIN / '.claude' / 'worktrees' / 'fix-collectors'

SRC = {
    'T5T6': MAIN / 'experiments' / 'experiment-models'
            / 'experiment1_metrics_20260626_102135.json',
    'T7_gt': WT / 'experiments' / 'ablation_gatekeeping' / 'gt_recovered.json',
    'T7_base': FC / 'experiments' / 'experiment_6strategies',
    'T8': WT / 'experiments' / 'ablation_original' / 'f1_summary.json',
    'T9': MAIN / 'experiments' / 'experiment_kappa' / 'Basic' / '289'
          / 'analysis' / 'agreement_metrics.json',
}

# 表 7 的复现容差：正文 3 位小数，复现差别可达 0.003
TOL_T7 = 0.005
# 其余表的容差：按正文展示位数精确比对
TOL_EXACT = 1e-9

MODELS = [('DeepSeek V4', 'deepseek_v4'), ('Qwen 3.7', 'qwen3'),
          ('GLM 5.2', 'glm52'), ('Kimi K2.6', 'kimi')]


# ── markdown 表格解析 ───────────────────────────────────────────────────
def parse_table(text, header_probe):
    """取以 header_probe 开头的表，返回 [[cell, ...], ...]（不含表头与分隔行）。"""
    lines = text.split('\n')
    i = next((k for k, l in enumerate(lines)
              if l.strip().startswith('|') and header_probe in l), None)
    if i is None:
        return []
    rows = []
    for l in lines[i:]:
        s = l.strip()
        if not s.startswith('|'):
            break
        rows.append([c.strip() for c in s.strip('|').split('|')])
    body = rows[1:] if rows else []          # 去掉表头
    # 去掉分隔行（|---|---:|）——否则会被当成数据行解析出 '-'
    return [r for r in body
            if not (r and all(re.fullmatch(r':?-+:?', c) for c in r))]


def nums(cell):
    """从单元格抽出全部数字（处理 **粗体**、千分位、%，以及 U+2212 减号）。"""
    s = cell.replace('**', '').replace(',', '')
    return [float(x.replace('−', '-')) for x in re.findall(r'[-−]?\d+(?:\.\d+)?', s)]


def first_num(cell, default=None):
    v = nums(cell)
    return v[0] if v else default


def cmp_val(got, exp, tol=TOL_EXACT):
    return abs(got - exp) <= tol


# ── 表 5：四模型基础字段准确率 ──────────────────────────────────────────
T5_ROWS = {'Accident type': '事故类型', 'Severity': '事故等级', 'Deaths': '死亡人数',
           'Injuries': '受伤人数', 'Province': '省份', 'City': '城市',
           'Overall': '总体',
           # 中文版行名（键即归一后的中文名）
           '事故类型': '事故类型', '事故等级': '事故等级', '死亡人数': '死亡人数',
           '受伤人数': '受伤人数', '省份': '省份', '城市': '城市', '总体': '总体'}
T5_FIELDS = {'事故类型': 'accident_type', '事故等级': 'severity',
             '死亡人数': 'deaths', '受伤人数': 'injuries',
             '省份': 'location', '城市': 'city'}


def spec_t5(src):
    out = {}
    for disp, key in MODELS:
        bf = src[key]['basic_fields']
        for zh, fld in T5_FIELDS.items():
            out[(zh, disp)] = bf[fld]['accuracy']
        out[('总体', disp)] = bf['avg_accuracy']
    return out


# ── 表 6：维度级 P/R/F1（HFACS 源字段 → CAICS 层名）────────────────────
T6_LAYERS = {'管理': 'management_factors', '防线': 'machine_factors',
             '触发': 'environmental_factors', '人的行为': 'human_factors'}
T6_ROWS = {'Management': '管理', 'Protection': '防线', 'Trigger': '触发',
           'Human': '人的行为', 'Average': '平均',
           # 中文版行名
           '管理': '管理', '防线': '防线', '触发': '触发',
           '人的行为': '人的行为', '平均': '平均'}


def spec_t6(src):
    out = {}
    for disp, key in MODELS:
        cd = src[key]['cause_dim_f1']
        for zh, fld in T6_LAYERS.items():
            d = cd[fld]
            for m, k in [('P', 'precision'), ('R', 'recall'), ('F1', 'f1')]:
                out[(zh, disp, m)] = d[k]
        a = cd['avg']
        for m, k in [('P', 'precision'), ('R', 'recall'), ('F1', 'f1')]:
            out[('平均', disp, m)] = a[k]
    return out


def got_t6(text, probe):
    got = {}
    for row in parse_table(text, probe):
        if len(row) < 3:
            continue
        zh = T6_ROWS.get(row[0].replace('**', '').strip())
        if zh is None:
            continue
        for ci, (disp, _) in enumerate(MODELS, start=1):
            v = nums(row[ci]) if ci < len(row) else []
            if len(v) >= 3:
                for m, x in zip(('P', 'R', 'F1'), v[:3]):
                    got[(zh, disp, m)] = x
    return got


# ── 表 9：三专家协议指标 ────────────────────────────────────────────────
T9_ROWS = {'Accident type': 'accident_type', 'Severity': 'severity',
           'Province': 'province', 'Deaths': 'deaths', 'Injuries': 'injuries',
           'Date (year-month)': 'date', '事故类型': 'accident_type',
           '事故等级': 'severity', '省份': 'province', '死亡人数': 'deaths',
           '受伤人数': 'injuries', '日期（年-月）': 'date'}


def spec_t9(src):
    out = {}
    for k, d in src.items():
        if k == 'meta' or not isinstance(d, dict):
            continue
        out[(k, 'value')] = d['value']
        if 'ci_lo' in d:
            out[(k, 'ci_lo')] = d['ci_lo']
            out[(k, 'ci_hi')] = d['ci_hi']
        if 'agreement_pct' in d:
            out[(k, 'pct')] = d['agreement_pct']
        if 'mae' in d:
            out[(k, 'mae')] = d['mae']
    return out


def got_t9(text, probe):
    got = {}
    for row in parse_table(text, probe):
        if len(row) < 4:
            continue
        k = T9_ROWS.get(row[0].replace('**', '').strip())
        if k is None:
            continue
        v = nums(row[2])
        if v:
            got[(k, 'value')] = v[0]
        if len(v) >= 3:
            got[(k, 'ci_lo')], got[(k, 'ci_hi')] = v[1], v[2]
        tail = nums(row[3])
        if tail:
            got[(k, 'mae' if 'MAE' in row[3] or '平均绝对误差' in row[3]
                 else 'pct')] = tail[-1]
    return got


# ── 表 8：2×2 消融 + 两行差值 ───────────────────────────────────────────
T8_KEYS = ['v1', 'v2a', 'v2b', 'v2']
T8_COLS = [('F1', 'F1'), ('Recall', 'R'), ('Precision', 'P'),
           ('Empty output', 'empty'), ('M1 recall', 'M1R'),
           ('M3 recall', 'M3R'), ('H3 recall', 'H3R')]
T8_ROWNAMES = {'v1': 'v1', 'v2a': 'v2a', 'v2b': 'v2b', 'v2': 'v2'}


def spec_t8(src):
    """返回 {('cell', 'v1', 'F1'): 0.503, ...}；差值行由 spec 另算。

    源是未舍入的浮点（0.503377…），正文表格按 3 位小数呈现，故此处对齐到
    表格精度再比——否则会报出 42 格"不符"而全部只是舍入差。
    """
    def fmt(fld, x):
        return round(x) if fld == 'empty' else round(x, 3)

    out = {}
    for k in T8_KEYS:
        for disp, fld in T8_COLS:
            out[('cell', k, disp)] = fmt(fld, src[k][fld])
    for name, a, b in [('Δ(phrasing)', 'v2a', 'v1'), ('Δ(policy)', 'v2b', 'v1')]:
        for disp, fld in T8_COLS:
            out[('delta', name, disp)] = fmt(fld, src[a][fld] - src[b][fld])
    return out


def got_t8(text, probe):
    got = {}
    for row in parse_table(text, probe):
        if len(row) < 10:
            continue
        raw = row[1].replace('**', '').strip()
        head = row[0].replace('**', '').strip()
        m = re.match(r'Δ\((.+?)\)\s*=\s*(v\w+)\s*−\s*(v\w+)', raw)
        if m:
            name = 'Δ(phrasing)' if '措辞' in m.group(1) or 'phrasing' in m.group(1) \
                else 'Δ(policy)'
            key = ('delta', name)
        elif head in T8_ROWNAMES:
            key = ('cell', head)
        else:
            continue
        for ci, (disp, _) in enumerate(T8_COLS, start=3):
            if ci < len(row):
                v = first_num(row[ci])
                if v is not None:
                    got[(*key, disp)] = v
    return got


# ── 表 7：六策略（复现）────────────────────────────────────────────────
def metrics_correct(preds, gts, min_gt=5):
    """按索引对齐的多标签 P/R/F1 + Ave+。compute_metrics 的对齐修正版。"""
    classes = sorted({c for g in gts for c in (g.get('codes') or [])}
                     | {c for p in preds for c in (p.get('codes') or [])})
    per = {}
    for cls in classes:
        tp = fp = fn = n_gt = 0
        for i in range(max(len(preds), len(gts))):
            g = (gts[i].get('codes') or []) if i < len(gts) else []
            p = (preds[i].get('codes') or []) if i < len(preds) else []
            in_g, in_p = cls in g, cls in p
            n_gt += in_g
            if in_g and in_p:
                tp += 1
            elif in_p and not in_g:
                fp += 1
            elif in_g and not in_p:
                fn += 1
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        per[cls] = {'P': pr, 'R': rc, 'F1': 2 * pr * rc / (pr + rc) if pr + rc else 0.0,
                    'count': n_gt}
    sel = [v for v in per.values() if v['count'] >= min_gt]
    avg = {m: sum(v[m] for v in sel) / len(sel) for m in ('P', 'R', 'F1')}
    type_ok = sum(1 for i, g in enumerate(gts)
                  if i < len(preds) and preds[i].get('accident_type') == g.get('accident_type'))
    sev_ok = sum(1 for i, g in enumerate(gts)
                 if i < len(preds) and preds[i].get('severity') == g.get('severity'))
    avg['type'] = type_ok / len(gts)
    avg['sev'] = sev_ok / len(gts)
    return avg


def load_strategy(base, i):
    d = json.loads((base / f'S{i}_result' / f'S{i}_results.json').read_text(encoding='utf-8'))
    out = []
    for r in sorted(d['results'], key=lambda x: x.get('index', 0))[:100]:
        p = r.get('parsed') if isinstance(r.get('parsed'), dict) else r
        out.append({'codes': p.get('codes') or r.get('codes') or [],
                    'accident_type': p.get('accident_type') or r.get('accident_type') or '',
                    'severity': p.get('severity') or r.get('severity') or ''})
    return out


def spec_t7():
    """复现六策略指标（含 bootstrap CI）。返回 {('S0','F1'): ..., ('S0','F1_lo'): ...}。"""
    gt = json.loads(SRC['T7_gt'].read_text(encoding='utf-8'))
    gts = [gt[str(i)] for i in range(100)]
    preds = {i: load_strategy(SRC['T7_base'], i) for i in range(6)}
    out = {}
    random.seed(42)
    for i in range(6):
        m = metrics_correct(preds[i], gts)
        out[(f'S{i}', 'F1')] = m['F1']
        out[(f'S{i}', 'R')] = m['R']
        out[(f'S{i}', 'P')] = m['P']
        out[(f'S{i}', 'type')] = m['type']
        out[(f'S{i}', 'sev')] = m['sev']
        f1s = []
        for _ in range(1000):
            idx = [random.randint(0, 99) for _ in range(100)]
            f1s.append(metrics_correct([preds[i][j] for j in idx],
                                       [gts[j] for j in idx])['F1'])
        f1s.sort()
        out[(f'S{i}', 'F1_lo')], out[(f'S{i}', 'F1_hi')] = f1s[25], f1s[974]
    return out


def got_t7(text, probe):
    got = {}
    for row in parse_table(text, probe):
        if len(row) < 7:
            continue
        m = re.match(r'\**(S\d)', row[0].replace('**', '').strip())
        if not m:
            continue
        s = m.group(1)
        v = nums(row[1])
        if len(v) >= 3:
            got[(s, 'F1')], got[(s, 'F1_lo')], got[(s, 'F1_hi')] = v[0], v[1], v[2]
        for ci, k in [(2, 'R'), (3, 'P'), (4, 'type'), (5, 'sev')]:
            if ci < len(row):
                x = first_num(row[ci])
                if x is not None:
                    got[(s, k)] = x
    return got


# ── 对账驱动 ────────────────────────────────────────────────────────────
def diff(exp_spec, got_spec, label, lang, tol, out):
    """逐项比对，返回 (比对格数, 不符列表)。缺格也算不符。"""
    missing, mismatch = [], []
    for key, e in exp_spec.items():
        if key not in got_spec:
            missing.append(key)
        elif not cmp_val(got_spec[key], e, tol):
            mismatch.append((key, got_spec[key], e))
    extra = [k for k in got_spec if k not in exp_spec]
    out.append((label, lang, len(exp_spec), missing, mismatch, extra))
    return len(exp_spec), len(missing) + len(mismatch)


def main(verbose=False):
    texts = {t: p.read_text(encoding='utf-8') for t, p in FILES.items()}
    # 源可达性
    miss_src = []
    for name in ('T5T6', 'T7_gt', 'T8', 'T9'):
        if not Path(SRC[name]).exists():
            miss_src.append((name, SRC[name]))
    if not Path(SRC['T7_base']).exists():
        miss_src.append(('T7_base', SRC['T7_base']))

    print('A. 源可达性')
    print('-' * 70)
    for name, p in SRC.items():
        ok = Path(p).exists()
        print(f'  {"✅" if ok else "❌"} {name:<9} {p}')

    results = []
    if Path(SRC['T5T6']).exists():
        src = json.loads(SRC['T5T6'].read_text(encoding='utf-8'))
        spec5 = spec_t5(src)
        for t, txt in texts.items():
            # ⚠ 表 3 的表头也是 "| Field |" / "| 字段 |"，只写到这里会抓到表 3
            probe = '| Field | DeepSeek' if t == 'EN' else '| 字段 | DeepSeek'
            got = {}
            for row in parse_table(txt, probe):
                if len(row) < 5:
                    continue
                zh = T5_ROWS.get(row[0].replace('**', '').strip())
                if zh is None:
                    continue
                for ci, (disp, _) in enumerate(MODELS, start=1):
                    if ci < len(row):
                        x = first_num(row[ci])
                        if x is not None:
                            got[(zh, disp)] = x
            diff(spec5, got, '表5 四模型基础字段', t, TOL_EXACT, results)
        # 表 6
        spec6 = spec_t6(src)
        for t, txt in texts.items():
            # 同理：表 4 的表头是 "| Dimension | Value |"
            probe = '| Dimension | DeepSeek' if t == 'EN' else '| 维度 | DeepSeek'
            diff(spec6, got_t6(txt, probe), '表6 维度级 P/R/F1', t, TOL_EXACT, results)

    if Path(SRC['T9']).exists():
        src9 = json.loads(SRC['T9'].read_text(encoding='utf-8'))
        spec9 = spec_t9(src9)
        for t, txt in texts.items():
            probe = '| Feature |' if t == 'EN' else '| 特征 |'
            diff(spec9, got_t9(txt, probe), '表9 三专家协议', t, TOL_EXACT, results)

    if Path(SRC['T8']).exists():
        src8 = json.loads(SRC['T8'].read_text(encoding='utf-8'))
        spec8 = spec_t8(src8)
        for t, txt in texts.items():
            probe = '| Cell |' if t == 'EN' else '| 单元 |'
            diff(spec8, got_t8(txt, probe), '表8 2×2 消融', t, TOL_EXACT, results)

    if Path(SRC['T7_base']).exists() and Path(SRC['T7_gt']).exists():
        spec7 = spec_t7()
        for t, txt in texts.items():
            probe = '| Strategy |' if t == 'EN' else '| 策略 |'
            diff(spec7, got_t7(txt, probe), '表7 六策略（复现）', t, TOL_T7, results)

    print('\nB. 逐表逐格对账')
    print('-' * 70)
    bad = 0
    for label, lang, n, missing, mismatch, extra in results:
        issues = len(missing) + len(mismatch)
        bad += issues
        print(f'  {"✅" if not issues else "❌"} [{lang}] {label}：{n} 格，'
              f'不符 {issues}')
        for k in missing[:8]:
            print(f'      ✗ 正文缺 {k}')
        for k, g, e in mismatch[:8]:
            print(f'      ✗ {k}：正文 {g} ≠ 源 {e:.6g}')
        if verbose and extra:
            print(f'      （正文多出 {len(extra)} 格，未在源中找到）')

    print('\nC. 表中无源可核的数字（源未落盘，脚本只提醒）')
    print('-' * 70)
    print('  表 7 有效输出率 / 耗时 两列：现有源（S*_results.json 的 summary）'
          '只有 elapsed_total_sec 与 success，')
    print('  与表中值不符，故无法自动核验；如需核验须补留产生该表的运行记录。')

    print('\n' + '=' * 70)
    if miss_src:
        print(f'❌ {len(miss_src)} 个源不可达：'
              + '、'.join(f'{n}' for n, _ in miss_src))
        return 1
    if bad:
        print(f'❌ {bad} 格与源不符——表格数字不得脱离源数据')
        return 1
    print('✅ 五张表逐格与源数据一致，可接进改稿流程')
    return 0


# ── 自测：验证比较器真的能报错 ──────────────────────────────────────────
def self_test():
    """构造正反用例：一致的必须过、故意改错的必须抓到。"""
    cases = [
        ('一致', [('表X', 'EN', 3, [], [], [])], 0),
        ('缺格', [('表X', 'EN', 3, [('a', 'b')], [], [])], 1),
        ('数值不符', [('表X', 'EN', 3, [], [('a', 0.60, 0.66)], [])], 1),
    ]
    fails = 0
    for name, res, expect_bad in cases:
        n = sum(len(m) + len(mm) for *_, m, mm, _ in res)
        ok = (n > 0) == (expect_bad > 0)
        fails += not ok
        print(f'{"✅" if ok else "❌"} {name}: 检出 {n}，期望{"有" if expect_bad else "无"}问题')

    # 数值比较器本身：精确口径下 0.5033 应算不符，表 7 容差下应算相符
    checks = [
        (0.503, 0.5033772153379138, TOL_EXACT, False),
        (0.503, 0.5033772153379138, TOL_T7, True),
        (0.600, 0.603, TOL_EXACT, False),
        (0.600, 0.603, TOL_T7, True),    # 差 0.003 < 0.005，表 7 容差内
        (0.600, 0.610, TOL_T7, False),   # 差 0.010 超出表 7 容差
    ]
    for got, exp, tol, want in checks:
        ok = cmp_val(got, exp, tol) == want
        fails += not ok
        print(f'{"✅" if ok else "❌"} cmp({got}, {exp}, tol={tol}) '
              f'→ {cmp_val(got, exp, tol)}，期望 {want}')

    # 表格解析：真表能被切出行
    txt = ('| Field | A | B |\n|---|---|---|\n| x | 1 | 2 |\n\n后面不是表\n')
    rows = parse_table(txt, '| Field |')
    ok = rows == [['x', '1', '2']]
    fails += not ok
    print(f'{"✅" if ok else "❌"} parse_table 切表 → {rows}')

    print('\n' + '=' * 70)
    if fails:
        print(f'❌ {fails} 条自测失败——脚本判定逻辑有问题')
        return 1
    print('✅ 自测全过：比较器能报错、解析器能切表')
    return 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    sys.exit(main('--verbose' in sys.argv))
