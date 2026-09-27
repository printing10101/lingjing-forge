"""P2-2 验证测试：AsyncTaskManager.shutdown() 任务取消与 _shutdown 标志位。

验证内容：
1. ``shutdown()`` 设置 ``_shutdown = True``，后续 ``create_task()`` 抛 ``RuntimeError``
2. ``shutdown()`` 对所有未触发的 cancel event 调用 ``set()``，通知任务协程退出
3. ``shutdown()`` 清空 ``_subscribers`` / ``_cancel_events`` / ``_cancel_hooks`` 字典
4. ``shutdown()`` 日志包含正确的取消计数

设计说明：
- 通过 ``AsyncTaskManager.__new__`` 绕过单例，每个测试创建独立实例
- 不依赖 DB / Redis，所有外部依赖在 fixture 中保持 None / 空字典
- ``_cancel_events`` 注入预构造的 ``asyncio.Event`` 以观察 set 行为
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.tasks.task_system import AsyncTaskManager
from app.tasks.task_manager import TaskStatus, TaskType


@pytest.fixture
def isolated_task_manager(monkeypatch):
    """创建隔离的 AsyncTaskManager 实例，绕过单例与外部依赖。

    复制 ``tests/test_async_task_system.py::task_manager`` fixture 的构造策略，
    额外初始化 ``_cancel_hooks`` 字典与 ``_shutdown = False``，
    确保每个测试从干净状态开始。

    P2-2 修复补充：通过 ``monkeypatch`` 将 ``get_sessionmaker`` 替换为返回
    ``None`` 的桩函数，使 ``_persist_task_to_db`` 提前返回，避免触发
    ``create_async_engine`` → ``aiosqlite`` 导入链（测试环境未安装 aiosqlite）。
    这样 ``test_create_task_works_before_shutdown`` 可在无 DB 环境下运行。
    """
    # 隔离数据库依赖：get_sessionmaker 返回 None，_persist_task_to_db 将提前返回
    # patch 目标是 task_system 模块内的引用（from ... import get_sessionmaker）
    monkeypatch.setattr(
        "app.tasks.task_system.get_sessionmaker",
        lambda: None,
    )

    manager = AsyncTaskManager.__new__(AsyncTaskManager)
    manager._initialized = True
    manager._tasks = {}
    manager._idempotency_map = {}
    manager._cancel_events = {}
    manager._task_lock = asyncio.Lock()
    manager._subscribers = {}
    manager._cancel_hooks = {}
    manager._max_concurrent = 3
    manager._semaphore = asyncio.Semaphore(3)
    manager._started = True
    # P2-2 关键标志位
    manager._shutdown = False
    manager._task_timeout = 3600
    manager._max_retries = 3
    return manager


class TestShutdownFlag:
    """验证 ``_shutdown`` 标志位防止 shutdown 后创建新任务。"""

    @pytest.mark.asyncio
    async def test_shutdown_sets_flag(self, isolated_task_manager):
        """``shutdown()`` 后 ``_shutdown`` 必须为 True。"""
        assert isolated_task_manager._shutdown is False
        await isolated_task_manager.shutdown()
        assert isolated_task_manager._shutdown is True

    @pytest.mark.asyncio
    async def test_shutdown_rejects_create_task(self, isolated_task_manager):
        """``shutdown()`` 后 ``create_task()`` 必须抛 ``RuntimeError``。"""
        await isolated_task_manager.shutdown()

        with pytest.raises(RuntimeError, match="shut down"):
            await isolated_task_manager.create_task(
                task_type=TaskType.LNN_TRAINING,
                params={"epochs": 10},
            )

    @pytest.mark.asyncio
    async def test_create_task_works_before_shutdown(self, isolated_task_manager):
        """``shutdown()`` 之前 ``create_task()`` 应正常工作（回归保护）。"""
        record = await isolated_task_manager.create_task(
            task_type=TaskType.LNN_INFERENCE,
            params={},
        )
        assert record.status == TaskStatus.QUEUED
        assert record.job_id in isolated_task_manager._tasks


class TestShutdownCancelsRunningTasks:
    """验证 ``shutdown()`` 通过 set cancel event 通知运行中任务退出。"""

    @pytest.mark.asyncio
    async def test_shutdown_sets_all_cancel_events(self, isolated_task_manager):
        """``shutdown()`` 必须对所有未触发的 cancel event 调用 ``set()``。"""
        # 注入 3 个 cancel event，模拟 3 个运行中任务
        evt1 = asyncio.Event()
        evt2 = asyncio.Event()
        evt3 = asyncio.Event()
        isolated_task_manager._cancel_events = {
            "job-1": evt1,
            "job-2": evt2,
            "job-3": evt3,
        }
        # 同步在 _tasks 中放入对应记录，以便观察日志计数
        from app.tasks.task_system import TaskRecord
        isolated_task_manager._tasks = {
            "job-1": TaskRecord(
                job_id="job-1",
                task_type=TaskType.LNN_TRAINING,
                status=TaskStatus.RUNNING,
            ),
            "job-2": TaskRecord(
                job_id="job-2",
                task_type=TaskType.LNN_TRAINING,
                status=TaskStatus.RUNNING,
            ),
            "job-3": TaskRecord(
                job_id="job-3",
                task_type=TaskType.LNN_TRAINING,
                status=TaskStatus.RUNNING,
            ),
        }

        await isolated_task_manager.shutdown()

        assert evt1.is_set()
        assert evt2.is_set()
        assert evt3.is_set()

    @pytest.mark.asyncio
    async def test_shutdown_does_not_reset_already_set_events(
        self, isolated_task_manager
    ):
        """已 set 的 event 不应被重复 set（计数准确性）。"""
        evt_already_set = asyncio.Event()
        evt_already_set.set()
        evt_pending = asyncio.Event()
        isolated_task_manager._cancel_events = {
            "job-done": evt_already_set,
            "job-running": evt_pending,
        }

        # shutdown 应只 set 未触发的 event
        await isolated_task_manager.shutdown()

        assert evt_already_set.is_set()  # 仍为 set
        assert evt_pending.is_set()  # 现在被 set

    @pytest.mark.asyncio
    async def test_shutdown_logs_cancel_count(
        self, isolated_task_manager, caplog
    ):
        """``shutdown()`` 日志应包含正确的取消数量。"""
        evt1 = asyncio.Event()
        evt2 = asyncio.Event()
        isolated_task_manager._cancel_events = {"job-1": evt1, "job-2": evt2}

        with caplog.at_level(logging.INFO, logger="app.tasks.task_system"):
            await isolated_task_manager.shutdown()

        # 日志应包含 "2 task(s) signalled to cancel"
        cancel_logs = [
            r for r in caplog.records if "signalled to cancel" in r.getMessage()
        ]
        assert len(cancel_logs) == 1
        assert "2 task(s) signalled to cancel" in cancel_logs[0].getMessage()


class TestShutdownClearsDicts:
    """验证 ``shutdown()`` 清空任务相关字典避免内存泄漏。"""

    @pytest.mark.asyncio
    async def test_shutdown_clears_subscribers(self, isolated_task_manager):
        """``_subscribers`` 必须被清空（SSE/WS 连接已断开，Queue 不再被消费）。"""
        isolated_task_manager._subscribers = {
            "job-1": [asyncio.Queue(), asyncio.Queue()],
            "job-2": [asyncio.Queue()],
        }
        assert len(isolated_task_manager._subscribers) > 0

        await isolated_task_manager.shutdown()

        assert isolated_task_manager._subscribers == {}

    @pytest.mark.asyncio
    async def test_shutdown_clears_cancel_events(self, isolated_task_manager):
        """``_cancel_events`` 必须被清空，避免 shutdown 后残留 Event 引用。"""
        isolated_task_manager._cancel_events = {
            "job-1": asyncio.Event(),
            "job-2": asyncio.Event(),
        }
        assert len(isolated_task_manager._cancel_events) > 0

        await isolated_task_manager.shutdown()

        assert isolated_task_manager._cancel_events == {}

    @pytest.mark.asyncio
    async def test_shutdown_clears_cancel_hooks(self, isolated_task_manager):
        """``_cancel_hooks`` 必须被清空，与 _subscribers/_cancel_events 保持一致。"""

        def hook1():
            pass

        def hook2():
            pass

        isolated_task_manager._cancel_hooks = {"job-1": hook1, "job-2": hook2}
        assert len(isolated_task_manager._cancel_hooks) > 0

        await isolated_task_manager.shutdown()

        assert isolated_task_manager._cancel_hooks == {}

    @pytest.mark.asyncio
    async def test_shutdown_idempotent_clear(self, isolated_task_manager):
        """多次 ``shutdown()`` 调用应安全（幂等清空已空字典）。"""
        await isolated_task_manager.shutdown()
        # 第二次调用不应抛异常
        await isolated_task_manager.shutdown()

        assert isolated_task_manager._subscribers == {}
        assert isolated_task_manager._cancel_events == {}
        assert isolated_task_manager._cancel_hooks == {}


class TestShutdownStartsFlag:
    """验证 ``shutdown()`` 同时重置 ``_started`` 标志位。"""

    @pytest.mark.asyncio
    async def test_shutdown_resets_started_flag(self, isolated_task_manager):
        """``_started`` 必须被重置为 False，与 ``_shutdown`` 一致。"""
        assert isolated_task_manager._started is True
        await isolated_task_manager.shutdown()
        assert isolated_task_manager._started is False
