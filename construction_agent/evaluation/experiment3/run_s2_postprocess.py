"""S2_AutoCoT 后处理：从 M/F/T/H/C 分层格式中提取编码，展示真实能力。

问题: S2 96/100 输出 {M:[...], F:[...], T:[...], H:[...], C:[...]} 分层JSON
      而非预期的 {codes:[...]} 格式，导致 extract_json() 找不到 codes 键。
      实际 LLM 推理质量良好（96条产出了405个编码）。

修复: 从 M/F/T/H/C 键中聚合编码到 codes，重新计算 metrics。
      不重新运行 API —— 纯后处理。
"""

import json
import re
from pathlib import Path
from collections import Counter

IN_FILE = Path("data/experiments/exp3_6strategies/S2_AutoCoT.json")
GT_PATH = Path("data/ground_truth/expertA.json")
SAMPLES_PATH = Path("data/ground_truth/gt_samples_100.json")
OUT_FILE = Path("data/experiments/exp3_6strategies/S2_AutoCoT_fixed.json")


def load_gt():
    """加载 GT 标签。"""
    gt_data = json.loads(GT_PATH.read_text(encoding="utf-8"))
    gt_labels = []
    for record in gt_data:
        codes = set()
        atype = sev = ""
        for ann in record.get("annotations", []):
            for res in ann.get("result", []):
                name = res["from_name"]
                val = res.get("value", {})
                if name in ('M', 'F', 'T', 'H', 'C'):
                    for v in val.get('choices', []):
                        codes.add(v.split('-')[0])
                elif name == 'accident_type':
                    atype = val.get('choices', [''])[0]
                elif name == 'severity':
                    sev = val.get('choices', [''])[0]
        gt_labels.append({
            "codes": sorted(codes),
            "accident_type": atype,
            "severity": sev
        })
    return gt_labels


def fix_prediction(pred: dict) -> dict:
    """从分层格式中提取编码，修复为统一格式。"""
    codes = []
    has_layered = False

    # 1. 从 M/F/T/H/C 键中提取分层编码
    for layer in ["M", "F", "T", "H", "C"]:
        if layer in pred and isinstance(pred[layer], list):
            has_layered = True
            for item in pred[layer]:
                if isinstance(item, str):
                    codes.append(item)

    # 2. 如果已有 codes 键，也合并进来
    if "codes" in pred and isinstance(pred.get("codes"), list):
        for item in pred["codes"]:
            if isinstance(item, str) and item not in codes:
                codes.append(item)

    # 3. 如果有 _raw 文本但未提取到编码，尝试正则提取
    if not codes and "_raw" in pred:
        raw = pred["_raw"]
        # 匹配所有 CAICS 编码模式：M1-M5, F1-F4, T1-T4, H1-H4, C1-C2
        code_pattern = re.findall(r'\b([MFTHC][1-5])\b', raw)
        codes = list(set(code_pattern))

    return {
        "codes": codes,
        "accident_type": pred.get("accident_type", ""),
        "severity": pred.get("severity", ""),
        "deaths": pred.get("deaths", 0) if isinstance(pred.get("deaths"), (int, float)) else 0,
        "injuries": pred.get("injuries", 0) if isinstance(pred.get("injuries"), (int, float)) else 0,
        "direct_cause": pred.get("direct_cause", ""),
        "_fixed_from_layered": has_layered,
        "_original_error": pred.get("_error", ""),
    }


def compute_metrics(predictions, ground_truth):
    """计算 Per-class P/R/F1/Acc + Ave/Ave+。"""
    all_classes = sorted(set(
        c for gt in ground_truth for c in gt.get("codes", [])
        if isinstance(gt, dict) and isinstance(c, str)
    ) | set(
        c for pred in predictions for c in pred.get("codes", [])
        if isinstance(pred, dict) and isinstance(c, str)
    ))

    results = {}
    for cls in all_classes:
        tp = sum(1 for i, gt in enumerate(ground_truth)
                 if isinstance(gt, dict) and i < len(predictions)
                 and cls in gt.get("codes", []) and cls in predictions[i].get("codes", []))
        fp = sum(1 for i, pred in enumerate(predictions)
                 if isinstance(pred, dict) and cls in pred.get("codes", [])
                 and (i >= len(ground_truth)
                      or not isinstance(ground_truth[i], dict)
                      or cls not in ground_truth[i].get("codes", [])))
        fn = sum(1 for i, gt in enumerate(ground_truth)
                 if isinstance(gt, dict) and cls in gt.get("codes", [])
                 and (i >= len(predictions)
                      or cls not in predictions[i].get("codes", [])))

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        acc = tp / len(ground_truth) if len(ground_truth) > 0 else 0
        results[cls] = {
            "P": prec, "R": rec, "F1": f1, "Acc": acc,
            "tp": tp, "fp": fp, "fn": fn,
            "count": sum(1 for gt in ground_truth
                         if isinstance(gt, dict) and cls in gt.get("codes", []))
        }

    vals = [v for v in results.values()]
    ave = {
        metric: sum(v[metric] for v in vals) / len(vals) if vals else 0
        for metric in ["P", "R", "F1", "Acc"]
    }
    vals_plus = [v for v in vals if v["count"] >= 5]
    ave_plus = {
        metric: sum(v[metric] for v in vals_plus) / len(vals_plus) if vals_plus else 0
        for metric in ["P", "R", "F1", "Acc"]
    }

    type_match = sum(1 for i, gt in enumerate(ground_truth)
                     if isinstance(gt, dict) and i < len(predictions)
                     and isinstance(predictions[i], dict)
                     and predictions[i].get("accident_type", "") == gt.get("accident_type", ""))
    sev_match = sum(1 for i, gt in enumerate(ground_truth)
                    if isinstance(gt, dict) and i < len(predictions)
                    and isinstance(predictions[i], dict)
                    and predictions[i].get("severity", "") == gt.get("severity", ""))

    return {
        "per_class": results,
        "ave": ave,
        "ave_plus": ave_plus,
        "type_acc": type_match / len(ground_truth) if ground_truth else 0,
        "sev_acc": sev_match / len(ground_truth) if ground_truth else 0,
    }


