"""统一依赖注入工厂（V3.0 架构 · 2026-08-02 扩展）。

本模块集中注册所有跨模块共享的 FastAPI 依赖工厂，逐步取代分散在
60+ 个模块中的 ``get_xxx()`` 全局单例模式。

使用方式：
    from app.dependencies import get_db, get_redis, get_user_store

    @router.get("/users")
    async def list_users(
        db: AsyncSession = Depends(get_db),
        store: UserStore = Depends(get_user_store),
    ): ...

设计原则：
  - 所有工厂返回单例语义（进程生命周期内同一实例）
  - 不依赖 HTTP request/response（可在测试中脱离 HTTP 上下文使用）
  - 原始 getter 函数仍可用作向后兼容（逐步添加 deprecation 标记）
  - 新增依赖优先注册到此模块

迁移进度：20/50 已注册 (2026-08-02)
"""

from __future__ import annotations

import threading
from typing import Generic, TypeVar
from collections.abc import Callable

T = TypeVar("T")


# 工具函数：懒加载单例包装器


class _LazySingleton(Generic[T]):
    """线程安全的懒加载单例包装器。

    用于包装原始 ``get_xxx()`` 函数，确保：
      - 首次调用时初始化
      - 后续调用返回缓存实例
      - 支持 ``reset()`` 用于测试隔离
    """

    __slots__ = ("_factory", "_instance", "_lock", "_initialized")

    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._instance: T | None = None
        self._lock = threading.Lock()
        self._initialized = False

    def get(self) -> T:
        if self._initialized:
            assert self._instance is not None  # 已初始化则实例必存在
            return self._instance
        with self._lock:
            if not self._initialized:
                self._instance = self._factory()
                self._initialized = True
            assert self._instance is not None  # 初始化完成后实例必存在
            return self._instance

    def reset(self) -> None:
        """重置缓存实例（用于测试 teardown）。"""
        with self._lock:
            self._instance = None
            self._initialized = False


# 数据库


def get_db():
    """数据库会话（FastAPI yield-style 依赖）。

    委托 ``app.database.connection.get_db``。
    """
    from app.database.connection import get_db as _impl

    return _impl()


def get_db_engine():
    """数据库引擎（可能为 None）。"""
    from app.database.connection import get_db_engine as _impl

    return _impl()


def get_db_sessionmaker():
    """数据库 sessionmaker（可能为 None）。"""
    from app.database.connection import get_db_sessionmaker as _impl

    return _impl()


# 缓存与消息队列

_get_redis = _LazySingleton(lambda: _import_redis())


def _import_redis():
    from app.services.redis_client import get_redis

    return get_redis()


def get_redis():
    """Redis 客户端单例。"""
    return _get_redis.get()


def get_ring_log_buffer():
    """环形日志缓冲区单例。"""
    from app.utils.ring_buffer import get_ring_log_buffer as _impl

    return _impl()


# 时序数据库

_get_tdengine = _LazySingleton(lambda: _import_tdengine())


def _import_tdengine():
    from app.services.tdengine_client import get_tdengine

    return get_tdengine()


def get_tdengine():
    """TDengine 客户端单例（同步）。"""
    return _get_tdengine.get()


async def get_tdengine_async():
    """TDengine 客户端单例（异步）。"""
    from app.services.tdengine_client import get_tdengine_async as _impl

    return await _impl()


# 用户与认证

from app.models.user import get_user_store as _get_user_store_raw, UserStore


def get_user_store() -> UserStore:
    """用户存储单例。"""
    return _get_user_store_raw()


def get_token_ban_list():
    """Token 黑名单单例。"""
    from app.auth.security import get_token_ban_list as _impl

    return _impl()


# 预算与审批


def get_budget_enforcer():
    """预算执行器单例。"""
    from app.budget.enforcer import get_budget_enforcer as _impl

    return _impl()


def get_cost_optimizer():
    """成本优化器单例。"""
    from app.budget.cost_optimizer import get_cost_optimizer as _impl

    return _impl()


def get_cost_tracker():
    """成本追踪器单例。"""
    from app.budget.cost_tracker import get_cost_tracker as _impl

    return _impl()


def get_budget_manager():
    """预算管理器单例。"""
    from app.budget.budget import get_budget_manager as _impl

    return _impl()


def get_approval_engine():
    """审批引擎单例。"""
    from app.budget.approval_workflow import get_approval_engine as _impl

    return _impl()


# AI / LLM


def get_llm_registry():
    """LLM Provider 注册表单例。"""
    from app.ai.llm.provider_registry import get_registry as _impl

    return _impl()


def get_llm_router():
    """LLM Provider 路由器单例。"""
    from app.ai.llm.router import get_router as _impl

    return _impl()


async def get_llm_client():
    """LLM 客户端单例（异步）。"""
    from app.ai.llm_client import get_llm_client as _impl

    return await _impl()


async def get_shared_http_client():
    """共享 HTTP 客户端（异步）。"""
    from app.ai.llm_client import get_shared_http_client as _impl

    return await _impl()


def get_process_understanding_engine():
    """工艺理解引擎单例。"""
    from app.ai.process_understanding.engine import (
        get_process_understanding_engine as _impl,
    )

    return _impl()


# Agent 编排


def get_orchestrator():
    """Agent 编排器单例。"""
    from app.agent.orchestrator import get_orchestrator as _impl

    return _impl()


def get_sovereignty_policy():
    """AI 主权/自主等级策略引擎单例（W9.1）。"""
    from app.services.sovereignty import get_sovereignty_policy as _impl

    return _impl()


