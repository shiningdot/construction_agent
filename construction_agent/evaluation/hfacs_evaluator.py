"""HFACS-CS 编码质量评测框架。

对 29 个 HFACS 编码逐一做二元分类评测，
支持 LLM vs 关键词方法对比。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# HFACS 层级信息
LEVEL_NAMES = {
    1: "组织因素", 2: "不安全监督", 3: "不安全行为的前提条件",
    4: "不安全行为", 5: "施工现场条件", 6: "物理环境",
}

ALL_HFACS_CODES = [
    "O1", "O2", "O3", "O4",
    "S1", "S2", "S3", "S4", "S5",
    "P1", "P2", "P3", "P4",
    "A1", "A2", "A3", "A4", "A5",
    "W1", "W2", "W3", "W4", "W5", "W6",
    "E1", "E2", "E3", "E4", "E5",
]


def code_to_level(code: str) -> int:
    prefix = code[0] if code else ""
    return {"O": 1, "S": 2, "P": 3, "A": 4, "W": 5, "E": 6}.get(prefix, 0)


@dataclass
class HfacsCodeMetrics:
    """单个 HFACS 编码的评测指标。"""
    code: str
    level: int
    level_name: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "level": self.level,
            "level_name": self.level_name,
            "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass
class HfacsEvaluationReport:
    """完整 HFACS 评测报告。"""
    per_code: list[HfacsCodeMetrics] = field(default_factory=list)
    per_level: dict[int, dict] = field(default_factory=dict)
    macro_avg: dict = field(default_factory=dict)
    exact_match_rate: float = 0.0
    hamming_distance: float = 0.0
    total_records: int = 0

    def to_dict(self) -> dict:
        return {
            "total_records": self.total_records,
            "exact_match_rate": round(self.exact_match_rate, 4),
            "hamming_distance": round(self.hamming_distance, 4),
            "macro_avg": {k: round(v, 4) for k, v in self.macro_avg.items()},
            "per_level": {
                str(lvl): {k: round(v, 4) for k, v in metrics.items()}
                for lvl, metrics in self.per_level.items()
            },
            "per_code": [m.to_dict() for m in self.per_code],
        }

    def to_markdown(self, title: str = "HFACS 编码评测报告") -> str:
        lines = [f"# {title}", f"**总记录**: {self.total_records}", ""]
        # 宏观指标
        ma = self.macro_avg
        lines.append(f"**Macro Avg**: P={ma['precision']:.1%} R={ma['recall']:.1%} F1={ma['f1']:.1%}")
        lines.append(f"**Exact Match**: {self.exact_match_rate:.1%}")
        lines.append(f"**Hamming Distance**: {self.hamming_distance:.4f}\n")

        # 逐层级
        lines.append("## 逐层级指标\n")
        lines.append("| Level | 层级 | Precision | Recall | F1 |")
        lines.append("|-------|------|-----------|--------|-----|")
        for lvl in range(1, 7):
            m = self.per_level.get(lvl, {})
            lines.append(
                f"| L{lvl} | {LEVEL_NAMES.get(lvl,'')} | "
                f"{m.get('precision',0):.1%} | {m.get('recall',0):.1%} | {m.get('f1',0):.1%} |"
            )

        # 逐编码
        lines.append("\n## 逐编码指标\n")
        lines.append("| 编码 | 层级 | Precision | Recall | F1 | Support |")
        lines.append("|------|------|-----------|--------|-----|---------|")
        for cm in self.per_code:
            support = cm.tp + cm.fn
            lines.append(
                f"| {cm.code} | L{cm.level} | "
                f"{cm.precision:.1%} | {cm.recall:.1%} | {cm.f1:.1%} | {support} |"
            )
        return "\n".join(lines)


def evaluate_hfacs(
    predictions: list[list[str]],
    ground_truth: list[list[str]],
) -> HfacsEvaluationReport:
    """评测 HFACS 编码质量。

    对 29 个编码逐一做二元分类，计算逐编码/逐层级/宏观指标。

    Args:
        predictions: 预测的编码列表，每条记录一个 list[str]
        ground_truth: 真实的编码列表

    Returns:
        HfacsEvaluationReport
    """
    n = len(predictions)
    if n != len(ground_truth):
        raise ValueError(f"预测({n})和真实({len(ground_truth)})数量不一致")

    per_code = []
    for code in ALL_HFACS_CODES:
        tp = fp = fn = tn = 0
        for pred, truth in zip(predictions, ground_truth):
            in_pred = code in pred
            in_truth = code in truth
            if in_pred and in_truth:
                tp += 1
            elif in_pred and not in_truth:
                fp += 1
            elif not in_pred and in_truth:
                fn += 1
            else:
                tn += 1
        level = code_to_level(code)
        per_code.append(HfacsCodeMetrics(
            code=code, level=level,
            level_name=LEVEL_NAMES.get(level, ""),
            tp=tp, fp=fp, fn=fn, tn=tn,
        ))

    # 逐层级聚合
    per_level = {}
    for lvl in range(1, 7):
        codes_in_level = [c for c in per_code if c.level == lvl]
        if not codes_in_level:
            continue
        tp = sum(c.tp for c in codes_in_level)
        fp = sum(c.fp for c in codes_in_level)
        fn = sum(c.fn for c in codes_in_level)
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        per_level[lvl] = {"precision": p, "recall": r, "f1": f}

    # Macro avg
    macro_p = sum(c.precision for c in per_code) / len(per_code)
    macro_r = sum(c.recall for c in per_code) / len(per_code)
    macro_f = sum(c.f1 for c in per_code) / len(per_code)

    # Exact match rate (exact set equality per record)
    exact_matches = sum(
        1 for p, t in zip(predictions, ground_truth)
        if set(p) == set(t)
    )

    # Hamming distance (average per-code disagreement rate)
    hamming = 0.0
    total_pairs = n * len(ALL_HFACS_CODES)
    if total_pairs > 0:
        mismatches = sum(
            1 for p, t in zip(predictions, ground_truth)
            for code in ALL_HFACS_CODES
            if (code in p) != (code in t)
        )
        hamming = mismatches / total_pairs

    return HfacsEvaluationReport(
        per_code=per_code,
        per_level=per_level,
        macro_avg={"precision": macro_p, "recall": macro_r, "f1": macro_f},
        exact_match_rate=exact_matches / n if n > 0 else 0.0,
        hamming_distance=hamming,
        total_records=n,
    )


def compare_encoding_methods(
    llm_predictions: list[list[str]],
    keyword_predictions: list[list[str]],
    ground_truth: list[list[str]],
) -> dict:
    """比较 LLM 编码 vs 关键词编码的差异，以 ground_truth 为基准。

    Returns:
        {
            "llm": HfacsEvaluationReport,
            "keyword": HfacsEvaluationReport,
            "improvement": {"precision": +x, "recall": +x, "f1": +x},
            "llm_minus_keyword": {"a_better": N, "keyword_better": M, "same": K},
        }
    """
    llm_report = evaluate_hfacs(llm_predictions, ground_truth)
    kw_report = evaluate_hfacs(keyword_predictions, ground_truth)

    llm_f1 = llm_report.macro_avg["f1"]
    kw_f1 = kw_report.macro_avg["f1"]

    # Per-code comparison
    llm_better = sum(
        1 for l, k in zip(llm_report.per_code, kw_report.per_code)
        if l.f1 > k.f1
    )
    kw_better = sum(
        1 for l, k in zip(llm_report.per_code, kw_report.per_code)
        if k.f1 > l.f1
    )
    same = len(ALL_HFACS_CODES) - llm_better - kw_better

    return {
        "llm": llm_report,
        "keyword": kw_report,
        "improvement": {
            "precision": llm_report.macro_avg["precision"] - kw_report.macro_avg["precision"],
            "recall": llm_report.macro_avg["recall"] - kw_report.macro_avg["recall"],
            "f1": llm_f1 - kw_f1,
        },
        "per_code_comparison": {
            "llm_better": llm_better,
            "keyword_better": kw_better,
            "same": same,
        },
    }
