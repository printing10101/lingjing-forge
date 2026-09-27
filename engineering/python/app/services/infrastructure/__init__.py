"""基础设施服务：外部系统连接与底层能力。

包含 Redis 缓存、TDengine 时序数据库、内存缓存等与业务逻辑无关的
基础设施组件。这些服务被领域服务层消费，不包含业务规则。
"""

from app.services import redis_client
from app.services import tdengine_client
from app.services import memory_cache
