"""配置管理模块。"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（强制覆盖已有环境变量）
env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(env_path, override=True)


class Settings:
    """应用配置。"""

    # Claude API（也支持 DeepSeek 等兼容 Anthropic 格式的 API）
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    # 2026-10-05 补：**与 `BING_SEARCH_ENDPOINT` 是同一种病的第二次发作**——
    # 代码读了一个类里没定义的属性。这次是三处：
    #   `step1_basic_extractor.py:484` 裸访问 `settings.ANTHROPIC_BASE_URL`
    #   `step2_caics_encoder.py:239`    同上（这两处都在 `if key` 才求值，
    #                                   故只在 .env 配了 key 时才炸——更隐蔽）
    #   `llm_classifier.py:325`         写成 `getattr(settings, ..., None)`，
    #                                   **有兜底所以不炸**——同一个字段两种写法，
    #                                   一半炸一半不炸。
    # 这次修复原先只落在了主仓库分支（提交 `4e689fc`，2026-10-01），
    # worktree 分支从来没有过；两处 config.py 因此长期分叉。今在本分支补齐，
    # 内容与该提交一致。
    ANTHROPIC_BASE_URL: str = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    ANTHROPIC_MAX_TOKENS: int = int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096"))

    # Baidu Search API
    BAIDU_SEARCH_API_KEY: str = os.getenv("BAIDU_SEARCH_API_KEY", "")

    # Bing Search API（保留，备选）
    BING_SEARCH_API_KEY: str = os.getenv("BING_SEARCH_API_KEY", "")
    # 2026-09-30 补：bing_search.py:31 一直在读 BING_SEARCH_ENDPOINT，
    # 但本类从未定义该属性，导致 BingSearchEngine() 构造即抛
    # AttributeError（tests/test_search.py 两项失败即由此）。
    BING_SEARCH_ENDPOINT: str = os.getenv(
        "BING_SEARCH_ENDPOINT", "https://api.bing.microsoft.com/v7.0/search")

    # Web UI
    FLASK_SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
    FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    FLASK_HOST: str = os.getenv("FLASK_HOST", "127.0.0.1")
    FLASK_PORT: int = int(os.getenv("FLASK_PORT", "5000"))

    # Data
    DATA_DIR: Path = Path(__file__).resolve().parents[1] / "data"
    # ⚠️ 待决（2026-10-05）：主仓库分支把这一行改成了 `DATA_DIR / "datasets" /
    # "accidents_merged.json"`（同属 `4e689fc`），本分支仍是根目录。**两种写法
    # 都指不到真实文件**——实测主仓库 `data/` 下没有该文件、`data/datasets/`
    # 下只有 `raw_accidents.json`。故本轮**只补字段、不搬路径**：搬过来只是让
    # 两份一致，并不解决"配置指向不存在的文件"。属另一族问题，单列登记。
    DATASET_FILE: Path = DATA_DIR / "accidents_merged.json"
    """Web UI 展示的数据集文件（V1+V2 合并去重）。"""
    DATASET_NORMALIZED: Path = DATA_DIR / "datasets" / "accidents_normalized.json"

    # Database
    # `web/db.py:119` 读的是 `getattr(settings, "DATABASE_URL", None)`，有兜底
    # 所以缺了不炸；这里补上是为了与另一份副本一致、并让 `resolve_db_path()`
    # 的第二个来源真正可用。
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/caiems_v2.db")

    # MinerU PDF 解析（云端 API，`pdf_processor.py` 用它做扫描件 OCR）
    # 全仓无属性访问（`mohurd.py:454` / `mem.py:558` 那两处是**注释**，
    # 内容正是"Settings 上没这个字段"）。字段本身该在：`4e689fc` 已加。
    MINERU_API_TOKEN: str = os.getenv("MINERU_API_TOKEN", "")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
