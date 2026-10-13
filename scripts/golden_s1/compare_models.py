"""多模型在 golden 300 条上的能力对照。

对每个模型给出：编码数/条、与库内 Step2 的一致度、基础特征填充率；
以及模型两两之间的逐条 Jaccard 矩阵与逐类编码包含率。

只对比「所有指定模型都跑过的 id」，因此可安全地在某个模型还在跑时先看已完成部分。

用法：
    python3 scripts/golden_s1/compare_models.py                       # 全部已就绪的模型
    python3 scripts/golden_s1/compare_models.py --models flash,pro    # 指定子集
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # 工作树根：按脚本位置推导，任何 worktree 都适用（勿写死绝对路径）
GOLD = ROOT / "experiments/golden_standard"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))
from analyze_causes_threeway import ALL_CODES, CODE_NAME  # noqa: E402

# 模型 → 结果文件（相对 GOLD）
MODELS = {
    "flash": "flash/S1_golden_results.jsonl",
    "pro": "pro/S1_golden_results.jsonl",
    "glm-5.2": "glm5.2/S1_golden_results.jsonl",
    "hunyuan": "hy3/S1_golden_results.jsonl",
}


def load(path: Path):
    """返回 {id: parsed}，跳过解析失败的条目。"""
    out = {}
    if not path.exists():
        return out
    for line in path.open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("error") or r.get("parse_error"):
            continue
        out[r["id"]] = r.get("parsed") or {}
    return out


def jac(a, b):
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="", help="逗号分隔的模型名；留空=全部")
    args = ap.parse_args()

    want = [m.strip() for m in args.models.split(",") if m.strip()] or list(MODELS)
    data, missing = {}, []
    for m in want:
        d = load(GOLD / MODELS[m])
        if not d:
            missing.append(m)
        else:
            data[m] = d
    if missing:
        print(f"跳过（无结果）: {missing}")
    if len(data) < 2:
        print("至少需要两个模型的结果才能对照")
        return

    cand = {json.loads(l)["id"]: json.loads(l)
            for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}
    ids = [i for i in cand if all(i in data[m] for m in data)]
    print(f"可比事故数 {len(ids)} / {len(cand)}（取所有模型都跑过的）\n")

    # 库内编码
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = ",".join("?" * len(ids))
    D = defaultdict(set)
    for aid, code in con.execute(
            f"select accident_id, code from caics_causes where accident_id in ({q})", ids):
        D[aid].add(code)
    con.close()

    print("=" * 88)
    print("一、各模型总体")
    print("=" * 88)
    print(f"{'模型':10s}{'编码数/条':>9s}{'中位':>6s}{'min':>5s}{'max':>5s}"
          f"{'vs库内Jaccard中位':>18s}{'完全一致':>9s}")
    sets = {}
    for m, d in data.items():
        n = [len(d[i].get("codes") or []) for i in ids]
        sets[m] = [set(d[i].get("codes") or []) for i in ids]
        j = [jac(s, D.get(i, set())) for s, i in zip(sets[m], ids)]
        ident = sum(1 for s, i in zip(sets[m], ids) if s == D.get(i, set()))
        print(f"{m:10s}{st.mean(n):9.2f}{st.median(n):6.0f}{min(n):5d}{max(n):5d}"
              f"{st.median(j):18.3f}{ident:9d}")
    n_db = [len(D.get(i, set())) for i in ids]
    print(f"{'库内':10s}{st.mean(n_db):9.2f}{st.median(n_db):6.0f}"
          f"{min(n_db):5d}{max(n_db):5d}{'—':>18s}{'—':>9s}")

    print("\n" + "=" * 88)
    print("二、模型两两一致度（逐条 Jaccard 中位 / 完全一致的条数）")
    print("=" * 88)
    names = list(data)
    print(f"{'':12s}" + "".join(f"{m:>14s}" for m in names))
    for a in names:
        row = f"{a:12s}"
        for b in names:
            if a == b:
                row += f"{'—':>14s}"
            else:
                v = [jac(x, y) for x, y in zip(sets[a], sets[b])]
                idn = sum(1 for x, y in zip(sets[a], sets[b]) if x == y)
                row += f"{st.median(v):>8.3f}/{idn:<5d}"
        print(row)
    print("（格式：Jaccard 中位 / 完全一致条数）")

    print("\n" + "=" * 88)
    print("三、19 类编码：各模型的包含率（%）")
    print("=" * 88)
    print(f"{'编码':6s}{'名称':18s}" + "".join(f"{m:>10s}" for m in names) + f"{'库内':>10s}")
    for c in ALL_CODES:
        row = f"{c:6s}{CODE_NAME[c]:18s}"
        for m in names:
            row += f"{sum(1 for s in sets[m] if c in s)/len(ids)*100:10.1f}"
        row += f"{sum(1 for i in ids if c in D.get(i,set()))/len(ids)*100:10.1f}"
        print(row)

    print("\n" + "=" * 88)
    print("四、基础特征填充率（%）")
    print("=" * 88)
    FIELDS = ["accident_type", "severity", "deaths", "injuries", "trapped_count",
              "direct_economic_loss", "fall_height", "date", "accident_time",
              "location", "city", "project_type", "collapse_subtype"]
    print(f"{'字段':24s}" + "".join(f"{m:>10s}" for m in names))
    for f in FIELDS:
        row = f"{f:24s}"
        for m in names:
            filled = sum(1 for i in ids
                         if data[m][i].get(f) not in (None, "", "未知", "未提及", "null"))
            row += f"{filled/len(ids)*100:10.1f}"
        print(row)


if __name__ == "__main__":
    main()
