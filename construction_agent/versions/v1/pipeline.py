"""V1 Pipeline 实现。

封装现有的 ClaudeExtractor 到标准 Pipeline 接口，
支持统一评测和版本对比。
"""

from __future__ import annotations

import time
from typing import Optional

from construction_agent.models.accident import Accident
from construction_agent.extraction.claude_extractor import ClaudeExtractor
from construction_agent.versions.base import BasePipeline, PipelineResult, BatchResult
from construction_agent.versions.registry import register
from construction_agent.database.dataset_manager import DatasetManager


@register
class V1Pipeline(BasePipeline):
    """V1 Pipeline：直接 LLM 提取，固定流程。

    特点：
    - 直接调用 Claude API 进行结构化提取
    - 不联网搜索，纯文本提取
    - 自动派生字段（星期、季节、时段、光照）
    - 支持 CSV 批量导入
    """

    version = "v1"

    def __init__(
        self,
        api_key: Optional[str] = None,
        dataset_path: Optional[str] = None,
    ):
        self.extractor = ClaudeExtractor(api_key=api_key)
        self.db = DatasetManager(filepath=dataset_path)

    def process_single(self, text: str, **kwargs) -> PipelineResult:
        """处理单条事故文本。

        Args:
            text: 事故描述文本

        Returns:
            PipelineResult
        """
        t0 = time.time()
        version = self.version

        try:
            result = self.extractor.extract(text)
            if result is None:
                return PipelineResult(
                    success=False,
                    error_message="LLM 提取返回空",
                    processing_time=time.time() - t0,
                    version=version,
                )

            accident = result.to_accident(raw_input=text)
            elapsed = time.time() - t0

            return PipelineResult(
                success=True,
                accident=accident,
                processing_time=elapsed,
                version=version,
            )

        except Exception as e:
            return PipelineResult(
                success=False,
                error_message=f"{type(e).__name__}: {e}",
                processing_time=time.time() - t0,
                version=version,
            )

    def process_batch_from_csv(
        self,
        csv_path: str,
        start: int = 0,
        count: Optional[int] = None,
        resume: bool = False,
        retry: int = 3,
    ) -> BatchResult:
        """从 CSV 文件批量处理。

        Args:
            csv_path: CSV 文件路径
            start: 起始行
            count: 处理条数（None=到末尾）
            resume: 是否跳过已存在的数据
            retry: 失败重试次数

        Returns:
            BatchResult
        """
        import csv
        from pathlib import Path

        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV 文件不存在: {csv_path}")

        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            all_rows = list(reader)

        total_rows = len(all_rows)
        end = min(start + count, total_rows) if count else total_rows
        batch = all_rows[start:end]

        # 续传：加载已有数据集标题
        existing_titles: set[str] = set()
        if resume:
            for r in self.db.get_all():
                t = (r.get("title") or "").strip()
                if t:
                    existing_titles.add(t)

        results = []

        for i, row in enumerate(batch):
            global_idx = start + i
            seq = row.get("序列", "").strip()
            text = (row.get("爬虫") or "").strip()
            title_csv = (row.get("事故名") or "").strip()

            if not text:
                results.append(PipelineResult(
                    success=False,
                    error_message="爬虫字段为空",
                    version=self.version,
                ))
                continue

            if resume and title_csv in existing_titles:
                results.append(PipelineResult(
                    success=True,
                    accident=None,
                    processing_time=0,
                    version=self.version,
                ))
                continue

            # 带重试的处理
            pr = self._process_with_retry(text, retry, global_idx, seq, title_csv)
            results.append(pr)

            # 成功时自动保存
            if pr.success and pr.accident:
                self.db.add(pr.accident)

        return BatchResult(
            version=self.version,
            total=len(results),
            successes=[r for r in results if r.success],
            failures=[r for r in results if not r.success],
        )

    def _process_with_retry(
        self,
        text: str,
        max_retries: int,
        global_idx: int,
        seq: str,
        title_csv: str,
    ) -> PipelineResult:
        """带重试的提取。"""
        last_error = ""
        for attempt in range(1, max_retries + 1):
            pr = self.process_single(text)
            if pr.success:
                return pr
            last_error = pr.error_message
            if attempt < max_retries:
                delay = 3 ** (attempt - 1)
                time.sleep(delay)
        return PipelineResult(
            success=False,
            error_message=f"重试{max_retries}次均失败: {last_error}",
            version=self.version,
        )

    def get_info(self) -> dict:
        """版本详细信息。"""
        return {
            "version": "v1",
            "description": "直接 LLM 提取，固定流程 Pipeline",
            "extraction_method": "Claude API + tool_use",
            "features": [
                "纯文本提取",
                "自动派生字段（星期/季节/时段/光照）",
                "四维度致因分析（人/机/管/环）",
                "按标题去重",
                "CSV 批量续传",
                "错误重试（指数退避）",
            ],
            "model": self.extractor.model,
        }
