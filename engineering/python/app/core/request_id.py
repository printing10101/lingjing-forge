"""请求追踪——为每个API请求生成唯一request_id并全链路透传。

使用 contextvars 实现异步安全的请求级上下文传递。
request_id 在中间件、控制器、服务层、数据访问层均可通过 get_request_id() 获取。
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_request_id_var: ContextVar[str] = ContextVar("request_id", default="unknown")

REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> str:
    """获取当前请求上下文的request_id。

    可在请求生命周期内的任意位置调用（中间件、路由处理器、服务层等）。
    若在请求上下文外调用则返回 "unknown"。
    """
    return _request_id_var.get()


def set_request_id(request_id: str) -> None:
    """在当前上下文中设置request_id。"""
    _request_id_var.set(request_id)


def generate_request_id() -> str:
    """生成UUID格式的请求ID（不含连字符，36->32字符）。"""
    return uuid.uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    """请求ID中间件。

    职责：
    1. 从请求头 X-Request-ID 提取已有request_id，若无则生成新的
    2. 注入到 contextvars 以供全链路使用
    3. 在响应头中回写 X-Request-ID

    执行顺序：应作为第一个中间件注册，确保所有后续中间件和路由
    处理器都能获取到 request_id。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or generate_request_id()
        _request_id_var.set(request_id)

        # P1-2 修复：记录请求方法，供 get_db() 决定是否 commit。
        # GET / HEAD / OPTIONS 按语义为只读，不应触发事务提交。
        from app.database.connection import set_request_method

        token = set_request_method(request.method)
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            # contextvar 在请求结束时通过 reset 恢复默认值，避免线程复用导致串请求
            from app.database.connection import _current_request_method

            _current_request_method.reset(token)
