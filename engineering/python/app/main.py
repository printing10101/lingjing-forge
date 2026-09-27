"""FastAPI application entry point.

职责（阶段 1 装配拆分后）：
- 创建 FastAPI 应用实例
- 注册生命周期事件（startup / shutdown）
- 装配中间件链（委托给 ``app.middleware_stack.register_middleware_stack``）
- 注册 API 路由（委托给 ``app.router_registry.register_routers``）
- 注册异常处理器
- 提供少量内联端点（version / health / logs / sidecar shutdown）

非职责：
- 具体路由的导入与 ``include_router`` 调用 -> ``router_registry.py``
- 中间件的 ``add_middleware`` 调用与顺序控制 -> ``middleware_stack.py``
- 条件导入标志位的定义 -> ``router_registry.py``（本文件仅重新导出以保持向后兼容）
"""

from __future__ import annotations

import logging
import logging.config
import os
import sys
from pathlib import Path

# 确保 shared/ 契约层在 Python 路径中（V3.0 架构）
# shared/ 位于 monorepo 根目录，main.py 位于 engineering/python/app/
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import uvicorn
from fastapi import FastAPI

from app.api.v1.sse import sse_manager
from app.middleware.cors_config import (
    cors_settings,
    enforce_startup_security,
    CorsConfigError,
)
from app.core.exception_handlers import register_exception_handlers
from app.core.logging_config import configure_logging
from app.utils.utils import get_metrics_collector
from app.sidecar.sidecar_lifecycle import GracefulShutdownHandler

# 修复：使用支持 base_dir 参数的 ring_buffer 版本（dependencies 单例版无参，
# 与 L120 的 base_dir= 调用不匹配——2026-08-03 安装验证发现）
from app.utils.ring_buffer import get_ring_log_buffer
from app.config import config
from app.config.limits import LOG_MAX_BYTES
from app.startup_hooks import (
    run_alembic_upgrade,
    verify_critical_dependencies,
)
from app.version import VERSION as PY_VERSION

# 阶段 1 装配拆分：路由注册与中间件装配已迁移至独立模块
# - router_registry.register_routers: 集中管理 60+ 个 include_router 调用
# - middleware_stack.register_middleware_stack: 集中管理 6 个中间件的注册顺序
# - 条件导入标志位（_OLLAMA_AVAILABLE 等）在 router_registry 中定义，
# 此处重新导出以保持向后兼容（部分测试文件直接 from app.main import _FLAG）
from app.router_registry import (
    register_routers,
    _OLLAMA_AVAILABLE,
    _IMAGE_TO_3D_AVAILABLE,
    _FEATURE_EXTRACTION_AVAILABLE,
    _PARAMETRIC_GEOMETRY_AVAILABLE,
    _CUTTING_PARAMETERS_AVAILABLE,
    _CHATTER_PREDICTION_AVAILABLE,
    _GCODE_GENERATION_AVAILABLE,
    _CAM_VALIDATION_AVAILABLE,
)
from app.middleware_stack import register_middleware_stack

__all__ = [
    # 应用实例
    "app",
    # 装配函数（供测试与 sidecar_main 显式调用）
    "register_routers",
    "register_middleware_stack",
    # 条件导入标志位（向后兼容：测试文件仍可 from app.main import _FLAG）
    "_OLLAMA_AVAILABLE",
    "_IMAGE_TO_3D_AVAILABLE",
    "_FEATURE_EXTRACTION_AVAILABLE",
    "_PARAMETRIC_GEOMETRY_AVAILABLE",
    "_CUTTING_PARAMETERS_AVAILABLE",
    "_CHATTER_PREDICTION_AVAILABLE",
    "_GCODE_GENERATION_AVAILABLE",
    "_CAM_VALIDATION_AVAILABLE",
]

