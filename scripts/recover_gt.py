#!/usr/bin/env python3
"""从三专家 LabelStudio 导出恢复策略实验 GT（多数投票）并核验"""
import json, re, sys
from pathlib import Path
from collections import Counter

EXP = {n: f'/home/suntianhao/TEM/expert{n}.json' for n in 'ABC'}

# 读取三专家标注
experts = {}
for n, path in EXP.items():
    data = json.load(open(path))
    experts[n] = {}
    for task in data:
        # 每条记录对应一个 task
        task_id = task['id']
        # 取第一条完成标注
        ann = task['annotations'][0]
        fields = {}
        for item in ann['result']:
            fn = item['from_name']
            v = item['value']
            if item['type'] == 'choices':
                fields[fn] = v.get('choices', [])
            elif item['type'] == 'number':
                fields[fn] = v.get('number')
        # data 字段可能含原文/标题
        rec = task.get('data', {})
        text = rec.get('text', '') or rec.get('raw_text', '')
        title = text[:40].replace('\n', ' ') if text else ''
        experts[n][task_id] = {'fields': fields, 'title': title}

print('专家记录数:', {n: len(v) for n, v in experts.items()})

# 按位置对齐（三份导出为同一样本的标注任务，顺序一致），以标题相似度验证
listA = sorted(experts['A'].items())
listB = sorted(experts['B'].items())
listC = sorted(experts['C'].items())
aligned = []
for (ida, a), (idb, b), (idc, c) in zip(listA, listB, listC):
    aligned.append((a, b, c))
# 位置对齐标题一致性抽查
match = sum(1 for a, b, c in aligned if a['title'][:15] == b['title'][:15] == c['title'][:15])
print(f'位置对齐标题一致: {match}/{len(aligned)}')

# 多数投票构建 GT
LAYERS = ['M', 'F', 'T', 'H', 'C']
def code_of(choice):
    m = re.match(r'^([MFTHC]\d+)', choice)
    return m.group(1) if m else choice

gt = {}
for i, (a, b, c) in enumerate(aligned):
    votes = {fn: Counter() for fn in ['accident_type', 'severity'] + LAYERS}
    votes_num = {'deaths': [], 'injuries': []}
    for f in (a['fields'], b['fields'], c['fields']):
        for fn in ['accident_type', 'severity']:
            for ch in f.get(fn, []):
                votes[fn][ch] += 1
        for L in LAYERS:
            for ch in f.get(L, []):
                votes[L][code_of(ch)] += 1
        if f.get('deaths') is not None:
            votes_num['deaths'].append(f['deaths'])
        if f.get('injuries') is not None:
            votes_num['injuries'].append(f['injuries'])
    codes = []
    for L in LAYERS:
        for code, cnt in votes[L].items():
            if cnt >= 2:  # 多数（≥2/3）
                codes.append(code)
    gt[i] = {
        'codes': sorted(set(codes)),
        'accident_type': votes['accident_type'].most_common(1)[0][0] if votes['accident_type'] else '',
        'severity': votes['severity'].most_common(1)[0][0] if votes['severity'] else '',
        'deaths': round(sum(votes_num['deaths'])/3, 1) if votes_num['deaths'] else None,
        'injuries': round(sum(votes_num['injuries'])/3, 1) if votes_num['injuries'] else None,
        'title': a['title'],
    }

print(f'\nGT 记录数: {len(gt)}')
n_codes = [len(g['codes']) for g in gt.values()]
print(f'GT 平均编码/条: {sum(n_codes)/len(n_codes):.2f} (原实验报告 5.0)')

# Fleiss' Kappa 层间一致性核验（每层按二元编码计）
def fleiss_kappa(ratings_matrix):
    """ratings_matrix: n_items x n_raters，值为类别标签"""
    import numpy as np
    n, r = ratings_matrix.shape
    cats = set(ratings_matrix.flatten().tolist())
    if len(cats) < 2:
        return 1.0
    cat_list = sorted(cats)
    n_ij = np.zeros((n, len(cat_list)))
    for i in range(n):
        for j, c in enumerate(cat_list):
            n_ij[i, j] = np.sum(ratings_matrix[i] == c)
    p_j = n_ij.sum(axis=0) / (n * r)
    P_i = (np.sum(n_ij ** 2, axis=1) - r) / (r * (r - 1))
    P_bar = P_i.mean()
    P_e = np.sum(p_j ** 2)
    return (P_bar - P_e) / (1 - P_e) if P_e < 1 else 1.0

print('\n=== 专家间 Fleiss Kappa（每层，二元: 含该层任一编码 vs 无）===')
for L in LAYERS:
    mat = []
    for a, b, c in aligned:
        row = []
        for f in (a['fields'], b['fields'], c['fields']):
            codes_in_layer = [code_of(ch) for ch in f.get(L, [])]
            row.append('Y' if codes_in_layer else 'N')
        mat.append(row)
    import numpy as np
    k = fleiss_kappa(np.array(mat))
    print(f'  {L} 层: κ = {k:.4f}')

out = str(Path(__file__).resolve().parents[1]
          / 'experiments' / 'ablation_gatekeeping' / 'gt_recovered.json')
json.dump(gt, open(out, 'w'), ensure_ascii=False, indent=1)
print(f'\nGT 已保存: {out}')
print('样例:', json.dumps(gt[list(gt.keys())[0]], ensure_ascii=False)[:300])
