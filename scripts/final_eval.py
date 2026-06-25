"""四版本最终评测 — Silver Standard vs V1/V2/V3/V4

表1: 基本字段提取 (V1 vs V2 vs V4)
  字段: accident_type, severity, deaths, injuries, location, weather

表2: 致因提取 (V1 vs V2 vs V3 vs V4)
  维度: 致因四维度是否有填充
"""

import json, sys, logging
from pathlib import Path
from collections import Counter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 加载银标准 ──
with open("data/silver_standard.json", "r") as f:
    ss = json.load(f)
ss_records = ss["records"] if isinstance(ss, dict) and "records" in ss else ss
print(f"银标准: {len(ss_records)} 条")

def norm(s):
    import re
    return re.sub(r'\s+', '', (s or "")).lower()

ss_by_title = {norm(r.get("title","")): r for r in ss_records}

# ── 加载各版本数据 ──
def load_results(path):
    with open(path, "r") as f:
        data = json.load(f)
    if isinstance(data, dict) and "records" in data:
        return data["records"]
    return data

v1_records = load_results("data/accidents.json")
v2_records = load_results("data/accidents_v2.json")

# V4 结果（如果存在）
v4_results = []
if Path("data/v4_results.json").exists():
    v4_data = json.load(open("data/v4_results.json"))
    v4_results = [r for r in v4_data if r.get("success") and r.get("accident")]
    print(f"V4 结果: {len(v4_results)} 条")

# V3 结果（如果存在）
v3_results = []
if Path("data/v3_results.json").exists():
    v3_data = json.load(open("data/v3_results.json"))
    v3_results = [r for r in v3_data if r.get("success") and r.get("accident")]
    print(f"V3 结果: {len(v3_results)} 条")

def index_by_title(records):
    idx = {}
    for r in records:
        title = r.get("title","") or r.get("accident",{}).get("title","")
        if not title and isinstance(r, dict) and "accident" in r:
            title = r["accident"].get("title","")
        t = norm(title)
        if t:
            idx[t] = r
    return idx

v1_idx = index_by_title(v1_records)
v2_idx = index_by_title(v2_records)
v4_idx = {}
for r in v4_results:
    a = r.get("accident", {})
    t = norm(a.get("title",""))
    if t:
        v4_idx[t] = a

v3_idx = {}
for r in v3_results:
    a = r.get("accident", {})
    t = norm(a.get("title",""))
    if t:
        v3_idx[t] = a

# ── 表1: 基本字段提取 ──
BASIC_FIELDS = ["accident_type", "severity", "deaths", "injuries", "location", "weather"]

def eval_basic(version, idx):
    correct = Counter()
    total = 0
    for title, gt in ss_by_title.items():
        if title not in idx:
            continue
        pred = idx[title]
        total += 1
        for field in BASIC_FIELDS:
            pv = str(pred.get(field,"")).strip()
            gv = str(gt.get(field,"")).strip()
            if pv == gv:
                correct[field] += 1
    return {
        "version": version,
        "matched": total,
        "field_accuracy": {f: round(correct[f]/max(total,1)*100, 1) for f in BASIC_FIELDS},
        "overall": round(sum(correct.values())/max(total*len(BASIC_FIELDS),1)*100, 2),
    }

# ── 表2: 致因提取 ──
CAUSE_DIMS = [
    "human_factors", "machine_factors",
    "management_factors", "environmental_factors"
]

def has_causes(record):
    """检查记录是否有致因（V1/V2 格式）。"""
    for dim in CAUSE_DIMS:
        factors = record.get(dim, [])
        if isinstance(factors, list) and len(factors) > 0:
            return True
    return False

def has_causes_v3(record):
    """V3 结果中的致因检查。"""
    a = record.get("accident", {})
    for dim in CAUSE_DIMS:
        factors = a.get(dim, [])
        if isinstance(factors, list) and len(factors) > 0:
            return True
    return False

def eval_causation(version, idx, is_v3=False):
    tp = {d: 0 for d in CAUSE_DIMS}  # 预测有，GT也有
    fp = {d: 0 for d in CAUSE_DIMS}  # 预测有，GT无
    fn = {d: 0 for d in CAUSE_DIMS}  # 预测无，GT有
    tn = {d: 0 for d in CAUSE_DIMS}  # 预测无，GT无
    total = 0

    for title, gt in ss_by_title.items():
        if title not in idx:
            continue
        total += 1
        pred = idx[title]

        for dim in CAUSE_DIMS:
            gt_has = len(gt.get(dim, []) or []) > 0

            if is_v3:
                a = pred.get("accident", pred)
                pred_has = len(a.get(dim, []) or []) > 0
            else:
                pred_has = len(pred.get(dim, []) or []) > 0

            if pred_has and gt_has: tp[dim] += 1
            elif not pred_has and gt_has: fn[dim] += 1
            elif pred_has and not gt_has: fp[dim] += 1
            else: tn[dim] += 1

    results = {}
    for dim in CAUSE_DIMS:
        precision = tp[dim]/max(tp[dim]+fp[dim], 1)
        recall = tp[dim]/max(tp[dim]+fn[dim], 1)
        f1 = 2*precision*recall/max(precision+recall, 0.001)
        results[dim] = {
            "precision": round(precision*100, 1),
            "recall": round(recall*100, 1),
            "f1": round(f1*100, 1),
            "tp": tp[dim], "fp": fp[dim], "fn": fn[dim],
        }

    return {
        "version": version,
        "matched": total,
        "dimension_metrics": results,
        "avg_f1": round(sum(r["f1"] for r in results.values())/len(results), 1),
    }


