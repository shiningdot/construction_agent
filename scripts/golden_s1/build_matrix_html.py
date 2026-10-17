"""生成「策略 × 模型」交叉矩阵看板：单个自包含 HTML。

## 为什么是单文件

数据是 24 格 × 300 条完整标注（约 7 MB JSON）。若拆成 data.json，浏览器以 `file://`
打开时会被 CORS 拦住 `fetch`；若直接内联成明文，HTML 会到 10 MB 以上。因此把数据
gzip 压缩后 base64 内嵌，页面用浏览器的 `DecompressionStream('gzip')` 解压——仍是
一个可直接双击打开、离线可用的文件（约 2.5 MB）。

## 指标口径

**不在前端重算总指标**，一律复用 `eval_matrix.py` 的取数与计算：

- 致因：`load_cause_gt` / `load_preds`（按 id 去重、同 id 取最后一条成功行）、
  `per_class_metrics`、`runner.compute_metrics`（主口径，与《最终说明》同源）
- 基础特征：`basic_acc`（13 字段；数值 ±0.5 容差，其余严格相等）

前端只做一件事：给定某条事故的 GT 编码与某格预测编码，现算 TP/FP/FN——
这是逐条 diff，不涉及汇总口径。

用法：
    python3 scripts/golden_s1/build_matrix_html.py
    python3 scripts/golden_s1/build_matrix_html.py --out /path/to/看板.html
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from eval_matrix import (ALL_CODES, BASIC_13, LABELS, STRATEGIES, TARGET,  # noqa: E402
                         basic_acc, load_basic_gt, load_cause_gt, load_preds,
                         micro_f1, per_class_metrics, runner_metrics, summarize)

GOLD = ROOT / "experiments/golden_standard"
GTM = GOLD / "gt_manual"
TEMPLATE = ROOT / "scripts/golden_s1/matrix_dashboard_template.html"
DEFAULT_OUT = GOLD / "strategies" / "策略模型对比看板.html"

STRATEGY_LABELS = {
    "S0": "S0′ 零样本基线",
    "S1": "S1′ 少样本（3 例）",
    "S2": "S2′ 逐层推理",
    "S3": "S3′ 计划与求解",
    "S4": "S4′ CAICS-CoT",
    "S5": "S5′ CAICS-CoT+",
}
STRATEGY_SHORT = {s: s + "′" for s in STRATEGIES}
MODEL_LABELS = {
    "glm5.2": "GLM-5.2",
    "flash": "DeepSeek Flash",
    "hy3": "混元 hy3",
    "pro": "DeepSeek Pro",
}
MODEL_SHORT = {"glm5.2": "GLM", "flash": "Flash", "hy3": "hy3", "pro": "Pro"}

CODE_NAMES = {
    "M1": "现场监管缺失", "M2": "隐患排查失效", "M3": "安全培训不足",
    "M4": "制度规程漏洞", "M5": "市场行为违规",
    "F1": "临边洞口防护缺失", "F2": "支撑体系失稳", "F3": "设备安全装置失效",
    "F4": "个体防护未使用",
    "T1": "设备机具故障", "T2": "物料状态异常", "T3": "地质环境突变", "T4": "气象水文触发",
    "H1": "违反操作规程", "H2": "违规指挥决策", "H3": "风险辨识失败", "H4": "应急行为失当",
    "C1": "救援响应迟缓", "C2": "信息迟报瞒报",
}
LAYERS = [
    ["M", "管理根基", [f"M{i}" for i in range(1, 6)]],
    ["F", "防线失效", [f"F{i}" for i in range(1, 5)]],
    ["T", "触发条件", [f"T{i}" for i in range(1, 5)]],
    ["H", "人的行为", [f"H{i}" for i in range(1, 5)]],
    ["C", "后果放大", ["C1", "C2"]],
]

BASIC_NAMES = {
    "accident_type": "事故类型", "severity": "事故等级", "project_type": "工程类型",
    "collapse_subtype": "坍塌子类", "deaths": "死亡人数", "injuries": "受伤人数",
    "trapped_count": "被困人数", "direct_economic_loss": "直接经济损失",
    "fall_height": "坠落高度", "date": "日期", "accident_time": "时间",
    "location": "省份", "city": "城市",
}

FIELD_ORDER = [
    "codes", "title", "date", "location", "city", "district", "accident_time",
    "accident_location_detail", "weather", "project_name", "project_type",
    "building_structure", "construction_phase", "work_task", "developer", "contractor",
    "contractor_type", "supervision_company", "accident_type", "severity", "deaths",
    "injuries", "trapped_count", "direct_economic_loss", "collapse_subtype",
    "fall_height", "equipment_involved", "material_involved", "worker_type",
    "report_type", "summary", "direct_cause",
]
FIELD_NAMES = {
    "codes": "CAICS 编码", "title": "事故标题", "date": "日期", "location": "省份",
    "city": "城市", "district": "区县", "accident_time": "事故时间",
    "accident_location_detail": "具体地点", "weather": "天气",
    "project_name": "工程名称", "project_type": "工程类型",
    "building_structure": "建筑结构", "construction_phase": "施工阶段",
    "work_task": "作业类型", "developer": "建设单位", "contractor": "施工单位",
    "contractor_type": "承包类型", "supervision_company": "监理单位",
    "accident_type": "事故类型", "severity": "事故等级", "deaths": "死亡人数",
    "injuries": "受伤人数", "trapped_count": "被困人数",
    "direct_economic_loss": "直接经济损失（万元）", "collapse_subtype": "坍塌子类",
    "fall_height": "坠落高度（米）", "equipment_involved": "涉及设备",
    "material_involved": "涉及物料", "worker_type": "工人类型",
    "report_type": "报告类型", "summary": "事故摘要", "direct_cause": "直接原因",
}


def trim_parsed(d: dict) -> dict:
    """去掉 null/空串字段以缩体积；`codes` 必须保留（空数组也保留）。"""
    out = {}
    for k, v in d.items():
        if k == "codes":
            out[k] = v if isinstance(v, list) else []
        elif v not in (None, "", [], {}):
            out[k] = v
    return out


def load_accidents(cause_gt: dict[str, set[str]], basic_gt: dict[str, dict]) -> list[dict]:
    """事故清单：以 gt_draft 的顺序为准（idx 即该顺序），GT 编码取 cause_gt。"""
    rows = []
    for i, line in enumerate((GTM / "gt_draft.jsonl").open(encoding="utf-8"), 1):
        if not line.strip():
            continue
        r = json.loads(line)
        bf = r.get("basic_features") or {}
        rows.append({
            "idx": len(rows) + 1,
            "id": r["id"],
            "title": r.get("title") or "",
            "source": r.get("source_name") or "",
            "tier": r.get("tier") or "",
            "type": bf.get("accident_type") or "未知",
            "severity": bf.get("severity") or "",
            "deaths": bf.get("deaths"),
            "gtCodes": [c for c in ALL_CODES if c in cause_gt.get(r["id"], set())],
            "gtBasic": {f: bf.get(f) for f in BASIC_13},
        })
    return rows


def compute_metrics(cause_gt: dict[str, set[str]], basic_gt: dict[str, dict],
                    preds: dict[tuple[str, str], dict[str, dict]]) -> dict:
    metrics: dict[str, dict] = {}
    for s in STRATEGIES:
        for lb in LABELS:
            pr = preds[(s, lb)]
            ids = sorted(set(cause_gt) & set(pr))
            if not ids:
                continue
            g = [cause_gt[i] for i in ids]
            p = [{c for c in (pr[i].get("codes") or []) if c in ALL_CODES} for i in ids]
            pc = per_class_metrics(g, p)
            summ = summarize(pc)
            mp, mr, mf = micro_f1(g, p)
            rn = runner_metrics(ids, cause_gt, pr)
            ba = basic_acc({i: basic_gt[i] for i in ids if i in basic_gt}, pr)
            bh = sum(h for h, _ in ba.values())
            bt = sum(t for _, t in ba.values())
            metrics[f"{s}|{lb}"] = {
                "n": len(ids),
                "ave_F1": rn["ave"]["F1"] if rn else summ["ave_F1"],
                "ave_plus_F1": rn["ave_plus"]["F1"] if rn else summ["ave_plus_F1"],
                "ave_R": rn["ave"]["R"] if rn else summ["ave_R"],
                "ave_P": rn["ave"]["P"] if rn else summ["ave_P"],
                "micro_F1": mf,
                "exact": sum(1 for x, y in zip(g, p) if x == y) / len(ids),
                "avg_codes": sum(len(x) for x in p) / len(p),
                "basic_acc": bh / bt if bt else 0.0,
                "perClass": {c: {k: pc[c][k] for k in ("P", "R", "F1", "n_gt", "n_pred")}
                             for c in ALL_CODES},
                "perBasic": {f: {"hit": h, "tot": t} for f, (h, t) in ba.items()},
            }
    return metrics


def build_findings(metrics: dict) -> list[dict]:
    def avg(s, k):
        return sum(metrics[f"{s}|{m}"][k] for m in LABELS) / len(LABELS)

    def avg_model(m, k):
        return sum(metrics[f"{s}|{m}"][k] for s in STRATEGIES) / len(STRATEGIES)

    means = {s: avg(s, "ave_plus_F1") for s in STRATEGIES}
    code_rate = {s: avg(s, "avg_codes") for s in STRATEGIES}
    basic = {s: avg(s, "basic_acc") for s in STRATEGIES}

    # 策略效应 vs 模型效应：各自的极差（同一模型跨六策略 / 同一策略跨四模型）
    strat_ranges = [max(metrics[f"{s}|{m}"]["ave_plus_F1"] for s in STRATEGIES)
                    - min(metrics[f"{s}|{m}"]["ave_plus_F1"] for s in STRATEGIES) for m in LABELS]
    model_ranges = [max(metrics[f"{s}|{m}"]["ave_plus_F1"] for m in LABELS)
                    - min(metrics[f"{s}|{m}"]["ave_plus_F1"] for m in LABELS) for s in STRATEGIES]

    # 逐类过输出比（所有 GT 正例 >0 的格平均）
    ratios = {}
    for c in ALL_CODES:
        vals = [metrics[k]["perClass"][c]["n_pred"] / metrics[k]["perClass"][c]["n_gt"]
                for k in metrics if metrics[k]["perClass"][c]["n_gt"]]
        if vals:
            ratios[c] = sum(vals) / len(vals)
    top = sorted(ratios.items(), key=lambda x: -x[1])[:3]

    model_codes = {m: avg_model(m, "avg_codes") for m in LABELS}
    model_rec = {m: avg_model(m, "ave_R") for m in LABELS}
    model_basic = {m: avg_model(m, "basic_acc") for m in LABELS}

    return [
        {"t": "① 示例 > 推理结构 > 推断授权",
         "d": f"四模型均值 Ave+-F1：S1′（3 个少样本示例）{means['S1']:.3f} 最高，"
              f"S3′ {means['S3']:.3f}、S2′ {means['S2']:.3f} 次之；"
              f"S4′/S5′ 加入「推断授权」后降至 {means['S4']:.3f}/{means['S5']:.3f}。"},
        {"t": "② S3′ 与 S2′ 打平",
         "d": f"模型自主规划的 S3′（{means['S3']:.3f}）与人工给定固定因果链的 S2′"
              f"（{means['S2']:.3f}）只差 {abs(means['S3'] - means['S2']):.3f}，"
              "逐类差异多在 ±0.01 内——与论文中 plan-and-solve 最差的结论相反。"},
        {"t": "③ 过输出是「推断授权」的副作用",
         "d": f"平均编码/条：S4′ {code_rate['S4']:.2f}、S5′ {code_rate['S5']:.2f}（GT {7.71:.2f}），"
              f"约 {code_rate['S4'] / 7.71:.2f}×/{code_rate['S5'] / 7.71:.2f}×；"
              f"S0′–S3′ 只有 {min(code_rate[s] for s in ['S0', 'S1', 'S2', 'S3']):.2f}–"
              f"{max(code_rate[s] for s in ['S0', 'S1', 'S2', 'S3']):.2f}，反而欠输出。"
              f"过输出最集中于 " + "、".join(f"{c} {r:.2f}×" for c, r in top) + "。"},
        {"t": "④ 基础特征几乎不受策略影响",
         "d": f"13 字段准确率在 24 格间只从 {min(basic.values()):.3f} 变到 {max(basic.values()):.3f}"
              f"（{min(metrics[k]['basic_acc'] for k in metrics):.1%}–"
              f"{max(metrics[k]['basic_acc'] for k in metrics):.1%}）；"
              f"而致因 Ave+-F1 从 {min(m['ave_plus_F1'] for m in metrics.values()):.3f} 到 "
              f"{max(m['ave_plus_F1'] for m in metrics.values()):.3f}。策略改的是致因推断，不是字段抽取。"},
        {"t": "⑤ 模型差异与策略差异同量级（F1 口径），hy3 最保守",
         "d": f"策略效应（同一模型跨六策略极差）均值 {sum(strat_ranges) / len(strat_ranges):.3f}，"
              f"模型效应（同一策略跨四模型极差）均值 {sum(model_ranges) / len(model_ranges):.3f}。"
              f"hy3 编码最少（{model_codes['hy3']:.2f}/条）、召回最低（{model_rec['hy3']:.3f}）；"
              f"glm-5.2 基础特征最低（{model_basic['glm5.2']:.3f}）。"},
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    cause_gt = load_cause_gt()
    basic_gt = load_basic_gt()
    print(f"GT：致因 {len(cause_gt)} 条 | 基础特征 {len(basic_gt)} 条 × {len(BASIC_13)} 字段")

    preds: dict[tuple[str, str], dict[str, dict]] = {}
    for s in STRATEGIES:
        for lb in LABELS:
            preds[(s, lb)] = load_preds(s, lb)
            print(f"  {s}′ × {lb}: {len(preds[(s, lb)])} 条")

    missing = [f"{s}|{lb}" for (s, lb), v in preds.items() if len(v) < TARGET]
    if missing:
        print(f"⚠️  以下格不足 {TARGET} 条：{missing}")

    metrics = compute_metrics(cause_gt, basic_gt, preds)
    accidents = load_accidents(cause_gt, basic_gt)

    # 每类 GT 正例数（以任一满格为准，实际与全部格一致）
    any_cell = metrics[f"{STRATEGIES[0]}|{LABELS[0]}"]
    n_gt = {c: any_cell["perClass"][c]["n_gt"] for c in ALL_CODES}
    gt_avg = sum(n_gt.values()) / len(accidents)

    # 色阶范围：自适应但固定写进 meta，保证切换指标时颜色可比
    ranges = {}
    for k in ("ave_plus_F1", "ave_F1", "ave_R", "ave_P", "micro_F1", "exact", "basic_acc"):
        vals = [m[k] for m in metrics.values()]
        lo, hi = min(vals), max(vals)
        pad = (hi - lo) * 0.08 or 0.01
        ranges[k] = {"lo": max(0.0, lo - pad), "hi": min(1.0, hi + pad)}

    per_basic_vals = [pb["hit"] / pb["tot"] for m in metrics.values()
                      for pb in m["perBasic"].values() if pb["tot"]]
    bl, bh = min(per_basic_vals), max(per_basic_vals)
    bpad = (bh - bl) * 0.08 or 0.01

    best_s, best_m = max(metrics, key=lambda k: metrics[k]["ave_plus_F1"]).split("|")
    best = {"label": f"{STRATEGY_SHORT[best_s]} × {MODEL_LABELS[best_m]}",
            "value": metrics[f"{best_s}|{best_m}"]["ave_plus_F1"]}

    payload = {
        "meta": {
            "strategies": STRATEGIES,
            "models": LABELS,
            "strategyLabels": STRATEGY_LABELS,
            "strategyShort": STRATEGY_SHORT,
            "modelLabels": MODEL_LABELS,
            "modelShort": MODEL_SHORT,
            "codes": ALL_CODES,
            "codeNames": CODE_NAMES,
            "layers": LAYERS,
            "nGt": n_gt,
            "gtAvgCodes": gt_avg,
            "basicFields": BASIC_13,
            "basicNames": BASIC_NAMES,
            "basicRange": {"lo": max(0.0, bl - bpad), "hi": min(1.0, bh + bpad)},
            "ranges": ranges,
            "fieldOrder": FIELD_ORDER,
            "fieldNames": FIELD_NAMES,
            "best": best,
            "findings": build_findings(metrics),
        },
        "accidents": accidents,
        "metrics": metrics,
        "preds": {f"{s}|{lb}": {i: trim_parsed(d) for i, d in v.items()}
                  for (s, lb), v in preds.items()},
    }

    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(raw, 9)
    b64 = base64.b64encode(gz).decode("ascii")
    print(f"\n数据：明文 {len(raw)/1e6:.2f} MB → gzip {len(gz)/1e6:.2f} MB → base64 {len(b64)/1e6:.2f} MB")

    html = TEMPLATE.read_text(encoding="utf-8")
    if "__DATA_B64__" not in html:
        sys.exit(f"模板里找不到 __DATA_B64__ 占位符：{TEMPLATE}")
    html = html.replace("__DATA_B64__", b64)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"已写入 {out}（{out.stat().st_size/1e6:.2f} MB）")

    # 自检：解压回来核对
    assert json.loads(gzip.decompress(base64.b64decode(b64)))["meta"]["strategies"] == STRATEGIES
    print(f"自检通过：{len(metrics)} 格 × {len(accidents)} 条事故")


if __name__ == "__main__":
    main()