# P2-5-2 修复：提取日志配置魔法数字为命名常量，便于统一管理
# LOG_MAX_BYTES 由 ``app.config.limits`` 集中管理（50 MB），与
# ``core/logging_config.py`` / ``config/logging_config.py`` 共享同一基准值。
LOG_RETENTION_DAYS: int = 30
# 优雅关闭延迟（秒）：等待 HTTP 响应发送完成后再触发 shutdown
SHUTDOWN_DELAY_SECONDS: float = 0.2
# Alembic 迁移日志截断长度已迁移至 ``app.startup_hooks`` 模块

_log_root = os.environ.get("LNN_LOG_DIR", str(Path(config.paths.gstack_dir).parent / "logs"))
configure_logging(
    level=logging.INFO,
    log_root=_log_root,
    module_name="python",
    max_bytes=LOG_MAX_BYTES,
    retention_days=LOG_RETENTION_DAYS,
)
logger = logging.getLogger(__name__)


# 运行期共享对象（metrics / ring_log / state_file_path）
# 这些对象在 startup 之前就需要存在（中间件装配依赖），因此放在模块级初始化。
metrics = get_metrics_collector()
ring_log = get_ring_log_buffer(base_dir=config.paths.gstack_dir)

# P0-11 修复：state_file 路径三方一致。
# 此前 STATE_FILE_PATH 取自 gstack_dir/sidecar.json，但 Rust 端（sidecar.rs wait_ready）
# 读取的是 LNN_LOG_DIR/sidecar.json，sidecar_main.py 默认也是 LNN_LOG_DIR/sidecar.json。
# 三方不一致导致 Rust 端永远读不到 Python 写入的 state 文件，无法快速感知 failed/stopped 状态。
# 现统一为：优先使用 LNN_LOG_DIR/sidecar.json；若 LNN_LOG_DIR 未设置则回退到 gstack_dir/sidecar.json
# （保持桌面开发模式兼容）。
if os.environ.get("LNN_LOG_DIR"):
    STATE_FILE_PATH = str(Path(os.environ["LNN_LOG_DIR"]) / "sidecar.json")
else:
    STATE_FILE_PATH = str(Path(config.paths.gstack_dir) / "sidecar.json")


def get_state_file_path() -> str:
    return STATE_FILE_PATH


# 应用实例创建
# P2-1 修复：生产环境关闭 docs_url/redoc_url/openapi_url，避免接口暴露
# 通过 LNN_ENVIRONMENT / ENVIRONMENT 控制：production 时关闭，其他环境开启
_LNN_ENV = os.environ.get("LNN_ENVIRONMENT", os.environ.get("ENVIRONMENT", "development")).lower()
_DOCS_DISABLED = _LNN_ENV == "production"

app = FastAPI(
    title="灵境制造 API",
    version="2.8.0",
    description="Lingjing Manufacturing - NC Machining AI Platform",
    docs_url=None if _DOCS_DISABLED else "/api/docs",
    redoc_url=None if _DOCS_DISABLED else "/api/redoc",
    openapi_url=None if _DOCS_DISABLED else "/api/openapi.json",
)

shutdown_handler = GracefulShutdownHandler(app=app, state_file_path=STATE_FILE_PATH)

# Ensure state file directory exists
Path(STATE_FILE_PATH).parent.mkdir(parents=True, exist_ok=True)

# IdleAutoShutdownMiddleware configuration
# P1-1 修复：通过 LNN_IDLE_AUTO_SHUTDOWN 环境变量控制是否启用空闲自动关机。
# - 桌面 sidecar 模式（sidecar_main.py 启动）：默认禁用（"false"），用户随时回来使用
# - Docker / 独立服务模式：默认启用（"true"），节省云端资源
# 默认值：未设置环境变量时启用（保持向后兼容）
_IDLE_AUTO_SHUTDOWN_ENABLED = os.environ.get("LNN_IDLE_AUTO_SHUTDOWN", "true").lower() == "true"
IDLE_TIMEOUT_SECONDS = 1800


