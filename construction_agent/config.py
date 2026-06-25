"""配置管理模块。"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（强制覆盖已有环境变量）
env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(env_path, override=True)


class Settings:
    """应用配置。"""

    # Claude API
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    ANTHROPIC_MAX_TOKENS: int = int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096"))

    # Baidu Search API
    BAIDU_SEARCH_API_KEY: str = os.getenv("BAIDU_SEARCH_API_KEY", "")

    # Bing Search API（保留，备选）
    BING_SEARCH_API_KEY: str = os.getenv("BING_SEARCH_API_KEY", "")

    # Web UI
    FLASK_SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
    FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    FLASK_HOST: str = os.getenv("FLASK_HOST", "127.0.0.1")
    FLASK_PORT: int = int(os.getenv("FLASK_PORT", "5000"))

    # Data
    DATA_DIR: Path = Path(__file__).resolve().parents[1] / "data"
    DATASET_FILE: Path = DATA_DIR / "accidents_merged.json"
    """Web UI 展示的数据集文件（V1+V2 合并去重）。"""

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
