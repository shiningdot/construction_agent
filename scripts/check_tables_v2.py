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

表 4 的「CLIP-filtered images 226」原先与表 7 那两列同待遇（"源未落盘"），
2026-10-05 查明**源在文件系统而非库里**：CLIP 过滤的产物落在 imageV3/
（`pipeline_image_v2.py` 的管线串「… → CLIP过滤(>0.5) → imageV3/」），
故该行改为读目录文件数（`clip_images()`）。主库 `image_files` 只登记 211 条
imageV3 记录、比磁盘少 15，**拿库去核会核出一个 15 的假差**——这是登记滞后
的数据问题，不影响论文数字（论文的 226 指 CLIP 通过后落盘的图片数）。

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


# ── 表 1 / 表 3 / 表 4：主库侧（源 = data/caiems_v2.db 只读）────────────
DB = MAIN / 'data' / 'caiems_v2.db'
# CLIP 过滤的产地在文件系统、不在库里：pipeline_image_v2.py 的管线串
# 「Step1提取 → 搜索增强 → 图片采集 → CLIP过滤(>0.5) → imageV3/」把
# clip_filter.py 判为正类「施工现场事故现场照片」softmax 概率 > 0.5 的图片
# 落进 imageV3/。故论文表 4 的「226 images above the 0.5 similarity threshold」
# 的源就是**这个目录的文件数**。
IMGV3 = MAIN / 'data' / 'imageV3'


def clip_images():
    """CLIP 过滤后保留的图片数（= imageV3/ 下的文件数）。

    与 check_figures_v2.py 的图 1 核验同源（论文里是同一个数）。
    **主库不是这个数的源**：image_files 只登记 211 条 imageV3 记录，比磁盘
    少 15（登记滞后，见 C 段），拿库去核会核出一个 15 的假差。
    """
    if not IMGV3.exists():
        return None
    return sum(1 for f in IMGV3.rglob('*') if f.is_file())


def conn():
    import sqlite3
    return sqlite3.connect(f'file:{DB}?mode=ro', uri=True)


def one(c, sql):
    return c.execute(sql).fetchone()


# 表 1：来源轨道的判据
T1_SRC = [
    ('safehoo', lambda s: s == 'safehoo'),
    ('esafety', lambda s: s == 'esafety'),
    ('provincial', lambda s: (s or '').endswith('_em')),
    ('mem', lambda s: s == 'mem'),
    ('fanxiren', lambda s: '樊锡仁' in (s or '')),
    ('mohurd', lambda s: '住建部' in (s or '')),
]


def spec_t1(c):
    rows = c.execute('SELECT source_name, COUNT(*) FROM accidents GROUP BY 1').fetchall()
    n_all = one(c, 'SELECT COUNT(*) FROM accidents')[0]
    out = {}
    for key, pred in T1_SRC:
        n = sum(v for s, v in rows if pred(s))
        out[('rec', key)] = n
        out[('share', key)] = round(n / n_all * 100, 1)
    web = sum(out[('rec', k)] for k in ('safehoo', 'esafety', 'provincial', 'mem'))
    doc = sum(out[('rec', k)] for k in ('fanxiren', 'mohurd'))
    for nm, v in [('sub_web', web), ('sub_doc', doc), ('total', n_all)]:
        out[('rec', nm)] = v
        out[('share', nm)] = round(v / n_all * 100, 1)
    return out


def got_t1(text, probe):
    got, subs = {}, []
    for row in parse_table(text, probe):
        if len(row) < 4:
            continue
        desc = ' '.join(row[:2])
        low = desc.lower()
        rec, share = first_num(row[2]), first_num(row[3])
        if rec is None:
            continue
        key = None
        for k, pats in [('safehoo', ('safehoo',)), ('esafety', ('esafety',)),
                        ('provincial', ('provincial', '省级')),
                        ('mem', ('ministry of emergency', '应急管理部')),
                        ('fanxiren', ('fan xiren', '樊锡仁')),
                        ('mohurd', ('mohurd', '住建部'))]:
            if any(p in low for p in pats):
                key = k
                break
        if key is None:
            if 'subtotal' in low or '小计' in low:
                subs.append((rec, share))
            elif 'total' in low or '合计' in low:
                key = 'total'
            else:
                continue
        if key == 'total':
            got[('rec', 'total')], got[('share', 'total')] = rec, share
        elif key:
            got[('rec', key)] = rec
            if share is not None:
                got[('share', key)] = share
    for nm, (rec, share) in zip(('sub_web', 'sub_doc'), subs):
        got[('rec', nm)] = rec
        if share is not None:
            got[('share', nm)] = share
    return got


