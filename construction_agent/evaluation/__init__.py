"""评测框架。

支持对任意版本 Pipeline 进行统一评测，生成可对比的实验数据。
用于论文中的实验对比和消融分析。
"""

from construction_agent.evaluation.evaluator import (
    Evaluator,
    EvaluationResult,
    FieldAccuracy,
)
from construction_agent.evaluation.reporter import Reporter
from construction_agent.evaluation.metrics import MetricsCalculator

__all__ = ["Evaluator", "EvaluationResult", "FieldAccuracy", "Reporter", "MetricsCalculator"]
