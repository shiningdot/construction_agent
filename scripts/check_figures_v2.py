#!/usr/bin/env python3
"""图内数值 ↔ 源数据对账（第八类核验）。

核的是**成品 SVG 里画着的数字**（不是生成脚本的期望值），以及 SVG 里
提不到、只能核生成脚本字面量的部分（图 6 的专家阴影带是矩形，无文字标注）。

动因：图 3 曾把分布数字停在孤儿外键清理前的 17,364，说明「图与源脱节」
在本项目真实发生过；而图 5 / 图 6 的数值至今仍是**手抄进
gen_paper_figures.py 的**（图 3 已改为从主库现读，这两张没有），
源一变图不会跟。本脚本让脱节立刻可见。

用法：python3 scripts/check_figures_v2.py [--verbose | --self-test]
"""
import io
import json
import re
import sqlite3
import sys
from pathlib import Path

WT = Path(__file__).resolve().parent.parent
MAIN = Path('/home/suntianhao/construction_agent')
FIG = WT / 'docs' / 'paper_v2' / 'figures'
GEN = WT / 'scripts' / 'gen_paper_figures.py'
DB = MAIN / 'data' / 'caiems_v2.db'

T9 = (MAIN / 'experiments' / 'experiment_kappa' / 'Basic' / '289' /
      'analysis' / 'agreement_metrics.json')
EXPERT = (MAIN / 'experiments' / 'experiment_kappa' / 'kappa_old' /
          'expert_inter_rater.json')
F1SUM = WT / 'experiments' / 'ablation_original' / 'f1_summary.json'
GATE = WT / 'experiments' / 'ablation_gatekeeping' / 'summary.json'
FOURWAY = MAIN / 'experiments' / 'four_way_comparison.md'
EXP1 = (MAIN / 'experiments' / 'experiment-models' /
        'experiment1_metrics_20260626_102135.json')

TOL = 6e-4          # 图里保留 3–4 位小数，比到源的第 4 位


# ── SVG 文本提取 ────────────────────────────────────────────────────────
def svg_text(name):
    p = FIG / name
    if not p.exists():
        return None
    raw = p.read_text(encoding='utf-8')
    out = []
    for x in re.findall(r'<text[^>]*>(.*?)</text>', raw, re.S):
        s = re.sub(r'<[^>]+>', '', x).strip()
        if s:
            out.append(s)
    return out


def nums_in(texts, pat=r'[\d,]+'):
    return [float(x.replace(',', ''))
            for t in texts for x in re.findall(pat, t)]


def near(a, b, tol=TOL):
    return a is not None and b is not None and abs(a - b) <= tol


def multi_sub(got, exp, tol=TOL):
    """多重集包含：源的每个期望值都要能在图里找到（坐标刻度等多余值忽略）。"""
    rest = list(got)
    miss = []
    for e in exp:
        for i, g in enumerate(rest):
            if near(g, e, tol):
                rest.pop(i)
                break
        else:
            miss.append(e)
    return miss


def gen_literals(name):
    """从生成脚本里取某函数体内的列表字面量 {变量名: [值…]}。"""
    src = GEN.read_text(encoding='utf-8')
    m = re.search(rf'^def {name}\(.*?$(.*?)(?=^def |\Z)', src, re.S | re.M)
    if not m:
        return {}
    out = {}
    for vm in re.finditer(r'^\s*(\w+)\s*=\s*\[([^\[\]]*)\]', m.group(1), re.M):
        vals = []
        for x in vm.group(2).split(','):
            x = x.strip()
            if not x:
                continue
            vals.append(float(x) if re.fullmatch(r'-?\d+(\.\d+)?', x) else None)
        out[vm.group(1)] = vals
    return out


