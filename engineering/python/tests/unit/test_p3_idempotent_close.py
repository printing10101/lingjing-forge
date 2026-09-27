"""P3 验证测试：资源 close/stop 方法幂等性。

验证内容：
1. ``BudgetManager.close()`` 多次调用安全，仅首次释放资源并记录日志
2. ``MultiDimensionCostTracker.close()`` 多次调用安全，仅首次释放资源并记录日志
3. ``RuleDatabase.close()`` 多次调用安全，``close_all()`` 仅被调用一次
4. ``WakeupQueue.close()`` 多次调用安全，``return_connection()`` 仅被调用一次
5. ``HeartbeatScheduler.stop()`` 多次调用安全，``wakeup_queue.close()`` 仅被调用一次
6. ``VectorStore.close()`` 多次调用安全，``client.close()`` 仅被调用一次

设计说明：
- 使用 ``unittest.mock.MagicMock`` 模拟 SQLite 连接池与 ChromaDB 客户端，
  隔离外部依赖，专注验证幂等性契约
- 每个测试通过 ``_closed`` / ``_stopped`` 标志位验证 no-op 行为
- 计数器（``call_count``）验证资源释放方法仅被调用一次
- 异常路径测试验证即使首次 close 抛出异常，``_closed`` 仍被置位（VectorStore 场景）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# BudgetManager.close() 幂等性


@pytest.fixture
def isolated_budget_manager():
    """构造隔离的 BudgetManager 实例，绕过 SQLite 与 default budgets 加载。

    使用 MagicMock 替换连接池与连接，使测试专注于 close 幂等性契约。
    """
    from app.budget.budget import BudgetManager

    manager = BudgetManager.__new__(BudgetManager)
    manager.db_path = ":memory:"
    manager.tracker = MagicMock()
    manager._manager = MagicMock()
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    manager._pool = mock_pool
    manager._conn = mock_conn
    manager._lock = __import__("threading").RLock()
    manager._closed = False
    return manager, mock_pool, mock_conn


class TestBudgetManagerCloseIdempotency:
    """验证 BudgetManager.close() 幂等性。"""

    def test_first_close_releases_connection(self, isolated_budget_manager):
        """首次 close 应当归还连接并设置 _closed 标志位。"""
        manager, mock_pool, mock_conn = isolated_budget_manager

        manager.close()

        mock_pool.return_connection.assert_called_once_with(mock_conn)
        assert manager._conn is None
        assert manager._closed is True

    def test_second_close_is_noop(self, isolated_budget_manager):
        """二次 close 应当直接返回，不再调用 return_connection。"""
        manager, mock_pool, mock_conn = isolated_budget_manager

        manager.close()
        manager.close()  # 二次调用

        # return_connection 仍只被调用一次
        assert mock_pool.return_connection.call_count == 1
        assert manager._closed is True

    def test_close_after_conn_already_none(self, isolated_budget_manager):
        """若 _conn 已为 None，首次 close 应安全执行并设置标志位。"""
        manager, mock_pool, _ = isolated_budget_manager
        manager._conn = None

        manager.close()

        mock_pool.return_connection.assert_not_called()
        assert manager._closed is True


# MultiDimensionCostTracker.close() 幂等性


@pytest.fixture
def isolated_cost_tracker():
    """构造隔离的 MultiDimensionCostTracker 实例。"""
    from app.budget.cost_tracker import MultiDimensionCostTracker

    tracker = MultiDimensionCostTracker.__new__(MultiDimensionCostTracker)
    tracker.db_path = ":memory:"
    tracker._manager = MagicMock()
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    tracker._pool = mock_pool
    tracker._conn = mock_conn
    tracker._unit_prices = MagicMock()
    tracker._closed = False
    return tracker, mock_pool, mock_conn


class TestCostTrackerCloseIdempotency:
    """验证 MultiDimensionCostTracker.close() 幂等性。"""

    def test_first_close_releases_connection(self, isolated_cost_tracker):
        """首次 close 应当归还连接到连接池。"""
        tracker, mock_pool, mock_conn = isolated_cost_tracker

        tracker.close()

        mock_pool.return_connection.assert_called_once_with(mock_conn)
        assert tracker._conn is None
        assert tracker._closed is True

    def test_second_close_is_noop(self, isolated_cost_tracker):
        """二次 close 应当直接返回，不再调用 return_connection。"""
        tracker, mock_pool, mock_conn = isolated_cost_tracker

        tracker.close()
        tracker.close()

        assert mock_pool.return_connection.call_count == 1
        assert tracker._closed is True

    def test_close_fallback_to_direct_close(self, isolated_cost_tracker):
        """无 _pool 时应回退到 conn.close() 直接关闭。"""
        tracker, _, mock_conn = isolated_cost_tracker
        tracker._pool = None

        tracker.close()

        mock_conn.close.assert_called_once()
        assert tracker._conn is None
        assert tracker._closed is True


# RuleDatabase.close() 幂等性


@pytest.fixture
def isolated_rule_database():
    """构造隔离的 RuleDatabase 实例。"""
    from app.database.rule_db import RuleDatabase

    db = RuleDatabase.__new__(RuleDatabase)
    db.db_path = ":memory:"
    db._manager = MagicMock()
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    db._pool = mock_pool
    db._conn = mock_conn
    db._closed = False
    return db, mock_pool, mock_conn


class TestRuleDatabaseCloseIdempotency:
    """验证 RuleDatabase.close() 幂等性。

    重点：``close_all()`` 必须仅被调用一次，避免对连接池重复释放。
    """

    def test_first_close_calls_close_all_once(self, isolated_rule_database):
        """首次 close 应当归还连接并调用 close_all() 一次。"""
        db, mock_pool, mock_conn = isolated_rule_database

        db.close()

        mock_pool.return_connection.assert_called_once_with(mock_conn)
        mock_pool.close_all.assert_called_once()
        assert db._conn is None
        assert db._closed is True

    def test_second_close_does_not_call_close_all(self, isolated_rule_database):
        """二次 close 不应再次调用 close_all()，避免重复释放池中连接。"""
        db, mock_pool, _ = isolated_rule_database

        db.close()
        db.close()

        assert mock_pool.close_all.call_count == 1
        assert mock_pool.return_connection.call_count == 1
        assert db._closed is True

    def test_close_swallows_close_all_exception(self, isolated_rule_database):
        """close_all() 抛异常时不应阻断 close 流程，_closed 仍应被置位。"""
        db, mock_pool, _ = isolated_rule_database
        mock_pool.close_all.side_effect = RuntimeError("pool closed")

        db.close()  # 不应抛出

        assert db._closed is True


# WakeupQueue.close() 幂等性


@pytest.fixture
def isolated_wakeup_queue():
    """构造隔离的 WakeupQueue 实例。"""
    from app.heartbeat.heartbeat import WakeupQueue

    queue = WakeupQueue.__new__(WakeupQueue)
    queue.db_path = ":memory:"
    queue._manager = MagicMock()
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    queue._pool = mock_pool
    queue._conn = mock_conn
    queue._closed = False
    return queue, mock_pool, mock_conn


class TestWakeupQueueCloseIdempotency:
    """验证 WakeupQueue.close() 幂等性。"""

    def test_first_close_releases_connection(self, isolated_wakeup_queue):
        queue, mock_pool, mock_conn = isolated_wakeup_queue

        queue.close()

        mock_pool.return_connection.assert_called_once_with(mock_conn)
        assert queue._conn is None
        assert queue._closed is True

    def test_second_close_is_noop(self, isolated_wakeup_queue):
        queue, mock_pool, _ = isolated_wakeup_queue

        queue.close()
        queue.close()

        assert mock_pool.return_connection.call_count == 1
        assert queue._closed is True


# HeartbeatScheduler.stop() 幂等性


@pytest.fixture
def isolated_heartbeat_scheduler():
    """构造隔离的 HeartbeatScheduler 实例。

    注入 mock wakeup_queue 以验证 close() 调用次数。
    """
    from app.heartbeat.heartbeat import HeartbeatScheduler

    scheduler = HeartbeatScheduler.__new__(HeartbeatScheduler)
    mock_queue = MagicMock()
    scheduler.wakeup_queue = mock_queue
    scheduler.heartbeat_interval = 60
    scheduler._running = False
    scheduler._stopped = False
    scheduler._task = None
    scheduler._on_task_trigger = None
    scheduler._execution_stats = {
        "total_triggered": 0,
        "total_coalesced": 0,
        "total_failed": 0,
    }
    return scheduler, mock_queue


class TestHeartbeatSchedulerStopIdempotency:
    """验证 HeartbeatScheduler.stop() 幂等性。"""

    def test_first_stop_closes_wakeup_queue(self, isolated_heartbeat_scheduler):
        """首次 stop 应当调用 wakeup_queue.close() 一次。"""
        scheduler, mock_queue = isolated_heartbeat_scheduler

        asyncio.run(scheduler.stop())

        mock_queue.close.assert_called_once()
        assert scheduler._stopped is True
        assert scheduler._running is False
        assert scheduler._task is None

    def test_second_stop_is_noop(self, isolated_heartbeat_scheduler):
        """二次 stop 不应再次调用 wakeup_queue.close()。"""
        scheduler, mock_queue = isolated_heartbeat_scheduler

        asyncio.run(scheduler.stop())
        asyncio.run(scheduler.stop())

        assert mock_queue.close.call_count == 1
        assert scheduler._stopped is True

    def test_stop_without_task_does_not_raise(self, isolated_heartbeat_scheduler):
        """无 _task 时 stop 应安全执行（_task 已为 None）。"""
        scheduler, _ = isolated_heartbeat_scheduler
        scheduler._task = None

        asyncio.run(scheduler.stop())  # 不应抛出

        assert scheduler._stopped is True


# VectorStore.close() 幂等性


@pytest.fixture
def isolated_vector_store():
    """构造隔离的 VectorStore 实例，注入 mock ChromaDB client。"""
    from app.rag.vector_store import VectorStore

    store = VectorStore.__new__(VectorStore)
    store._persist_directory = "/tmp/test_chroma"
    store._collection_name = "test"
    store._client = MagicMock()
    store._client.close = MagicMock()
    store._collection = MagicMock()
    store._closed = False
    return store


class TestVectorStoreCloseIdempotency:
    """验证 VectorStore.close() 幂等性。"""

    def test_first_close_calls_client_close(self, isolated_vector_store):
        """首次 close 应当调用 client.close() 并清理引用。"""
        store = isolated_vector_store
        # 保存原始 close 引用，因为 close() 后 _client 会被置为 None
        original_close = store._client.close

        store.close()

        original_close.assert_called_once()
        assert store._client is None
        assert store._collection is None
        assert store._closed is True

    def test_second_close_is_noop(self, isolated_vector_store):
        """二次 close 不应再次调用 client.close()。"""
        store = isolated_vector_store
        original_close = store._client.close

        store.close()
        # 二次调用前 client 已为 None，应直接返回
        store.close()

        original_close.assert_called_once()
        assert store._closed is True

    def test_close_sets_closed_even_on_exception(self, isolated_vector_store):
        """client.close() 抛异常时 _closed 仍应被置位（finally 块）。

        实现中 ``close_fn()`` 的异常被内部 ``try/except`` 捕获并记 warning，
        不向上抛出；``finally`` 块确保 ``_closed = True`` 被执行。

        这确保即使关闭失败，后续 close 调用也不会重复尝试，
        避免在已损坏的对象上反复触发异常路径。
        """
        store = isolated_vector_store
        # 保存原始 close 引用，因为 close() 后 _client 会被置为 None
        original_close = store._client.close
        original_close.side_effect = RuntimeError("client broken")

        # 不应抛出异常（已被内部捕获）
        store.close()

        assert store._closed is True
        assert original_close.call_count == 1
        # 二次调用应为 no-op，不应再次触发 close_fn()
        store.close()
        assert original_close.call_count == 1

    def test_close_without_client_is_safe(self, isolated_vector_store):
        """无 client 时 close 应安全执行并设置标志位。"""
        store = isolated_vector_store
        store._client = None
        store._collection = None

        store.close()

        assert store._closed is True


# 静态契约验证：所有资源类均定义了 _closed / _stopped 标志位


class TestIdempotencyFlagContract:
    """静态验证所有资源类在 __init__ 中初始化幂等性标志位。

    通过 AST 解析确保标志位初始化不被意外删除，
    同时 close/stop 方法首行包含 ``if self._closed`` / ``if self._stopped`` 检查。
    """

    def test_budget_manager_has_closed_flag(self):
        import inspect

        from app.budget import budget

        source = inspect.getsource(budget.BudgetManager.__init__)
        assert "self._closed = False" in source, "BudgetManager.__init__ 必须初始化 self._closed = False"
        close_source = inspect.getsource(budget.BudgetManager.close)
        assert "if self._closed:" in close_source, "BudgetManager.close 必须以 if self._closed: return 开头"
        assert "self._closed = True" in close_source, "BudgetManager.close 必须在结束时设置 self._closed = True"

    def test_cost_tracker_has_closed_flag(self):
        import inspect

        from app.budget.cost_tracker import MultiDimensionCostTracker

        source = inspect.getsource(MultiDimensionCostTracker.__init__)
        assert "self._closed = False" in source, "MultiDimensionCostTracker.__init__ 必须初始化 self._closed = False"

        # 取最后一个 close 方法（覆盖之前的）
        close_source = inspect.getsource(MultiDimensionCostTracker.close)
        assert "if self._closed:" in close_source
        assert "self._closed = True" in close_source

    def test_rule_database_has_closed_flag(self):
        import inspect

        from app.database.rule_db import RuleDatabase

        source = inspect.getsource(RuleDatabase.__init__)
        assert "self._closed = False" in source

        close_source = inspect.getsource(RuleDatabase.close)
        assert "if self._closed:" in close_source
        assert "self._closed = True" in close_source

    def test_wakeup_queue_has_closed_flag(self):
        import inspect

        from app.heartbeat.heartbeat import WakeupQueue

        source = inspect.getsource(WakeupQueue.__init__)
        assert "self._closed = False" in source

        close_source = inspect.getsource(WakeupQueue.close)
        assert "if self._closed:" in close_source
        assert "self._closed = True" in close_source

    def test_heartbeat_scheduler_has_stopped_flag(self):
        import inspect

        from app.heartbeat.heartbeat import HeartbeatScheduler

        source = inspect.getsource(HeartbeatScheduler.__init__)
        assert "self._stopped = False" in source

        stop_source = inspect.getsource(HeartbeatScheduler.stop)
        assert "if self._stopped:" in stop_source
        assert "self._stopped = True" in stop_source

    def test_vector_store_has_closed_flag(self):
        import inspect

        from app.rag.vector_store import VectorStore

        source = inspect.getsource(VectorStore.__init__)
        assert "self._closed = False" in source

        close_source = inspect.getsource(VectorStore.close)
        assert "if self._closed:" in close_source
        assert "self._closed = True" in close_source


# 重复 close 日志频次验证


class TestCloseLogFrequency:
    """验证重复 close 不会重复记录 "closed" 日志。

    这避免了 shutdown 流程中产生噪声日志（如 "BudgetManager closed" 被记录 N 次）。
    """

    @pytest.fixture
    def captured_logs(self, caplog):
        """捕获 INFO 级别日志。"""
        caplog.set_level(logging.INFO)
        return caplog

    def test_budget_manager_close_logs_once(self, isolated_budget_manager, captured_logs):
        manager, _, _ = isolated_budget_manager

        manager.close()
        manager.close()
        manager.close()

        close_logs = [r for r in captured_logs.records if "BudgetManager closed" in r.message]
        assert len(close_logs) == 1, f"BudgetManager closed 日志应仅出现 1 次，实际 {len(close_logs)} 次"

    def test_cost_tracker_close_logs_once(self, isolated_cost_tracker, captured_logs):
        tracker, _, _ = isolated_cost_tracker

        tracker.close()
        tracker.close()
        tracker.close()

        close_logs = [r for r in captured_logs.records if "MultiDimensionCostTracker closed" in r.message]
        assert len(close_logs) == 1

    def test_rule_database_close_logs_once(self, isolated_rule_database, captured_logs):
        db, _, _ = isolated_rule_database

        db.close()
        db.close()
        db.close()

        close_logs = [r for r in captured_logs.records if "RuleDatabase closed" in r.message]
        assert len(close_logs) == 1

    def test_wakeup_queue_close_logs_once(self, isolated_wakeup_queue, captured_logs):
        queue, _, _ = isolated_wakeup_queue

        queue.close()
        queue.close()
        queue.close()

        close_logs = [r for r in captured_logs.records if "WakeupQueue closed" in r.message]
        assert len(close_logs) == 1

    def test_vector_store_close_logs_once(self, isolated_vector_store, captured_logs):
        store = isolated_vector_store

        store.close()
        store.close()
        store.close()

        close_logs = [r for r in captured_logs.records if "ChromaDB PersistentClient closed" in r.message]
        assert len(close_logs) == 1