def get_state_persistence_manager():
    """状态持久化管理器单例。"""
    raise NotImplementedError(
        "StatePersistenceManager 需要数据库连接参数，请使用 app.state.manager.StatePersistenceManager(...) 直接构造"
    )


# 插件系统


def get_plugin_manager():
    """插件管理器单例。"""
    from app.plugins.plugin_manager import get_plugin_manager as _impl

    return _impl()


def get_skill_marketplace():
    """Skill 市场单例。"""
    from app.plugins.skill_marketplace import get_marketplace as _impl

    return _impl()


# RAG / 知识库


def get_vector_store():
    """向量存储单例。"""
    from app.rag.vector_store import get_vector_store as _impl

    return _impl()


def get_knowledge_base():
    """知识库单例。"""
    from app.rag.knowledge_base import get_knowledge_base as _impl

    return _impl()


def get_embedding_service():
    """嵌入服务单例。"""
    from app.rag.embeddings import get_embedding_service as _impl

    return _impl()


# 业务服务


def get_rule_db():
    """规则数据库单例。"""
    from app.database.rule_db import get_rule_db as _impl

    return _impl()


def get_model_registry_service():
    """模型注册服务单例。"""
    from app.services.model_registry_service import (
        get_model_registry_service as _impl,
    )

    return _impl()


def get_task_checkout_manager():
    """任务签出管理器单例。"""
    from app.tasks.task_checkout import get_checkout_manager as _impl

    return _impl()


# 飞轮 / 指标


def get_flywheel_metrics():
    """飞轮指标收集器单例。"""
    from app.metrics.flywheel_metrics import get_flywheel_collector as _impl

    return _impl()


# 配置


def get_config():
    """全局应用配置单例。"""
    from app.config import config as _cfg

    return _cfg


# 测试辅助


def reset_all_singletons() -> None:
    """重置所有懒加载单例缓存（仅供测试使用）。"""
    global _get_redis, _get_tdengine
    _get_redis.reset()
    _get_tdengine.reset()


# 业务服务（第二批 · 2026-08-03）


def get_rl_agent_service():
    """RL Agent 服务单例。"""
    from app.services.rl_agent_service import get_rl_agent_service as _impl

    return _impl()


def get_resource_card_service():
    """资源卡片服务单例。"""
    from app.services.resource_card_service import get_resource_card_service as _impl

    return _impl()


def get_project_package_service():
    """项目打包服务单例。"""
    from app.services.project_package_service import get_project_package_service as _impl

    return _impl()


def get_world_model_service():
    """世界模型服务单例。"""
    from app.services.world_model_service import get_world_model_service as _impl

    return _impl()


def get_workflow_template_service():
    """工作流模板服务单例。"""
    from app.services.workflow_template_service import get_workflow_template_service as _impl

    return _impl()


def get_explainability_service():
    """可解释性服务单例。"""
    from app.services.explainability.service import get_explainability_service as _impl

    return _impl()


def get_project_sync_service():
    """项目同步服务单例。"""
    from app.services.project_sync_service.service import get_project_sync_service as _impl

    return _impl()


def get_memory_cache():
    """内存缓存单例。"""
    from app.services.memory_cache import get_memory_cache as _impl

    return _impl()


def get_dataset_store():
    """数据集存储单例。"""
    from app.data.dataset_store import get_dataset_store as _impl

    return _impl()


def get_goal_chain_store():
    """目标链存储单例。"""
    from app.goals.goal_chain_store import get_goal_chain_store as _impl

    return _impl()


def get_risk_identifier():
    """风险识别器单例。"""
    from app.risk.risk_identifier import get_risk_identifier as _impl

    return _impl()


def get_scheduler():
    """定时任务调度器单例。"""
    from app.heartbeat.heartbeat import get_scheduler as _impl

    return _impl()


# Repository 层（V3.0）


def get_agent_state_repo():
    from app.infrastructure.repositories.agent_state_repo import get_agent_state_repo as _impl

    return _impl()


def get_notification_repo():
    from app.infrastructure.repositories.notification_repo import get_notification_repo as _impl

    return _impl()


def get_system_repo():
    from app.infrastructure.repositories.system_repo import get_system_repo as _impl

    return _impl()


# 迁移进度跟踪
#  已注册 (~41): get_db, get_db_engine, get_db_sessionmaker, get_redis,
# get_ring_log_buffer, get_tdengine, get_tdengine_async, get_user_store,
# get_token_ban_list, get_budget_enforcer, get_cost_optimizer,
# get_cost_tracker, get_budget_manager, get_approval_engine,
# get_llm_registry, get_llm_router, get_llm_client, get_shared_http_client,
# get_process_understanding_engine, get_orchestrator, get_plugin_manager,
# get_skill_marketplace, get_vector_store, get_knowledge_base,
# get_embedding_service, get_rule_db, get_model_registry_service,
# get_task_checkout_manager, get_flywheel_metrics, get_config,
# get_rl_agent_service, get_resource_card_service,
# get_project_package_service, get_world_model_service,
# get_workflow_template_service, get_explainability_service,
# get_project_sync_service, get_memory_cache, get_dataset_store,
# get_goal_chain_store, get_risk_identifier, get_scheduler
#
#  迁移指南中但实际无对应 get_xxx 的模块：
# stock_model (类构造函数), process_data_manager (类构造函数),
# experience_store, validation_calibrator, tool_wear/facade,
# rust_engine, postprocessor/registry, state/checkpoint,
# state/recovery, templates/template_*
