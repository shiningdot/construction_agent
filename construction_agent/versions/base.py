"""Pipeline 基类定义。

所有版本必须实现 BasePipeline 接口，确保统一评测。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from construction_agent.models.accident import Accident


@dataclass
class PipelineResult:
    """单条处理结果。"""
    success: bool
    accident: Optional[Accident] = None
    error_message: str = ""
    processing_time: float = 0.0
    version: str = ""


@dataclass
class BatchResult:
    """批量处理结果。"""
    version: str
    total: int
    successes: list[PipelineResult] = field(default_factory=list)
    failures: list[PipelineResult] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return len(self.successes)

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.success_count / self.total * 100

    @property
    def avg_processing_time(self) -> float:
        if not self.successes:
            return 0.0
        return sum(r.processing_time for r in self.successes) / len(self.successes)

    def to_dict(self) -> dict:
        """转为可序列化字典，用于实验数据导出。"""
        return {
            "version": self.version,
            "total": self.total,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": round(self.success_rate, 2),
            "avg_processing_time": round(self.avg_processing_time, 2),
            "failures": [
                {
                    "index": i,
                    "error": f.error_message,
                    "time": round(f.processing_time, 2),
                }
                for i, f in enumerate(self.failures)
            ],
        }


class BasePipeline(ABC):
    """所有版本 Pipeline 的基类。"""

    version: str = "base"

    @abstractmethod
    def process_single(self, text: str, **kwargs) -> PipelineResult:
        """处理单条事故文本。"""
        ...

    def process_batch(
        self,
        texts: list[str],
        batch_size: int = 0,
        **kwargs,
    ) -> BatchResult:
        """批量处理，默认逐条调用 process_single。"""
        results = []
        for i, text in enumerate(texts):
            if batch_size and i >= batch_size:
                break
            result = self.process_single(text, **kwargs)
            results.append(result)

        return BatchResult(
            version=self.version,
            total=len(results),
            successes=[r for r in results if r.success],
            failures=[r for r in results if not r.success],
        )

    def get_info(self) -> dict:
        """返回版本信息，用于实验记录。"""
        return {
            "version": self.version,
            "class": self.__class__.__name__,
            "description": self.__doc__ or "",
        }
