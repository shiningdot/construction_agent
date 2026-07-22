"""数据访问层 —— Repository 模式。

本质：业务代码通过 AccidentRepository 接口操作数据，
实现：SQLite（SQLAlchemy 2.0）/ JSON（DatasetManager 兼容）。
"""

from construction_agent.database.dataset_manager import DatasetManager
from construction_agent.database.repository import (
    AccidentRepository,
    AddResult,
    DatasetStats,
    ProcessingRecord,
    LlmApiRecord,
    SearchLogRecord,
    PdfProcessingRecord,
)
from construction_agent.database.sqlite_repository import SqliteAccidentRepository

__all__ = [
    "DatasetManager",
    "AccidentRepository",
    "SqliteAccidentRepository",
    "AddResult",
    "DatasetStats",
    "ProcessingRecord",
    "LlmApiRecord",
    "SearchLogRecord",
    "PdfProcessingRecord",
]


_repository: AccidentRepository | None = None


def get_repository(db_type: str | None = None) -> AccidentRepository:
    """获取数据仓库实例（单例）。

    Args:
        db_type: "sqlite" / "json"。默认从环境变量 DATABASE_TYPE 读取，未设置则 "json"（向后兼容）。
    """
    global _repository

    if _repository is not None:
        return _repository

    import os
    from construction_agent.config import settings

    db_type = db_type or os.getenv("DATABASE_TYPE", "json")

    if db_type == "sqlite":
        repo = SqliteAccidentRepository(settings.DATABASE_URL)
        repo.initialize()
        _repository = repo
    else:
        _repository = DatasetManager()

    return _repository


def reset_repository():
    """重置仓库单例（测试用）。"""
    global _repository
    _repository = None
