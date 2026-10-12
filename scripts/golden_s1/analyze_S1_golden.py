"""S1'(FewShot + 基础特征) 结果分析，并生成人工审核表。

只写两个审核用的表，其余统计打印到 stdout（目录里不落过程文件）：
  experiments/golden_standard/S1_golden_review.csv        —— 逐条：编码 + 基础字段 + 原文节选
  experiments/golden_standard/S1_golden_review_vs_db.csv  —— 与库内既有 caics_causes 的逐条对照（差异信号，非真值）
"""
from __future__ import annotations

import csv
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

from prompt_build import _load_step1_tool  # noqa: E402

CAICS_ORDER = ([f"M{i}" for i in range(1, 6)] + [f"F{i}" for i in range(1, 5)]
               + [f"T{i}" for i in range(1, 5)] + [f"H{i}" for i in range(1, 5)] + ["C1", "C2"])


def main():
    rows = [json.loads(l) for l in (GOLD / "S1_golden_results.jsonl").open(encoding="utf-8")]
    raw = {json.loads(l)["id"]: json.loads(l)["raw_text"]
           for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}

    ok = [r for r in rows if not r.get("error") and not r.get("parse_error")]
    failed = [r for r in rows if r.get("error") or r.get("parse_error")]
    schema_bad = [r for r in ok if r.get("schema_errors")]

    code_cnt, per_case = Counter(), []
    for r in ok:
        codes = r["parsed"].get("codes") or []
        per_case.append(len(codes))
        code_cnt.update(codes)

    props = list(_load_step1_tool()["input_schema"]["properties"])
    fill = defaultdict(int)
    for r in ok:
        for f in props:
            v = r["parsed"].get(f)
            if v is None or (isinstance(v, str) and v.strip() in ("", "未提及", "未知", "null")):
                continue
            fill[f] += 1

    print(f"样本 {len(rows)} | 成功 {len(ok)} | 解析失败 {len(failed)} | schema 违规 {len(schema_bad)}")
    if per_case:
        print(f"CAICS 编码数/条：均值 {sum(per_case)/len(per_case):.2f}，"
              f"min {min(per_case)}，max {max(per_case)}")
    print(f"总 tokens：{sum(r.get('prompt_tokens',0) for r in rows)} prompt + "
          f"{sum(r.get('completion_tokens',0) for r in rows)} completion")
    print(f"耗时均值 {sum(r.get('elapsed_sec',0) for r in rows)/max(1,len(rows)):.1f} s/条")

    print("\n== CAICS 编码分布（19 类，按层）==")
    for c in CAICS_ORDER:
        n = code_cnt.get(c, 0)
        print(f"  {c:3s} {n:4d}  ({n/max(1,len(ok))*100:5.1f}%)")

    print("\n== 基础特征字段填充率 ==")
    for f in props:
        print(f"  {f:26s} {fill.get(f,0):4d}  ({fill.get(f,0)/max(1,len(ok))*100:5.1f}%)")

    print("\n== 事故类型分布 ==")
    for k, v in Counter((r.get("parsed") or {}).get("accident_type") for r in ok).most_common():
        print(f"  {k} {v}")
    print("\n== 严重程度分布 ==")
    for k, v in Counter((r.get("parsed") or {}).get("severity") for r in ok).most_common():
        print(f"  {k} {v}")

    if failed or schema_bad:
        print("\n== 异常清单 ==")
        for r in failed:
            print(f"  ❌ {r['id'][:8]} {r['title'][:30]} | {r.get('error') or r.get('parse_error')}")
        for r in schema_bad:
            print(f"  ⚠ {r['id'][:8]} {r['title'][:30]} | {'; '.join(r['schema_errors'])}")

    # 人工审核表
    with (GOLD / "S1_golden_review.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "tier", "source", "text_len", "codes", "n_codes",
                    "accident_type", "severity", "deaths", "injuries", "date", "location",
                    "city", "district", "project_type", "work_task", "equipment_involved",
                    "worker_type", "direct_cause", "原文节选(前600字)"])
        for r in rows:
            p = r.get("parsed") or {}
            txt = re.sub(r"\s+", " ", raw.get(r["id"], ""))[:600]
            w.writerow([r["id"], r["title"], r.get("tier"), r.get("source_name"), r.get("text_len"),
                        "|".join(p.get("codes") or []), len(p.get("codes") or []),
                        p.get("accident_type"), p.get("severity"), p.get("deaths"), p.get("injuries"),
                        p.get("date"), p.get("location"), p.get("city"), p.get("district"),
                        p.get("project_type"), p.get("work_task"), p.get("equipment_involved"),
                        p.get("worker_type"), p.get("direct_cause"), txt])

    # 与库内既有标注对照
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db_codes = defaultdict(set)
    for aid, code in con.execute("select accident_id, code from caics_causes"):
        db_codes[aid].add(code)
    con.close()
    with (GOLD / "S1_golden_review_vs_db.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "S1_codes", "db_codes", "仅S1有", "仅库有", "Jaccard"])
        for r in rows:
            s1 = set((r.get("parsed") or {}).get("codes") or [])
            db = db_codes.get(r["id"], set())
            union = s1 | db
            w.writerow([r["id"], r["title"], "|".join(sorted(s1)), "|".join(sorted(db)),
                        "|".join(sorted(s1 - db)), "|".join(sorted(db - s1)),
                        round(len(s1 & db) / len(union), 3) if union else ""])

    print(f"\n产物 → {GOLD}/S1_golden_review.csv, {GOLD}/S1_golden_review_vs_db.csv")


if __name__ == "__main__":
    main()
