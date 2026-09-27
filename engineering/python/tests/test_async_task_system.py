"""
Test Async Task System

Tests for:
- TaskRecord: Task lifecycle tracking
- AsyncTaskManager: Async task management with lifecycle control
- Concurrency control with configurable limits
- SSE event broadcasting
- Idempotency support
"""

import asyncio
import pytest

from app.tasks.task_system import AsyncTaskManager, TaskRecord
from app.tasks.task_manager import TaskStatus, TaskType


class TestTaskRecord:
    """Test TaskRecord dataclass"""

    def test_task_record_creation(self):
        record = TaskRecord(
            job_id="test-job-123",
            task_type=TaskType.LNN_TRAINING,
            status=TaskStatus.PENDING,
        )

        assert record.job_id == "test-job-123"
        assert record.task_type == TaskType.LNN_TRAINING
        assert record.status == TaskStatus.PENDING
        assert record.progress == 0.0
        assert record.result is None
        assert record.error is None

    def test_task_record_with_all_fields(self):
        record = TaskRecord(
            job_id="test-job-456",
            task_type=TaskType.LNN_INFERENCE,
            status=TaskStatus.RUNNING,
            progress=50.0,
            result={"output": "data"},
            error=None,
            params={"param1": "value1"},
            owner_id="user-123",
            idempotency_key="idem-key",
            created_at=1000.0,
            started_at=1010.0,
            completed_at=1050.0,
            metrics={"accuracy": 0.95},
        )

        assert record.job_id == "test-job-456"
        assert record.progress == 50.0
        assert record.result == {"output": "data"}
        assert record.owner_id == "user-123"
        assert record.idempotency_key == "idem-key"

    def test_task_record_to_dict(self):
        record = TaskRecord(
            job_id="test-job-789",
            task_type=TaskType.DATA_PROCESSING,
            status=TaskStatus.COMPLETED,
            started_at=1000.0,
            completed_at=1050.0,
        )

        result = record.to_dict()

        assert result["job_id"] == "test-job-789"
        assert result["status"] == "completed"
        assert result["task_type"] == "data_processing"
        assert "created_at_iso" in result
        assert "started_at_iso" in result
        assert "completed_at_iso" in result
        assert result["duration_seconds"] == 50.0

    def test_task_record_to_dict_without_timestamps(self):
        record = TaskRecord(
            job_id="test-job-000",
            task_type=TaskType.MODEL_EXPORT,
            status=TaskStatus.PENDING,
        )

        result = record.to_dict()

        assert "created_at_iso" in result
        assert "started_at_iso" not in result
        assert "completed_at_iso" not in result
        assert "duration_seconds" not in result


@pytest.fixture
def task_manager(monkeypatch):
    """Create a fresh AsyncTaskManager instance for each test"""
    # 强制内存模式：任务持久化走 DB 会跨测试残留（list_tasks 从 DB 读）
    monkeypatch.setattr("app.tasks.task_system.get_sessionmaker", lambda: None)

    # 重构后完成任务会被 _cleanup_task 从 _tasks 移除；测试需保留任务以断言状态
    async def _noop_cleanup(self, job_id):
        pass

    monkeypatch.setattr(
        "app.tasks.task_system.AsyncTaskManager._cleanup_task", _noop_cleanup
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
    manager._started = False
    manager._shutdown = False  # P2-2：shutdown 标志（新增属性，fixture 同步）
    from app.tasks.task_system import DEFAULT_MAX_RETRIES, DEFAULT_TASK_TIMEOUT_SECONDS

    manager._task_timeout = DEFAULT_TASK_TIMEOUT_SECONDS
    manager._max_retries = DEFAULT_MAX_RETRIES
    return manager


class TestAsyncTaskManagerCreation:
    """Test AsyncTaskManager task creation"""

    @pytest.mark.asyncio
    async def test_create_task_basic(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={"epochs": 100},
        )

        assert record.job_id is not None
        assert record.task_type == TaskType.LNN_TRAINING
        assert record.status == TaskStatus.QUEUED
        assert record.params == {"epochs": 100}

    @pytest.mark.asyncio
    async def test_create_task_with_owner(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.LNN_INFERENCE,
            params={},
            owner_id="user-123",
        )

        assert record.owner_id == "user-123"

    @pytest.mark.asyncio
    async def test_create_task_with_idempotency_key(self, task_manager):
        record1 = await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
            idempotency_key="same-key",
        )

        record2 = await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
            idempotency_key="same-key",
        )

        assert record1.job_id == record2.job_id
        assert len(task_manager._tasks) == 1

    @pytest.mark.asyncio
    async def test_create_task_different_idempotency_keys(self, task_manager):
        record1 = await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
            idempotency_key="key-1",
        )

        record2 = await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
            idempotency_key="key-2",
        )

        assert record1.job_id != record2.job_id
        assert len(task_manager._tasks) == 2

    @pytest.mark.asyncio
    async def test_task_id_format(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
        )

        assert record.job_id.startswith("lnn_training-")
        assert len(record.job_id.split("-")[-1]) == 12


