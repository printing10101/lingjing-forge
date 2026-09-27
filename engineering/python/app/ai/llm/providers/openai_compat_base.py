"""本地 OpenAI 兼容 Provider 公共基类。

lmstudio / llamacpp / vllm / tgi / koboldcpp 五个本地推理服务的 API
完全同构（``GET {base}/models`` + ``POST {base}/chat/completions``），
公共实现收敛为本模块的预设驱动基类，各服务文件只声明差异（preset）。

差异点全部由 :class:`OpenAICompatPreset` 表达：

- ProviderType 与默认地址/端口（服务不同）；
- llama.cpp 的默认 base_url 不含 ``/v1``，路径需自带前缀；
- LM Studio 需要 OpenAI 兼容接口的占位 Authorization 头（本地服务
  不校验，官方文档公开字符串，可用环境变量覆盖）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, ClassVar

from app.ai.llm.provider_base import (
    LLMProvider,
    ProviderConfig,
    ProviderHTTPError,
    ProviderStatus,
    ProviderType,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAICompatPreset:
    """一个本地 OpenAI 兼容服务的静态差异描述。"""

    #: Provider 类型标识
    provider_type: ProviderType
    #: 日志 / 错误消息前缀（与历史实现保持一致，如 "Vllm"、"TGI"）
    display_name: str
    #: 默认 base_url（用户未配置时使用）
    default_base_url: str
    #: 默认端口（仅作元数据/探测表使用）
    default_port: int
    #: 请求路径前缀：默认地址含 ``/v1`` 的服务为 ""，llama.cpp 为 "/v1"
    path_prefix: str = ""
    #: 默认占位 API Key（None 表示不注入）
    default_api_key: str | None = None
    #: 覆盖占位 Key 的环境变量名
    api_key_env: str | None = None


class OpenAICompatLocalProvider(LLMProvider):
    """本地 OpenAI 兼容 Provider 基类。

    子类只需声明 ``preset: ClassVar[OpenAICompatPreset]``。
    """

    preset: ClassVar[OpenAICompatPreset]

    # 本地推理服务：请求绕过系统代理环境变量（防止 Clash 等劫持 127.0.0.1）
    _bypass_env_proxy = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 回填基类声明的类属性接口，保持与其它 Provider 的内省兼容
        cls.DEFAULT_PORT = cls.preset.default_port
        cls.DEFAULT_BASE_URL = cls.preset.default_base_url

    def __init__(self, config: ProviderConfig) -> None:
        # 如果未指定 base_url，使用预设的默认本地地址
        if not config.base_url:
            config.base_url = self.preset.default_base_url
        # 路径前缀规范化：用户配置的 base_url 已带前缀（如 llama.cpp 的
        # ".../v1"）时不再重复拼接，避免产生 "/v1/v1/models" 双前缀 URL。
        prefix = self.preset.path_prefix
        if prefix and config.base_url.rstrip("/").endswith(prefix):
            config.base_url = config.base_url.rstrip("/")[: -len(prefix)]
        # 占位 Key：环境变量优先（在实例化时读取，便于测试注入），
        # 回退到预设的公开占位字符串。default_api_key 为 None 且声明了
        # api_key_env 时为「纯环境变量」模式：环境变量缺失则不注入
        # （保持 api_key 为空，由调用方显式配置），供 llama-server 的
        # --api-key-file 这类无公开占位值的部署使用。
        if not config.api_key and (self.preset.default_api_key is not None or self.preset.api_key_env):
            env_key = os.environ.get(self.preset.api_key_env or "", "")
            config.api_key = env_key or (self.preset.default_api_key or "")
        # 确保类型正确
        config.provider_type = self.preset.provider_type
        super().__init__(config)

    # ------------------------------------------------------------------
    # OpenAI 兼容公共实现
    # ------------------------------------------------------------------

    @property
    def _display(self) -> str:
        return self.preset.display_name

    def _models_url(self) -> str:
        return f"{self.config.base_url}{self.preset.path_prefix}/models"

    def _chat_url(self) -> str:
        return f"{self.config.base_url}{self.preset.path_prefix}/chat/completions"

    async def detect(self) -> bool:
        """探测服务是否运行。"""
        try:
            response = await self._http_get(
                self._models_url(),
                headers=self._build_auth_headers(),
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug("%s detect failed: %s", self._display, e)
            return False

    async def health_check(self) -> ProviderStatus:
        """健康检查：调用 /models 验证服务可用性。"""
        try:
            response = await self._http_get(
                self._models_url(),
                headers=self._build_auth_headers(),
            )
            if response.status_code == 200:
                self._update_status(ProviderStatus.ONLINE)
            else:
                self._update_status(ProviderStatus.OFFLINE)
        except Exception as e:
            logger.debug("%s health check failed: %s", self._display, e)
            self._update_status(ProviderStatus.OFFLINE)
        return self._last_status

    async def list_models(self) -> list[str]:
        """列出服务已加载的模型。"""
        try:
            response = await self._http_get(
                self._models_url(),
                headers=self._build_auth_headers(),
            )
            if response.status_code != 200:
                return []
            data = response.json()
            return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        except Exception as e:
            logger.warning("%s list_models failed: %s", self._display, e)
            return []

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> dict[str, Any]:
        """调用 /chat/completions 端点（OpenAI 兼容）。"""
        target_model = self._resolve_model(model)
        payload = {
            "model": target_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        start = time.time()
        response = await self._http_post(
            self._chat_url(),
            payload,
            headers=self._build_auth_headers(),
        )
        self._measure_latency(start)
        if response.status_code != 200:
            raise ProviderHTTPError(
                f"{self._display} API error: {response.status_code} - {response.text}",
                status_code=response.status_code,
                body=response.text,
            )
        data = response.json()
        self._update_status(ProviderStatus.ONLINE)
        choices = data.get("choices", [])
        content = ""
        finish_reason = "stop"
        if choices:
            first = choices[0]
            content = first.get("message", {}).get("content", "")
            finish_reason = first.get("finish_reason", "stop")
        return {
            "content": content,
            "model": data.get("model", target_model),
            "finish_reason": finish_reason,
            "usage": data.get("usage", {}),
        }

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """原生流式：OpenAI 兼容 SSE（``data: {...}`` / ``data: [DONE]``）。"""
        target_model = self._resolve_model(model)
        payload = {
            "model": target_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        client = await self._get_http_client()
        async with client.stream(
            "POST",
            self._chat_url(),
            json=payload,
            headers=self._build_auth_headers(),
            timeout=self.config.timeout,
        ) as response:
            if response.status_code != 200:
                raise ProviderHTTPError(
                    f"{self._display} API error (HTTP {response.status_code})",
                    status_code=response.status_code,
                )
            async for line in response.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:") :].strip()
                if not data_str or data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.debug("%s stream 跳过非 JSON 行: %.80s", self._display, line)
                    continue
                choices = chunk.get("choices") or [{}]
                first = choices[0] or {}
                delta = first.get("delta") or {}
                piece = delta.get("content") or ""
                finish = first.get("finish_reason")
                chunk_model = chunk.get("model", target_model)
                if piece or finish:
                    yield {
                        "content": piece,
                        "model": chunk_model,
                        "done": finish is not None,
                        "stream_mode": "native",
                    }
                if finish:
                    yield {
                        "content": "",
                        "model": chunk_model,
                        "done": True,
                        "stream_mode": "native",
                    }
                    return
        yield {"content": "", "model": target_model, "done": True, "stream_mode": "native"}
