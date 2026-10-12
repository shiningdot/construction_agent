"""300 条事故的 CAICS 致因编码三方比对：flash / pro / 库内 Step2。

与基础特征不同，致因的"值"是一个编码集合（19 类中选若干），故比对分两个层面：
  集合层面 —— 三方给出的编码集合是否相同（决定逐条一致度）
  编码层面 —— 19 个类各自被哪几方选中（决定每个类的可信度，以及共识集怎么取）
"""
from __future__ import annotations

import json
import statistics as st
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen")
GOLD = ROOT / "experiments/golden_standard"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"

CODE_NAME = {
    "M1": "现场监管缺失", "M2": "隐患排查失效", "M3": "安全培训不足",
    "M4": "制度规程漏洞", "M5": "市场行为违规",
    "F1": "临边洞口防护缺失", "F2": "支撑体系失稳", "F3": "设备安全装置失效",
    "F4": "个体防护未使用",
    "T1": "设备机具故障", "T2": "物料状态异常", "T3": "地质环境突变", "T4": "气象水文触发",
    "H1": "违反操作规程", "H2": "违规指挥决策", "H3": "风险辨识失败", "H4": "应急行为失当",
    "C1": "救援响应迟缓", "C2": "信息迟报瞒报",
}
LAYERS = [("M", "管理根基", [f"M{i}" for i in range(1, 6)]),
          ("F", "防线失效", [f"F{i}" for i in range(1, 5)]),
          ("T", "触发条件", [f"T{i}" for i in range(1, 5)]),
          ("H", "人的行为", [f"H{i}" for i in range(1, 5)]),
          ("C", "后果放大", ["C1", "C2"])]
ALL_CODES = [c for _, _, cs in LAYERS for c in cs]


def codes_of(path: Path) -> dict[str, set]:
    out = {}
    for line in path.open(encoding="utf-8"):
        r = json.loads(line)
        out[r["id"]] = set((r.get("parsed") or {}).get("codes") or [])
    return out


def db_codes(ids) -> dict[str, set]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = ",".join("?" * len(ids))
    out = defaultdict(set)
    for aid, code in con.execute(
            f"select accident_id, code from caics_causes where accident_id in ({q})", list(ids)):
        out[aid].add(code)
    con.close()
    return out


