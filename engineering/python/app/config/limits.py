"""跨模块复用的运行时限/上限常量。

本模块集中管理在历史中散落于多个业务文件顶部、且**值与语义均一致**
的模块级常量（如默认上传大小、SSE 心跳超时、查询条数上限等），
避免一处调整、多处不同步的运维风险。

设计原则
---------
1. **只收纳真正跨文件复用的常量**。仅本模块使用的常量保留在原模块，
   不强行迁移，避免过度抽象。
2. **保持 100% 向后兼容**。原文件中常被 ``from app.xxx import CONST``
   的常量，在本模块提供同名定义后，原文件改为
   ``from app.config.limits import CONST``，外部导入路径不变。
3. **命名不一致的同值同语义常量**（如 ``MAX_FILE_SIZE`` /
   ``MAX_UPLOAD_SIZE`` / ``DEFAULT_MAX_UPLOAD_SIZE`` 都是 50MB 上传上限），
   在本模块同时提供三个名称，统一指向同一基础常量，避免破坏现有 API。
4. **不收纳值相同但语义不同的常量**。例如 ``ai/llm_client.py``
   与 ``utils/sqlite_retry.py`` 都有 ``DEFAULT_MAX_RETRIES`` 但值不同
   （3 vs 5），分别保留原位。
5. 所有常量均为模块级不可变标量，**不支持**运行时环境变量覆盖；
   需要环境变量覆盖的配置项应放入对应的 dataclass 子模块
   （如 ``app/config/tasks.py``）。

环境变量
---------
本模块不读取环境变量。如需覆盖，请在对应 dataclass 子模块中扩展
（例如 ``TASK_MAX_CONCURRENT`` 已在 ``tasks.py`` 中支持）。
"""

from __future__ import annotations

# 文件上传大小上限

#: : 默认上传文件大小上限（50 MB）。
#: :
#: : 历史上以 ``MAX_FILE_SIZE`` / ``MAX_UPLOAD_SIZE`` / ``DEFAULT_MAX_UPLOAD_SIZE``
#: : 三种名称在 ``dxf/api.py`` / ``step_import/api.py`` / ``utils/upload_security.py``
#: : / ``projects/project_api.py`` / ``utils/utils.py`` 等多处重复定义。
#: : 此处定义基准值，下方提供三个别名供各业务模块按原名称导入。
DEFAULT_MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024

#: : ``dxf/api.py`` 与 ``step_import/api.py`` 原使用的名称（向后兼容别名）。
MAX_FILE_SIZE: int = DEFAULT_MAX_UPLOAD_SIZE

#: : ``utils/upload_security.py`` 与 ``projects/project_api.py`` 原使用的名称
#: : （向后兼容别名）。
MAX_UPLOAD_SIZE: int = DEFAULT_MAX_UPLOAD_SIZE


# SSE 心跳超时

#: : SSE 事件流心跳超时（秒）。
#: :
#: : 历史上在 ``api/v1/jobs.py`` / ``api/v1/workflows.py`` /
#: : ``api/v1/lnn/services.py`` 三处重复定义，且注释明确要求"任一处调整
#: : 需同步更新另一处"。集中到此处后，调整只需改一行。
#: :
#: : 注意：``api/v1/agent_gateway/_state.py`` 中另定义了
#: : ``SSE_HEARTBEAT_TIMEOUT = 30.0``（无 ``_SEC`` 后缀），属于 P2-1 范围外
#: : （由其他任务处理），本模块不强制合并。
SSE_HEARTBEAT_TIMEOUT_SEC: float = 30.0


# 后台线程 Join 超时

#: : 后台事件循环线程的统一 join 超时（秒）。
#: :
#: : 历史上在 ``data/pipeline/loader.py`` / ``integrations/opcua/adapter.py``
#: : / ``plugins/skill_loader/lifecycle.py`` 三处重复定义，且注释明确要求
#: : "任一处调整需同步更新另一处，避免不同模块关停策略不一致"。
DEFAULT_THREAD_JOIN_TIMEOUT_SEC: float = 5.0


