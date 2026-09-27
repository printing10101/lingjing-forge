"""统一错误处理模块单元测试。

测试覆盖：
- 错误分类体系（ErrorType、ErrorSeverity）
- 错误码映射和分类
- 结构化错误响应构建
- 错误上下文收集（ErrorContext）
- 诊断信息生成
"""

import pytest
from datetime import datetime

from app.core.error_handler import (
    ErrorType,
    ErrorSeverity,
    classify_error_by_code,
    classify_severity,
    get_string_error_code,
    build_error_response,
    build_error_response_from_exception,
    ErrorContext,
    log_error,
)


class TestErrorType:
    """错误类型分类测试。"""

    def test_error_type_values(self):
        """测试错误类型枚举值。"""
        assert ErrorType.BUSINESS.value == "business"
        assert ErrorType.SYSTEM.value == "system"
        assert ErrorType.EXTERNAL.value == "external"
        assert ErrorType.REPOSITORY.value == "repository"
        assert ErrorType.VALIDATION.value == "validation"
        assert ErrorType.AUTH.value == "auth"
        assert ErrorType.MANUFACTURING.value == "manufacturing"
        assert ErrorType.UNKNOWN.value == "unknown"


class TestErrorSeverity:
    """错误严重程度测试。"""

    def test_severity_values(self):
        """测试严重程度枚举值。"""
        assert ErrorSeverity.INFO.value == "info"
        assert ErrorSeverity.WARNING.value == "warning"
        assert ErrorSeverity.ERROR.value == "error"
        assert ErrorSeverity.CRITICAL.value == "critical"


class TestClassifyErrorByCode:
    """错误码分类测试。"""

    def test_business_error_codes(self):
        """测试业务错误码分类（1xxx）。"""
        assert classify_error_by_code(1001) == ErrorType.BUSINESS
        assert classify_error_by_code(1005) == ErrorType.BUSINESS
        assert classify_error_by_code(1006) == ErrorType.BUSINESS

    def test_validation_error_code(self):
        """测试校验错误码分类。"""
        assert classify_error_by_code(1002) == ErrorType.VALIDATION

    def test_auth_error_codes(self):
        """测试认证授权错误码分类。"""
        assert classify_error_by_code(1003) == ErrorType.AUTH
        assert classify_error_by_code(1004) == ErrorType.AUTH

    def test_system_error_codes(self):
        """测试系统错误码分类（2xxx）。"""
        assert classify_error_by_code(2001) == ErrorType.SYSTEM
        assert classify_error_by_code(2002) == ErrorType.SYSTEM

    def test_repository_error_codes(self):
        """测试仓库层错误码分类（3xxx）。"""
        assert classify_error_by_code(3001) == ErrorType.REPOSITORY
        assert classify_error_by_code(3002) == ErrorType.REPOSITORY

    def test_execution_lock_error_codes(self):
        """测试执行锁错误码分类（4xxx）。"""
        assert classify_error_by_code(4001) == ErrorType.BUSINESS
        assert classify_error_by_code(4002) == ErrorType.BUSINESS

    def test_state_persistence_error_codes(self):
        """测试状态持久化错误码分类（5xxx）。"""
        assert classify_error_by_code(5001) == ErrorType.SYSTEM

    def test_external_service_error_codes(self):
        """测试外部服务错误码分类（6xxx）。"""
        assert classify_error_by_code(6001) == ErrorType.EXTERNAL
        assert classify_error_by_code(6002) == ErrorType.EXTERNAL

    def test_cad_error_codes(self):
        """测试CAD错误码分类（7xxx）。"""
        assert classify_error_by_code(7001) == ErrorType.BUSINESS
        assert classify_error_by_code(7002) == ErrorType.BUSINESS

    def test_manufacturing_string_codes(self):
        """测试制造工艺字符串错误码分类（E1xxx-E4xxx）。"""
        assert classify_error_by_code("E1001") == ErrorType.MANUFACTURING
        assert classify_error_by_code("E2001") == ErrorType.MANUFACTURING
        assert classify_error_by_code("E3004") == ErrorType.MANUFACTURING
        assert classify_error_by_code("E4001") == ErrorType.MANUFACTURING

    def test_system_string_codes(self):
        """测试系统字符串错误码分类（E5xxx）。"""
        assert classify_error_by_code("E5001") == ErrorType.SYSTEM

    def test_unknown_string_codes(self):
        """测试未知字符串错误码分类。"""
        assert classify_error_by_code("E9001") == ErrorType.UNKNOWN
        assert classify_error_by_code("X123") == ErrorType.UNKNOWN

    def test_unknown_numeric_codes(self):
        """测试未知数值错误码分类。"""
        assert classify_error_by_code(9999) == ErrorType.UNKNOWN
        assert classify_error_by_code(0) == ErrorType.UNKNOWN


