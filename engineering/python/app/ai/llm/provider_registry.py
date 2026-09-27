"""LLM Provider 注册表。

负责管理所有 Provider 配置的持久化（SQLite）、API Key 加密、
实例缓存、激活切换等生命周期管理。

设计要点：
- SQLite 持久化：配置存储在 python/data/llm_providers.db，与项目其他 DB 对齐
- API Key 加密：使用 Fernet 对称加密，密钥从环境变量或项目令牌派生
- 实例缓存：Provider 实例创建后缓存，配置变更时失效
- 激活互斥：同一时刻仅一个 Provider 处于 is_active=True
- 首次初始化：自动建表 + 种子默认 Provider 模板（全部 disabled）

本模块为门面：实现已拆分至 _db / _cipher / _factory / _registry。
"""

from __future__ import annotations

from app.ai.llm._cipher import APIKeyCipher  # noqa: F401
from app.ai.llm._db import _get_db_path  # noqa: F401
from app.ai.llm._factory import (  # noqa: F401
    _default_provider_templates,
    _load_all_provider_classes,
    _provider_base_url,
    _register_provider_class,
    create_provider,
)
from app.ai.llm._registry import (  # noqa: F401
    ProviderRegistry,
    get_registry,
    init_registry,
    reset_registry,
)

__all__ = [
    "APIKeyCipher",
    "ProviderRegistry",
    "_get_db_path",
    "_default_provider_templates",
    "_load_all_provider_classes",
    "_provider_base_url",
    "_register_provider_class",
    "create_provider",
    "get_registry",
    "init_registry",
    "reset_registry",
]
