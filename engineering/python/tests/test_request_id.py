"""Request ID 中间件与上下文传播测试。

验证:
- RequestIdMiddleware: UUID生成、请求头注入、响应头回写
- contextvars: 跨中间件/路由的request_id透传
- 无中间件时get_request_id()返回 "unknown"
"""

import re
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.request_id import (
    RequestIdMiddleware,
    get_request_id,
    set_request_id,
    generate_request_id,
    REQUEST_ID_HEADER,
    _request_id_var,
)
from app.core.response import success, error, ErrorCode
from app.core.logging_config import configure_logging


UUID_HEX_PATTERN = re.compile(r"^[a-f0-9]{32}$")


class TestGenerateRequestId:
    """测试request_id生成"""

    def test_generates_32_char_hex(self):
        rid = generate_request_id()
        assert len(rid) == 32
        assert UUID_HEX_PATTERN.match(rid)

    def test_is_valid_uuid_hex(self):
        rid = generate_request_id()
        uuid.UUID(hex=rid)

    def test_generates_unique_ids(self):
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100


class TestContextVarWithoutMiddleware:
    """无中间件上下文时get_request_id()返回unknown"""

    @pytest.fixture(autouse=True)
    def _reset_context(self):
        token = _request_id_var.set("unknown")
        yield
        _request_id_var.reset(token)

    def test_default_is_unknown(self):
        assert get_request_id() == "unknown"

    def test_set_and_get(self):
        set_request_id("custom-rid-123")
        assert get_request_id() == "custom-rid-123"


class TestRequestIdMiddleware:
    """RequestIdMiddleware单元测试"""

    @pytest.fixture
    def app_with_middleware(self):
        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)

        @app.get("/echo")
        async def echo(request: Request):
            return {
                "request_id_from_context": get_request_id(),
                "request_id_from_header": request.headers.get(REQUEST_ID_HEADER, ""),
            }

        @app.get("/success")
        async def success_route():
            return success(data={"ok": True})

        @app.get("/error")
        async def error_route():
            return error(ErrorCode.NOT_FOUND, message="Not found")

        return app

    def test_generates_request_id_when_missing(self, app_with_middleware):
        client = TestClient(app_with_middleware)
        response = client.get("/echo")
        data = response.json()
        assert UUID_HEX_PATTERN.match(data["request_id_from_context"])

    def test_response_header_contains_request_id(self, app_with_middleware):
        client = TestClient(app_with_middleware)
        response = client.get("/echo")
        assert REQUEST_ID_HEADER in response.headers
        assert UUID_HEX_PATTERN.match(response.headers[REQUEST_ID_HEADER])

    def test_preserves_client_request_id(self, app_with_middleware):
        client = TestClient(app_with_middleware)
        client_rid = "aaaa1111222233334444555566667777"
        response = client.get("/echo", headers={REQUEST_ID_HEADER: client_rid})
        data = response.json()
        assert data["request_id_from_context"] == client_rid
        assert response.headers[REQUEST_ID_HEADER] == client_rid

    def test_request_id_in_success_response(self, app_with_middleware):
        client = TestClient(app_with_middleware)
        response = client.get("/success")
        data = response.json()
        assert "request_id" in data
        assert data["code"] == 0
        assert data["request_id"] != "unknown"

    def test_request_id_in_error_response(self, app_with_middleware):
        client = TestClient(app_with_middleware)
        response = client.get("/error")
        data = response.json()
        assert "request_id" in data
        assert data["code"] == 1001
        assert data["request_id"] != "unknown"

    def test_different_requests_have_different_ids(self, app_with_middleware):
        client = TestClient(app_with_middleware)
        ids = set()
        for _ in range(10):
            response = client.get("/echo")
            ids.add(response.headers[REQUEST_ID_HEADER])
        assert len(ids) == 10

    def test_same_response_header_and_context_match(self, app_with_middleware):
        client = TestClient(app_with_middleware)
        response = client.get("/echo")
        data = response.json()
        assert data["request_id_from_context"] == response.headers[REQUEST_ID_HEADER]


class TestRequestIdInExceptionHandlers:
    """request_id在异常处理中的一致性"""

    @pytest.fixture
    def app_with_handlers(self):
        from app.core.exception_handlers import register_exception_handlers

        configure_logging()
        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)
        register_exception_handlers(app)

        @app.get("/raise-app")
        async def raise_app_exc():
            from app.core.exceptions import NotFoundException

            raise NotFoundException(message="Test not found")

        @app.get("/raise-generic")
        async def raise_generic():
            raise ValueError("Something broke")

        return app

    def test_exception_response_has_request_id(self, app_with_handlers):
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/raise-app")
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] != "unknown"
        assert data["code"] == 1001
        assert data["message"] == "Test not found"

    def test_generic_exception_has_request_id(self, app_with_handlers):
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/raise-generic")
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] != "unknown"
        assert data["code"] == 2001

    def test_response_header_has_request_id_on_error(self, app_with_handlers):
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/raise-app")
        assert REQUEST_ID_HEADER in response.headers
        assert UUID_HEX_PATTERN.match(response.headers[REQUEST_ID_HEADER])


class TestErrorResponseFormat:
    """验证错误响应格式符合规范"""

    @pytest.fixture
    def app(self):
        from app.core.exception_handlers import register_exception_handlers

        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)
        register_exception_handlers(app)

        @app.get("/not-found")
        async def not_found():
            from app.core.exceptions import NotFoundException

            raise NotFoundException(message="资源未找到")

        return app

    def test_error_response_has_code_message_request_id(self, app):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/not-found")
        data = response.json()

        assert "code" in data
        assert "message" in data
        assert "request_id" in data
        assert isinstance(data["code"], int)
        assert isinstance(data["message"], str)
        assert isinstance(data["request_id"], str)
        assert data["code"] == 1001
        assert data["message"] == "资源未找到"
        assert UUID_HEX_PATTERN.match(data["request_id"])
        assert response.headers[REQUEST_ID_HEADER] == data["request_id"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