# ── 各图核验 ────────────────────────────────────────────────────────────
def check_fig1(conn, out):
    texts = svg_text('fig1_architecture.svg')
    if texts is None:
        return
    line = next((t for t in texts if 'records' in t), None)
    n = nums_in([line]) if line else []
    # 汇总行顺序：2,623 records | 16,323 causal codes | 226 images | 282 VLM analyses
    # 第 3 个（226 images）无源可核，见 C 段；其余三个核主库
    want = [('records', 0, one(conn, 'SELECT COUNT(*) FROM accidents')[0]),
            ('causal codes', 1, one(conn, 'SELECT COUNT(*) FROM caics_causes')[0]),
            ('VLM analyses', 3, one(conn, 'SELECT COUNT(*) FROM image_analyses')[0])]
    bad = []
    if len(n) != 4:
        bad.append(f'汇总行应含 4 个数（含无源可核的 images），实提 {len(n)} 个')
    for nm, i, w in want:
        g = n[i] if i < len(n) else None
        if not near(g, w):
            bad.append(f'{nm}: 图 {g} ≠ 主库 {w}')
    out.append(('图1 汇总行 3 个数（images 留白）', len(want), bad, []))


def check_fig2(conn, out):
    texts = svg_text('fig2_caics_tree.svg')
    if texts is None:
        return
    got = [int(x) for t in texts for x in re.findall(r'\((\d)\)', t)]
    rows = dict(conn.execute('SELECT substr(code,1,1) l, COUNT(DISTINCT code) '
                             'FROM caics_causes GROUP BY 1'))
    bad, n = [], 0
    for k in ('M', 'F', 'T', 'H', 'C'):
        n += 1
        w = rows.get(k)
        if w not in got:
            bad.append(f'{k} 层类数 {w} 未出现在图里（提得 {got}）')
    out.append(('图2 五层类数', n, bad, []))


def fig3_pairs(texts):
    """位置法：最后一段 code 标签之后的纯数字段即 19 个计数，按序与 code 配对。

    ⚠ 不能用「带千分位逗号」筛：只有 ≥1000 的 7 个标签有逗号，其余 12 个
    三位数与坐标轴刻度（0/500/…/2500）落在同一区间，逗号法会漏 12 个。
    """
    codes = [t for t in texts if re.fullmatch(r'[CFHMT][1-9]', t)]
    if not codes:
        return {}, []
    last = max(i for i, t in enumerate(texts) if re.fullmatch(r'[CFHMT][1-9]', t))
    nums = [float(t.replace(',', '')) for t in texts[last + 1:]
            if re.fullmatch(r'[\d,]+', t)]
    if len(nums) < len(codes):
        return {}, [f'code 标签 {len(codes)} 个但只提到 {len(nums)} 个计数']
    return dict(zip(codes, nums)), []


def check_fig3(conn, out):
    texts = svg_text('fig3_code_dist.svg')
    if texts is None:
        return
    pairs, bad = fig3_pairs(texts)
    src = dict(conn.execute('SELECT code, COUNT(*) FROM caics_causes '
                            'GROUP BY code ORDER BY code'))
    for code, w in sorted(src.items()):
        g = pairs.get(code)
        if g is None:
            bad.append(f'{code} 未在图 3 中配对到数值')
        elif not near(g, w, 0.5):
            bad.append(f'{code}: 图 {g:g} ≠ 主库 {w}')
    extra = set(pairs) - set(src)
    if extra:
        bad.append(f'图里出现主库没有的 code：{sorted(extra)}')
    out.append(('图3 十九类计数（按 code 配对）', len(src), bad, []))


def check_fig4(conn, out):
    texts = svg_text('fig4_sampling.svg')
    if texts is None:
        return
    blob = ' | '.join(texts)
    ns = [float(x) for x in re.findall(r'n\s*=\s*(\d+)', blob)]
    exp1 = json.loads(EXP1.read_text(encoding='utf-8'))
    items = [
        ('full corpus', first_num(blob, r'([\d,]+)\s+records'),
         one(conn, 'SELECT COUNT(*) FROM accidents')[0], '主库 accidents'),
        ('silver standard', first_num(blob, r'([\d,]+)\s+validated'),
         first_num(FOURWAY.read_text(encoding='utf-8'), r'银标准\*\*:\s*(\d+)'),
         'four_way_comparison.md'),
        ('model selection n', ns[0] if len(ns) > 0 else None,
         dig(exp1, 'deepseek_v4', 'cost', 'total'), 'experiment1 cost.total'),
        ('strategy n', ns[1] if len(ns) > 1 else None,
         json.loads(GATE.read_text(encoding='utf-8')).get('n_sample'),
         'ablation_gatekeeping n_sample'),
        ('agreement n', ns[2] if len(ns) > 2 else None,
         json.loads(T9.read_text(encoding='utf-8')).get('meta', {}).get('n'),
         'agreement_metrics meta.n'),
    ]
    bad = []
    for nm, g, w, where in items:
        if not near(g, w, 0.5):
            bad.append(f'{nm}: 图 {g} ≠ {where} {w}')
    out.append(('图4 样本层级 5 个数', len(items), bad, []))