# 表 3：五个枚举字段
T3_FIELDS = [('工序', 'work_task'), ('涉事设备', 'equipment_involved'),
             ('涉事材料', 'material_involved'), ('工种', 'worker_type'),
             ('建筑结构', 'building_structure')]
# 英文行首 → 与 T3_FIELDS 同一套 key（从 T3_FIELDS 派生，避免两处改名脱节）
T3_ROW_ALIAS = dict(zip(
    ['Work task', 'Equipment involved', 'Material involved',
     'Worker type', 'Building structure'],
    [z for z, _ in T3_FIELDS]))


def spec_t3(c):
    n_all = one(c, 'SELECT COUNT(*) FROM accidents')[0]
    out = {}
    for zh, col in T3_FIELDS:
        nn = one(c, f'SELECT COUNT(*) FROM accidents WHERE {col} IS NOT NULL '
                   f'AND {col}!=""')[0]
        vals = c.execute(f'SELECT {col}, COUNT(*) n FROM accidents WHERE {col} IS '
                         f'NOT NULL AND {col}!="" GROUP BY 1 ORDER BY n DESC').fetchall()
        out[(zh, 'cats')] = len(vals)
        out[(zh, 'nonnull')] = nn
        out[(zh, 'nonnull_pct')] = round(nn / n_all * 100, 1)
        top, topn = vals[0]
        out[(zh, 'top_n')] = topn
        out[(zh, 'top_pct')] = round(topn / nn * 100, 1)
        others = c.execute(
            f"SELECT code FROM enum_definitions WHERE enum_type='{col}' "
            f"AND is_active=1 AND name LIKE '%其他%'").fetchall()
        if others:
            o = sum(n for v, n in vals if v in {x[0] for x in others})
            out[(zh, 'other_pct')] = round(o / nn * 100, 1)
    return out


def got_t3(text, probe):
    got = {}
    for row in parse_table(text, probe):
        if len(row) < 5:
            continue
        raw = row[0].replace('**', '').strip()
        # 表 3 行首是「Work task (WT)」「工序（WT）」——按前缀认，精确键匹配不到
        zh = next((z for en, z in T3_ROW_ALIAS.items() if raw.startswith(en)), None)
        if zh is None:
            zh = next((z for z, _ in T3_FIELDS if raw.startswith(z)), None)
        if zh is None:
            continue
        v1 = first_num(row[1])
        if v1 is not None:
            got[(zh, 'cats')] = v1
        v2 = nums(row[2])
        if len(v2) >= 2:
            got[(zh, 'nonnull')], got[(zh, 'nonnull_pct')] = v2[0], v2[1]
        elif v2:
            got[(zh, 'nonnull')] = v2[0]
        # ⚠ 不能对整格取 nums：类名 'WT02' 会被当成数字 2。只取括号内的两个数
        # ⚠ 中文版括号内用全角逗号「（397，18.0%）」，正则须一并接受
        m = re.search(r'[(（]\s*([\d,，]+)\s*[,，]\s*([\d.]+)\s*%', row[3])
        if m:
            got[(zh, 'top_n')] = float(m.group(1).replace(',', '').replace('，', ''))
            got[(zh, 'top_pct')] = float(m.group(2))
        v4 = first_num(row[4])
        if v4 is not None:
            got[(zh, 'other_pct')] = v4
    return got


# 表 4：概览（逐行核数字集合）
T4_ROWS = [
    ('records', 'Records', '事故记录'),
    ('types_n', 'Accident types', '事故类型'),
    ('severity', 'Severity', '事故等级'),
    ('casualties', 'Casualties', '伤亡'),
    ('causes', 'Causal annotations', '致因标注'),
    ('search', 'Search-enriched', '搜索增强'),
    ('vlm', 'VLM analyses', 'VLM 分析'),
    ('clip', 'CLIP-filtered', 'CLIP 过滤'),
]


