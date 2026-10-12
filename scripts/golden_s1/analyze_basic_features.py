"""flash vs pro 在 31 个基础特征上的一致性分析，并生成逐条裁决用 HTML。

目的：先敲定 300 条 GT 在基础特征上的标准答案。做法是逐字段量化两个档位的一致度，
把字段分成「可直接采纳」与「必须人工裁决」两类，再给出逐条对照视图。

三方数据（都不是真值，只作裁决参考）：
  flash / pro  —— 本次两次 S1′ 运行
  库内 Step1   —— 主管道既有提取（作为第三意见）

用法：
    python3 scripts/golden_s1/analyze_basic_features.py
"""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen")
GOLD = ROOT / "experiments/golden_standard"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"

# ── 字段分组：决定用什么比较方式 ──
F_ENUM = ["accident_type", "severity", "project_type", "building_structure",
          "construction_phase", "work_task", "contractor_type", "collapse_subtype",
          "equipment_involved", "material_involved", "worker_type", "report_type"]
F_NUM = ["deaths", "injuries", "trapped_count"]
F_NUM_TOL = {"direct_economic_loss": 0.05, "fall_height": 0.5}
F_DATE = ["date", "accident_time"]
F_TEXT = ["title", "location", "city", "district", "accident_location_detail", "weather",
          "project_name", "developer", "contractor", "supervision_company", "summary",
          "direct_cause"]
ALL = F_ENUM + F_NUM + list(F_NUM_TOL) + F_DATE + F_TEXT
assert len(ALL) == 31, len(ALL)

# 文本类字段的模糊判定阈值（difflib 相似度）
FUZZY = {"title": 0.75, "summary": 0.7, "direct_cause": 0.6,
         "accident_location_detail": 0.7, "project_name": 0.78,
         "developer": 0.7, "contractor": 0.75, "supervision_company": 0.7}
EMPTY = {None, "", "未知", "未提及", "null"}

DB_COL = {"location": "province"}   # 字段名差异


def norm(v):
    if isinstance(v, str):
        s = v.strip()
        if s in EMPTY:
            return None
        return re.sub(r"[\s“”\"'·,，。.：:；;（）()\[\]【】—\-–_/、]", "", s)
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def load_results(p: Path):
    return {json.loads(l)["id"]: json.loads(l)["parsed"] for l in p.open(encoding="utf-8")}


def load_db(ids):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cols = ", ".join(["id", "title"] + [f'"{c}"' for c in
                                         [DB_COL.get(f, f) for f in ALL if f != "title"]])
    out = {}
    for r in con.execute(f"select {cols} from accidents"):
        out[r["id"]] = {f: r[DB_COL.get(f, f)] for f in ALL}
    con.close()
    return out


def cmp_value(field: str, a, b, db=None):
    """返回 'agree' | 'soft' | 'disagree' | 'both_null' | 'one_null'"""
    na, nb = norm(a), norm(b)
    nd = norm(db) if db is not None else None

    if field in F_NUM:
        try:
            x, y = float(a or 0), float(b or 0)
        except (TypeError, ValueError):
            x, y = 0, 0
        return "agree" if x == y else "disagree"
    if field in F_NUM_TOL:
        if na is None and nb is None:
            return "both_null"
        if na is None or nb is None:
            return "one_null"
        tol = F_NUM_TOL[field]
        x, y = float(a), float(b)
        if field == "fall_height":
            ok = abs(x - y) <= tol                      # 高度：绝对容差 0.5 m
        else:
            ok = abs(x - y) <= tol * max(abs(x), abs(y), 1e-9)   # 经济损失：相对容差 5%
        return "agree" if ok else "disagree"

    if na is None and nb is None:
        return "both_null"
    if na is None or nb is None:
        return "one_null"
    if na == nb:
        return "agree"
    if field in F_TEXT:
        if field == "city":   # 市名常有"市"省略差异，已由 norm 处理；再做包含判定
            if na in nb or nb in na:
                return "soft"
        r = difflib.SequenceMatcher(None, na, nb).ratio()
        if r >= FUZZY.get(field, 0.75):
            return "soft"
    return "disagree"