def check_fig5(conn, out):
    texts = svg_text('fig5_gatekeeping.svg')
    if texts is None:
        return
    # 图里 empty 率画成三位（0.210 / 0.000），故 \d\.\d{3} 恰好提全 16 个标签；
    # y 轴刻度是 1 位小数（0.0/0.2/…/1.2），不与之冲突
    got = nums_in(texts, r'\d\.\d{3}')
    d = json.loads(F1SUM.read_text(encoding='utf-8'))
    exp = []
    for cell in ('v1', 'v2a', 'v2b', 'v2'):          # 右面板：F1 与 M1 recall
        exp += [round(d[cell]['F1'], 3), round(d[cell]['M1R'], 3)]
    for cell in ('v1', 'v2'):                        # 左面板：F1/R/M1R/empty
        exp += [round(d[cell]['F1'], 3), round(d[cell]['R'], 3),
                round(d[cell]['M1R'], 3), round(d[cell]['empty'] / 100, 3)]
    bad = [f'{v:.3f} 未出现在图 5' for v in multi_sub(got, exp)]
    # 双保险：生成脚本字面量也须与源一致（图若重生成，脚本是下一版图的输入）
    lit = gen_literals('fig5_gatekeeping')
    for var, cell, expv in (('v1', 'v1', [d['v1']['F1'], d['v1']['R'],
                                          d['v1']['M1R'], d['v1']['empty'] / 100]),
                            ('v2', 'v2', [d['v2']['F1'], d['v2']['R'],
                                          d['v2']['M1R'], d['v2']['empty'] / 100]),
                            ('f1', None, [d[c]['F1'] for c in ('v1', 'v2a', 'v2b', 'v2')]),
                            ('m1r', None, [d[c]['M1R'] for c in ('v1', 'v2a', 'v2b', 'v2')])):
        vals = lit.get(var, [])
        if len(vals) != 4:
            bad.append(f'脚本 {var} 应 4 个值，实为 {vals}')
            continue
        for g, w in zip(vals, expv):
            if not near(g, w):
                bad.append(f'脚本 {var}: {g} ≠ 源 {w:.4f}')
    out.append(('图5 门控 16 个值 + 脚本字面量', len(exp) + 16, bad, []))


def check_fig6(conn, out):
    texts = svg_text('fig6_kappa.svg')
    if texts is None:
        return
    src = json.loads(T9.read_text(encoding='utf-8'))
    order = ['accident_type', 'severity', 'province', 'deaths', 'injuries']
    pat = re.compile(r'^(\d\.\d{4}) \[(\d\.\d{4}), (\d\.\d{4})\]$')
    lines = [t for t in texts if pat.match(t)]
    bad, n = [], 0
    if len(lines) != 5:
        bad.append(f'图 6 应有 5 条「κ [CI 下界, 上界]」，实提 {len(lines)} 条')
    for key, t in zip(order, lines):
        v, lo, hi = map(float, pat.match(t).groups())
        for nm, g, w in (('value', v, dig(src, key, 'value')),
                         ('CI 下界', lo, dig(src, key, 'ci_lo')),
                         ('CI 上界', hi, dig(src, key, 'ci_hi'))):
            n += 1
            if not near(g, w, 1e-4):
                bad.append(f'{key} {nm}: 图 {g} ≠ 源 {w}')
    # 专家阴影带：SVG 里是矩形、无文字标注，只能以生成脚本字面量为代理
    lit = gen_literals('fig6_kappa')
    ex = [v for v in (lit.get('expert_lo', []) + lit.get('expert_hi', []))
          if v is not None]
    pool = walk_nums(json.loads(EXPERT.read_text(encoding='utf-8')))
    n += len(ex)
    for v in ex:
        if not any(near(v, y, 1e-4) for y in pool):
            bad.append(f'专家阴影带 {v} 未在 expert_inter_rater.json 中找到')
    for var, w in (('vals', [dig(src, k, 'value') for k in order]),
                   ('los', [dig(src, k, 'ci_lo') for k in order]),
                   ('his', [dig(src, k, 'ci_hi') for k in order])):
        vals = lit.get(var, [])
        if len(vals) != 5:
            bad.append(f'脚本 {var} 应 5 个值，实为 {vals}')
            continue
        for g, e in zip(vals, w):
            if not near(g, e, 1e-4):
                bad.append(f'脚本 {var}: {g} ≠ 源 {e}')
    out.append(('图6 κ/ICC 15 值 + 专家带 8 值 + 脚本字面量', n + 15, bad, []))


