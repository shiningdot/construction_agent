"""两套编码结果的逐条对照分析与可视化 HTML。

默认 A = flash 结果（golden_standard/S1_golden_results.jsonl），B = 库内既有 caics_causes。
B 也可指向另一份结果 jsonl（如 pro/），从而做模型间对照。

注意：库内编码来自主管道 Step2，**不是真值**；任何差异只说明两套标注不一致。

用法：
    python3 scripts/golden_s1/analyze_vs_db.py                    # flash vs 库内
    python3 scripts/golden_s1/analyze_vs_db.py \
        --a experiments/golden_standard/pro/S1_golden_results.jsonl \
        --a-name "deepseek-pro" --out experiments/golden_standard/pro/S1_vs_db_diff.html \
        --csv-out experiments/golden_standard/pro/S1_golden_review_vs_db.csv
    python3 scripts/golden_s1/analyze_vs_db.py \
        --b experiments/golden_standard/pro/S1_golden_results.jsonl \
        --a-name "flash" --b-name "pro" \
        --out experiments/golden_standard/flash_vs_pro.html
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen")
GOLD = ROOT / "experiments/golden_standard"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"

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


def codes_from_results(path: Path) -> dict[str, set[str]]:
    out = {}
    for line in path.open(encoding="utf-8"):
        r = json.loads(line)
        out[r["id"]] = set((r.get("parsed") or {}).get("codes") or [])
    return out


def codes_from_db(ids: list[str]) -> dict[str, set[str]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = ",".join("?" * len(ids))
    out = defaultdict(set)
    for aid, code in con.execute(
            f"select accident_id, code from caics_causes where accident_id in ({q})", ids):
        out[aid].add(code)
    con.close()
    return out


def load(ids: list[str], a_path: Path, b_arg: str):
    A = codes_from_results(a_path)
    B = codes_from_db(ids) if b_arg == "db" else codes_from_results(Path(b_arg))
    return A, B


def report(rows, a_name, b_name):
    J = sorted(r["j"] for r in rows)
    print("=" * 74)
    print(f"{a_name}  vs  {b_name} | 对比 {len(rows)} 条")
    print(f"Jaccard  中位 {st.median(J):.3f}  均值 {st.mean(J):.3f}  "
          f"p10 {J[int(len(J)*.1)]:.2f}  p90 {J[int(len(J)*.9)]:.2f}")
    print(f"编码数/条  {a_name} {st.mean([len(r['a']) for r in rows]):.2f}  "
          f"{b_name} {st.mean([len(r['b']) for r in rows]):.2f}")

    print("\n-- 一致度分档 --")
    for nm, f in [("完全一致 (J=1.0)", lambda r: r["j"] == 1.0),
                  ("高度一致 (0.8≤J<1)", lambda r: 0.8 <= r["j"] < 1.0),
                  ("中等 (0.6≤J<0.8)", lambda r: 0.6 <= r["j"] < 0.8),
                  ("较大差异 (0.4≤J<0.6)", lambda r: 0.4 <= r["j"] < 0.6),
                  ("严重分歧 (J<0.4)", lambda r: r["j"] < 0.4)]:
        n = sum(1 for r in rows if f(r))
        print(f"  {nm:22s} {n:4d}  {n/len(rows)*100:5.1f}%")

    print(f"\n-- 逐编码对照（19 类）--")
    per_code = {}
    for c in ALL_CODES:
        b = sum(1 for r in rows if c in r["both"])
        o1 = sum(1 for r in rows if c in r["a"] - r["b"])
        o2 = sum(1 for r in rows if c in r["b"] - r["a"])
        tot = b + o1 + o2
        per_code[c] = (b, o1, o2, b / tot if tot else 0)
        print(f"  {c:5s} {CODE_NAME[c]:16s} 两边都有 {b:4d} | 仅{a_name} {o1:4d} | "
              f"仅{b_name} {o2:4d} | 一致率 {b/tot*100 if tot else 0:5.1f}%")

    print(f"\n-- 总体 --  共有 {sum(len(r['both']) for r in rows)} | "
          f"仅{a_name} {sum(len(r['a']-r['b']) for r in rows)} | "
          f"仅{b_name} {sum(len(r['b']-r['a']) for r in rows)}")
    print(f"  {a_name} 编码更多 {sum(1 for r in rows if len(r['a'])>len(r['b']))} 条，"
          f"更少 {sum(1 for r in rows if len(r['a'])<len(r['b']))} 条，"
          f"等量 {sum(1 for r in rows if len(r['a'])==len(r['b']))} 条")
    return per_code


def build_html(rows, per_code, a_name, b_name, out_path: Path):
    J = sorted(r["j"] for r in rows)
    payload = [{"id": r["id"], "t": r["title"], "s1": sorted(r["a"]), "db": sorted(r["b"]),
                "bonly": sorted(r["a"] - r["b"]), "donly": sorted(r["b"] - r["a"]),
                "j": r["j"], "len": len(r["text"]),
                "txt": " ".join(r["text"].split())[:400]} for r in rows]
    code_rows = [{"c": c, "n": CODE_NAME[c], "both": per_code[c][0], "o1": per_code[c][1],
                  "o2": per_code[c][2], "agree": round(per_code[c][3] * 100, 1)} for c in ALL_CODES]
    stats = {"n": len(rows), "jmed": round(st.median(J), 3), "jmean": round(st.mean(J), 3),
             "ident": sum(1 for r in rows if r["j"] == 1.0),
             "s1avg": round(st.mean([len(r["a"]) for r in rows]), 2),
             "dbavg": round(st.mean([len(r["b"]) for r in rows]), 2),
             "s1extra": sum(len(r["a"] - r["b"]) for r in rows),
             "dboost": sum(len(r["b"] - r["a"]) for r in rows),
             "aName": a_name, "bName": b_name,
             "layers": [{"k": k, "n": n, "cs": cs} for k, n, cs in LAYERS]}

    tpl = (Path(__file__).parent / "diff_template.html").read_text(encoding="utf-8")
    out = (tpl.replace("/*__DATA__*/", json.dumps(payload, ensure_ascii=False))
              .replace("/*__CODES__*/", json.dumps(code_rows, ensure_ascii=False))
              .replace("/*__STATS__*/", json.dumps(stats, ensure_ascii=False))
              .replace("__ANAME__", a_name).replace("__BNAME__", b_name))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out, encoding="utf-8")
    print(f"\nHTML → {out_path}  ({len(out)//1024} KB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default=str(GOLD / "S1_golden_results.jsonl"))
    ap.add_argument("--b", default="db", help="db 或另一份结果 jsonl 路径")
    ap.add_argument("--a-name", default="S1′")
    ap.add_argument("--b-name", default="库内")
    ap.add_argument("--out", default=str(GOLD / "S1_vs_db_diff.html"))
    ap.add_argument("--csv-out", default=str(GOLD / "S1_golden_review_vs_db.csv"))
    args = ap.parse_args()

    cand = [json.loads(l) for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")]
    ids = [c["id"] for c in cand]
    title = {c["id"]: c["title"] for c in cand}
    text = {c["id"]: c["raw_text"] for c in cand}

    A, B = load(ids, Path(args.a), args.b)
    rows = []
    for i in ids:
        a, b = A.get(i, set()), B.get(i, set())
        u = a | b
        rows.append({"id": i, "title": title[i], "a": a, "b": b, "both": a & b,
                     "j": len(a & b) / len(u) if u else 0.0, "text": text[i]})

    per_code = report(rows, args.a_name, args.b_name)
    build_html(rows, per_code, args.a_name, args.b_name, Path(args.out))

    if args.csv_out:
        with Path(args.csv_out).open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "title", f"{args.a_name}_codes", f"{args.b_name}_codes",
                        f"仅{args.a_name}有", f"仅{args.b_name}有", "Jaccard"])
            for r in rows:
                w.writerow([r["id"], r["title"], "|".join(sorted(r["a"])), "|".join(sorted(r["b"])),
                            "|".join(sorted(r["a"] - r["b"])), "|".join(sorted(r["b"] - r["a"])),
                            round(r["j"], 3)])
        print(f"CSV  → {args.csv_out}")


if __name__ == "__main__":
    main()
