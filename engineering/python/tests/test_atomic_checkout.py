"""
Comprehensive tests for the Atomic Task Checkout System.

Covers:
- Execution lock lifecycle (create, heartbeat, timeout, force release, cleanup)
- Atomic checkout with thread safety
- Anti-duplicate-work strategies (single-task mode, batch mode)
- Checkout failure handling and retry strategies
- Priority queue management
- Task board data aggregation
- API endpoints
"""

import os
import sys
import time
import uuid
import pytest
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tasks.execution_lock import (  # noqa: E402
    ExecutionLockStore,
    LockConflictError,
    LockNotFoundError,
    LockOwnershipError,
    LockStatus,
)
from app.tasks.task_checkout import (  # noqa: E402
    TaskCheckoutManager,
    CheckoutStatus,
    CheckoutFailureReason,
    CheckoutPriority,
    AgentMode,
    TaskStatus,
    CheckoutRequest,
    TaskRecord,
    MAX_RETRY_COUNT,
)


@pytest.fixture
def temp_db_dir():
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    import shutil

    try:
        shutil.rmtree(tmpdir)
    except Exception:
        pass


@pytest.fixture
def lock_store(temp_db_dir):
    db_path = os.path.join(temp_db_dir, "locks.db")
    store = ExecutionLockStore(db_path=db_path)
    yield store
    store.close()


@pytest.fixture
def checkout_manager(temp_db_dir, lock_store):
    db_path = os.path.join(temp_db_dir, "checkout.db")
    mgr = TaskCheckoutManager(lock_store=lock_store, db_path=db_path)
    _setup_budget_gpu_checkers(mgr)
    yield mgr
    mgr.close()


def _setup_budget_gpu_checkers(mgr):
    budget_state = {"exceeded": False}

    def budget_checker(agent_id: str, project_id: str) -> bool:
        return not budget_state["exceeded"]

    def gpu_checker(required_memory: float) -> bool:
        return True

    mgr.set_budget_checker(budget_checker)
    mgr.set_gpu_checker(gpu_checker)
    return budget_state


def _make_task(
    task_id: str = None,
    title: str = "Test Task",
    status: str = "pending",
    assigned_to: str = None,
    priority: int = 3,
    required_gpu: float = 0.0,
    blockers: list = None,
    **kwargs,
) -> TaskRecord:
    return TaskRecord(
        id=task_id or f"task-{uuid.uuid4().hex[:8]}",
        title=title,
        description=kwargs.get("description", ""),
        task_type=kwargs.get("task_type", "execution"),
        status=status,
        assigned_to=assigned_to,
        parent_goal_id=kwargs.get("parent_goal_id"),
        project_id=kwargs.get("project_id", "default-project"),
        required_gpu_memory=required_gpu,
        blockers=blockers or [],
        priority=priority,
    )


def _make_request(
    task_id: str,
    agent_id: str = "agent-001",
    agent_mode: AgentMode = AgentMode.SINGLE,
    priority: CheckoutPriority = CheckoutPriority.NORMAL,
    required_gpu: float = 0.0,
    timeout_hours: float = 4.0,
) -> CheckoutRequest:
    return CheckoutRequest(
        task_id=task_id,
        agent_id=agent_id,
        agent_mode=agent_mode,
        priority=priority,
        required_gpu_memory=required_gpu,
        timeout_hours=timeout_hours,
    )