def walk_nums(o, acc=None):
    if acc is None:
        acc = []
    if isinstance(o, dict):
        for v in o.values():
            walk_nums(v, acc)
    elif isinstance(o, list):
        for v in o:
            walk_nums(v, acc)
    elif isinstance(o, (int, float)) and not isinstance(o, bool):
        acc.append(float(o))
    return acc


def dig(o, *keys):
    for k in keys:
        if not isinstance(o, dict):
            return None
        o = o.get(k)
    return o


def first_num(text, pat):
    m = re.search(pat, text)
    return float(m.group(1).replace(',', '')) if m else None


def one(c, sql):
    return c.execute(sql).fetchone()


# ── 驱动 ────────────────────────────────────────────────────────────────
FIGS = ('fig1_architecture.svg', 'fig2_caics_tree.svg', 'fig3_code_dist.svg',
        'fig4_sampling.svg', 'fig5_gatekeeping.svg', 'fig6_kappa.svg')


def main(verbose=False):
    print('A. 图文件与源可达性')
    print('-' * 70)
    miss = []
    for name in FIGS:
        ok = (FIG / name).exists()
        print(f'  {"✅" if ok else "❌"} {name}')
        if not ok:
            miss.append(name)
    for tag, p in (('主库', DB), ('图注源 agreement_metrics', T9),
                   ('专家范围 expert_inter_rater', EXPERT),
                   ('消融 f1_summary', F1SUM), ('门控 summary(ablation_gatekeeping)', GATE),
                   ('银标准 four_way_comparison.md', FOURWAY),
                   ('成本 experiment1', EXP1), ('生成脚本', GEN)):
        ok = Path(p).exists()
        print(f'  {"✅" if ok else "❌"} {tag}')
        if not ok:
            miss.append(tag)

    results = []
    c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True) if DB.exists() else None
    try:
        for fn in (check_fig1, check_fig2, check_fig3, check_fig4,
                   check_fig5, check_fig6):
            try:
                fn(c, results)
            except Exception as e:                  # 单图失败不掩其他图
                results.append((fn.__name__, 0, [f'核验异常：{e!r}'], []))
    finally:
        if c:
            c.close()

    print('\nB. 逐图核验')
    print('-' * 70)
    bad = 0
    for label, n, issues, _ in results:
        bad += len(issues)
        print(f'  {"✅" if not issues else "❌"} {label}：核 {n} 个值，不符 {len(issues)}')
        for x in issues[:8]:
            print(f'      ✗ {x}')

    print('\nC. 图内无源可核的数字（只提醒，不计入退出码）')
    print('-' * 70)
    print('  图 1「226 images」：与表 4 同一个数。CLIP 分数与阈值过滤结果从未落库'
          '（见 17.4），源已不可考。')
    print('  图 2 的示例证据链（四川阆中 2023 模板支撑坍塌）：语料中一条真实记录，'
          '非统计量，无法用聚合查询核。')

    print('\n' + '=' * 70)
    if miss:
        print(f'❌ {len(miss)} 个图/源不可达：' + '、'.join(miss))
        return 1
    if bad:
        print(f'❌ {bad} 个图内数值与源不符——图不得脱离源数据')
        return 1
    print('✅ 六张图内数值与源一致，可接进改稿流程')
    print('  ⚠ 图 5 / 图 6 的值仍手抄在 gen_paper_figures.py 里（图 3 已改为从主库'
          '现读）；')
    print('    本脚本能发现脱节，根治要让这两张图也改为从源现读。')
    return 0


