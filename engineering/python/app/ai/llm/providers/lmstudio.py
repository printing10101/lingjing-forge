"""LM Studio 本地 LLM Provider。

支持探测 LM Studio 服务、列出已加载模型、调用对话补全 API。
LM Studio 默认端口 1234，提供 OpenAI 兼容 API：
- GET  /v1/models
- POST /v1/chat/completions

本地默认 API Key 可通过环境变量 ``LMSTUDIO_API_KEY`` 覆盖。
LM Studio 默认不验证此 key（本地服务安全模型依赖网络隔离而非 API 认证）。

公共实现在 :mod:`app.ai.llm.providers.openai_compat_base`。
"""

from __future__ import annotations

from typing import ClassVar

from app.ai.llm.provider_base import ProviderType
from app.ai.llm.providers.openai_compat_base import OpenAICompatLocalProvider, OpenAICompatPreset


class LMStudioProvider(OpenAICompatLocalProvider):
    """LM Studio 本地 Provider（OpenAI 兼容 API）。"""

    preset: ClassVar[OpenAICompatPreset] = OpenAICompatPreset(
        provider_type=ProviderType.LMSTUDIO,
        display_name="LMStudio",
        default_base_url="http://127.0.0.1:1234/v1",
        default_port=1234,
        # LM Studio 本地占位 key（OpenAI 兼容接口需要 Authorization 头）。
        # 这是 LM Studio 官方文档公开的占位字符串，非真实凭证，本地服务不校验此值。
        # 可通过 LMSTUDIO_API_KEY 环境变量覆盖。
        default_api_key="lm-studio",
        api_key_env="LMSTUDIO_API_KEY",
    )
