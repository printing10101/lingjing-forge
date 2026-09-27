"""API 响应装饰器。

把 endpoint 中 ``return success(...)`` / ``return error(...)`` 的样板代码
抽离为装饰器，让 endpoint 主体只关注业务逻辑与返回值。

注意：失败响应仍必须显式 ``raise HTTPException(...)`` 或返回 ``error(...)``，
装饰器仅捕获 ``Exception`` 并转换为统一 500 错误，避免泄露异常细节。
"""

from __future__ import annotations

import functools
import logging
from typing import Any, ParamSpec, TypeVar
from collections.abc import Callable, Coroutine

from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def api_response(
    func: Callable[P, Coroutine[Any, Any, Any]],
) -> Callable[P, Coroutine[Any, Any, dict[str, Any]]]:
    """装饰器：把 endpoint 返回值包成标准 success 响应；异常转为 500 错误。

    用法::

        @router.get("/items")
        @api_response
        async def list_items():
            return {"items": [...]}        # → success(data=...)

        @router.get("/items")
        @api_response
        async def list_items():
            return success(data=...)        # 已包好则原样返回
    """
    qualname = f"{func.__module__}.{func.__qualname__}"

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            logger.exception("api_response caught exception in %s", qualname)
            payload = safe_error_message(exc, context=qualname)
            return error(
                code=ErrorCode.INTERNAL_ERROR,
                message=payload["message"],
                detail=payload.get("detail"),
            )
        # 已经是统一响应格式则原样返回
        if isinstance(result, dict) and "code" in result:
            return result
        return success(data=result)

    return wrapper
