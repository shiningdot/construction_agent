"""可复现性检验：同一条事故、相同输入与参数，多次运行的输出是否一致。

对比对象：

- **原始结果**：`strategies/S5/{model}/results.jsonl` 中该事故的最后一条成功记录
  （即 300 条矩阵实验时跑出来的）
- **重跑结果**：`strategies/_reproducibility/S5/{model}/results_run{1..N}.jsonl`

比较维度：

1. `codes` 集合：是否完全相同、Jaccard
2. 19 类逐类是否一致
3. 31 个基础特征字段逐字段是否一致
4. S5 特有的 `layer_codes` 与 `rules_fired`
5. 文本字段（summary / direct_cause）是否逐字相同

用法：
    python3 scripts/golden_s1/check_reproducibility.py
    python3 scripts/golden_s1/check_reproducibility.py --accident-id 7dd2459ac7fa
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from eval_matrix import ALL_CODES, LABELS  # noqa: E402

STRAT = ROOT / "experiments/golden_standard/strategies"
REPRO = STRAT / "_reproducibility" / "S5"
DEFAULT_OUT = REPRO / "复现性检验.md"
DEFAULT_AID = "7dd2459ac7fa"

MODEL_LABELS = {
    "glm5.2": "GLM-5.2", "flash": "DeepSeek Flash",
    "hy3": "混元 hy3", "pro": "DeepSeek Pro",
}
TEXT_FIELDS = ("summary", "direct_cause")


def load_last(path: Path, aid: str) -> dict | None:
    """按 id 取最后一条成功记录（跳过 error / parse_error / schema_errors）。"""
    if not path.exists():
        return None
    last = None
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("id") != aid:
            continue
        if r.get("error") or r.get("parse_error") or r.get("schema_errors"):
            continue
        last = r
    return last


def codes_of(r: dict) -> list[str]:
    return [c for c in ((r.get("parsed") or {}).get("codes") or []) if c in ALL_CODES]


def jaccard(a, b) -> float:
    sa, sb = set(a), set(b)
    u = sa | sb
    return len(sa & sb) / len(u) if u else 1.0


def load_expert_codes(aid: str) -> set[str]:
    """从专家审核导出取该事故的 reviewed_codes。"""
    p = ROOT / "experiments/golden_standard/CAICS致因审核_孙天浩_2026-10-18.json"
    if not p.exists():
        return set()
    obj = json.loads(p.read_text(encoding="utf-8"))
    for it in obj.get("items") or []:
        if it.get("accident_id") == aid:
            return set(it.get("reviewed_codes") or [])
    return set()


def accident_prf(gt: set[str], pred) -> dict:
    tp = len(gt & set(pred))
    fp = len(set(pred) - gt)
    fn = len(gt - set(pred))
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"TP": tp, "FP": fp, "FN": fn, "P": p, "R": r, "F1": f1}


def compare_fields(a: dict, b: dict) -> tuple[int, int, list[str]]:
    pa = (a.get("parsed") or {})
    pb = (b.get("parsed") or {})
    keys = sorted(set(pa) | set(pb))
    same = 0
    diffs = []
    for k in keys:
        if pa.get(k) == pb.get(k):
            same += 1
        else:
            diffs.append(k)
    return same, len(keys), diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accident-id", default=DEFAULT_AID)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    aid = args.accident_id

    rows = []
    for lb in LABELS:
        orig = load_last(STRAT / "S5" / lb / "results.jsonl", aid)
        runs = []
        for i in (1, 2, 3):
            r = load_last(REPRO / lb / f"results_run{i}.jsonl", aid)
            if r:
                runs.append((i, r))
        if not orig and not runs:
            continue
        rows.append({"model": lb, "orig": orig, "runs": runs})

    out = []
    def emit(x=""):
        print(x)
        out.append(x)

    emit("# S5′ 可复现性检验")
    emit()
    emit(f"事故 id：`{aid}`｜同一文本、同一提示词、`temperature = 0.0`｜"
         f"每个模型重跑 3 次")
    emit()
    emit("> 目的：判断相同输入与参数下，模型输出是否稳定。若不一致，"
         "则批量实验结果包含不可复现的随机成分。")
    emit()

    emit("## 一、编码集合是否一致")
    emit()
    emit("| 模型 | 原始编码数 | run1 | run2 | run3 | 与原始完全相同 | 4 次两两 Jaccard 均值 |")
    emit("|---|---:|---:|---:|---:|---|---:|")
    details = []
    for row in rows:
        lb = row["model"]
        orig = row["orig"]
        orc = codes_of(orig) if orig else []
        nums = [len(codes_of(r)) for _, r in row["runs"]]
        sets = ([set(orc)] if orig else []) + [set(codes_of(r)) for _, r in row["runs"]]
        pair_j = [jaccard(a, b) for i, a in enumerate(sets) for b in sets[i + 1:]]
        same_as_orig = all(set(codes_of(r)) == set(orc) for _, r in row["runs"]) if orig else None
        jmean = f"{statistics.mean(pair_j):.3f}" if pair_j else "—"
        same_txt = "是" if same_as_orig else ("否" if same_as_orig is not None else "—")
        emit(f"| {MODEL_LABELS[lb]} | {len(orc) if orig else '—'} | "
             + " | ".join(str(n) for n in nums)
             + f" | {same_txt} | {jmean} |")
        details.append((row, orc, sets))

    emit()
    emit("## 二、逐字段一致率")
    emit()
    emit("| 模型 | 与原始相比：相同字段 / 总字段 | 不一致字段 |")
    emit("|---|---:|---|")
    field_detail = []
    for row in rows:
        lb = row["model"]
        orig = row["orig"]
        if not orig:
            emit(f"| {MODEL_LABELS[lb]} | — | 无原始结果 |")
            continue
        per_model = []
        for i, r in row["runs"]:
            same, tot, diffs = compare_fields(orig, r)
            per_model.append((same, tot, diffs))
            field_detail.append((lb, i, same, tot, diffs))
        if per_model:
            # 取与原始差异最大的一次作为该模型表现
            worst = min(per_model, key=lambda x: x[0] / x[1] if x[1] else 1)
            emit(f"| {MODEL_LABELS[lb]} | {worst[0]} / {worst[1]} | "
                 + ("、".join(worst[2]) if worst[2] else "无") + " |")

    emit()
    emit("## 三、S5′ 特有字段")
    emit()
    emit("| 模型 | 运行 | layer_codes 一致 | rules_fired 一致 | summary 逐字相同 | direct_cause 逐字相同 |")
    emit("|---|---|---|---|---|---|")
    for row in rows:
        lb = row["model"]
        orig = row["orig"]
        if not orig:
            continue
        for i, r in row["runs"]:
            layer_same = orig.get("layer_codes") == r.get("layer_codes")
            rules_same = orig.get("rules_fired") == r.get("rules_fired")
            pa, pb = orig.get("parsed") or {}, r.get("parsed") or {}
            s_same = pa.get("summary") == pb.get("summary")
            d_same = pa.get("direct_cause") == pb.get("direct_cause")
            emit(f"| {MODEL_LABELS[lb]} | run{i} | {'是' if layer_same else '否'} | "
                 f"{'是' if rules_same else '否'} | {'是' if s_same else '否'} | "
                 f"{'是' if d_same else '否'} |")

    emit()
    emit("## 四、逐条编码明细")
    emit()
    for row in rows:
        lb = row["model"]
        orig = row["orig"]
        emit(f"### {MODEL_LABELS[lb]}")
        emit()
        if orig:
            emit(f"- 原始：`{','.join(codes_of(orig))}`")
        for i, r in row["runs"]:
            emit(f"- run{i}：`{','.join(codes_of(r))}`")
        if orig:
            base = set(codes_of(orig))
            for i, r in row["runs"]:
                cur = set(codes_of(r))
                add, rem = sorted(cur - base), sorted(base - cur)
                if add or rem:
                    emit(f"  - run{i} 差异：新增 {add or '无'} / 缺失 {rem or '无'}")
        emit()

    gt_codes = load_expert_codes(aid)
    if gt_codes:
        emit()
        emit("## 五、对该事故 F1 的影响（对照专家 GT）")
        emit()
        emit(f"专家 GT（{len(gt_codes)} 类）：`{','.join(sorted(gt_codes))}`")
        emit()
        emit("| 模型 | 运行 | 编码数 | TP | FP | FN | 精确 | 召回 | F1 |")
        emit("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        f1s = {}
        for row in rows:
            lb = row["model"]
            seq = ([("原始", row["orig"])] if row["orig"] else []) + \
                  [(f"run{i}", r) for i, r in row["runs"]]
            vals = []
            for name, r in seq:
                m = accident_prf(gt_codes, codes_of(r))
                vals.append(m["F1"])
                emit(f"| {MODEL_LABELS[lb]} | {name} | {len(codes_of(r))} | "
                     f"{m['TP']} | {m['FP']} | {m['FN']} | {m['P']:.3f} | "
                     f"{m['R']:.3f} | {m['F1']:.3f} |")
            if vals:
                f1s[lb] = vals
        emit()
        if f1s:
            spans = {lb: max(v) - min(v) for lb, v in f1s.items()}
            emit("- 同一模型 4 次运行的 F1 极差：" +
                 "；".join(f"{MODEL_LABELS[lb]} {spans[lb]:.3f}" for lb in spans))
            emit(f"- 平均极差 **{statistics.mean(spans.values()):.3f}**"
                 "（单条事故；300 条批量时随机误差会被平均，但系统性多标不会）")
        emit()

    emit("## 六、结论")
    emit()
    n_all_same = sum(
        1 for row in rows
        if row["orig"] and all(set(codes_of(r)) == set(codes_of(row["orig"]))
                               for _, r in row["runs"])
    )
    n_models = sum(1 for row in rows if row["orig"])
    emit(f"- 在 {n_models} 个模型 × 3 次重跑中，编码集合与原始结果**完全相同**的有 "
         f"**{n_all_same}** 个模型的所有运行。")
    if n_all_same < n_models:
        emit("- 存在差异的模型见第四节。差异说明：即使 `temperature=0`，"
             "服务端推理仍可能因批处理、MoE 路由等因素产生非确定性输出。")
    else:
        emit("- 本次检验未观察到差异，说明在该服务商与参数下输出是可复现的。")
    emit()

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n已写入 {p}")


if __name__ == "__main__":
    main()
