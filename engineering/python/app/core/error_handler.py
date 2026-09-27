"""统一错误处理与可观测性模块。

整合现有异常体系、请求追踪、日志系统，提供：
- 结构化错误响应（含错误代码、消息、时间戳、请求ID）
- 错误分类体系（业务错误、系统错误、外部服务错误）
- 全链路错误追踪上下文
- 错误上下文收集（用于诊断信息复制）

响应格式示例:
{
    "code": 1001,
    "error_code": "BIZ_NOT_FOUND",
    "message": "资源未找到",
    "error_type": "business",
    "severity": "error",
    "timestamp": "2026-06-15T10:30:00.123Z",
    "request_id": "abc123...",
    "path": "/api/v1/lnn/predict",
    "trace_id": "abc123...",
    "detail": {...},
    "suggestion": "请检查参数是否正确"
}
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)


# 错误类型分类


class ErrorType(str, Enum):
    """错误大类分类。"""

    BUSINESS = "business"  # 业务错误 (1xxx)
    SYSTEM = "system"  # 系统错误 (2xxx)
    EXTERNAL = "external"  # 外部服务错误 (6xxx)
    REPOSITORY = "repository"  # 数据仓库错误 (3xxx)
    VALIDATION = "validation"  # 参数校验错误 (1002)
    AUTH = "auth"  # 认证授权错误 (1003/1004)
    MANUFACTURING = "manufacturing"  # 制造工艺错误 (Exxx)
    UNKNOWN = "unknown"  # 未知错误


class ErrorSeverity(str, Enum):
    """错误严重程度。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# 错误码到分类的映射

_CODE_TO_ERROR_TYPE: dict[int, ErrorType] = {}

# 客户端/业务错误 (1xxx)
for _c in range(1000, 1099):
    _CODE_TO_ERROR_TYPE[_c] = ErrorType.BUSINESS
# 特殊：校验错误
_CODE_TO_ERROR_TYPE[1002] = ErrorType.VALIDATION
# 特殊：认证授权
_CODE_TO_ERROR_TYPE[1003] = ErrorType.AUTH
_CODE_TO_ERROR_TYPE[1004] = ErrorType.AUTH

# 服务端/系统错误 (2xxx)
for _c in range(2000, 2099):
    _CODE_TO_ERROR_TYPE[_c] = ErrorType.SYSTEM

# 仓库层错误 (3xxx)
for _c in range(3000, 3099):
    _CODE_TO_ERROR_TYPE[_c] = ErrorType.REPOSITORY

# 执行锁错误 (4xxx) — 归入业务
for _c in range(4000, 4099):
    _CODE_TO_ERROR_TYPE[_c] = ErrorType.BUSINESS

# 状态持久化错误 (5xxx) — 归入系统
for _c in range(5000, 5099):
    _CODE_TO_ERROR_TYPE[_c] = ErrorType.SYSTEM

# AI/LLM 外部服务错误 (6xxx)
for _c in range(6000, 6099):
    _CODE_TO_ERROR_TYPE[_c] = ErrorType.EXTERNAL

# CAD 错误 (7xxx) — 归入业务
for _c in range(7000, 7099):
    _CODE_TO_ERROR_TYPE[_c] = ErrorType.BUSINESS


# 字符串错误码 (E1xxx-E5xxx) 前缀映射
_MFG_PREFIX_TO_TYPE: dict[str, ErrorType] = {
    "E1": ErrorType.MANUFACTURING,
    "E2": ErrorType.MANUFACTURING,
    "E3": ErrorType.MANUFACTURING,
    "E4": ErrorType.MANUFACTURING,
    "E5": ErrorType.SYSTEM,
}


def classify_error_by_code(code: int | str) -> ErrorType:
    """根据错误码推断错误分类。

    Args:
        code: 数值错误码（如 1001）或字符串错误码（如 "E3004"）

    Returns:
        ErrorType 枚举值
    """
    if isinstance(code, str):
        prefix = code[:2] if len(code) >= 2 else ""
        return _MFG_PREFIX_TO_TYPE.get(prefix, ErrorType.UNKNOWN)
    return _CODE_TO_ERROR_TYPE.get(code, ErrorType.UNKNOWN)


