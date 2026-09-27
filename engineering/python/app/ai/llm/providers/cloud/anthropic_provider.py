"""Anthropic Claude API LLM Provider。

支持 Anthropic Claude 系列模型，使用 Anthropic 专属 API 协议。
端点：/v1/messages
认证方式：x-api-key 头 + anthropic-version 头（非 Bearer Token）
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


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API Provider。"""

    DEFAULT_BASE_URL = "https://api.anthropic.com/v1"

    # 静态模型列表（Anthropic /v1/models 接口可能不可用）
    _STATIC_MODELS: list[str] = [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ]

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = self.DEFAULT_BASE_URL
        if not config.default_model:
            config.default_model = "claude-3-5-sonnet-20241022"
        config.provider_type = ProviderType.ANTHROPIC
        super().__init__(config)

    def _build_auth_headers(self) -> dict[str, str]:
        """覆盖：使用 x-api-key + anthropic-version 而非 Bearer。"""
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
        return headers

    async def detect(self) -> bool:
        """探测：验证 api_key 非空 + 最小化消息调用成功。"""
        if not self.config.api_key:
            return False
        try:
            # Anthropic 没有 /models 列表接口（或不可靠），使用最小化消息调用验证
            headers = self._build_auth_headers()
            payload = {
                "model": self.config.default_model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }
            response = await self._http_post(f"{self.config.base_url}/messages", payload, headers)
            return response.status_code == 200
        except Exception as e:
            logger.debug("Anthropic detect failed: %s", e)
            return False

    async def health_check(self) -> ProviderStatus:
        """健康检查：最小化消息调用验证可用性。"""
        if not self.config.api_key:
            self._update_status(ProviderStatus.UNCONFIGURED)
            return self._last_status
        try:
            headers = self._build_auth_headers()
            payload = {
                "model": self.config.default_model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }
            response = await self._http_post(f"{self.config.base_url}/messages", payload, headers)
            if response.status_code == 200:
                self._update_status(ProviderStatus.ONLINE)
            else:
                self._update_status(ProviderStatus.OFFLINE)
        except Exception as e:
            logger.debug("Anthropic health check failed: %s", e)
            self._update_status(ProviderStatus.OFFLINE)
        return self._last_status

    async def list_models(self) -> list[str]:
        """返回静态模型列表（Anthropic /v1/models 接口可能不可用）。"""
        return list(self._STATIC_MODELS)

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> dict[str, Any]:
        """调用 Anthropic /messages 端点（Anthropic 专属格式）。"""
        target_model = self._resolve_model(model)
        payload = {
            "model": target_model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        headers = self._build_auth_headers()
        start = time.time()
        response = await self._http_post(f"{self.config.base_url}/messages", payload, headers)
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
        # 响应解析：content[0].text
        content_blocks = data.get("content", [])
        if not content_blocks:
            raise ProviderError("API 返回空 content")
        text = content_blocks[0].get("text", "")
        return {
            "content": text,
            "model": target_model,
            "finish_reason": data.get("stop_reason", "end_turn"),
            "usage": data.get("usage", {}),
        }