def jac(a, b):
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def main():
    cand = {json.loads(l)["id"]: json.loads(l)
            for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}
    ids = list(cand)
    F = codes_of(GOLD / "S1_golden_results.jsonl")
    P = codes_of(GOLD / "pro/S1_golden_results.jsonl")
    D = db_codes(ids)

    print("=" * 92)
    print(f"CAICS 致因三方比对 | {len(ids)} 条 | flash / pro / 库内 Step2（库内来自本项目管道，非真值）")
    print("=" * 92)
    for nm, S in [("flash", F), ("pro", P), ("库内", D)]:
        n = [len(S.get(i, set())) for i in ids]
        print(f"  {nm:6s} 编码数/条 均值 {st.mean(n):.2f}  中位 {st.median(n):.0f}  "
              f"min {min(n)}  max {max(n)}  合计 {sum(n)}")

    # ── 集合层面 ──
    cls = Counter()
    pairs = Counter()
    for i in ids:
        f, p, d = F.get(i, set()), P.get(i, set()), D.get(i, set())
        if f == p == d:
            cls["三方一致"] += 1
        else:
            eq = []
            if f == p: eq.append("flash=pro")
            if f == d: eq.append("flash=库内")
            if p == d: eq.append("pro=库内")
            if eq:
                cls["两方一致"] += 1
                pairs[eq[0]] += 1
            else:
                cls["三方互异"] += 1

    print("\n-- 集合层面：逐条编码集合是否相同 --")
    for k in ["三方一致", "两方一致", "三方互异"]:
        print(f"  {k:8s} {cls[k]:3d} 条  {cls[k]/len(ids)*100:5.1f}%")
    if pairs:
        print("    两方一致的分组:", dict(pairs))

    for nm, X, Y in [("flash-pro", F, P), ("flash-库内", F, D), ("pro-库内", P, D)]:
        v = [jac(X.get(i, set()), Y.get(i, set())) for i in ids]
        print(f"  逐条 Jaccard {nm:10s} 中位 {st.median(v):.3f}  均值 {st.mean(v):.3f}")

    # ── 补集方向 ──
    print("\n-- 编码多寡方向 --")
    for nm, X, Y in [("flash vs 库内", F, D), ("pro vs 库内", P, D), ("flash vs pro", F, P)]:
        more = sum(1 for i in ids if len(X.get(i, set())) > len(Y.get(i, set())))
        less = sum(1 for i in ids if len(X.get(i, set())) < len(Y.get(i, set())))
        only_x = sum(len(X.get(i, set()) - Y.get(i, set())) for i in ids)
        only_y = sum(len(Y.get(i, set()) - X.get(i, set())) for i in ids)
        print(f"  {nm:14s} 前者更多 {more:3d} 条 / 更少 {less:3d} 条 | "
              f"仅前者有 {only_x:4d} 个编码 / 仅后者有 {only_y:4d} 个")

    # ── 编码层面：19 类各自被几方选中 ──
    print("\n-- 编码层面：19 类各自被几方选中（三方 = 3 票全中）--")
    print(f"  {'编码':5s}{'名称':18s}{'三方都选':>9s}{'两方选':>8s}{'仅一方选':>9s}{'至少一方':>9s}")
    vote = {}
    for c in ALL_CODES:
        v3 = v2 = v1 = 0
        for i in ids:
            n = sum(1 for S in (F.get(i, set()), P.get(i, set()), D.get(i, set())) if c in S)
            v3 += n == 3
            v2 += n == 2
            v1 += n == 1
        vote[c] = (v3, v2, v1)
        print(f"  {c:5s}{CODE_NAME[c]:18s}{v3:9d}{v2:8d}{v1:9d}{v3+v2+v1:9d}")

    tot3 = sum(v[0] for v in vote.values())
    tot2 = sum(v[1] for v in vote.values())
    tot1 = sum(v[2] for v in vote.values())
    print(f"\n  编码-事故格合计：三方都选 {tot3} | 两方选 {tot2} | 仅一方选 {tot1} | 总计 {tot3+tot2+tot1}")

    # ── 共识集（逐编码 2/3 多数）──
    print("\n-- 共识集（逐编码取「三方中≥2 方选中」）--")
    C = {}
    for i in ids:
        s = set()
        for c in ALL_CODES:
            n = sum(1 for S in (F.get(i, set()), P.get(i, set()), D.get(i, set())) if c in S)
            if n >= 2:
                s.add(c)
        C[i] = s
    for nm, X in [("flash", F), ("pro", P), ("库内", D)]:
        v = [jac(C[i], X.get(i, set())) for i in ids]
        print(f"  共识集 vs {nm:6s}  Jaccard 中位 {st.median(v):.3f}  均值 {st.mean(v):.3f}")
    print(f"  共识集大小 均值 {st.mean([len(C[i]) for i in ids]):.2f} 编码/条 "
          f"（合计 {sum(len(C[i]) for i in ids)}）")

    # ── 争点定位 ──
    print("\n-- 争议编码归属（仅一方选中的，按类统计）--")
    only = Counter()
    for i in ids:
        for c in ALL_CODES:
            n = sum(1 for S in (F.get(i, set()), P.get(i, set()), D.get(i, set())) if c in S)
            if n == 1:
                only[c] += 1
    for c, n in only.most_common(10):
        print(f"  {c} {CODE_NAME[c]:18s} {n:3d}")

    # 落盘供后续 HTML / GT 用
    out = [{"id": i, "title": cand[i]["title"], "flash": sorted(F.get(i, set())),
            "pro": sorted(P.get(i, set())), "db": sorted(D.get(i, set())),
            "consensus": sorted(C[i])} for i in ids]
    p = GOLD / "gt" / "causes_threeway.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n明细 → {p}")


if __name__ == "__main__":
    main()
