#!/usr/bin/env python3
"""Stage 2.5 数据完整性验证：论文关键数字 vs 数据源文件"""
import sqlite3, json, re, sys
from pathlib import Path

# 本仓库根（脚本在 scripts/ 下，上溯一级），worktree 与主仓库都成立
ROOT = Path(__file__).resolve().parents[1]
DB = '/home/suntianhao/construction_agent/data/caiems_v2.db'
E1 = '/home/suntianhao/construction_agent/experiments/experiment-models/experiment1_metrics_20260626_102135.json'
KAPPA = '/home/suntianhao/construction_agent/experiments/experiment_kappa/Basic/289/analysis/agreement_metrics.json'
PAPER = str(ROOT / 'docs' / 'paper' / 'eswa_paper.md')

paper = open(PAPER, encoding='utf-8').read()
results = []

def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print(f'  {"✓" if ok else "✗"} {name} {detail}')

print('=== A. 数据库统计 ===')
db = sqlite3.connect(DB)
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
n = db.execute('SELECT COUNT(*) FROM accidents').fetchone()[0]
check('事故记录 2,623', n == 2623, f'(DB={n})')
n = db.execute('SELECT COUNT(*) FROM caics_causes').fetchone()[0]
check('致因编码 17,364', n == 17364, f'(DB={n})')
t = db.execute('SELECT SUM(deaths), SUM(injuries) FROM accidents').fetchone()
check('死亡 5,894', t[0] == 5894, f'(DB={t[0]})')
check('受伤 5,796', t[1] == 5796, f'(DB={t[1]})')
n = db.execute("SELECT COUNT(*) FROM accidents WHERE search_performed=1").fetchone()[0]
check('搜索增强 1,636', n == 1636, f'(DB={n})')
check('62.4% 覆盖率', abs(n/2623 - 0.624) < 0.001, f'(实际 {n/2623:.3f})')
import os
n = sum(len(fs) for _, _, fs in os.walk('/home/suntianhao/construction_agent/data/imageV3'))
check('CLIP图片 226 (磁盘)', n == 226, f'(磁盘={n})')
db_v3 = db.execute("SELECT COUNT(*) FROM image_files WHERE local_path LIKE 'imageV3%'").fetchone()[0]
check('DB imageV3 注册一致', db_v3 == n, f'(DB={db_v3}, 磁盘={n}，差{abs(db_v3-n)}条)')
n = db.execute('SELECT COUNT(*) FROM image_analyses').fetchone()[0]
check('VLM分析 282', n == 282, f'(DB={n})')
n = db.execute('SELECT COUNT(DISTINCT accident_id) FROM image_analyses').fetchone()[0]
check('VLM覆盖 176 事故', n == 176, f'(DB={n})')
# 日期范围
mn, mx = db.execute("SELECT MIN(date), MAX(date) FROM accidents WHERE date IS NOT NULL AND date != ''").fetchone()
check('日期 1978-08 ~ 2026-06', mn.startswith('1978-08') and mx.startswith('2026-06'), f'(DB={mn}~{mx})')
# 省份数（归一化后的大陆省级区划）
import re as _re
provs = [r[0] for r in db.execute("SELECT DISTINCT province FROM accidents WHERE province IS NOT NULL AND province != ''")]
norm = {_re.sub(r'(省|市|壮族自治区|回族自治区|维吾尔自治区|自治区)$', '', p) for p in provs}
foreign = {'乌干达','印度','塞内加尔','太子港','孟加拉国','尼泊尔','巴基斯坦','摩洛哥','新加坡','法国','泰国','洪都拉斯','海地','罗马尼亚','美国','肯尼亚','菲律宾','蒙古国','西班牙','越南','阿尔巴尼亚','韩国','中国','台湾','香港'}
prov_cn = norm - foreign
long_vals = {p for p in norm if len(p) > 6}  # 地址类错误值
prov_ok = prov_cn - long_vals
check('31 大陆省级区域', len(prov_ok) == 31, f'(归一化后大陆省级={len(prov_ok)}; 国外={len(foreign & norm)}; 地址类错误={len(long_vals)})')
# 文本长度中位数
import statistics as _st
lens = [r[0] for r in db.execute("SELECT length(raw_text) FROM accidents WHERE raw_text IS NOT NULL AND raw_text != '' LIMIT 3000")]
med = _st.median(lens)
check('正文中位数 517 字符', abs(med - 517) < 5, f'(DB中位数={med:.0f})')
# 事故类型分布
types = dict(db.execute("SELECT accident_type, COUNT(*) FROM accidents WHERE accident_type IS NOT NULL AND accident_type != '' GROUP BY accident_type").fetchall())
check('12 事故类型', len(types) == 12, f'({len(types)}类)')
check('坍塌 1,092', types.get('坍塌') == 1092, f'({types.get("坍塌")})')
check('高处坠落 570', types.get('高处坠落') == 570, f'({types.get("高处坠落")})')
check('起重伤害 225', types.get('起重伤害') == 225, f'({types.get("起重伤害")})')
check('土方坍塌 222', types.get('土方坍塌') == 222, f'({types.get("土方坍塌")})')
check('触电 160', types.get('触电') == 160, f'({types.get("触电")})')
check('物体打击 119', types.get('物体打击') == 119, f'({types.get("物体打击")})')
check('火灾爆炸 47', types.get('火灾爆炸') == 47, f'({types.get("火灾爆炸")})')
check('机械伤害 46', types.get('机械伤害') == 46, f'({types.get("机械伤害")})')
check('中毒窒息 41', types.get('中毒窒息') == 41, f'({types.get("中毒窒息")})')
check('车辆伤害 38', types.get('车辆伤害') == 38, f'({types.get("车辆伤害")})')
check('淹溺 7', types.get('淹溺') == 7, f'({types.get("淹溺")})')
# 等级
sev = dict(db.execute("SELECT severity, COUNT(*) FROM accidents WHERE severity IS NOT NULL AND severity != '' GROUP BY severity").fetchall())
check('等级 1889/672/53/8', sev.get('一般事故')==1889 and sev.get('较大事故')==672 and sev.get('重大事故')==53 and sev.get('特别重大事故')==8, f'({sev})')
db.close()

