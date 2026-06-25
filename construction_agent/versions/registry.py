"""版本注册表，统一管理所有 Pipeline 版本。

支持自动发现和懒加载：按需导入版本模块，无需手动注册。
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Optional

from construction_agent.versions.base import BasePipeline

_registry: dict[str, type[BasePipeline]] = {}
_discovered: bool = False


def _discover_versions():
    """自动发现所有版本模块。"""
    global _discovered
    if _discovered:
        return
    _discovered = True

    # 扫描 versions/ 下的子包（v1, v2, ...）
    versions_path = Path(__file__).parent
    for entry in versions_path.iterdir():
        if entry.is_dir() and entry.name.startswith("v"):
            # 尝试导入该版本的 pipeline 模块
            # @register 装饰器会在导入时自动执行
            try:
                importlib.import_module(f"construction_agent.versions.{entry.name}.pipeline")
            except (ImportError, Exception) as e:
                # 版本模块可能还未创建，忽略
                pass


def register(cls: type[BasePipeline]) -> type[BasePipeline]:
    """装饰器：注册 Pipeline 版本。"""
    version = getattr(cls, "version", None)
    if not version:
        raise ValueError(f"{cls.__name__} 必须设置 version 类属性")
    _registry[version] = cls
    return cls


def get_pipeline(version: str, **kwargs) -> Optional[BasePipeline]:
    """获取指定版本的 Pipeline 实例。"""
    _discover_versions()
    cls = _registry.get(version)
    if cls is None:
        return None
    return cls(**kwargs)


def list_versions() -> list[str]:
    """列出所有已注册的版本号。"""
    _discover_versions()
    return sorted(_registry.keys())
