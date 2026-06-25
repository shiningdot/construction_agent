"""指标计算模块。

对标论文：Expert Systems With Applications 269 (2025) 126422
- Precision / Recall / F1 per category (positive-class focused)
- Accuracy (exact match)
- Average / Average+（剔除异常类）
- Fill rate (覆盖率)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── 基础统计 ──

@dataclass
class FieldStats:
    """单个字段的提取统计。"""
    field_name: str
    total: int
    filled: int
    fill_rate: float
    distinct_values: int

    def to_dict(self) -> dict:
        return {
            "field": self.field_name,
            "fill_rate": round(self.fill_rate, 2),
            "filled": self.filled,
            "total": self.total,
            "distinct_values": self.distinct_values,
        }


@dataclass
class ClassMetrics:
    """单个类别的 Precision / Recall / F1 / Accuracy。"""
    class_name: str
    support: int           # 真实标签中出现次数
    tp: int                # True Positive
    fp: int                # False Positive
    fn: int                # False Negative
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    accuracy: float = 0.0  # 该类别的二元准确率

    def __post_init__(self):
        self.precision = self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0
        self.recall = self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0
        self.f1_score = (
            2 * self.precision * self.recall / (self.precision + self.recall)
            if (self.precision + self.recall) > 0 else 0.0
        )
        total_neg = (self.support - self.tp) + (self.fp)  # TN + FP 近似
        # 二元准确率：此类别 vs 非此类别
        tn = (self.support - (self.tp + self.fn))  # True Negative 近似
        self.accuracy = (self.tp + tn) / (self.tp + self.fp + self.fn + tn) if (self.tp + self.fp + self.fn + tn) > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "class": self.class_name,
            "support": self.support,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1_score, 4),
            "accuracy": round(self.accuracy, 4),
        }


@dataclass
class ClassificationReport:
    """完整分类评测报告（对标论文 Table 3）。"""
    field_name: str
    per_class: list[ClassMetrics]
    # 聚合指标
    macro_precision: float = 0.0
    macro_recall: float = 0.0
    macro_f1: float = 0.0
    weighted_f1: float = 0.0
    accuracy: float = 0.0         # Overall accuracy (exact match)
    avg_plus_precision: float = 0.0  # Ave+（剔除指定异常类）
    avg_plus_recall: float = 0.0
    avg_plus_f1: float = 0.0

    def __post_init__(self):
        if not self.per_class:
            return
        n = len(self.per_class)
        total_support = sum(c.support for c in self.per_class)

        self.macro_precision = sum(c.precision for c in self.per_class) / n
        self.macro_recall = sum(c.recall for c in self.per_class) / n
        self.macro_f1 = sum(c.f1_score for c in self.per_class) / n

        self.weighted_f1 = sum(c.f1_score * c.support for c in self.per_class) / max(total_support, 1)

        # 总体准确率: 所有正确预测 / 总数
        total_correct = sum(c.tp for c in self.per_class)
        total_predictions = sum(c.tp + c.fp for c in self.per_class)  # 所有预测
        self.accuracy = total_correct / max(total_predictions, 1)

    def set_avg_plus(self, exclude_classes: list[str]):
        """计算 Avreage+：剔除异常类后的平均指标。"""
        filtered = [c for c in self.per_class if c.class_name not in exclude_classes]
        if not filtered:
            return
        n = len(filtered)
        self.avg_plus_precision = sum(c.precision for c in filtered) / n
        self.avg_plus_recall = sum(c.recall for c in filtered) / n
        self.avg_plus_f1 = sum(c.f1_score for c in filtered) / n

    def to_dict(self) -> dict:
        return {
            "field": self.field_name,
            "per_class": [c.to_dict() for c in self.per_class],
            "macro_f1": round(self.macro_f1, 4),
            "weighted_f1": round(self.weighted_f1, 4),
            "accuracy": round(self.accuracy, 4),
            "avg_plus_f1": round(self.avg_plus_f1, 4),
        }


# ── 完整评测指标 ──

@dataclass
class ExtractionMetrics:
    """完整提取质量指标集。"""
    # 填充率指标
    field_stats: list[FieldStats]
    overall_fill_rate: float

    # 性能指标
    avg_time_per_record: float
    total_processing_time: float

    # 可靠性指标
    success_rate: float
    error_rate: float
    total_records: int
    success_count: int
    failure_count: int

    # 多样性指标
    accident_type_distribution: dict[str, int]
    severity_distribution: dict[str, int]

    # 分类评测（需要 ground truth）
    classification_reports: list[ClassificationReport] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overall_fill_rate": round(self.overall_fill_rate, 2),
            "avg_time_per_record": round(self.avg_time_per_record, 2),
            "total_processing_time": round(self.total_processing_time, 2),
            "success_rate": round(self.success_rate, 2),
            "error_rate": round(self.error_rate, 2),
            "total_records": self.total_records,
            "field_stats": [fs.to_dict() for fs in self.field_stats],
            "accident_type_distribution": self.accident_type_distribution,
            "severity_distribution": self.severity_distribution,
            "classification": [cr.to_dict() for cr in self.classification_reports] if self.classification_reports else None,
        }


# ── 指标计算器 ──

class MetricsCalculator:
    """指标计算器。"""

    # 需要统计填充率的字段
    FILL_RATE_FIELDS = [
        "title", "date", "accident_time", "location", "city",
        "accident_location_detail", "project_name", "project_type",
        "building_structure", "accident_type", "severity",
        "deaths", "injuries", "direct_economic_loss",
        "weather", "direct_cause", "contractor", "trapped_count",
        "summary", "human_factors", "machine_factors",
        "management_factors", "environmental_factors",
        "hfacs_codes",
    ]

    # 可用于 Precision/Recall/F1 评测的分类字段
    CLASSIFICATION_FIELDS = [
        "accident_type",  # 坍塌/高处坠落/...
        "severity",       # 一般事故/较大事故/...
        "project_type",   # 房屋建筑工程/交通运输工程/...
    ]

    @classmethod
    def compute(cls, records: list[dict], batch_result=None) -> ExtractionMetrics:
        """从记录列表计算基本指标。

        Args:
            records: 事故记录字典列表
            batch_result: 可选的 BatchResult

        Returns:
            ExtractionMetrics
        """
        n = len(records)

        # 计算各字段填充率
        field_stats = []
        for field in cls.FILL_RATE_FIELDS:
            filled = sum(
                1 for r in records
                if r.get(field) not in (None, "", [], 0, "0")
            )
            # 统计不同值（列表字段需要转换为可哈希类型）
            raw_values = []
            for r in records:
                v = r.get(field)
                if v not in (None, "", [], 0, "0"):
                    if isinstance(v, list):
                        raw_values.append(str(v))
                    else:
                        raw_values.append(v)
            distinct_values = len(set(raw_values)) if raw_values else 0
            field_stats.append(FieldStats(
                field_name=field,
                total=n,
                filled=filled,
                fill_rate=filled / n * 100 if n > 0 else 0,
                distinct_values=distinct_values,
            ))

        overall_fill_rate = (
            sum(fs.fill_rate for fs in field_stats) / len(field_stats)
            if field_stats else 0.0
        )

        # 事故类型分布
        type_dist: dict[str, int] = {}
        for r in records:
            t = r.get("accident_type", "未知")
            type_dist[t] = type_dist.get(t, 0) + 1

        # 等级分布
        severity_dist: dict[str, int] = {}
        for r in records:
            s = r.get("severity", "未知")
            severity_dist[s] = severity_dist.get(s, 0) + 1

        # 性能指标
        if batch_result:
            avg_time = batch_result.avg_processing_time
            success_rate = batch_result.success_rate
            error_rate = batch_result.failure_count / max(batch_result.total, 1) * 100
            success_count = batch_result.success_count
            failure_count = batch_result.failure_count
        else:
            avg_time = 0.0
            success_rate = 100.0
            error_rate = 0.0
            success_count = n
            failure_count = 0

        return ExtractionMetrics(
            field_stats=field_stats,
            overall_fill_rate=overall_fill_rate,
            avg_time_per_record=avg_time,
            total_processing_time=avg_time * n,
            success_rate=success_rate,
            error_rate=error_rate,
            total_records=n,
            success_count=success_count,
            failure_count=failure_count,
            accident_type_distribution=dict(sorted(type_dist.items(), key=lambda x: -x[1])),
            severity_distribution=dict(sorted(severity_dist.items(), key=lambda x: -x[1])),
        )

    # ── 分类评测（需要 ground truth） ──

    @classmethod
    def evaluate_classification(
        cls,
        predictions: list[str],
        ground_truth: list[str],
        field_name: str = "",
        exclude_classes: Optional[list[str]] = None,
    ) -> ClassificationReport:
        """计算分类字段的 Precision / Recall / F1。

        对标论文方式：每个类别做二元分类（该类 vs 非该类），
        然后计算 macro / weighted / Ave+ 聚合指标。

        Args:
            predictions: 模型预测的标签列表
            ground_truth: 真实标签列表
            field_name: 字段名
            exclude_classes: 计算 Ave+ 时排除的异常类别

        Returns:
            ClassificationReport
        """
        if len(predictions) != len(ground_truth):
            raise ValueError(f"预测({len(predictions)})和真实({len(ground_truth)})标签数量不一致")

        # 收集所有类别
        all_classes = sorted(set(ground_truth) | set(predictions))
        # 过滤掉空/未知类
        all_classes = [c for c in all_classes if c and c not in ("", "未知", "无法判断")]

        per_class = []
        for cls_name in all_classes:
            tp = fp = fn = tn = 0
            for pred, truth in zip(predictions, ground_truth):
                is_positive = (truth == cls_name)
                is_predicted = (pred == cls_name)
                if is_positive and is_predicted:
                    tp += 1
                elif not is_positive and is_predicted:
                    fp += 1
                elif is_positive and not is_predicted:
                    fn += 1
                else:
                    tn += 1

            support = sum(1 for t in ground_truth if t == cls_name)
            per_class.append(ClassMetrics(
                class_name=cls_name,
                support=support,
                tp=tp,
                fp=fp,
                fn=fn,
            ))

        report = ClassificationReport(
            field_name=field_name,
            per_class=per_class,
        )

        if exclude_classes:
            report.set_avg_plus(exclude_classes)

        return report

    @classmethod
    def evaluate_multiple_fields(
        cls,
        predictions: dict[str, list[str]],
        ground_truth: dict[str, list[str]],
        exclude_map: Optional[dict[str, list[str]]] = None,
    ) -> list[ClassificationReport]:
        """对多个分类字段进行评测。

        Args:
            predictions: {field_name: [pred_labels]}
            ground_truth: {field_name: [true_labels]}
            exclude_map: {field_name: [exclude_classes]} 用于 Ave+

        Returns:
            list[ClassificationReport]
        """
        reports = []
        for field in cls.CLASSIFICATION_FIELDS:
            if field not in predictions or field not in ground_truth:
                continue
            excludes = (exclude_map or {}).get(field, None)
            report = cls.evaluate_classification(
                predictions[field],
                ground_truth[field],
                field_name=field,
                exclude_classes=excludes,
            )
            reports.append(report)
        return reports

    @classmethod
    def compute_with_ground_truth(
        cls,
        records: list[dict],
        ground_truth: list[dict],
        batch_result=None,
    ) -> ExtractionMetrics:
        """在已有的数据集指标基础上，增加分类评测。

        Args:
            records: 模型提取的记录
            ground_truth: 人工标注的 ground truth 记录
            batch_result: 可选

        Returns:
            ExtractionMetrics（含 classification_reports）
        """
        metrics = cls.compute(records, batch_result)

        # 对齐 ground truth 和 predictions（按相同顺序）
        # 这里假设 records 和 ground_truth 按相同顺序排列
        if len(records) != len(ground_truth):
            # 尝试按 id 或 title 对齐
            truth_map = {}
            for gt in ground_truth:
                gid = gt.get("id", "") or gt.get("title", "")
                truth_map[gid] = gt
            aligned = []
            for r in records:
                rid = r.get("id", "") or r.get("title", "")
                if rid in truth_map:
                    aligned.append(truth_map[rid])
            if aligned:
                ground_truth = aligned
                records = records[:len(ground_truth)]

        if len(records) != len(ground_truth):
            # 无法对齐，跳过分类评测
            return metrics

        # 逐字段评测
        reports = []
        for field in cls.CLASSIFICATION_FIELDS:
            preds = [r.get(field, "") or "" for r in records]
            truths = [g.get(field, "") or "" for g in ground_truth]
            report = cls.evaluate_classification(preds, truths, field_name=field)
            reports.append(report)

        metrics.classification_reports = reports
        return metrics


# ── 混淆矩阵辅助 ──

def confusion_matrix(labels_true: list[str], labels_pred: list[str]) -> dict:
    """生成混淆矩阵。"""
    classes = sorted(set(labels_true) | set(labels_pred))
    matrix = {c: {c2: 0 for c2 in classes} for c in classes}
    for t, p in zip(labels_true, labels_pred):
        matrix[t][p] = matrix[t].get(p, 0) + 1
    return {"classes": classes, "matrix": matrix}
