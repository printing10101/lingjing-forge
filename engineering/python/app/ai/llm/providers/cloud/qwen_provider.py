"""阿里通义千问 DashScope API LLM Provider。

使用 DashScope 的 OpenAI 兼容模式。
端点：/compatible-mode/v1/models, /compatible-mode/v1/chat/completions
认证方式：Bearer Token
默认模型：qwen-plus
"""

from __future__ import annotations

import logging
from typing import Any
import time

from app.ai.llm.provider_base import (
    LLMProvider,
    ProviderConfig,
    ProviderError,
    ProviderHTTPError,
    ProviderStatus,
    ProviderType,
)

logger = logging.getLogger(__name__)


class QwenProvider(LLMProvider):
    """阿里通义千问 DashScope API Provider（OpenAI 兼容模式）。"""

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = self.DEFAULT_BASE_URL
        if not config.default_model:
            config.default_model = "qwen-plus"
        config.provider_type = ProviderType.QWEN
        super().__init__(config)

    async def detect(self) -> bool:
        """探测：验证 api_key 非空 + GET /models 返回 200。"""
        if not self.config.api_key:
            return False
        try:
            headers = self._build_auth_headers()
            response = await self._http_get(f"{self.config.base_url}/models", headers)
            return response.status_code == 200
        except Exception as e:
            logger.debug("Qwen detect failed: %s", e)
            return False

    async def health_check(self) -> ProviderStatus:
        """健康检查：同 detect 逻辑。"""
        if not self.config.api_key:
            self._update_status(ProviderStatus.UNCONFIGURED)
            return self._last_status
        try:
            headers = self._build_auth_headers()
            response = await self._http_get(f"{self.config.base_url}/models", headers)
            if response.status_code == 200:
                self._update_status(ProviderStatus.ONLINE)
            else:
                self._update_status(ProviderStatus.OFFLINE)
        except Exception as e:
            logger.debug("Qwen health check failed: %s", e)
            self._update_status(ProviderStatus.OFFLINE)
        return self._last_status

    async def list_models(self) -> list[str]:
        """列出 Qwen 可用模型。"""
        if not self.config.api_key:
            return []
        try:
            headers = self._build_auth_headers()
            response = await self._http_get(f"{self.config.base_url}/models", headers)
            if response.status_code != 200:
                return []
            data = response.json()
            return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        except Exception as e:
            logger.warning("Qwen list_models failed: %s", e)
            return []

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> dict[str, Any]:
        """调用 Qwen /chat/completions 端点（OpenAI 兼容格式）。"""
        target_model = self._resolve_model(model)
        payload = {
            "model": target_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = self._build_auth_headers()
        start = time.time()
        response = await self._http_post(f"{self.config.base_url}/chat/completions", payload, headers)
        self._measure_latency(start)
        if response.status_code != 200:
            self._update_status(ProviderStatus.OFFLINE)
            raise ProviderHTTPError(
                f"API error: {response.status_code} - {response.text}",
                status_code=response.status_code,
                body=response.text,
            )
        data = response.json()
        self._update_status(ProviderStatus.ONLINE)
        choices = data.get("choices", [])
        if not choices:
            raise ProviderError("API 返回空 choices")
        choice = choices[0]
        return {
            "content": choice.get("message", {}).get("content", ""),
            "model": target_model,
            "finish_reason": choice.get("finish_reason", "stop"),
            "usage": data.get("usage", {}),
        }
