"""以库内（DB）为 GT，评测四模型在【基本特征】与【致因编码】上的 Acc / P / R / F1。

GT 来源：
  基本特征 —— `accidents` 表的各字段（30 个 schema 字段，`location` ↔ 库内 `province`）
  致因编码 —— `caics_causes` 表（19 类 CAICS）

⚠️ 库内数据是本项目 Step2 管道的产物，不是独立真值。本评测衡量的是
   「各模型向库内标注靠拢的程度」，**不能**当作因果编码正确性的绝对指标。

字段分型决定指标口径：
  cat  分类（含枚举码与中文枚举）→ 准确率 + 逐类 macro P/R/F1
  num  离散数值（死亡/受伤/被困）→ 同 cat（按取值当类别）
  tol  带容差数值（经济损失 ±5%、坠落高度 ±0.5m）→ 只报准确率
  text 自由文本 → 只报准确率（严格匹配 + 模糊匹配），P/R/F1 不适用

用法：
    python3 scripts/golden_s1/eval_vs_db.py
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import statistics as st
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path("/home/suntianhao/construction_agent/.claude/worktrees/metrics-4models")
GOLD = ROOT / "experiments/golden_standard"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"

MODELS = [("flash", "deepseek-v4-flash", "flash"),
          ("pro", "deepseek-v4-pro", "pro"),
          ("hy3", "混元 hy3", "hy3"),
          ("glm5.2", "GLM-5.2", "glm5.2")]

CAT = ["accident_type", "severity", "project_type", "building_structure", "construction_phase",
       "work_task", "contractor_type", "collapse_subtype", "report_type", "date", "accident_time"]
# 多值字段：schema 允许用顿号分隔多个取值，按**集合**比较（逐编码二分类再宏平均），
# 否则模型输出「EQP06、EQP01」与 GT「EQP06」会被判为完全不符。
MULTI = ["equipment_involved", "material_involved", "worker_type"]
NUM = ["deaths", "injuries", "trapped_count"]
TOL = {"direct_economic_loss": 0.05, "fall_height": 0.5}
TEXT = ["title", "location", "city", "district", "accident_location_detail", "weather",
        "project_name", "developer", "contractor", "supervision_company", "summary", "direct_cause"]
FIELDS = CAT + MULTI + NUM + list(TOL) + TEXT
assert len(FIELDS) == 31, len(FIELDS)

DB_COL = {"location": "province"}
EMPTY = {None, "", "未知", "未提及", "null", "不详"}
NULL = "<空>"

CAUSE_CODES = ([f"M{i}" for i in range(1, 6)] + [f"F{i}" for i in range(1, 5)]
               + [f"T{i}" for i in range(1, 5)] + [f"H{i}" for i in range(1, 5)] + ["C1", "C2"])


def norm(v):
    """归一化：去空白与中英标点；空值统一成哨兵；数值统一成整数字符串。"""
    if v is None:
        return NULL
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).strip()
    if s in EMPTY:
        return NULL
    s = re.sub(r"[\s“”\"'·,，。.：:；;（）()\[\]【】—\-–_/、]", "", s)
    return s or NULL


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def tol_ok(field, gt, pred):
    a, b = num(gt), num(pred)
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if field == "fall_height":
        return abs(a - b) <= TOL[field]
    return abs(a - b) <= TOL[field] * max(abs(a), abs(b), 1e-9)


def prf_macro(y_true, y_pred, labels):
    """逐类算 P/R/F1 再宏平均（labels 取 GT∪预测 出现过的类别）。"""
    P = R = F = 0.0
    for L in labels:
        tp = fp = fn = 0
        for t, p in zip(y_true, y_pred):
            if t == L and p == L:
                tp += 1
            elif p == L:
                fp += 1
            elif t == L:
                fn += 1
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        P += pr; R += rc
        F += 2 * pr * rc / (pr + rc) if pr + rc else 0.0
    n = max(1, len(labels))
    return P / n, R / n, F / n


def to_set(v):
    """多值字段 → 取值集合（顿号/逗号/分号/斜杠分隔）。"""
    s = str(v or "").strip()
    if s in EMPTY:
        return frozenset()
    parts = [norm(p) for p in re.split(r"[、,，;；/]", s)]
    return frozenset(p for p in parts if p != NULL)


def prf_macro_sets(gt_sets, pred_sets, vocab):
    """多标签 macro P/R/F1：把每个取值当二分类标签。"""
    P = R = F = 0.0
    for L in vocab:
        tp = sum(1 for g, p in zip(gt_sets, pred_sets) if L in g and L in p)
        fp = sum(1 for g, p in zip(gt_sets, pred_sets) if L not in g and L in p)
        fn = sum(1 for g, p in zip(gt_sets, pred_sets) if L in g and L not in p)
        pr, rc, f1 = prf_binary(tp, fp, fn)
        P += pr; R += rc; F += f1
    m = max(1, len(vocab))
    return P / m, R / m, F / m


def prf_binary(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def main():
    cands = [json.loads(l) for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")]
    ids = [c["id"] for c in cands]
    n = len(ids)

    # ── GT：库内 ──
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = ",".join("?" * n)
    sel = ", ".join(['id'] + [f'"{DB_COL.get(f, f)}" AS "{f}"' for f in FIELDS])
    gt_field = {f: {} for f in FIELDS}
    for r in con.execute(f"select {sel} from accidents where id in ({q})", ids):
        for f in FIELDS:
            gt_field[f][r["id"]] = r[f]
    gt_codes = defaultdict(set)
    for aid, code in con.execute(
            f"select accident_id, code from caics_causes where accident_id in ({q})", ids):
        gt_codes[aid].add(code)
    con.close()

    # ── 模型预测 ──
    pred_field, pred_codes = {}, {}
    for d, _, _ in MODELS:
        rows = {json.loads(l)["id"]: json.loads(l)
                for l in (GOLD / d / "S1_golden_results.jsonl").open(encoding="utf-8")}
        pred_field[d] = {f: {i: (rows[i].get("parsed") or {}).get(f) for i in ids} for f in FIELDS}
        pred_codes[d] = {i: set((rows[i].get("parsed") or {}).get("codes") or []) for i in ids}

    # ── 基本特征逐字段 ──
    field_metrics = {}
    for d, _, _ in MODELS:
        fm = {}
        for f in FIELDS:
            # GT 是否为「非空」：用于算非空条件准确率，避免「双方都为空」把准确率虚高
            if f in TOL:
                gtv = [gt_field[f][i] for i in ids]
                nonnull = [num(v) is not None for v in gtv]
                ok = [tol_ok(f, gt_field[f][i], pred_field[d][f][i]) for i in ids]
            elif f in MULTI:
                gtv = [to_set(gt_field[f][i]) for i in ids]
                pv = [to_set(pred_field[d][f][i]) for i in ids]
                nonnull = [len(s) > 0 for s in gtv]
                ok = [a == b for a, b in zip(gtv, pv)]
                vocab = sorted({x for s in gtv + pv for x in s})
                p, r, f1 = prf_macro_sets(gtv, pv, vocab)
            else:
                yt = [norm(gt_field[f][i]) for i in ids]
                yp = [norm(pred_field[d][f][i]) for i in ids]
                nonnull = [a != NULL for a in yt]
                ok = [a == b for a, b in zip(yt, yp)]

            acc = sum(ok) / n
            idx_nz = [k for k in range(n) if nonnull[k]]
            acc_nz = sum(1 for k in idx_nz if ok[k]) / len(idx_nz) if idx_nz else 0.0
            m = {"acc": acc, "acc_nonnull": acc_nz, "gt_nonnull": len(idx_nz),
                 "p": None, "r": None, "f1": None}

            if f in TOL:
                m["type"] = "tol"
            elif f in MULTI:
                m.update({"type": "multi", "p": p, "r": r, "f1": f1, "n_labels": len(vocab)})
            elif f in TEXT:
                yt = [norm(gt_field[f][i]) for i in ids]
                yp = [norm(pred_field[d][f][i]) for i in ids]
                m["type"] = "text"
                m["fuzzy"] = sum(1 for a, b in zip(yt, yp)
                                 if a == b or (a != NULL and b != NULL
                                               and SequenceMatcher(None, a, b).ratio() >= 0.8)) / n
            else:
                yt = [norm(gt_field[f][i]) for i in ids]
                yp = [norm(pred_field[d][f][i]) for i in ids]
                labels = sorted(set(yt) | set(yp))
                p, r, f1 = prf_macro(yt, yp, labels)
                m.update({"type": "cat" if f in CAT else "num",
                          "p": p, "r": r, "f1": f1, "n_labels": len(labels)})
            fm[f] = m
        field_metrics[d] = fm

    # ── 致因：19 类多标签 ──
    cause_metrics = {}
    for d, _, _ in MODELS:
        per_code, TP = {}, 0
        FP = FN = 0
        exact = 0
        for c in CAUSE_CODES:
            tp = sum(1 for i in ids if c in gt_codes[i] and c in pred_codes[d][i])
            fp = sum(1 for i in ids if c not in gt_codes[i] and c in pred_codes[d][i])
            fn = sum(1 for i in ids if c in gt_codes[i] and c not in pred_codes[d][i])
            TP += tp; FP += fp; FN += fn
            p, r, f1 = prf_binary(tp, fp, fn)
            per_code[c] = {"tp": tp, "fp": fp, "fn": fn, "p": p, "r": r, "f1": f1,
                           "gt": tp + fn, "pred": tp + fp}
        for i in ids:
            if gt_codes[i] == pred_codes[d][i]:
                exact += 1
        micro = prf_binary(TP, FP, FN)
        macro_p = st.mean([per_code[c]["p"] for c in CAUSE_CODES])
        macro_r = st.mean([per_code[c]["r"] for c in CAUSE_CODES])
        macro_f = st.mean([per_code[c]["f1"] for c in CAUSE_CODES])
        cause_metrics[d] = {
            "per_code": per_code, "tp": TP, "fp": FP, "fn": FN,
            "micro": {"p": micro[0], "r": micro[1], "f1": micro[2]},
            "macro": {"p": macro_p, "r": macro_r, "f1": macro_f},
            "hamming_acc": 1 - (FP + FN) / (len(CAUSE_CODES) * n),
            "exact_set_match": exact / n,
            "avg_pred": st.mean([len(pred_codes[d][i]) for i in ids]),
            "avg_gt": st.mean([len(gt_codes[i]) for i in ids]),
        }

    # ── 整合宏观 F1：字段（有 macro-F1 的）与 19 类编码等权平均 ──
    combined = {}
    for d, _, _ in MODELS:
        fs = [field_metrics[d][f]["f1"] for f in FIELDS if field_metrics[d][f]["f1"] is not None]
        cs = [cause_metrics[d]["per_code"][c]["f1"] for c in CAUSE_CODES]
        combined[d] = {"field_macro_f1": st.mean(fs), "n_fields": len(fs),
                       "cause_macro_f1": st.mean(cs), "n_codes": len(cs),
                       "combined_macro_f1": st.mean(fs + cs)}

    # ── 明细落盘 ──
    out = {"models": {d: {"display": disp} for d, disp, _ in MODELS},
           "n": n, "gt": "库内 DB（Step2 管道产物，非独立真值）",
           "field_metrics": field_metrics, "cause_metrics": cause_metrics,
           "combined": combined}
    (GOLD / "eval_vs_db.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    with (GOLD / "eval_vs_db_fields.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "type", "model", "accuracy", "precision_macro",
                    "recall_macro", "f1_macro", "fuzzy_acc", "acc_nonnull",
                    "gt_nonnull", "n_labels"])
        for f in FIELDS:
            for d, _, _ in MODELS:
                m = field_metrics[d][f]
                r3 = lambda x: "" if x is None else round(x, 4)          # noqa: E731
                w.writerow([f, m["type"], d, round(m["acc"], 4), r3(m["p"]), r3(m["r"]),
                            r3(m["f1"]), r3(m.get("fuzzy")), r3(m.get("acc_nonnull")),
                            m.get("gt_nonnull", ""), m.get("n_labels", "")])
    with (GOLD / "eval_vs_db_causes.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "model", "gt", "pred", "tp", "fp", "fn",
                    "precision", "recall", "f1"])
        for c in CAUSE_CODES:
            for d, _, _ in MODELS:
                m = cause_metrics[d]["per_code"][c]
                w.writerow([c, d, m["gt"], m["pred"], m["tp"], m["fp"], m["fn"],
                            round(m["p"], 4), round(m["r"], 4), round(m["f1"], 4)])

    # ── 报告 ──
    disp = {d: name for d, name, _ in MODELS}
    L = ["# 以库内为 GT 的四模型评测（基本特征 + 致因）", "",
         f"- 样本：{n} 条（四模型均 300/300 成功）",
         "- **GT = 库内**（`accidents` 表 31 字段 + `caics_causes` 19 类）。",
         "  库内是本项目 Step2 管道的产物，**不是独立真值**——本表衡量「各模型向库内标注靠拢的程度」。",
         "- 口径：分类/离散数值字段用逐类 macro P/R/F1；带容差数值只报准确率；",
         "  自由文本只报严格匹配与模糊匹配（相似度≥0.8）准确率，P/R/F1 不适用。", ""]

    L += ["## 一、基本特征：各字段 Accuracy", "",
          "| 字段 | 类型 | GT非空 | " + " | ".join(disp[d] for d, _, _ in MODELS) + " |",
          "|---|---|---:|" + "---:|" * len(MODELS)]
    for f in FIELDS:
        t = field_metrics[MODELS[0][0]][f]["type"]
        gtn = field_metrics[MODELS[0][0]][f]["gt_nonnull"]
        row = " | ".join(f"{field_metrics[d][f]['acc']*100:.1f}%" for d, _, _ in MODELS)
        L.append(f"| `{f}` | {t} | {gtn} | {row} |")

    L += ["", "## 一b、基本特征：仅 GT 非空子集上的 Accuracy（排除「双方都为空」的虚高）", "",
          "| 字段 | GT非空 | " + " | ".join(disp[d] for d, _, _ in MODELS) + " |",
          "|---|---:|" + "---:|" * len(MODELS)]
    for f in FIELDS:
        gtn = field_metrics[MODELS[0][0]][f]["gt_nonnull"]
        row = " | ".join(
            (f"{field_metrics[d][f]['acc_nonnull']*100:.1f}%" if gtn else "—")
            for d, _, _ in MODELS)
        L.append(f"| `{f}` | {gtn} | {row} |")

    L += ["", "## 二、基本特征：各字段 Macro-F1（P/R 见 CSV）", "",
          "| 字段 | " + " | ".join(disp[d] for d, _, _ in MODELS) + " |",
          "|---|" + "---:|" * len(MODELS)]
    for f in FIELDS:
        cells = []
        for d, _, _ in MODELS:
            v = field_metrics[d][f]["f1"]
            cells.append("n/a" if v is None else f"{v:.3f}")
        L.append(f"| `{f}` | " + " | ".join(cells) + " |")

    L += ["", "## 三、致因（19 类多标签）", "",
          "| 指标 | " + " | ".join(disp[d] for d, _, _ in MODELS) + " |",
          "|---|" + "---:|" * len(MODELS)]
    for key, name, fmt in [("hamming_acc", "Hamming 准确率", "{:.3f}"),
                           ("exact_set_match", "编码集合完全一致率", "{:.3f}"),
                           ("avg_pred", "平均编码数（GT {:.2f}）".format(cause_metrics[MODELS[0][0]]["avg_gt"]), "{:.2f}")]:
        L.append(f"| {name} | " + " | ".join(fmt.format(cause_metrics[d][key]) for d, _, _ in MODELS) + " |")
    for grp in ("micro", "macro"):
        for k, nm in (("p", "精确率"), ("r", "召回率"), ("f1", "F1")):
            L.append(f"| {grp.upper()} {nm} | "
                     + " | ".join(f"{cause_metrics[d][grp][k]:.3f}" for d, _, _ in MODELS) + " |")

    L += ["", "## 四、致因：19 类各自 F1", "",
          "| 编码 | " + " | ".join(disp[d] for d, _, _ in MODELS) + " |",
          "|---|" + "---:|" * len(MODELS)]
    for c in CAUSE_CODES:
        L.append(f"| {c} | " + " | ".join(
            f"{cause_metrics[d]['per_code'][c]['f1']:.3f}" for d, _, _ in MODELS) + " |")

    L += ["", "## 五、整合宏观 F1", "",
          "定义：把「有 macro-F1 的基本特征字段」与「19 类致因编码」等权平均。", "",
          "| 组成 | " + " | ".join(disp[d] for d, _, _ in MODELS) + " |",
          "|---|" + "---:|" * len(MODELS)]
    L.append("| 基本特征 macro-F1（{} 字段） | ".format(combined[MODELS[0][0]]["n_fields"])
             + " | ".join(f"{combined[d]['field_macro_f1']:.3f}" for d, _, _ in MODELS) + " |")
    L.append("| 致因 macro-F1（19 类） | "
             + " | ".join(f"{combined[d]['cause_macro_f1']:.3f}" for d, _, _ in MODELS) + " |")
    L.append("| **整合宏观 F1** | "
             + " | ".join(f"**{combined[d]['combined_macro_f1']:.3f}**" for d, _, _ in MODELS) + " |")
    L += ["", "排序：" + " > ".join(
        f"{disp[d]} {combined[d]['combined_macro_f1']:.3f}"
        for d in sorted(combined, key=lambda x: -combined[x]["combined_macro_f1"]))]

    best = max(MODELS, key=lambda m: combined[m[0]]["combined_macro_f1"])
    lowfill = [f for f in FIELDS if field_metrics[MODELS[0][0]][f]["gt_nonnull"] < n * 0.5]
    L += ["", "## 注意", "",
          "- 库内作为 GT 存在**循环性**：库内编码由本项目 Step2 管道产出，",
          "  用它评测同源系统会高估一致性。若库内当年用的模型与某被测模型相同，",
          "  该模型的分数会被系统性抬高。",
          "- **「双方都为空」会被算作准确**：以下字段的 GT 非空率不足一半，",
          "  表一的准确率偏高属假象，请以**表一b（非空子集）**为准：",
          "  " + "、".join(f"`{f}`({field_metrics[MODELS[0][0]][f]['gt_nonnull']}/300)" for f in lowfill), "",
          "- 自由文本字段（title/summary/direct_cause/具体部位等）严格匹配率天然极低，",
          "  不代表模型能力差；应看 CSV 里的 `fuzzy`（相似度≥0.8）与 `acc_nonnull`。",
          "- 多值字段（设备/材料/工种）已按**集合**比较，用逐编码二分类宏平均。",
          f"- 整合宏观 F1 最高：{disp[best[0]]}（{combined[best[0]]['combined_macro_f1']:.3f}）。", ""]
    (GOLD / "EVAL_VS_DB.md").write_text("\n".join(L), encoding="utf-8")

    print(f"样本 {n} 条；GT = 库内")
    print("整合宏观 F1：" + " | ".join(
        f"{disp[d]} {combined[d]['combined_macro_f1']:.3f}" for d, _, _ in MODELS))
    print("  基本特征 macro-F1：" + " | ".join(
        f"{disp[d]} {combined[d]['field_macro_f1']:.3f}" for d, _, _ in MODELS))
    print("  致因 macro-F1：" + " | ".join(
        f"{disp[d]} {combined[d]['cause_macro_f1']:.3f}" for d, _, _ in MODELS))
    print(f"产出 → {GOLD}/EVAL_VS_DB.md, eval_vs_db.json, eval_vs_db_fields.csv, eval_vs_db_causes.csv")


if __name__ == "__main__":
    main()
