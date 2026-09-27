"""LNN API 依赖注入和全局状态。

本模块仅承载 **service 实例化与全局单例对象**（如 registry_service、
audit_log、task_manager 等），供 LNN 子路由按模块级变量直接导入使用。

.. note::
    本模块 **不包含** FastAPI ``Depends`` 函数。若后续需要添加基于
    ``Depends`` 的依赖（例如从请求上下文中解析用户身份、读取配置项），
    应放置到 ``app/api/v1/lnn/dependencies_fastapi.py`` 等独立文件，
    以保持"service 实例化"与"FastAPI 依赖"职责分离。

所有通过 ``from app.api.v1.lnn.dependencies import registry_service, ...``
等形式的导入均向后兼容，重构时不得删除或重命名既有导出符号。
"""

from app.services.model_registry_service import get_model_registry_service
from app.audit.audit_log import AuditLog
from app.tasks.task_system import AsyncTaskManager

# 统一服务层 - 不要直接实例化 LNNModelRegistry
registry_service = get_model_registry_service()
model_registry = registry_service.model_registry
pytorch_registry = registry_service.pytorch_registry
model_cache = registry_service.model_cache
training_tasks = registry_service.get_training_tasks()
audit_log = AuditLog()

# 仅用于兼容旧 health_check 端点的活跃任务计数;
# 真正的并发控制由 AsyncTaskManager._semaphore 统一管理。
_active_training_tasks: set[str] = set()

# 并发训练任务上限（与 AsyncTaskManager 默认 max_concurrent=3 保持一致；
# P0-2.3 子路由拆分时引用此常量的 routes_system.py 保留，此处补回定义）
MAX_CONCURRENT_TRAINING_TASKS = 3

task_manager = AsyncTaskManager()


# 以下状态从 routes.py 迁移（P0-2.3 子路由拆分）
import os
from typing import Any

import threading
from pathlib import Path
from datetime import timedelta

# 路径遍历防护：允许的数据文件根目录白名单
_ALLOWED_DATA_BASE_DIRS: list[Path] = [
    Path(os.getenv("LNN_DATA_DIR", Path(__file__).resolve().parents[3] / "data")).resolve(),
    Path(__file__).resolve().parents[4],
]
_extra_data_dirs = os.getenv("LNN_DATA_BASE_DIRS", "")
if _extra_data_dirs:
    for _d in _extra_data_dirs.split(os.pathsep):
        _d = _d.strip()
        if _d:
            _ALLOWED_DATA_BASE_DIRS.append(Path(_d).resolve())

# 训练队列状态（job_id -> {"cancel": Event, "progress": Queue, "created_at": datetime}）
_TRAINING_QUEUES: dict = {}
_TRAINING_QUEUES_LOCK = threading.Lock()
_TRAINING_QUEUE_TTL = timedelta(hours=1)

# 流式推理引擎单例
_hybrid_engine: Any = None
_hybrid_engine_lock = threading.Lock()