# ── 执行评测 ──
print("\n" + "="*70)
print("📊 表1: 基本字段提取对比")
print("="*70)

results_basic = {}
for v_name, v_idx in [("V1", v1_idx), ("V2", v2_idx), ("V4", v4_idx)]:
    r = eval_basic(v_name, v_idx)
    results_basic[v_name] = r
    print(f"\n{v_name} (匹配{r['matched']}条):")
    for f in BASIC_FIELDS:
        print(f"  {f:20s} {r['field_accuracy'][f]:>6.1f}%")
    print(f"  {'综合':20s} {r['overall']:>6.2f}%")

print("\n" + "="*70)
print("📊 表2: 致因提取对比 (Precision/Recall/F1)")
print("="*70)

DIM_NAMES = {"human_factors": "人的因素", "machine_factors": "设备因素",
             "management_factors": "管理因素", "environmental_factors": "环境因素"}

results_cause = {}
for v_name, v_idx, is_v3 in [("V1", v1_idx, False), ("V2", v2_idx, False),
                                ("V3", v3_idx, True), ("V4", v4_idx, False)]:
    if not v_idx:
        print(f"\n{v_name}: 无数据")
        continue
    r = eval_causation(v_name, v_idx, is_v3)
    results_cause[v_name] = r
    print(f"\n{v_name} (匹配{r['matched']}条):")
    for dim, m in r["dimension_metrics"].items():
        print(f"  {DIM_NAMES[dim]:12s} P={m['precision']:>5.1f}% R={m['recall']:>5.1f}% F1={m['f1']:>5.1f}%")
    print(f"  {'Avg F1':12s} {r['avg_f1']:>5.1f}%")

# ── 保存结果 ──
output = {
    "table1_basic_fields": results_basic,
    "table2_causation": results_cause,
    "silver_standard_size": len(ss_records),
}
with open("experiments/four_version_comparison.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n✅ 结果已保存: experiments/four_version_comparison.json")

# ── 生成 LaTeX 表格 ──
print("\n" + "="*70)
print("📝 LaTeX 表格")
print("="*70)

# 表1 LaTeX
versions_basic = [v for v in ["V1","V2","V4"] if v in results_basic]
print("\n% 表1: 基本字段提取对比")
print(r"\begin{table}[htbp]")
print(r"\centering")
print(r"\caption{基本字段提取准确率对比}")
print(r"\label{tab:basic_fields}")
print(r"\begin{tabular}{l" + "c"*len(versions_basic) + "}")
print(r"\toprule")
print(" & ".join(["字段"] + versions_basic) + r" \\")
print(r"\midrule")
for f in BASIC_FIELDS:
    row = [f]
    for v in versions_basic:
        row.append(f"{results_basic[v]['field_accuracy'][f]:.1f}\\%")
    print(" & ".join(row) + r" \\")
print(r"\bottomrule")
print(r"\end{tabular}")
print(r"\end{table}")

# 表2 LaTeX
versions_cause = [v for v in ["V1","V2","V3","V4"] if v in results_cause]
print("\n% 表2: 致因提取 F1 对比")
for metric in ["F1-score"]:
    print(r"\begin{table}[htbp]")
    print(r"\centering")
    print(r"\caption{致因维度提取" + metric + "对比}")
    print(r"\label{tab:causation}")
    print(r"\begin{tabular}{l" + "c"*len(versions_cause) + "}")
    print(r"\toprule")
    print(" & ".join(["维度"] + versions_cause) + r" \\")
    print(r"\midrule")
    for dim in CAUSE_DIMS:
        row = [DIM_NAMES[dim]]
        for v in versions_cause:
            f1 = results_cause[v]["dimension_metrics"][dim]["f1"]
            row.append(f"{f1:.1f}")
        print(" & ".join(row) + r" \\")
    print(r"\midrule")
    row = ["Avg F1"]
    for v in versions_cause:
        row.append(f"{results_cause[v]['avg_f1']:.1f}")
    print(" & ".join(row) + r" \\")
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(r"\end{table}")

print(f"\n✅ LaTeX 表格已打印")
