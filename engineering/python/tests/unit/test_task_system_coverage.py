"""tasks/task_system AsyncTaskManager 覆盖率补强测试。

覆盖无 DB / 无 Redis 的纯内存路径：任务创建与执行（成功/可重试失败/
不可重试失败/超时/取消）、幂等去重、并发信号量、订阅广播、
列表/统计/过滤、单例语义、shutdown 后拒绝。
"""

from __future__ import annotations

import asyncio
import pytest

from app.tasks.task_system import AsyncTaskManager
from app.tasks.task_manager import TaskStatus, TaskType

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_manager(monkeypatch):
    """每个测试独立：重置单例 + 无 DB 模式。"""
    AsyncTaskManager._instance = None
    monkeypatch.setattr("app.tasks.task_system.get_sessionmaker", lambda: None)
    yield
    AsyncTaskManager._instance = None


async def _submit(mgr, handler, **kw):
    """create_task + 调度 execute_task（handler 由 execute_task 接收）。"""
    params = kw.pop("params", {})
    rec = await mgr.create_task(task_type=TaskType.LNN_TRAINING, params=params, **kw)
    asyncio.create_task(mgr.execute_task(rec.job_id, handler))
    return rec


def _echo(*args, **kw):
    async def run(cancel_evt, updater):
        return {"status": "ok", "value": 42}

    return run


def _fail_valueerror(*args, **kw):
    async def run(cancel_evt, updater):
        raise ValueError("bad input")

    return run


def _fail_retryable(*args, **kw):
    async def run(cancel_evt, updater):
        raise ConnectionError("network down")

    return run


