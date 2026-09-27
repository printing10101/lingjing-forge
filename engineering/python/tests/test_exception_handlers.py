"""
Test Exception Handlers

Tests for:
- AppException: Unified base exception with numeric code
- NotFoundException: 1001
- ValidationException: 1002
- InternalServerException: 2001
- ServiceUnavailableException: 2002
- Exception handlers registration and unified response format
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from app.core.exceptions import (
    AppException,
    NotFoundException,
    ValidationException,
    InternalServerException,
    ServiceUnavailableException,
)
from app.core.exception_handlers import register_exception_handlers
from starlette.exceptions import HTTPException as StarletteHTTPException


class TestAppException:
    """Test base AppException"""

    def test_default_initialization(self):
        exc = AppException(code=9999, message="Test error")
        assert exc.code == 9999
        assert exc.message == "Test error"
        assert exc.status_code == 500
        assert exc.detail is None

    def test_custom_status_code(self):
        exc = AppException(code=9999, message="Custom", status_code=418)
        assert exc.status_code == 418

    def test_custom_detail(self):
        exc = AppException(code=9999, message="Error", detail={"field": "x"})
        assert exc.detail == {"field": "x"}

    def test_full_customization(self):
        exc = AppException(
            code=1004, message="Forbidden", status_code=403, detail="no access"
        )
        assert exc.code == 1004
        assert exc.message == "Forbidden"
        assert exc.status_code == 403
        assert exc.detail == "no access"

    def test_inherits_from_exception(self):
        exc = AppException(code=9999, message="Test")
        assert isinstance(exc, Exception)

    def test_to_dict(self):
        exc = AppException(code=2001, message="Server error")
        d = exc.to_dict()
        assert d["code"] == 2001
        assert d["message"] == "Server error"
        assert "detail" not in d

    def test_to_dict_with_detail(self):
        exc = AppException(code=2001, message="Error", detail={"id": "abc"})
        d = exc.to_dict()
        assert d["code"] == 2001
        assert d["detail"] == {"id": "abc"}


class TestNotFoundException:
    """Test NotFoundException (1001)"""

    def test_default_message(self):
        exc = NotFoundException()
        assert exc.code == 1001
        assert exc.message == "资源未找到"
        assert exc.status_code == 404

    def test_custom_message(self):
        exc = NotFoundException(message="用户不存在")
        assert exc.message == "用户不存在"

    def test_with_detail(self):
        exc = NotFoundException(message="Model X not found", detail={"model": "X"})
        assert exc.detail == {"model": "X"}


class TestValidationException:
    """Test ValidationException (1002)"""

    def test_default_message(self):
        exc = ValidationException()
        assert exc.code == 1002
        assert exc.message == "请求参数校验失败"
        assert exc.status_code == 422

    def test_custom_message(self):
        exc = ValidationException(message="参数格式错误")
        assert exc.message == "参数格式错误"


class TestInternalServerException:
    """Test InternalServerException (2001)"""

    def test_default_message(self):
        exc = InternalServerException()
        assert exc.code == 2001
        assert exc.message == "服务器内部错误"
        assert exc.status_code == 500

    def test_custom_message(self):
        exc = InternalServerException(message="数据库连接失败")
        assert exc.message == "数据库连接失败"


class TestServiceUnavailableException:
    """Test ServiceUnavailableException (2002)"""

    def test_default_message(self):
        exc = ServiceUnavailableException()
        assert exc.code == 2002
        assert exc.message == "服务暂不可用"
        assert exc.status_code == 503

    def test_custom_message(self):
        exc = ServiceUnavailableException(message="GPU资源暂不可用")
        assert exc.message == "GPU资源暂不可用"


class TestExceptionHandlersIntegration:
    """Test exception handlers with FastAPI app - unified response format"""

    @pytest.fixture
    def app_with_handlers(self):
        app = FastAPI()
        register_exception_handlers(app)
        return app

    def _assert_unified_error(self, response_data, expected_code, expected_message):
        assert response_data["code"] == expected_code
        assert response_data["message"] == expected_message
        assert "request_id" in response_data
        assert isinstance(response_data["request_id"], str)

    def test_app_exception_handler(self, app_with_handlers):
        @app_with_handlers.get("/test")
        async def test_endpoint():
            raise NotFoundException(message="Experience not found")

        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/test")
        assert response.status_code == 404
        self._assert_unified_error(response.json(), 1001, "Experience not found")

    def test_not_found_exception_handler(self, app_with_handlers):
        @app_with_handlers.get("/notfound")
        async def test_endpoint():
            raise NotFoundException()

        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/notfound")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == 1001

    def test_validation_exception_handler(self, app_with_handlers):
        @app_with_handlers.get("/invalid")
        async def test_endpoint():
            raise ValidationException(message="Invalid parameters")

        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/invalid")
        assert response.status_code == 422
        data = response.json()
        assert data["code"] == 1002

    def test_internal_error_handler(self, app_with_handlers):
        @app_with_handlers.get("/internal")
        async def test_endpoint():
            raise InternalServerException(message="Database error")

        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/internal")
        assert response.status_code == 500
        data = response.json()
        assert data["code"] == 2001
        assert "request_id" in data

    def test_service_unavailable_handler(self, app_with_handlers):
        @app_with_handlers.get("/unavailable")
        async def test_endpoint():
            raise ServiceUnavailableException(message="GPU busy")

        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/unavailable")
        assert response.status_code == 503
        data = response.json()
        assert data["code"] == 2002

    def test_generic_exception_handler(self, app_with_handlers):
        @app_with_handlers.get("/crash")
        async def test_endpoint():
            raise ValueError("Unexpected error")

        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/crash")
        assert response.status_code == 500
        data = response.json()
        assert data["code"] == 2001
        assert "request_id" in data
        assert "detail" in data
        assert "error_id" in data["detail"]


class TestHttpExceptionHandler:
    """Test Starlette HTTP exception handling"""

    @pytest.fixture
    def app_with_handlers(self):
        app = FastAPI()
        register_exception_handlers(app)
        return app

    def test_http_exception_401(self, app_with_handlers):
        @app_with_handlers.get("/http")
        async def test_endpoint():
            raise StarletteHTTPException(status_code=401, detail="Unauthorized")

        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/http")
        assert response.status_code == 401
        data = response.json()
        assert data["code"] == 1003
        assert data["message"] == "Unauthorized"
        assert "request_id" in data

    def test_http_exception_404(self, app_with_handlers):
        @app_with_handlers.get("/missing")
        async def test_endpoint():
            raise StarletteHTTPException(status_code=404, detail="Page not found")

        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/missing")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == 1001

    def test_http_exception_500(self, app_with_handlers):
        @app_with_handlers.get("/fail")
        async def test_endpoint():
            raise StarletteHTTPException(status_code=500, detail="Server error")

        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/fail")
        assert response.status_code == 500
        data = response.json()
        assert data["code"] == 2001

    def test_http_exception_422(self, app_with_handlers):
        @app_with_handlers.get("/bad")
        async def test_endpoint():
            raise StarletteHTTPException(status_code=422, detail="Invalid")

        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.get("/bad")
        assert response.status_code == 422
        data = response.json()
        assert data["code"] == 1002


class TestValidationExceptionHandler:
    """Test FastAPI validation exception handling"""

    @pytest.fixture
    def app_with_handlers(self):
        app = FastAPI()
        register_exception_handlers(app)

        class Item(BaseModel):
            name: str
            price: float

        @app.post("/item")
        async def create_item(item: Item):
            return item

        return app

    def test_validation_error_format(self, app_with_handlers):
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        response = client.post("/item", json={"name": "test"})
        assert response.status_code == 422
        data = response.json()
        assert data["code"] == 1002
        assert data["message"] == "请求参数校验失败"
        assert "detail" in data
        assert "request_id" in data


class TestExceptionHandlerRegistration:
    """Test handler registration function"""

    def test_register_exception_handlers(self):
        app = FastAPI()
        register_exception_handlers(app)
        assert AppException in app.exception_handlers
        assert StarletteHTTPException in app.exception_handlers
        assert RequestValidationError in app.exception_handlers
        assert Exception in app.exception_handlers


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