def spec_t4(c):
    out = {}
    out[('records', 'v')] = [one(c, 'SELECT COUNT(*) FROM accidents')[0]]
    out[('types_n', 'v')] = [
        one(c, 'SELECT COUNT(DISTINCT accident_type) FROM accidents '
               'WHERE accident_type!=""')[0],
        one(c, 'SELECT COUNT(*) FROM accidents WHERE accident_type!=""')[0]]
    out[('severity', 'v')] = [n for _, n in c.execute(
        'SELECT severity, COUNT(*) FROM accidents GROUP BY 1 ORDER BY 2 DESC')]
    d, i = one(c, 'SELECT SUM(deaths), SUM(injuries) FROM accidents')
    out[('casualties', 'v')] = [d, i]
    out[('causes', 'v')] = [one(c, 'SELECT COUNT(*) FROM caics_causes')[0]] + [
        n for _, n in c.execute('SELECT evidence_quality, COUNT(*) FROM caics_causes '
                                'GROUP BY 1 ORDER BY 2 DESC')]
    out[('search', 'v')] = [one(c, 'SELECT COUNT(*) FROM accidents '
                                'WHERE search_performed=1')[0]]
    out[('vlm', 'v')] = [one(c, 'SELECT COUNT(*) FROM image_analyses')[0],
                         one(c, 'SELECT COUNT(DISTINCT accident_id) '
                                'FROM image_analyses')[0]]
    n_clip = clip_images()
    if n_clip is not None:
        out[('clip', 'v')] = [n_clip]
    return out


def got_t4(text, probe):
    got = {}
    for row in parse_table(text, probe):
        if len(row) < 2:
            continue
        label = row[0].replace('**', '').strip()
        v = nums(row[1])
        for key, en, zh in T4_ROWS:
            if label.startswith(en) or label.startswith(zh):
                got[(key, 'v')] = v
    return got


# ── 表 2：CAICS 19 类（三方对账：论文 ↔ 生产提示词 ↔ 主库落库类名）──────
STEP2 = MAIN / 'construction_agent' / 'extraction' / 'step2_caics_encoder.py'
T2_LAYER_RE = re.compile(r'([MFHTC])\s*层')
T2_STOP = set('未无不的了或在被')


def prompt_codes():
    """从生产提示词里抽 19 类 code（源 = step2_caics_encoder.py）。"""
    return set(re.findall(r'\b([CFHMT][1-9])\b', STEP2.read_text(encoding='utf-8')))


def db_codes(c):
    return {r[0] for r in c.execute('SELECT DISTINCT code FROM caics_causes')}


def db_names(c):
    return dict(c.execute('SELECT code, name FROM caics_causes '
                          'GROUP BY code ORDER BY code'))


def got_t2(text, probe):
    """解析表 2。首列只在每层第一行有值，需向下填充。"""
    out, cur = [], None
    for row in parse_table(text, probe):
        if len(row) < 5:
            continue
        first = row[0].replace('**', '').strip()
        if first:
            m = T2_LAYER_RE.match(first) or re.match(r'([MFHTC])\s*[—–-]', first)
            if m:
                cur = m.group(1)
        code = row[1].strip()
        if not re.fullmatch(r'[CFHMT][1-9]', code):
            continue
        out.append({'layer': cur, 'code': code, 'name': row[2].strip(),
                    'kws': [x for x in re.split(r'[；;]', row[4]) if x.strip()]})
    return out


def t2_compare(rows, lang, c, pcodes, dnames):
    """硬项：三方 code 集合、层归属、中文类名。软项：关键词核心概念可溯。"""
    missing, mismatch, n = [], [], 0
    dcs = db_codes(c)
    seen = {r['code'] for r in rows}
    # 1) code 集合三方一致（19 格）
    for code in sorted(dcs | pcodes | seen):
        n += 1
        where = [w for w, s in (('主库', dcs), ('提示词', pcodes), ('表2', seen))
                 if code not in s]
        if where:
            missing.append(f'{code} 不在 {"、".join(where)}')
    # 2) 层归属：行所属层标题的字母须等于 code 首字母（19 格）
    for r in rows:
        n += 1
        if r['layer'] != r['code'][0]:
            mismatch.append(f"{r['code']} 归在 {r['layer']} 层，应按首字母归 "
                            f"{r['code'][0]} 层")
    # 3) 中文类名 ↔ 主库落库类名（仅中文版，19 格）
    if lang == 'ZH':
        for r in rows:
            n += 1
            if r['code'] in dnames and r['name'] != dnames[r['code']]:
                mismatch.append(f"{r['code']} 类名「{r['name']}」≠ 主库"
                                f"「{dnames[r['code']]}」")
    # 4) 关键词核心概念须能在该类证据里找到（仅中文版；防「凭空概念」的弱判据）
    if lang == 'ZH':
        for r in rows:
            nc = one(c, f"SELECT COUNT(*) FROM caics_causes WHERE "
                        f"code='{r['code']}'")[0]
            for kw in r['kws']:
                n += 1
                hit = one(c, "SELECT COUNT(*) FROM caics_causes WHERE "
                             f"code='{r['code']}' AND evidence LIKE '%{kw}%'")[0]
                if hit:
                    continue
                # 整词不中 → 退到 2-gram：概念在不在该类语料里
                best = 0
                for j in range(len(kw) - 1):
                    if kw[j] in T2_STOP or kw[j + 1] in T2_STOP:
                        continue
                    g = kw[j:j + 2]
                    best = max(best, one(
                        c, "SELECT COUNT(*) FROM caics_causes WHERE "
                           f"code='{r['code']}' AND evidence LIKE '%{g}%'")[0])
                if best == 0:
                    mismatch.append(f"{r['code']} 关键词「{kw}」在该类 "
                                    f"{nc} 条证据里无任何片段命中（疑凭空概念）")
    return ('表2 CAICS 定义（三方）', lang, n, missing, mismatch, [])


