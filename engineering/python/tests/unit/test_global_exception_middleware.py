"""GlobalExceptionMiddleware 回归测试。

锁定三个行为约束：
1. ``_handle_app_exception`` 的日志调用不得使用非法关键字参数
   （历史上 ``exc_detail=`` 导致分支触发即 TypeError）；
2. ``_safe_traceback`` 必须脱敏用户路径（复用 ``LogSanitizer.sanitize_paths``）；
3. 详细错误信息仅由服务端 ``ENVIRONMENT`` 环境变量决定，
   客户端 ``x-environment`` 请求头不参与判定。
"""

import getpass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import AppException
from app.core.log_sanitizer import LogSanitizer, sanitizer
from app.core.middleware import GlobalExceptionMiddleware, setup_exception_handlers


def _build_app(*, with_handlers: bool = False) -> FastAPI:
    app = FastAPI()
    if with_handlers:
        setup_exception_handlers(app)
    else:
        app.add_middleware(GlobalExceptionMiddleware, record_errors=True)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("database password leaked in message")

    @app.get("/app-error")
    async def app_error():
        raise AppException(code=6011, message="Ollama 超时", status_code=504, detail={"timeout": 30})

    return app


class TestAppExceptionBranch:
    """中间件 AppException 分支不得因日志参数非法而二次抛错。"""

    def test_app_exception_converted_without_type_error(self):
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/app-error")
        assert resp.status_code == 504
        body = resp.json()
        assert body["code"] == 6011
        assert body["message"] == "Ollama 超时"
        assert resp.headers["x-error-handled"] == "true"


class TestTracebackSanitization:
    def test_safe_traceback_redacts_username(self):
        middleware = GlobalExceptionMiddleware(app=None)
        try:
            raise ValueError("boom")
        except ValueError as exc:
            tb = middleware._safe_traceback(exc)

        assert "boom" in tb
        assert getpass.getuser() not in tb

    def test_sanitize_paths_covers_windows_and_posix(self):
        s = LogSanitizer()
        text = r'File "C:\Users\alice\proj\app.py" and /home/bob/x.py'
        out = s.sanitize_paths(text)
        assert "alice" not in out
        assert "bob" not in out
        assert out.count("[user]") == 2


class TestEnvironmentDrivenDetail:
    """详细错误只由服务端 ENVIRONMENT 决定，且永远脱敏。"""

    def test_production_env_hides_traceback(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        client = TestClient(_build_app(with_handlers=True), raise_server_exceptions=False)
        resp = client.get("/boom")
        body = resp.json()
        assert resp.status_code == 500
        assert "traceback" not in body
        assert "database password" not in body.get("message", "")

    def test_development_env_returns_sanitized_traceback(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        client = TestClient(_build_app(with_handlers=True), raise_server_exceptions=False)
        resp = client.get("/boom")
        body = resp.json()
        assert resp.status_code == 500
        assert "traceback" in body
        assert getpass.getuser() not in body["traceback"]

    def test_client_header_cannot_toggle_production(self, monkeypatch):
        """客户端伪造 x-environment: production 不应再影响响应内容。"""
        monkeypatch.setenv("ENVIRONMENT", "development")
        client = TestClient(_build_app(with_handlers=True), raise_server_exceptions=False)
        resp = client.get("/boom", headers={"x-environment": "production"})
        assert "traceback" in resp.json()

    def test_unexpected_exception_via_middleware_sanitized(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/boom")
        body = resp.json()
        assert resp.status_code == 500
        assert "traceback" in body
        assert getpass.getuser() not in body["traceback"]


def test_module_level_sanitizer_instance():
    assert isinstance(sanitizer, LogSanitizer)
