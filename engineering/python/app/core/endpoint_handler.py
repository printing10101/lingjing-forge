"""统一的 FastAPI 端点错误处理装饰器。

目的：消除各路由器中大量重复的 try/except 模板代码（截至 2026-07-31，
signal_fusion_kb.py 12次、resource_cards.py 13次、process_explainer.py 7次 等）。

行为：将端点函数包裹在统一的异常处理层中：
  - ValueError → ``ErrorCode.INVALID_REQUEST`` (HTTP 400)
  - 其他 Exception → ``ErrorCode.INTERNAL_ERROR`` (HTTP 500)
  - 所有异常通过 ``safe_error_message()`` 脱敏后再返回前端
  - 异步函数自动检测并使用 ``await``

用法::

    from app.core.endpoint_handler import safe_endpoint

    @router.post("/samples")
    @safe_endpoint(context="signal_fusion_kb.register_sample", fallback="注册失败")
    async def register_sample(request: Request, req: SampleRequest):
        kb = get_kb()
        sample_id = kb.register_sample(sample)
        return success(data={"sample_id": sample_id}, message="已注册")

注意：为避免与 Pydantic 前向引用解析冲突，本模块不使用 ``from __future__ import annotations``。
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from typing import Any, TypeVar
from collections.abc import Callable

from app.core.response import error, ErrorCode
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def safe_endpoint(
    *,
    context: str = "",
    fallback: str = "服务内部错误，请稍后重试",
    reraise: tuple[type[BaseException], ...] | None = None,
) -> Callable[[F], F]:
    """为 FastAPI 端点提供统一的异常处理包裹器。

    Args:
        context: 异常发生位置标识（如 ``"signal_fusion_kb.register_sample"``），
            用于服务端日志关联。
        fallback: 面向用户的通用错误描述，在非调试模式下返回。
        reraise: 可选的需要向上重新抛出的异常类型元组（例如 ``(asyncio.CancelledError,)``），
            这些异常不会被捕获。

    Returns:
        装饰器工厂，返回装饰后的端点函数。
    """
    _reraise = reraise or (asyncio.CancelledError, KeyboardInterrupt, SystemExit)

    def decorator(func: F) -> F:
        is_coro = inspect.iscoroutinefunction(func)

        if is_coro:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except _reraise:
                    raise
                except ValueError as e:
                    safe = safe_error_message(e, context=context or func.__qualname__, fallback="参数错误")
                    return error(
                        ErrorCode.INVALID_REQUEST,
                        message=safe["message"],
                        detail={"error_id": safe["error_id"]},
                    )
                except Exception as e:
                    safe = safe_error_message(e, context=context or func.__qualname__, fallback=fallback)
                    return error(
                        ErrorCode.INTERNAL_ERROR,
                        message=safe["message"],
                        detail={"error_id": safe["error_id"]},
                    )

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except _reraise:
                raise
            except ValueError as e:
                safe = safe_error_message(e, context=context or func.__qualname__, fallback="参数错误")
                return error(
                    ErrorCode.INVALID_REQUEST,
                    message=safe["message"],
                    detail={"error_id": safe["error_id"]},
                )
            except Exception as e:
                safe = safe_error_message(e, context=context or func.__qualname__, fallback=fallback)
                return error(
                    ErrorCode.INTERNAL_ERROR,
                    message=safe["message"],
                    detail={"error_id": safe["error_id"]},
                )

        return sync_wrapper  # type: ignore[return-value]

    return decorator
