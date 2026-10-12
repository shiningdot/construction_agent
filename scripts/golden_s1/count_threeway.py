"""三方（flash / pro / 库内 Step1）严格比对 24 个客观事故特征，统计自动确定 vs 待人工。"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen")
GOLD = ROOT / "experiments/golden_standard"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

# 剔除 7 个非客观/书写类字段：摘要 直接原因 天气 具体部位 区县 标题 报告类型
EXCLUDED = {"summary", "direct_cause", "weather", "accident_location_detail",
            "district", "title", "report_type"}

ENUM = ["accident_type", "severity", "project_type", "building_structure",
        "construction_phase", "work_task", "contractor_type", "collapse_subtype",
        "equipment_involved", "material_involved", "worker_type"]
NUM = ["deaths", "injuries", "trapped_count"]
NUM_TOL = {"direct_economic_loss": 0.05, "fall_height": 0.5}
DATE = ["date", "accident_time"]
TEXT = ["location", "city", "project_name", "developer", "contractor", "supervision_company"]
FIELDS = ENUM + NUM + list(NUM_TOL) + DATE + TEXT          # 24
assert len(FIELDS) == 24, len(FIELDS)

EMPTY = {None, "", "未知", "未提及", "null"}
DB_COL = {"location": "province"}


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


def num_ok(field, x, y):
    try:
        a, b = float(x), float(y)
    except (TypeError, ValueError):
        return False
    if field == "fall_height":
        return abs(a - b) <= NUM_TOL[field]
    return abs(a - b) <= NUM_TOL[field] * max(abs(a), abs(b), 1e-9)


def load_results(p):
    return {json.loads(l)["id"]: json.loads(l)["parsed"] for l in p.open(encoding="utf-8")}


def load_db(ids):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sel = ", ".join(["id"] + [f'"{DB_COL.get(f, f)}" AS "{f}"' for f in FIELDS])
    q = ",".join("?" * len(ids))
    out = {}
    for r in con.execute(f"select {sel} from accidents where id in ({q})", list(ids)):
        out[r["id"]] = {f: r[f] for f in FIELDS}
    con.close()
    return out


def three_way(field, a, b, c):
    """返回 (是否三方一致, 代表值, 候选列表)"""
    if field in NUM:
        va = float(a or 0); vb = float(b or 0); vc = float(c or 0)
        same = va == vb == vc
        return same, (a if a is not None else 0), [a, b, c]
    if field in NUM_TOL:
        nz = [v for v in (a, b, c) if norm(v) is not None]
        same = len(nz) <= 1 or (len(nz) == 3 and num_ok(field, nz[0], nz[1])
                                and num_ok(field, nz[0], nz[2]))
        return same, (nz[0] if nz else None), [a, b, c]
    na, nb, nc = norm(a), norm(b), norm(c)
    same = na == nb == nc
    return same, (a if na is not None else (b if nb is not None else c)), [a, b, c]


def main():
    flash = load_results(GOLD / "S1_golden_results.jsonl")
    pro = load_results(GOLD / "pro/S1_golden_results.jsonl")
    cand = {json.loads(l)["id"]: json.loads(l)
            for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}
    ids = list(cand)
    db = load_db(ids)

    rows, per_field = [], defaultdict(Counter)
    for i in ids:
        need = []
        for f in FIELDS:
            same, val, cands = three_way(f, flash[i].get(f), pro[i].get(f), db[i].get(f))
            per_field[f]["same" if same else "diff"] += 1
            if not same:
                need.append({"field": f, "cands": cands})
        rows.append({"id": i, "need": need})

    n_items = sum(len(r["need"]) for r in rows)
    print(f"字段数 {len(FIELDS)} | 事故数 {len(ids)} | 总判定格 {len(FIELDS)*len(ids)}")
    print(f"{'字段':24s}{'三方一致':>9s}{'三方有异':>9s}{'自动率':>9s}")
    for f in FIELDS:
        c = per_field[f]
        t = c["same"] + c["diff"]
        print(f"{f:24s}{c['same']:9d}{c['diff']:9d}{c['same']/t*100:8.1f}%")
    print(f"\n三方一致（可直接确定）: {len(FIELDS)*len(ids) - n_items}")
    print(f"三方有异（需人工）    : {n_items}")
    print(f"涉及事故条数          : {sum(1 for r in rows if r['need'])} / {len(ids)}")
    print("\n各字段待人工条数：")
    for f, c in sorted(per_field.items(), key=lambda kv: -kv[1]["diff"]):
        if c["diff"]:
            print(f"  {f:24s} {c['diff']:3d}")


if __name__ == "__main__":
    main()
