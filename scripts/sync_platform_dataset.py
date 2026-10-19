#!/usr/bin/env python3
"""将 caiems_v2.db 的 2,623 条事故同步到 Web 平台数据集 JSON
（原文件备份为 accidents_merged.json.bak193）
字段映射: DB 48列 → legacy 平台 schema，caics_codes 取自 caics_causes
"""
import json, sqlite3, shutil
from pathlib import Path

DB = '/home/suntianhao/construction_agent/data/caiems_v2.db'
DST = Path('/home/suntianhao/construction_agent/data/datasets/accidents_merged.json')

# 备份原文件
if DST.exists():
    shutil.copy(DST, DST.with_suffix('.json.bak193'))
    print('已备份原 193 条文件 → accidents_merged.json.bak193')

db = sqlite3.connect(DB)
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
db.row_factory = sqlite3.Row

# 每条事故的 CAICS 编码
codes = {}
for r in db.execute('SELECT accident_id, code FROM caics_causes ORDER BY accident_id, sort_order'):
    codes.setdefault(r['accident_id'], []).append(r['code'])

records = []
rows = db.execute('SELECT * FROM accidents ORDER BY date').fetchall()
for r in rows:
    rec = {
        'id': r['id'],
        'title': r['title'],
        'date': r['date'],
        'accident_time': r['accident_time'],
        'location': r['province'],
        'city': r['city'],
        'district': r['district'],
        'accident_location_detail': r['accident_location_detail'],
        'project_name': r['project_name'],
        'project_type': r['project_type'],
        'building_structure': r['building_structure'],
        'construction_phase': r['construction_phase'],
        'work_task': r['work_task'],
        # ⚠️ 历史遗留列：1,538 条里 703 条是已废弃编码（WT11–WT15，当前
        # `enum_definitions` 中不存在）。当前口径只看 `work_task`。
        # 这里照搬是为了兼容 legacy 平台的 schema，**新代码不要读这一列**。
        'work_task_enum': r['work_task_enum'],
        'accident_type': r['accident_type'],
        'severity': r['severity'],
        'weather': r['weather'],
        'deaths': r['deaths'],
        'injuries': r['injuries'],
        'trapped_count': r['trapped_count'],
        'direct_economic_loss': r['direct_economic_loss'],
        'fall_height': r['fall_height'],
        'collapse_subtype': r['collapse_subtype'],
        'equipment_involved': r['equipment_involved'],
        'material_involved': r['material_involved'],
        'worker_type': r['worker_type'],
        'direct_cause': r['direct_cause'],
        'contractor': r['contractor'],
        'developer': r['developer'],
        'supervision_company': r['supervision_company'],
        'contractor_type': r['contractor_type'],
        'source_name': r['source_name'],
        'source_urls': [r['source_url']] if r['source_url'] else [],
        'summary': r['summary'],
        'caics_codes': codes.get(r['id'], []),
        'search_snippets': [],
        'search_queries': [],
        'images': [],
        'image_urls': [],
        'raw_input': (r['raw_text'] or '')[:3000],
        'created_at': r['created_at'],
    }
    records.append(rec)

with open(DST, 'w', encoding='utf-8') as f:
    json.dump(records, f, ensure_ascii=False, indent=1)

n_caics = sum(1 for x in records if x['caics_codes'])
print(f'已写入 {len(records)} 条记录（其中 {n_caics} 条带 CAICS 编码）')
db.close()
