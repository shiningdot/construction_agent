"""逐条对比 S1'(FewShot) 编码 与 库内既有 caics_causes 编码，并生成可视化 HTML。

注意：库内编码来自项目自己的 Step2 管道，**不是真值**。差异只说明两套标注体系不一致，
不代表 S1 对或错；判断谁更合理需要回原文核对。
"""
from __future__ import annotations

import csv
import html
import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

GOLD = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen"
            "/experiments/golden_standard")

LAYERS = [
    ("M", "管理根基", [f"M{i}" for i in range(1, 6)]),
    ("F", "防线失效", [f"F{i}" for i in range(1, 5)]),
    ("T", "触发条件", [f"T{i}" for i in range(1, 5)]),
    ("H", "人的行为", [f"H{i}" for i in range(1, 5)]),
    ("C", "后果放大", ["C1", "C2"]),
]
CODE_NAME = {
    "M1": "现场监管缺失", "M2": "隐患排查失效", "M3": "安全培训不足", "M4": "制度规程漏洞", "M5": "市场行为违规",
    "F1": "临边洞口防护缺失", "F2": "支撑体系失稳", "F3": "设备安全装置失效", "F4": "个体防护未使用",
    "T1": "设备机具故障", "T2": "物料状态异常", "T3": "地质环境突变", "T4": "气象水文触发",
    "H1": "违反操作规程", "H2": "违规指挥决策", "H3": "风险辨识失败", "H4": "应急行为失当",
    "C1": "救援响应迟缓", "C2": "信息迟报瞒报",
}
ALL_CODES = [c for _, _, cs in LAYERS for c in cs]


def load():
    rows = list(csv.DictReader(open(GOLD / "S1_golden_review_vs_db.csv", encoding="utf-8")))
    text = {json.loads(l)["id"]: json.loads(l)["raw_text"]
            for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}
    meta = {json.loads(l)["id"]: json.loads(l)
            for l in (GOLD / "S1_golden_results.jsonl").open(encoding="utf-8")}
    for r in rows:
        r["s1"] = set(filter(None, r["S1_codes"].split("|")))
        r["db"] = set(filter(None, r["db_codes"].split("|")))
        r["both"] = r["s1"] & r["db"]
        r["j"] = float(r["Jaccard"]) if r["Jaccard"] else 0.0
        r["text"] = text.get(r["id"], "")
        r["meta"] = meta.get(r["id"], {})
    return rows


def report(rows):
    J = sorted(r["j"] for r in rows)
    print("=" * 74)
    print(f"对比样本 {len(rows)} 条 | 库内编码来自主管道 Step2（**非真值**）")
    print(f"Jaccard  中位 {st.median(J):.3f}  均值 {st.mean(J):.3f}  "
          f"p10 {J[int(len(J)*.1)]:.2f}  p90 {J[int(len(J)*.9)]:.2f}")
    print(f"编码数/条  S1 均值 {st.mean([len(r['s1']) for r in rows]):.2f}  "
          f"库内均值 {st.mean([len(r['db']) for r in rows]):.2f}")

    buckets = [("完全一致 (J=1.0)", lambda r: r["j"] == 1.0),
               ("高度一致 (0.8≤J<1)", lambda r: 0.8 <= r["j"] < 1.0),
               ("中等 (0.6≤J<0.8)", lambda r: 0.6 <= r["j"] < 0.8),
               ("较大差异 (0.4≤J<0.6)", lambda r: 0.4 <= r["j"] < 0.6),
               ("严重分歧 (J<0.4)", lambda r: r["j"] < 0.4)]
    print("\n-- 一致度分档 --")
    for name, f in buckets:
        n = sum(1 for r in rows if f(r))
        print(f"  {name:22s} {n:4d}  {n/len(rows)*100:5.1f}%")

    print("\n-- 逐编码对照（19 类）--")
    print(f"  {'编码':5s} {'名称':16s} {'两边都有':>8s} {'仅S1有':>7s} {'仅库有':>7s} {'一致率':>7s}")
    per_code = {}
    for c in ALL_CODES:
        b = sum(1 for r in rows if c in r["both"])
        o1 = sum(1 for r in rows if c in r["s1"] - r["db"])
        o2 = sum(1 for r in rows if c in r["db"] - r["s1"])
        tot = b + o1 + o2
        per_code[c] = (b, o1, o2, b / tot if tot else 0)
        print(f"  {c:5s} {CODE_NAME[c]:16s} {b:8d} {o1:7d} {o2:7d} {b/tot*100 if tot else 0:6.1f}%")

    s1_extra = sum(len(r["s1"] - r["db"]) for r in rows)
    db_extra = sum(len(r["db"] - r["s1"]) for r in rows)
    both = sum(len(r["both"]) for r in rows)
    print(f"\n-- 总体 --  共有编码 {both} | 仅 S1 有 {s1_extra} | 仅库有 {db_extra}")
    print(f"  S1 比库内多编码的事故 {sum(1 for r in rows if len(r['s1'])>len(r['db']))} 条，"
          f"少编码的 {sum(1 for r in rows if len(r['s1'])<len(r['db']))} 条，"
          f"等量 {sum(1 for r in rows if len(r['s1'])==len(r['db']))} 条")

    print("\n-- 分歧最大的 8 条 --")
    for r in sorted(rows, key=lambda x: x["j"])[:8]:
        print(f"  J={r['j']:.2f} {r['title'][:30]}")
        print(f"      S1({len(r['s1'])}): {'|'.join(sorted(r['s1']))}")
        print(f"      库({len(r['db'])}): {'|'.join(sorted(r['db']))}")
    return per_code


def build_html(rows, per_code):
    J = sorted(r["j"] for r in rows)
    payload = []
    for r in rows:
        payload.append({
            "id": r["id"], "t": r["title"], "s1": sorted(r["s1"]), "db": sorted(r["db"]),
            "bonly": sorted(r["s1"] - r["db"]), "donly": sorted(r["db"] - r["s1"]),
            "j": r["j"], "len": len(r["text"]),
            "txt": " ".join(r["text"].split())[:400],
        })
    code_rows = [{"c": c, "n": CODE_NAME[c],
                  "both": per_code[c][0], "o1": per_code[c][1], "o2": per_code[c][2],
                  "agree": round(per_code[c][3] * 100, 1)} for c in ALL_CODES]
    stats = {
        "n": len(rows), "jmed": round(st.median(J), 3), "jmean": round(st.mean(J), 3),
        "ident": sum(1 for r in rows if r["j"] == 1.0),
        "s1avg": round(st.mean([len(r["s1"]) for r in rows]), 2),
        "dbavg": round(st.mean([len(r["db"]) for r in rows]), 2),
        "s1extra": sum(len(r["s1"] - r["db"]) for r in rows),
        "dboost": sum(len(r["db"] - r["s1"]) for r in rows),
        "layers": [{"k": k, "n": name, "cs": cs} for k, name, cs in LAYERS],
    }

    tpl = (Path(__file__).parent / "diff_template.html").read_text(encoding="utf-8")
    out = tpl.replace("/*__DATA__*/", json.dumps(payload, ensure_ascii=False)) \
             .replace("/*__CODES__*/", json.dumps(code_rows, ensure_ascii=False)) \
             .replace("/*__STATS__*/", json.dumps(stats, ensure_ascii=False))
    (GOLD / "S1_vs_db_diff.html").write_text(out, encoding="utf-8")
    print(f"\nHTML → {GOLD}/S1_vs_db_diff.html  ({len(out)//1024} KB)")


if __name__ == "__main__":
    rows = load()
    per_code = report(rows)
    build_html(rows, per_code)
