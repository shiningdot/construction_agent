"""JSON 格式输出模块。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from construction_agent.models.accident import Accident

logger = logging.getLogger(__name__)


class JSONFormatter:
    """JSON 格式化输出。"""

    @staticmethod
    def format(accident: Accident, indent: int = 2, ensure_ascii: bool = False) -> str:
        """将单条事故格式化为 JSON 字符串。"""
        return accident.to_json(indent=indent)

    @staticmethod
    def format_batch(accidents: list[Accident], indent: int = 2) -> str:
        """将多条事故格式化为 JSON 数组字符串。"""
        data = [a.to_dict() for a in accidents]
        return json.dumps(data, indent=indent, ensure_ascii=False, default=str)

    @staticmethod
    def save_single(accident: Accident, filepath: Path | str) -> bool:
        """保存单条事故到 JSON 文件。

        Args:
            accident: 事故数据
            filepath: 文件路径

        Returns:
            是否成功
        """
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(accident.to_json())
            logger.info(f"保存到 {path}")
            return True
        except Exception as e:
            logger.error(f"保存 JSON 失败: {e}")
            return False

    @staticmethod
    def save_batch(accidents: list[Accident], filepath: Path | str) -> bool:
        """保存多条事故到 JSON 文件。

        Args:
            accidents: 事故数据列表
            filepath: 文件路径

        Returns:
            是否成功
        """
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = JSONFormatter.format_batch(accidents)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"批量保存 {len(accidents)} 条到 {path}")
            return True
        except Exception as e:
            logger.error(f"批量保存 JSON 失败: {e}")
            return False