def main():
    flash = load_results(GOLD / "S1_golden_results.jsonl")
    pro = load_results(GOLD / "pro/S1_golden_results.jsonl")
    cand = {json.loads(l)["id"]: json.loads(l)
            for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}
    ids = list(cand)
    db = load_db(set(ids))

    # 逐字段统计
    per_field = {}
    for f in ALL:
        c = Counter()
        for i in ids:
            c[cmp_value(f, flash[i].get(f), pro[i].get(f), db[i].get(f))] += 1
        per_field[f] = c

    print("=" * 96)
    print(f"{'字段':26s}{'全一致':>7s}{'模糊一致':>9s}{'一方空':>7s}{'冲突':>6s}"
          f"{'双方空':>7s}{'严格一致率':>11s}{'有效一致率':>11s}")
    print("-" * 96)
    for f in ALL:
        c = per_field[f]
        n = len(ids)
        strict = (c["agree"] + c["both_null"]) / n
        denom = c["agree"] + c["soft"] + c["disagree"]      # 双方都非空
        valid = (c["agree"] + c["soft"]) / denom if denom else 0
        print(f"{f:26s}{c['agree']:7d}{c['soft']:9d}{c['one_null']:7d}{c['disagree']:6d}"
              f"{c['both_null']:7d}{strict*100:10.1f}%{valid*100:10.1f}%")

    # 逐条统计：多少字段完全一致
    per_row = []
    for i in ids:
        agree = sum(1 for f in ALL if cmp_value(f, flash[i].get(f), pro[i].get(f)) in ("agree", "both_null"))
        soft = sum(1 for f in ALL if cmp_value(f, flash[i].get(f), pro[i].get(f)) == "soft")
        hard = [f for f in ALL if cmp_value(f, flash[i].get(f), pro[i].get(f)) == "disagree"]
        per_row.append({"id": i, "agree": agree, "soft": soft, "hard": hard})
    dist = Counter(r["agree"] for r in per_row)
    print("\n每条 31 字段中「完全一致（含双方空）」的字段数分布：")
    for k in sorted(dist, reverse=True):
        print(f"  {k:2d} 个字段一致：{dist[k]:3d} 条")

    print(f"\n全 31 字段都一致的事故：{sum(1 for r in per_row if r['agree']==31)} 条")
    print(f"存在硬冲突字段的事故：{sum(1 for r in per_row if r['hard'])} 条")
    hard_cnt = Counter(f for r in per_row for f in r["hard"])
    print("\n硬冲突出现最多的字段（top 10）：")
    for f, n in hard_cnt.most_common(10):
        print(f"  {f:26s} {n:3d} 条")

    # 导出逐条三方对照（给 HTML 用）
    payload = []
    for i in ids:
        rows = []
        for f in ALL:
            a, b, d = flash[i].get(f), pro[i].get(f), db[i].get(f)
            rows.append({"f": f, "flash": a, "pro": b, "db": d,
                         "st": cmp_value(f, a, b, d)})
        payload.append({"id": i, "t": cand[i]["title"], "tier": cand[i]["tier"],
                        "fields": rows,
                        "hard": sum(1 for r in rows if r["st"] == "disagree")})
    # 裁决建议：高一致 → 可自动采纳；report_type 是来源元数据不该由模型抽；其余需人工
    verdict = {}
    for f in ALL:
        c = per_field[f]
        denom = c["agree"] + c["soft"] + c["disagree"]
        valid = (c["agree"] + c["soft"]) / denom if denom else 0.0
        if f == "report_type":
            verdict[f] = "meta"
        elif valid >= 0.95 and c["disagree"] <= 10:
            verdict[f] = "auto"
        elif f in F_TEXT:
            verdict[f] = "free"
        else:
            verdict[f] = "manual"

    stats = {
        "n": len(ids),
        "fieldOrder": ALL,
        "enum": F_ENUM, "num": F_NUM, "numTol": list(F_NUM_TOL),
        "date": F_DATE, "text": F_TEXT,
        "fields": {f: dict(per_field[f]) for f in ALL},
        "verdict": verdict,
        "fullAgree": sum(1 for r in per_row if r["agree"] == 31),
        "anyHard": sum(1 for r in per_row if r["hard"]),
    }
    print("\n裁决建议：")
    for v, label in [("auto", "可直接采纳（有效一致率≥95% 且冲突≤10）"),
                     ("meta", "取库内元数据，不由模型抽"),
                     ("free", "自由文本，需定书写规范"),
                     ("manual", "需人工逐条裁决")]:
        fs = [f for f in ALL if verdict[f] == v]
        print(f"  {label}: {len(fs)} 个 —— {', '.join(fs)}")

    # 需人工裁决的字段里，有多少条分歧可由「三方（flash/pro/库内）多数」直接定
    manual_fields = [f for f in ALL if verdict[f] == "manual"]
    print("\n人工裁决字段的分歧可解性（三方多数 = flash/pro/库内中至少两方相同）：")
    print(f"  {'字段':24s}{'分歧条数':>9s}{'多数可定':>9s}{'仍需人工':>9s}")
    tot_res = tot_need = 0
    for f in manual_fields:
        dis = res = 0
        for i in ids:
            if cmp_value(f, flash[i].get(f), pro[i].get(f)) != "disagree":
                continue
            dis += 1
            vals = Counter(v for v in (norm(flash[i].get(f)), norm(pro[i].get(f)),
                                       norm(db[i].get(f))) if v is not None)
            if vals and vals.most_common(1)[0][1] >= 2:
                res += 1
        tot_res += res
        tot_need += dis - res
        print(f"  {f:24s}{dis:9d}{res:9d}{dis-res:9d}")
    print(f"  {'合计':24s}{tot_res+tot_need:9d}{tot_res:9d}{tot_need:9d}")
    tpl = (Path(__file__).parent / "basic_feat_template.html").read_text(encoding="utf-8")
    out = (tpl.replace("/*__DATA__*/", json.dumps(payload, ensure_ascii=False))
              .replace("/*__STATS__*/", json.dumps(stats, ensure_ascii=False)))
    p = GOLD / "basic_features_flash_vs_pro.html"
    p.write_text(out, encoding="utf-8")
    print(f"\nHTML → {p}  ({len(out)//1024} KB)")


if __name__ == "__main__":
    main()