def classify_severity(http_status: int | None = None, code: int | str | None = None) -> ErrorSeverity:
    """根据 HTTP 状态码或错误码推断严重程度。"""
    if http_status is not None:
        if http_status < 400:
            return ErrorSeverity.INFO
        if http_status < 500:
            return ErrorSeverity.WARNING
        return ErrorSeverity.ERROR
    if code is not None:
        error_type = classify_error_by_code(code)
        if error_type == ErrorType.SYSTEM:
            return ErrorSeverity.ERROR
        if error_type == ErrorType.EXTERNAL:
            return ErrorSeverity.ERROR
        return ErrorSeverity.WARNING
    return ErrorSeverity.ERROR


# 错误码到字符串标识的映射

_NUMERIC_TO_STRING_CODE: dict[int, str] = {
    1001: "BIZ_NOT_FOUND",
    1002: "BIZ_VALIDATION",
    1003: "AUTH_UNAUTHORIZED",
    1004: "AUTH_FORBIDDEN",
    1005: "BIZ_CONFLICT",
    1006: "BIZ_BAD_REQUEST",
    1007: "BIZ_RATE_LIMIT",
    2001: "SYS_INTERNAL",
    2002: "SYS_UNAVAILABLE",
    2003: "SYS_GATEWAY",
    2004: "SYS_TIMEOUT",
    3001: "REPO_ERROR",
    3002: "REPO_NOT_FOUND",
    3003: "REPO_STORAGE",
    4001: "LOCK_ERROR",
    4002: "LOCK_CONFLICT",
    6001: "EXT_LLM_ERROR",
    6002: "EXT_LLM_RATE_LIMIT",
    6003: "EXT_LLM_RESPONSE",
    7001: "BIZ_CAD_ERROR",
    7002: "BIZ_CAD_SCRIPT",
    7003: "BIZ_CAD_EXPORT",
}


def get_string_error_code(numeric_code: int) -> str:
    """将数值错误码转换为字符串标识。"""
    return _NUMERIC_TO_STRING_CODE.get(numeric_code, f"ERR_{numeric_code}")


# 结构化错误响应构建