print()
print('=== B. 实验一指标 ===')
e1 = json.load(open(E1))
ds, qw, gl, km = e1['deepseek_v4'], e1['qwen3'], e1['glm52'], e1['kimi']
check('DS 总体准确率 91.2', ds['basic_fields']['avg_accuracy'] == 91.2)
check('Qwen 91.5', qw['basic_fields']['avg_accuracy'] == 91.5)
check('GLM 91.5', gl['basic_fields']['avg_accuracy'] == 91.5)
check('Kimi 63.9', km['basic_fields']['avg_accuracy'] == 63.9)
check('DS P/R 89.5/52.7', ds['cause_dim_f1']['avg']['precision']==89.5 and ds['cause_dim_f1']['avg']['recall']==52.7)
check('Qwen P/R 78.0/80.5', qw['cause_dim_f1']['avg']['precision']==78.0 and qw['cause_dim_f1']['avg']['recall']==80.5)
check('GLM P/R 84.4/65.5', gl['cause_dim_f1']['avg']['precision']==84.4 and gl['cause_dim_f1']['avg']['recall']==65.5)
check('Kimi P/R 90.9/38.9', km['cause_dim_f1']['avg']['precision']==90.9 and km['cause_dim_f1']['avg']['recall']==38.9)
check('成本 $0.13/$0.43/$2.91/$2.84', abs(ds['cost']['total_usd']-0.1333)<0.01 and abs(qw['cost']['total_usd']-0.4279)<0.01 and abs(gl['cost']['total_usd']-2.912)<0.01 and abs(km['cost']['total_usd']-2.8394)<0.01)
check('22倍成本差', abs(2.912/0.1333 - 21.85) < 0.5, f'(实际 {2.912/0.1333:.1f}x)')
check('延迟 9.8/8.6/27.0/74.9', abs(ds['cost']['avg_latency']-9.79)<0.2 and abs(qw['cost']['avg_latency']-8.62)<0.2 and abs(gl['cost']['avg_latency']-26.96)<0.2 and abs(km['cost']['avg_latency']-74.93)<0.2)
check('有效率 200/200/197/140', ds['cost']['success']==200 and qw['cost']['success']==200 and gl['cost']['success']==197 and km['cost']['success']==140)
check('每记录 $0.0007', abs(0.1333/200 - 0.0007) < 0.0001, f'(实际 {0.1333/200:.4f})')
# 字段级核对（论文 Table 4）
f = ds['basic_fields']
check('DS city 81.5', f['city']['accuracy'] == 81.5)
check('DS severity 98.0', f['severity']['accuracy'] == 98.0)
f = km['basic_fields']
check('Kimi accident_type 61.5', f['accident_type']['accuracy'] == 61.5)