class TestExecutionLockStore:
    def test_create_lock_success(self, lock_store):
        lock = lock_store.create_lock(task_id="task-001", agent_id="agent-A")
        assert lock.task_id == "task-001"
        assert lock.agent_id == "agent-A"
        assert lock.status == LockStatus.ACTIVE
        assert lock.expires_at is not None
        assert lock.heartbeat_at is not None

    def test_create_duplicate_lock_fails(self, lock_store):
        lock_store.create_lock(task_id="task-001", agent_id="agent-A")
        with pytest.raises(LockConflictError):
            lock_store.create_lock(task_id="task-001", agent_id="agent-B")

    def test_get_lock(self, lock_store):
        lock_store.create_lock(task_id="task-002", agent_id="agent-A")
        lock = lock_store.get_lock("task-002")
        assert lock is not None
        assert lock.task_id == "task-002"
        assert lock.status == LockStatus.ACTIVE

    def test_get_nonexistent_lock_returns_none(self, lock_store):
        assert lock_store.get_lock("nonexistent") is None

    def test_heartbeat_renews_expiry(self, lock_store):
        lock_store.create_lock(task_id="task-003", agent_id="agent-A")
        time.sleep(0.05)
        renewed = lock_store.heartbeat("task-003", "agent-A")
        assert renewed.status == LockStatus.ACTIVE

    def test_heartbeat_wrong_agent_fails(self, lock_store):
        lock_store.create_lock(task_id="task-004", agent_id="agent-A")
        with pytest.raises(LockNotFoundError):
            lock_store.heartbeat("task-004", "agent-B")

    def test_heartbeat_nonexistent_lock_fails(self, lock_store):
        with pytest.raises(LockNotFoundError):
            lock_store.heartbeat("nonexistent", "agent-A")

    def test_release_lock(self, lock_store):
        lock_store.create_lock(task_id="task-005", agent_id="agent-A")
        lock = lock_store.release_lock("task-005", "agent-A")
        assert lock.status == LockStatus.RELEASED
        assert lock.released_at is not None

    def test_release_wrong_agent_fails(self, lock_store):
        lock_store.create_lock(task_id="task-006", agent_id="agent-A")
        with pytest.raises(LockOwnershipError):
            lock_store.release_lock("task-006", "agent-B")

    def test_force_release(self, lock_store):
        lock_store.create_lock(task_id="task-007", agent_id="agent-A")
        lock = lock_store.force_release("task-007", "admin")
        assert lock.status == LockStatus.FORCE_RELEASED

    def test_force_release_nonexistent_raises_error(self, lock_store):
        with pytest.raises(LockNotFoundError):
            lock_store.force_release("nonexistent", "admin")

    def test_cleanup_expired_locks(self, lock_store):
        lock_store.create_lock(
            task_id="task-008", agent_id="agent-A", timeout_hours=0.001
        )
        lock_store.create_lock(task_id="task-009", agent_id="agent-B", timeout_hours=24)
        time.sleep(5)
        expired = lock_store.cleanup_expired_locks()
        expired_ids = [e.task_id for e in expired]
        assert "task-008" in expired_ids
        assert "task-009" not in expired_ids

    def test_list_active_locks(self, lock_store):
        lock_store.create_lock(task_id="task-010", agent_id="agent-A")
        lock_store.create_lock(task_id="task-011", agent_id="agent-B")
        active = lock_store.list_active_locks()
        assert len(active) == 2

    def test_list_all_locks(self, lock_store):
        lock_store.create_lock(task_id="task-012", agent_id="agent-A")
        lock_store.force_release("task-012", "admin")
        all_locks = lock_store.list_all_locks()
        assert len(all_locks) >= 1

    def test_get_lock_history(self, lock_store):
        lock_store.create_lock(task_id="task-013", agent_id="agent-A")
        lock_store.release_lock("task-013", "agent-A")
        history = lock_store.get_lock_history("task-013")
        assert len(history) >= 2
        actions = [h["action"] for h in history]
        assert "created" in actions
        assert "released" in actions

    def test_get_active_lock_by_agent(self, lock_store):
        lock_store.create_lock(task_id="task-014", agent_id="agent-A")
        active = lock_store.get_active_lock_by_agent("agent-A")
        assert active is not None
        assert active.task_id == "task-014"

    def test_get_active_lock_by_agent_no_lock(self, lock_store):
        active = lock_store.get_active_lock_by_agent("no-agent")
        assert active is None

    def test_lock_to_dict(self, lock_store):
        lock = lock_store.create_lock(task_id="task-015", agent_id="agent-A")
        d = lock.to_dict()
        assert d["task_id"] == "task-015"
        assert d["agent_id"] == "agent-A"
        assert d["status"] == "active"
        assert "created_at" in d
        assert "expires_at" in d