class TestClassifySeverity:
    """错误严重程度分类测试。"""

    def test_severity_by_http_status(self):
        """测试根据HTTP状态码推断严重程度。"""
        assert classify_severity(http_status=200) == ErrorSeverity.INFO
        assert classify_severity(http_status=301) == ErrorSeverity.INFO
        assert classify_severity(http_status=400) == ErrorSeverity.WARNING
        assert classify_severity(http_status=404) == ErrorSeverity.WARNING
        assert classify_severity(http_status=500) == ErrorSeverity.ERROR
        assert classify_severity(http_status=503) == ErrorSeverity.ERROR

    def test_severity_by_error_code(self):
        """测试根据错误码推断严重程度。"""
        # 系统错误 -> error
        assert classify_severity(code=2001) == ErrorSeverity.ERROR
        # 外部服务错误 -> error
        assert classify_severity(code=6001) == ErrorSeverity.ERROR
        # 业务错误 -> warning
        assert classify_severity(code=1001) == ErrorSeverity.WARNING

    def test_severity_default(self):
        """测试默认严重程度。"""
        assert classify_severity() == ErrorSeverity.ERROR


class TestGetStringErrorCode:
    """字符串错误码转换测试。"""

    def test_known_codes(self):
        """测试已知错误码的字符串转换。"""
        assert get_string_error_code(1001) == "BIZ_NOT_FOUND"
        assert get_string_error_code(1002) == "BIZ_VALIDATION"
        assert get_string_error_code(2001) == "SYS_INTERNAL"
        assert get_string_error_code(6001) == "EXT_LLM_ERROR"

    def test_unknown_codes(self):
        """测试未知错误码的字符串转换。"""
        assert get_string_error_code(9999) == "ERR_9999"
        assert get_string_error_code(0) == "ERR_0"


class TestBuildErrorResponse:
    """结构化错误响应构建测试。"""

    def test_basic_response(self):
        """测试基本错误响应构建。"""
        response = build_error_response(
            code=1001,
            message="资源未找到",
            http_status=404,
        )

        assert response["code"] == 1001
        assert response["error_code"] == "BIZ_NOT_FOUND"
        assert response["message"] == "资源未找到"
        assert response["error_type"] == "business"
        assert response["severity"] == "warning"
        assert "timestamp" in response
        assert "request_id" in response
        assert response["trace_id"] == response["request_id"]

    def test_response_with_all_fields(self):
        """测试包含所有字段的错误响应。"""
        response = build_error_response(
            code=2001,
            message="系统内部错误",
            http_status=500,
            detail={"exception": "ValueError"},
            suggestion="请联系管理员",
            severity="critical",
            path="/api/v1/test",
            error_code="CUSTOM_ERROR",
            recoverable=True,
            adjusted_values={"param1": 100},
            extra={"custom_field": "value"},
        )

        assert response["code"] == 2001
        assert response["error_code"] == "CUSTOM_ERROR"
        assert response["message"] == "系统内部错误"
        assert response["error_type"] == "system"
        assert response["severity"] == "critical"
        assert response["path"] == "/api/v1/test"
        assert response["detail"] == {"exception": "ValueError"}
        assert response["suggestion"] == "请联系管理员"
        assert response["recoverable"] is True
        assert response["adjusted_values"] == {"param1": 100}
        assert response["custom_field"] == "value"

    def test_response_timestamp_format(self):
        """测试时间戳格式为ISO 8601。"""
        response = build_error_response(code=1001, message="test")
        timestamp = response["timestamp"]
        # 验证可以解析为datetime
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        assert isinstance(dt, datetime)


