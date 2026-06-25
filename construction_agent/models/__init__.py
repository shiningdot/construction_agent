"""数据模型模块。"""

from .accident import (
    Accident,
    AccidentType,
    SeverityLevel,
    HumanFactor,
    MachineFactor,
    ManagementFactor,
    EnvironmentalFactor,
    AccidentCreateRequest,
    AccidentExtractionResult,
)

__all__ = [
    "Accident",
    "AccidentType",
    "SeverityLevel",
    "HumanFactor",
    "MachineFactor",
    "ManagementFactor",
    "EnvironmentalFactor",
    "AccidentCreateRequest",
    "AccidentExtractionResult",
]
