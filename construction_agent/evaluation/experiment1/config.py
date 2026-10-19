"""
实验一配置：模型定义、API 端点、评测参数
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ModelConfig:
    """单个模型的配置"""
    name: str
    key: str
    provider: str
    model_id: str
    api_key_env: str
    base_url: Optional[str] = None
    description: str = ""
    api_type: str = "openai"
    enabled: bool = True
    supports_json_response: bool = True
    max_tokens: int = 4096
    temperature: float = 0.0  # 部分模型强制指定值


CORE_MODELS: List[ModelConfig] = [
    ModelConfig(
        name="DeepSeek V4",
        key="deepseek_v4",
        provider="DeepSeek",
        model_id="deepseek-v4-flash",
        api_key_env="ANTHROPIC_API_KEY",
        base_url="https://api.deepseek.com/anthropic",
        api_type="anthropic",
        description="国产，已联通",
    ),
    ModelConfig(
        name="Qwen 3.7",
        key="qwen3",
        provider="Alibaba",
        model_id="qwen-plus",
        api_key_env="QWEN_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_type="openai",
        description="阿里 Qwen 最新版",
        supports_json_response=False,
    ),
    ModelConfig(
        name="DeepSeek R1",
        key="deepseek_r1",
        provider="DeepSeek",
        model_id="deepseek-r1",
        api_key_env="ANTHROPIC_API_KEY",
        base_url="https://api.deepseek.com",
        api_type="openai",
        description="推理模型（暂不支持）",
        enabled=False,
        supports_json_response=False,
        max_tokens=8192,
    ),
    ModelConfig(
        name="GLM 5.2",
        key="glm52",
        provider="Zhipu AI",
        model_id="glm-5.2",
        api_key_env="ZHIPU_API_KEY",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_type="openai",
        description="智谱最新推理模型",
        supports_json_response=False,
        max_tokens=16384,
    ),
    ModelConfig(
        name="Kimi K2.6",
        key="kimi",
        provider="Moonshot AI",
        model_id="kimi-k2.6",
        api_key_env="KIMI_API_KEY",
        base_url="https://api.moonshot.cn/v1",
        api_type="openai",
        description="月之暗面 Kimi 最新版",
        supports_json_response=False,
        max_tokens=8192,
        temperature=1.0,  # K2.x 系列强制要求
    ),
]


def get_enabled_models(include_optional: bool = False) -> List[ModelConfig]:
    return [m for m in CORE_MODELS if m.enabled]


SAMPLE_SIZE = 200
GROUND_TRUTH_PATH = "/home/suntianhao/construction_agent/data/accidents_merged.json"
EXPERIMENT_DIR = "/home/suntianhao/construction_agent/experiments/experiment1"

INFERENCE_PARAMS = {
    "temperature": 0.0,
    "max_tokens": 4096,
}
