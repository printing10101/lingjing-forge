"""Unit tests for core response module."""

from __future__ import annotations

from app.core.response import ErrorCode, error, success


class TestSuccessResponse:
    def test_success_with_data(self):
        result = success(data={"result": 42}, message="OK")
        assert result["code"] == 0
        assert result["message"] == "OK"
        assert result["data"]["result"] == 42

    def test_success_without_message(self):
        result = success(data={"key": "value"})
        assert result["code"] == 0
        assert result["data"]["key"] == "value"

    def test_success_with_none_data(self):
        result = success(data=None, message="No data")
        assert result["code"] == 0
        assert result["data"] is None

    def test_success_with_empty_data(self):
        result = success(data={})
        assert result["code"] == 0
        assert result["data"] == {}


class TestErrorResponse:
    def test_error_not_found(self):
        result = error(code=ErrorCode.NOT_FOUND, message="Resource not found")
        assert result["code"] == 1001
        assert result["message"] == "Resource not found"

    def test_error_invalid_request(self):
        result = error(code=ErrorCode.INVALID_REQUEST, message="Bad input")
        assert result["code"] == 1002
        assert result["message"] == "Bad input"

    def test_error_internal(self):
        result = error(code=ErrorCode.INTERNAL_ERROR, message="Something broke")
        assert result["code"] == 2001
        assert result["message"] == "Something broke"

    def test_error_with_default_message(self):
        result = error(code=ErrorCode.NOT_FOUND)
        assert result["code"] == 1001
        assert "message" in result


class TestErrorCodeEnum:
    def test_all_codes_are_defined(self):
        codes = [
            ErrorCode.SUCCESS,
            ErrorCode.NOT_FOUND,
            ErrorCode.INVALID_REQUEST,
            ErrorCode.INTERNAL_ERROR,
        ]
        for code in codes:
            assert isinstance(code.value, str)
            assert len(code.value) > 0
