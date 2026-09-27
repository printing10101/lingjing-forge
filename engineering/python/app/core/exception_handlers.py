"""统一异常处理器——将各类异常转换为标准化错误响应。

响应格式: {"code": <int>, "message": "...", "request_id": "<uuid>"}
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    AppException,
    ValidationException,
)

# V3.0: RepositoryError / RecordNotFoundError 已改为继承 AppException，
# FastAPI 自动通过下面的 app_exception_handler 处理，无需单独注册。
from app.core.response import error_response, manufacturing_error
from app.core.error_taxonomy import ManufacturingError

logger = logging.getLogger(__name__)


_STARLETTE_HTTP_TO_CODE: dict[int, int] = {
    400: 1006,
    401: 1003,
    403: 1004,
    404: 1001,
    405: 1006,
    408: 2004,
    409: 1005,
    422: 1002,
    429: 1007,
    500: 2001,
    502: 2003,
    503: 2002,
    504: 2004,
}


def _build_json_response(
    code: int,
    message: str,
    http_status: int,
    detail: Any = None,
) -> JSONResponse:
    body = error_response(code=code, message=message, detail=detail)
    return JSONResponse(status_code=http_status, content=body)


async def app_exception_handler(_request: Request, exc: AppException) -> JSONResponse:
    logger.warning(
        "AppException caught: code=%d message=%s path=%s",
        exc.code,
        exc.message,
        _request.url.path,
    )
    return _build_json_response(
        code=exc.code,
        message=exc.message,
        http_status=exc.status_code,
        detail=exc.detail,
    )


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STARLETTE_HTTP_TO_CODE.get(exc.status_code, 2001)
    # 对 5xx 错误进行脱敏处理，避免泄露敏感信息
    if exc.status_code >= 500:
        message = "系统内部错误，请联系管理员"
        logger.error(
            "HTTPException (5xx): status=%d detail=%s path=%s",
            exc.status_code,
            exc.detail,
            _request.url.path,
        )
    else:
        message = str(exc.detail) if exc.detail else "请求处理异常"
        logger.warning(
            "HTTPException: status=%d message=%s path=%s",
            exc.status_code,
            message,
            _request.url.path,
        )
    return _build_json_response(code=code, message=message, http_status=exc.status_code)


async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for error in exc.errors():
        loc = " -> ".join(str(p) for p in error.get("loc", []))
        msg = error.get("msg", "校验失败")
        # 仅暴露字段路径与校验提示，不回传 Pydantic 内部 ctx/type 等敏感字段
        errors.append(f"{loc}: {msg}")
    if errors:
        detail = "; ".join(errors)
    else:
        # 兜底文案，不直接回传 str(exc) 以避免内部异常信息泄露
        detail = "请求参数校验失败"
    exc_obj = ValidationException(message="请求参数校验失败", detail=detail)
    logger.warning("Validation failed: %s", detail)
    return _build_json_response(
        code=exc_obj.code,
        message=exc_obj.message,
        http_status=exc_obj.status_code,
        detail=exc_obj.detail,
    )


async def generic_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    # [H19] 使用 uuid 作为 error_id，与全系统 error_id 生成方式保持一致
    # 原 f"{id(exc):x}" 使用对象内存地址，CPython 中对象回收后地址可能复用，导致 error_id 不唯一
    error_id = uuid.uuid4().hex[:12]
    logger.error(
        "[UnhandledException] id=%s path=%s type=%s message=%s",
        error_id,
        _request.url.path,
        type(exc).__name__,
        str(exc),
        exc_info=True,
    )
    return _build_json_response(
        code=2001,
        message="系统内部错误，请联系管理员",
        http_status=500,
        detail={"error_id": error_id},
    )


async def manufacturing_error_handler(_request: Request, exc: ManufacturingError) -> JSONResponse:
    logger.warning(
        "[ManufacturingError] code=%s severity=%s message=%s path=%s detail=%s",
        exc.code,
        exc.severity,
        exc.message,
        _request.url.path,
        exc.detail,
    )
    body = manufacturing_error(exc)
    return JSONResponse(status_code=409, content=body)


def register_exception_handlers(app: Any) -> None:
    app.add_exception_handler(ManufacturingError, manufacturing_error_handler)
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