# Git 操作超时

#: : Git 命令操作超时（秒）。
#: :
#: : 用于 ``subprocess.run(timeout=...)``，防止 git 命令卡死。
#: : 当前在 ``app/version.py`` 和 ``app/observability/git_collector.py`` 中
#: : 都使用了 5.0s，建议统一此处标准。
GIT_COMMAND_TIMEOUT_SEC: float = 5.0


# 数据库健康检查超时

#: : 数据库健康检查超时（秒）。
#: :
#: : 用于 ``asyncio.wait_for(check_db_health(), timeout=...)``，
#: : 防止 DB 不可达时监听器长时间卡死。
#: : 当前已更新为 5.0s（原实现 3.0s），建议配置化支持环境变量覆盖。
DATABASE_HEALTH_CHECK_TIMEOUT_SEC: float = 5.0


# Alembic 迁移日志限制

#: : Alembic 迁移 stdout 输出行数上限。
#: :
#: : 用于截断长迁移输出，防止 stdout 过长污染日志。
#: : 当前在 ``app/startup_hooks.py`` 中定义为 5000，
#: : 此常量供外部模块参考。
ALEMBIC_STDOUT_LOG_LIMIT: int = 5000

#: : Alembic 迁移 stderr 输出行数上限。
ALEMBIC_STDERR_LOG_LIMIT: int = 5000


# MTConnect 采集超时

#: : MTConnect Agent 连接超时（秒）。
#: :
#: : 用于 ``MTConnectAdapter(config=AdapterConfig(..., timeout=...))``，
#: : 当前在 ``api/v1/monitor_ws.py`` 中定义为 3.0s，
#: : 此常量供外部参考。建议配置化支持环境变量覆盖。
MT_CONNECT_AGENT_TIMEOUT_SEC: float = 3.0


# 查询条数上限

#: : 默认查询条数上限（用于 list_rules / list_documents 等全量加载场景）。
#: :
#: : 历史上在 ``database/rule_db.py`` / ``rules/api.py`` /
#: : ``rag/knowledge_base.py`` 三处重复定义。
DEFAULT_QUERY_LIMIT: int = 10_000

#: : 导出场景下的最大条数上限。
#: :
#: : 历史上在 ``database/rule_db.py`` (``MAX_EXPORT_LIMIT``) 与
#: : ``audit/reader.py`` (``MAX_AUDIT_EXPORT_LIMIT``) 以不同名称重复定义，
#: : 值均为 100_000，语义均为"避免一次性加载过多数据导致内存激增"。
#: : 此处定义基准值，下方提供 ``MAX_AUDIT_EXPORT_LIMIT`` 别名供 audit 模块
#: : 按原名称导入。
MAX_EXPORT_LIMIT: int = 100_000

#: : ``audit/reader.py`` 原使用的名称（向后兼容别名）。
MAX_AUDIT_EXPORT_LIMIT: int = MAX_EXPORT_LIMIT


# SQLite 锁等待超时

#: : SQLite 连接的统一锁等待超时（秒）。
#: :
#: : 历史上在 ``ai/llm/provider_registry.py`` 与
#: : ``ai/process_explainer/session_store.py`` 重复定义，注释明确要求
#: : "避免不同模块锁等待策略不一致"。
DEFAULT_SQLITE_LOCK_TIMEOUT_SEC: float = 10.0


# 后台线程 join 超时

#: : 后台事件循环线程的统一 join 超时（秒）。
#: :
#: : 历史上在 ``data/pipeline/loader.py`` / ``integrations/opcua/adapter.py``
#: : / ``plugins/skill_loader/lifecycle.py`` 三处重复定义，且注释明确要求
#: : "任一处调整需同步更新另一处，避免不同模块关停策略不一致"。
DEFAULT_THREAD_JOIN_TIMEOUT_SEC: float = 5.0


# 训练并发上限