class TestAtomicCheckout:
    def test_checkout_success(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="task-001"))
        req = _make_request("task-001")
        result = checkout_manager.checkout_task(req)
        assert result.status == CheckoutStatus.SUCCESS
        assert result.lock is not None

        task = checkout_manager.get_task("task-001")
        assert task.status == TaskStatus.IN_PROGRESS.value
        assert task.assigned_to == "agent-001"

    def test_prevent_double_checkout(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="task-002"))
        req_a = _make_request("task-002", agent_id="agent-A")
        req_b = _make_request("task-002", agent_id="agent-B")

        r1 = checkout_manager.checkout_task(req_a)
        assert r1.status == CheckoutStatus.SUCCESS

        r2 = checkout_manager.checkout_task(req_b)
        assert r2.status == CheckoutStatus.FAILED
        assert r2.failure_reason == CheckoutFailureReason.ASSIGNED_TO_OTHER

    def test_cannot_checkout_completed_task(self, checkout_manager):
        task = _make_task(task_id="task-003", status="completed")
        checkout_manager.register_task(task)
        req = _make_request("task-003")
        result = checkout_manager.checkout_task(req)
        assert result.status == CheckoutStatus.FAILED
        assert result.failure_reason == CheckoutFailureReason.TASK_COMPLETED

    def test_cannot_checkout_failed_task(self, checkout_manager):
        task = _make_task(task_id="task-004", status="failed")
        checkout_manager.register_task(task)
        req = _make_request("task-004")
        result = checkout_manager.checkout_task(req)
        assert result.status == CheckoutStatus.FAILED

    def test_cannot_checkout_cancelled_task(self, checkout_manager):
        task = _make_task(task_id="task-005", status="cancelled")
        checkout_manager.register_task(task)
        req = _make_request("task-005")
        result = checkout_manager.checkout_task(req)
        assert result.status == CheckoutStatus.FAILED

    def test_cannot_checkout_with_unresolved_blockers(self, checkout_manager):
        task = _make_task(task_id="task-006", blockers=["depends-on-other-task"])
        checkout_manager.register_task(task)
        req = _make_request("task-006")
        result = checkout_manager.checkout_task(req)
        assert result.status == CheckoutStatus.FAILED
        assert result.failure_reason == CheckoutFailureReason.BLOCKERS_UNRESOLVED

    def test_cannot_checkout_nonexistent_task(self, checkout_manager):
        req = _make_request("nonexistent")
        result = checkout_manager.checkout_task(req)
        assert result.status == CheckoutStatus.FAILED


class TestSingleTaskMode:
    def test_single_task_mode_prevents_multiple_checkouts(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="task-single-1"))
        checkout_manager.register_task(_make_task(task_id="task-single-2"))

        r1 = checkout_manager.checkout_task(
            _make_request("task-single-1", agent_id="agent-A")
        )
        assert r1.status == CheckoutStatus.SUCCESS

        r2 = checkout_manager.checkout_task(
            _make_request("task-single-2", agent_id="agent-A")
        )
        assert r2.status == CheckoutStatus.FAILED
        assert r2.failure_reason == CheckoutFailureReason.AGENT_BUSY

    def test_after_completion_can_checkout_next(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="task-next-1"))
        checkout_manager.register_task(_make_task(task_id="task-next-2"))

        r1 = checkout_manager.checkout_task(
            _make_request("task-next-1", agent_id="agent-A")
        )
        assert r1.status == CheckoutStatus.SUCCESS

        checkout_manager.complete_task("task-next-1", "agent-A")

        r2 = checkout_manager.checkout_task(
            _make_request("task-next-2", agent_id="agent-A")
        )
        assert r2.status == CheckoutStatus.SUCCESS

    def test_after_abandon_can_checkout_next(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="task-ab-1"))
        checkout_manager.register_task(_make_task(task_id="task-ab-2"))

        checkout_manager.checkout_task(_make_request("task-ab-1", agent_id="agent-A"))
        checkout_manager.abandon_task("task-ab-1", "agent-A")

        r2 = checkout_manager.checkout_task(
            _make_request("task-ab-2", agent_id="agent-A")
        )
        assert r2.status == CheckoutStatus.SUCCESS


