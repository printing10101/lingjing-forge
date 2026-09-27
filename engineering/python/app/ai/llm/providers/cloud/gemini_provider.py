"""Google Gemini API LLM Provider。

支持 Google Gemini 系列模型，使用 Gemini 专属 API 协议。
端点：/v1beta/models, /v1beta/models/{model}:generateContent
认证方式：URL query 参数 ?key=API_KEY（非 Bearer header）
默认模型：gemini-1.5-flash
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.ai.llm.provider_base import (
    LLMProvider,
    ProviderConfig,
    ProviderError,
    ProviderHTTPError,
    ProviderStatus,
    ProviderType,
)

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """Google Gemini API Provider。"""

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = self.DEFAULT_BASE_URL
        if not config.default_model:
            config.default_model = "gemini-1.5-flash"
        config.provider_type = ProviderType.GEMINI
        super().__init__(config)

    def _build_auth_query(self) -> str:
        """构建 URL query 认证参数。"""
        if self.config.api_key:
            return f"?key={self.config.api_key}"
        return ""

    def _build_auth_headers(self) -> dict[str, str]:
        """覆盖：Gemini 使用 URL query 认证，不用 Bearer header。"""
        return {"Content-Type": "application/json"}

    async def detect(self) -> bool:
        """探测：验证 api_key 非空 + GET /models?key=... 返回 200。"""
        if not self.config.api_key:
            return False
        try:
            url = f"{self.config.base_url}/models{self._build_auth_query()}"
            response = await self._http_get(url)
            return response.status_code == 200
        except Exception as e:
            logger.debug("Gemini detect failed: %s", e)
            return False

    async def health_check(self) -> ProviderStatus:
        """健康检查：GET /models?key=... 验证可用性。"""
        if not self.config.api_key:
            self._update_status(ProviderStatus.UNCONFIGURED)
            return self._last_status
        try:
            url = f"{self.config.base_url}/models{self._build_auth_query()}"
            response = await self._http_get(url)
            if response.status_code == 200:
                self._update_status(ProviderStatus.ONLINE)
            else:
                self._update_status(ProviderStatus.OFFLINE)
        except Exception as e:
            logger.debug("Gemini health check failed: %s", e)
            self._update_status(ProviderStatus.OFFLINE)
        return self._last_status

    async def list_models(self) -> list[str]:
        """列出 Gemini 可用模型，解析 models[].name。"""
        if not self.config.api_key:
            return []
        try:
            url = f"{self.config.base_url}/models{self._build_auth_query()}"
            response = await self._http_get(url)
            if response.status_code != 200:
                return []
            data = response.json()
            # Gemini 返回的 name 格式为 "models/gemini-1.5-flash"，去除前缀
            models: list[str] = []
            for m in data.get("models", []):
                name = m.get("name", "")
                if name.startswith("models/"):
                    name = name[len("models/") :]
                if name:
                    models.append(name)
            return models
        except Exception as e:
            logger.warning("Gemini list_models failed: %s", e)
            return []

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> dict[str, Any]:
        """调用 Gemini /models/{model}:generateContent 端点。

        Gemini 请求/响应格式特殊：
        - 请求: {"contents": [{"parts": [{"text": ...}]}]}
        - 响应: candidates[0].content.parts[0].text
        """
        target_model = self._resolve_model(model)
        # 将 OpenAI 格式的 messages 转换为 Gemini contents 格式
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            # Gemini 中 assistant 对应 model
            if role == "assistant":
                role = "model"
            text = msg.get("content", "")
            contents.append(
                {
                    "role": role,
                    "parts": [{"text": text}],
                }
            )
        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        headers = self._build_auth_headers()
        url = f"{self.config.base_url}/models/{target_model}:generateContent{self._build_auth_query()}"
        start = time.time()
        response = await self._http_post(url, payload, headers)
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
        # 响应解析：candidates[0].content.parts[0].text
        candidates = data.get("candidates", [])
        if not candidates:
            raise ProviderError("API 返回空 candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise ProviderError("API 返回空 parts")
        text = parts[0].get("text", "")
        # finish_reason 映射
        finish_reason = candidates[0].get("finishReason", "STOP").lower()
        return {
            "content": text,
            "model": target_model,
            "finish_reason": finish_reason,
            "usage": data.get("usageMetadata", {}),
        }
