"""LLM 韧性层测试（2026-09 全量升格）。

覆盖：
- ``app/core/circuit_breaker``：async_execute、上下文管理器协议、
  ``circuit_breaker_context`` 回归（历史 bug：无 yield 的伪上下文管理器）
- ``app.ai.llm._resilience``：Provider 自动重试、熔断快速失败、
  6xxx 异常桥接（含 ProviderError 旧体系兼容）
- 流式：Ollama NDJSON / OpenAI 兼容 SSE 原生解析、伪流式降级、流式异常桥接
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.ai.llm._resilience import (
    ProviderCircuitOpenError,
    ProviderLLMAuthError,
    ProviderLLMError,
    ProviderLLMRateLimitError,
    ProviderLLMResponseError,
    ProviderLLMTimeoutError,
    get_llm_breaker,
    translate_exception,
)
from app.ai.llm.provider_base import (
    LLMProvider,
    ProviderConfig,
    ProviderError,
    ProviderHTTPError,
    ProviderStatus,
    ProviderType,
    ProviderUnavailableError,
)
from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    circuit_breaker_context,
)
from app.core.exceptions import CircuitBreakerOpenException

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_breakers():
    CircuitBreakerRegistry().reset_all()
    yield
    CircuitBreakerRegistry().reset_all()


def _make_config(**overrides: Any) -> ProviderConfig:
    defaults = dict(
        provider_id="prov_test",
        name="测试 Provider",
        provider_type=ProviderType.OLLAMA,
        base_url="http://127.0.0.1:11434",
        default_model="test-model",
        timeout=5,
        max_retries=3,
        retry_delay=0.0,
    )
    defaults.update(overrides)
    return ProviderConfig(**defaults)


class _ScriptedProvider(LLMProvider):
    """按脚本依次抛异常/返回结果的假 Provider（chat_completion 被自动包装）。"""

    def __init__(self, script: list[Any]):
        super().__init__(_make_config())
        self.script = list(script)
        self.calls = 0

    async def detect(self) -> bool:
        return True

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus.ONLINE

    async def list_models(self) -> list[str]:
        return []

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        return {"content": "ok", "model": "test-model", "finish_reason": "stop", "usage": {}}


# ---------------------------------------------------------------------------
# circuit_breaker 基础
# ---------------------------------------------------------------------------


def test_circuit_breaker_context_is_usable_with_statement():
    """回归：circuit_breaker_context 曾是无 yield 的伪上下文管理器。"""
    with circuit_breaker_context("ctx-test") as breaker:
        assert isinstance(breaker, CircuitBreaker)
        breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


async def test_async_execute_records_success_and_failure():
    breaker = CircuitBreaker("async-test", CircuitBreakerConfig(failure_threshold=2))

    async def ok():
        return 42

    async def boom():
        raise RuntimeError("x")

    assert await breaker.async_execute(ok) == 42
    with pytest.raises(RuntimeError):
        await breaker.async_execute(boom)
    assert breaker.state == CircuitState.CLOSED
    with pytest.raises(RuntimeError):
        await breaker.async_execute(boom)
    # 连续失败达到阈值 → OPEN
    assert breaker.state == CircuitState.OPEN


async def test_async_execute_rejects_when_open():
    breaker = CircuitBreaker("open-test", CircuitBreakerConfig(failure_threshold=1))

    async def boom():
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        await breaker.async_execute(boom)
    with pytest.raises(CircuitBreakerOpenException):
        await breaker.async_execute(boom)


# ---------------------------------------------------------------------------
# 异常翻译与双继承桥接
# ---------------------------------------------------------------------------


def test_translate_http_error_mapping():
    e429 = translate_exception("p", ProviderHTTPError("rl", status_code=429, body="x"))
    assert isinstance(e429, ProviderLLMRateLimitError) and e429.code == 6012
    assert isinstance(e429, ProviderError)

    e401 = translate_exception("p", ProviderHTTPError("auth", status_code=401, body="x"))
    assert isinstance(e401, ProviderLLMAuthError) and e401.code == 6013

    e503 = translate_exception("p", ProviderHTTPError("un", status_code=503, body="x"))
    assert isinstance(e503, ProviderLLMError) and e503.code == 6010
    # Router 降级判断捕获 ProviderError —— 桥接异常必须保持该继承
    assert isinstance(e503, ProviderError)

    e400 = translate_exception("p", ProviderHTTPError("bad", status_code=400, body="x"))
    assert isinstance(e400, ProviderLLMError) and e400.code == 6010


def test_translate_network_and_generic_errors():
    t = translate_exception("p", httpx.TimeoutException("t"))
    assert isinstance(t, ProviderLLMTimeoutError) and t.code == 6011

    n = translate_exception("p", httpx.ConnectError("refused"))
    assert isinstance(n, ProviderLLMError) and n.code == 6010

    empty = translate_exception("p", ProviderError("API 返回空 choices"))
    assert isinstance(empty, ProviderLLMResponseError) and empty.code == 6003

    biz = ValueError("业务异常原样返回")
    assert translate_exception("p", biz) is biz


# ---------------------------------------------------------------------------
# Provider 自动重试（chat_completion 被韧性层包装）
# ---------------------------------------------------------------------------


async def test_provider_retries_transient_then_succeeds():
    provider = _ScriptedProvider(
        [ProviderHTTPError("boom", status_code=503, body="x"), None]
    )
    result = await provider.chat_completion([{"role": "user", "content": "hi"}])
    assert result["content"] == "ok"
    assert provider.calls == 2


async def test_provider_persistent_500_raises_6010_provider_error():
    provider = _ScriptedProvider(
        [ProviderHTTPError("e", status_code=500, body="x")] * 5
    )
    with pytest.raises(ProviderLLMError) as ei:
        await provider.chat_completion([{"role": "user", "content": "hi"}])
    assert ei.value.code == 6010
    # 旧体系兼容：Router 降级判断依赖 ProviderError
    assert isinstance(ei.value, ProviderError)
    assert provider.calls == 3  # max_retries=3


async def test_provider_rate_limit_maps_6012_and_retries():
    provider = _ScriptedProvider([ProviderHTTPError("rl", status_code=429, body="x")] * 5)
    with pytest.raises(ProviderLLMRateLimitError) as ei:
        await provider.chat_completion([{"role": "user", "content": "hi"}])
    assert ei.value.code == 6012
    assert ei.value.retryable
    assert provider.calls == 3


async def test_provider_auth_error_no_retry():
    provider = _ScriptedProvider([ProviderHTTPError("a", status_code=401, body="x")] * 5)
    with pytest.raises(ProviderLLMAuthError) as ei:
        await provider.chat_completion([{"role": "user", "content": "hi"}])
    assert ei.value.code == 6013
    assert not ei.value.retryable
    assert provider.calls == 1  # 永久错误不重试


async def test_provider_timeout_retry_then_6011():
    provider = _ScriptedProvider([httpx.TimeoutException("t")] * 5)
    with pytest.raises(ProviderLLMTimeoutError) as ei:
        await provider.chat_completion([{"role": "user", "content": "hi"}])
    assert ei.value.code == 6011
    assert provider.calls == 3


async def test_provider_empty_content_maps_6003_without_retry():
    """空响应（永久错误）→ 6003，不重试，不计熔断。"""
    provider = _ScriptedProvider([ProviderError("API 返回空 choices")] * 5)
    with pytest.raises(ProviderLLMResponseError) as ei:
        await provider.chat_completion([{"role": "user", "content": "hi"}])
    assert ei.value.code == 6003
    assert provider.calls == 1
    # 熔断器不受永久错误影响
    assert get_llm_breaker("prov_test").state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# 熔断接入 LLM 调用链
# ---------------------------------------------------------------------------


async def test_breaker_opens_after_threshold_and_fails_fast():
    cfg = CircuitBreakerConfig(failure_threshold=3, recovery_timeout=60.0)
    provider = _ScriptedProvider([ProviderHTTPError("e", status_code=503, body="x")] * 20)
    provider.config.max_retries = 1
    breaker = get_llm_breaker("prov_test")
    breaker.config = cfg

    for _ in range(3):
        with pytest.raises(ProviderLLMError):
            await provider.chat_completion([{"role": "user", "content": "hi"}])
    assert breaker.state == CircuitState.OPEN

    # 熔断打开后：不触达 Provider，快速失败 9001（兼容 ProviderUnavailableError）
    calls_before = provider.calls
    with pytest.raises(ProviderCircuitOpenError) as ei:
        await provider.chat_completion([{"role": "user", "content": "hi"}])
    assert ei.value.code == 9001
    assert isinstance(ei.value, ProviderUnavailableError)
    assert provider.calls == calls_before


async def test_breaker_isolated_per_provider():
    p1 = _ScriptedProvider([ProviderHTTPError("e", status_code=503, body="x")] * 10)
    p1.config.provider_id = "prov_a"
    p1.config.max_retries = 1
    p2 = _ScriptedProvider([None])
    p2.config.provider_id = "prov_b"
    p2.config.max_retries = 1

    breaker_a = get_llm_breaker("prov_a")
    breaker_a.config = CircuitBreakerConfig(failure_threshold=1)
    with pytest.raises(ProviderLLMError):
        await p1.chat_completion([{"role": "user", "content": "hi"}])
    assert breaker_a.state == CircuitState.OPEN

    # 另一个 Provider 不受影响
    result = await p2.chat_completion([{"role": "user", "content": "hi"}])
    assert result["content"] == "ok"
    assert get_llm_breaker("prov_b").state == CircuitState.CLOSED


async def test_breaker_half_open_recovers_after_success():
    breaker = get_llm_breaker("prov_recover")
    breaker.config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.0, success_threshold=1)
    provider = _ScriptedProvider(
        [ProviderHTTPError("e", status_code=503, body="x"), None]
    )
    provider.config.provider_id = "prov_recover"
    provider.config.max_retries = 1

    with pytest.raises(ProviderLLMError):
        await provider.chat_completion([{"role": "user", "content": "hi"}])
    # recovery_timeout=0 → 状态读取时 OPEN 自动转入 HALF_OPEN（探测放行）
    assert breaker.state in (CircuitState.OPEN, CircuitState.HALF_OPEN)
    result = await provider.chat_completion([{"role": "user", "content": "hi"}])
    assert result["content"] == "ok"
    assert breaker.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# 流式：解析 / 伪流降级 / 异常桥接
# ---------------------------------------------------------------------------


class _FakeStreamResponse:
    def __init__(self, status_code: int, lines: list[str]):
        self.status_code = status_code
        self._lines = lines

    async def aread(self) -> bytes:
        return "\n".join(self._lines).encode("utf-8")

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


class _FakeStreamCtx:
    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakeHttpClient:
    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStreamCtx:
        return _FakeStreamCtx(self._response)


def _install_fake_http(monkeypatch: pytest.MonkeyPatch, lines_or_exc: Any) -> None:
    from app.ai import llm_client as llm_client_mod

    if isinstance(lines_or_exc, Exception):

        class _ErrClient:
            def stream(self, *args: Any, **kwargs: Any):
                raise lines_or_exc

        fake = _ErrClient()
    else:
        fake = _FakeHttpClient(_FakeStreamResponse(200, lines_or_exc))

    async def _get_client():
        return fake

    # 本地 Provider 走 no-proxy 客户端缝隙，两个入口都要打桩
    monkeypatch.setattr(llm_client_mod, "get_shared_http_client", _get_client)
    monkeypatch.setattr(llm_client_mod, "get_shared_http_client_no_proxy", _get_client)


async def test_ollama_provider_native_stream_ndjson(monkeypatch: pytest.MonkeyPatch):
    from app.ai.llm.providers.ollama import OllamaProvider

    _install_fake_http(
        monkeypatch,
        [
            json_line({"message": {"content": "你"}, "done": False}),
            "",
            json_line({"message": {"content": "好"}, "done": False}),
            json_line({"message": {"content": ""}, "done": True}),
        ],
    )
    provider = OllamaProvider(_make_config())
    chunks = [c async for c in provider.chat_completion_stream([{"role": "user", "content": "hi"}])]
    assert "".join(c["content"] for c in chunks) == "你好"
    assert all(c["stream_mode"] == "native" for c in chunks)
    assert chunks[-1]["done"] is True


async def test_openai_compat_native_stream_sse(monkeypatch: pytest.MonkeyPatch):
    from app.ai.llm.providers.lmstudio import LMStudioProvider

    _install_fake_http(
        monkeypatch,
        [
            'data: {"choices":[{"delta":{"content":"A"}}]}',
            "data: {not-json}",
            'data: {"choices":[{"delta":{"content":"B"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ],
    )
    provider = LMStudioProvider(_make_config(provider_type=ProviderType.LMSTUDIO, base_url="http://127.0.0.1:1234/v1"))
    chunks = [c async for c in provider.chat_completion_stream([{"role": "user", "content": "hi"}])]
    assert "".join(c["content"] for c in chunks) == "AB"
    assert chunks[-1]["done"] is True


async def test_stream_error_translated(monkeypatch: pytest.MonkeyPatch):
    from app.ai.llm.providers.ollama import OllamaProvider

    _install_fake_http(monkeypatch, httpx.ConnectError("refused"))
    provider = OllamaProvider(_make_config())
    with pytest.raises(ProviderLLMError):
        async for _ in provider.chat_completion_stream([{"role": "user", "content": "hi"}]):
            pass


async def test_pseudo_stream_fallback_for_non_native_provider():
    """未覆盖 chat_completion_stream 的 Provider：伪流式单 chunk 契约。"""
    provider = _ScriptedProvider([None])
    chunks = [c async for c in provider.chat_completion_stream([{"role": "user", "content": "hi"}])]
    assert len(chunks) == 1
    assert chunks[0]["stream_mode"] == "pseudo"
    assert chunks[0]["done"] is True
    assert chunks[0]["content"] == "ok"


def json_line(obj: dict) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# P2 行为回归
# ---------------------------------------------------------------------------


class TestP2Behavior:
    def test_half_open_concurrency_cap(self):
        """P2-1：half_open_max_calls 闸门真正生效（此前是死配置）。"""
        from app.core.exceptions import CircuitBreakerHalfOpenException

        breaker = get_llm_breaker("prov_halfopen")
        breaker.config = CircuitBreakerConfig(
            failure_threshold=1, recovery_timeout=0.0, half_open_max_calls=1
        )
        breaker.record_failure(RuntimeError("x"))  # → OPEN
        assert breaker.state in (CircuitState.OPEN, CircuitState.HALF_OPEN)
        # 第一个探测名额发放
        breaker.try_acquire_half_open()
        # 第二个探测被拒（9002）
        with pytest.raises(CircuitBreakerHalfOpenException) as ei:
            breaker.try_acquire_half_open()
        assert ei.value.code == 9002
        breaker.release_half_open()
        # 释放后可再次探测
        breaker.try_acquire_half_open()
        breaker.release_half_open()

    async def test_stream_client_disconnect_not_counted(self, monkeypatch):
        """P2-2：客户端停止生成导致的 RemoteProtocolError 不计入熔断。"""
        from app.ai.llm.providers.ollama import OllamaProvider

        breaker = get_llm_breaker("prov_test")

        class _DisconnectStreamCtx:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args: Any) -> bool:
                return False

            async def aread(self) -> bytes:
                return b""

            async def aiter_lines(self):  # noqa: D401
                raise httpx.RemoteProtocolError("peer closed connection")
                yield  # pragma: no cover

        class _FakeClient:
            def stream(self, *args: Any, **kwargs: Any):
                return _DisconnectStreamCtx()

        async def _get_client():
            return _FakeClient()

        monkeypatch.setattr("app.ai.llm_client.get_shared_http_client", _get_client)
        monkeypatch.setattr("app.ai.llm_client.get_shared_http_client_no_proxy", _get_client)
        provider = OllamaProvider(_make_config())
        with pytest.raises(ProviderLLMError):
            async for _ in provider.chat_completion_stream([{"role": "user", "content": "hi"}]):
                pass
        assert breaker.get_status()["failure_count"] == 0

    async def test_legacy_rate_limit_passthrough_after_retries(self, monkeypatch):
        """P2-10：legacy 客户端重试耗尽后 429 保持 6012 限流语义。"""
        from types import SimpleNamespace

        from app.ai import llm_client as mod

        class _FakePostClient:
            async def post(self, url, headers=None, json=None, timeout=None):
                return SimpleNamespace(status_code=429, text="rate limited", json=lambda: {})

        async def _get_client():
            return _FakePostClient()

        monkeypatch.setattr(mod, "_shared_http_client", _FakePostClient())
        client = mod.OllamaClient(base_url="http://x", model="m", retry_delay=0.0, max_retries=2)
        with pytest.raises(mod.RateLimitError) as ei:
            await client.chat_completion([{"role": "user", "content": "hi"}])
        assert ei.value.code == 6012
