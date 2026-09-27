"""KoboldCpp 本地 LLM Provider。

支持探测 KoboldCpp 服务、列出已加载模型、调用对话补全 API。
KoboldCpp 默认端口 5001，提供 OpenAI 兼容 API：
- GET  /v1/models
- POST /v1/chat/completions

KoboldCpp 启动时通过 --port 5001 指定端口，并需开启 OpenAI 兼容接口。

公共实现在 :mod:`app.ai.llm.providers.openai_compat_base`。
"""

from __future__ import annotations

from typing import ClassVar

from app.ai.llm.provider_base import ProviderType
from app.ai.llm.providers.openai_compat_base import OpenAICompatLocalProvider, OpenAICompatPreset


class KoboldCppProvider(OpenAICompatLocalProvider):
    """KoboldCpp 本地 Provider（OpenAI 兼容 API）。"""

    preset: ClassVar[OpenAICompatPreset] = OpenAICompatPreset(
        provider_type=ProviderType.KOBOLDCPP,
        display_name="KoboldCpp",
        default_base_url="http://127.0.0.1:5001/v1",
        default_port=5001,
    )
