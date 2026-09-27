"""共享后台任务基础设施。

为 cam_validation / gcode_generation 等任务型路由模块提供：
- 后台任务引用集合（防 GC）
- 通用错误响应构造器
- 通用下载端点实现
- 通用异常处理装饰器（handle_sovereignty_errors）
"""

from __future__ import annotations

import asyncio
import functools
import logging
from pathlib import Path
from typing import Any
from collections.abc import Callable, Coroutine

from fastapi.responses import FileResponse, JSONResponse

from app.core.response import error, ErrorCode
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

# 后台任务引用集合（防 GC）
_background_tasks: set = set()


def spawn_background_task(coro: Coroutine) -> asyncio.Task:
    """启动后台任务并保存引用，避免被 Python GC 回收。"""
    t = asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)
    return t


def build_not_found_response() -> JSONResponse:
    """任务不存在的统一响应（不回显 task_id，防枚举）。"""
    return JSONResponse(
        status_code=404,
        content=error(ErrorCode.NOT_FOUND, "任务不存在或已被删除"),
    )


def get_task_or_not_found(
    store: Any,
    task_id: str,
    *,
    not_found_exc: type[Exception] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """按 id 取任务；不存在时返回 ``(None, NOT_FOUND 错误响应)``。

    安全约束：错误响应不回显 task_id，防止任务枚举攻击。
    兼容两类任务 store：``get_task`` 返回 ``None`` 表示不存在（此时
    ``not_found_exc`` 留空），或不存在时抛特定异常（传入该异常类型，
    如 ``CamValidationError``）。

    Returns:
        ``(task, None)`` 或 ``(None, error 响应 dict)``。
    """
    not_found_response = error(code=ErrorCode.NOT_FOUND, message="任务不存在或已被删除")
    # 各模块任务 store 的取数方法名：多数为 get_task，feature_extraction 为 get
    get_task = getattr(store, "get_task", None) or store.get
    if not_found_exc is not None:
        try:
            task = get_task(task_id)
        except not_found_exc:
            return None, not_found_response
    else:
        task = get_task(task_id)
    if task is None:
        return None, not_found_response
    return task, None


def build_internal_error(
    exc: Exception,
    *,
    context: str,
    action: str,
    **log_detail: Any,
) -> dict[str, Any]:
    """兜底 ``Exception`` 的统一处理：脱敏 → 记日志 → INTERNAL_ERROR 响应。

    收敛各 service 模块重复的
    ``safe_error_message → logger.error(exc_info=True) → error(INTERNAL_ERROR)``
    三段式样板。日志经 ``context``（含模块.函数名）与 ``action``
    （中文动作描述）定位来源，``log_detail`` 以 k=v 形式附加上下文字段。
    """
    safe = safe_error_message(exc, context=context)
    detail = " ".join(f"{k}={v}" for k, v in log_detail.items())
    logger.error(
        "%s | %s | error_id=%s | exc=%s",
        action,
        detail,
        safe.get("error_id"),
        exc,
        exc_info=True,
    )
    return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"])


def clamp_limit(value: int, *, lo: int = 1, hi: int = 100) -> int:
    """分页 ``limit`` 限幅到 ``[lo, hi]``。"""
    return max(lo, min(hi, value))


def validate_review_action(
    action: str,
    edited_params: dict[str, Any] | None,
    review_status_enum: Any,
    *,
    edited_suggestion: str,
) -> dict[str, Any] | None:
    """工程师审核动作校验；非法时返回错误响应，合法返回 ``None``。

    - ``action`` 必须是 ``review_status_enum`` 的 CONFIRMED / REJECTED / EDITED；
    - ``action=edited`` 时必须提供 ``edited_params``，
      ``edited_suggestion`` 为该场景下返回给前端的完整 suggestion 文案
      （各模块可编辑字段语义不同）。
    """
    valid_actions = {
        review_status_enum.CONFIRMED.value,
        review_status_enum.REJECTED.value,
        review_status_enum.EDITED.value,
    }
    if action not in valid_actions:
        return error(code=ErrorCode.INVALID_REQUEST, message=f"非法 action: {action}，应为 {sorted(valid_actions)}")
    if action == review_status_enum.EDITED.value and not edited_params:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="action=edited 时必须提供 edited_params",
            suggestion=edited_suggestion,
        )
    return None


def build_file_download_response(
    file_path: str | Path | None,
    *,
    media_type: str = "application/octet-stream",
    filename: str | None = None,
) -> FileResponse | JSONResponse:
    """通用文件下载响应：文件不存在/路径为空时返回 JSON 错误。"""
    if not file_path:
        return JSONResponse(
            status_code=404,
            content=error(ErrorCode.NOT_FOUND, "报告文件不存在或路径为空"),
        )
    p = Path(file_path)
    if not p.exists():
        return JSONResponse(
            status_code=404,
            content=error(ErrorCode.NOT_FOUND, "报告文件不存在或已被删除"),
        )
    return FileResponse(
        path=str(p),
        media_type=media_type,
        filename=filename or p.name,
    )


# 通用异常处理装饰器