#: : LNN 训练任务并发上限。
#: :
#: : 历史上在 ``api/v1/lnn/dependencies.py`` (``MAX_CONCURRENT_TRAINING_TASKS``)
#: : 与 ``api/v1/agent_gateway/_state.py`` (``MAX_CONCURRENT_TRAINING``)
#: : 重复定义（值均为 3）。``agent_gateway`` 侧由其他任务负责，
#: : 本模块仅提供 ``MAX_CONCURRENT_TRAINING_TASKS`` 供 ``lnn/dependencies.py``
#: : 导入。
#: :
#: : 注意：真正的并发控制由 ``AsyncTaskManager._semaphore`` 统一管理
#: : （见 ``app/config/tasks.py`` 的 ``max_concurrent`` 配置项），
#: : 此常量仅用于兼容旧 ``health_check`` 端点的活跃任务计数。
MAX_CONCURRENT_TRAINING_TASKS: int = 3


# 流式 I/O 缓冲区大小

#: : 文件流式读写的统一分块大小（64 KB）。
#: :
#: : 历史上在以下 4 处以不同名称重复定义，且语义完全一致
#: : （均为"避免一次性 read() 全量入内存，按固定块大小循环读写"）：
#: :
#: : - ``contracts/project_package.py`` (``STREAM_BUFFER_SIZE``)
#: : - ``utils/upload_security.py`` (``_CHUNK_SIZE``)
#: : - ``api/v1/project_packages.py`` 两处局部变量 ``buffer_size``
#: :
#: : 集中到此处后，调整只需改一行；各业务模块按原名称从本模块导入。
#: : 注意：``rag/routes.py`` 中的 ``50 * 1024 * 1024`` 是 RAG 文档上传大小上限
#: : （与日志轮转 / 流式缓冲均无关），不在本常量收录范围。
STREAM_CHUNK_SIZE: int = 64 * 1024

#: : ``contracts/project_package.py`` 原使用的名称（向后兼容别名）。
STREAM_BUFFER_SIZE: int = STREAM_CHUNK_SIZE


# 日志轮转大小上限

#: : 标准日志文件轮转的单文件大小上限（50 MB）。
#: :
#: : 历史上在以下 3 处以不同名称重复定义，且语义完全一致
#: : （均为 ``logging.handlers.RotatingFileHandler.maxBytes`` 的默认值）：
#: :
#: : - ``main.py`` (``LOG_MAX_BYTES``)
#: : - ``core/logging_config.py`` (``DEFAULT_MAX_BYTES = 50 * MB``)
#: : - ``config/logging_config.py`` (``LoggingConfig.max_bytes`` 默认值 52428800)
#: :
#: : 集中到此处后，调整只需改一行。注意：``research_bridge/data_collector.py``
#: : 的 ``BRIDGE_LOG_MAX_BYTES`` 默认 20 MB 是另一套独立部署策略（研究数据
#: : 桥接器与生产日志分离），不在本常量收录范围。
LOG_ROTATE_MAX_BYTES: int = 50 * 1024 * 1024

#: : ``main.py`` 与 ``core/logging_config.py`` 原使用的名称（向后兼容别名）。
LOG_MAX_BYTES: int = LOG_ROTATE_MAX_BYTES
DEFAULT_MAX_BYTES: int = LOG_ROTATE_MAX_BYTES


__all__ = [
    "DEFAULT_MAX_UPLOAD_SIZE",
    "MAX_FILE_SIZE",
    "MAX_UPLOAD_SIZE",
    "SSE_HEARTBEAT_TIMEOUT_SEC",
    "DEFAULT_QUERY_LIMIT",
    "MAX_EXPORT_LIMIT",
    "MAX_AUDIT_EXPORT_LIMIT",
    "DEFAULT_SQLITE_LOCK_TIMEOUT_SEC",
    "DEFAULT_THREAD_JOIN_TIMEOUT_SEC",
    "MAX_CONCURRENT_TRAINING_TASKS",
    "STREAM_CHUNK_SIZE",
    "STREAM_BUFFER_SIZE",
    "LOG_ROTATE_MAX_BYTES",
    "LOG_MAX_BYTES",
    "DEFAULT_MAX_BYTES",
]
