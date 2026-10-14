"""一览「策略 × 模型」矩阵的完成度与质量，供定时巡检使用。

对每一格报告：唯一成功条数 / 300、异常行数、空编码数、撞顶数、编码均值、
JSON 前行文长度（判断模型是否真的先写了推理/计划）。

同时标出：尚未开跑的格子、跑了一半的格子、以及需要「补跑异常行」的格子。

用法：
    python3 scripts/golden_s1/verify_matrix.py
    python3 scripts/golden_s1/verify_matrix.py --md out.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STRAT = ROOT / "experiments/golden_standard/strategies"

STRATEGIES = ["S0", "S1", "S2", "S3", "S4", "S5"]
LABELS = ["glm5.2", "flash", "hy3", "pro"]
TARGET = 300


def load(strategy: str, label: str) -> tuple[dict, int, int, int]:
    """返回 (唯一成功行 {id: row}, 总行数, **未修复的**失败 id 数, 撞顶数)。

    注意：results.jsonl 是「追加」写的，重跑补齐会在同 id 后追加新行，
    旧失败行仍留在文件里。故「失败行数」不是待补数——只有**同一 id 没有任何成功行**
    才算真的待补。历史陈旧失败行是正常现象。
    """
    p = STRAT / strategy / label / "results.jsonl"
    if not p.exists():
        return {}, 0, 0, 0
    total = capped = 0
    ok: dict[str, dict] = {}
    failed_ids: set[str] = set()
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("hit_cap"):
            capped += 1
        rid = r.get("id", "")
        if r.get("error") or r.get("parse_error") or r.get("schema_errors"):
            failed_ids.add(rid)           # 失败行不覆盖已有的成功行
            continue
        ok[rid] = r                       # 同 id 以最后一条成功行为准
    return ok, total, len(failed_ids - ok.keys()), capped


def cell_stats(ok: dict) -> dict:
    n = len(ok)
    if not n:
        return {"n": 0}
    codes = [(r.get("parsed") or {}).get("codes") or [] for r in ok.values()]
    return {
        "n": n,
        "empty": sum(1 for c in codes if not c),
        "avg": sum(len(c) for c in codes) / n,
        "reason": sum(r.get("reason_chars") or 0 for r in ok.values()) / n,
        "inferred": sum(r.get("n_inferred") or 0 for r in ok.values()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    out: list[str] = []

    def emit(s: str = ""):
        print(s)
        out.append(s)

    emit("# 策略 × 模型 矩阵巡检")
    emit()
    todo: list[str] = []
    emit("| 策略 | 模型 | 成功/目标 | 待补 | 空编码 | 撞顶 | 编码均值 | 计划/推理行文 | 状态 |")
    emit("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for s in STRATEGIES:
        for lb in LABELS:
            ok, total, bad, capped = load(s, lb)
            st = cell_stats(ok)
            n = st.get("n", 0)
            if not total:
                status = "未开跑"
                todo.append(f"{s}′×{lb}：未开跑")
            elif n >= TARGET and bad == 0:
                status = "✅ 完成"
            elif n >= TARGET:
                status = f"⚠ 待补 {bad} 条"
                todo.append(f"{s}′×{lb}：补跑 {bad} 条异常行")
            else:
                status = "⏳ 进行中"
                todo.append(f"{s}′×{lb}：进行中 {n}/{TARGET}")
            emit(f"| {s}′ | {lb} | {n}/{TARGET} | {bad} | {st.get('empty', '-')} | "
                 f"{capped} | {st.get('avg', 0):.2f} | {st.get('reason', 0):.0f} | {status} |")
    emit()
    emit("## 待办")
    emit()
    if todo:
        for t in todo:
            emit(f"- {t}")
    else:
        emit("- 无，矩阵已跑满")
    emit()

    if args.md:
        Path(args.md).write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"\n已写入 {args.md}")


if __name__ == "__main__":
    main()
