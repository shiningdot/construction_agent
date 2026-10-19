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

    # 硅基流动 SiliconFlow（OpenAI 兼容聚合网关：一把 key 通过 model 参数切换所有模型）
    # 后续若只保留硅基流动，把此处作为统一入口；注意它是 OpenAI 协议，
    # 调用点统一走 construction_agent.llm_client.create_llm_client() 的适配层。
    SILICONFLOW_API_KEY: str = os.getenv("SILICONFLOW_API_KEY", "")
    SILICONFLOW_BASE_URL: str = os.getenv(
        "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    SILICONFLOW_MODEL: str = os.getenv(
        "SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash")
    # 推理模型先花预算写思维链，4096 容易返回空文本，给一个更大的默认值
    SILICONFLOW_MAX_TOKENS: int = int(os.getenv("SILICONFLOW_MAX_TOKENS", "16384"))

    # LLM 供应商开关：anthropic（默认，含 DeepSeek 的 Anthropic 兼容端点）
    # 或 siliconflow（OpenAI 兼容）。改这一行即可全项目切换。
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")

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

    # RAG（法规/标准/类目卡检索，供 Step2 编码注入依据）
    # 默认关闭：既有 440 条标注与论文实验必须可复现，不能被检索增强悄悄改变。
    # 需要时在 .env 设 RAG_ENABLED=true，或在调用处显式传 use_rag=True。
    RAG_ENABLED: bool = os.getenv("RAG_ENABLED", "false").lower() in ("1", "true", "yes")
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))
    RAG_BUDGET_CHARS: int = int(os.getenv("RAG_BUDGET_CHARS", "2400"))
    RAG_CORPUS: str = os.getenv("RAG_CORPUS", "")  # 留空用 corpus/chunks.jsonl

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
