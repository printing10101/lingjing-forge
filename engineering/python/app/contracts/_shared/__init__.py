"""跨领域共享契约。

提供 30+ 文件中重复定义的通用 Pydantic 模型基类，包括：
- PaginatedResponse：通用分页响应（泛型）
- ErrorResponse：通用错误响应（字符串错误码）
- TimestampedModel：带时间戳的模型基类
- MessageResponse：简单消息响应
- HealthResponse：通用健康检查响应
- TaskListResponse：通用任务列表响应

与 ``app.core.response_models`` 的关系见 ``base_schemas`` 模块文档。
"""

from app.contracts._shared.base_schemas import (
    ErrorResponse,
    HealthResponse,
    MessageResponse,
    PaginatedResponse,
    TaskListResponse,
    TimestampedModel,
)

__all__ = [
    "PaginatedResponse",
    "ErrorResponse",
    "TimestampedModel",
    "MessageResponse",
    "HealthResponse",
    "TaskListResponse",
]