# 生命周期事件
@app.on_event("startup")
async def startup_event():
    # CORS 安全配置验证：通配符 * 与 allow_credentials=True 同时使用属于
    # 严重安全风险，必须在进程绑定端口之前完成强制校验。校验失败时
    # 输出 ERROR 日志并以非零退出码终止启动流程，绝不允许带病上线。
    try:
        enforce_startup_security()
        logger.info(
            "CORS 配置安全验证通过: allow_origins=%s, env=%s",
            cors_settings.get_origins(),
            cors_settings._env,
        )
    except CorsConfigError as e:
        # CorsConfigError 自身已经写过 ERROR 日志（包含中文告警），这里
        # 再补一条更具体的启动上下文，然后强制以非零退出码终止进程。
        logger.error("CORS 启动安全校验失败，进程即将退出: %s", e)
        # 在 FastAPI 启动事件中 raise 会让 uvicorn 报告并以非零退出码
        # 终止；这里额外 sys.exit 用来保证独立运行（python -m app.main）
        # 时也立即退出。
        sys.exit(1)

    shutdown_handler.setup()

    # 修复：ring_log.start() 失败不应阻塞整个启动。
    # 原实现未捕获，若 ring_log 内部创建后台任务失败（如事件循环已关闭），
    # 会导致 startup_event 直接 raise，FastAPI 不会触发 shutdown_event，
    # 已注册的 shutdown_handler 信号处理器等资源无法清理。
    try:
        await ring_log.start()
    except Exception as ring_log_err:
        logger.error("ring_log.start() 失败，环形日志将不可用: %s", ring_log_err, exc_info=True)

    # 权限检查机制状态检查
    if not config.security.permission_enforced:
        logger.warning("权限检查机制已被关闭，这可能导致安全风险")

    from app.database.models import init_db
    from app.tasks.task_system import AsyncTaskManager
    from app.dependencies import get_redis

    # 确保默认 SQLite 数据库目录存在
    # DB_URL 环境变量不再由 main.py 设置，统一由 config.database.db_url 管理
    _db_url = config.database.db_url
    if _db_url.startswith("sqlite"):
        _db_file = _db_url.split("///", 1)[-1]
        Path(_db_file).parent.mkdir(parents=True, exist_ok=True)

    # Initialize async DB tables + seed RBAC
    # 修复：init_db 失败（非 "already exists" 的 OperationalError，如磁盘满、
    # 权限不足、schema 损坏）原实现会让 uvicorn 以非零码退出，但此时
    # ring_log 已启动，FastAPI startup 失败不会触发 shutdown_event，
    # 导致 ring_log 文件句柄泄漏（Windows 下可能锁定日志文件）。
    # 现改为 try/except 记录错误但不阻塞启动，让 verify_critical_dependencies
    # 后续报告 DB 不可用，由运维决定是否需要重启。
    logger.info("[startup] Calling init_db() ...")
    try:
        await init_db()
    except Exception as db_init_err:
        logger.error(
            "[startup] init_db() 失败，数据库相关功能将不可用: %s",
            db_init_err,
            exc_info=True,
        )
    logger.info("[startup] init_db() step done")

    # --- Step 2b: Alembic 迁移（失败不阻断启动，仅告警）---
    # P0-3 修复：在 init_db 后执行 alembic upgrade head，保证 schema 版本一致
    # 实现已迁移至 ``app.startup_hooks.run_alembic_upgrade``，便于独立测试
    await run_alembic_upgrade(logger)

    # Redis (optional, returns None if not configured)
    # Redis 已在 get_redis() 内部对 ConnectionError/TimeoutError 做降级
    # 到内存缓存，但为防御 ImportError 等未预期异常，外层再加 try/except。
    logger.info("[startup] Calling get_redis() ...")
    try:
        await get_redis()
    except Exception as redis_err:
        logger.error(
            "[startup] get_redis() 失败，将使用内存缓存降级: %s",
            redis_err,
            exc_info=True,
        )
    logger.info("[startup] get_redis() step done")

    # --- Step 3b: 关键依赖连通性自检（P1-15 修复）---
    # 启动后立即验证 DB / Redis 可达性，失败仅 warning 不阻断启动
    # （保持与 Alembic 迁移相同的容错策略：桌面开发模式可能未配置全部依赖）。
    # 避免应用以"僵尸态"启动——进程存活但所有请求因依赖不可达而 500。
    # 实现已迁移至 ``app.startup_hooks.verify_critical_dependencies``，便于独立测试
    await verify_critical_dependencies(logger)

    # Task manager
    # 修复：AsyncTaskManager.initialize 失败原实现未捕获，若内部创建线程池
    # 或注册定时器失败会导致 startup 直接 raise。现改为 try/except 让
    # 应用以降级模式启动（无后台任务执行能力但 API 仍可响应）。
    logger.info("[startup] Initializing AsyncTaskManager ...")
    try:
        task_mgr = AsyncTaskManager()
        await task_mgr.initialize(max_concurrent=config.tasks.max_concurrent)
    except Exception as task_mgr_err:
        logger.error(
            "[startup] AsyncTaskManager 初始化失败，后台任务功能将不可用: %s",
            task_mgr_err,
            exc_info=True,
        )
    logger.info("[startup] AsyncTaskManager step done")

    # 插件系统接线（P4 完整接线第一步）
    # init_plugin_system() 曾长期无调用点（会导致
    # get_plugin_manager() 抛 RuntimeError、插件 API 永远返回空。
    # 无参初始化：plugin_dirs 为空 发现 0 个插件（不触发 torch 依赖插件），
    # 但管理器被初始化，插件 API 返回空而非异常，前端插件页不再死数据。
    # 后续接入业务插件时再配置 plugin_dirs（见 init_plugin_system 参数）。
    # 失败仅告警不阻断启动（与现有容错策略一致）。
    logger.info("[startup] Initializing plugin system ...")
    try:
        from app.plugins.plugin_manager import init_plugin_system as _init_plugin_system

        _plugin_manager = _init_plugin_system()
        logger.info("[startup] Plugin system initialized")
    except Exception as plugin_err:
        logger.error(
            "[startup] 插件系统初始化失败，插件功能将不可用: %s",
            plugin_err,
            exc_info=True,
        )
    logger.info("[startup] Plugin system step done")

    # 自进化 M1：演化任务类型注册（workflow 模板 evolution_loop 依赖；
    # 失败仅告警——演化是旁路增强，不影响主业务）。
    logger.info("[startup] Registering evolution task handler ...")
    try:
        from app.evolution.task_handler import register_evolution_task_handler
        from app.evolution.workflow_adapter import EvolutionLoopHandler

        if not register_evolution_task_handler(EvolutionLoopHandler()):
            logger.warning("[startup] evolution_loop 任务类型注册未成功，演化 workflow 模板不可用")
    except Exception as evo_err:
        logger.error("[startup] 演化任务处理器注册失败（演化 workflow 模板不可用）: %s", evo_err, exc_info=True)
    logger.info("[startup] Evolution task handler step done")

    # 自进化 M1：心跳调度器启动（env LNN_HEARTBEAT_ENABLED，默认关；
    # 触发回调按 task_type 分发：演化任务走演化执行，其余回落既有引擎）。
    # 与 shutdown_event 的 get_scheduler().stop() 对称。
    if os.getenv("LNN_HEARTBEAT_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on"):
        logger.info("[startup] Starting HeartbeatScheduler ...")
        try:
            from app.dependencies import get_scheduler
            from app.evolution.task_handler import (
                heartbeat_trigger_callback,
                register_evolution_cron_task,
            )

            scheduler = get_scheduler()
            scheduler.set_task_trigger_callback(heartbeat_trigger_callback)
            await scheduler.start()
            register_evolution_cron_task()
            logger.info("[startup] HeartbeatScheduler started (interval=%ss)", scheduler.heartbeat_interval)
        except Exception as hb_err:
            logger.error(
                "[startup] HeartbeatScheduler 启动失败（心跳调度与演化定时不可用）: %s",
                hb_err,
                exc_info=True,
            )
    else:
        logger.info("[startup] HeartbeatScheduler 未启用（LNN_HEARTBEAT_ENABLED != 1）")

    # Agent 状态持久化（/agents 端点依赖）
    # 修复：set_persistence_manager 此前全仓库无调用点，导致 agent_state API
    # 一律返回 503 "State persistence not initialized"。现于启动时创建
    # StatePersistenceManager 并注入；失败仅告警（不影响核心图纸/工艺/NC 链路）。
    logger.info("[startup] Initializing agent state persistence ...")
    try:
        from app.state.recovery import create_state_persistence as _create_state_persistence
        from app.api.v1.agent_state import set_persistence_manager as _set_persistence_manager

        _persistence = await _create_state_persistence(
            db_url=config.database.db_url,
        )
        _set_persistence_manager(_persistence)
        logger.info("[startup] Agent state persistence initialized")
    except Exception as state_err:
        logger.error(
            "[startup] Agent 状态持久化初始化失败，/agents 端点将返回 503: %s",
            state_err,
            exc_info=True,
        )
    logger.info("[startup] Agent state persistence step done")

    ring_log.append(
        "system_event",
        level="INFO",
        source="startup",
        message="Application started",
        data={"version": PY_VERSION},
    )
    logger.info("Graceful shutdown handler and signal processors registered")
    logger.info("Idle auto-shutdown middleware registered (timeout: %ds)", IDLE_TIMEOUT_SECONDS)
    logger.info("State file path: %s", STATE_FILE_PATH)