# ── 自测 ────────────────────────────────────────────────────────────────
def self_test():
    fails = 0

    def ck(name, got, want):
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f'{"✅" if ok else "❌"} {name}: {got} 期望 {want}')

    ck('千分位解析', nums_in(['2,208', '0', '500']), [2208.0, 0.0, 500.0])
    ck('三位小数提取', nums_in(['0.210', '0.0', '1.000'], r'\d\.\d{3}'),
       [0.210, 1.0])
    # 多重集：命中不报、缺失必报、重复计数正确
    ck('多重集-全中', multi_sub([0.5, 0.6], [0.5, 0.6]), [])
    ck('多重集-报缺', multi_sub([0.5], [0.5, 0.6]), [0.6])
    ck('多重集-重复', multi_sub([0.5], [0.5, 0.5]), [0.5])
    ck('容差', [near(0.5034, 0.503), near(0.5034, 0.6)], [True, False])

    # fig3 位置法：坐标轴刻度在 code 之前，必须被排除
    t3 = ['0', '500', '1000', 'M1', 'F9', '2,208', '595', 'Distribution of x']
    ck('fig3 位置法', fig3_pairs(t3), ({'M1': 2208.0, 'F9': 595.0}, []))
    # fig3 少一个计数时必须报错，而不是静默配对
    t3b = ['M1', 'F9', '2,208']
    ck('fig3 计数不足能报错', fig3_pairs(t3b)[1] != [], True)

    # fig4 顺序法：三个 n 按文本顺序对应 200/100/289
    blob = ' | '.join(['2,623 records', '972 validated', 'n = 200',
                       'n = 100', 'n = 289'])
    ck('fig4 三个 n 按序', re.findall(r'n\s*=\s*(\d+)', blob),
       ['200', '100', '289'])
    ck('fig4 records/validated',
       [first_num(blob, r'([\d,]+)\s+records'),
        first_num(blob, r'([\d,]+)\s+validated')], [2623.0, 972.0])

    print('\n' + '=' * 70)
    if fails:
        print(f'❌ {fails} 条自测失败——判定逻辑有问题')
        return 1
    print('✅ 自测全过：提取器与多重集比对都能报错')
    return 0


def drift_test():
    """失败路径自证：往成品 SVG 里注入漂移，脚本必须报错。

    复制整份 figures 到临时目录再改，不动成品。这证明「退出码 0」不是因为
    脚本看不见问题，而是因为图确实与源一致——比断言常量强。
    """
    import shutil
    import tempfile
    from contextlib import redirect_stdout

    global FIG
    cases = [
        ('fig1_architecture.svg', '2,623 records', '2,600 records'),
        ('fig2_caics_tree.svg', '(5)', '(6)'),
        ('fig3_code_dist.svg', '2,208', '2,999'),
        ('fig4_sampling.svg', 'n = 200', 'n = 250'),
        ('fig5_gatekeeping.svg', '>0.503<', '>0.500<'),
        ('fig6_kappa.svg', '0.9136 [0.8747, 0.9466]', '0.9000 [0.8747, 0.9466]'),
    ]
    src = FIG
    base = Path(tempfile.mkdtemp(prefix='figdrift_'))
    ok = 0
    try:
        for name, old, new in cases:
            d = base / name
            shutil.copytree(src, d)
            f = d / name
            txt = f.read_text(encoding='utf-8')
            if old not in txt:
                print(f'❓ {name}: 注入串 {old!r} 不在图里，用例失效')
                continue
            f.write_text(txt.replace(old, new, 1), encoding='utf-8')
            FIG = d
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main()
            out = buf.getvalue()
            caught = rc == 1 and '❌' in out
            ok += caught
            line = next((l.strip() for l in out.splitlines()
                         if l.strip().startswith('✗')), '')
            print(f'{"✅" if caught else "❌"} {name} 注入 {old!r}→{new!r}：'
                  f'退出码 {rc}' + (f'，报出 {line}' if caught else ' —— 没报出来！'))
    finally:
        FIG = src
        shutil.rmtree(base, ignore_errors=True)
    print('\n' + '=' * 70)
    if ok != len(cases):
        print(f'❌ 失败路径 {ok}/{len(cases)}——有图漂移抓不住')
        return 1
    print(f'✅ 失败路径 {ok}/{len(cases)}：任何一张图偏离源都会被抓住')
    return 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    if '--drift-test' in sys.argv:
        sys.exit(drift_test())
    sys.exit(main('--verbose' in sys.argv))
