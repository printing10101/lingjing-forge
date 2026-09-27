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
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable

# shared imports moved to state/__init__.py
from app.models.agent_state import (
    AgentState,
    AgentStatus,
    Checkpoint,
    MemoryEntry,
    SessionContext,
    StateVersion,
)

# F821 修复：checkpoint/compressor/migration 为 V3.0 独立子模块，
# 直接子模块导入，避免经由 state/__init__.py 造成循环导入
from app.state.checkpoint import CheckpointLifecycleManager
from app.state.compressor import StateCompressor
from app.state.migration import StateMigrationEngine

HEARTBEAT_INTERVAL_SECONDS = 15 * 60
CHECKPOINT_MAX_AGE_SECONDS = 7 * 24 * 3600
CHECKPOINT_MAX_COUNT = 50
MAX_MEMORY_ENTRIES = 1000
MEMORY_PRUNING_THRESHOLD = 800
CONTEXT_COMPRESSION_THRESHOLD_BYTES = 1024 * 100
CHECKPOINT_BASE_DIR = "data/checkpoints"

logger = logging.getLogger(__name__)


class StatePersistenceManager:
    """Core persistence manager with three-tier storage architecture"""

    def __init__(
        self,
        redis_client=None,
        db_session_factory=None,
        checkpoint_base_dir: str = CHECKPOINT_BASE_DIR,
        async_engine=None,
        sync_engine=None,
    ):
        self._redis = redis_client
        self._db_session_factory = db_session_factory
        self._checkpoint_manager = CheckpointLifecycleManager(checkpoint_base_dir)
        self._compressor = StateCompressor()
        self._migration_engine = StateMigrationEngine()
        self._heartbeat_interval = HEARTBEAT_INTERVAL_SECONDS
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}
        self._save_locks: dict[str, asyncio.Lock] = {}
        self._active_states: dict[str, AgentState] = {}
        self._event_handlers: dict[str, list[Callable]] = {}
        self._running = False
        self._db_consecutive_failures: int = 0  # P0 修复: 跟踪连续数据库失败次数
        # [R-H1] 保留 engine 引用以便 shutdown 时 dispose，避免连接池泄漏
        self._async_engine = async_engine
        self._sync_engine = sync_engine

    async def shutdown(self) -> None:
        """[R-H1] 释放底层资源：dispose SQLAlchemy engine、关闭 Redis 连接。

        应在 FastAPI lifespan 的 shutdown 阶段调用。
        """
        # 停止心跳任务
        if self._heartbeat_tasks:
            for task in list(self._heartbeat_tasks.values()):
                task.cancel()
            await asyncio.gather(*self._heartbeat_tasks.values(), return_exceptions=True)
            self._heartbeat_tasks.clear()

        # dispose async engine
        if self._async_engine is not None:
            try:
                await self._async_engine.dispose()
            except Exception as e:
                logger.warning("async engine dispose 失败: %s", e)
            self._async_engine = None

        # dispose sync engine
        if self._sync_engine is not None:
            try:
                self._sync_engine.dispose()
            except Exception as e:
                logger.warning("sync engine dispose 失败: %s", e)
            self._sync_engine = None

        # 关闭 Redis
        if self._redis is not None:
            try:
                close_fn = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
                if close_fn:
                    result = close_fn()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception as e:
                logger.warning("redis client close 失败: %s", e)
            self._redis = None

        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _get_lock(self, agent_id: str) -> asyncio.Lock:
        if agent_id not in self._save_locks:
            self._save_locks[agent_id] = asyncio.Lock()
        return self._save_locks[agent_id]

    def _redis_key(self, agent_id: str) -> str:
        return f"agent_state:{agent_id}"

    async def _save_redis(self, state: AgentState):
        if not self._redis:
            return
        try:
            key = self._redis_key(state.agent_id)
            data = json.dumps(state.to_dict(), ensure_ascii=False)
            await asyncio.to_thread(self._redis.setex, key, self._heartbeat_interval * 2, data)
        except (ConnectionError, OSError, ValueError) as e:
            # Redis 缓存写入失败不影响主流程，但需记录以便排查
            logger.warning(
                "Failed to save heartbeat state to Redis: agent=%s error=%s",
                state.agent_id,
                e,
            )

    async def _load_redis(self, agent_id: str) -> AgentState | None:
        if not self._redis:
            return None
        try:
            key = self._redis_key(agent_id)
            data = await asyncio.to_thread(self._redis.get, key)
            if data:
                raw = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
                raw = self._migration_engine.migrate(raw)
                return AgentState.from_dict(raw)
        except (ConnectionError, OSError, ValueError, KeyError, TypeError) as e:
            # Redis 读取或反序列化失败时返回 None，让上层回退到 DB 层
            logger.warning("Failed to load state from Redis: agent=%s error=%s", agent_id, e)
        return None

    async def _save_db(self, state: AgentState):
        if not self._db_session_factory:
            return
        session = None
        try:
            session = await self._db_session_factory()
            json.dumps(state.to_dict(), ensure_ascii=False)
            compressed = self._compressor.should_compress(state.session_context)
            import sqlalchemy as sa

            # 方言适配（P0 修复）：PostgreSQL 用 to_timestamp()/NOW() 转换时间戳，
            # SQLite 无这两个函数，直接用 epoch 浮点值（列类型 TIMESTAMP 亦接受数值），
            # 与 _load_db 的 hasattr(row.x, "timestamp") 读取分支对称。
            is_sqlite = False
            try:
                dialect = getattr(getattr(session, "bind", None), "dialect", None)
                is_sqlite = bool(dialect is not None and dialect.name == "sqlite")
            except (AttributeError, TypeError) as e:
                # 方言探测失败时保守按非 SQLite 处理，但必须留痕——
                # 静默吞掉会导致 SQLite 走 to_timestamp()/NOW() 分支，
                # 运行时才报 SQL 错误，极难排查。
                logger.warning("Dialect detection failed; assuming non-sqlite: %s", e)
            hb_expr = ":hb" if is_sqlite else "to_timestamp(:hb)"
            now_expr = ":hb_now" if is_sqlite else "NOW()"
            hb_now = time.time()

            result = await session.execute(
                sa.text("SELECT 1 FROM agent_states WHERE agent_id = :agent_id"),
                {"agent_id": state.agent_id},
            )
            exists = result.scalar() is not None

            if exists:
                await session.execute(
                    sa.text(
                        f"""UPDATE agent_states SET
                            current_task_id = :task_id,
                            session_context = :session_ctx,
                            memory_json = :memory,
                            checkpoint_json = :checkpoint,
                            last_heartbeat = {hb_expr},
                            status = :status,
                            state_version = :sv,
                            updated_at = {now_expr},
                            metadata_json = :meta,
                            compressed = :compressed
                        WHERE agent_id = :agent_id"""
                    ),
                    {
                        "agent_id": state.agent_id,
                        "task_id": state.current_task_id,
                        "session_ctx": json.dumps(state.session_context.to_dict(), ensure_ascii=False),
                        "memory": json.dumps([m.to_dict() for m in state.memory], ensure_ascii=False),
                        "checkpoint": json.dumps(state.checkpoint.to_dict(), ensure_ascii=False)
                        if state.checkpoint
                        else None,
                        "hb": state.last_heartbeat,
                        "hb_now": hb_now,
                        "status": state.status.value,
                        "sv": json.dumps(state.state_version.to_dict(), ensure_ascii=False),
                        "meta": json.dumps(state.metadata, ensure_ascii=False),
                        "compressed": compressed,
                    },
                )
            else:
                await session.execute(
                    sa.text(
                        f"""INSERT INTO agent_states
                            (agent_id, current_task_id, session_context, memory_json, checkpoint_json,
                             last_heartbeat, status, checkpoints_history_json, state_version,
                             created_at, updated_at, metadata_json, compressed)
                        VALUES
                            (:agent_id, :task_id, :session_ctx, :memory, :checkpoint,
                             {hb_expr}, :status, :chk_hist, :sv,
                             {now_expr}, {now_expr}, :meta, :compressed)"""
                    ),
                    {
                        "agent_id": state.agent_id,
                        "task_id": state.current_task_id,
                        "session_ctx": json.dumps(state.session_context.to_dict(), ensure_ascii=False),
                        "memory": json.dumps([m.to_dict() for m in state.memory], ensure_ascii=False),
                        "checkpoint": json.dumps(state.checkpoint.to_dict(), ensure_ascii=False)
                        if state.checkpoint
                        else None,
                        "hb": state.last_heartbeat,
                        "hb_now": hb_now,
                        "status": state.status.value,
                        "chk_hist": json.dumps(
                            [c.to_dict() for c in state.checkpoints_history],
                            ensure_ascii=False,
                        ),
                        "sv": json.dumps(state.state_version.to_dict(), ensure_ascii=False),
                        "meta": json.dumps(state.metadata, ensure_ascii=False),
                        "compressed": compressed,
                    },
                )
            await session.commit()
            self._db_consecutive_failures = 0  # 成功后重置失败计数
        except (OSError, RuntimeError, ValueError, KeyError) as e:
            # 数据库保存失败需要记录详细错误以便排查
            self._db_consecutive_failures += 1
            if session is not None:
                try:
                    await session.rollback()
                except Exception as rollback_err:
                    logger.error(
                        "DB rollback failed for agent %s (consecutive failures=%d): %s",
                        state.agent_id,
                        self._db_consecutive_failures,
                        rollback_err,
                        exc_info=True,
                    )
            logger.error(
                "DB save failed for agent %s (consecutive=%d): %s",
                state.agent_id,
                self._db_consecutive_failures,
                e,
                exc_info=True,
            )
        except Exception as e:
            # 捕获 SQLAlchemyError 及其他数据库异常，显式 rollback
            # 注意：保留 Exception 兜底，因 try 块内 import sqlalchemy 可能抛 ImportError，
            # sa.text() 可能抛 sa.exc.SQLAlchemyError，session.execute/commit 可能抛多种 DB 异常。
            # 此处作为最外层兜底，保证 rollback 一定执行。
            self._db_consecutive_failures += 1
            if session is not None:
                try:
                    await session.rollback()
                except Exception as rollback_err:
                    logger.error(
                        "DB rollback failed for agent %s (consecutive failures=%d): %s",
                        state.agent_id,
                        self._db_consecutive_failures,
                        rollback_err,
                        exc_info=True,
                    )
            logger.error(
                "DB save failed for agent %s (consecutive=%d): %s",
                state.agent_id,
                self._db_consecutive_failures,
                e,
                exc_info=True,
            )
        finally:
            if session is not None:
                try:
                    await session.close()
                except Exception as close_err:
                    self._db_consecutive_failures += 1
                    logger.error(
                        "DB session close failed for agent %s (consecutive failures=%d): %s",
                        state.agent_id,
                        self._db_consecutive_failures,
                        close_err,
                        exc_info=True,
                    )
        # 连续失败超过阈值时发出健康告警
        if self._db_consecutive_failures > 10:
            logger.critical(
                "StatePersistenceManager: %d consecutive DB failures — possible outage",
                self._db_consecutive_failures,
            )

    async def _load_db(self, agent_id: str) -> AgentState | None:
        if not self._db_session_factory:
            return None
        # P0-3 修复：session 必须在 finally 块中关闭，防止异常路径下 session 泄漏
        # 导致连接池耗尽。原代码仅在成功路径调用 session.close()，一旦 execute/fetch
        # 抛出异常，session 将泄漏至 GC 回收前。
        session = None
        try:
            session = await self._db_session_factory()
            import sqlalchemy as sa

            result = await session.execute(
                sa.text(
                    """SELECT
                        agent_id, current_task_id, session_context, memory_json,
                        checkpoint_json, last_heartbeat, status, checkpoints_history_json,
                        state_version, created_at, updated_at, metadata_json
                    FROM agent_states WHERE agent_id = :agent_id"""
                ),
                {"agent_id": agent_id},
            )
            row = result.fetchone()
            if not row:
                return None

            session_ctx = json.loads(row.session_context) if row.session_context else {}
            memory = json.loads(row.memory_json) if row.memory_json else []
            checkpoint = json.loads(row.checkpoint_json) if row.checkpoint_json else None
            chk_hist = json.loads(row.checkpoints_history_json) if row.checkpoints_history_json else []
            state_ver = json.loads(row.state_version) if row.state_version else {}
            metadata = json.loads(row.metadata_json) if row.metadata_json else {}

            state = AgentState(
                agent_id=row.agent_id,
                current_task_id=row.current_task_id,
                session_context=SessionContext.from_dict(session_ctx),
                memory=[MemoryEntry.from_dict(m) for m in memory],
                checkpoint=Checkpoint.from_dict(checkpoint) if checkpoint else None,
                last_heartbeat=float(row.last_heartbeat.timestamp())
                if hasattr(row.last_heartbeat, "timestamp")
                else row.last_heartbeat,
                status=AgentStatus(row.status),
                checkpoints_history=[Checkpoint.from_dict(c) for c in chk_hist],
                state_version=StateVersion.from_dict(state_ver),
                created_at=float(row.created_at.timestamp())
                if hasattr(row.created_at, "timestamp")
                else row.created_at,
                updated_at=float(row.updated_at.timestamp())
                if hasattr(row.updated_at, "timestamp")
                else row.updated_at,
                metadata=metadata,
            )
            return state
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as e:
            # 数据库加载失败时返回 None，让上层回退到其他存储层
            logger.error("DB load failed for agent %s: %s", agent_id, e, exc_info=True)
        finally:
            if session is not None:
                try:
                    await session.close()
                except Exception as close_err:
                    logger.warning("Failed to close DB session in _load_db: %s", close_err)
        return None

    async def _save_checkpoint_files(self, state: AgentState):
        if not state.checkpoint:
            return
        agent_id = state.agent_id
        ckpt = state.checkpoint
        if ckpt.state_dict_path and os.path.exists(ckpt.state_dict_path):
            data = Path(ckpt.state_dict_path).read_bytes()
            path = self._checkpoint_manager.save_checkpoint_file(agent_id, ckpt.checkpoint_id, data)
            ckpt.file_size_bytes = os.path.getsize(path)

    async def _load_checkpoint_files(self, state: AgentState):
        if not state.checkpoint:
            return
        agent_id = state.agent_id
        ckpt = state.checkpoint
        data = self._checkpoint_manager.load_checkpoint_file(agent_id, ckpt.checkpoint_id)
        if data:
            temp_path = self._checkpoint_manager.get_checkpoint_path(agent_id, ckpt.checkpoint_id)
            ckpt.state_dict_path = str(temp_path.with_suffix(".restored.pt"))
            Path(ckpt.state_dict_path).write_bytes(data)

    async def save_state(self, state: AgentState, trigger: str = "manual") -> AgentState:
        """Atomic save to all three storage tiers"""
        lock = self._get_lock(state.agent_id)
        async with lock:
            state.update_heartbeat()
            tasks = []
            tasks.append(asyncio.create_task(self._save_redis(state)))
            tasks.append(asyncio.create_task(self._save_db(state)))
            tasks.append(asyncio.create_task(self._save_checkpoint_files(state)))
            await asyncio.gather(*tasks, return_exceptions=True)
            self._active_states[state.agent_id] = state
            logger.info(
                "State saved: agent=%s trigger=%s status=%s",
                state.agent_id,
                trigger,
                state.status.value,
            )
            return state

    async def load_state(self, agent_id: str) -> AgentState | None:
        """Load state from fastest available storage tier"""
        if agent_id in self._active_states:
            return self._active_states[agent_id]
        state = await self._load_redis(agent_id)
        if state:
            self._active_states[agent_id] = state
            return state
        state = await self._load_db(agent_id)
        if state:
            self._active_states[agent_id] = state
            await self._save_redis(state)
            logger.info("State loaded from DB: agent=%s", agent_id)
            return state
        return None

    async def delete_state(self, agent_id: str):
        if self._redis:
            try:
                await asyncio.to_thread(self._redis.delete, self._redis_key(agent_id))
            except (ConnectionError, OSError, ValueError) as e:
                logger.warning("Failed to delete state from Redis: agent=%s error=%s", agent_id, e)
        if self._db_session_factory:
            # P0-3 修复：session 必须在 finally 块中关闭，防止异常路径下 session 泄漏
            session = None
            try:
                session = await self._db_session_factory()
                import sqlalchemy as sa

                await session.execute(
                    sa.text("DELETE FROM agent_states WHERE agent_id = :agent_id"),
                    {"agent_id": agent_id},
                )
                await session.commit()
            except (OSError, sa.exc.SQLAlchemyError, RuntimeError) as e:
                # 数据库删除失败不应阻塞内存状态清理
                logger.warning(
                    f"Failed to delete agent state from DB for {agent_id}: {e}",
                    exc_info=True,
                )
            finally:
                if session is not None:
                    try:
                        await session.close()
                    except Exception as close_err:
                        logger.warning(
                            "Failed to close DB session in delete_state: %s",
                            close_err,
                        )
        self._active_states.pop(agent_id, None)
        self._stop_heartbeat(agent_id)
        self._checkpoint_manager.cleanup_agent_checkpoints(agent_id, max_age_seconds=0)
        logger.info("State deleted: agent=%s", agent_id)

    async def save_checkpoint(
        self,
        agent_id: str,
        checkpoint: Checkpoint,
        trigger: str = "auto",
    ) -> AgentState:
        state = await self.load_state(agent_id)
        if not state:
            state = AgentState(agent_id=agent_id)
        state.set_checkpoint(checkpoint)
        await self.save_state(state, trigger=f"checkpoint_{trigger}")
        return state

    async def update_context_increment(
        self,
        agent_id: str,
        updates: dict[str, Any],
        trigger: str = "incremental",
    ) -> AgentState:
        state = await self.load_state(agent_id)
        if not state:
            state = AgentState(agent_id=agent_id)
        state.add_context_increment(updates)
        await self.save_state(state, trigger=f"context_{trigger}")
        return state

    async def prune_memory(self, agent_id: str) -> AgentState:
        state = await self.load_state(agent_id)
        if not state:
            return AgentState(agent_id=agent_id)
        if len(state.memory) > MAX_MEMORY_ENTRIES:
            state.memory.sort(key=lambda m: m.importance, reverse=True)
            state.memory = state.memory[:MEMORY_PRUNING_THRESHOLD]
            await self.save_state(state, trigger="memory_prune")
        return state

    async def cleanup_checkpoints(self, agent_id: str) -> int:
        return self._checkpoint_manager.cleanup_agent_checkpoints(agent_id)

    def _start_heartbeat(self, agent_id: str):
        if agent_id in self._heartbeat_tasks:
            return
        self._heartbeat_tasks[agent_id] = asyncio.create_task(self._heartbeat_loop(agent_id))

    def _stop_heartbeat(self, agent_id: str):
        task = self._heartbeat_tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()

    async def _heartbeat_loop(self, agent_id: str):
        while self._running:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                state = await self.load_state(agent_id)
                if state:
                    state.update_heartbeat()
                    await self.save_state(state, trigger="heartbeat")
            except asyncio.CancelledError:
                break
            except (OSError, RuntimeError, AttributeError, KeyError) as e:
                # 心跳循环中单次失败不应中断后续心跳，记录后继续
                logger.debug(
                    f"Heartbeat iteration failed for {agent_id}: {e}",
                    exc_info=True,
                )

    async def start_heartbeat(self, agent_id: str):
        self._start_heartbeat(agent_id)

    async def stop_heartbeat(self, agent_id: str):
        self._stop_heartbeat(agent_id)

    async def snapshot_for_rollback(self, agent_id: str) -> str | None:
        """Create a snapshot and return its version identifier"""
        state = await self.load_state(agent_id)
        if not state:
            return None
        rollback_key = f"agent_state_rollback:{agent_id}"
        if self._redis:
            try:
                data = json.dumps(state.to_dict(), ensure_ascii=False)
                await asyncio.to_thread(self._redis.setex, rollback_key, 86400 * 7, data)
            except (TypeError, ValueError, ConnectionError, OSError) as e:
                # Redis 回滚快照写入失败不应阻塞当前状态保存
                logger.warning(
                    f"Failed to write rollback snapshot to Redis for {agent_id}: {e}",
                    exc_info=True,
                )
        return rollback_key

    async def rollback_to_version(self, agent_id: str) -> AgentState | None:
        rollback_key = f"agent_state_rollback:{agent_id}"
        if not self._redis:
            return None
        try:
            data = await asyncio.to_thread(self._redis.get, rollback_key)
            if not data:
                return None
            raw = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
            raw = self._migration_engine.migrate(raw)
            state = AgentState.from_dict(raw)
            await self.save_state(state, trigger="rollback")
            return state
        except (ConnectionError, OSError, ValueError, KeyError, TypeError, UnicodeDecodeError) as e:
            # 回滚快照读取/解析失败不应抛出，返回 None 让上层处理
            logger.warning(
                "Failed to load rollback snapshot from Redis for %s: %s",
                agent_id,
                e,
                exc_info=True,
            )
            return None

    def on_event(self, event: str):
        def decorator(func: Callable):
            self._event_handlers.setdefault(event, [])
            self._event_handlers[event].append(func)
            return func

        return decorator

    async def emit_event(self, event: str, agent_id: str, data: dict | None = None):
        handlers = self._event_handlers.get(event, [])
        for handler in handlers:
            try:
                if data is not None:
                    await handler(agent_id, data)
                else:
                    await handler(agent_id)
            except (TypeError, ValueError, RuntimeError, AttributeError) as e:
                # 单个事件处理器失败不应阻塞其他处理器执行
                logger.warning(
                    f"Event handler {getattr(handler, '__name__', repr(handler))} "
                    f"failed for event {event}, agent {agent_id}: {e}",
                    exc_info=True,
                )

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False
        for agent_id, task in list(self._heartbeat_tasks.items()):
            self._stop_heartbeat(agent_id)
        for agent_id, state in list(self._active_states.items()):
            try:
                state.status = AgentStatus.STOPPED
                await self.save_state(state, trigger="pre_shutdown")
            except (OSError, RuntimeError, AttributeError) as e:
                # 关闭时单 agent 状态保存失败不应阻塞其他 agent 的关闭流程
                logger.warning(
                    f"Failed to save pre-shutdown state for {agent_id}: {e}",
                    exc_info=True,
                )
        self._active_states.clear()
        self._heartbeat_tasks.clear()

    async def list_all_agent_states(self) -> list[dict[str, Any]]:
        if not self._db_session_factory:
            return [
                {
                    "agent_id": aid,
                    "status": s.status.value,
                    "current_task_id": s.current_task_id,
                    "last_heartbeat": s.last_heartbeat,
                }
                for aid, s in self._active_states.items()
            ]
        # P0-3 修复：session 必须在 finally 块中关闭，防止异常路径下 session 泄漏
        session = None
        try:
            session = await self._db_session_factory()
            import sqlalchemy as sa

            result = await session.execute(
                sa.text(
                    """SELECT agent_id, status, current_task_id, last_heartbeat, updated_at
                    FROM agent_states ORDER BY updated_at DESC"""
                )
            )
            rows = result.fetchall()
            return [
                {
                    "agent_id": r.agent_id,
                    "status": r.status,
                    "current_task_id": r.current_task_id,
                    "last_heartbeat": r.last_heartbeat.isoformat()
                    if hasattr(r.last_heartbeat, "isoformat")
                    else r.last_heartbeat,
                    "updated_at": r.updated_at.isoformat() if hasattr(r.updated_at, "isoformat") else r.updated_at,
                }
                for r in rows
            ]
        except (OSError, RuntimeError, ValueError, KeyError) as e:
            # 数据库查询失败时回退到内存状态，保证接口可用性
            logger.warning("Failed to query agent states from DB, falling back to memory: %s", e, exc_info=True)
            return [
                {
                    "agent_id": aid,
                    "status": s.status.value,
                    "current_task_id": s.current_task_id,
                    "last_heartbeat": s.last_heartbeat,
                    "updated_at": s.updated_at,
                }
                for aid, s in self._active_states.items()
            ]
        finally:
            if session is not None:
                try:
                    await session.close()
                except Exception as close_err:
                    logger.warning(
                        "Failed to close DB session in list_all_agent_states: %s",
                        close_err,
                    )
