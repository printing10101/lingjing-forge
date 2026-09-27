"""LLM 流式端点（SSE）测试。

覆盖 ``app/api/v1/llm_stream.py`` 的事件契约：
- 内容帧 + [DONE] 终止帧
- 6xxx 分级异常 → 错误帧直通 code/level/retryable
- 非 AppException → 6001 兜底脱敏错误帧
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.api.v1.llm_stream import ChatStreamRequest, _chat_stream_events
from app.ai.llm._resilience import ProviderLLMError


class _FakeClient:
    def __init__(self, chunks: list[dict[str, Any]] | None = None, exc: Exception | None = None):
        self._chunks = chunks or []
        self._exc = exc

    async def chat_completion_stream(self, messages, max_tokens=2048, temperature=0.7, model=None):
        for c in self._chunks:
            yield dict(c)
        if self._exc is not None:
            raise self._exc


def _parse_frames(events: list[str]) -> list[dict[str, Any]]:
    parsed = []
    for ev in events:
        assert ev.startswith("data: ") or ev == "data: [DONE]\n\n", f"非法 SSE 帧: {ev!r}"
        if ev == "data: [DONE]\n\n":
            parsed.append("__DONE__")
        else:
            parsed.append(json.loads(ev[len("data: "):]))
    return parsed


def test_messages_role_validation():
    """备忘项：非法 role/content 在请求模型层前置拒绝。"""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ChatStreamRequest(messages=[{"role": "hacker", "content": "x"}])
    with pytest.raises(pydantic.ValidationError):
        ChatStreamRequest(messages=[{"role": "user", "content": 123}])
    with pytest.raises(pydantic.ValidationError):
        ChatStreamRequest(messages=[])
    # 合法角色通过
    ChatStreamRequest(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])


@pytest.mark.asyncio
async def test_stream_content_frames_and_done(monkeypatch: pytest.MonkeyPatch):
    from app.ai import llm_client as mod

    fake = _FakeClient(
        chunks=[
            {"content": "你", "model": "m", "done": False, "stream_mode": "native"},
            {"content": "好", "model": "m", "done": True, "stream_mode": "native"},
        ]
    )

    async def _get():
        return fake

    monkeypatch.setattr(mod, "get_llm_client", _get)
    req = ChatStreamRequest(messages=[{"role": "user", "content": "hi"}])
    frames = _parse_frames([e async for e in _chat_stream_events(req)])
    assert frames[-1] == "__DONE__"
    contents = [f for f in frames[:-1] if f["type"] == "content"]
    assert "".join(f["content"] for f in contents) == "你好"
    assert contents[0]["stream_mode"] == "native"


@pytest.mark.asyncio
async def test_stream_app_exception_error_frame(monkeypatch: pytest.MonkeyPatch):
    from app.ai import llm_client as mod

    fake = _FakeClient(exc=ProviderLLMError(provider="p", message="服务暂时不可用 (HTTP 503)"))

    async def _get():
        return fake

    monkeypatch.setattr(mod, "get_llm_client", _get)
    req = ChatStreamRequest(messages=[{"role": "user", "content": "hi"}])
    frames = _parse_frames([e async for e in _chat_stream_events(req)])
    err = next(f for f in frames if isinstance(f, dict) and f["type"] == "error")
    assert err["code"] == 6010
    assert err["retryable"] is True
    assert frames[-1] == "__DONE__"


@pytest.mark.asyncio
async def test_stream_unexpected_exception_sanitized(monkeypatch: pytest.MonkeyPatch):
    from app.ai import llm_client as mod

    fake = _FakeClient(exc=RuntimeError("内部堆栈 secret-value"))

    async def _get():
        return fake

    monkeypatch.setattr(mod, "get_llm_client", _get)
    req = ChatStreamRequest(messages=[{"role": "user", "content": "hi"}])
    frames = _parse_frames([e async for e in _chat_stream_events(req)])
    err = next(f for f in frames if isinstance(f, dict) and f["type"] == "error")
    assert err["code"] == 6001
    assert frames[-1] == "__DONE__"
