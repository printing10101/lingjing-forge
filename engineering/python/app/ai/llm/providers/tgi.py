"""Text Generation Inference (TGI) 本地 LLM Provider。

支持探测 TGI 服务、列出已加载模型、调用对话补全 API。
TGI 默认端口 8090，提供 OpenAI 兼容 API：
- GET  /v1/models
- POST /v1/chat/completions

公共实现在 :mod:`app.ai.llm.providers.openai_compat_base`。
"""

from __future__ import annotations

from typing import ClassVar

from app.ai.llm.provider_base import ProviderType
from app.ai.llm.providers.openai_compat_base import OpenAICompatLocalProvider, OpenAICompatPreset


class TGIProvider(OpenAICompatLocalProvider):
    """Text Generation Inference 本地 Provider（OpenAI 兼容 API）。"""

    preset: ClassVar[OpenAICompatPreset] = OpenAICompatPreset(
        provider_type=ProviderType.TGI,
        display_name="TGI",
        default_base_url="http://127.0.0.1:8090/v1",
        default_port=8090,
    )
