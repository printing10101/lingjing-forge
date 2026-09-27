"""llama.cpp 本地 LLM Provider。

支持探测 llama.cpp server 服务、列出模型、调用对话补全 API。
llama.cpp 默认端口 8080，提供 OpenAI 兼容 API：
- GET  /v1/models
- POST /v1/chat/completions

llama.cpp server 启动命令通常为：./server --host 0.0.0.0 --port 8080。

注意：与其它本地 Provider 不同，llama.cpp 的默认 base_url 不含
``/v1``，路径前缀由 preset 的 ``path_prefix="/v1"`` 提供。

公共实现在 :mod:`app.ai.llm.providers.openai_compat_base`。
"""

from __future__ import annotations

from typing import ClassVar

from app.ai.llm.provider_base import ProviderType
from app.ai.llm.providers.openai_compat_base import OpenAICompatLocalProvider, OpenAICompatPreset


class LlamaCppProvider(OpenAICompatLocalProvider):
    """llama.cpp 本地 Provider（OpenAI 兼容 API）。"""

    preset: ClassVar[OpenAICompatPreset] = OpenAICompatPreset(
        provider_type=ProviderType.LLAMACPP,
        display_name="LlamaCpp",
        default_base_url="http://127.0.0.1:8080",
        default_port=8080,
        path_prefix="/v1",
    )