print()
print('=== C. 实验二策略指标（对照分析文档） ===')
doc = open('/home/suntianhao/construction_agent/docs/paper_sections/5.2-5.4_Prompt策略对比实验分析.md', encoding='utf-8').read()
pairs = [
    ('S1 F1 0.737', '0.737', doc), ('S4 F1 0.676', '0.676', doc), ('S5 F1 0.488', '0.488', doc),
    ('S0 F1 0.628', '0.628', doc), ('S2 F1 0.630', '0.630', doc), ('S3 F1 0.643', '0.643', doc),
    ('S4 Recall 0.772', '0.772', doc), ('S1 Recall 0.739', '0.739', doc),
    ('v1 F1 0.425', '0.425', doc), ('v2 F1 0.676', '0.676', doc),
    ('v1 Recall 0.319', '0.319', doc), ('v2 Recall 0.772', '0.772', doc),
    ('M1 v1 Recall 0.143', '0.143', doc), ('M1 v2 Recall 0.941', '0.941', doc),
    ('空编码 30%→1%', '30%', doc),
    ('S4 TypeAcc 0.880', '0.880', doc), ('S4 SevAcc 0.970', '0.970', doc),
    ('编码密度 5.0 vs 5.0', '5.0', doc),
    ('T层 S4 0.753 > S1 0.708', '0.753', doc),
    ('F1 提升 +0.251', '0.251', doc),
]
for name, val, src in pairs:
    check(name, val in src, '')

print()
print('=== D. 实验三 Kappa ===')
kp = json.load(open(KAPPA))
check('类型 κ=0.9136 [0.8747,0.9466]', kp['accident_type']['value']==0.9136 and kp['accident_type']['ci_lo']==0.8747 and kp['accident_type']['ci_hi']==0.9466)
check('等级 κ=0.9477', kp['severity']['value']==0.9477)
check('省份 κ=0.9746', kp['province']['value']==0.9746)
check('死亡 ICC=0.9849 MAE=0.118', kp['deaths']['value']==0.9849 and kp['deaths']['mae']==0.118)
check('受伤 ICC=0.9853 MAE=0.087', kp['injuries']['value']==0.9853 and kp['injuries']['mae']==0.087)
check('日期 0.9965', kp['date']['value']==0.9965)
check('n=289 排除11', kp['meta']['n']==289 and kp['meta']['excluded']==11)
check('CI 半宽 ≤0.045', max((v['ci_hi']-v['ci_lo'])/2 for k,v in kp.items() if 'ci_hi' in v) <= 0.046)

print()
print('=== E. 专家区间（kappa_old） ===')
expert = json.load(open('/home/suntianhao/construction_agent/experiments/experiment_kappa/kappa_old/expert_inter_rater.json'))
ex_str = json.dumps(expert)
check('类型专家区间 [0.8584, 0.9642]', '0.8584' in ex_str and '0.9642' in ex_str)
check('等级专家区间 [0.8799, 0.9822]', '0.8799' in ex_str and '0.9822' in ex_str)
check('死亡专家 ICC [0.9951, 1.0]', '0.9951' in ex_str)
check('受伤专家 ICC [0.9738, 1.0]', '0.9738' in ex_str)

fails = [r for r in results if not r[1]]
print()
print(f'═══ 汇总: {len(results)-len(fails)}/{len(results)} 通过, {len(fails)} 失败 ═══')
for name, ok, detail in fails:
    print(f'  ✗ {name} {detail}')
sys.exit(1 if fails else 0)