@app.on_event("shutdown")
async def shutdown_event():
    ring_log.append(
        "system_event",
        level="INFO",
        source="shutdown",
        message="Application shutting down",
    )
    await ring_log.stop()
    await sse_manager.shutdown()

    from app.tasks.task_system import AsyncTaskManager
    from app.database.connection import close_db
    from app.services.redis_client import close_redis
    from app.ai.llm_client import close_shared_http_client
    from app.core.logging_config import shutdown_logging

    # 关闭顺序设计（P2-3 优化）
    # 关闭顺序遵循"调度层先停 执行层停 业务模块停 基础设施停"原则：
    # 1) HeartbeatScheduler（调度层）：停止提交新任务到 AsyncTaskManager
    # 2) AsyncTaskManager（执行层）：取消已运行任务，拒绝新任务
    # 3) 业务模块（Budget/Cost/Rule/Goal/Audit）：归还 SQLite 连接
    # 4) Redis / HTTP Client（外部依赖）：关闭网络连接
    # 5) DB / VectorStore（持久化层）：关闭文件句柄
    # 6) Logging（最底层）：最后关闭
    # 这样可避免调度器在执行层关闭后仍提交新任务（虽 P2-2 已加 _shutdown
    # 标志位保护，但语义上仍应调度层先停）。

    # 1) HeartbeatScheduler：取消心跳 asyncio.Task 并关闭 WakeupQueue 连接
    try:
        from app.dependencies import get_scheduler

        await get_scheduler().stop()
    except (OSError, RuntimeError, ValueError, AttributeError, ImportError, TypeError) as e:
        logger.warning("HeartbeatScheduler stop failed during shutdown: %s", e)

    # 2) AsyncTaskManager：取消所有运行中任务，清空订阅者与 cancel events
    task_mgr = AsyncTaskManager()
    await task_mgr.shutdown()

    # 3) 业务模块：归还 SQLite 连接池连接，避免连接泄漏与 Windows 文件句柄锁定。
    # 各模块独立 try/except，避免一处失败影响其他资源的释放。
    try:
        from app.dependencies import get_budget_manager

        get_budget_manager().close()
    except (OSError, RuntimeError, ValueError, AttributeError, ImportError, TypeError) as e:
        logger.warning("BudgetManager close failed during shutdown: %s", e)

    try:
        from app.dependencies import get_cost_tracker

        get_cost_tracker().close()
    except (OSError, RuntimeError, ValueError, AttributeError, ImportError, TypeError) as e:
        logger.warning("CostTracker close failed during shutdown: %s", e)

    try:
        from app.dependencies import get_rule_db

        get_rule_db().close()
    except (OSError, RuntimeError, ValueError, AttributeError, ImportError, TypeError) as e:
        logger.warning("RuleDatabase close failed during shutdown: %s", e)

    try:
        from app.dependencies import get_goal_chain_store

        get_goal_chain_store().close()
    except (OSError, RuntimeError, ValueError, AttributeError, ImportError, TypeError) as e:
        logger.warning("GoalChainStore close failed during shutdown: %s", e)

    # AgentAuditLog：关闭审计日志文件句柄
    # P2-1 修复：``app.auth.audit`` 已改为 re-export shim，
    # 全进程只有 ``app.agent.middleware`` 创建一个实例，
    # ``get_agent_audit_log()`` 返回同一实例，close 一次即可。
    try:
        from app.agent.middleware import get_agent_audit_log

        get_agent_audit_log().close()
    except (OSError, ValueError, AttributeError, ImportError) as e:
        logger.warning("AgentAuditLog close failed: %s", e)

    # 4) 外部依赖：Redis / HTTP Client
    await close_redis()
    await close_shared_http_client()

    # 5) 持久化层：DB / VectorStore
    await close_db()

    # 显式关闭 ChromaDB PersistentClient，释放底层 SQLite/DuckDB 资源，
    # 避免 Windows 文件句柄锁定导致下次启动失败。
    try:
        from app.dependencies import get_vector_store

        get_vector_store().close()
    except (OSError, RuntimeError, ValueError, AttributeError, ImportError, TypeError) as e:
        # Q1 修复：收窄为可预期的关闭阶段异常。OSError 覆盖文件句柄/SQLite
        # 关闭错误；ImportError 覆盖 vector_store 模块缺失场景。
        logger.warning("VectorStore close failed during shutdown: %s", e)

    # 5.5) 插件系统：卸载全部插件（若启动时已初始化）
    try:
        from app.plugins.plugin_manager import shutdown_plugin_system

        shutdown_plugin_system()
        logger.info("Plugin system shutdown completed")
    except Exception as e:  # noqa: BLE001 - 插件关闭失败不阻断整体关闭
        logger.warning("Plugin system shutdown failed: %s", e)

    # 6) 最底层：Logging
    shutdown_logging()

    logger.info("FastAPI shutdown event completed")


