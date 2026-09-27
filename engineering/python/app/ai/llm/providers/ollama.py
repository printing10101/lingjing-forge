"""Ollama 本地 LLM Provider。

支持探测 Ollama 服务、列出已安装模型、调用对话补全 API。
Ollama 默认端口 11434，API 端点 /api/chat 和 /api/tags。

流式：原生 NDJSON 流（``chat_completion_stream``，stream_mode="native"），
由韧性层（``app.ai.llm._resilience``）自动包装熔断与异常桥接。
"""

from __future__ import annotations

import json
import logging
from typing import Any
from collections.abc import AsyncIterator
import time

import httpx

from app.ai.llm.provider_base import (
    LLMProvider,
    ProviderConfig,
    ProviderHTTPError,
    ProviderStatus,
    ProviderType,
)

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    """Ollama 本地 Provider。"""

    DEFAULT_PORT = 11434
    DEFAULT_BASE_URL = "http://127.0.0.1:11434"
    # 本地推理服务：请求绕过系统代理环境变量（防止 Clash 等劫持 127.0.0.1）
    _bypass_env_proxy = True

    def __init__(self, config: ProviderConfig) -> None:
        # 如果未指定 base_url，使用默认本地地址
        if not config.base_url:
            config.base_url = self.DEFAULT_BASE_URL
        # 确保类型正确
        config.provider_type = ProviderType.OLLAMA
        super().__init__(config)

    async def detect(self) -> bool:
        """探测 Ollama 服务是否运行。"""
        try:
            response = await self._http_get(f"{self.config.base_url}/api/tags")
            return response.status_code == 200
        except httpx.TimeoutException as e:
            logger.debug("Ollama detect timeout: %s", e)
            return False
        except httpx.TransportError as e:
            logger.debug("Ollama detect connection failed: %s", e)
            return False
        except Exception as e:
            logger.debug("Ollama detect failed: %s", e)
            return False

    async def health_check(self) -> ProviderStatus:
        """健康检查：调用 /api/tags 验证服务可用性。"""
        try:
            response = await self._http_get(f"{self.config.base_url}/api/tags")
            if response.status_code == 200:
                self._update_status(ProviderStatus.ONLINE)
            else:
                self._update_status(ProviderStatus.OFFLINE)
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPError) as e:
            logger.debug("Ollama health check failed: %s", e)
            self._update_status(ProviderStatus.OFFLINE)
        except Exception as e:
            logger.debug("Ollama health check failed: %s", e)
            self._update_status(ProviderStatus.OFFLINE)
        return self._last_status

    async def list_models(self) -> list[str]:
        """列出 Ollama 已安装的模型。"""
        try:
            response = await self._http_get(f"{self.config.base_url}/api/tags")
            if response.status_code != 200:
                return []
            data = response.json()
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except Exception as e:
            logger.warning("Ollama list_models failed: %s", e)
            return []

    async def _chat_payload(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        model: str | None,
        stream: bool,
    ) -> tuple[str, dict[str, Any]]:
        target_model = self._resolve_model(model)
        payload = {
            "model": target_model,
            "messages": messages,
            "stream": stream,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        return target_model, payload

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> dict[str, Any]:
        """调用 Ollama /api/chat 端点。

        Note:
            ``think=False`` 关闭 qwen3 系列默认开启的思考模式。否则在
            ``num_predict`` 较小时，所有 token 会被思考消耗，导致
            ``content`` 为空字符串（影响 SHARP ReAct 循环、工艺理解
            等所有结构化 JSON 输出场景）。对不支持思考模式的老模型
            （如 llama3.2）该参数无影响。
        """
        target_model, payload = await self._chat_payload(messages, max_tokens, temperature, model, stream=False)
        start = time.time()
        response = await self._http_post(f"{self.config.base_url}/api/chat", payload)
        self._measure_latency(start)
        if response.status_code != 200:
            raise ProviderHTTPError(
                f"Ollama API error: {response.status_code} - {response.text}",
                status_code=response.status_code,
                body=response.text,
            )
        data = response.json()
        self._update_status(ProviderStatus.ONLINE)
        return {
            "content": data.get("message", {}).get("content", ""),
            "model": target_model,
            "finish_reason": "stop",
            "usage": data.get("usage", {}),
        }

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """原生流式：Ollama /api/chat NDJSON（每行一个 JSON 对象）。"""
        target_model, payload = await self._chat_payload(messages, max_tokens, temperature, model, stream=True)
        client = await self._get_http_client()
        async with client.stream(
            "POST",
            f"{self.config.base_url}/api/chat",
            json=payload,
            timeout=self.config.timeout,
        ) as response:
            if response.status_code != 200:
                raise ProviderHTTPError(
                    f"Ollama API error (HTTP {response.status_code})",
                    status_code=response.status_code,
                )
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Ollama stream 跳过非 JSON 行: %.80s", line)
                    continue
                message = data.get("message") or {}
                piece = message.get("content", "") or ""
                done = bool(data.get("done", False))
                if piece or not done:
                    yield {"content": piece, "model": target_model, "done": done, "stream_mode": "native"}
                if done:
                    yield {
                        "content": "",
                        "model": target_model,
                        "done": True,
                        "stream_mode": "native",
                        "usage": {},
                    }
                    return
            # 服务端未发 done 帧即断流：补一个终止帧，保证契约完整
            yield {"content": "", "model": target_model, "done": True, "stream_mode": "native"}
