"""
全局异常处理中间件

提供统一的错误响应格式、错误日志记录、熔断器支持。
"""

import logging
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config.environment import EnvironmentConfig

from .exceptions import (
    AppException,
    ErrorLevel,
    CircuitBreakerOpenException,
)
from .log_sanitizer import sanitizer

logger = logging.getLogger(__name__)


class GlobalExceptionMiddleware(BaseHTTPMiddleware):
    """全局异常处理中间件"""

    def __init__(self, app, record_errors: bool = True):
        super().__init__(app)
        self.record_errors = record_errors

    async def dispatch(self, request: Request, call_next):
        """处理请求并捕获异常"""
        try:
            response = await call_next(request)

            # 将异常转换为标准响应格式
            if hasattr(response, "headers") and "x-error-handled" in response.headers:
                del response.headers["x-error-handled"]

            return response

        except AppException as e:
            # 应用异常（已带错误码和状态码）
            return await self._handle_app_exception(request, e)

        except CircuitBreakerOpenException as e:
            # 熔断器打开，返回 429
            return await self._handle_circuit_breaker(request, e)

        except Exception as e:
            # 未处理的异常，记录并返回 500
            return await self._handle_unexpected_exception(request, e)

    async def _handle_app_exception(self, request: Request, exc: AppException):
        """处理应用异常"""
        logger.warning(
            "[AppException] %s: %s (code=%d, level=%s, detail=%s)",
            request.url.path,
            exc.message,
            exc.code,
            exc.error_level.value,
            exc.detail,
        )

        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
            headers={"x-error-handled": "true"},
        )

    async def _handle_circuit_breaker(self, request: Request, exc: CircuitBreakerOpenException):
        """处理熔断器错误"""
        logger.warning(
            "[CircuitBreaker] %s: %s (service=%s)",
            request.url.path,
            exc.message,
            exc.details.get("service", "unknown"),
        )

        return JSONResponse(
            status_code=429,
            content=exc.to_dict(),
            headers={"x-error-handled": "true"},
        )

    async def _handle_unexpected_exception(self, request: Request, exc: Exception):
        """处理未预期的异常"""
        path = str(request.url.path)
        method = request.method

        # 记录完整栈
        logger.exception(
            "[UnexpectedError] %s %s raised %s: %s",
            method,
            path,
            type(exc).__name__,
            str(exc),
        )

        # 环境判定以服务端环境变量为准（与 app/main.py 文档开关一致），
        # 不信任客户端可任意伪造的 x-environment 请求头
        if EnvironmentConfig().is_production:
            error_content = {
                "code": 2001,
                "message": "Internal server error",
                "level": ErrorLevel.ERROR.value,
                "hint": "请稍后重试",
                "retryable": True,
            }
        else:
            error_content = {
                "code": 2001,
                "message": str(exc),
                "level": ErrorLevel.ERROR.value,
                "traceback": self._safe_traceback(exc),
            }

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_content,
            headers={"x-error-handled": "true"},
        )

    def _safe_traceback(self, exc: Exception) -> str:
        """安全地获取异常栈（过滤用户路径等敏感信息）"""
        import traceback

        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        return sanitizer.sanitize_paths("".join(tb))


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """请求日志中间件"""

    async def dispatch(self, request: Request, call_next):
        """记录请求信息"""
        import time
        import uuid

        start_time = time.time()
        request_id = str(uuid.uuid4())[:8]

        logger.info(
            "[Request] %s %s id=%s ip=%s path=%s query=%s",
            request.method,
            request_id,
            request.client.host if request.client else "-",
            request.url.path,
            dict(request.query_params),
        )

        try:
            response = await call_next(request)
            duration = time.time() - start_time

            # 记录响应
            logger.info(
                "[Response] %s %s status=%d duration=%.3fs size=%d",
                request.method,
                request_id,
                response.status_code,
                duration,
                len(response.body) if response.body else 0,
            )

            # 添加请求 ID 到响应头
            response.headers["x-request-id"] = request_id

            return response

        except Exception as e:
            duration = time.time() - start_time

            # 记录失败
            logger.error(
                "[Response] %s %s FAILED duration=%.3fs error=%s",
                request.method,
                request_id,
                duration,
                str(e),
            )
            raise


def setup_exception_handlers(app: FastAPI):
    """设置全局异常处理器"""

    # 添加中间件
    app.add_middleware(GlobalExceptionMiddleware, record_errors=True)
    app.add_middleware(RequestLoggingMiddleware)

    # 注册 FastAPI 级异常处理器
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        """应用异常处理器"""
        logger.warning(
            "[AppException] %s: %s (code=%d)",
            request.url.path,
            exc.message,
            exc.code,
        )

        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @app.exception_handler(CircuitBreakerOpenException)
    async def circuit_breaker_handler(request: Request, exc: CircuitBreakerOpenException):
        """熔断器异常处理器"""
        logger.warning(
            "[CircuitBreaker] %s: service=%s",
            request.url.path,
            exc.details.get("service", "unknown"),
        )

        return JSONResponse(
            status_code=429,
            content=exc.to_dict(),
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """全局异常处理器"""
        path = str(request.url.path)

        logger.exception(
            "[GlobalError] %s: %s raised %s",
            path,
            str(exc),
            type(exc).__name__,
        )

        # 环境判定以服务端环境变量为准；详细信息仅开发环境返回且必须脱敏
        if not EnvironmentConfig().is_production:
            import traceback

            return JSONResponse(
                status_code=500,
                content={
                    "error": "INTERNAL_ERROR",
                    "message": str(exc),
                    "traceback": sanitizer.sanitize_paths(traceback.format_exc()),
                },
            )

        return JSONResponse(
            status_code=500,
            content={
                "code": 2001,
                "message": "Internal server error",
                "level": ErrorLevel.ERROR.value,
                "hint": "请稍后重试",
                "retryable": True,
            },
        )

    return app