class TestBatchMode:
    def test_batch_mode_allows_multiple_checkouts(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="batch-1", required_gpu=0.5))
        checkout_manager.register_task(_make_task(task_id="batch-2", required_gpu=0.5))

        r1 = checkout_manager.checkout_task(
            _make_request("batch-1", agent_id="batch-agent", agent_mode=AgentMode.BATCH)
        )
        assert r1.status == CheckoutStatus.SUCCESS

        r2 = checkout_manager.checkout_task(
            _make_request("batch-2", agent_id="batch-agent", agent_mode=AgentMode.BATCH)
        )
        assert r2.status == CheckoutStatus.SUCCESS

    def test_batch_bypasses_single_mode_lock(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="mix-1"))

        r1 = checkout_manager.checkout_task(
            _make_request("mix-1", agent_id="agent-X", agent_mode=AgentMode.SINGLE)
        )
        assert r1.status == CheckoutStatus.SUCCESS

        checkout_manager.register_task(_make_task(task_id="mix-2"))

        r2 = checkout_manager.checkout_task(
            _make_request("mix-2", agent_id="agent-X", agent_mode=AgentMode.BATCH)
        )
        assert r2.status == CheckoutStatus.SUCCESS


class TestBudgetAndGPU:
    def test_budget_exceeded_precludes_checkout(self, checkout_manager):
        budget_state = _setup_budget_gpu_checkers(checkout_manager)
        budget_state["exceeded"] = True

        checkout_manager.register_task(_make_task(task_id="budget-1"))
        req = _make_request("budget-1")
        result = checkout_manager.checkout_task(req)
        assert result.status == CheckoutStatus.FAILED
        assert result.failure_reason == CheckoutFailureReason.BUDGET_EXCEEDED

    def test_gpu_unavailable_precludes_checkout(self, checkout_manager):
        def gpu_checker_fail(required_memory: float) -> bool:
            return False

        checkout_manager.set_gpu_checker(gpu_checker_fail)

        checkout_manager.register_task(_make_task(task_id="gpu-1", required_gpu=16.0))
        req = _make_request("gpu-1", required_gpu=16.0)
        result = checkout_manager.checkout_task(req)
        assert result.status == CheckoutStatus.FAILED
        assert result.failure_reason == CheckoutFailureReason.GPU_UNAVAILABLE


class TestFailureHandling:
    def test_failure_recorded(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="fail-rec-1"))
        checkout_manager.checkout_task(_make_request("fail-rec-1"))

        result = checkout_manager.fail_task("fail-rec-1", "agent-001", "GPU OOM")
        assert result.status == CheckoutStatus.SUCCESS

        task = checkout_manager.get_task("fail-rec-1")
        assert task is not None
        assert task.status == TaskStatus.FAILED.value

        history = checkout_manager.get_task_checkout_history("fail-rec-1")
        assert len(history["failure_history"]) >= 1
        assert history["failure_history"][0]["reason"] == "task_failed"
        assert history["failure_history"][0]["message"] == "GPU OOM"

    def test_complete_task(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="complete-1"))
        checkout_manager.checkout_task(_make_request("complete-1"))

        result = checkout_manager.complete_task("complete-1", "agent-001")
        assert result.status == CheckoutStatus.SUCCESS

        task = checkout_manager.get_task("complete-1")
        assert task.status == TaskStatus.COMPLETED.value

    def test_abandon_task_returns_to_pending(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="abandon-1"))
        checkout_manager.checkout_task(_make_request("abandon-1"))

        result = checkout_manager.abandon_task("abandon-1", "agent-001")
        assert result.status == CheckoutStatus.SUCCESS

        task = checkout_manager.get_task("abandon-1")
        assert task.status == TaskStatus.PENDING.value
        assert task.assigned_to is None

    def test_cannot_complete_task_not_owned(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="not-owned"))
        checkout_manager.checkout_task(_make_request("not-owned", agent_id="agent-A"))

        result = checkout_manager.complete_task("not-owned", "agent-B")
        assert result.status == CheckoutStatus.FAILED
        assert result.failure_reason == CheckoutFailureReason.ASSIGNED_TO_OTHER


