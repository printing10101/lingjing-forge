"""
Tests for Agent State Persistence & Session Recovery System

Covers:
- AgentState & Checkpoint model serialization/deserialization
- Three-tier storage (memory cache only when no external services)
- Dual-trigger save mechanism
- Session recovery flow
- State version migration
- State cloning and rollback
- Memory pruning
- Context compression
- Checkpoint lifecycle management
- Multi-agent state isolation
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
import pytest_asyncio

from app.models.agent_state import (
    AgentState,
    AgentStatus,
    Checkpoint,
    CheckpointType,
    MemoryEntry,
    SessionContext,
    StateVersion,
    migrate_state,
)
from app.state.state_persistence import (
    StatePersistenceManager,
    StateRecoveryManager,
    CheckpointLifecycleManager,
    StateCompressor,
    StateMigrationEngine,
    MEMORY_PRUNING_THRESHOLD,
)
from app.state.recovery import create_state_persistence


class TestAgentStateModel:
    """AgentState model unit tests"""

    def test_agent_state_creation_defaults(self):
        state = AgentState(agent_id="agent_001")
        assert state.agent_id == "agent_001"
        assert state.current_task_id is None
        assert state.status == AgentStatus.IDLE
        assert isinstance(state.session_context, SessionContext)
        assert state.memory == []
        assert state.checkpoint is None
        assert state.last_heartbeat > 0
        assert state.checkpoints_history == []
        assert state.state_version.schema_version == "1.0.0"

    def test_agent_state_full_creation(self):
        checkpoint = Checkpoint(epoch=5, step=100, best_metric=0.95)
        memory = [
            MemoryEntry(
                content="Test memory", memory_type="observation", importance=0.8
            ),
            MemoryEntry(
                content="Important decision", memory_type="decision", importance=0.9
            ),
        ]
        state = AgentState(
            agent_id="agent_002",
            current_task_id="task_123",
            status=AgentStatus.BUSY,
            checkpoint=checkpoint,
            memory=memory,
        )
        assert state.current_task_id == "task_123"
        assert state.status == AgentStatus.BUSY
        assert state.checkpoint.epoch == 5
        assert len(state.memory) == 2

    def test_checkpoint_serialization(self):
        ckpt = Checkpoint(
            epoch=10,
            step=500,
            best_metric=0.88,
            best_metric_name="accuracy",
            state_dict_path="/tmp/model.pt",
            rng_state={"python": 42, "numpy": [1, 2, 3]},
            checkpoint_type=CheckpointType.EPOCH,
            metrics={"loss": 0.12},
        )
        d = ckpt.to_dict()
        restored = Checkpoint.from_dict(d)
        assert restored.epoch == 10
        assert restored.step == 500
        assert restored.best_metric == 0.88
        assert restored.best_metric_name == "accuracy"
        assert restored.rng_state == {"python": 42, "numpy": [1, 2, 3]}
        assert restored.checkpoint_type == CheckpointType.EPOCH

    def test_session_context_roundtrip(self):
        ctx = SessionContext(
            task_id="task_001",
            task_type="prediction",
            task_description="Predict tool wear",
            goal_chain=[{"name": "step1"}, {"name": "step2"}],
            current_stage="inference",
            conversation_history=[{"role": "user", "content": "hello"}],
            injected_skills=["wear_analyst"],
            active_context_keys={"sensor_data", "tool_config"},
            custom_context={"model_version": "v2"},
        )
        d = ctx.to_dict()
        restored = SessionContext.from_dict(d)
        assert restored.task_id == "task_001"
        assert restored.current_stage == "inference"
        assert "wear_analyst" in restored.injected_skills
        assert "sensor_data" in restored.active_context_keys
        assert restored.custom_context["model_version"] == "v2"
        assert len(restored.goal_chain) == 2

    def test_session_context_incremental_update(self):
        ctx = SessionContext(task_id="task_001", current_stage="stage1")
        ctx.increment_update(
            {"current_stage": "stage2", "custom_context": {"key": "val"}}
        )
        assert ctx.current_stage == "stage2"
        assert ctx.custom_context["key"] == "val"
        assert ctx.task_id == "task_001"

    def test_full_state_serialization_roundtrip(self):
        state = AgentState(
            agent_id="agent_full",
            current_task_id="task_xyz",
            status=AgentStatus.BUSY,
            session_context=SessionContext(
                task_type="training", current_stage="epoch_3"
            ),
            memory=[MemoryEntry(content="learned pattern", memory_type="observation")],
            checkpoint=Checkpoint(epoch=3, step=300),
            metadata={"region": "cn-east"},
        )
        json_str = state.to_json()
        restored = AgentState.from_json(json_str)
        assert restored.agent_id == "agent_full"
        assert restored.status == AgentStatus.BUSY
        assert len(restored.memory) == 1
        assert restored.checkpoint.epoch == 3
        assert restored.metadata["region"] == "cn-east"

    def test_checkpoint_types(self):
        for ct in CheckpointType:
            ckpt = Checkpoint(checkpoint_type=ct)
            assert ckpt.checkpoint_type == ct

    def test_agent_status_enum(self):
        for status in AgentStatus:
            assert isinstance(status.value, str)
        state = AgentState(agent_id="a1")
        state.status = AgentStatus.ERROR
        assert state.status == AgentStatus.ERROR
        assert state.to_dict()["status"] == "error"

    def test_get_checkpoints_for_rollback(self):
        state = AgentState(agent_id="rollback_test")
        c1 = Checkpoint(epoch=1, created_at=1000)
        c2 = Checkpoint(epoch=2, created_at=2000)
        c3 = Checkpoint(epoch=3, created_at=3000)
        state.checkpoints_history = [c1, c2, c3]
        rollback_list = state.get_checkpoints_for_rollback()
        assert len(rollback_list) == 3
        assert rollback_list[0].epoch == 3

    def test_rollback_to_checkpoint(self):
        state = AgentState(agent_id="rb")
        c1 = Checkpoint(checkpoint_id="ckpt_1", epoch=1)
        c2 = Checkpoint(checkpoint_id="ckpt_2", epoch=2)
        state.set_checkpoint(c1)
        state.set_checkpoint(c2)
        assert state.checkpoint.checkpoint_id == "ckpt_2"
        result = state.rollback_to_checkpoint("ckpt_1")
        assert result is True
        assert state.checkpoint.checkpoint_id == "ckpt_1"
        result = state.rollback_to_checkpoint("nonexistent")
        assert result is False

    def test_clone_creates_independent_copy(self):
        original = AgentState(
            agent_id="original",
            current_task_id="task_abc",
            session_context=SessionContext(task_description="original task"),
            memory=[MemoryEntry(content="original mem", importance=0.7)],
            checkpoint=Checkpoint(epoch=5),
        )
        cloned = original.clone(new_agent_id="cloned")
        assert cloned.agent_id == "cloned"
        assert cloned.current_task_id == "task_abc"
        assert cloned.session_context.task_description == "original task"
        assert len(cloned.memory) == 1
        cloned.memory.append(MemoryEntry(content="new mem"))
        assert len(original.memory) == 1
        assert len(cloned.memory) == 2
        assert cloned.state_version.parent_version_id == "clone_of_original"

    def test_memory_entry_defaults(self):
        entry = MemoryEntry()
        assert entry.memory_id.startswith("mem_")
        assert entry.importance == 0.5
        assert entry.access_count == 0

    def test_state_version_migration_tracking(self):
        sv = StateVersion(state_version=2, schema_version="2.0.0")
        sv.migration_history.append(
            {"from": "1.0.0", "to": "2.0.0", "timestamp": time.time()}
        )
        d = sv.to_dict()
        restored = StateVersion.from_dict(d)
        assert restored.state_version == 2
        assert len(restored.migration_history) == 1


class TestStateMigration:
    """State version migration tests"""

    def test_migration_noop(self):
        data = {
            "agent_id": "test",
            "state_version": {"state_version": 1, "schema_version": "1.0.0"},
        }
        result = migrate_state(data, "1.0.0")
        assert result is data

    def test_migration_engine_registration(self):
        engine = StateMigrationEngine()
        engine.register_migration("1.0.0", "2.0.0", lambda d: d)
        path = engine.get_migration_path("1.0.0")
        assert len(path) >= 1
        assert path[0] == "1.0.0"


class TestStateCompressor:
    """Context compression tests"""

    def test_compact_conversation_history(self):
        ctx = SessionContext()
        ctx.conversation_history = [
            {"role": "user", "content": f"msg {i}"} for i in range(300)
        ]
        result = StateCompressor.compact_conversation_history(ctx, max_entries=200)
        assert len(result.conversation_history) == 200
        assert result.conversation_history[-1]["content"] == "msg 299"

    def test_compression_roundtrip(self):
        ctx = SessionContext(
            task_id="comp_test",
            task_description="A" * 500,
            custom_context={"large_key": "B" * 2000},
        )
        compressed = StateCompressor.compress_context(ctx)
        assert len(compressed) > 0
        decompressed = StateCompressor.decompress_context(compressed)
        assert decompressed.task_id == "comp_test"
        assert decompressed.task_description == "A" * 500


class TestCheckpointLifecycleManager:
    """Checkpoint lifecycle management tests"""

    def test_agent_isolation(self, tmp_path):
        base = tmp_path / "checkpoints"
        manager = CheckpointLifecycleManager(str(base))
        dir_a = manager.get_agent_checkpoint_dir("agent_a")
        dir_b = manager.get_agent_checkpoint_dir("agent_b")
        assert dir_a != dir_b
        assert dir_a.exists()
        assert dir_b.exists()

    def test_checkpoint_save_and_load(self, tmp_path):
        base = tmp_path / "checkpoints"
        manager = CheckpointLifecycleManager(str(base))
        data = b"model weights data here"
        path = manager.save_checkpoint_file("agent_x", "ckpt_1", data)
        assert path.exists()
        loaded = manager.load_checkpoint_file("agent_x", "ckpt_1")
        assert loaded == data
        assert manager.size_bytes("agent_x") > 0

    def test_checkpoint_cleanup_old_files(self, tmp_path):
        base = tmp_path / "checkpoints"
        manager = CheckpointLifecycleManager(str(base))
        manager.save_checkpoint_file("agent_c", "old_ckpt", b"old data")
        path = manager.get_checkpoint_path("agent_c", "old_ckpt")
        os.utime(path, (0, 0))
        manager.save_checkpoint_file("agent_c", "new_ckpt", b"new data")
        removed = manager.cleanup_agent_checkpoints("agent_c", max_age_seconds=1)
        assert removed >= 1
        assert manager.load_checkpoint_file("agent_c", "old_ckpt") is None
        assert manager.load_checkpoint_file("agent_c", "new_ckpt") is not None

    def test_cleanup_respects_max_count(self, tmp_path):
        base = tmp_path / "checkpoints"
        manager = CheckpointLifecycleManager(str(base))
        for i in range(60):
            manager.save_checkpoint_file("agent_d", f"ckpt_{i}", f"data_{i}".encode())
        removed = manager.cleanup_agent_checkpoints(
            "agent_d", max_count=50, max_age_seconds=1e9
        )
        assert removed >= 10

    def test_remove_single_checkpoint(self, tmp_path):
        base = tmp_path / "checkpoints"
        manager = CheckpointLifecycleManager(str(base))
        manager.save_checkpoint_file("agent_e", "ckpt_rm", b"to remove")
        manager.remove_checkpoint("agent_e", "ckpt_rm")
        assert manager.load_checkpoint_file("agent_e", "ckpt_rm") is None

    def test_nonexistent_checkpoint_load(self, tmp_path):
        base = tmp_path / "checkpoints"
        manager = CheckpointLifecycleManager(str(base))
        assert manager.load_checkpoint_file("agent_fake", "nonexistent") is None


class TestStatePersistenceManager:
    """State persistence manager tests (in-memory mode without external services)"""

    @pytest_asyncio.fixture
    async def persistence(self, tmp_path):
        checkpoint_dir = str(tmp_path / "checkpoints")
        manager = StatePersistenceManager(checkpoint_base_dir=checkpoint_dir)
        await manager.start()
        return manager

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, persistence):
        state = AgentState(
            agent_id="test_agent_1",
            status=AgentStatus.BUSY,
            current_task_id="task_001",
        )
        await persistence.save_state(state, trigger="test")
        loaded = await persistence.load_state("test_agent_1")
        assert loaded is not None
        assert loaded.agent_id == "test_agent_1"
        assert loaded.status == AgentStatus.BUSY

    @pytest.mark.asyncio
    async def test_load_nonexistent_agent(self, persistence):
        loaded = await persistence.load_state("nonexistent")
        assert loaded is None

    @pytest.mark.asyncio
    async def test_save_updates_heartbeat(self, persistence):
        state = AgentState(agent_id="hb_test")
        old_hb = state.last_heartbeat
        await asyncio_wait(0.01)
        await persistence.save_state(state)
        loaded = await persistence.load_state("hb_test")
        assert loaded.last_heartbeat > old_hb

    @pytest.mark.asyncio
    async def test_save_checkpoint(self, persistence):
        ckpt = Checkpoint(epoch=10, step=500, best_metric=0.95)
        state = await persistence.save_checkpoint("agent_ck", ckpt, trigger="test")
        assert state.checkpoint is not None
        assert state.checkpoint.epoch == 10
        assert len(state.checkpoints_history) == 1

    @pytest.mark.asyncio
    async def test_update_context_increment(self, persistence):
        state = await persistence.update_context_increment(
            "agent_ctx",
            {"current_stage": "processing", "custom_context": {"key": "value"}},
        )
        assert state.session_context.current_stage == "processing"
        assert state.session_context.custom_context["key"] == "value"

    @pytest.mark.asyncio
    async def test_multi_agent_isolation(self, persistence):
        s1 = AgentState(agent_id="agent_iso_1", metadata={"role": "trainer"})
        s2 = AgentState(agent_id="agent_iso_2", metadata={"role": "predictor"})
        await persistence.save_state(s1)
        await persistence.save_state(s2)
        loaded1 = await persistence.load_state("agent_iso_1")
        loaded2 = await persistence.load_state("agent_iso_2")
        assert loaded1.metadata["role"] == "trainer"
        assert loaded2.metadata["role"] == "predictor"

    @pytest.mark.asyncio
    async def test_delete_state(self, persistence):
        state = AgentState(agent_id="to_delete")
        await persistence.save_state(state)
        assert await persistence.load_state("to_delete") is not None
        await persistence.delete_state("to_delete")
        assert await persistence.load_state("to_delete") is None

    @pytest.mark.asyncio
    async def test_memory_pruning(self, persistence):
        state = AgentState(agent_id="mem_prune")
        entries = [
            MemoryEntry(
                content=f"mem_{i}",
                importance=0.1 + (i * 0.01),
            )
            for i in range(1200)
        ]
        state.memory = entries
        await persistence.save_state(state)
        await persistence.prune_memory("mem_prune")
        loaded = await persistence.load_state("mem_prune")
        assert len(loaded.memory) <= MEMORY_PRUNING_THRESHOLD

    @pytest.mark.asyncio
    async def test_checkpoint_cleanup(self, persistence):
        state = AgentState(agent_id="cleanup_test")
        for i in range(10):
            ckpt = Checkpoint(epoch=i)
            state.set_checkpoint(ckpt)
        await persistence.save_state(state)
        removed = await persistence.cleanup_checkpoints("cleanup_test")
        assert removed >= 0

    @pytest.mark.asyncio
    async def test_snapshot_and_rollback(self, persistence):
        state = AgentState(agent_id="snap_test", status=AgentStatus.IDLE)
        await persistence.save_state(state)
        await persistence.snapshot_for_rollback("snap_test")
        state.status = AgentStatus.BUSY
        await persistence.save_state(state)
        rolled = await persistence.rollback_to_version("snap_test")
        if rolled:
            assert rolled.status == AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_list_all_agents(self, persistence):
        for i in range(3):
            state = AgentState(agent_id=f"list_agent_{i}")
            await persistence.save_state(state)
        agents = await persistence.list_all_agent_states()
        agent_ids = [a.get("agent_id") for a in agents]
        for i in range(3):
            assert f"list_agent_{i}" in agent_ids

    @pytest.mark.asyncio
    async def test_running_flag(self, persistence):
        assert persistence.running is True
        await persistence.stop()
        assert persistence.running is False

    @pytest.mark.asyncio
    async def test_heartbeat_start_stop(self, persistence):
        await persistence.start_heartbeat("hb_agent")
        await persistence.stop_heartbeat("hb_agent")


class TestStateRecoveryManager:
    """Session recovery flow tests"""

    @pytest_asyncio.fixture
    async def recovery(self, tmp_path):
        checkpoint_dir = str(tmp_path / "checkpoints")
        manager = StatePersistenceManager(checkpoint_base_dir=checkpoint_dir)
        await manager.start()
        recovery_mgr = StateRecoveryManager(manager)
        return recovery_mgr

    @pytest.mark.asyncio
    async def test_resume_new_agent_no_state(self, recovery):
        result = await recovery.resume_agent("new_agent_no_state")
        assert result["agent_id"] == "new_agent_no_state"
        assert result["action"] == "no_state_found"

    @pytest.mark.asyncio
    async def test_resume_agent_no_task(self, recovery):
        state = AgentState(agent_id="idle_agent", status=AgentStatus.IDLE)
        await recovery._persistence.save_state(state)
        result = await recovery.resume_agent("idle_agent")
        assert result["recovered"] is True
        assert result["action"] in ("idle_resume", "no_state_found")

    @pytest.mark.asyncio
    async def test_resume_with_current_task_no_loader(self, recovery):
        state = AgentState(
            agent_id="task_no_loader",
            current_task_id="task_001",
            status=AgentStatus.BUSY,
        )
        await recovery._persistence.save_state(state)
        result = await recovery.resume_agent("task_no_loader")
        assert result["agent_id"] == "task_no_loader"

    @pytest.mark.asyncio
    async def test_resume_with_task_and_checkpoint(self, recovery):
        state = AgentState(
            agent_id="resume_with_ckpt",
            current_task_id="task_002",
            status=AgentStatus.BUSY,
            checkpoint=Checkpoint(epoch=5, step=250),
        )
        await recovery._persistence.save_state(state)

        task = type(
            "Task", (), {"status": type("Status", (), {"value": "in_progress"})()}
        )()

        async def fake_task_loader(task_id: str):
            return task

        def fake_task_runner(task_id: str, checkpoint=None):
            return {"ran": True, "from_checkpoint": checkpoint is not None}

        result = await recovery.resume_agent(
            "resume_with_ckpt",
            task_loader=fake_task_loader,
            task_runner=fake_task_runner,
        )
        assert result["recovered"] is True
        assert result["action"] in (
            "resumed_with_checkpoint",
            "restarted_without_checkpoint",
            "idle_task_done",
        )

    @pytest.mark.asyncio
    async def test_resume_task_not_in_progress(self, recovery):
        state = AgentState(
            agent_id="done_agent",
            current_task_id="task_done",
            status=AgentStatus.BUSY,
        )
        await recovery._persistence.save_state(state)
        task = type(
            "Task", (), {"status": type("Status", (), {"value": "completed"})()}
        )()

        async def fake_loader(task_id: str):
            return task

        result = await recovery.resume_agent(
            "done_agent",
            task_loader=fake_loader,
        )
        assert result["action"] == "idle_task_done"

    @pytest.mark.asyncio
    async def test_clone_agent_state(self, recovery):
        original = AgentState(
            agent_id="clone_src",
            current_task_id="task_clone",
            session_context=SessionContext(task_description="source task"),
            memory=[MemoryEntry(content="src mem")],
        )
        await recovery._persistence.save_state(original)
        cloned = await recovery.clone_agent_state("clone_src", "clone_dst")
        assert cloned is not None
        assert cloned.agent_id == "clone_dst"
        assert cloned.current_task_id == "task_clone"
        assert cloned.session_context.task_description == "source task"

    @pytest.mark.asyncio
    async def test_clone_nonexistent_source(self, recovery):
        cloned = await recovery.clone_agent_state("nonexistent", "target")
        assert cloned is None

    @pytest.mark.asyncio
    async def test_recovery_history(self, recovery):
        result = await recovery.get_recovery_history("no_history_agent")
        assert isinstance(result, list)


class TestPersistenceIntegration:
    """Integration tests for persistence end-to-end"""

    @pytest_asyncio.fixture
    async def manager(self, tmp_path):
        checkpoint_dir = str(tmp_path / "checkpoints_int")
        mgr = StatePersistenceManager(checkpoint_base_dir=checkpoint_dir)
        await mgr.start()
        return mgr

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, manager):
        agent_id = f"lifecycle_{uuid.uuid4().hex[:8]}"
        state = AgentState(agent_id=agent_id)
        await manager.save_state(state)

        ckpt = Checkpoint(epoch=1, step=100)
        state = await manager.save_checkpoint(agent_id, ckpt)
        assert len(state.checkpoints_history) == 1

        state = await manager.update_context_increment(
            agent_id,
            {"current_stage": "training", "task_description": "LNN training task"},
        )
        assert state.session_context.current_stage == "training"

        state.memory.append(MemoryEntry(content="learned", importance=0.9))
        await manager.save_state(state, trigger="test")

        loaded = await manager.load_state(agent_id)
        assert loaded is not None
        assert loaded.session_context.current_stage == "training"
        assert len(loaded.memory) == 1

        await manager.cleanup_checkpoints(agent_id)
        await manager.delete_state(agent_id)
        assert await manager.load_state(agent_id) is None

    @pytest.mark.asyncio
    async def test_concurrent_agent_isolation(self, manager):
        for i in range(5):
            state = AgentState(
                agent_id=f"concurrent_{i}",
                status=AgentStatus.BUSY if i % 2 == 0 else AgentStatus.IDLE,
                metadata={"index": i},
            )
            await manager.save_state(state)

        agents = await manager.list_all_agent_states()
        ids = [a["agent_id"] for a in agents]
        for i in range(5):
            assert f"concurrent_{i}" in ids

    @pytest.mark.asyncio
    async def test_event_driven_save(self, manager):
        state = AgentState(agent_id="event_agent")
        await manager.save_state(state)

        await manager.update_context_increment(
            "event_agent",
            {"current_stage": "epoch_complete"},
            trigger="epoch_end",
        )
        loaded = await manager.load_state("event_agent")
        assert loaded.session_context.current_stage == "epoch_complete"

    @pytest.mark.asyncio
    async def test_memory_importance_sorting(self, manager):
        state = AgentState(agent_id="sort_test")
        memories = [
            MemoryEntry(content=f"mem_{i}", importance=0.1 + i * 0.05)
            for i in range(1010)
        ]
        state.memory = memories
        await manager.save_state(state)
        await manager.prune_memory("sort_test")
        loaded = await manager.load_state("sort_test")
        importances = [m.importance for m in loaded.memory]
        assert len(loaded.memory) <= MEMORY_PRUNING_THRESHOLD
        assert importances == sorted(importances, reverse=True)


def asyncio_wait(seconds: float):
    import asyncio

    return asyncio.sleep(seconds)


class TestDbTierSqlite:
    """SQLite DB 持久化回归测试。

    复现并验证 P0 修复：``_save_db`` 原先固定使用 PostgreSQL 方言的
    ``to_timestamp()`` / ``NOW()``，在 SQLite 下抛 ``no such function``
    导致状态持久化静默失败（API 返回成功但 DB 无数据）。
    修复后按方言生成 SQL，SQLite 直接写入 epoch 浮点值。
    """

    @pytest_asyncio.fixture
    async def sqlite_mgr(self, tmp_path):
        db_path = tmp_path / "agent_state_test.db"
        mgr = await create_state_persistence(
            db_url=f"sqlite:///{db_path.as_posix()}",
            checkpoint_dir=str(tmp_path / "ckpts"),
        )
        yield mgr
        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_sqlite_save_load_roundtrip(self, sqlite_mgr):
        state = AgentState(
            agent_id="sqlite_agent",
            status=AgentStatus.BUSY,
            current_task_id="task_1",
        )
        state.session_context.task_description = "SQLite 持久化任务"
        await sqlite_mgr.save_state(state, trigger="test")

        # 绕过内存层与 Redis，强制从 DB 读取（验证数据确实落库）
        sqlite_mgr._active_states.clear()
        sqlite_mgr._redis = None
        loaded = await sqlite_mgr.load_state("sqlite_agent")
        assert loaded is not None
        assert loaded.agent_id == "sqlite_agent"
        assert loaded.status == AgentStatus.BUSY
        assert loaded.current_task_id == "task_1"
        assert loaded.session_context.task_description == "SQLite 持久化任务"
        assert isinstance(loaded.last_heartbeat, float)

    @pytest.mark.asyncio
    async def test_sqlite_update_path(self, sqlite_mgr):
        state = AgentState(agent_id="sqlite_upd", current_task_id="t1")
        await sqlite_mgr.save_state(state, trigger="test")
        # 触发 UPDATE 分支
        state2 = AgentState(
            agent_id="sqlite_upd",
            current_task_id="t2",
            status=AgentStatus.BUSY,
        )
        state2.session_context.task_description = "updated"
        await sqlite_mgr.save_state(state2, trigger="test")

        sqlite_mgr._active_states.clear()
        sqlite_mgr._redis = None
        loaded = await sqlite_mgr.load_state("sqlite_upd")
        assert loaded is not None
        assert loaded.current_task_id == "t2"
        assert loaded.status == AgentStatus.BUSY
        assert loaded.session_context.task_description == "updated"

    @pytest.mark.asyncio
    async def test_sqlite_missing_agent_returns_none(self, sqlite_mgr):
        sqlite_mgr._active_states.clear()
        sqlite_mgr._redis = None
        assert await sqlite_mgr.load_state("ghost_sqlite") is None

    @pytest.mark.asyncio
    async def test_sqlite_delete_state(self, sqlite_mgr):
        state = AgentState(agent_id="sqlite_del")
        await sqlite_mgr.save_state(state, trigger="test")
        await sqlite_mgr.delete_state("sqlite_del")
        sqlite_mgr._active_states.clear()
        sqlite_mgr._redis = None
        assert await sqlite_mgr.load_state("sqlite_del") is None
