"""vLLM 本地 LLM Provider。

支持探测 vLLM 服务、列出已加载模型、调用对话补全 API。
vLLM 默认端口 8000，提供 OpenAI 兼容 API：
- GET  /v1/models
- POST /v1/chat/completions

vLLM 启动命令通常为：python -m vllm.entrypoints.openai.api_server
                      --model <model> --port 8000。

公共实现在 :mod:`app.ai.llm.providers.openai_compat_base`，
本文件仅声明 vLLM 的差异预设。
"""

from __future__ import annotations

from typing import ClassVar

from app.ai.llm.provider_base import ProviderType
from app.ai.llm.providers.openai_compat_base import OpenAICompatLocalProvider, OpenAICompatPreset


class VllmProvider(OpenAICompatLocalProvider):
    """vLLM 本地 Provider（OpenAI 兼容 API）。"""

    preset: ClassVar[OpenAICompatPreset] = OpenAICompatPreset(
        provider_type=ProviderType.VLLM,
        display_name="Vllm",
        default_base_url="http://127.0.0.1:8000/v1",
        default_port=8000,
    )
