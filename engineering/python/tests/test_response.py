"""
Test Response Utilities

Tests for:
- ErrorCode: Enumeration of error codes
- code_to_numeric / numeric_to_code: Code mapping
- success(): Success response builder
- error(): Error response builder with optional fields
- error_response(): Direct numeric error response builder
"""

import pytest
from app.core.response import (
    success,
    error,
    error_response,
    ErrorCode,
    code_to_numeric,
    numeric_to_code,
)


class TestErrorCode:
    """Test ErrorCode enumeration"""

    def test_success_code(self):
        assert ErrorCode.SUCCESS == "SUCCESS"

    def test_error_codes_defined(self):
        assert ErrorCode.INTERNAL_ERROR == "INTERNAL_ERROR"
        assert ErrorCode.NOT_FOUND == "NOT_FOUND"
        assert ErrorCode.INVALID_REQUEST == "INVALID_REQUEST"
        assert ErrorCode.UNAUTHORIZED == "UNAUTHORIZED"

    def test_domain_specific_codes(self):
        assert ErrorCode.FILE_NOT_FOUND == "FILE_NOT_FOUND"
        assert ErrorCode.CAD_GENERATION_ERROR == "CAD_GENERATION_ERROR"
        assert ErrorCode.SERVICE_UNAVAILABLE == "SERVICE_UNAVAILABLE"

    def test_code_to_numeric_success(self):
        assert code_to_numeric(ErrorCode.SUCCESS) == 0

    def test_code_to_numeric_not_found(self):
        assert code_to_numeric(ErrorCode.NOT_FOUND) == 1001

    def test_code_to_numeric_internal_error(self):
        assert code_to_numeric(ErrorCode.INTERNAL_ERROR) == 2001

    def test_code_to_numeric_file_not_found(self):
        # FILE_NOT_FOUND 已在 _ERROR_CODE_TO_NUMERIC 中注册为独立错误码 1008
        # （此前误断言为 1001，且测试名"unknown"与用例不符，2026-08-28 修正）
        assert code_to_numeric(ErrorCode.FILE_NOT_FOUND) == 1008

    def test_code_to_numeric_unmapped_returns_2001_fallback(self):
        """映射表外的 ErrorCode 成员回落到 2001（.get 默认值兜底分支）。"""
        from unittest.mock import MagicMock

        assert code_to_numeric(MagicMock(spec=ErrorCode)) == 2001

    def test_numeric_to_code(self):
        assert numeric_to_code(0) == ErrorCode.SUCCESS
        assert numeric_to_code(1001) == ErrorCode.NOT_FOUND
        assert numeric_to_code(2001) == ErrorCode.INTERNAL_ERROR


class TestSuccessResponse:
    """Test success response builder"""

    def test_success_code_is_zero(self):
        result = success()
        assert result["code"] == 0

    def test_success_with_default_values(self):
        result = success()
        assert result["message"] == "Success"
        assert result["data"] is None

    def test_success_has_request_id(self):
        result = success()
        assert "request_id" in result
        assert isinstance(result["request_id"], str)

    def test_success_with_custom_message(self):
        result = success(message="Operation completed")
        assert result["message"] == "Operation completed"

    def test_success_with_data(self):
        data = {"id": "123", "name": "test"}
        result = success(data=data)
        assert result["data"] == data
        assert result["data"]["id"] == "123"

    def test_success_with_data_and_message(self):
        data = {"items": [1, 2, 3]}
        result = success(data=data, message="Items retrieved")
        assert result["data"] == data
        assert result["message"] == "Items retrieved"

    def test_success_with_empty_data(self):
        result = success(data=[])
        assert result["data"] == []

    def test_success_with_dict_data(self):
        data = {"key": "value", "count": 42}
        result = success(data=data)
        assert result["data"]["key"] == "value"
        assert result["data"]["count"] == 42

    def test_success_structure(self):
        result = success()
        assert "code" in result
        assert "message" in result
        assert "data" in result
        assert "request_id" in result
        assert len(result) == 4