# 中间件链装配（委托给 middleware_stack.py）
# 注册顺序与期望执行顺序的对应关系、CORS 与 UnifiedAuth 的位置约束等
# 关键设计决策均在 ``app.middleware_stack`` 模块顶部注释中详细说明。
register_middleware_stack(
    app,
    metrics=metrics,
    ring_log=ring_log,
    state_file_path=STATE_FILE_PATH,
    idle_auto_shutdown_enabled=_IDLE_AUTO_SHUTDOWN_ENABLED,
    idle_timeout_seconds=IDLE_TIMEOUT_SECONDS,
)


# 路由注册（委托给 router_registry.py）
# 所有 60+ 个 include_router 调用集中在此处，按域分组（LNN / RAG / 模拟 /
# 制造 / CAM / DNC / MES / 插件 / ADR 阶段 1-8）。条件路由（依赖可选库）
# 最后注册，失败仅告警不阻断启动。
register_routers(app)

# 异常处理器注册
register_exception_handlers(app)

logger.info("Application initialized with %d routes", len(app.routes))


if __name__ == "__main__":
    # P2-2 修复：reload 写死改为 config.server.debug 控制，避免生产环境意外开启热重载
    uvicorn.run(
        "app.main:app",
        host=config.server.host,
        port=config.server.port,
        reload=config.server.debug,
    )
