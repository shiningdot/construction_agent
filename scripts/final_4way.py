"""V1/V2/V3/V4 四版本横向对比评测"""
import json, re, sys
from pathlib import Path
sys.path.insert(0, ".")

def norm(s):
    return re.sub(r'\s+', '', (s or "")).lower()

# ── 银标准 ──
with open("data/silver_standard.json", "r") as f:
    ss = json.load(f)
ss_records = ss if isinstance(ss, list) else ss.get("records", [])
ss_idx = {norm(r.get("title","")): r for r in ss_records}

# ── V1/V2 ──
v1_idx = {norm(r.get("title","")): r for r in json.load(open("data/accidents.json"))}
v2_idx = {norm(r.get("title","")): r for r in json.load(open("data/accidents_v2.json"))}

# ── V4 ──
v4_idx = {}
if Path("data/v4_results.json").exists():
    for r in json.load(open("data/v4_results.json")):
        if r.get("success"):
            a = r["accident"]
            v4_idx[norm(a.get("title",""))] = a

# ── V3 ──
v3_idx = {}
v3_cause = {}
if Path("data/v3_cause_results.json").exists():
    for r in json.load(open("data/v3_cause_results.json")):
        if r.get("success"):
            t = norm(r["title"])
            v3_idx[t] = r.get("accident", {})
            v3_cause[t] = r.get("causes", {})

# ── 字段定义 ──
FIELDS = [
    ("accident_type","分类","事故类型"), ("severity","分类","事故等级"),
    ("location","字符串","省级地点"), ("city","字符串","地级市"),
    ("deaths","数值","死亡人数"), ("injuries","数值","受伤人数"),
    ("weather","字符串","天气"), ("project_type","字符串","工程类型"),
    ("direct_cause","字符串","直接原因"), ("contractor","字符串","施工单位"),
]

def calc(preds, truths, ft):
    total = len(truths)
    if total == 0:
        return {"acc":0,"prec":0,"rec":0,"f1":0}
    exact = sum(1 for p,g in zip(preds,truths) if str(p).strip()==str(g).strip())
    acc = exact/total*100
    if ft != "分类":
        return {"acc":round(acc,1),"prec":round(acc,1),"rec":round(acc,1),"f1":round(acc,1)}
    classes = sorted(set(str(g).strip() for g in truths if str(g).strip()) | set(str(p).strip() for p in preds if str(p).strip()))
    if not classes:
        return {"acc":round(acc,1),"prec":100,"rec":100,"f1":100}
    ps, rs, fs = [], [], []
    for cls in classes:
        tp = sum(1 for p,g in zip(preds,truths) if str(p).strip()==cls and str(g).strip()==cls)
        fp = sum(1 for p,g in zip(preds,truths) if str(p).strip()==cls and str(g).strip()!=cls)
        fn = sum(1 for p,g in zip(preds,truths) if str(p).strip()!=cls and str(g).strip()==cls)
        p = tp/(tp+fp) if (tp+fp)>0 else 0
        r = tp/(tp+fn) if (tp+fn)>0 else 0
        f = 2*p*r/(p+r) if (p+r)>0 else 0
        ps.append(p); rs.append(r); fs.append(f)
    return {"acc":round(acc,1),"prec":round(sum(ps)/len(ps)*100,1),
            "rec":round(sum(rs)/len(rs)*100,1),"f1":round(sum(fs)/len(fs)*100,1)}

def evaluate(name, idx, cause_only=False):
    titles = [t for t in ss_idx if t in idx]
    if not titles:
        return None
    res = {"matched":len(titles),"fields":{}}
    for fk, ft, fcn in FIELDS:
        if cause_only and fk not in ("direct_cause",):
            continue
        preds = [str(idx[t].get(fk,"")).strip() for t in titles]
        truths = [str(ss_idx[t].get(fk,"")).strip() for t in titles]
        res["fields"][fk] = calc(preds, truths, ft)
    return res

