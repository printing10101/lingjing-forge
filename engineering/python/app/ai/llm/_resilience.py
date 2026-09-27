"""LLM Provider 韧性层：熔断 + 重试 + 6xxx 异常桥接。

2026-09 全量升格（AI 深度参与）的地基设施。此前 Provider 网关存在三个断层：

1. ``ProviderConfig.max_retries`` 字段存在，但没有任何 Provider 真正实现
   重试（``ProviderAdapter`` 注释声称"Provider 自身已实现重试"与事实不符）；
2. ``app/core/circuit_breaker.py`` 熔断器对 LLM 调用零接入，Provider 挂掉后
   每次调用都要等满超时才失败；
3. ``app/core/exceptions.py`` 的 LLM 6xxx 分级异常（6010/6011/6012/6013）
   定义完整但 AI 链路零使用，实际抛出的是 ``ProviderError`` 平行体系，
   统一错误中间件拿不到分级/可重试信息。

本模块通过 ``LLMProvider.__init_subclass__`` 自动包装子类的
``chat_completion``（重试+熔断+桥接）与 ``chat_completion_stream``
（熔断+桥接，不做重试——流式中途重试会造成重复输出），业务代码零改动。

异常桥接设计：翻译后的 6xxx 异常同时继承 Provider 旧异常体系
（``ProviderError`` / ``ProviderUnavailableError``），因此
``_router_core`` 的降级判断、既有的 ``except ProviderError`` 分支、
以及统一错误中间件的分级响应全部兼容，无需改动任何调用方。
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any
from collections.abc import AsyncIterator, Callable

import httpx

from app.ai.llm.provider_base import ProviderError, ProviderHTTPError, ProviderUnavailableError
from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
)
from app.core.exceptions import (
    CircuitBreakerOpenException,
    LLMAuthException,
    LLMProviderException,
    LLMRateLimitException,
    LLMResponseException,
    LLMTimeoutException,
)

logger = logging.getLogger(__name__)

#: 视为瞬态（可重试、计入熔断）的 HTTP 状态码
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

#: LLM 属慢服务：阈值 5 次连续瞬态失败即熔断，60s 恢复窗口后半开探测
_LLM_BREAKER_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,
    recovery_timeout=60.0,
    half_open_max_calls=3,
    success_threshold=2,
)


# ---------------------------------------------------------------------------
# 6xxx ↔ ProviderError 双继承桥接异常
# ---------------------------------------------------------------------------


class ProviderLLMError(LLMProviderException, ProviderError):
    """6010 Provider 服务失败（含旧体系 ProviderError 兼容）。"""


class ProviderLLMTimeoutError(LLMTimeoutException, ProviderUnavailableError):
    """6011 Provider 响应超时（可重试；旧体系视为不可用）。"""


class ProviderLLMRateLimitError(LLMRateLimitException, ProviderError):
    """6012 Provider 限流（429）。"""


class ProviderLLMAuthError(LLMAuthException, ProviderError):
    """6013 Provider 认证失败（不可重试）。"""


class ProviderLLMResponseError(LLMResponseException, ProviderError):
    """6003 Provider 响应无效（空内容等）。"""


class ProviderCircuitOpenError(CircuitBreakerOpenException, ProviderUnavailableError):
    """9001 Provider 熔断打开（快速失败；旧体系视为不可用）。"""


# ---------------------------------------------------------------------------
# 熔断器管理
# ---------------------------------------------------------------------------


def get_llm_breaker(provider_id: str) -> CircuitBreaker:
    """获取（或创建）指定 Provider 的全局熔断器。"""
    return CircuitBreakerRegistry().get_or_create(f"llm:{provider_id}", _LLM_BREAKER_CONFIG)


# ---------------------------------------------------------------------------
# 异常翻译
# ---------------------------------------------------------------------------


def is_transient_error(exc: BaseException) -> bool:
    """判定是否瞬态错误（可重试、计入熔断）。

    永久性错误（认证失败、空响应、参数问题）不重试也不计入熔断——
    它们不会因重试而好转，也不应让熔断器掩盖配置问题。
    """
    if isinstance(exc, ProviderHTTPError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError, asyncio.TimeoutError))


def translate_exception(provider_label: str, exc: Exception) -> Exception:
    """把 Provider 实现抛出的底层异常翻译为 6xxx 双继承桥接异常。

    非 ProviderError/httpx 家族的异常（业务语义异常）原样返回。
    """
    if isinstance(exc, ProviderHTTPError):
        status = exc.status_code
        # 安全纪律：上游响应体可能含账号/配额/内部端点信息，一律不进
        # message/detail（AppException 会被中间件原样回传客户端）
        if status == 429:
            return ProviderLLMRateLimitError(provider=provider_label)
        if status in (401, 403):
            return ProviderLLMAuthError(
                provider=provider_label,
                message=f"{provider_label} 认证失败 (HTTP {status})",
            )
        if status in RETRYABLE_STATUS_CODES:
            return ProviderLLMError(
                provider=provider_label,
                message=f"{provider_label} 服务暂时不可用 (HTTP {status})",
            )
        return ProviderLLMError(
            provider=provider_label,
            message=f"{provider_label} 服务错误 (HTTP {status})",
        )
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        return ProviderLLMTimeoutError(provider=provider_label)
    if isinstance(exc, httpx.TransportError):
        return ProviderLLMError(
            provider=provider_label,
            message=f"{provider_label} 网络错误: {type(exc).__name__}",
        )
    if isinstance(exc, ProviderUnavailableError):
        return ProviderLLMError(provider=provider_label, message=str(exc))
    if isinstance(exc, ProviderError):
        return ProviderLLMResponseError(str(exc))
    return exc


# ---------------------------------------------------------------------------
# 包装器
# ---------------------------------------------------------------------------


def wrap_chat_completion(impl: Callable[..., Any]) -> Callable[..., Any]:
    """包装 Provider 的 chat_completion：熔断 → 线性退避重试 → 6xxx 桥接。"""
    if getattr(impl, "_resilience_wrapped", False):
        return impl

    @functools.wraps(impl)
    async def wrapper(
        self: Any,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> dict[str, Any]:
        label = getattr(self, "provider_id", "") or type(self).__name__
        config = getattr(self, "config", None)
        try:
            max_retries = max(1, int(getattr(config, "max_retries", 1) or 1))
        except (TypeError, ValueError):
            max_retries = 1
        try:
            retry_delay = max(0.0, float(getattr(config, "retry_delay", 1.0) or 0.0))
        except (TypeError, ValueError):
            retry_delay = 1.0
        breaker = get_llm_breaker(label)

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            # P2-1：半开并发闸门（超过 half_open_max_calls 的探测被拒）
            try:
                breaker.try_acquire_half_open()
            except CircuitBreakerOpenException as e:
                raise ProviderCircuitOpenError(service=breaker.name) from e
            try:
                result = await impl(
                    self,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    model=model,
                )
                breaker.record_success()
                return result
            except Exception as exc:
                translated = translate_exception(label, exc)
                if is_transient_error(exc):
                    breaker.record_failure(translated)
                last_exc = translated
                if attempt >= max_retries or not is_transient_error(exc):
                    raise translated from exc
                delay = retry_delay * attempt
                logger.warning(
                    "LLM Provider [%s] 第 %d/%d 次调用失败（%s），%.1fs 后重试",
                    label,
                    attempt,
                    max_retries,
                    type(translated).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
            finally:
                breaker.release_half_open()

        raise last_exc if last_exc else ProviderLLMError(provider=label, message="unreachable")

    wrapper._resilience_wrapped = True  # type: ignore[attr-defined]
    return wrapper


def wrap_chat_stream(impl: Callable[..., Any]) -> Callable[..., AsyncIterator[dict[str, Any]]]:
    """包装 Provider 的 chat_completion_stream：熔断 + 6xxx 桥接。

    流式不做自动重试：中途重试会导致已输出的内容重复。首 chunk 前的
    失败由调用方（如 SSE 端点）决定是否重建流。
    """
    if getattr(impl, "_stream_resilience_wrapped", False):
        return impl

    @functools.wraps(impl)
    def wrapper(
        self: Any,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        label = getattr(self, "provider_id", "") or type(self).__name__
        breaker = get_llm_breaker(label)

        async def gen() -> AsyncIterator[dict[str, Any]]:
            try:
                breaker.try_acquire_half_open()
            except CircuitBreakerOpenException as e:
                raise ProviderCircuitOpenError(service=breaker.name) from e
            try:
                async for chunk in impl(
                    self,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    model=model,
                ):
                    yield chunk
            except Exception as exc:
                translated = translate_exception(label, exc)
                # P2-2：客户端提前断开（停止生成）会让 provider 侧抛
                # RemoteProtocolError——这是消费方行为，不是服务健康问题，
                # 不计入熔断（否则频繁"停止生成"会误熔断 provider）
                if is_transient_error(exc) and not isinstance(exc, httpx.RemoteProtocolError):
                    breaker.record_failure(translated)
                raise translated from exc
            finally:
                breaker.release_half_open()
            breaker.record_success()

        return gen()

    wrapper._stream_resilience_wrapped = True  # type: ignore[attr-defined]
    return wrapper


def install_resilience(cls: type) -> None:
    """在 Provider 子类上安装韧性包装（由 ``LLMProvider.__init_subclass__`` 调用）。

    只包装子类自己定义的方法（``cls.__dict__``），因此
    ``OpenAICompatLocalProvider`` 的五个本地服务子类不会重复包装。
    """
    impl = cls.__dict__.get("chat_completion")
    if impl is not None and not getattr(impl, "_resilience_wrapped", False):
        cls.chat_completion = wrap_chat_completion(impl)  # type: ignore[method-assign]
    stream_impl = cls.__dict__.get("chat_completion_stream")
    if stream_impl is not None and not getattr(stream_impl, "_stream_resilience_wrapped", False):
        cls.chat_completion_stream = wrap_chat_stream(stream_impl)  # type: ignore[method-assign]