def _dispatch_sovereignty_error(
    exc: BaseException,
    *,
    context: str,
    tag: str,
    logger: logging.Logger,
    type_error_code: ErrorCode,
    catch_permission_error: bool,
    catch_key_error: bool,
    catch_attribute_error: bool,
) -> dict[str, Any]:
    """将异常映射为 user_sovereignty 端点的标准错误响应。

    异常→错误码映射（保持与原 user_sovereignty.py 一致）：
        ValueError / JSONDecodeError(子类)  → INVALID_REQUEST
        TypeError                            → type_error_code (默认 INVALID_REQUEST)
        KeyError   (仅 catch_key_error)      → NOT_FOUND
        AttributeError (仅 catch_attribute_error) → INTERNAL_ERROR (回显原始消息)
        PermissionError (仅 catch_permission_error) → FORBIDDEN
        OSError / IOError (含 PermissionError 子类) → INTERNAL_ERROR
        Exception (兜底)                     → INTERNAL_ERROR + safe_error_message

    注意：``json.JSONDecodeError`` 是 ``ValueError`` 的子类，原代码中被
    ``except ValueError`` 捕获，因此这里不单独分支，保持与原行为一致。
    """
    if isinstance(exc, ValueError):
        logger.error("%s - value error | err=%s", tag, exc, exc_info=True)
        return error(code=ErrorCode.INVALID_REQUEST, message=f"参数值无效: {exc}")

    if isinstance(exc, TypeError):
        logger.error("%s - type error | err=%s", tag, exc, exc_info=True)
        return error(code=type_error_code, message=f"类型错误: {exc}")

    if isinstance(exc, KeyError) and catch_key_error:
        logger.error("%s - key error | err=%s", tag, exc, exc_info=True)
        return error(code=ErrorCode.NOT_FOUND, message=f"资源未找到: {exc}")

    if isinstance(exc, AttributeError) and catch_attribute_error:
        logger.error("%s - attribute error | err=%s", tag, exc, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=f"属性错误: {exc}")

    if isinstance(exc, PermissionError) and catch_permission_error:
        logger.error("%s - permission error | err=%s", tag, exc, exc_info=True)
        return error(code=ErrorCode.FORBIDDEN, message=f"权限不足: {exc}")

    if isinstance(exc, (OSError, IOError)):
        # PermissionError 是 OSError 子类；若未启用 catch_permission_error，
        # 则在此落入 INTERNAL_ERROR 分支（与原代码 except OSError 行为一致）。
        logger.error("%s - OS error | err=%s", tag, exc, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=f"系统错误: {exc}")

    # 兜底：捕获未预期的异常
    safe = safe_error_message(exc, context=context)
    logger.error(
        "%s - unexpected error | error_id=%s | exc=%s",
        tag,
        safe.get("error_id"),
        exc,
        exc_info=True,
    )
    return error(
        code=ErrorCode.INTERNAL_ERROR,
        message=safe["message"],
        detail=safe.get("detail"),
    )


def handle_sovereignty_errors(
    context: str,
    *,
    log_tag: str | None = None,
    type_error_code: ErrorCode = ErrorCode.INVALID_REQUEST,
    catch_permission_error: bool = False,
    catch_key_error: bool = False,
    catch_attribute_error: bool = False,
) -> Callable:
    """装饰器：统一处理 user_sovereignty 端点的异常。

    将每个端点中重复的 5-6 层 try/except 兜底收敛为一处，保持：
    - 响应格式：``error()`` 返回的 dict（由 FastAPI 序列化为 JSON）
    - 错误码映射：与原 ``user_sovereignty.py`` 完全一致
    - 日志级别：``logger.error`` + ``exc_info=True``
    - ``safe_error_message`` 用于兜底 ``Exception``

    Args:
        context: ``safe_error_message`` 的上下文标识，如
            ``"user_sovereignty.predict"``。
        log_tag: 日志消息前缀；默认使用 ``context``。
        type_error_code: ``TypeError`` 的错误码。多数端点为
            ``INVALID_REQUEST``（默认），``statistics`` / ``settings``
            等端点为 ``INTERNAL_ERROR``。
        catch_permission_error: 是否显式捕获 ``PermissionError`` →
            ``FORBIDDEN``。默认 ``False``，此时 ``PermissionError`` 作为
            ``OSError`` 子类落入 ``INTERNAL_ERROR`` 分支（与原代码
            ``except OSError`` 行为一致）。
        catch_key_error: 是否显式捕获 ``KeyError`` → ``NOT_FOUND``
            （``predict`` 端点需要）。
        catch_attribute_error: 是否显式捕获 ``AttributeError`` →
            ``INTERNAL_ERROR``（回显原始消息，``predict`` 端点需要）。

    可复用：可应用于其他路由模块的 ``async`` 端点。
    """
    tag = log_tag or context

    def decorator(func: Callable) -> Callable:
        # 使用被装饰函数所在模块的 logger，保持日志来源不变
        logger = logging.getLogger(func.__module__)

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                return _dispatch_sovereignty_error(
                    exc,
                    context=context,
                    tag=tag,
                    logger=logger,
                    type_error_code=type_error_code,
                    catch_permission_error=catch_permission_error,
                    catch_key_error=catch_key_error,
                    catch_attribute_error=catch_attribute_error,
                )

        return wrapper

    return decorator
