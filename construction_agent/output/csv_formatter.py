"""CSV 格式输出模块。"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional

from construction_agent.models.accident import Accident

logger = logging.getLogger(__name__)


class CSVFormatter:
    """CSV 格式化输出。"""

    @staticmethod
    def get_fieldnames() -> list[str]:
        """获取 CSV 列名。"""
        return [
            "id",              # 事故唯一ID
            "title",           # 事故标题
            "date",            # 日期
            "accident_time",   # 具体时刻
            "day_of_week",     # 星期
            "season",          # 季节
            "time_period",     # 时段
            "lighting",        # 光照情况
            "weather",         # 气象情况
            "location",        # 省级地点
            "city",            # 地级市
            "accident_location_detail",  # 具体部位
            "project_name",    # 项目名称
            "project_type",    # 工程类型
            "building_structure",       # 建筑结构
            "building_completion_year", # 建成年份
            "construction_phase", # 施工阶段（保留原始字眼）
            "accident_type",   # 事故类型
            "severity",        # 事故等级
            "deaths",          # 死亡人数
            "injuries",        # 受伤人数
            "direct_economic_loss",  # 直接经济损失
            "direct_cause",     # 直接原因
            "contractor",       # 施工单位
            "trapped_count",    # 被困人数
            "human_factors",   # 人的因素
            "machine_factors", # 设备因素
            "management_factors",   # 管理因素
            "environmental_factors", # 环境因素
            "summary",         # 事故摘要
            "source_urls",     # 来源链接
            "search_queries",  # 搜索查询
            "search_snippets", # 搜索摘要
            "image_urls",      # 图片来源
            "images",          # 本地图片
            "hfacs_codes",     # HFACS编码
            "hfacs_encoding_version",  # 编码版本
        ]

    @staticmethod
    def format_row(accident: Accident) -> dict:
        """将单条事故格式化为 CSV 行字典。"""
        return accident.to_flat_dict()

    @staticmethod
    def save_single(accident: Accident, filepath: Path | str) -> bool:
        """保存单条事故到 CSV 文件。

        Args:
            accident: 事故数据
            filepath: 文件路径

        Returns:
            是否成功
        """
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            fieldnames = CSVFormatter.get_fieldnames()
            row = CSVFormatter.format_row(accident)

            # 判断文件是否已存在（决定是否写 header）
            file_exists = path.exists()

            with open(path, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row)

            logger.info(f"追加写入 CSV: {path}")
            return True
        except Exception as e:
            logger.error(f"保存 CSV 失败: {e}")
            return False

    @staticmethod
    def save_batch(accidents: list[Accident], filepath: Path | str) -> bool:
        """批量保存事故到 CSV 文件。"""
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            fieldnames = CSVFormatter.get_fieldnames()

            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for accident in accidents:
                    writer.writerow(CSVFormatter.format_row(accident))

            logger.info(f"批量保存 {len(accidents)} 条到 CSV: {path}")
            return True
        except Exception as e:
            logger.error(f"批量保存 CSV 失败: {e}")
            return False