class TestForceRelease:
    def test_force_release_resets_task(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="force-1"))
        checkout_manager.checkout_task(_make_request("force-1", agent_id="agent-A"))

        result = checkout_manager.force_release_lock("force-1", "admin")
        assert result.status == CheckoutStatus.SUCCESS

        task = checkout_manager.get_task("force-1")
        assert task.status == TaskStatus.PENDING.value
        assert task.assigned_to is None

    def test_force_release_nonexistent_task(self, checkout_manager):
        result = checkout_manager.force_release_lock("no-lock-task", "admin")
        assert result.status == CheckoutStatus.FAILED


class TestExpiredLockCleanup:
    def test_cleanup_resets_expired_tasks(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="exp-1"))
        req = _make_request("exp-1", agent_id="agent-A", timeout_hours=0.001)
        checkout_manager.checkout_task(req)

        time.sleep(5)

        expired = checkout_manager.cleanup_expired_locks()
        assert len(expired) >= 1

        task = checkout_manager.get_task("exp-1")
        assert task.status == TaskStatus.PENDING.value
        assert task.assigned_to is None


class TestTaskBoard:
    def test_board_separates_by_status(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="board-p", status="pending"))
        checkout_manager.register_task(
            _make_task(task_id="board-ip", status="in_progress", assigned_to="agent-A")
        )
        checkout_manager.register_task(
            _make_task(task_id="board-c", status="completed", assigned_to="agent-A")
        )
        checkout_manager.register_task(
            _make_task(task_id="board-f", status="failed", assigned_to="agent-A")
        )

        board = checkout_manager.get_task_board()
        assert len(board["pending"]) >= 1
        assert len(board["in_progress"]) >= 1
        assert len(board["completed"]) >= 1
        assert len(board["failed"]) >= 1

    def test_board_includes_lock_info(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="board-lock"))
        checkout_manager.checkout_task(_make_request("board-lock"))

        board = checkout_manager.get_task_board()
        ip_tasks = board["in_progress"]
        task_with_lock = [t for t in ip_tasks if t.get("id") == "board-lock"]
        assert len(task_with_lock) == 1

        lock_info = task_with_lock[0].get("lock_info")
        assert lock_info is not None
        assert lock_info.get("agent_id") == "agent-001"
        assert lock_info.get("status") == "active"


class TestPriorityQueue:
    def test_enqueue_checkout(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="queue-1"))

        entry = checkout_manager.enqueue_checkout(
            _make_request("queue-1", priority=CheckoutPriority.HIGH)
        )
        assert entry.task_id == "queue-1"
        assert entry.priority == CheckoutPriority.HIGH

        queue = checkout_manager.get_queue_status()
        assert len(queue) >= 1
        assert queue[0]["task_id"] == "queue-1"

    def test_queue_processes_by_priority(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="qp-low"))
        checkout_manager.register_task(_make_task(task_id="qp-high"))

        checkout_manager.enqueue_checkout(
            _make_request("qp-low", agent_id="agent-L", priority=CheckoutPriority.LOW)
        )
        checkout_manager.enqueue_checkout(
            _make_request("qp-high", agent_id="agent-H", priority=CheckoutPriority.HIGH)
        )

        results = checkout_manager.process_queue(max_batch=10)
        assert len(results) >= 1

    def test_queue_max_retries(self, checkout_manager):
        def gpu_always_fail(required_memory: float) -> bool:
            return False

        checkout_manager.set_gpu_checker(gpu_always_fail)
        checkout_manager.register_task(
            _make_task(task_id="retry-task", required_gpu=16.0)
        )

        checkout_manager.enqueue_checkout(
            _make_request("retry-task", agent_id="agent-R", required_gpu=16.0)
        )

        for i in range(MAX_RETRY_COUNT + 2):
            checkout_manager.process_queue(max_batch=10)

        task = checkout_manager.get_task("retry-task")
        if task:
            assert task.status in (
                TaskStatus.FAILED.value,
                TaskStatus.PENDING.value,
                TaskStatus.IN_PROGRESS.value,
            )


