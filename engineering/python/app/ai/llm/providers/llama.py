"""Llama 本地 Provider（Meta Llama 系模型 / llama-server 推理栈）。

面向 Meta Llama 系模型的本地部署：由 llama.cpp ``llama-server``（或
兼容的 OpenAI 协议推理服务）加载 Llama GGUF 权重后提供
OpenAI 兼容 API：

- GET  /v1/models
- POST /v1/chat/completions

与 :class:`LlamaCppProvider`（llamacpp）的区别是定位：

- ``llamacpp``：通用 llama.cpp 服务探测入口（惯例端口 8080）；
- ``llama``：Llama 模型专用接口，默认直连 llama-server 上游
  （本机部署约定 8081，8080 由 model-proxy 自动切换代理占用）。

鉴权：llama-server 常以 ``--api-key-file`` 开启 Bearer 认证（本机
部署约定密钥文件 ``E:\\llama-cpp\\.api-key``，并镜像到环境变量
``LLAMA_LOCAL_API_KEY``）。本 Provider 读取环境变量
``LLAMA_LOCAL_API_KEY`` 作为默认 Key 来源（也可在 ProviderConfig
中直接配置 ``api_key``，配置值优先）。

公共实现在 :mod:`app.ai.llm.providers.openai_compat_base`。
"""

from __future__ import annotations

from typing import ClassVar

from app.ai.llm.provider_base import ProviderType
from app.ai.llm.providers.openai_compat_base import (
    OpenAICompatLocalProvider,
    OpenAICompatPreset,
)


class LlamaProvider(OpenAICompatLocalProvider):
    """Llama 本地 Provider（llama-server OpenAI 兼容 API）。"""

    preset: ClassVar[OpenAICompatPreset] = OpenAICompatPreset(
        provider_type=ProviderType.LLAMA,
        display_name="Llama",
        default_base_url="http://127.0.0.1:8081",
        default_port=8081,
        path_prefix="/v1",
        # llama-server 开启 --api-key-file 后必须带 Bearer 认证；
        # 环境变量 LLAMA_LOCAL_API_KEY 由部署脚本镜像密钥文件维护，
        # ProviderConfig.api_key 显式配置时优先于环境变量。
        default_api_key=None,
        api_key_env="LLAMA_LOCAL_API_KEY",
    )