# ── 执行 ──
all_r = {}
for name, idx, cause in [("V1",v1_idx,False),("V2",v2_idx,False),("V4",v4_idx,False)]:
    r = evaluate(name, idx, cause)
    if r: all_r[name] = r

# V3: 只致因
if v3_idx:
    r = evaluate("V3", v3_idx, cause_only=True)
    if r: all_r["V3"] = r

# ── 输出 MD ──
versions = [v for v in ["V1","V2","V3","V4"] if v in all_r]

print("# V1 vs V2 vs V3 vs V4 横向对比\n")
print(f"**银标准**: {len(ss_idx)} 条\n")

# 表头
print(f"| 字段 | {' | '.join([f'{v} Acc' for v in versions])} | {' | '.join([f'{v} F1' for v in versions])} |")
print(f"|------|{'|'.join(['-------' for _ in versions])}|{'|'.join(['-------' for _ in versions])}|")

for fk, ft, fcn in FIELDS:
    row = [fcn]
    for v in versions:
        fields = all_r[v]["fields"]
        if fk in fields:
            a = fields[fk]["acc"]
            row.append(f"{'🟢' if a>=95 else '🟡' if a>=80 else '🔴'}{a:.1f}%")
        else:
            row.append("—")
    for v in versions:
        fields = all_r[v]["fields"]
        if fk in fields:
            f1 = fields[fk]["f1"]
            row.append(f"{'🟢' if f1>=95 else '🟡' if f1>=80 else '🔴'}{f1:.1f}%")
        else:
            row.append("—")
    print(f"| {' | '.join(row)} |")

# 综合
print(f"\n## 综合\n")
for v in versions:
    r = all_r[v]
    fields = r["fields"]
    avg_f1 = sum(m["f1"] for m in fields.values())/len(fields)
    avg_acc = sum(m["acc"] for m in fields.values())/len(fields)
    print(f"- **{v}**: Acc={avg_acc:.1f}% | F1={avg_f1:.1f}% | 匹配{r['matched']}条")

# 致因专项
print(f"\n## 致因专项（V3 对比）\n")
cause_field = "direct_cause"
print(f"| 版本 | Accuracy | Precision | Recall | F1 |")
print(f"|------|----------|-----------|--------|-----|")
for v in versions:
    if cause_field in all_r[v]["fields"]:
        m = all_r[v]["fields"][cause_field]
        print(f"| {v} | {m['acc']:.1f}% | {m['prec']:.1f}% | {m['rec']:.1f}% | {m['f1']:.1f}% |")

# ── 保存 ──
with open("experiments/four_way_comparison.md", "w", encoding="utf-8") as f:
    f.write("# V1 vs V2 vs V3 vs V4 横向对比\n\n")
    f.write(f"**银标准**: {len(ss_idx)} 条\n\n")
    f.write(f"| 字段 | {' | '.join([f'{v} Acc' for v in versions])} | {' | '.join([f'{v} F1' for v in versions])} |\n")
    f.write(f"|------|{'|'.join(['-------' for _ in versions])}|{'|'.join(['-------' for _ in versions])}|\n")
    for fk, ft, fcn in FIELDS:
        row = [fcn]
        for v in versions:
            row.append(f"{all_r[v]['fields'].get(fk,{}).get('acc',0):.1f}%")
        for v in versions:
            row.append(f"{all_r[v]['fields'].get(fk,{}).get('f1',0):.1f}%")
        f.write(f"| {' | '.join(row)} |\n")
    f.write("\n## 致因专项\n\n")
    f.write(f"| 版本 | Accuracy | Precision | Recall | F1 |\n")
    f.write(f"|------|----------|-----------|--------|-----|\n")
    for v in versions:
        if cause_field in all_r[v]["fields"]:
            m = all_r[v]["fields"][cause_field]
            f.write(f"| {v} | {m['acc']:.1f}% | {m['prec']:.1f}% | {m['rec']:.1f}% | {m['f1']:.1f}% |\n")

print(f"\n✅ experiments/four_way_comparison.md")
