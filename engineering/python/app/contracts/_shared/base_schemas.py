"""跨领域通用 Pydantic 模型基类。

消除 30+ 文件中重复的 PaginatedResponse / ErrorResponse / TimestampedModel 等定义。
所有模型继承自 pydantic.BaseModel，保持与现有代码 100% 兼容。

设计说明
--------
- 本模块仅收录**跨多个领域复用**的通用 schema；领域专属契约仍归各自模块。
- 与 ``app/core/response_models.py`` 的关系：
  * ``app.core.response_models.ErrorResponse`` 使用 ``code: int``（数值错误码），
    与本模块的 ``ErrorResponse``（``code: str``，字符串错误码）语义不同。
    二者并存：数值版用于项目统一错误响应链路，字符串版用于通用 REST 风格场景。
  * ``app.core.response_models.PaginatedData`` 与本模块的 ``PaginatedResponse``
    字段集略有差异（前者多 ``pages`` 字段，后者多 ``has_next``）。
    保留两套以兼容历史调用方，新代码按需选用。
- ``TaskListResponse`` 收录于本模块是因为 7 个 v1 路由文件存在字节级相同的定义。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """通用分页响应。

    泛型参数 ``T`` 表示单条数据项的模型类型。配合
    ``SuccessResponse[PaginatedResponse[ItemModel]]`` 即可在 OpenAPI 中
    生成精确的分页 schema。
    """

    items: list[T] = Field(..., description="当前页数据列表")
    total: int = Field(..., ge=0, description="数据总条数")
    page: int = Field(1, ge=1, description="当前页码（1-based）")
    page_size: int = Field(..., ge=1, le=1000, description="每页条数")
    has_next: bool = Field(False, description="是否存在下一页")


class ErrorResponse(BaseModel):
    """通用错误响应（字符串错误码版本）。

    注意：与 ``app.core.response_models.ErrorResponse``（数值错误码）语义不同，
    不可互换使用。本模型适用于 REST 风格的字符串错误码场景。
    """

    code: str = Field(..., description="字符串错误码，如 INVALID_INPUT")
    message: str = Field(..., description="错误描述信息")
    detail: str | None = Field(default=None, description="错误详情")
    request_id: str | None = Field(default=None, description="请求追踪标识")


class TimestampedModel(BaseModel):
    """带时间戳的模型基类。

    可作为需要记录创建/更新时间的领域模型基类继承使用。
    """

    created_at: datetime | None = Field(default=None, description="创建时间")
    updated_at: datetime | None = Field(default=None, description="更新时间")


class MessageResponse(BaseModel):
    """简单消息响应。"""

    message: str = Field(..., description="消息内容")
    success: bool = Field(default=True, description="是否成功")


class HealthResponse(BaseModel):
    """通用健康检查响应。

    注意：领域专属的健康检查（如 ``app.models.schemas.HealthResponse``
    含 ``ai_status``，``app.integrations.mes.api.HealthResponse`` 含
    ``mes_connected``）请继续使用各自模块的定义，本模型仅用于通用场景。
    """

    status: str = Field(default="ok", description="服务状态")
    version: str | None = Field(default=None, description="服务版本")
    uptime_seconds: float | None = Field(default=None, description="运行时长（秒）")


class TaskListResponse(BaseModel):
    """通用任务列表响应。

    收录原因：``app/api/v1/`` 下 7 个路由模块存在字节级相同的本定义副本。
    统一引用以消除重复维护负担，OpenAPI schema 也将合并为单一 ``TaskListResponse``。
    """

    tasks: list[dict[str, Any]] = Field(..., description="任务列表")
    total: int = Field(..., description="任务总数")


__all__ = [
    "PaginatedResponse",
    "ErrorResponse",
    "TimestampedModel",
    "MessageResponse",
    "HealthResponse",
    "TaskListResponse",
]