# ── 对账驱动 ────────────────────────────────────────────────────────────
def t4_compare(spec, got, lang):
    """表 4 每行是多值，故按「源值是否出现在该行数字集合里」判定。"""
    missing, mismatch, n = [], [], 0
    for key, exp in spec.items():
        have = got.get(key, [])
        n += len(exp)
        for v in exp:
            if not any(cmp_val(h, v, TOL_EXACT) for h in have):
                (missing if not have else mismatch).append(f'{key[0]}={v:g}')
    return ('表4 数据库统计', lang, n, missing, mismatch, [])


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
    print(f'  {"✅" if DB.exists() else "❌"} {"DB":<9} {DB}（表 1/3/4）')

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

    # 表 1 / 3 / 4：源 = 主库（只读）
    if not IMGV3.exists():
        # 表 4 的 CLIP 行源在文件系统而非库里，缺目录时须报"源不可达"，
        # 不能因为 spec 里少了这一格就静默降级（那一格会滑进 extra 不报错）
        miss_src.append(('imageV3/（CLIP 图片数的源）', IMGV3))
    if DB.exists():
        c = conn()
        try:
            spec1 = spec_t1(c)
            spec3 = spec_t3(c)
            spec4 = spec_t4(c)
        finally:
            c.close()
        for t, txt in texts.items():
            p1 = '| Track | Source |' if t == 'EN' else '| 轨道 | 来源 |'
            diff(spec1, got_t1(txt, p1), '表1 数据来源分布', t, TOL_EXACT, results)
            p3 = '| Field | Categories |' if t == 'EN' else '| 字段 | 类别数 |'
            diff(spec3, got_t3(txt, p3), '表3 枚举字段统计', t, TOL_EXACT, results)
            p4 = '| Dimension | Value |' if t == 'EN' else '| 维度 | 数值 |'
            results.append(t4_compare(spec4, got_t4(txt, p4), t))
            # 表 2：三方对账（论文 ↔ 生产提示词 ↔ 主库）
            if STEP2.exists():
                p2 = '| Layer | Code |' if t == 'EN' else '| 层 | 码 |'
                c2 = conn()
                try:
                    results.append(t2_compare(got_t2(txt, p2), t, c2,
                                              prompt_codes(), db_names(c2)))
                finally:
                    c2.close()

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

    print('\nC. 表中无源可核的数字（源未落盘或口径不明，脚本只提醒、不计入退出码）')
    print('-' * 70)
    print('  表 7 有效输出率 / 耗时 两列：现有源（S*_results.json 的 summary）'
          '只有 elapsed_total_sec 与 success，')
    print('  与表中值不符，故无法自动核验；如需核验须补留产生该表的运行记录。')
    print('  （2026-10-05 已转入核验，不再属于本段）表 4 「CLIP-filtered images 226」：'
          '源 = 磁盘 imageV3/ 目录的文件数（clip_images()）。')
    print('   原先记「源已不可考」是因为只在主库找——主库 image_files 只登记 211 条'
          ' imageV3 记录、比磁盘少 15（登记滞后），')
    print('   拿库去核会核出一个 15 的假差；CLIP 的产地在文件系统，不在库里。')
    print('  表 1 事故年代列：表中为采集计划口径，与主库 date 的实测 min/max 不一致'
          '（如 safehoo 实为 2008–2026，表中写 2005–2026），口径不明故不自动判负。')
    print('  表 2 关键词列：判据只抓「整词与全部 2-gram 在该类语料里都零命中」的凭空概念，')
    print('    **抓不到用真词拼凑的伪概念**（如「外星人施工」会被高频的「施工」救活）'
          '——弱判据，勿当强核验看。')
    print('    一次性人工放宽核验（2026-10-04）：57 个关键词里 31 个整词零命中，'
          '退到 2-gram 后 27 个的核心概念')
    print('    在该类语料里有支撑（「监理未到场」→「监理」336 条、「未做三级教育」→'
          '「教育」454 条），与该列表注')
    print('    「关键词为语料中中文表述的概括」一致，故属概括表述而非凭空生成。'
          '此结论不可自动复现，仅此一次记录。')

    print('\n' + '=' * 70)
    if miss_src:
        print(f'❌ {len(miss_src)} 个源不可达：'
              + '、'.join(f'{n}' for n, _ in miss_src))
        return 1
    if bad:
        print(f'❌ {bad} 格与源不符——表格数字不得脱离源数据')
        return 1
    print('✅ 九张表逐格与源数据一致，可接进改稿流程（表 10 源在他人已发表报告，'
          '不在本脚本范围）')
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

    # 表 3 解析：英文行首带 (WT)、中文用全角逗号与（），两条都要能取到数
    for lang, head, row, want in [
        ('EN', '| Field | Categories | Non-null | Largest | Residual |',
         '| Work task (WT) | 11 | 2,202 (83.9%) | WT02 main structure works (397, 18.0%) | 2.8% |',
         {('工序', 'cats'): 11, ('工序', 'nonnull'): 2202,
          ('工序', 'top_n'): 397, ('工序', 'top_pct'): 18.0,
          ('工序', 'other_pct'): 2.8}),
        ('ZH', '| 字段 | 类别数 | 非空 | 最大 | 残余 |',
         '| 工序（WT） | 11 | 2,202（83.9%） | WT02 主体结构工程（397，18.0%） | 2.8% |',
         {('工序', 'cats'): 11, ('工序', 'nonnull'): 2202,
          ('工序', 'top_n'): 397, ('工序', 'top_pct'): 18.0,
          ('工序', 'other_pct'): 2.8}),
    ]:
        g = got_t3(f'{head}\n|---|\n{row}\n', '| Field |' if lang == 'EN' else '| 字段 |')
        bad = {k: (g.get(k), v) for k, v in want.items() if g.get(k) != v}
        fails += bool(bad)
        print(f'{"✅" if not bad else "❌"} [{lang}] got_t3 解析表 3 行'
              + (f' → 偏差 {bad}' if bad else ''))

    # 表 2 解析：首列只在每层第一行有值，必须向下填充；中英两条路径都要对
    for lang, head, body, want_codes, want_layers in [
        ('EN', '| Layer | Code | Category | Operational definition | Keywords |',
         '| **M — Management foundation** | M1 | Deficient supervision | d | no supervisor |\n'
         '| | M2 | Hazard inspection failure | d | hazards not rectified |',
         ['M1', 'M2'], ['M', 'M']),
        ('ZH', '| 层 | 码 | 类别 | 操作定义 | 典型证据关键词 |',
         '| **M 层 — 管理根基** | M1 | 现场监管缺失 | d | 无人监督 |\n'
         '| | M2 | 隐患排查失效 | d | 隐患未整改 |',
         ['M1', 'M2'], ['M', 'M']),
    ]:
        got2 = got_t2(f'{head}\n|---|\n{body}\n',
                      '| Layer | Code |' if lang == 'EN' else '| 层 | 码 |')
        ok = ([r['code'] for r in got2] == want_codes
              and [r['layer'] for r in got2] == want_layers)
        fails += not ok
        print(f'{"✅" if ok else "❌"} [{lang}] got_t2 层向下填充 → '
              f'{[(r["code"], r["layer"]) for r in got2]}')

    # 表 4 比对器：源值不在行内数字集合里必须报错
    r = t4_compare({('records', 'v'): [2623]}, {('records', 'v'): [2623, 99]}, 'EN')
    ok = not r[4]
    r2 = t4_compare({('records', 'v'): [2624]}, {('records', 'v'): [2623, 99]}, 'EN')
    ok &= bool(r2[4])
    fails += not ok
    print(f'{"✅" if ok else "❌"} t4_compare：命中不报错、错值必报错')

    # CLIP 行：源在文件系统（main 库不是它的源），读得到且为正整数。
    # 目录缺失时 clip_images() 返回 None，由主流程报"源不可达"——
    # 这不算自测失败（自测不依赖数据盘在不在），故 None 也放行
    n_clip = clip_images()
    ok = n_clip is None or isinstance(n_clip, int) and n_clip > 0
    fails += not ok
    print(f'{"✅" if ok else "❌"} clip_images() 读磁盘 imageV3/ → {n_clip}')

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
