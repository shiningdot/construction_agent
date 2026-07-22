"""
对比报告生成: MD 报告 + JSON 指标 + 图表数据
"""

import json
import os
from datetime import datetime
from typing import Dict, List

from ..experiment1.metrics import compute_all_metrics, CORE_FIELDS, CAUSE_DIMS
from ..experiment1.config import EXPERIMENT_DIR


def _build(pred_records: List[Dict], gt: List[Dict]):
    """对齐 predictions 与 ground_truth"""
    gt_map = {g["index"]: g for g in gt} if "index" in (gt[0] if gt else {}) else {i: g for i, g in enumerate(gt)}
    preds, aligned = [], []
    for r in pred_records:
        idx = r.get("index")
        if idx is not None and idx in gt_map:
            preds.append(r.get("extracted") or {})
            aligned.append(gt_map[idx])
    return preds, aligned


class ComparisonReport:
    def __init__(self, data: Dict):
        self.data = data
        self.gt = data.get("ground_truth", [])
        self.models = {}
        self.metrics = {}

    def compute_all(self):
        for mk, md in self.data.get("models", {}).items():
            self.models[mk] = md["config"]
            preds, aligned = _build(md.get("parsed_records", []), self.gt)
            if preds:
                self.metrics[mk] = compute_all_metrics(preds, aligned)
                cost = md.get("summary", {})
                self.metrics[mk]["cost"] = {
                    "total_usd": round(cost.get("total_cost", 0), 4),
                    "avg_latency": round(cost.get("avg_latency", 0), 2),
                    "success": cost.get("success", 0),
                    "total": cost.get("total", 0),
                }
        return self.metrics

    def to_markdown(self) -> str:
        lines = ["# 实验一：多模型横向对比报告",
                 f"\n**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                 f"**样本**: {len(self.gt)} 条 | **模型**: {len(self.models)}",
                 "\n---\n## 1 基础字段精度\n| 字段 | " + " | ".join(m["name"] for m in self.models.values()) + " |",
                 "|------|" + "|".join("---" for _ in self.models) + "|"]

        for fc in CORE_FIELDS:
            f = fc["field"]
            row = [f]
            for mk in self.models:
                m = self.metrics.get(mk, {})
                b = m.get("basic_fields", {})
                acc = b.get(f, {}).get("accuracy", "-")
                row.append(f"{acc}%" if acc != "-" else "-")
            lines.append("| " + " | ".join(row) + " |")

        avg_row = ["**平均**"]
        for mk in self.models:
            b = self.metrics.get(mk, {}).get("basic_fields", {})
            avg_row.append(f"**{b.get('avg_accuracy', '-')}%**")
        lines.append("| " + " | ".join(avg_row) + " |")

        lines += ["\n---\n## 2 致因二元F1\n| 维度 | " + " | ".join(m["name"] for m in self.models.values()) + " |",
                  "|------|" + "|".join("---" for _ in self.models) + "|"]
        for d in CAUSE_DIMS:
            row = [d]
            for mk in self.models:
                cf = self.metrics.get(mk, {}).get("cause_dim_f1", {})
                row.append(f"{cf.get(d, {}).get('f1', '-')}%")
            lines.append("| " + " | ".join(row) + " |")
        af_row = ["**平均**"]
        for mk in self.models:
            cf = self.metrics.get(mk, {}).get("cause_dim_f1", {})
            af_row.append(f"**{cf.get('avg', {}).get('f1', '-')}%**")
        lines.append("| " + " | ".join(af_row) + " |")

        lines += ["\n---\n## 3 成本-性能\n| 模型 | 综合 | 延迟 | 成功/总数 |",
                  "|------|:---:|:----:|:--------:|"]
        for mk, mc in self.models.items():
            m = self.metrics.get(mk, {})
            lines.append(f"| {mc['name']} | {m.get('overall_score', '-')} | "
                         f"{m.get('cost', {}).get('avg_latency', '-')}s | "
                         f"{m.get('cost', {}).get('success', 0)}/{m.get('cost', {}).get('total', 0)} |")

        lines += ["\n---\n## 4 结论\n待实验完成后填写。"]
        return "\n".join(lines)

    def export_chart_data(self) -> Dict:
        series, pareto = {}, []
        for mk, mc in self.models.items():
            m = self.metrics.get(mk, {})
            b = m.get("basic_fields", {})
            cf = m.get("cause_dim_f1", {})
            cm = m.get("cause_category_match", {})
            series[mc["name"]] = [
                b.get("avg_accuracy", 0), cf.get("avg", {}).get("f1", 0),
                cm.get("avg", {}).get("f1", 0), m.get("overall_score", 0)]
            pareto.append({"model": mc["name"],
                           "cost": m.get("cost", {}).get("total_usd", 0),
                           "score": m.get("overall_score", 0)})
        return {"radar": {"categories": ["基础Accuracy", "致因F1", "类目F1", "综合"], "series": series},
                "pareto": sorted(pareto, key=lambda p: p["cost"])}

    def save(self, prefix: str = "experiment1") -> Dict[str, str]:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        paths = {}
        for name, content in [("report", self.to_markdown()), ("metrics", json.dumps(self.metrics, ensure_ascii=False, indent=2)),
                               ("chart", json.dumps(self.export_chart_data(), ensure_ascii=False, indent=2))]:
            p = os.path.join(EXPERIMENT_DIR, f"{prefix}_{name}_{ts}.{'md' if name == 'report' else 'json'}")
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            paths[name] = p
        return paths


def run_report(path: str):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    r = ComparisonReport(data)
    r.compute_all()
    paths = r.save()
    print(f"\n📊 报告已生成:")
    for k, v in paths.items():
        print(f"   {k}: {v}")
    print("\n📋 摘要:")
    for mk, mc in r.models.items():
        m = r.metrics.get(mk, {})
        print(f"   {mc['name']:20s} | 综合{m.get('overall_score',0):5.1f} | "
              f"Acc{m.get('basic_fields',{}).get('avg_accuracy',0):5.1f}% | "
              f"致因F1{m.get('cause_dim_f1',{}).get('avg',{}).get('f1',0):5.1f}%")


if __name__ == "__main__":
    import sys
    run_report(sys.argv[1])