class TestErrorResponse:
    """Test error response builder"""

    def test_error_code_is_numeric(self):
        result = error(ErrorCode.NOT_FOUND)
        assert result["code"] == 1001
        assert isinstance(result["code"], int)

    def test_error_with_code_only(self):
        result = error(ErrorCode.NOT_FOUND)
        assert result["message"] == "Error"

    def test_error_has_request_id(self):
        result = error(ErrorCode.INTERNAL_ERROR)
        assert "request_id" in result
        assert isinstance(result["request_id"], str)

    def test_error_with_custom_message(self):
        result = error(ErrorCode.INTERNAL_ERROR, message="Database connection failed")
        assert result["code"] == 2001
        assert result["message"] == "Database connection failed"

    def test_error_with_detail(self):
        detail = {"field": "email", "reason": "invalid format"}
        result = error(ErrorCode.INVALID_REQUEST, detail=detail)
        assert result["detail"] == detail

    def test_error_with_suggestion(self):
        suggestion = "Please check the input format and try again"
        result = error(ErrorCode.INVALID_REQUEST, suggestion=suggestion)
        assert result["suggestion"] == suggestion

    def test_error_with_all_fields(self):
        result = error(
            code=ErrorCode.INVALID_REQUEST,
            message="Validation failed",
            detail={"errors": ["field required"]},
            suggestion="Fill in all required fields",
        )
        assert result["code"] == 1002
        assert result["message"] == "Validation failed"
        assert "errors" in result["detail"]
        assert result["suggestion"] == "Fill in all required fields"

    def test_error_without_optional_fields(self):
        result = error(ErrorCode.NOT_FOUND)
        assert "detail" not in result
        assert "suggestion" not in result

    def test_error_with_none_detail_excluded(self):
        result = error(ErrorCode.INTERNAL_ERROR, detail=None)
        assert "detail" not in result

    def test_error_with_none_suggestion_excluded(self):
        result = error(ErrorCode.INTERNAL_ERROR, suggestion=None)
        assert "suggestion" not in result

    def test_all_error_codes_map_to_int(self):
        for code in ErrorCode:
            result = error(code)
            assert isinstance(result["code"], int)


class TestErrorResponseDirect:
    """Test direct numeric error response builder"""

    def test_error_response_basic(self):
        result = error_response(code=1001, message="Not found")
        assert result["code"] == 1001
        assert result["message"] == "Not found"
        assert "request_id" in result

    def test_error_response_with_detail(self):
        result = error_response(
            code=2001, message="Server error", detail={"error_id": "abc"}
        )
        assert result["code"] == 2001
        assert result["detail"] == {"error_id": "abc"}

    def test_error_response_without_detail(self):
        result = error_response(code=1006, message="Bad request")
        assert "detail" not in result


class TestResponseIntegration:
    """Test response patterns in integration scenarios"""

    def test_validation_error_response(self):
        result = error(
            code=ErrorCode.INVALID_REQUEST,
            message="输入验证失败",
            detail={"field": "cutting_speed", "reason": "must be positive"},
            suggestion="请输入大于0的切削速度值",
        )
        assert result["code"] == 1002
        assert "detail" in result
        assert "request_id" in result

    def test_not_found_response(self):
        result = error(
            code=ErrorCode.NOT_FOUND,
            message="资源未找到",
            detail={"resource": "experience", "id": "invalid-id"},
        )
        assert result["code"] == 1001
        assert "request_id" in result

    def test_service_unavailable_response(self):
        result = error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="服务暂不可用",
            suggestion="请稍后重试或联系管理员",
        )
        assert result["code"] == 2002
        assert "request_id" in result

    def test_success_with_pagination(self):
        data = {
            "items": [{"id": 1}, {"id": 2}],
            "total": 100,
            "page": 1,
            "page_size": 10,
        }
        result = success(data=data, message="Page retrieved")
        assert result["data"]["total"] == 100
        assert result["data"]["page"] == 1
        assert result["code"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