class TestAsyncTaskManagerExecution:
    """Test AsyncTaskManager task execution"""

    @pytest.mark.asyncio
    async def test_execute_task_success(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.LNN_INFERENCE,
            params={},
        )

        async def executor(cancel_event, progress_updater):
            await progress_updater(100.0, "Done")
            return {"result": "success"}

        await task_manager.execute_task(record.job_id, executor)

        updated = await task_manager.get_task(record.job_id)
        assert updated.status == TaskStatus.COMPLETED
        assert updated.result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_execute_task_failure(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
        )

        async def executor(cancel_event, progress_updater):
            raise ValueError("Training failed")

        await task_manager.execute_task(record.job_id, executor)

        updated = await task_manager.get_task(record.job_id)
        assert updated.status == TaskStatus.FAILED
        assert "Training failed" in updated.error or updated.error not in (None, "")

    @pytest.mark.asyncio
    async def test_execute_task_cancellation(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.DATA_PROCESSING,
            params={},
        )

        cancel_called = False

        async def executor(cancel_event, progress_updater):
            nonlocal cancel_called
            if cancel_event:
                cancel_called = True
            await asyncio.sleep(10)

        task = asyncio.create_task(task_manager.execute_task(record.job_id, executor))
        await asyncio.sleep(0.01)

        await task_manager.cancel_task(record.job_id)
        # 3.11.0rc2 无 asyncio.Task.catch()（正式版 3.11 引入），用 try/except 替代
        try:
            await task
        except asyncio.CancelledError:
            pass

        updated = await task_manager.get_task(record.job_id)
        assert updated.status == TaskStatus.CANCELLED


class TestAsyncTaskManagerCancellation:
    """Test AsyncTaskManager task cancellation"""

    @pytest.mark.asyncio
    async def test_cancel_task_running(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.MODEL_EXPORT,
            params={},
        )

        async def executor(cancel_event, progress_updater):
            await asyncio.sleep(10)

        asyncio.create_task(task_manager.execute_task(record.job_id, executor))
        await asyncio.sleep(0.01)

        result = await task_manager.cancel_task(record.job_id)

        assert result is True
        updated = await task_manager.get_task(record.job_id)
        assert updated.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_task_completed_returns_false(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.LNN_INFERENCE,
            params={},
        )

        async def executor(cancel_event, progress_updater):
            return {"done": True}

        await task_manager.execute_task(record.job_id, executor)

        result = await task_manager.cancel_task(record.job_id)

        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, task_manager):
        result = await task_manager.cancel_task("nonexistent-job-id")
        assert result is False


