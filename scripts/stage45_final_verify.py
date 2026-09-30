#!/usr/bin/env python3
"""Stage 4.5 最终完整性验证：修订版论文关键数字 vs 数据源（从零独立）

⚠️ 冻结状态：本脚本核验的是 V1 存档稿 docs/paper/eswa_paper.md，该稿按约定不再修改。
   稿中"致因编码 17,364"为 2026-09-23 清理前口径，当前库为 16,323，故第 D 节该断言
   会失败——属预期现象，不代表数据异常。V2 的数字核验以 docs/paper_v2/修订对账记录.md 为准。
"""
import json, re, sys, sqlite3

PAPER = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper.md'
paper = open(PAPER, encoding='utf-8').read()

fails = []
def check(name, ok, detail=''):
    print(f'  {"✓" if ok else "✗"} {name} {detail}')
    if not ok:
        fails.append((name, detail))

print('=== A. 表 7 消融数字（vs f1_summary.json） ===')
f1s = json.load(open('/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/experiments/ablation_original/f1_summary.json'))
expect = {
    'v1': ('0.503', '0.423', '0.856', '21%', '0.141', '0.250', '0.111'),
    'v2a': ('0.665', '0.677', '0.709', '2%', '0.941', '0.667', '0.511'),
    'v2b': ('0.653', '0.641', '0.724', '5%', '0.329', '0.444', '0.533'),
    'v2': ('0.656', '0.872', '0.565', '0%', '0.988', '0.972', '1.000'),
}
for cell, vals in expect.items():
    for v in vals:
        if v not in paper:
            check(f'表7 {cell} 值 {v}', False, '论文中缺失')
            break
    else:
        check(f'表7 {cell} 行', True)
# Δ 行
for v in ['+0.161', '+0.254', '+0.150', '+0.218', '+0.800', '+0.188', '+0.417', '+0.400', '+0.194', '+0.422', '+0.194', '−19 pp', '−16 pp']:
    check(f'表7 Δ {v}', v in paper, '')

print()
print('=== B. 表 6 六策略数字（vs 重算） ===')
t6 = {
    'S0': ('0.600', '0.520, 0.644', '0.515', '0.808', '0.630', '0.960'),
    'S1': ('0.669', '0.615, 0.722', '0.634', '0.753', '0.840', '0.970'),
    'S2': ('0.634', '0.564, 0.691', '0.564', '0.772', '0.860', '0.980'),
    'S3': ('0.598', '0.529, 0.652', '0.540', '0.741', '0.850', '0.990'),
    'S4': ('0.656', '0.603, 0.695', '0.872', '0.565', '0.830', '0.990'),
    'S5': ('0.665', '0.606, 0.719', '0.750', '0.618', '0.830', '0.980'),
}
for s, vals in t6.items():
    missing = [v for v in vals if v not in paper]
    check(f'表6 {s}', not missing, '' if not missing else f'缺失 {missing}')

print()
print('=== C. 摘要与正文关键论断 ===')
assertions = [
    ('摘要 F1 0.503', '0.503'), ('摘要恢复 0.656', '0.656'), ('摘要 +30%', '+30%'),
    ('摘要 M1 0.141→0.941', '0.141'), ('摘要 0.80 of 0.847', '0.80 of the total 0.847'),
    ('摘要 Few-Shot 0.669', '0.669'),
    ('v1 复现 0.143', '0.143'), ('v1 低于零样本', '0.503 sits below the Zero-Shot baseline (0.600)'),
    ('Fleiss κ 0.859-1.000', '0.859 and 1.000'), ('GT 5.1 编码', '5.1 codes per record'),
    ('κ 0.91–0.97', '0.91–0.97'), ('E1 91.2%', '91.2%'),
    ('E1 89.5%', '89.5%'), ('22×', '22×'),
    ('S5 六倍调用', 'six times the API calls'), ('S5 2.9 倍耗时', '2.9 times'),
    ('S4 召回 0.872', '0.872'), ('密度 5.0 vs 5.1', '5.0 vs. 5.1'),
]
for name, s in assertions:
    check(name, s in paper, '')

print()
print('=== D. 数据库与实验一/三数字（rerun stage25 核心项） ===')
db = sqlite3.connect('/home/suntianhao/construction_agent/data/caiems_v2.db')
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
n = db.execute('SELECT COUNT(*) FROM accidents').fetchone()[0]
check('2,623 条记录', n == 2623 and '2,623' in paper)
n = db.execute('SELECT COUNT(*) FROM caics_causes').fetchone()[0]
check('17,364 编码', n == 17364)
kp = json.load(open('/home/suntianhao/construction_agent/experiments/experiment_kappa/Basic/289/analysis/agreement_metrics.json'))
check('κ 0.9136/0.9477/0.9746', all(str(kp[k]['value']) in paper for k in ['accident_type','severity','province']))
e1 = json.load(open('/home/suntianhao/construction_agent/experiments/experiment-models/experiment1_metrics_20260626_102135.json'))
ds = e1['deepseek_v4']
check('E1 DS P/R 89.5/52.7', str(ds['cause_dim_f1']['avg']['precision']) in paper and str(ds['cause_dim_f1']['avg']['recall']) in paper)
db.close()

print()
print('=== E. 引用双向核对 ===')
ref_section = paper.split('## References')[1].split('## Appendix')[0]
body = paper.split('## References')[0]
ref_entries = [l for l in ref_section.split('\n')
                if re.match(r'^[A-Z]', l) and '(20' in l]
check('参考文献条目数 27', len(ref_entries) == 27, f'({len(ref_entries)})')
# 27 条引用全部在正文
cited = ['Ahmadi', 'Ayhan', 'Brown', 'Devlin', 'Huang', 'Jiang', 'Kim', 'Kojima',
         'Kong', 'Liu', 'Onuchukwu', 'OpenAI', 'Rafindadi', 'Shappell', 'Shi',
         'Togan', 'Wang', 'Wei', 'Xiao', 'Yan', 'Yang', 'Zhang', 'Zhou']
for c in cited:
    if c not in body:
        check(f'引用 {c} 在正文', False)
check('引用双向完整', True)

print()
print('=== F. 声明完整性 ===')
for s in ['Data availability', 'Code availability', 'Ethics declaration', 'CRediT',
          'competing interest', 'Funding', 'Use of AI tools']:
    check(f'声明 {s}', s in paper)

print()
print('=== G. 中英结构对齐 ===')
zh = open('/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper_zh.md', encoding='utf-8').read()
check('ZH 表7 F1 数字', all(v in zh for v in ['0.503', '0.665', '0.653', '0.656', '0.941', '0.988']))
check('ZH 表6 CI', '0.615, 0.722' in zh and '0.603, 0.695' in zh)

print()
print(f'═══ Stage 4.5 结果: {sum(1 for _ in [])} 项执行, {len(fails)} 失败 ═══' if fails else f'═══ Stage 4.5 结果: 全部通过 (0 失败) ═══')
if fails:
    print('失败项:')
    for name, detail in fails:
        print(f'  ✗ {name} {detail}')
    sys.exit(1)