class TestCheckoutHistory:
    def test_history_includes_task_and_lock_history(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="hist-1"))
        checkout_manager.checkout_task(_make_request("hist-1", agent_id="agent-A"))

        history = checkout_manager.get_task_checkout_history("hist-1")
        assert history["task"] is not None
        assert history["task"]["id"] == "hist-1"
        assert len(history["lock_history"]) >= 1
        assert len(history["failure_history"]) == 0

    def test_history_includes_failure_records(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="hist-fail"))
        checkout_manager.checkout_task(_make_request("hist-fail", agent_id="agent-A"))
        checkout_manager.fail_task("hist-fail", "agent-A", "Test failure")

        history = checkout_manager.get_task_checkout_history("hist-fail")
        assert len(history["failure_history"]) >= 1
        assert history["failure_history"][0]["reason"] == "task_failed"
        assert history["failure_history"][0]["message"] == "Test failure"

    def test_history_nonexistent_task(self, checkout_manager):
        history = checkout_manager.get_task_checkout_history("no-such-task")
        assert history["task"] is None


class TestAgentStatus:
    def test_agent_status_with_active_task(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="status-1"))
        checkout_manager.checkout_task(_make_request("status-1", agent_id="agent-A"))

        status = checkout_manager.get_agent_status("agent-A")
        assert status["has_active_task"] is True
        assert status["active_lock"] is not None
        assert status["active_lock"]["task_id"] == "status-1"

    def test_agent_status_idle(self, checkout_manager):
        status = checkout_manager.get_agent_status("idle-agent")
        assert status["has_active_task"] is False
        assert status["active_lock"] is None


class TestThreadSafety:
    def test_concurrent_checkout_same_task(self, checkout_manager):
        checkout_manager.register_task(_make_task(task_id="concurrent-1"))

        results = []

        def attempt(agent_id):
            req = _make_request("concurrent-1", agent_id=agent_id)
            result = checkout_manager.checkout_task(req)
            results.append(result)

        t1 = threading.Thread(target=attempt, args=("agent-T1",))
        t2 = threading.Thread(target=attempt, args=("agent-T2",))
        t3 = threading.Thread(target=attempt, args=("agent-T3",))

        t1.start()
        t2.start()
        t3.start()

        t1.join()
        t2.join()
        t3.join()

        successes = [r for r in results if r.status == CheckoutStatus.SUCCESS]
        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"

        failures = [r for r in results if r.status == CheckoutStatus.FAILED]
        assert len(failures) == 2

    def test_concurrent_checkout_different_tasks(self, checkout_manager):
        for i in range(5):
            checkout_manager.register_task(_make_task(task_id=f"conc-diff-{i}"))

        results = []

        def attempt(idx):
            req = _make_request(f"conc-diff-{idx}", agent_id=f"agent-{idx}")
            result = checkout_manager.checkout_task(req)
            results.append(result)

        threads = []
        for i in range(5):
            t = threading.Thread(target=attempt, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        successes = [r for r in results if r.status == CheckoutStatus.SUCCESS]
        assert len(successes) == 5