class TestAsyncTaskManagerRetrieval:
    """Test AsyncTaskManager task retrieval"""

    @pytest.mark.asyncio
    async def test_get_task_existing(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
        )

        result = await task_manager.get_task(record.job_id)

        assert result is not None
        assert result.job_id == record.job_id

    @pytest.mark.asyncio
    async def test_get_task_nonexistent(self, task_manager):
        result = await task_manager.get_task("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_tasks_all(self, task_manager):
        for i in range(5):
            await task_manager.create_task(
                task_type=TaskType.LNN_TRAINING,
                params={},
            )

        tasks = await task_manager.list_tasks()

        assert len(tasks) == 5

    @pytest.mark.asyncio
    async def test_list_tasks_filter_by_owner(self, task_manager):
        await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
            owner_id="user-1",
        )
        await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
            owner_id="user-2",
        )
        await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
            owner_id="user-1",
        )

        tasks = await task_manager.list_tasks(owner_id="user-1")

        assert len(tasks) == 2
        assert all(t.owner_id == "user-1" for t in tasks)

    @pytest.mark.asyncio
    async def test_list_tasks_filter_by_status(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
        )

        async def executor(cancel_event, progress_updater):
            return {}

        await task_manager.execute_task(record.job_id, executor)

        queued_tasks = await task_manager.list_tasks(status=TaskStatus.QUEUED)
        completed_tasks = await task_manager.list_tasks(status=TaskStatus.COMPLETED)

        assert len(queued_tasks) == 0
        assert len(completed_tasks) == 1

    @pytest.mark.asyncio
    async def test_list_tasks_pagination(self, task_manager):
        for i in range(10):
            await task_manager.create_task(
                task_type=TaskType.LNN_TRAINING,
                params={},
            )

        page1 = await task_manager.list_tasks(limit=3, offset=0)
        page2 = await task_manager.list_tasks(limit=3, offset=3)

        assert len(page1) == 3
        assert len(page2) == 3


class TestAsyncTaskManagerConcurrency:
    """Test AsyncTaskManager concurrency control"""

    @pytest.mark.asyncio
    async def test_max_concurrent_limit(self, task_manager):
        task_manager._max_concurrent = 2
        task_manager._semaphore = asyncio.Semaphore(2)

        execution_order = []

        async def slow_executor(cancel_event, progress_updater):
            execution_order.append("start")
            await asyncio.sleep(0.1)
            execution_order.append("end")
            return {}

        for i in range(3):
            record = await task_manager.create_task(
                task_type=TaskType.LNN_TRAINING,
                params={},
            )
            asyncio.create_task(task_manager.execute_task(record.job_id, slow_executor))

        await asyncio.sleep(0.15)

        running_count = sum(
            1 for t in task_manager._tasks.values() if t.status == TaskStatus.RUNNING
        )

        assert running_count <= 2


class TestAsyncTaskManagerStats:
    """Test AsyncTaskManager statistics"""

    @pytest.mark.asyncio
    async def test_get_stats(self, task_manager):
        for i in range(3):
            await task_manager.create_task(
                task_type=TaskType.LNN_TRAINING,
                params={},
            )

        stats = task_manager.get_stats()

        assert stats["total_tasks"] == 3
        assert "max_concurrent" in stats
        assert "available_slots" in stats

    @pytest.mark.asyncio
    async def test_get_stats_with_completed_tasks(self, task_manager):
        async def _ok_executor(cancel_event, progress_updater):
            return {}

        for i in range(2):
            record = await task_manager.create_task(
                task_type=TaskType.LNN_TRAINING,
                params={},
            )
            await task_manager.execute_task(record.job_id, _ok_executor)

        stats = task_manager.get_stats()

        assert stats["completed_tasks"] == 2


class TestAsyncTaskManagerErrorSuggestions:
    """Test error suggestion logic"""

    def test_memory_error_suggestion(self, task_manager):
        error = ValueError("Out of memory error")
        suggestion = task_manager._get_error_suggestion(error)

        assert "batch_size" in suggestion or "CPU" in suggestion

    def test_cuda_error_suggestion(self, task_manager):
        error = ValueError("CUDA out of memory")
        suggestion = task_manager._get_error_suggestion(error)

        assert "GPU" in suggestion or "CPU" in suggestion

    def test_file_error_suggestion(self, task_manager):
        error = ValueError("File not found")
        suggestion = task_manager._get_error_suggestion(error)

        assert "文件路径" in suggestion


class TestAsyncTaskManagerProgressUpdates:
    """Test progress update functionality"""

    @pytest.mark.asyncio
    async def test_progress_update(self, task_manager):
        record = await task_manager.create_task(
            task_type=TaskType.LNN_TRAINING,
            params={},
        )

        progress_updater = task_manager._create_progress_updater(record.job_id)
        await progress_updater(50.0, "Halfway done", {"step": "training"})

        updated = await task_manager.get_task(record.job_id)
        assert updated.progress == 50.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