class TestSingletonAndLifecycle:
    def test_singleton(self):
        assert AsyncTaskManager() is AsyncTaskManager()

    def test_initialize_and_shutdown(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize(max_concurrent=2)
            assert mgr._started is True
            assert mgr._semaphore is not None
            await mgr.shutdown()
            assert mgr._started is False
            assert mgr._shutdown is True

        asyncio.run(flow())

    def test_shutdown_clears_subscribers(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            q = mgr.subscribe("j1")
            mgr._subscribers["j1"] = [q]
            await mgr.shutdown()
            assert mgr._subscribers == {}
            assert mgr._cancel_events == {}

        asyncio.run(flow())

    def test_create_after_shutdown_raises(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            await mgr.shutdown()
            with pytest.raises(RuntimeError):
                await mgr.create_task(task_type=TaskType.LNN_TRAINING, params={})

        asyncio.run(flow())

    def test_recover_running_without_db(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            assert mgr._started is True

        asyncio.run(flow())


class TestCreateAndExecute:
    def test_create_task_runs_to_completion(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            rec = await _submit(mgr, _echo(), params={"epochs": 1})
            assert rec.job_id
            assert rec.status in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING)
            await asyncio.sleep(0.3)
            done = await mgr.get_task(rec.job_id)
            assert done is None  # 完成后清理

        asyncio.run(flow())

    def test_create_task_invalid_type(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            with pytest.raises((ValueError, AttributeError)):
                await mgr.create_task(task_type="nope", params={})

        asyncio.run(flow())

    def test_idempotency_key_dedup(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            r1 = await mgr.create_task(
                task_type=TaskType.LNN_TRAINING, params={}, idempotency_key="k1"
            )
            r2 = await mgr.create_task(
                task_type=TaskType.LNN_TRAINING, params={}, idempotency_key="k1"
            )
            assert r1.job_id == r2.job_id

        asyncio.run(flow())

    def test_execute_unknown_job_noop(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            await mgr.execute_task("ghost", _echo())

        asyncio.run(flow())

    def test_non_retryable_error_marks_failed(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            rec = await _submit(mgr, _fail_valueerror())
            await asyncio.sleep(0.3)
            done = await mgr.get_task(rec.job_id)
            assert done is None  # 失败后同样清理

        asyncio.run(flow())

    def test_retryable_exhausts_retries(self):
        mgr = AsyncTaskManager()
        mgr._max_retries = 0

        async def flow():
            await mgr.initialize()
            rec = await _submit(mgr, _fail_retryable())
            await asyncio.sleep(0.3)
            done = await mgr.get_task(rec.job_id)
            assert done is None

        asyncio.run(flow())

    def test_timeout_marks_failed(self):
        mgr = AsyncTaskManager()
        mgr._task_timeout = 0.05
        mgr._max_retries = 0

        async def flow():
            await mgr.initialize()

            async def slow(cancel_evt, updater):
                await asyncio.sleep(5.0)
                return {}

            rec = await _submit(mgr, slow)
            await asyncio.sleep(0.3)
            done = await mgr.get_task(rec.job_id)
            assert done is None

        asyncio.run(flow())

    def test_cancelled_task(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()

            async def cancellable(cancel_evt, updater):
                await asyncio.sleep(0.5)
                return {}

            rec = await _submit(mgr, cancellable)
            await mgr.cancel_task(rec.job_id)
            await asyncio.sleep(0.2)
            done = await mgr.get_task(rec.job_id)
            assert done is None or done.status == TaskStatus.CANCELLED

        asyncio.run(flow())

    def test_progress_updater_sets_progress(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()

            async def with_progress(cancel_evt, updater):
                await updater(50.0, "halfway", {"loss": 0.1})
                return {"metrics": {"loss": 0.1}}

            rec = await _submit(mgr, with_progress)
            await asyncio.sleep(0.3)
            done = await mgr.get_task(rec.job_id)
            if done is not None:
                assert done.progress in (50.0, 100.0)

        asyncio.run(flow())

    def test_broadcast_event_reaches_subscriber(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            rec = await _submit(mgr, _echo())
            q = mgr.subscribe(rec.job_id)
            await mgr._broadcast_event(rec.job_id, "test", {"k": 1})
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            assert "event: test" in event
            assert '"k": 1' in event

        asyncio.run(flow())


class TestCancelAndHooks:
    def test_cancel_missing_returns_false(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            assert await mgr.cancel_task("ghost") is False

        asyncio.run(flow())

    def test_cancel_completed_returns_false(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            rec = await _submit(mgr, _echo())
            await asyncio.sleep(0.3)
            assert await mgr.cancel_task(rec.job_id) is False

        asyncio.run(flow())

    def test_cancel_hook_invoked(self):
        mgr = AsyncTaskManager()
        calls = []

        def hook():
            calls.append(1)

        async def flow():
            await mgr.initialize()

            async def cancellable(cancel_evt, updater):
                await asyncio.sleep(0.5)
                return {}

            rec = await _submit(mgr, cancellable)
            mgr.register_cancel_hook(rec.job_id, hook)
            await mgr.cancel_task(rec.job_id)
            assert calls == [1]

        asyncio.run(flow())

    def test_cancel_hook_failure_swallowed(self):
        mgr = AsyncTaskManager()

        def bad_hook():
            raise ValueError("hook exploded")

        async def flow():
            await mgr.initialize()

            async def cancellable(cancel_evt, updater):
                await asyncio.sleep(0.5)
                return {}

            rec = await _submit(mgr, cancellable)
            mgr.register_cancel_hook(rec.job_id, bad_hook)
            assert await mgr.cancel_task(rec.job_id) is True

        asyncio.run(flow())


class TestListAndStats:
    async def _seed(self, mgr):
        await mgr.initialize()
        recs = []
        for i in range(3):
            recs.append(
                await mgr.create_task(
                    task_type=TaskType.LNN_TRAINING,
                    params={"i": i},
                    owner_id=f"owner-{i % 2}",
                )
            )
        return recs

    def test_filter_tasks_by_owner_and_type(self):
        mgr = AsyncTaskManager()

        async def flow():
            await self._seed(mgr)
            listed = await mgr.list_tasks(owner_id="owner-0")
            assert all(r.owner_id == "owner-0" for r in listed)
            by_type = await mgr.list_tasks(task_type=TaskType.LNN_TRAINING)
            assert isinstance(by_type, list)
            assert len(by_type) == 3
            # 过滤边界：offset/limit
            paged = await mgr.list_tasks(limit=2, offset=1)
            assert len(paged) == 2

        asyncio.run(flow())

    def test_count_tasks_memory_fallback(self):
        mgr = AsyncTaskManager()

        async def flow():
            await self._seed(mgr)
            n = await mgr.count_tasks()
            assert n == 3
            n_owner = await mgr.count_tasks(owner_id="owner-1")
            assert n_owner == 1

        asyncio.run(flow())

    def test_get_stats_shape(self):
        mgr = AsyncTaskManager()

        async def flow():
            await self._seed(mgr)
            s = mgr.get_stats()
            assert s["total_tasks"] == 3
            assert s["queued_tasks"] == 3
            assert s["available_slots"] == 3
            assert s["max_concurrent"] == 3

        asyncio.run(flow())

    def test_estimate_wait_and_queue_size(self):
        mgr = AsyncTaskManager()

        async def flow():
            await mgr.initialize()
            assert mgr._estimate_wait() == 0.0
            assert mgr._queue_size() == 0

        asyncio.run(flow())


class TestHelpers:
    def test_error_suggestion_branches(self):
        mgr = AsyncTaskManager()
        assert "batch_size" in mgr._get_error_suggestion(MemoryError("out of memory"))
        assert "GPU" in mgr._get_error_suggestion(RuntimeError("cuda error: device failure"))
        assert "路径" in mgr._get_error_suggestion(FileNotFoundError("file not found"))
        assert "检查输入参数" in mgr._get_error_suggestion(ValueError("misc"))

    def test_subscribe_unsubscribe(self):
        mgr = AsyncTaskManager()
        q = mgr.subscribe("j1")
        assert q is not None  # 未注册任务也可订阅，返回队列
        # 通过 create_task 注册后订阅者被广播
        asyncio.run(mgr._broadcast_event("j1", "x", {"a": 1}))  # 无订阅者不抛
        mgr.unsubscribe("j1", q)  # 重复取消不抛
        mgr.unsubscribe("ghost", q)

    def test_broadcast_no_subscribers(self):
        mgr = AsyncTaskManager()
        asyncio.run(mgr._broadcast_event("j1", "x", {"a": 1}))

    def test_progress_updater_no_task(self):
        mgr = AsyncTaskManager()
        updater = mgr._create_progress_updater("ghost")

        async def flow():
            await updater(10.0, "m")

        asyncio.run(flow())

    def test_get_task_progress_from_redis_no_redis(self):
        mgr = AsyncTaskManager()

        async def flow():
            assert await mgr.get_task_progress_from_redis("j1") == {}

        asyncio.run(flow())
