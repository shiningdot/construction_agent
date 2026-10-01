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
    DATASET_FILE: Path = DATA_DIR / "datasets" / "accidents_merged.json"
    DATASET_NORMALIZED: Path = DATA_DIR / "datasets" / "accidents_normalized.json"

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/caiems_v2.db")

    # MinerU PDF 解析
    MINERU_API_TOKEN: str = os.getenv("MINERU_API_TOKEN", "")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
