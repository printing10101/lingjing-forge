"""
State Persistence System - Persistent Agent State & Session Recovery

References Paperclip's Persistent Agent State design:
- Three-tier storage: Redis (cache) + PostgreSQL (metadata) + Filesystem (checkpoints)
- Dual-trigger save: timer heartbeat (15min) + event-driven (epoch complete, status change)
- Async non-blocking save operations
- State compression for large contexts
- Checkpoint lifecycle management
- Multi-agent state isolation
- Permission-controlled state operations
- Comprehensive audit logging
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any
from collections.abc import Callable

# shared imports moved to state/__init__.py
from app.state.exceptions import StatePersistenceError

# F821 修复：StatePersistenceManager 定义于 manager.py（本模块运行期依赖）
from app.state.manager import StatePersistenceManager
from app.models.agent_state import (
    AgentState,
    AgentStatus,
    Checkpoint,
)

HEARTBEAT_INTERVAL_SECONDS = 15 * 60
CHECKPOINT_MAX_AGE_SECONDS = 7 * 24 * 3600
CHECKPOINT_MAX_COUNT = 50
MAX_MEMORY_ENTRIES = 1000
MEMORY_PRUNING_THRESHOLD = 800
CONTEXT_COMPRESSION_THRESHOLD_BYTES = 1024 * 100
CHECKPOINT_BASE_DIR = "data/checkpoints"

logger = logging.getLogger(__name__)


class StateRecoveryManager:
    """Handles agent state recovery and session resumption"""

    def __init__(self, persistence: StatePersistenceManager):
        self._persistence = persistence

    async def resume_agent(
        self,
        agent_id: str,
        task_loader: Callable[[str], Any] | None = None,
        task_runner: Callable[[str, Checkpoint | None], Any] | None = None,
    ) -> dict[str, Any]:
        """
        Resume an agent from its persisted state.
        Returns a dict with recovery status and details.
        """
        result = {
            "agent_id": agent_id,
            "recovered": False,
            "action": "no_state_found",
            "state": None,
            "task": None,
            "checkpoint_used": None,
            "error": None,
        }
        try:
            state = await self._persistence.load_state(agent_id)
            if not state:
                state = AgentState(agent_id=agent_id)
                state.status = AgentStatus.IDLE
                await self._persistence.save_state(state, trigger="recovery_new")
                result["state"] = state
                return result
            state.status = AgentStatus.RECOVERING
            await self._persistence.save_state(state, trigger="recovery_start")
            if not state.current_task_id:
                state.status = AgentStatus.IDLE
                await self._persistence.save_state(state, trigger="recovery_no_task")
                result["recovered"] = True
                result["action"] = "idle_resume"
                result["state"] = state
                return result
            if not task_loader:
                state.status = AgentStatus.IDLE
                await self._persistence.save_state(state, trigger="recovery_no_loader")
                result["recovered"] = True
                result["action"] = "idle_no_loader"
                result["state"] = state
                return result
            task = (
                await task_loader(state.current_task_id)
                if asyncio.iscoroutinefunction(task_loader)
                else task_loader(state.current_task_id)
            )
            if not task:
                state.current_task_id = None
                state.status = AgentStatus.IDLE
                await self._persistence.save_state(state, trigger="recovery_task_not_found")
                result["recovered"] = True
                result["action"] = "idle_task_gone"
                result["state"] = state
                return result
            task_status = getattr(task, "status", None)
            # hasattr 不缩小 Optional 类型：先判 None，再取 value（枚举）或转 str
            if task_status is None:
                task_status_str = ""
            else:
                task_status_str = task_status.value if hasattr(task_status, "value") else str(task_status)
            result["task"] = {
                "task_id": state.current_task_id,
                "status": task_status_str,
            }
            if task_status and task_status_str in ("in_progress", "running"):
                if state.checkpoint and task_runner:
                    try:
                        (
                            await task_runner(state.current_task_id, state.checkpoint)
                            if asyncio.iscoroutinefunction(task_runner)
                            else task_runner(state.current_task_id, state.checkpoint)
                        )
                        state.status = AgentStatus.BUSY
                        await self._persistence.save_state(state, trigger="recovery_resumed")
                        result["recovered"] = True
                        result["action"] = "resumed_with_checkpoint"
                        result["checkpoint_used"] = state.checkpoint.checkpoint_id
                        result["state"] = state
                        return result
                    except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as e:
                        from app.core.safe_errors import safe_error_message

                        safe = safe_error_message(
                            e,
                            context="state_persistence.resume_with_checkpoint",
                            fallback="从检查点恢复失败",
                        )
                        result["error"] = safe["message"]
                        result["error_id"] = safe["error_id"]
                    except Exception as e:
                        # 兜底捕获用户提供的 task_runner 可能抛出的其他异常
                        from app.core.safe_errors import safe_error_message

                        safe = safe_error_message(
                            e,
                            context="state_persistence.resume_with_checkpoint",
                            fallback="从检查点恢复失败",
                        )
                        result["error"] = safe["message"]
                        result["error_id"] = safe["error_id"]
                        logger.warning("Unexpected error in task_runner during checkpoint resume: %s", e, exc_info=True)
                if task_runner:
                    try:
                        (
                            await task_runner(state.current_task_id, None)
                            if asyncio.iscoroutinefunction(task_runner)
                            else task_runner(state.current_task_id, None)
                        )
                        state.status = AgentStatus.BUSY
                        await self._persistence.save_state(state, trigger="recovery_restarted")
                        result["recovered"] = True
                        result["action"] = "restarted_without_checkpoint"
                        result["state"] = state
                        return result
                    except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as e:
                        from app.core.safe_errors import safe_error_message

                        safe = safe_error_message(
                            e,
                            context="state_persistence.restart_without_checkpoint",
                            fallback="无检查点重启失败",
                        )
                        result["error"] = safe["message"]
                        result["error_id"] = safe["error_id"]
                    except Exception as e:
                        # 兜底捕获用户提供的 task_runner 可能抛出的其他异常
                        from app.core.safe_errors import safe_error_message

                        safe = safe_error_message(
                            e,
                            context="state_persistence.restart_without_checkpoint",
                            fallback="无检查点重启失败",
                        )
                        result["error"] = safe["message"]
                        result["error_id"] = safe["error_id"]
                        logger.warning("Unexpected error in task_runner during restart: %s", e, exc_info=True)
                state.status = AgentStatus.IDLE
                await self._persistence.save_state(state, trigger="recovery_fallback_idle")
                result["recovered"] = True
                result["action"] = "fallback_idle"
                result["state"] = state
                return result
            else:
                state.status = AgentStatus.IDLE
                await self._persistence.save_state(state, trigger="recovery_task_complete")
                result["recovered"] = True
                result["action"] = "idle_task_done"
                result["state"] = state
                return result
        except (StatePersistenceError, OSError, RuntimeError, ValueError, KeyError, AttributeError) as e:
            from app.core.safe_errors import safe_error_message

            safe = safe_error_message(
                e,
                context="state_persistence.recover",
                fallback="状态恢复失败",
            )
            result["error"] = safe["message"]
            result["error_id"] = safe["error_id"]
            result["action"] = "recovery_failed"
            try:
                fallback_state = AgentState(agent_id=agent_id)
                fallback_state.status = AgentStatus.IDLE
                await self._persistence.save_state(fallback_state, trigger="recovery_fallback")
                result["state"] = fallback_state
            except (OSError, RuntimeError, AttributeError) as e:
                # 恢复回退状态失败时仅记录，仍返回已有恢复信息
                logger.warning(
                    f"Failed to write recovery fallback state for {agent_id}: {e}",
                    exc_info=True,
                )
            return result
        except Exception as e:
            # 兜底捕获其他未预料的异常
            from app.core.safe_errors import safe_error_message

            safe = safe_error_message(
                e,
                context="state_persistence.recover",
                fallback="状态恢复失败",
            )
            result["error"] = safe["message"]
            result["error_id"] = safe["error_id"]
            result["action"] = "recovery_failed"
            logger.warning("Unexpected error during state recovery: %s", e, exc_info=True)
            try:
                fallback_state = AgentState(agent_id=agent_id)
                fallback_state.status = AgentStatus.IDLE
                await self._persistence.save_state(fallback_state, trigger="recovery_fallback")
                result["state"] = fallback_state
            except (OSError, RuntimeError, AttributeError) as e:
                logger.warning(
                    f"Failed to write recovery fallback state for {agent_id}: {e}",
                    exc_info=True,
                )
            return result

    async def clone_agent_state(
        self,
        source_agent_id: str,
        target_agent_id: str,
    ) -> AgentState | None:
        source = await self._persistence.load_state(source_agent_id)
        if not source:
            return None
        clone = source.clone(target_agent_id)
        await self._persistence.save_state(clone, trigger="clone")
        return clone

    async def get_recovery_history(self, agent_id: str) -> list[dict[str, Any]]:
        state = await self._persistence.load_state(agent_id)
        if not state:
            return []
        return state.state_version.migration_history


async def create_state_persistence(
    redis_url: str | None = None,
    db_url: str | None = None,
    checkpoint_dir: str = CHECKPOINT_BASE_DIR,
) -> StatePersistenceManager:
    redis_client = None
    db_session_factory = None
    # [R-H1] 追踪 engine 引用以便传递给 StatePersistenceManager.shutdown 时 dispose
    async_engine = None
    sync_engine = None

    if redis_url:
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(redis_url, decode_responses=False)
        except ImportError:
            try:
                import redis

                redis_client = redis.from_url(redis_url, decode_responses=False)
            except (ImportError, ConnectionError, ValueError) as e:
                # Redis 可选依赖未安装或连接失败时跳过（系统可降级为仅 DB 存储）
                logger.debug(
                    f"Failed to initialize Redis client: {e}",
                    exc_info=True,
                )

    if db_url:
        try:
            from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

            async_url = db_url
            if async_url.startswith("postgresql://"):
                async_url = async_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif async_url.startswith("sqlite://"):
                async_url = async_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

            engine = create_async_engine(async_url, echo=False)
            async_engine = engine  # [R-H1] 保存引用以便 shutdown 时 dispose
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

            async def session_factory():
                return sessionmaker()

            # 方言适配：PostgreSQL 使用 JSONB/TIMESTAMPTZ/NOW()，
            # SQLite 使用 TEXT/TIMESTAMP/CURRENT_TIMESTAMP（原实现固定用
            # PG 方言 DDL，SQLite 下 CREATE TABLE 报 "near '('" 语法错误，
            # 且该异常未被 except 元组捕获导致持久化初始化整体失败）。
            if async_url.startswith("sqlite"):
                _agent_states_ddl = """CREATE TABLE IF NOT EXISTS agent_states (
                    agent_id VARCHAR(128) PRIMARY KEY,
                    current_task_id VARCHAR(128),
                    session_context TEXT DEFAULT '{}',
                    memory_json TEXT DEFAULT '[]',
                    checkpoint_json TEXT DEFAULT NULL,
                    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status VARCHAR(32) DEFAULT 'idle',
                    checkpoints_history_json TEXT DEFAULT '[]',
                    state_version TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata_json TEXT DEFAULT '{}',
                    compressed BOOLEAN DEFAULT FALSE
                )"""
            else:
                _agent_states_ddl = """CREATE TABLE IF NOT EXISTS agent_states (
                    agent_id VARCHAR(128) PRIMARY KEY,
                    current_task_id VARCHAR(128),
                    session_context JSONB DEFAULT '{}',
                    memory_json JSONB DEFAULT '[]',
                    checkpoint_json JSONB DEFAULT NULL,
                    last_heartbeat TIMESTAMPTZ DEFAULT NOW(),
                    status VARCHAR(32) DEFAULT 'idle',
                    checkpoints_history_json JSONB DEFAULT '[]',
                    state_version JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    metadata_json JSONB DEFAULT '{}',
                    compressed BOOLEAN DEFAULT FALSE
                )"""

            async with engine.begin() as conn:
                import sqlalchemy as sa

                await conn.execute(sa.text(_agent_states_ddl))
                await conn.execute(
                    sa.text("CREATE INDEX IF NOT EXISTS idx_agent_states_status ON agent_states (status)")
                )
                await conn.execute(
                    sa.text("CREATE INDEX IF NOT EXISTS idx_agent_states_heartbeat ON agent_states (last_heartbeat)")
                )
            db_session_factory = session_factory
        except (ImportError, OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as e:
            # 异步数据库初始化失败，尝试回退到同步引擎
            logger.warning("Async DB initialization failed, falling back to sync: %s", e)
            try:
                from sqlalchemy import create_engine, text

                sync_url = db_url
                if sync_url.startswith("postgresql+asyncpg://"):
                    sync_url = sync_url.replace("postgresql+asyncpg://", "postgresql://", 1)
                sync_engine = create_engine(sync_url, echo=False)
                with sync_engine.begin() as sync_conn:
                    sync_conn.execute(
                        text(
                            """CREATE TABLE IF NOT EXISTS agent_states (
                                agent_id VARCHAR(128) PRIMARY KEY,
                                current_task_id VARCHAR(128),
                                session_context TEXT DEFAULT '{}',
                                memory_json TEXT DEFAULT '[]',
                                checkpoint_json TEXT DEFAULT NULL,
                                last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                status VARCHAR(32) DEFAULT 'idle',
                                checkpoints_history_json TEXT DEFAULT '[]',
                                state_version TEXT DEFAULT '{}',
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                metadata_json TEXT DEFAULT '{}',
                                compressed BOOLEAN DEFAULT FALSE
                            )"""
                        )
                    )
                from sqlalchemy.orm import Session

                def db_session_factory():
                    return Session(sync_engine)
            except (ImportError, OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as e2:
                # 同步回退也失败，禁用数据库持久化
                logger.error("Sync DB fallback also failed: %s", e2, exc_info=True)
                db_session_factory = None

    return StatePersistenceManager(
        redis_client=redis_client,
        db_session_factory=db_session_factory,
        checkpoint_base_dir=checkpoint_dir,
        async_engine=async_engine,
        sync_engine=sync_engine,
    )
