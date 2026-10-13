import csv, json, statistics as st
from collections import Counter

p = "experiments/golden_standard/S1_golden_review_vs_db.csv"
rows = list(csv.DictReader(open(p, encoding="utf-8")))
j = [float(r["Jaccard"]) for r in rows if r["Jaccard"]]
print(f"与库内既有标注对照 n={len(rows)}，可比 {len(j)}")
print(f"  Jaccard 中位 {st.median(j):.3f}  均值 {st.mean(j):.3f}")
print(f"  完全一致 {sum(1 for x in j if x==1.0)}  无交集 {sum(1 for x in j if x==0.0)}")
only_s1 = Counter()
only_db = Counter()
for r in rows:
    for c in filter(None, r["仅S1有"].split("|")):
        only_s1[c] += 1
    for c in filter(None, r["仅库有"].split("|")):
        only_db[c] += 1
print("  库内没有但 S1 提出的编码 top:", only_s1.most_common(6))
print("  S1 漏掉但库内有的编码 top:", only_db.most_common(6))

# 死亡人数一致性：S1 vs 库内
recs = {json.loads(l)["id"]: json.loads(l) for l in
        open("experiments/golden_standard/flash/S1_golden_results.jsonl", encoding="utf-8")}
cand = {json.loads(l)["id"]: json.loads(l) for l in
        open("experiments/golden_standard/golden_candidates.jsonl", encoding="utf-8")}
import sqlite3
con = sqlite3.connect("file:/home/suntianhao/construction_agent/data/caiems_v2.db?mode=ro", uri=True)
db = {i: (d, inj) for i, d, inj in con.execute("select id,deaths,injuries from accidents")}
eq_d = eq_s = 0
for i, r in recs.items():
    p_ = r.get("parsed") or {}
    if i in db:
        if p_.get("deaths") == db[i][0]:
            eq_d += 1
        if p_.get("severity") and cand[i].get("severity") == p_.get("severity"):
            eq_s += 1
print(f"\n死亡人数与库内一致: {eq_d}/{len(recs)} ({eq_d/len(recs)*100:.1f}%)")
print(f"严重程度与筛选阶段一致: {eq_s}/{len(recs)} ({eq_s/len(recs)*100:.1f}%)")
