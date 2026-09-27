"""lomo SDK 异常层次结构。

所有 SDK 异常派生自 :class:`LomoError`。后端响应信封中的非零 ``code``
会被映射为对应的子类异常（见 :func:`_raise_for_envelope`）。

错误码 ↔ 异常映射表（与 ``app.core.response`` 的 ErrorCode 数值保持一致）：

    0      —— 不抛异常（成功）
    1001   —— :class:`LomoNotFoundError`
    1002   —— :class:`LomoValidationError`
    1003   —— :class:`LomoAuthError`
    1008   —— :class:`LomoNotFoundError`（FILE_NOT_FOUND 视为资源未找到）
    2001   —— :class:`LomoInternalError`
    2002   —— :class:`LomoServiceUnavailableError`
    7001   —— :class:`LomoAPIError`（CAD 生成错误，由调用方按 detail 处理）
    其他   —— :class:`LomoAPIError`
"""

from __future__ import annotations

from typing import Any, Optional


class LomoError(Exception):
    """所有 lomo SDK 异常的基类。"""


class LomoAPIError(LomoError):
    """后端返回非零 ``code``。

    属性:
        code: 后端响应信封中的数值错误码。
        request_id: 后端返回的请求追踪标识（可用于排障对账）。
        detail: 后端附加的详细错误信息（可选）。
        suggestion: 后端给出的修复建议（可选）。
        recoverable: 后端标记该错误是否可重试恢复。
    """

    def __init__(
        self,
        message: str,
        *,
        code: int,
        request_id: Optional[str] = None,
        detail: Any = None,
        suggestion: Optional[str] = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id
        self.detail = detail
        self.suggestion = suggestion
        self.recoverable = recoverable

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"{self.__class__.__name__}(code={self.code!r}, message={str(self)!r}, request_id={self.request_id!r})"


class LomoConnectionError(LomoError):
    """网络层错误（DNS 解析失败 / 连接被拒 / TCP 中断等）。"""


class LomoTimeoutError(LomoError):
    """请求超时（httpx.TimeoutException 包装）。"""


class LomoNotFoundError(LomoAPIError):
    """资源未找到（code=1001 / 1008）。"""


class LomoValidationError(LomoAPIError):
    """请求参数非法或语义错误（code=1002）。"""


class LomoAuthError(LomoAPIError):
    """认证失败或权限不足（code=1003）。"""


class LomoInternalError(LomoAPIError):
    """后端内部错误（code=2001）。通常意味着服务端 bug，建议附带
    ``request_id`` 反馈给后端开发。"""


class LomoServiceUnavailableError(LomoAPIError):
    """服务暂时不可用（code=2002）。一般可重试。"""


# 响应信封 -> 异常 的自动映射

# 数值错误码 -> 异常类的映射表。未列出的码统一抛 LomoAPIError。
_NUMERIC_CODE_TO_EXC: dict[int, type[LomoAPIError]] = {
    1001: LomoNotFoundError,
    1002: LomoValidationError,
    1003: LomoAuthError,
    1008: LomoNotFoundError,  # FILE_NOT_FOUND 视为资源未找到
    2001: LomoInternalError,
    2002: LomoServiceUnavailableError,
}


def _raise_for_envelope(payload: dict[str, Any]) -> None:
    """检查响应信封，若 ``code != 0`` 则抛出对应的 :class:`LomoAPIError` 子类。

    参数:
        payload: 后端返回的完整响应信封 dict。

    抛出:
        LomoAPIError 及其子类：当 ``payload['code'] != 0`` 时。
    """
    code = payload.get("code", 0)
    if code == 0:
        return

    message = str(payload.get("message", "Unknown error"))
    kwargs = dict(
        code=int(code),
        request_id=payload.get("request_id"),
        detail=payload.get("detail"),
        suggestion=payload.get("suggestion"),
        recoverable=bool(payload.get("recoverable", False)),
    )
    exc_cls = _NUMERIC_CODE_TO_EXC.get(int(code), LomoAPIError)
    raise exc_cls(message, **kwargs)


__all__ = [
    "LomoError",
    "LomoAPIError",
    "LomoConnectionError",
    "LomoTimeoutError",
    "LomoNotFoundError",
    "LomoValidationError",
    "LomoAuthError",
    "LomoInternalError",
    "LomoServiceUnavailableError",
    "_raise_for_envelope",
]
