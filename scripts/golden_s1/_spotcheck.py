import json, re, random

recs = [json.loads(l) for l in
        open("experiments/golden_standard/flash/S1_golden_results.jsonl", encoding="utf-8")]
raw = {json.loads(l)["id"]: json.loads(l)["raw_text"] for l in
       open("experiments/golden_standard/golden_candidates.jsonl", encoding="utf-8")}
random.seed(11)
for r in random.sample(recs, 2):
    p = r["parsed"]
    t = re.sub(r"\s+", " ", raw[r["id"]])
    print("=" * 78)
    print("标题:", r["title"], "| tier", r["tier"])
    print("S1 编码:", p["codes"])
    print("类型/严重/死亡:", p["accident_type"], p["severity"], p["deaths"], "| 直接原因:", p["direct_cause"][:90])
    print("--- 原文（事故简况段）---")
    m = re.search(r"(发生(?:事故)?[简高]况|事故经过|事故简介)", t)
    print(t[m.start():m.start() + 800] if m else t[:800])
    print("--- 原文（原因段）---")
    m2 = re.search(r"(直接原因|间接原因|事故原因)", t)
    print(t[m2.start():m2.start() + 700] if m2 else "(无)")
