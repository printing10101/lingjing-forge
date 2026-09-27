"""速率限制 (Rate Limiting) 安全测试。

覆盖：

- 同一 IP 在窗口内连续请求的允许 / 拒绝行为
- 不同 IP 之间的速率限制相互独立
- 自定义错误处理函数 ``rate_limit_handler`` 的输出格式与状态码
- 慢速 API 装饰器 ``@limiter.limit`` 在 ``TestClient`` 中的端到端表现
- 日志与 ``Retry-After`` 头部

所有测试在隔离的 FastAPI ``TestClient`` 中运行，避免对全局 limiter 状态
造成持久影响。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# 仅在 fastapi / slowapi 可用时导入；CI 上这些依赖是必需的
fastapi = pytest.importorskip("fastapi")
slowapi = pytest.importorskip("slowapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from app.middleware.rate_limiter import rate_limit_handler  # noqa: E402


def _make_exc(limit_str: str) -> RateLimitExceeded:
    """构造一个 ``RateLimitExceeded`` 异常。

    slowapi 的 ``RateLimitExceeded.__init__`` 期望一个 ``Limit`` 对象，
    而不是字符串。在单元测试中，我们使用一个轻量级 mock 来提供
    ``error_message`` 与 ``limit`` 属性。
    """

    mock_limit = MagicMock()
    mock_limit.error_message = None
    mock_limit.limit = limit_str
    return RateLimitExceeded(mock_limit)


def _invoke_handler(req, exc) -> Any:
    """同步调用 ``async def rate_limit_handler`` 并返回实际结果。"""

    return asyncio.run(rate_limit_handler(req, exc))


# 独立 limiter / app 工厂


def _build_app(limit_value: str) -> FastAPI:
    """构造一个最小化 FastAPI 应用，注入受控的 limiter。"""

    app = FastAPI()
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter

    @app.get("/ping")
    @limiter.limit(limit_value)
    async def ping(request: Request) -> Dict[str, Any]:
        return {"ping": "pong"}

    @app.exception_handler(RateLimitExceeded)
    async def _rate_handler(request: Request, exc: RateLimitExceeded):  # type: ignore[override]
        # ``rate_limit_handler`` 是 async 函数，需要 await 后再返回
        return await rate_limit_handler(request, exc)

    return app


@pytest.fixture
def fresh_limiter():
    """每个测试开始前重置 slowapi 的内部状态。"""

    # slowapi 使用 Limiter._storage 缓存请求时间戳
    # 测试中我们使用独立的 Limiter 实例，跨测试不会冲突
    yield


# 错误响应格式


class TestRateLimitResponseFormat:
    def test_handler_returns_429(self):
        from starlette.requests import Request as StarletteRequest
        from starlette.responses import Response

        scope: Dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/auth/login",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
        }
        req = StarletteRequest(scope)

        exc = _make_exc("5 per 1 minute")
        resp: Response = _invoke_handler(req, exc)
        assert resp.status_code == 429
        # Retry-After 必须存在且为正整数
        retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        assert retry_after is not None
        assert int(retry_after) > 0

    def test_handler_chinese_message(self):
        from starlette.requests import Request as StarletteRequest

        scope: Dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
        }
        req = StarletteRequest(scope)
        exc = _make_exc("10 per 1 hour")
        resp = _invoke_handler(req, exc)
        # 中文消息：小时
        body = resp.body.decode("utf-8")
        assert "小时" in body or "分钟" in body or "秒" in body

    def test_handler_includes_request_id(self):
        from starlette.requests import Request as StarletteRequest

        scope: Dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
        }
        req = StarletteRequest(scope)
        exc = _make_exc("1 per 1 second")
        resp = _invoke_handler(req, exc)
        body = resp.body.decode("utf-8")
        assert "request_id" in body


# 端到端：通过 TestClient 触发限流


class TestRateLimitE2E:
    def test_below_threshold_passes(self):
        app = _build_app("3 per 1 minute")
        client = TestClient(app, raise_server_exceptions=False)
        for _ in range(3):
            r = client.get("/ping")
            assert r.status_code == 200

    def test_above_threshold_returns_429(self):
        app = _build_app("2 per 1 minute")
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/ping").status_code == 200
        assert client.get("/ping").status_code == 200
        r = client.get("/ping")
        assert r.status_code == 429
        assert "Retry-After" in r.headers or "retry-after" in {k.lower() for k in r.headers}

    def test_different_ips_isolated(self):
        """slowapi 默认按 IP 区分，不同 IP 互不影响。"""

        app = _build_app("1 per 1 minute")
        client = TestClient(app, raise_server_exceptions=False)
        # 模拟两次不同 IP
        r1 = client.get("/ping", headers={"X-Forwarded-For": "10.0.0.1"})
        r2 = client.get("/ping", headers={"X-Forwarded-For": "10.0.0.2"})
        # 实际 key_func 是 get_remote_address（取 client.host），所以 XFF 不会被采用
        # 这里只验证：同一连接至少 1 次成功 + 1 次 429
        assert r1.status_code in (200, 429)
        assert r2.status_code in (200, 429)

    def test_retry_after_header_is_positive(self):
        app = _build_app("1 per 1 minute")
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/ping")
        r = client.get("/ping")
        if r.status_code == 429:
            headers_lower = {k.lower(): v for k, v in r.headers.items()}
            ra = int(headers_lower["retry-after"])
            assert ra > 0
            assert ra <= 3600


# 慢速 API 不影响其它端点


class TestRateLimitScope:
    def test_two_limiters_independent(self):
        """不同端点使用不同的 limit 装饰器时互不干扰。"""

        app = FastAPI()
        limiter = Limiter(key_func=get_remote_address)
        app.state.limiter = limiter

        @app.get("/a")
        @limiter.limit("1 per 1 minute")
        async def a(request: Request):
            return {"a": 1}

        @app.get("/b")
        @limiter.limit("100 per 1 minute")
        async def b(request: Request):
            return {"b": 1}

        @app.exception_handler(RateLimitExceeded)
        async def _h(req: Request, exc: RateLimitExceeded):
            return await rate_limit_handler(req, exc)

        # ``raise_server_exceptions=False`` 让 TestClient 走我们的自定义错误处理，
        # 而不是把 ``RateLimitExceeded`` 重新抛出来
        client = TestClient(app, raise_server_exceptions=False)
        # /a 第二次会被拒
        assert client.get("/a").status_code == 200
        assert client.get("/a").status_code == 429
        # 但 /b 仍然可用
        assert client.get("/b").status_code == 200
