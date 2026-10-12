"""S1'(FewShot + 基础特征) 在 300 条 golden 候选上的结果分析与人工审核表。

产出：
  S1_result/S1_golden_v2_analysis.md     —— 编码分布 / 字段填充率 / 异常清单
  S1_result/S1_golden_v2_review.csv      —— 人工审核用（逐条：编码 + 基础字段 + 原文节选）
  S1_result/S1_golden_v2_vs_db.csv       —— 与库内既有 caics_causes 的逐条对照（差异信号，非真值）
  S1_result/system_prompt_snapshot.txt     —— 提示词快照（复现用）
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
RES = GOLD / "S1_result"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from prompt_build import build_system_prompt, _load_step1_tool  # noqa: E402

CAICS_ORDER = ([f"M{i}" for i in range(1, 6)] + [f"F{i}" for i in range(1, 5)]
               + [f"T{i}" for i in range(1, 5)] + [f"H{i}" for i in range(1, 5)] + ["C1", "C2"])


def main():
    rows = [json.loads(l) for l in (RES / "S1_golden_v2.jsonl").open(encoding="utf-8")]
    raw = {json.loads(l)["id"]: json.loads(l)["raw_text"]
           for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}

    ok = [r for r in rows if not r.get("error") and not r.get("parse_error")]
    failed = [r for r in rows if r.get("error") or r.get("parse_error")]
    schema_bad = [r for r in ok if r.get("schema_errors")]

    # 编码分布
    code_cnt = Counter()
    per_case = []
    for r in ok:
        codes = r["parsed"].get("codes") or []
        per_case.append(len(codes))
        code_cnt.update(codes)

    # 字段填充率
    props = list(_load_step1_tool()["input_schema"]["properties"])
    fill = defaultdict(int)
    for r in ok:
        p = r["parsed"]
        for f in props:
            v = p.get(f)
            if v is None or (isinstance(v, str) and v.strip() in ("", "未提及", "未知", "null")):
                continue
            fill[f] += 1

    tier_cnt = Counter(r["tier"] for r in rows)
    type_cnt = Counter((r.get("parsed") or {}).get("accident_type") for r in ok)
    sev_cnt = Counter((r.get("parsed") or {}).get("severity") for r in ok)

    L = ["# S1'(FewShot + 基础特征) 在 300 条 Golden 候选上的结果", "",
         f"- 模型：`{json.loads((RES / 'S1_golden_v2_results.json').read_text())['summary']['model']}`",
         f"- 成功 {len(ok)}/{len(rows)}，JSON 解析失败 {len(failed)}，schema 违规 {len(schema_bad)}",
         f"- CAICS 编码数/条：均值 {sum(per_case)/max(1,len(per_case)):.2f}，"
         f"min {min(per_case) if per_case else 0}，max {max(per_case) if per_case else 0}", "",
         "## CAICS 编码分布（19 类，按 5 层）", "", "| 层 | 编码 | 命中条数 | 占比 |", "|---|---|---:|---:|"]
    for c in CAICS_ORDER:
        L.append(f"| {c[0]} | {c} | {code_cnt.get(c,0)} | {code_cnt.get(c,0)/max(1,len(ok))*100:.1f}% |")

    L += ["", "## 基础特征字段填充率", "", "| 字段 | 填充条数 | 填充率 |", "|---|---:|---:|"]
    for f in props:
        L.append(f"| {f} | {fill.get(f,0)} | {fill.get(f,0)/max(1,len(ok))*100:.1f}% |")

    L += ["", "## 事故类型 / 严重程度分布", "", "| 事故类型 | 条数 |", "|---|---:|"]
    for k, v in type_cnt.most_common():
        L.append(f"| {k} | {v} |")
    L += ["", "| 严重程度 | 条数 |", "|---|---:|"]
    for k, v in sev_cnt.most_common():
        L.append(f"| {k} | {v} |")

    if schema_bad or failed:
        L += ["", "## 异常清单", "", "| id | 标题 | 问题 |", "|---|---|---|"]
        for r in failed:
            L.append(f"| {r['id'][:8]} | {r['title'][:30]} | {r.get('error') or r.get('parse_error')} |")
        for r in schema_bad:
            L.append(f"| {r['id'][:8]} | {r['title'][:30]} | {'; '.join(r['schema_errors'])} |")
    (RES / "S1_golden_v2_analysis.md").write_text("\n".join(L), encoding="utf-8")

    # 人工审核表
    with (RES / "S1_golden_v2_review.csv").open("w", newline="", encoding="utf-8") as f:
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

    # 与库内既有标注对照（差异信号）
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db_codes = defaultdict(set)
    for aid, code in con.execute("select accident_id, code from caics_causes"):
        db_codes[aid].add(code)
    with (RES / "S1_golden_v2_vs_db.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "S1_codes", "db_codes", "仅S1有", "仅库有", "Jaccard"])
        for r in rows:
            s1 = set((r.get("parsed") or {}).get("codes") or [])
            db = db_codes.get(r["id"], set())
            inter = s1 & db
            union = s1 | db
            w.writerow([r["id"], r["title"], "|".join(sorted(s1)), "|".join(sorted(db)),
                        "|".join(sorted(s1 - db)), "|".join(sorted(db - s1)),
                        round(len(inter) / len(union), 3) if union else ""])
    con.close()

    # 提示词快照
    (RES / "system_prompt_snapshot.txt").write_text(build_system_prompt(), encoding="utf-8")

    print(f"分析完成 → {RES}")
    print(f"成功 {len(ok)}/{len(rows)} | 编码均值 {sum(per_case)/max(1,len(per_case)):.2f}")
    print("编码命中率最低的 5 类:",
          [(c, code_cnt.get(c, 0)) for c in sorted(CAICS_ORDER, key=lambda x: code_cnt.get(x, 0))[:5]])


if __name__ == "__main__":
    main()
