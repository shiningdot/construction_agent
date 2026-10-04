"""
多模型 API 客户端封装
依赖: httpx（已有），不引入新包。
"""

import json
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Dict

import httpx
from ..experiment1.config import ModelConfig


class ModelClient(ABC):
    def __init__(self, config: ModelConfig):
        self.config = config
        self.api_key = os.getenv(config.api_key_env, "")

    @abstractmethod
    def call(self, system_prompt: str, user_message: str) -> Dict[str, Any]:
        ...

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        PRICING = {
            "deepseek-v4-flash": (0.3, 0.6), "deepseek-r1": (0.55, 2.19),
            "qwen-plus": (0.8, 2.0), "glm-5.2": (2.0, 8.0),
            "kimi-k2.6": (2.0, 8.0),
        }
        ippk, oppk = PRICING.get(self.config.model_id, (1.0, 4.0))
        return (prompt_tokens / 1_000_000 * ippk) + (completion_tokens / 1_000_000 * oppk)


class OpenAICompatibleClient(ModelClient):
    """OpenAI 兼容 API (Qwen, DeepSeek R1, GLM)"""

    def call(self, system_prompt: str, user_message: str) -> Dict[str, Any]:
        try:
            base_url = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            payload = {
                "model": self.config.model_id,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            }
            if self.config.supports_json_response:
                payload["response_format"] = {"type": "json_object"}

            start = time.time()
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            elapsed = time.time() - start

            text = data["choices"][0]["message"].get("content", "").strip()
            usage = data.get("usage", {})
            pt = usage.get("prompt_tokens", 0)
            ct = usage.get("completion_tokens", 0)

            return {
                "success": True, "text": text, "latency": round(elapsed, 2),
                "prompt_tokens": pt, "completion_tokens": ct,
                "total_tokens": pt + ct,
                "cost_usd": self.estimate_cost(pt, ct), "error": None,
            }
        except Exception as e:
            return {"success": False, "text": "", "latency": 0,
                    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                    "cost_usd": 0, "error": str(e)}


class AnthropicCompatibleClient(ModelClient):
    """Anthropic 兼容 API (DeepSeek V4 走 /anthropic 端点)"""

    def call(self, system_prompt: str, user_message: str) -> Dict[str, Any]:
        try:
            base_url = (self.config.base_url or "https://api.anthropic.com/v1").rstrip("/")
            headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
            if self.api_key.startswith("sk-ant-"):
                headers["x-api-key"] = self.api_key
            else:
                headers["Authorization"] = f"Bearer {self.api_key}"

            payload = {
                "model": self.config.model_id,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            }

            start = time.time()
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(f"{base_url}/messages", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            elapsed = time.time() - start

            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "")
                    break
                elif block.get("type") == "tool_use":
                    text = json.dumps(block.get("input", {}), ensure_ascii=False)
                    break

            usage = data.get("usage", {})
            pt = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
            ct = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)

            return {
                "success": True, "text": text.strip(), "latency": round(elapsed, 2),
                "prompt_tokens": pt, "completion_tokens": ct,
                "total_tokens": pt + ct,
                "cost_usd": self.estimate_cost(pt, ct), "error": None,
            }
        except Exception as e:
            return {"success": False, "text": "", "latency": 0,
                    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                    "cost_usd": 0, "error": str(e)}


def create_client(config: ModelConfig) -> ModelClient:
    if config.api_type == "anthropic":
        return AnthropicCompatibleClient(config)
    return OpenAICompatibleClient(config)
