"""LLM client implementations for Ollama and cloud providers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.exceptions import AppException, LLMProviderException

logger = logging.getLogger(__name__)


class LLMError(LLMProviderException):
    """Base exception for LLM client errors。

    2026-09 全量升格：legacy 客户端异常统一挂接 6xxx 分级异常体系
    （默认 6010），走统一错误中间件时能返回 code/level/retryable 结构化
    响应；``isinstance(e, LLMError)`` 等既有判断完全兼容。
    """

    def __init__(self, message: str = "大模型调用异常", detail: Any = None) -> None:
        super().__init__(provider="LLM", message=message, detail=detail)


class RateLimitError(LLMError):
    """Raised when the API rate limit is exceeded (HTTP 429)。错误码 6012。"""

    def __init__(self, message: str = "LLM 服务达到限流阈值", detail: Any = None) -> None:
        super().__init__(message, detail=detail)
        self.code = 6012
        self.retryable = True
        self.hint = "请稍后重试"


class ServiceUnavailableError(LLMError):
    """Raised when the API service is temporarily unavailable (HTTP 5xx)。错误码 6010。"""

    def __init__(self, message: str = "LLM 服务暂时不可用", detail: Any = None) -> None:
        super().__init__(message, detail=detail)
        self.retryable = True
        self.hint = "请检查 AI 服务状态或稍后重试"


class InvalidResponseError(LLMError):
    """Raised when the API returns an empty or malformed response。错误码 6003。"""

    def __init__(self, message: str = "LLM 响应无效", detail: Any = None) -> None:
        super().__init__(message, detail=detail)
        self.code = 6003
        self.retryable = True
        self.hint = "请重试请求"


DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0


# 共享 httpx.AsyncClient 单例（连接池复用，避免每次调用都新建客户端）
# 原实现每次 chat_completion 都 `async with httpx.AsyncClient(...)` 新建客户端，
# 导致 TLS 握手重复、连接无法复用。改为共享单例后，所有 LLM 调用复用同一连接池。
# 单次请求的 timeout 仍在 post() 调用级别通过 timeout=self.timeout 覆盖。

_shared_http_client: httpx.AsyncClient | None = None
# [H1] asyncio.Lock 懒初始化：模块级创建会绑定到导入时的事件循环，
# 在多事件循环场景（如 FastAPI + 测试）下抛 RuntimeError。
_shared_http_client_lock: asyncio.Lock | None = None

# 共享连接池配置：默认 60s 总超时，5s 连接超时，最多 100 连接，20 keepalive
_SHARED_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
_SHARED_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)


def _get_shared_http_client_lock() -> asyncio.Lock:
    """懒初始化共享 httpx 客户端锁，绑定到首次调用的事件循环。"""
    global _shared_http_client_lock
    if _shared_http_client_lock is None:
        _shared_http_client_lock = asyncio.Lock()
    return _shared_http_client_lock


async def get_shared_http_client() -> httpx.AsyncClient:
    """获取共享的 httpx.AsyncClient 单例。

    使用双重检查锁定（DCL）确保并发安全且无锁开销。
    """
    global _shared_http_client
    if _shared_http_client is not None:
        return _shared_http_client
    async with _get_shared_http_client_lock():
        if _shared_http_client is not None:
            return _shared_http_client
        _shared_http_client = httpx.AsyncClient(
            timeout=_SHARED_TIMEOUT,
            limits=_SHARED_LIMITS,
        )
        logger.info("Shared httpx.AsyncClient initialized (connection pool reuse)")
        return _shared_http_client


# 本地推理服务专用客户端（trust_env=False）。
# 背景：httpx 默认 trust_env=True 会读取 HTTP(S)_PROXY 环境变量，
# 用户机器上常驻 Clash 等系统代理时，发往 127.0.0.1 的 LLM 请求会被
# 代理劫持（实测出现流式 404 / 连接复用异常）。本地服务直连，必须
# 绕过环境代理；云端 Provider 仍走默认客户端（可能需要代理出境）。
_shared_http_client_no_proxy: httpx.AsyncClient | None = None


async def get_shared_http_client_no_proxy() -> httpx.AsyncClient:
    """获取绕过系统代理环境变量的共享客户端（本地推理服务专用）。"""
    global _shared_http_client_no_proxy
    if _shared_http_client_no_proxy is not None:
        return _shared_http_client_no_proxy
    async with _get_shared_http_client_lock():
        if _shared_http_client_no_proxy is not None:
            return _shared_http_client_no_proxy
        _shared_http_client_no_proxy = httpx.AsyncClient(
            timeout=_SHARED_TIMEOUT,
            limits=_SHARED_LIMITS,
            trust_env=False,
        )
        logger.info("Shared no-proxy httpx.AsyncClient initialized (local inference)")
        return _shared_http_client_no_proxy


async def close_shared_http_client() -> None:
    """关闭共享的 httpx.AsyncClient（FastAPI shutdown 时调用）。"""
    global _shared_http_client, _shared_http_client_no_proxy
    if _shared_http_client is not None:
        try:
            await _shared_http_client.aclose()
        except (RuntimeError, httpx.HTTPError) as e:
            logger.debug("Shared httpx client close failed: %s", e, exc_info=True)
        _shared_http_client = None
        logger.info("Shared httpx.AsyncClient closed")
    if _shared_http_client_no_proxy is not None:
        try:
            await _shared_http_client_no_proxy.aclose()
        except (RuntimeError, httpx.HTTPError) as e:
            logger.debug("Shared no-proxy httpx client close failed: %s", e, exc_info=True)
        _shared_http_client_no_proxy = None


def _classify_error(status_code: int, body: str) -> LLMError:
    # 安全纪律：body 不进异常消息（AppException 会被中间件原样回传客户端，
    # 上游响应体可能含账号/配额/内部端点信息）
    if status_code == 429:
        return RateLimitError("LLM 服务达到限流阈值 (HTTP 429)")
    if 500 <= status_code < 600:
        return ServiceUnavailableError(f"LLM 服务暂时不可用 (HTTP {status_code})")
    return LLMError(f"LLM 服务错误 (HTTP {status_code})")


class BaseLLMClient:
    """Base class for LLM clients with shared retry logic."""

    def __init__(
        self,
        timeout: int = 60,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def _build_payload(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        model: str | None,
    ) -> tuple[dict[str, Any], dict[str, str] | None, str]:
        raise NotImplementedError("LLM 客户端必须实现 _do_call() 方法来调用模型 API")

    def _parse_response(self, data: dict[str, Any], model: str) -> dict[str, Any]:
        raise NotImplementedError("LLM 客户端必须实现 _parse_response() 方法来解析模型响应")

    def _default_model(self) -> str:
        """Return the default model name for this client."""
        raise NotImplementedError("LLM 客户端必须实现 _default_model() 方法返回默认模型名称")

    @staticmethod
    def _validate_inputs(
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> None:
        if not messages:
            raise ValueError(
                "LLM API 调用失败：'messages' 参数不能为空。LLM API 需要至少一条消息（包含 role 和 content）才能发起请求。请传入格式如 [{'role': 'user', 'content': 'your prompt'}] 的消息列表。"
            )
        if max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1, got {max_tokens}")
        if not (0.0 <= temperature <= 2.0):
            raise ValueError(
                f"LLM API 调用失败：'temperature' 参数值必须在 [0.0, 2.0] 区间内，当前值: {temperature}。temperature 控制生成结果的随机性（0.0=最确定，2.0=最随机）。请调整至合理区间。"
            )

    @staticmethod
    def _safe_parse(parser, response_data: dict[str, Any], model: str) -> dict[str, Any]:
        try:
            return parser(response_data, model)
        except (LLMError, InvalidResponseError):
            raise
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            logger.error("Failed to parse response from %s: %s", model, e, exc_info=True)
            raise InvalidResponseError(f"Failed to parse response from {model}: {e}") from e

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Call LLM chat completion API with retry logic."""
        self._validate_inputs(messages, max_tokens, temperature)

        payload, headers, endpoint = await self._build_payload(messages, max_tokens, temperature, model)
        target_model = model or self._default_model()

        last_error: Exception | None = None
        # 复用共享 httpx.AsyncClient 连接池，避免每次调用都新建客户端
        client = await get_shared_http_client()
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code != 200:
                    raise _classify_error(response.status_code, response.text)
                return self._safe_parse(self._parse_response, response.json(), target_model)
            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    "%s API timeout (attempt %d/%d): %s",
                    type(self).__name__,
                    attempt,
                    self.max_retries,
                    e,
                )
            except httpx.NetworkError as e:
                last_error = e
                logger.warning(
                    "%s API network error (attempt %d/%d): %s",
                    type(self).__name__,
                    attempt,
                    self.max_retries,
                    e,
                )
            except (ServiceUnavailableError, RateLimitError) as e:
                last_error = e
                logger.warning(
                    "%s API %s (attempt %d/%d): %s",
                    type(self).__name__,
                    type(e).__name__,
                    attempt,
                    self.max_retries,
                    e,
                )
            except (LLMError, InvalidResponseError):
                raise

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay * attempt)

        # P2-10：按末次错误类型透传——限流耗尽重试仍应报 6012 限流语义
        # （"稍后重试"提示），而不是被统一降级为 6010
        if isinstance(last_error, RateLimitError):
            raise last_error
        raise ServiceUnavailableError(
            f"{type(self).__name__} API call failed after {self.max_retries} retries"
        ) from last_error

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式对话补全（统一 chunk 契约）。

        chunk 格式：``{"content": str, "model": str, "done": bool,
        "stream_mode": "native"|"pseudo", "usage": dict(仅 done 时)}``。
        基类默认为伪流式（单 chunk），支持原生流的客户端覆盖本方法。
        """
        result = await self.chat_completion(messages, max_tokens=max_tokens, temperature=temperature, model=model)
        yield {
            "content": result.get("content", ""),
            "model": result.get("model", model or self._default_model()),
            "done": True,
            "stream_mode": "pseudo",
            "usage": result.get("usage", {}),
        }


class OllamaClient(BaseLLMClient):
    """Client for Ollama API."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: int = 60,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> None:
        super().__init__(timeout=timeout, max_retries=max_retries, retry_delay=retry_delay)
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _default_model(self) -> str:
        return self.model

    async def _build_payload(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        model: str | None,
    ) -> tuple[dict[str, Any], dict[str, str] | None, str]:
        target_model = model or self.model
        payload = {
            "model": target_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        endpoint = f"{self.base_url}/api/chat"
        return payload, None, endpoint

    def _parse_response(self, data: dict[str, Any], model: str) -> dict[str, Any]:
        return {
            "content": data.get("message", {}).get("content", ""),
            "model": model,
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
        target_model = model or self.model
        payload = {
            "model": target_model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        client = await get_shared_http_client()
        async with client.stream(
            "POST",
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.timeout,
        ) as response:
            if response.status_code != 200:
                raise ServiceUnavailableError(f"Ollama API error (HTTP {response.status_code})")
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
                        "usage": data.get("usage") or {},
                    }
                    return
            yield {"content": "", "model": target_model, "done": True, "stream_mode": "native"}


class CloudLLMClient(BaseLLMClient):
    """Client for cloud LLM providers (OpenAI-compatible API)."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int = 60,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> None:
        super().__init__(timeout=timeout, max_retries=max_retries, retry_delay=retry_delay)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _default_model(self) -> str:
        return self.model

    async def _build_payload(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        model: str | None,
    ) -> tuple[dict[str, Any], dict[str, str] | None, str]:
        target_model = model or self.model
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": target_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        endpoint = f"{self.base_url}/chat/completions"
        return payload, headers, endpoint

    def _parse_response(self, data: dict[str, Any], model: str) -> dict[str, Any]:
        choices = data.get("choices", [])
        if not choices:
            raise InvalidResponseError(
                "LLM API 响应解析失败：API 返回的响应中未包含任何候选结果（choices 列表为空）。可能原因：1) API 服务异常或返回了空响应；2) 请求参数配置有误。请检查 API 请求参数，或调用 API 健康检查端点确认服务状态。"
            )
        choice = choices[0]
        message = choice.get("message", {})
        return {
            "content": message.get("content", ""),
            "model": model,
            "finish_reason": choice.get("finish_reason", "stop"),
            "usage": data.get("usage", {}),
        }


class ProviderAdapter(BaseLLMClient):
    """将 LLMProvider 适配为 BaseLLMClient 接口。

    让既有调用方（task_classifier / solution_generator / nl2cad / ...）
    可以无感切换到 ProviderRegistry 管理的 Provider 实例。

    韧性说明（2026-09 全量升格）：Provider 的 ``chat_completion`` 已由
    ``app.ai.llm._resilience`` 自动包装「熔断 + 线性退避重试 + 6xxx 异常
    桥接」，本适配器不再叠加重试；桥接异常同时继承 6xxx AppException 与
    ProviderError 旧体系，此处原样透传，保留分级错误码。
    """

    def __init__(self, provider: Any) -> None:
        # Provider 的重试/熔断在 _resilience 层实现，此处只做接口适配
        self._provider = provider
        # 同步关键属性以兼容外部读取
        self.timeout = getattr(provider.config, "timeout", 60)
        self.max_retries = getattr(provider.config, "max_retries", DEFAULT_MAX_RETRIES)
        self.retry_delay = getattr(provider.config, "retry_delay", DEFAULT_RETRY_DELAY)

    def _default_model(self) -> str:
        return getattr(self._provider.config, "default_model", "")

    async def _build_payload(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        model: str | None,
    ) -> tuple[dict[str, Any], dict[str, str] | None, str]:
        # 适配器模式下不使用 BaseLLMClient 的请求构造路径
        raise NotImplementedError("ProviderAdapter 通过 chat_completion() 直接委托给 Provider")

    def _parse_response(self, data: dict[str, Any], model: str) -> dict[str, Any]:
        raise NotImplementedError("ProviderAdapter 通过 chat_completion() 直接委托给 Provider")

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> dict[str, Any]:
        """直接委托给封装的 LLMProvider（重试/熔断由韧性层负责）。"""
        self._validate_inputs(messages, max_tokens, temperature)
        try:
            return await self._provider.chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=model,
            )
        except AppException:
            # 6xxx 分级异常（含熔断快速失败）原样上抛，保留 code/level/retryable
            raise
        except Exception as e:
            # 将 Provider 异常转换为 LLMError 体系，保持调用方错误处理一致
            if isinstance(e, LLMError):
                raise
            raise LLMError(f"Provider {self._provider.provider_id} 调用失败: {e}") from e

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式透传：优先 Provider 原生流，Provider 不支持时回退伪流式。"""
        self._validate_inputs(messages, max_tokens, temperature)
        provider_stream = getattr(self._provider, "chat_completion_stream", None)
        if provider_stream is None:
            async for chunk in super().chat_completion_stream(
                messages, max_tokens=max_tokens, temperature=temperature, model=model
            ):
                yield chunk
            return
        async for chunk in provider_stream(messages, max_tokens=max_tokens, temperature=temperature, model=model):
            yield chunk


async def get_llm_client() -> BaseLLMClient:
    """工厂函数：返回当前可用的 LLM 客户端。

    优先级：
    1. ProviderRegistry 中已激活的 Provider（用户在系统设置中启用的 Provider）
    2. 回退到 config.ai 配置（向后兼容，未启用 Provider 网关时使用）

    Returns:
        BaseLLMClient 实例（OllamaClient / CloudLLMClient / ProviderAdapter）
    """
    # 优先尝试 Provider 网关
    try:
        from app.ai.llm.provider_registry import get_registry

        registry = get_registry()
        provider = registry.get_active_provider()
        if provider is not None:
            logger.debug(
                "使用激活的 LLM Provider: %s (%s)",
                provider.provider_id,
                provider.provider_type.value,
            )
            return ProviderAdapter(provider)
    except Exception as e:
        # 注册表不可用（如数据库未初始化）时降级到原有配置逻辑
        logger.debug(
            "ProviderRegistry 不可用，回退到 config.ai 配置: %s",
            e,
            exc_info=True,
        )

    # 向后兼容：基于 config.ai.mode 创建客户端
    from app.config import config

    mode = config.ai.mode
    if mode == "local":
        return OllamaClient(
            base_url=config.ai.ollama_base_url,
            model=config.ai.ollama_model,
            timeout=config.ai.timeout,
            max_retries=config.ai.max_retries,
        )
    else:
        return CloudLLMClient(
            api_key=config.ai.cloud_api_key,
            base_url=config.ai.cloud_base_url,
            model=config.ai.cloud_model,
            timeout=config.ai.timeout,
            max_retries=config.ai.max_retries,
        )