def build_error_response(
    code: int,
    message: str,
    *,
    http_status: int = 500,
    detail: Any = None,
    suggestion: str | None = None,
    severity: str | None = None,
    path: str | None = None,
    error_code: str | None = None,
    recoverable: bool = False,
    adjusted_values: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建标准化的结构化错误响应。

    所有 API 错误均通过此函数生成统一格式，包含：
    - code: 数值错误码
    - error_code: 字符串错误标识
    - message: 用户可读错误消息
    - error_type: 错误分类（business/system/external/...）
    - severity: 严重程度（info/warning/error/critical）
    - timestamp: ISO 8601 格式时间戳
    - request_id: 请求追踪ID
    - trace_id: 链路追踪ID（与 request_id 相同，便于跨系统关联）
    - path: 请求路径（可选）

    Args:
        code: 数值错误码
        message: 错误消息
        http_status: HTTP 状态码
        detail: 详细错误信息（可选）
        suggestion: 修复建议（可选）
        severity: 严重程度覆盖（可选，自动推断）
        path: 请求路径（可选）
        error_code: 字符串错误码覆盖（可选，自动推断）
        recoverable: 是否可自动恢复
        adjusted_values: 自动调整后的参数值
        extra: 额外自定义字段

    Returns:
        结构化错误响应字典
    """
    request_id = get_request_id()
    error_type = classify_error_by_code(code)
    resolved_severity = severity or classify_severity(http_status=http_status, code=code).value
    resolved_error_code = error_code or get_string_error_code(code)

    response: dict[str, Any] = {
        "code": code,
        "error_code": resolved_error_code,
        "message": message,
        "error_type": error_type.value,
        "severity": resolved_severity,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "request_id": request_id,
        "trace_id": request_id,
    }

    if path:
        response["path"] = path

    if detail is not None:
        response["detail"] = detail

    if suggestion is not None:
        response["suggestion"] = suggestion

    if recoverable:
        response["recoverable"] = True

    if adjusted_values:
        response["adjusted_values"] = adjusted_values

    if extra:
        response.update(extra)

    return response


def build_error_response_from_exception(
    exc: Exception,
    *,
    code: int = 2001,
    message: str | None = None,
    http_status: int = 500,
    path: str | None = None,
    detail: Any = None,
    suggestion: str | None = None,
) -> dict[str, Any]:
    """从异常对象构建结构化错误响应。

    自动提取异常类型信息，在调试模式下包含异常详情。

    Args:
        exc: 原始异常对象
        code: 错误码（默认 2001 系统内部错误）
        message: 面向用户的错误消息（默认通用文案）
        http_status: HTTP 状态码
        path: 请求路径
        detail: 额外详情
        suggestion: 修复建议

    Returns:
        结构化错误响应字典
    """
    from app.core.safe_errors import is_debug_mode

    user_message = message or "系统内部错误，请联系管理员"

    # 在调试模式下保留异常详情
    resolved_detail = detail
    if is_debug_mode():
        debug_info = {
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
        }
        if resolved_detail and isinstance(resolved_detail, dict):
            debug_info.update(resolved_detail)
        elif resolved_detail:
            debug_info["original_detail"] = resolved_detail
        resolved_detail = debug_info

    return build_error_response(
        code=code,
        message=user_message,
        http_status=http_status,
        detail=resolved_detail,
        suggestion=suggestion,
        path=path,
    )


# 错误上下文收集（用于诊断信息）


class ErrorContext:
    """错误上下文收集器。

    收集错误发生时的完整上下文信息，用于：
    - 前端"复制诊断信息"功能
    - 服务端日志关联
    - 跨系统错误追踪
    """

    def __init__(
        self,
        error_code: int | str,
        message: str,
        *,
        request_id: str | None = None,
        path: str | None = None,
        http_status: int | None = None,
        detail: Any = None,
        suggestion: str | None = None,
        severity: str | None = None,
        component: str | None = None,
        user_action: str | None = None,
        extra: dict[str, Any] | None = None,
    ):
        self.error_code = error_code
        self.message = message
        self.request_id = request_id or get_request_id()
        self.path = path
        self.http_status = http_status
        self.detail = detail
        self.suggestion = suggestion
        self.severity = (
            severity
            or classify_severity(
                http_status=http_status,
                code=error_code if isinstance(error_code, int) else None,
            ).value
        )
        self.component = component
        self.user_action = user_action
        self.timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        self.extra = extra or {}

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        result: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
            "request_id": self.request_id,
            "trace_id": self.request_id,
            "timestamp": self.timestamp,
            "severity": self.severity,
        }
        if self.path:
            result["path"] = self.path
        if self.http_status is not None:
            result["http_status"] = self.http_status
        if self.detail is not None:
            result["detail"] = self.detail
        if self.suggestion:
            result["suggestion"] = self.suggestion
        if self.component:
            result["component"] = self.component
        if self.user_action:
            result["user_action"] = self.user_action
        if self.extra:
            result["extra"] = self.extra
        return result

    def to_diagnostic_text(self) -> str:
        """生成人类可读的诊断信息文本（用于复制）。"""
        lines = [
            "=== 错误诊断信息 ===",
            f"时间: {self.timestamp}",
            f"错误码: {self.error_code}",
            f"消息: {self.message}",
            f"严重程度: {self.severity}",
            f"请求ID: {self.request_id}",
        ]
        if self.path:
            lines.append(f"路径: {self.path}")
        if self.http_status is not None:
            lines.append(f"HTTP状态: {self.http_status}")
        if self.component:
            lines.append(f"组件: {self.component}")
        if self.user_action:
            lines.append(f"用户操作: {self.user_action}")
        if self.suggestion:
            lines.append(f"建议: {self.suggestion}")
        if self.detail:
            if isinstance(self.detail, dict):
                lines.append(f"详情: {json.dumps(self.detail, ensure_ascii=False, indent=2)}")
            else:
                lines.append(f"详情: {self.detail}")
        if self.extra:
            lines.append(f"附加信息: {json.dumps(self.extra, ensure_ascii=False, indent=2)}")
        lines.append("===================")
        return "\n".join(lines)


# 错误日志增强


def log_error(
    exc: Exception,
    *,
    code: int | str | None = None,
    context: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    """增强型错误日志记录。

    自动附加 request_id、异常类型、上下文信息。
    返回生成的 error_id 用于前端关联。

    Args:
        exc: 异常对象
        code: 错误码
        context: 发生位置的上下文描述
        extra: 额外信息

    Returns:
        error_id 字符串
    """
    error_id = uuid.uuid4().hex[:12]
    request_id = get_request_id()

    log_data = {
        "error_id": error_id,
        "request_id": request_id,
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
    }
    if code is not None:
        log_data["error_code"] = str(code)
    if context:
        log_data["context"] = context
    if extra:
        log_data.update(extra)

    logger.error(
        "[Error] error_id=%s request_id=%s type=%s context=%s message=%s",
        error_id,
        request_id,
        type(exc).__name__,
        context or "<unspecified>",
        str(exc),
        exc_info=True,
    )

    return error_id
