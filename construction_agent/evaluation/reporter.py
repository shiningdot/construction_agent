"""报告生成器：生成论文友好的实验报告。

对标 Expert Systems With Applications 269 (2025) 126422 的 Table 3/4 格式。
支持：LaTeX 表格（F1/Precision/Recall/Accuracy），Markdown 报告，JSON 实验数据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from construction_agent.evaluation.evaluator import EvaluationResult
from construction_agent.evaluation.metrics import ExtractionMetrics, ClassificationReport


class Reporter:
    """实验报告生成器。"""

    def __init__(self, output_dir: str = "experiments"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── LaTeX 表格 ──

    def generate_classification_table(
        self,
        results: list[EvaluationResult],
        field: str = "accident_type",
        title: str = "",
        label: str = "",
    ) -> str:
        """生成分类指标的 LaTeX 表格（对标论文 Table 3）。

        格式:
        | Metric | Category | V1 | V2 | V3 | V4 |
        | F1     | 坍塌      | xx | xx | xx | xx |
        | F1     | 高处坠落   | xx | xx | xx | xx |
        | ...    |          |    |    |    |    |
        | Macro F1 |        | xx | xx | xx | xx |
        | Ave+ F1  |        | xx | xx | xx | xx |
        """
        # 收集每个版本的分类报告
        class_reports: list[ClassificationReport] = []
        for r in results:
            for cr in (r.extraction_metrics.classification_reports or []):
                if cr.field_name == field:
                    class_reports.append(cr)
                    break

        if not class_reports:
            return "% 无分类数据"

        versions = [f"V{r.version}" for r in results]
        title = title or f"{field} 提取 Precision / Recall / F1 对比"
        label = label or f"tab:classification_{field}"

        lines = [
            r"\begin{table}[htbp]",
            r"\centering",
            rf"\caption{{{title}}}",
            rf"\label{{{label}}}",
            r"\begin{tabular}{lcc" + "c" * len(versions) + "}",
            r"\toprule",
        ]

        # 每个指标一张子表
        for metric_name in ["F1-score", "Precision", "Recall"]:
            lines.append(r"\midrule")
            lines.append(rf"\multicolumn{{{3 + len(versions)}}}{{l}}{{\textbf{{{metric_name}}}}} \\")
            lines.append(r"\midrule")

            header = (["类别", "Support"] if metric_name == "F1-score" else ["类别", ""]) + versions
            lines.append(" & ".join(header) + r" \\")
            lines.append(r"\midrule")

            # 收集所有类别
            all_classes = sorted(set(
                c.class_name for cr in class_reports for c in cr.per_class
            ))

            for cls_name in all_classes:
                row = [cls_name]
                # Support（仅在 F1 表显示）
                if metric_name == "F1-score":
                    support = next(
                        (c.support for cr in class_reports for c in cr.per_class if c.class_name == cls_name),
                        0,
                    )
                    row.append(str(support))
                else:
                    row.append("")

                for cr in class_reports:
                    val = next(
                        (getattr(c, {
                            "F1-score": "f1_score",
                            "Precision": "precision",
                            "Recall": "recall",
                        }[metric_name]) for c in cr.per_class if c.class_name == cls_name),
                        None,
                    )
                    row.append(f"{val:.4f}" if val is not None else "-")
                lines.append(" & ".join(row) + r" \\")

            # 聚合指标
            lines.append(r"\midrule")
            for agg_name, agg_attr in [("Macro", "macro_f1" if metric_name == "F1-score" else f"macro_{metric_name.lower()}"),
                                        ("Weighted", "weighted_f1" if metric_name == "F1-score" else ""),
                                        ("Ave+", "avg_plus_f1" if metric_name == "F1-score" else f"avg_plus_{metric_name.lower()}")]:
                if not agg_attr:
                    continue
                row = [f"{agg_name}"]
                row.append("")
                for cr in class_reports:
                    val = getattr(cr, agg_attr, None)
                    row.append(f"{val:.4f}" if val else "-")
                lines.append(" & ".join(row) + r" \\")

        # Accuracy
        lines.append(r"\midrule")
        lines.append(r"\multicolumn{" + str(3 + len(versions)) + r"}{l}{\textbf{Accuracy}} \\")
        lines.append(r"\midrule")
        row = ["Overall"]
        row.append("")
        for cr in class_reports:
            row.append(f"{cr.accuracy:.4f}")
        lines.append(" & ".join(row) + r" \\")

        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
        lines.append(r"\end{table}")

        return "\n".join(lines)

    def generate_overall_comparison_table(
        self,
        results: list[EvaluationResult],
        title: str = "不同版本 Pipeline 总体性能对比",
        label: str = "tab:overall_comparison",
    ) -> str:
        """生成总体性能对比 LaTeX 表格。"""
        metrics_list = [r.extraction_metrics for r in results]
        versions = [f"V{r.version}" for r in results]

        lines = [
            r"\begin{table}[htbp]",
            r"\centering",
            rf"\caption{{{title}}}",
            rf"\label{{{label}}}",
            r"\begin{tabular}{l" + "c" * len(versions) + "}",
            r"\toprule",
        ]

        header = ["指标"] + versions
        lines.append(" & ".join(header) + r" \\")
        lines.append(r"\midrule")

        # 基本指标
        rows = [
            ("总记录数", [str(m.total_records) for m in metrics_list]),
            ("处理成功率(\\%)", [f"{m.success_rate:.1f}" for m in metrics_list]),
            ("字段平均填充率(\\%)", [f"{m.overall_fill_rate:.1f}" for m in metrics_list]),
            ("平均处理时间(s)", [f"{m.avg_time_per_record:.2f}" for m in metrics_list]),
        ]

        for name, vals in rows:
            lines.append(" & ".join([name] + vals) + r" \\")

        # 如果有 F1 指标
        has_f1 = any(m.classification_reports for m in metrics_list)
        if has_f1:
            lines.append(r"\midrule")
            lines.append(r"\multicolumn{" + str(1 + len(versions)) + r"}{l}{\textbf{分类评测}} \\")
            lines.append(r"\midrule")

            for m in metrics_list:
                for cr in (m.classification_reports or []):
                    versions_f1 = []
                    for m2 in metrics_list:
                        cr2 = next(
                            (c for c in (m2.classification_reports or []) if c.field_name == cr.field_name),
                            None,
                        )
                        versions_f1.append(f"{cr2.macro_f1:.4f}" if cr2 else "-")
                    lines.append(f"F1 ({cr.field_name}) & " + " & ".join(versions_f1) + r" \\")

                    versions_ave = []
                    for m2 in metrics_list:
                        cr2 = next(
                            (c for c in (m2.classification_reports or []) if c.field_name == cr.field_name),
                            None,
                        )
                        versions_ave.append(f"{cr2.avg_plus_f1:.4f}" if cr2 else "-")
                    lines.append(f"  Ave+ ({cr.field_name}) & " + " & ".join(versions_ave) + r" \\")

        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
        lines.append(r"\end{table}")

        return "\n".join(lines)

    def generate_fill_rate_table(
        self,
        results: list[EvaluationResult],
        title: str = "各字段填充率对比",
        label: str = "tab:fill_rate",
    ) -> str:
        """生成字段填充率 LaTeX 表格。"""
        metrics_list = [r.extraction_metrics for r in results]
        versions = [f"V{r.version}" for r in results]

        all_fields = set()
        for m in metrics_list:
            for fs in m.field_stats:
                all_fields.add(fs.field_name)

        sorted_fields = sorted(
            all_fields,
            key=lambda f: next(
                (fs.fill_rate for m in metrics_list for fs in m.field_stats if fs.field_name == f),
                0,
            ),
            reverse=True,
        )

        lines = [
            r"\begin{table}[htbp]",
            r"\centering",
            rf"\caption{{{title}}}",
            rf"\label{{{label}}}",
            r"\begin{tabular}{l" + "c" * len(versions) + "}",
            r"\toprule",
        ]

        header = ["字段"] + versions
        lines.append(" & ".join(header) + r" \\")
        lines.append(r"\midrule")

        for field in sorted_fields:
            row = [field]
            for m in metrics_list:
                rate = next(
                    (fs.fill_rate for fs in m.field_stats if fs.field_name == field),
                    0,
                )
                row.append(f"{rate:.1f}\\%")
            lines.append(" & ".join(row) + r" \\")

        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
        lines.append(r"\end{table}")

        return "\n".join(lines)

    # ── Markdown 报告 ──

    def generate_markdown_report(
        self,
        result: EvaluationResult,
    ) -> str:
        """生成 Markdown 格式的完整评测报告。"""
        m = result.extraction_metrics

        lines = [
            f"# Pipeline 评测报告: v{result.version}",
            f"",
            f"**时间**: {result.timestamp}",
            f"**总记录数**: {m.total_records}",
            f"**处理成功率**: {m.success_rate:.1f}%",
            f"**字段平均填充率**: {m.overall_fill_rate:.1f}%",
            f"**平均处理时间**: {m.avg_time_per_record:.2f}s/条",
            f"",
        ]

        # F1 分类评测（如果有）
        if m.classification_reports:
            lines.append("## 分类评测结果")
            lines.append("")
            for cr in m.classification_reports:
                lines.append(f"### {cr.field_name}")
                lines.append("")
                lines.append("| 类别 | Support | Precision | Recall | F1 |")
                lines.append("|------|---------|-----------|--------|----|")
                for c in cr.per_class:
                    lines.append(
                        f"| {c.class_name} | {c.support} | "
                        f"{c.precision:.4f} | {c.recall:.4f} | {c.f1_score:.4f} |"
                    )
                lines.append(f"| **Macro** | | {cr.macro_precision:.4f} | {cr.macro_recall:.4f} | {cr.macro_f1:.4f} |")
                lines.append(f"| **Weighted** | | | | {cr.weighted_f1:.4f} |")
                if cr.avg_plus_f1 > 0:
                    lines.append(f"| **Ave+** | | {cr.avg_plus_precision:.4f} | {cr.avg_plus_recall:.4f} | {cr.avg_plus_f1:.4f} |")
                lines.append(f"| **Accuracy** | | | | {cr.accuracy:.4f} |")
                lines.append("")

        lines.extend([
            f"## 字段填充率明细",
            f"",
            f"| 字段 | 填充率 | 不同值数 |",
            f"|------|--------|----------|",
        ])
        for fs in sorted(m.field_stats, key=lambda x: -x.fill_rate):
            lines.append(f"| {fs.field_name} | {fs.fill_rate:.1f}% | {fs.distinct_values} |")

        lines.extend([f"", f"## 事故类型分布", f"", f"| 类型 | 数量 |", f"|------|------|"])
        for t, c in m.accident_type_distribution.items():
            lines.append(f"| {t} | {c} |")

        lines.extend([f"", f"## 事故等级分布", f"", f"| 等级 | 数量 |", f"|------|------|"])
        for s, c in m.severity_distribution.items():
            lines.append(f"| {s} | {c} |")

        return "\n".join(lines)

    # ── 实验数据导出 ──

    def export_experiment_data(
        self,
        results: list[EvaluationResult],
        filename: str = "experiment_data.json",
    ) -> Path:
        """导出标准化的实验数据（论文引用用）。"""
        data = {
            "experiment_type": "construction_accident_extraction",
            "description": "建筑施工事故信息提取 Pipeline 版本对比实验",
            "reference_paper": "Expert Systems With Applications 269 (2025) 126422",
            "metrics": "F1-score, Precision, Recall, Accuracy (per class + macro + weighted + Ave+)",
            "versions": [],
        }

        for r in results:
            version_data = r.to_dict()
            # 附加 LaTeX 表格
            version_data["latex_classification"] = self.generate_classification_table([r])
            version_data["latex_overall"] = self.generate_overall_comparison_table([r])
            version_data["latex_fill_rate"] = self.generate_fill_rate_table([r])
            data["versions"].append(version_data)

        filepath = self.output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"实验数据导出: {filepath}")
        return filepath

    def export_latex_tables(
        self,
        results: list[EvaluationResult],
        filename: str = "latex_tables.tex",
    ) -> Path:
        """导出所有 LaTeX 表格到单一文件。"""
        parts = [
            r"% LaTeX tables for construction accident extraction experiment",
            r"% Generated automatically by evaluation.Reporter",
            r"% Reference: Expert Systems With Applications 269 (2025) 126422",
            r"",
        ]

        # 总体对比表
        parts.append(self.generate_overall_comparison_table(results))
        parts.append("")

        # 填充率表
        parts.append(self.generate_fill_rate_table(results))
        parts.append("")

        # 分类指标表（每个 field）
        fields_done = set()
        for r in results:
            for cr in (r.extraction_metrics.classification_reports or []):
                if cr.field_name not in fields_done:
                    parts.append(self.generate_classification_table(results, field=cr.field_name))
                    parts.append("")
                    fields_done.add(cr.field_name)

        filepath = self.output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))

        logger.info(f"LaTeX 表格导出: {filepath}")
        return filepath


import logging
logger = logging.getLogger(__name__)
