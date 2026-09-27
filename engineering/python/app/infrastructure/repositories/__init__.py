"""基础设施 Repository 层 —— API 与数据访问的隔离边界。

约定：
- Repository 只含数据访问（CRUD），不含业务规则或 HTTP 协议处理。
- API 层通过 ``Depends(get_xxx_repo)`` 获取实例。
- 类型/ORM 模型导入仅限 Repository 内部；API 层只引用 Pydantic schemas。
"""

from app.dependencies import get_agent_state_repo
from app.infrastructure.repositories.agent_state_repo import AgentStateRepo
from app.dependencies import get_notification_repo
from app.infrastructure.repositories.notification_repo import NotificationRepo
from app.dependencies import get_system_repo
from app.infrastructure.repositories.system_repo import SystemRepo

__all__ = [
    "AgentStateRepo",
    "get_agent_state_repo",
    "NotificationRepo",
    "get_notification_repo",
    "SystemRepo",
    "get_system_repo",
]
