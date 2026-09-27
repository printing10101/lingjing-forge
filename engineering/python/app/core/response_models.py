"""统一 Pydantic 响应模型基类体系。

提供与 ``app.core.response.success`` / ``error`` 函数配套的 Pydantic 响应模型，
用于 FastAPI 端点的 ``response_model`` 参数声明，从而：
  1. 自动生成 OpenAPI 文档的响应 schema；
  2. 提供前端 TypeScript 类型推导依据；
  3. 强约束响应结构，避免 dict 自由格式漂移。

设计原则
--------
- **向后兼容**：现有 ``return success(data=...)`` 调用无需修改，仅在端点签名
  上添加 ``response_model=SuccessResponse[MyDataModel]`` 即可。
- **泛型支持**：使用 ``TypeVar`` + ``Generic[T]`` 让 ``data`` 字段承载具体模型。
- **错误响应统一**：``ErrorResponse`` 与 ``error()`` 函数字段一一对应。

使用示例
--------
.. code-block:: python

    from pydantic import BaseModel
    from app.core.response_models import SuccessResponse, ErrorResponse

    class ItemData(BaseModel):
        id: int
        name: str

    @router.get("/items/{item_id}",
                response_model=SuccessResponse[ItemData],
                responses={404: {"model": ErrorResponse}})
    async def get_item(item_id: int):
        ...
        return success(data=ItemData(id=1, name="x"))

迁移策略
--------
对于历史端点（约 360 个未声明 ``response_model``），建议按以下优先级
逐步迁移，而非一次性批量改造（避免大范围回归风险）：

  1. **P0 优先**：对外公开 API、前端强烈依赖类型推导的端点；
  2. **P1 次优**：写入/修改类端点（POST/PUT/DELETE），约束请求/响应 schema；
  3. **P2 可选**：内部管理类端点、查询类端点。

迁移时只需：
  1. 在端点装饰器添加 ``response_model=SuccessResponse[YourDataModel]``；
  2. 在 ``responses`` 字典中声明错误模型 ``{404: {"model": ErrorResponse}}``；
  3. 端点内部 ``return success(...)`` 调用保持不变。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.response import ErrorCode, code_to_numeric

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """统一错误响应模型。

    与 ``app.core.response.error()`` 返回的 dict 结构一一对应，
    用于 FastAPI ``responses`` 字段声明错误响应 schema。
    """

    code: int = Field(
        ...,
        description="数值错误码（0 表示成功，其余为错误码）",
        examples=[1001],
    )
    message: str = Field(..., description="错误描述信息")
    request_id: str = Field(..., description="请求追踪标识")

    detail: Any | None = Field(default=None, description="错误详情（如字段校验失败详情）")
    suggestion: str | None = Field(default=None, description="修复建议（面向开发者或用户）")
    severity: str | None = Field(default=None, description="错误严重级别：info/warning/error/critical")
    recoverable: bool | None = Field(default=None, description="是否可恢复（True 时前端可重试）")
    adjusted_values: dict[str, Any] | None = Field(default=None, description="服务端调整后的建议值（如参数修正）")


class SuccessResponse(BaseModel, Generic[T]):
    """统一成功响应模型（泛型）。

    通过 ``SuccessResponse[YourDataModel]`` 让 OpenAPI 文档生成精确的
    响应 schema，前端可基于此推导 TypeScript 类型。

    示例::

        @router.get("/users/{uid}",
                    response_model=SuccessResponse[UserOut])
        async def get_user(uid: int): ...

    ``data`` 字段类型由泛型参数 ``T`` 决定；若端点返回自由 dict 而无
    精确模型，可使用 ``SuccessResponse[dict[str, Any]]`` 作为过渡。
    """

    code: int = Field(
        default=0,
        description="错误码（0 表示成功）",
        examples=[0],
    )
    message: str = Field(
        default="Success",
        description="响应描述信息",
        examples=["Success"],
    )
    data: T | None = Field(
        default=None,
        description="业务数据载荷，类型由泛型参数 T 决定",
    )
    request_id: str = Field(..., description="请求追踪标识")


class PaginatedData(BaseModel, Generic[T]):
    """分页数据载荷模型。

    配合 ``SuccessResponse[PaginatedData[ItemModel]]`` 使用，
    统一分页响应结构。
    """

    items: list[T] = Field(..., description="当前页数据列表")
    total: int = Field(..., description="数据总条数", ge=0)
    page: int = Field(..., description="当前页码（1-based）", ge=1)
    page_size: int = Field(..., description="每页条数", ge=1)
    pages: int = Field(..., description="总页数", ge=0)


def error_response_model(
    code: ErrorCode,
    message: str = "Error",
    detail: Any = None,
    suggestion: str | None = None,
    severity: str | None = None,
    recoverable: bool = False,
    adjusted_values: dict[str, Any] | None = None,
) -> ErrorResponse:
    """构造 ``ErrorResponse`` 实例（与 ``error()`` 函数对齐）。

    适用于需要返回 Pydantic 模型而非 dict 的场景（如 FastAPI 异常处理器
    抛出 ``HTTPException`` 时，detail 字段使用 ErrorResponse 序列化）。
    """
    return ErrorResponse(
        code=code_to_numeric(code),
        message=message,
        request_id="",  # 由中间件/异常处理器注入
        detail=detail,
        suggestion=suggestion,
        severity=severity,
        recoverable=recoverable or None,
        adjusted_values=adjusted_values,
    )


__all__ = [
    "ErrorResponse",
    "SuccessResponse",
    "PaginatedData",
    "error_response_model",
]
