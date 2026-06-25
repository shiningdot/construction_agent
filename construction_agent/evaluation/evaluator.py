"""评测器：运行 Pipeline 并计算指标。

支持：
- Pipeline 自动运行 + 指标计算
- 已有数据集静态评测
- Ground Truth 分类评测（Precision / Recall / F1）
- 多版本横向对比
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from construction_agent.versions.base import BasePipeline, PipelineResult, BatchResult
from construction_agent.evaluation.metrics import (
    ExtractionMetrics,
    MetricsCalculator,
    ClassificationReport,
    FieldStats,
)
from construction_agent.database.dataset_manager import DatasetManager


@dataclass
class FieldAccuracy:
    """单字段准确率（ground truth 评测用）。"""
    field: str
    correct: int
    total: int
    accuracy: float = 0.0

    def __post_init__(self):
        self.accuracy = self.correct / self.total if self.total > 0 else 0.0


@dataclass
class EvaluationResult:
    """完整评测结果。"""
    version: str
    timestamp: str
    batch_result: BatchResult
    extraction_metrics: ExtractionMetrics
    field_accuracy: Optional[list[FieldAccuracy]] = None

    def summary(self) -> dict:
        """一句话摘要。"""
        m = self.extraction_metrics
        s = {
            "version": self.version,
            "total_records": m.total_records,
            "success_rate": f"{m.success_rate:.1f}%",
            "overall_fill_rate": f"{m.overall_fill_rate:.1f}%",
            "avg_time": f"{m.avg_time_per_record:.1f}s",
            "top_types": dict(list(m.accident_type_distribution.items())[:5]),
        }
        # 如果有分类评测，添加 F1 摘要
        if m.classification_reports:
            for cr in m.classification_reports:
                s[f"f1_{cr.field_name}"] = f"{cr.macro_f1:.4f}"
                s[f"ave+_{cr.field_name}"] = f"{cr.avg_plus_f1:.4f}"
        return s

    def to_dict(self) -> dict:
        """完整结果序列化。"""
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "summary": self.summary(),
            "batch": self.batch_result.to_dict(),
            "metrics": self.extraction_metrics.to_dict(),
        }

    def save(self, path: str):
        """保存评测结果到 JSON。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return p


class Evaluator:
    """评测器。"""

    def __init__(self, output_dir: str = "experiments"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def evaluate(
        self,
        pipeline: BasePipeline,
        texts: list[str],
        label: Optional[str] = None,
        batch_size: int = 0,
        ground_truth: Optional[list[dict]] = None,
    ) -> EvaluationResult:
        """对 Pipeline 进行完整评测。

        Args:
            pipeline: 待评测的 Pipeline 实例
            texts: 测试文本列表
            label: 可选标签，用于输出文件名
            batch_size: 限制处理条数
            ground_truth: 可选的人工标注 ground truth 记录

        Returns:
            EvaluationResult
        """
        from datetime import datetime

        version = pipeline.version
        label = label or version
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 运行 Pipeline
        t0 = time.time()
        batch_result = pipeline.process_batch(texts, batch_size=batch_size)
        total_time = time.time() - t0

        # 收集成功的记录
        records = []
        for pr in batch_result.successes:
            if pr.accident:
                records.append(pr.accident.to_dict())

        # 计算指标
        if ground_truth:
            metrics = MetricsCalculator.compute_with_ground_truth(records, ground_truth, batch_result)
        else:
            metrics = MetricsCalculator.compute(records, batch_result)

        result = EvaluationResult(
            version=version,
            timestamp=timestamp,
            batch_result=batch_result,
            extraction_metrics=metrics,
        )

        # 保存到 experiments/
        filepath = self.output_dir / f"eval_{label}_{timestamp[:10]}.json"
        result.save(str(filepath))

        return result

    def evaluate_from_dataset(
        self,
        pipeline: BasePipeline,
        dataset: DatasetManager,
        label: Optional[str] = None,
    ) -> EvaluationResult:
        """对已有数据集计算指标（不重新运行 Pipeline）。"""
        from datetime import datetime

        version = pipeline.version
        label = label or version
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        records = dataset.get_all()
        if not records:
            raise ValueError("数据集中没有记录")

        metrics = MetricsCalculator.compute(records)

        result = EvaluationResult(
            version=version,
            timestamp=timestamp,
            batch_result=BatchResult(
                version=version,
                total=len(records),
                successes=[],
                failures=[],
            ),
            extraction_metrics=metrics,
        )

        filepath = self.output_dir / f"eval_{label}_dataset_{timestamp[:10]}.json"
        result.save(str(filepath))

        return result

    def compare_versions(
        self,
        results: list[EvaluationResult],
        save: bool = True,
    ) -> dict:
        """对比多个版本的评测结果。"""
        from datetime import datetime

        comparison = {
            "compare_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "versions": [],
            "fill_rate_comparison": {},
            "speed_comparison": {},
            "f1_comparison": {},
        }

        for r in results:
            m = r.extraction_metrics
            version_info = {
                "version": r.version,
                "success_rate": m.success_rate,
                "overall_fill_rate": m.overall_fill_rate,
                "avg_time": m.avg_time_per_record,
                "total_records": m.total_records,
            }
            # 如果有分类评测指标
            if m.classification_reports:
                for cr in m.classification_reports:
                    version_info[f"f1_{cr.field_name}"] = cr.macro_f1
                    version_info[f"ave+_{cr.field_name}"] = cr.avg_plus_f1
            comparison["versions"].append(version_info)

            # 字段填充率对比
            for fs in m.field_stats:
                if fs.field_name not in comparison["fill_rate_comparison"]:
                    comparison["fill_rate_comparison"][fs.field_name] = {}
                comparison["fill_rate_comparison"][fs.field_name][r.version] = round(fs.fill_rate, 1)

            comparison["speed_comparison"][r.version] = {
                "avg_time": round(m.avg_time_per_record, 2),
                "total_time": round(m.total_processing_time, 2),
            }

            # F1 对比
            if m.classification_reports:
                for cr in m.classification_reports:
                    if cr.field_name not in comparison["f1_comparison"]:
                        comparison["f1_comparison"][cr.field_name] = {}
                    comparison["f1_comparison"][cr.field_name][r.version] = {
                        "macro_f1": round(cr.macro_f1, 4),
                        "weighted_f1": round(cr.weighted_f1, 4),
                        "ave+_f1": round(cr.avg_plus_f1, 4),
                        "accuracy": round(cr.accuracy, 4),
                    }

        if save:
            filepath = self.output_dir / "version_comparison.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(comparison, f, ensure_ascii=False, indent=2)

        return comparison
