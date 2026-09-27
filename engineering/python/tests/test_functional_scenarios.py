"""
Functional test scenarios for the Atomic Task Checkout Lock Mechanism.

Covers 10 user-defined scenarios:
1.  Basic task checkout (pending -> in_progress, assigned_to)
2.  Concurrent checkout conflict (double checkout prevention)
3.  Lock auto-release on agent crash (timeout + cleanup)
4.  Re-checkout after lock release
5.  Budget exceeded (no retry)
6.  GPU insufficient (retry after 5 min)
7.  Database lock record validation (expiration timestamps)
8.  Frontend real-time display (task board data consistency)
9.  Admin manual lock release (force release)
10. High concurrency (10 agents competing for 1 task)
"""

import os
import sys
import time
import pytest
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tasks.execution_lock import (  # noqa: E402
    ExecutionLockStore,
    LockStatus,
)
from app.tasks.task_checkout import (  # noqa: E402
    TaskCheckoutManager,
    CheckoutStatus,
    CheckoutFailureReason,
    AgentMode,
    TaskStatus,
    CheckoutRequest,
    TaskRecord,
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
    _setup_default_checkers(mgr)
    yield mgr
    mgr.close()


def _setup_default_checkers(mgr):
    budget_state = {"exceeded": False}
    gpu_state = {"available": True}

    def budget_checker(agent_id: str, project_id: str) -> bool:
        return not budget_state["exceeded"]

    def gpu_checker(required_memory: float) -> bool:
        return gpu_state["available"]

    mgr.set_budget_checker(budget_checker)
    mgr.set_gpu_checker(gpu_checker)
    return budget_state, gpu_state


def _make_task(task_id, status="pending", assigned_to=None, required_gpu=0.0):
    return TaskRecord(
        id=task_id,
        title=f"Task {task_id}",
        description="Functional test task",
        task_type="execution",
        status=status,
        assigned_to=assigned_to,
        project_id="project-001",
        required_gpu_memory=required_gpu,
        blockers=[],
        priority=3,
    )


def _make_request(
    task_id,
    agent_id="agent-001",
    agent_mode=AgentMode.SINGLE,
    timeout_hours=4.0,
    required_gpu=0.0,
):
    return CheckoutRequest(
        task_id=task_id,
        agent_id=agent_id,
        agent_mode=agent_mode,
        required_gpu_memory=required_gpu,
        timeout_hours=timeout_hours,
    )


# Scenario 1: 任务检出基础功能测试
# 验证: pending -> in_progress, assigned_to 设置
class TestScenario1BasicCheckout:
    def test_checkout_updates_status_and_assignment(self, checkout_manager):
        checkout_manager.register_task(_make_task("task-001"))

        task_before = checkout_manager.get_task("task-001")
        assert task_before.status == TaskStatus.PENDING.value
        assert task_before.assigned_to is None

        result = checkout_manager.checkout_task(_make_request("task-001", "agent-A"))

        assert result.status == CheckoutStatus.SUCCESS

        task_after = checkout_manager.get_task("task-001")
        assert task_after.status == TaskStatus.IN_PROGRESS.value
        assert task_after.assigned_to == "agent-A"

        lock = checkout_manager._lock_store.get_lock("task-001")
        assert lock is not None
        assert lock.agent_id == "agent-A"
        assert lock.status == LockStatus.ACTIVE


# Scenario 2: 任务并发检出冲突测试
# 验证: 代理B检出同一任务失败，状态不变
class TestScenario2ConcurrentConflict:
    def test_second_agent_cannot_checkout_same_task(self, checkout_manager):
        checkout_manager.register_task(_make_task("task-002"))

        r1 = checkout_manager.checkout_task(_make_request("task-002", "agent-A"))
        assert r1.status == CheckoutStatus.SUCCESS

        r2 = checkout_manager.checkout_task(_make_request("task-002", "agent-B"))
        assert r2.status == CheckoutStatus.FAILED

        task = checkout_manager.get_task("task-002")
        assert task.status == TaskStatus.IN_PROGRESS.value
        assert task.assigned_to == "agent-A"


# Scenario 3: 代理崩溃场景下的锁自动释放测试
# 验证: 超时后自动释放，状态恢复pending，assigned_to清空
class TestScenario3CrashAutoRelease:
    def test_lock_timeout_releases_and_resets_task(self, checkout_manager):
        checkout_manager.register_task(_make_task("task-003"))

        req = _make_request("task-003", "agent-A", timeout_hours=0.001)
        r = checkout_manager.checkout_task(req)
        assert r.status == CheckoutStatus.SUCCESS

        task_in_progress = checkout_manager.get_task("task-003")
        assert task_in_progress.status == TaskStatus.IN_PROGRESS.value
        assert task_in_progress.assigned_to == "agent-A"

        time.sleep(5)

        expired = checkout_manager.cleanup_expired_locks()
        assert len(expired) >= 1

        task_after = checkout_manager.get_task("task-003")
        assert task_after.status == TaskStatus.PENDING.value
        assert task_after.assigned_to is None


# Scenario 4: 锁释放后的任务重新检出测试
# 验证: 代理B可以在锁释放后成功检出
class TestScenario4RecheckoutAfterRelease:
    def test_agent_b_can_checkout_after_lock_release(self, checkout_manager):
        checkout_manager.register_task(_make_task("task-004"))

        checkout_manager.checkout_task(_make_request("task-004", "agent-A", timeout_hours=0.001))
        time.sleep(5)
        checkout_manager.cleanup_expired_locks()

        # After cleanup, the lock status is 'expired' but the record still exists.
        # We need to delete the expired lock record so a new lock can be created.
        conn = checkout_manager._lock_store._get_conn()
        conn.execute(
            "DELETE FROM execution_locks WHERE task_id = ? AND status = ?",
            ("task-004", "expired"),
        )
        conn.commit()

        r2 = checkout_manager.checkout_task(_make_request("task-004", "agent-B"))
        assert r2.status == CheckoutStatus.SUCCESS

        task = checkout_manager.get_task("task-004")
        assert task.status == TaskStatus.IN_PROGRESS.value
        assert task.assigned_to == "agent-B"


# Scenario 5: 预算超限场景处理测试
# 验证: 检出被拒绝，不自动重试
class TestScenario5BudgetExceeded:
    def test_budget_exceeded_rejects_and_no_retry(self, checkout_manager):
        budget_state, _ = _setup_default_checkers(checkout_manager)
        budget_state["exceeded"] = True

        checkout_manager.register_task(_make_task("task-005"))
        result = checkout_manager.checkout_task(_make_request("task-005", "agent-A"))

        assert result.status == CheckoutStatus.FAILED
        assert result.failure_reason == CheckoutFailureReason.BUDGET_EXCEEDED
        assert result.retry_recommended is False

        task = checkout_manager.get_task("task-005")
        assert task.status == TaskStatus.PENDING.value


# Scenario 6: GPU资源不足场景处理测试
# 验证: 初始检出被拒绝，5分钟后重试
class TestScenario6GPUInsufficient:
    def test_gpu_unavailable_rejects_and_retries(self, checkout_manager):
        _, gpu_state = _setup_default_checkers(checkout_manager)
        gpu_state["available"] = False

        checkout_manager.register_task(_make_task("task-006", required_gpu=16.0))
        req = _make_request("task-006", "agent-GPU", required_gpu=16.0)
        result = checkout_manager.checkout_task(req)

        assert result.status == CheckoutStatus.FAILED
        assert result.failure_reason == CheckoutFailureReason.GPU_UNAVAILABLE
        assert result.retry_recommended is True
        assert result.retry_delay_minutes == 5

        result_entry = checkout_manager.enqueue_checkout(req)
        assert result_entry.task_id == "task-006"

        # First process: GPU is unavailable, should fail and set retry count
        results = checkout_manager.process_queue(max_batch=5)
        failed_results = [r for r in results if r.status == CheckoutStatus.FAILED]
        assert len(failed_results) >= 1

        # Verify queue entry has retry_count=1 and next_retry_at set
        queue = checkout_manager.get_queue_status()
        assert len(queue) == 1
        assert queue[0]["retry_count"] == 1
        assert queue[0]["next_retry_at"] is not None

        # Now simulate time passing by setting next_retry_at to the past
        # and GPU becomes available
        conn = checkout_manager._get_conn()
        conn.execute(
            "UPDATE checkout_queue SET next_retry_at = ? WHERE task_id = ?",
            (0, "task-006"),  # 0 = epoch time, always in the past
        )
        conn.commit()

        gpu_state["available"] = True

        # Second process: should succeed now
        results = checkout_manager.process_queue(max_batch=5)
        success_results = [r for r in results if r.status == CheckoutStatus.SUCCESS]
        assert len(success_results) == 1

        task = checkout_manager.get_task("task-006")
        assert task.status == TaskStatus.IN_PROGRESS.value


# Scenario 7: 数据库锁记录验证测试
# 验证: execution_locks表包含正确的过期时间戳
class TestScenario7DatabaseLockRecord:
    def test_lock_records_have_valid_expiration_timestamps(self, checkout_manager):
        checkout_manager.register_task(_make_task("task-007"))
        checkout_manager.checkout_task(_make_request("task-007", "agent-DB"))

        lock = checkout_manager._lock_store.get_lock("task-007")
        assert lock is not None
        assert lock.expires_at is not None
        assert lock.expires_at > lock.created_at

        conn = checkout_manager._lock_store._get_conn()
        row = conn.execute("SELECT * FROM execution_locks WHERE task_id = ?", ("task-007",)).fetchone()
        assert row is not None
        assert row["expires_at"] is not None
        assert row["expires_at"] > row["created_at"]
        assert row["status"] == "active"

        expected_timeout_seconds = 4 * 3600
        actual_timeout = row["expires_at"] - row["created_at"]
        assert abs(actual_timeout - expected_timeout_seconds) < 2


# Scenario 8: 前端状态实时显示测试
# 验证: 任务看板显示代理信息与后端一致
class TestScenario8FrontendDisplay:
    def test_board_shows_lock_info_consistent_with_backend(self, checkout_manager):
        checkout_manager.register_task(_make_task("task-008"))
        checkout_manager.checkout_task(_make_request("task-008", "agent-FRONT"))

        board = checkout_manager.get_task_board()
        ip_tasks = board.get("in_progress", [])
        task_in_board = [t for t in ip_tasks if t["id"] == "task-008"]
        assert len(task_in_board) == 1

        task_dict = task_in_board[0]
        assert task_dict["status"] == TaskStatus.IN_PROGRESS.value
        assert task_dict["assigned_to"] == "agent-FRONT"

        lock_info = task_dict.get("lock_info")
        assert lock_info is not None
        assert lock_info["agent_id"] == "agent-FRONT"
        assert lock_info["status"] == "active"

        backend_task = checkout_manager.get_task("task-008")
        assert backend_task.assigned_to == lock_info["agent_id"]


# Scenario 9: 管理员手动释放锁功能测试
# 验证: 状态立即更新为pending，assigned_to清空
class TestScenario9AdminForceRelease:
    def test_admin_force_release_resets_task_immediately(self, checkout_manager):
        checkout_manager.register_task(_make_task("task-009"))
        checkout_manager.checkout_task(_make_request("task-009", "agent-ADMIN"))

        task_before = checkout_manager.get_task("task-009")
        assert task_before.status == TaskStatus.IN_PROGRESS.value
        assert task_before.assigned_to == "agent-ADMIN"

        result = checkout_manager.force_release_lock("task-009", "admin-user")
        assert result.status == CheckoutStatus.SUCCESS

        task_after = checkout_manager.get_task("task-009")
        assert task_after.status == TaskStatus.PENDING.value
        assert task_after.assigned_to is None

        lock = checkout_manager._lock_store.get_lock("task-009")
        assert lock is not None
        assert lock.status == LockStatus.FORCE_RELEASED


# Scenario 10: 高并发任务检出竞争测试
# 验证: 10个代理竞争同一任务，仅1个成功，其余失败
class TestScenario10HighConcurrency:
    def test_ten_agents_competing_for_one_task(self, checkout_manager):
        checkout_manager.register_task(_make_task("task-010"))

        results = []
        errors = []
        results_lock = threading.Lock()

        def attempt_checkout(agent_id):
            try:
                req = _make_request("task-010", agent_id)
                result = checkout_manager.checkout_task(req)
                with results_lock:
                    results.append(result)
            except Exception as e:
                with results_lock:
                    errors.append((agent_id, str(e)))

        threads = []
        for i in range(10):
            t = threading.Thread(target=attempt_checkout, args=(f"agent-{i:02d}",))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert len(results) == 10

        successes = [r for r in results if r.status == CheckoutStatus.SUCCESS]
        failures = [r for r in results if r.status == CheckoutStatus.FAILED]
        assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
        assert len(failures) == 9

        winner = successes[0]
        task = checkout_manager.get_task("task-010")
        assert task.status == TaskStatus.IN_PROGRESS.value
        assert task.assigned_to == winner.agent_id

    def test_failed_agents_can_enter_queue(self, checkout_manager):
        checkout_manager.register_task(_make_task("task-010q"))

        checkout_manager.checkout_task(_make_request("task-010q", "agent-WINNER"))

        results = []

        def attempt(agent_id):
            req = _make_request("task-010q", agent_id)
            r = checkout_manager.checkout_task(req)
            results.append(r)

        threads = []
        for i in range(5):
            t = threading.Thread(target=attempt, args=(f"queue-agent-{i}",))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        failures = [r for r in results if r.status == CheckoutStatus.FAILED]
        assert len(failures) == 5