# ── Main ──
if __name__ == "__main__":
    print("=" * 60)
    print("S2_AutoCoT 后处理：从分层格式提取编码")
    print("=" * 60)

    # Load original S2 data
    original = json.loads(IN_FILE.read_text(encoding="utf-8"))
    raw_predictions = original["predictions"]

    # Load GT
    gt_labels = load_gt()

    # Fix predictions
    fixed_predictions = [fix_prediction(p) for p in raw_predictions]

    # Statistics
    total_layered = sum(1 for p in fixed_predictions if p["_fixed_from_layered"])
    total_errors = sum(1 for p in fixed_predictions if p["_original_error"])
    has_codes = sum(1 for p in fixed_predictions if p["codes"])
    total_codes = sum(len(p["codes"]) for p in fixed_predictions)

    print(f"\n原始预测: {len(raw_predictions)}")
    print(f"  分层格式: {total_layered} (从M/F/T/H/C键提取)")
    print(f"  解析错误: {total_errors}")
    print(f"  标准格式: {len(raw_predictions) - total_layered - total_errors}")
    print(f"\n修复后:")
    print(f"  有编码: {has_codes}/100")
    print(f"  总编码数: {total_codes}")
    print(f"  平均编码/条: {total_codes/100:.1f}")

    # Compute metrics
    metrics = compute_metrics(fixed_predictions, gt_labels)

    print(f"\n指标:")
    print(f"  Ave+(F1): {metrics['ave_plus']['F1']:.3f}")
    print(f"  Ave+(P):  {metrics['ave_plus']['P']:.3f}")
    print(f"  Ave+(R):  {metrics['ave_plus']['R']:.3f}")
    print(f"  Ave+(Acc):{metrics['ave_plus']['Acc']:.3f}")
    print(f"  TypeAcc:  {metrics['type_acc']:.3f} (⚠ 原始prompt未要求输出type/sev)")
    print(f"  SevAcc:   {metrics['sev_acc']:.3f} (⚠ 原始prompt未要求输出type/sev)")

    # Per-class detail
    print(f"\nPer-Class F1 (GT>=5):")
    per_class = metrics["per_class"]
    main_classes = [(c, v) for c, v in per_class.items() if v["count"] >= 5]
    main_classes.sort(key=lambda x: -x[1]["F1"])
    for cls, vals in main_classes:
        bar = "█" * int(vals["F1"] * 20)
        print(f"  {cls} (gt={vals['count']:2d}): F1={vals['F1']:.3f} "
              f"P={vals['P']:.3f} R={vals['R']:.3f} tp={vals['tp']} fp={vals['fp']} fn={vals['fn']} {bar}")

    # Save
    output = {
        "strategy": "S2_AutoCoT_fixed",
        "description": "S2后处理：从M/F/T/H/C分层JSON聚合编码，展示Auto-CoT真实能力。TypeAcc/SevAcc=0因为原始prompt未要求输出这些字段。",
        "original_file": str(IN_FILE),
        "fix_method": "从M/F/T/H/C键聚合编码到codes",
        "metrics": metrics,
        "predictions": fixed_predictions,
        "statistics": {
            "total_layered_format": total_layered,
            "total_parse_errors": total_errors,
            "total_has_codes": has_codes,
            "total_codes_extracted": total_codes,
            "avg_codes_per_sample": total_codes / 100,
        }
    }
    OUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 结果保存到: {OUT_FILE}")
    print(f"\n对比: 原始 Ave+F1=0.046 → 修复后 Ave+F1={metrics['ave_plus']['F1']:.3f}")
