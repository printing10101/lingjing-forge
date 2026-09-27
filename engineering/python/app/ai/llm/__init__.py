"""LLM Provider 网关模块。

提供统一的 LLM Provider 抽象层，支持多种本地和云端 LLM 部署方式：
- 本地: Ollama / LM Studio / llama.cpp / vLLM / TGI / KoboldCpp
- 云端: OpenAI / Anthropic / DeepSeek / Qwen / Gemini / OpenAI 兼容

核心组件：
- ProviderBase: Provider 抽象基类
- ProviderRegistry: Provider 注册表（SQLite 持久化）
- AutoDetector: 本地 LLM 服务自动探测
- ProviderRouter: 智能路由策略
"""

from app.ai.llm.provider_base import (
    LLMProvider,
    ProviderCapability,
    ProviderConfig,
    ProviderStatus,
    ProviderType,
)
from app.ai.llm.provider_registry import (
    ProviderRegistry,
    get_registry,
    init_registry,
)
from app.ai.llm.router import ProviderRouter, get_router

__all__ = [
    "LLMProvider",
    "ProviderCapability",
    "ProviderConfig",
    "ProviderStatus",
    "ProviderType",
    "ProviderRegistry",
    "get_registry",
    "init_registry",
    "ProviderRouter",
    "get_router",
]