class TestBuildErrorResponseFromException:
    """从异常构建错误响应测试。"""

    def test_basic_exception(self):
        """测试基本异常转换。"""
        exc = ValueError("测试异常")
        response = build_error_response_from_exception(
            exc,
            code=2001,
            http_status=500,
        )

        assert response["code"] == 2001
        assert response["message"] == "系统内部错误，请联系管理员"
        assert response["error_type"] == "system"

    def test_exception_with_custom_message(self):
        """测试自定义消息的异常转换。"""
        exc = RuntimeError("原始错误")
        response = build_error_response_from_exception(
            exc,
            code=1001,
            message="自定义错误消息",
            http_status=404,
        )

        assert response["message"] == "自定义错误消息"


class TestErrorContext:
    """错误上下文收集测试。"""

    def test_basic_context(self):
        """测试基本错误上下文。"""
        ctx = ErrorContext(
            error_code=1001,
            message="资源未找到",
            path="/api/v1/test",
        )

        assert ctx.error_code == 1001
        assert ctx.message == "资源未找到"
        assert ctx.path == "/api/v1/test"
        assert ctx.timestamp is not None
        assert ctx.request_id is not None

    def test_context_to_dict(self):
        """测试上下文转换为字典。"""
        ctx = ErrorContext(
            error_code=2001,
            message="系统错误",
            http_status=500,
            detail={"key": "value"},
            suggestion="重试",
            component="test_component",
            user_action="submit_form",
        )

        result = ctx.to_dict()

        assert result["error_code"] == 2001
        assert result["message"] == "系统错误"
        assert result["http_status"] == 500
        assert result["detail"] == {"key": "value"}
        assert result["suggestion"] == "重试"
        assert result["component"] == "test_component"
        assert result["user_action"] == "submit_form"

    def test_context_to_diagnostic_text(self):
        """测试生成诊断文本。"""
        ctx = ErrorContext(
            error_code=1001,
            message="资源未找到",
            path="/api/v1/users/123",
            http_status=404,
            suggestion="请检查资源ID是否正确",
        )

        text = ctx.to_diagnostic_text()

        assert "=== 错误诊断信息 ===" in text
        assert "错误码: 1001" in text
        assert "消息: 资源未找到" in text
        assert "路径: /api/v1/users/123" in text
        assert "HTTP状态: 404" in text
        assert "建议: 请检查资源ID是否正确" in text
        assert "===================" in text

    def test_context_with_dict_detail(self):
        """测试包含字典详情的上下文。"""
        ctx = ErrorContext(
            error_code=2001,
            message="系统错误",
            detail={"exception_type": "ValueError", "line": 42},
        )

        text = ctx.to_diagnostic_text()
        assert "详情:" in text
        assert "exception_type" in text or "ValueError" in text


class TestLogError:
    """错误日志记录测试。"""

    def test_log_error_returns_error_id(self):
        """测试日志记录返回error_id。"""
        exc = ValueError("测试错误")
        error_id = log_error(exc, code=2001, context="test_context")

        assert isinstance(error_id, str)
        assert len(error_id) == 12  # uuid4 hex[:12]

    def test_log_error_with_extra(self):
        """测试带额外信息的日志记录。"""
        exc = RuntimeError("测试")
        error_id = log_error(
            exc,
            code=1001,
            context="api_handler",
            extra={"user_id": 123, "action": "delete"},
        )

        assert isinstance(error_id, str)


class TestErrorClassificationIntegration:
    """错误分类集成测试。"""

    def test_full_error_flow(self):
        """测试完整的错误处理流程。"""
        # 1. 根据错误码分类
        code = 6001
        error_type = classify_error_by_code(code)
        assert error_type == ErrorType.EXTERNAL

        # 2. 确定严重程度
        severity = classify_severity(http_status=503, code=code)
        assert severity == ErrorSeverity.ERROR

        # 3. 获取字符串错误码
        string_code = get_string_error_code(code)
        assert string_code == "EXT_LLM_ERROR"

        # 4. 构建响应
        response = build_error_response(
            code=code,
            message="LLM服务不可用",
            http_status=503,
            suggestion="请稍后重试",
        )

        assert response["error_type"] == "external"
        assert response["severity"] == "error"
        assert response["error_code"] == "EXT_LLM_ERROR"
        assert response["suggestion"] == "请稍后重试"

        # 5. 收集上下文
        ctx = ErrorContext(
            error_code=code,
            message=response["message"],
            request_id=response["request_id"],
            path="/api/v1/llm/predict",
            http_status=503,
        )

        assert ctx.request_id == response["request_id"]
        diagnostic_text = ctx.to_diagnostic_text()
        assert "LLM服务不可用" in diagnostic_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
