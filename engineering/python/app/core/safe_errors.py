"""安全的错误信息处理。

目的：避免把内部异常原始信息（堆栈、路径、SQL 片段、密钥）直接返回给前端。
行为：仅在开发模式（``LNN_DEBUG=1`` 或 ``DEBUG=true``）下保留原始 ``str(e)``；
      其余场景返回通用描述 + 唯一的 ``error_id``，供用户报障时关联服务端日志。
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_DEBUG_ENV_KEYS = ("LNN_DEBUG", "DEBUG", "FLASK_DEBUG", "FASTAPI_DEBUG")


def is_debug_mode() -> bool:
    """是否为开发/调试模式。"""
    for key in _DEBUG_ENV_KEYS:
        val = os.environ.get(key, "").strip().lower()
        if val in {"1", "true", "yes", "on"}:
            return True
    return False


def safe_error_message(
    exc: BaseException,
    *,
    fallback: str = "服务内部错误，请稍后重试",
    context: str = "",
) -> dict[str, Any]:
    """生成对前端安全的错误响应字典。

    Args:
        exc: 原始异常对象。
        fallback: 面向用户的通用错误描述。
        context: 描述异常发生位置（如 "model.predict"），用于服务端日志。

    Returns:
        包含 ``message``、``error_id`` 与可选 ``detail`` 的字典。
        ``error_id`` 是 12 位十六进制字符串，可关联服务端日志。
    """
    error_id = uuid.uuid4().hex[:12]
    logger.exception(
        "internal error | error_id=%s | context=%s",
        error_id,
        context or "<unspecified>",
    )
    payload: dict[str, Any] = {
        "message": fallback,
        "error_id": error_id,
    }
    if is_debug_mode():
        payload["detail"] = f"{type(exc).__name__}: {exc}"
    return payload
