"""合并多位专家的审核导出，输出共识与分歧。

审核页导出的是每位专家一个 JSON（schema `caics-expert-review-v1`）。本脚本：

1. 校验并读取若干导出文件
2. 统计每位专家：已确认条数、修改条数、存疑条数
3. 在**至少两位专家都审过**的条目上，计算两两 Jaccard 一致率、逐类一致率
4. 对每条事故做多数投票，标出仍需人工裁定的类
5. 写出 `合并结果.jsonl`（逐条）与 `合并报告.md`（汇总）

用法：
    python3 scripts/golden_s1/merge_expert_reviews.py a.json b.json c.json
    python3 scripts/golden_s1/merge_expert_reviews.py *.json --outdir /path/to/out
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "experiments/golden_standard/expert_review"
ALL_CODES = [f"M{i}" for i in range(1, 6)] + [f"F{i}" for i in range(1, 5)] + \
            [f"T{i}" for i in range(1, 5)] + [f"H{i}" for i in range(1, 5)] + ["C1", "C2"]
CODE_NAMES = {
    "M1": "现场监管缺失", "M2": "隐患排查失效", "M3": "安全培训不足",
    "M4": "制度规程漏洞", "M5": "市场行为违规",
    "F1": "临边洞口防护缺失", "F2": "支撑体系失稳", "F3": "设备安全装置失效",
    "F4": "个体防护未使用",
    "T1": "设备机具故障", "T2": "物料状态异常", "T3": "地质环境突变", "T4": "气象水文触发",
    "H1": "违反操作规程", "H2": "违规指挥决策", "H3": "风险辨识失败", "H4": "应急行为失当",
    "C1": "救援响应迟缓", "C2": "信息迟报瞒报",
}


def load_one(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema") != "caics-expert-review-v1":
        sys.exit(f"{path} 不是审核导出文件（schema 不匹配）")
    expert = (obj.get("expert") or path.stem).strip()
    items = {}
    for it in obj.get("items") or []:
        aid = it.get("accident_id")
        if not aid:
            continue
        items[aid] = {
            "codes": set(it.get("reviewed_codes") or []),
            "uncertain": set(it.get("uncertain") or []),
            "note": it.get("note") or "",
            "status": it.get("status") or "edited",
            "base": set(it.get("base_gt_codes") or []),
        }
    return {"expert": expert, "path": str(path), "items": items, "raw": obj}


def jaccard(a: set, b: set) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--outdir", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    experts = [load_one(Path(f)) for f in args.files]
    if len(experts) < 2:
        print("⚠️  只有一位专家的文件，仍会输出逐条结果，但无法计算专家间一致率。")

    # 同名专家合并提示
    names = Counter(e["expert"] for e in experts)
    dup = [n for n, c in names.items() if c > 1]
    if dup:
        print(f"⚠️  专家重名：{dup}，将按文件顺序分别计为 {dup[0]}-1 / -2 …")
        seen = Counter()
        for e in experts:
            seen[e["expert"]] += 1
            if seen[e["expert"]] > 1:
                e["expert"] = f"{e['expert']}-{seen[e['expert']]}"

    all_ids = sorted({aid for e in experts for aid in e["items"]})
    print(f"专家 {len(experts)} 位 | 共涉及 {len(all_ids)} 条事故")

    # ---------- 逐专家统计 ----------
    per_expert = []
    for e in experts:
        codes_n = [len(v["codes"]) for v in e["items"].values()]
        changed = sum(1 for v in e["items"].values() if v["codes"] != v["base"])
        reviewed = sum(1 for v in e["items"].values() if v["status"] == "reviewed")
        unc = sum(1 for v in e["items"].values() if v["uncertain"])
        per_expert.append({
            "expert": e["expert"],
            "touched": len(e["items"]),
            "reviewed": reviewed,
            "changed": changed,
            "uncertain": unc,
            "avg_codes": round(sum(codes_n) / len(codes_n), 2) if codes_n else 0.0,
        })

    # ---------- 两两一致率 ----------
    pairs = []
    for a, b in combinations(experts, 2):
        overlap = sorted(set(a["items"]) & set(b["items"]))
        if not overlap:
            pairs.append({"a": a["expert"], "b": b["expert"], "n": 0, "mean_jaccard": None,
                          "exact": None})
            continue
        js = [jaccard(a["items"][i]["codes"], b["items"][i]["codes"]) for i in overlap]
        exact = sum(1 for i in overlap
                    if a["items"][i]["codes"] == b["items"][i]["codes"]) / len(overlap)
        pairs.append({"a": a["expert"], "b": b["expert"], "n": len(overlap),
                      "mean_jaccard": sum(js) / len(js), "exact": exact})

    # ---------- 逐类一致率（共同条目上，专家间对"该类是否成立"的一致比例） ----------
    overlap_all = set.intersection(*[set(e["items"]) for e in experts]) if experts else set()
    per_class = {}
    for c in ALL_CODES:
        if not overlap_all:
            per_class[c] = None
            continue
        agree = 0
        for aid in overlap_all:
            vals = {c in e["items"][aid]["codes"] for e in experts}
            if len(vals) == 1:
                agree += 1
        per_class[c] = agree / len(overlap_all)

    # ---------- 多数投票与分歧 ----------
    merged = []
    for aid in all_ids:
        voters = [(e["expert"], e["items"][aid]) for e in experts if aid in e["items"]]
        n = len(voters)
        counts = Counter()
        for _, v in voters:
            for c in v["codes"]:
                counts[c] += 1
        consensus = {c for c in ALL_CODES if counts[c] * 2 > n}
        disputed = {c for c in ALL_CODES if 0 < counts[c] < n}
        base = voters[0][1]["base"] if voters else set()
        base_list = [c for c in ALL_CODES if c in base]
        consensus_list = [c for c in ALL_CODES if c in consensus]
        merged.append({
            "accident_id": aid,
            "n_experts": n,
            "base_gt_codes": base_list,
            "expert_codes": {name: [c for c in ALL_CODES if c in v["codes"]] for name, v in voters},
            "consensus_codes": consensus_list,
            "disputed_codes": [c for c in ALL_CODES if c in disputed],
            "unchanged_from_gt": consensus_list == base_list,
            "notes": {name: v["note"] for name, v in voters if v["note"]},
            "uncertain": {name: sorted(v["uncertain"]) for name, v in voters if v["uncertain"]},
        })

    disputed_rows = [m for m in merged if m["disputed_codes"]]
    changed_rows = [m for m in merged if not m["unchanged_from_gt"]]

    # ---------- 输出 ----------
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    jsonl = outdir / "合并结果.jsonl"
    with jsonl.open("w", encoding="utf-8") as f:
        for m in merged:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    lines = []
    def emit(x=""):
        print(x)
        lines.append(x)

    emit("# 专家审核合并报告")
    emit()
    emit(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    emit()
    emit(f"- 专家：{'、'.join(e['expert'] for e in experts)}")
    emit(f"- 涉及事故：{len(all_ids)} 条")
    emit(f"- 全员共同审过：{len(overlap_all)} 条")
    emit()

    emit("## 一、逐专家统计")
    emit()
    emit("| 专家 | 已录条目 | 已确认 | 与 GT 不同 | 含存疑 | 平均编码数 |")
    emit("|---|---:|---:|---:|---:|---:|")
    for p in per_expert:
        emit(f"| {p['expert']} | {p['touched']} | {p['reviewed']} | {p['changed']} | "
             f"{p['uncertain']} | {p['avg_codes']} |")
    emit()

    if pairs:
        emit("## 二、两两一致率（共同审过的条目）")
        emit()
        emit("| 专家 A | 专家 B | 共同条目 | 平均 Jaccard | 完全一致率 |")
        emit("|---|---|---:|---:|---:|")
        for p in pairs:
            if p["n"] == 0:
                emit(f"| {p['a']} | {p['b']} | 0 | — | — |")
            else:
                emit(f"| {p['a']} | {p['b']} | {p['n']} | {p['mean_jaccard']:.3f} | "
                     f"{p['exact']:.1%} |")
        emit()

    if overlap_all:
        emit("## 三、逐类一致率（全员共同审过的条目）")
        emit()
        emit("| 类别 | 名称 | 一致率 |")
        emit("|---|---|---:|")
        for c in ALL_CODES:
            emit(f"| {c} | {CODE_NAMES[c]} | {per_class[c]:.1%} |")
        emit()

    emit("## 四、需要处理的分歧")
    emit()
    emit(f"专家意见不一致的事故：**{len(disputed_rows)} / {len(merged)}** 条；"
         f"审核后与 GT 不同的事故：**{len(changed_rows)} / {len(merged)}** 条。")
    emit()
    if disputed_rows:
        emit("| # | 事故 id | 分歧类别 | 各专家 |")
        emit("|---|---|---|---|")
        for i, m in enumerate(disputed_rows[:50], 1):
            detail = "；".join(f"{name}：{','.join(codes) or '无'}"
                              for name, codes in m["expert_codes"].items())
            emit(f"| {i} | `{m['accident_id']}` | {','.join(m['disputed_codes'])} | {detail} |")
        if len(disputed_rows) > 50:
            emit()
            emit(f"> 仅显示前 50 条，完整列表见 `{jsonl.name}`。")
    else:
        emit("没有专家间分歧。")
    emit()
    emit("多数投票规则：某类被超过半数专家勾选则纳入 `consensus_codes`；"
         "票数在 0 与 n 之间的类别列入 `disputed_codes`，建议人工裁定。")

    md = outdir / "合并报告.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print()
    print(f"已写入 {jsonl}")
    print(f"已写入 {md}")


if __name__ == "__main__":
    main()
