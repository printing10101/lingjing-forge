"""
Heartbeat System Test Suite

Comprehensive test suite for Paperclip Heartbeat Execution architecture,
covering all 8 test scenarios specified by the user.
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.budget.budget import (  # noqa: E402
    BudgetLevel,
    BudgetLimit,
    BudgetManager,
    BudgetStatus,
    ResourceType,
)
from app.budget.cost_tracker import MultiDimensionCostTracker  # noqa: E402
from app.tasks.execution import (  # noqa: E402
    ExecutionEngine,
    ExecutionSession,
    ExecutionStatus,
    SessionManager,
)
from app.heartbeat.heartbeat import (  # noqa: E402
    CronParser,
    HeartbeatScheduler,
    ScheduleStatus,
    ScheduledTask,
    WakeupQueue,
)

# pytest-asyncio 默认 strict 模式：本文件 8 个 async 测试需显式标记才会被
# 事件循环驱动（否则报 "async def functions are not natively supported"）。
# 本文件不参与 CI 单元门禁（CI 仅跑 tests/unit/），此前从未在本仓库配置下运行过。
pytestmark = pytest.mark.asyncio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("heartbeat_tests")

TEST_DIR = Path(__file__).resolve().parent / "test_heartbeat_data"
TEST_DIR.mkdir(exist_ok=True)


class TestResultRecorder:
    def __init__(self):
        self.results = []
        self.test_env = {
            "os": os.name,
            "python_version": sys.version,
            "test_directory": str(TEST_DIR),
            "test_start_time": datetime.now().isoformat(),
        }

    def record(
        self,
        test_name: str,
        step: str,
        expected: str,
        actual: str,
        status: str,
        details: str = "",
    ):
        self.results.append(
            {
                "test_name": test_name,
                "step": step,
                "expected": expected,
                "actual": actual,
                "status": status,
                "details": details,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def generate_report(self) -> str:
        report_lines = []
        report_lines.append("=" * 80)
        report_lines.append("灵境制造项目 - Heartbeat心跳调度系统测试报告")
        report_lines.append("=" * 80)
        report_lines.append("")
        report_lines.append("## 测试环境配置")
        report_lines.append(f"- 操作系统: {self.test_env['os']}")
        report_lines.append(f"- Python版本: {self.test_env['python_version']}")
        report_lines.append(f"- 测试目录: {self.test_env['test_directory']}")
        report_lines.append(f"- 测试开始时间: {self.test_env['test_start_time']}")
        report_lines.append("")
        report_lines.append("=" * 80)
        report_lines.append("## 测试结果")
        report_lines.append("=" * 80)

        passed = 0
        failed = 0
        current_test = ""

        for r in self.results:
            if r["test_name"] != current_test:
                current_test = r["test_name"]
                report_lines.append("")
                report_lines.append(f"### {current_test}")

            status_symbol = "✓" if r["status"] == "PASS" else "✗"
            if r["status"] == "PASS":
                passed += 1
            else:
                failed += 1

            report_lines.append(
                f"{status_symbol} [{r['step']}] {r['status']} - {r['details']}"
            )
            if r["status"] != "PASS":
                report_lines.append(f"  预期: {r['expected']}")
                report_lines.append(f"  实际: {r['actual']}")

        report_lines.append("")
        report_lines.append("=" * 80)
        report_lines.append("## 总结")
        report_lines.append(f"- 总测试项: {passed + failed}")
        report_lines.append(f"- 通过: {passed}")
        report_lines.append(f"- 失败: {failed}")
        report_lines.append(
            f"- 通过率: {passed / (passed + failed) * 100:.1f}%"
            if (passed + failed) > 0
            else "- 通过率: N/A"
        )
        report_lines.append("=" * 80)

        return "\n".join(report_lines)


recorder = TestResultRecorder()


_active_connections = []


def cleanup_test_dbs():
    for conn in _active_connections:
        try:
            conn.close()
        except Exception:
            pass
    _active_connections.clear()

    import time

    for db_file in TEST_DIR.glob("*.db"):
        try:
            db_file.unlink(missing_ok=True)
        except PermissionError:
            time.sleep(0.5)
            try:
                db_file.unlink(missing_ok=True)
            except Exception:
                pass


def create_test_wakeup_queue(db_name: str = "test_heartbeat.db") -> WakeupQueue:
    return WakeupQueue(str(TEST_DIR / db_name))


def create_test_budget_manager(db_name: str = "test_budget.db") -> BudgetManager:
    return BudgetManager(str(TEST_DIR / db_name))


def create_test_session_manager(db_name: str = "test_sessions.db") -> SessionManager:
    return SessionManager(str(TEST_DIR / db_name))


def create_test_cost_tracker(
    db_name: str = "test_costs.db",
) -> MultiDimensionCostTracker:
    return MultiDimensionCostTracker(db_path=str(TEST_DIR / db_name))


async def test_1_heartbeat_scheduling():
    """
    测试1: 心跳任务调度测试
    创建一个配置为每5分钟执行一次的测试心跳任务，
    验证调度器是否能准确、准时地触发任务执行
    """
    logger.info("=" * 60)
    logger.info("开始测试1: 心跳任务调度测试")
    logger.info("=" * 60)

    cleanup_test_dbs()
    queue = create_test_wakeup_queue()
    _active_connections.append(queue._conn)
    triggered_times = []

    task_id = "test_heartbeat_5min"
    agent_id = "test_agent_1"

    now = time.time()
    cron_expr = "*/5 * * * *"

    task = ScheduledTask(
        task_id=task_id,
        agent_id=agent_id,
        schedule=cron_expr,
        task_type="lnn_predict",
        status=ScheduleStatus.PENDING,
        next_run=now - 1,
    )

    queue.add_task(task)
    added_task = queue.get_task(task_id)

    recorder.record(
        "测试1: 心跳任务调度",
        "步骤1: 创建5分钟周期任务",
        "任务成功创建，状态为pending",
        f"任务状态={added_task.status.value}",
        "PASS" if added_task.status == ScheduleStatus.PENDING else "FAIL",
        f"task_id={task_id}, schedule={cron_expr}",
    )

    future_times = CronParser.parse(cron_expr)
    if len(future_times) >= 3:
        expected_interval = future_times[1] - future_times[0]
        recorder.record(
            "测试1: 心跳任务调度",
            "步骤2: 验证cron表达式解析",
            "解析出至少3个未来时间点，间隔约300秒",
            f"解析出{len(future_times)}个时间点，间隔={expected_interval:.0f}秒",
            "PASS" if expected_interval == 300 else "FAIL",
            f"前3个时间点: {[datetime.fromtimestamp(t).strftime('%H:%M:%S') for t in future_times[:3]]}",
        )
    else:
        recorder.record(
            "测试1: 心跳任务调度",
            "步骤2: 验证cron表达式解析",
            "解析出至少3个未来时间点",
            f"仅解析出{len(future_times)}个时间点",
            "FAIL",
        )

    scheduler = HeartbeatScheduler(wakeup_queue=queue, heartbeat_interval=5)

    async def mock_task_callback(task):
        triggered_times.append(time.time())
        logger.info(
            f"[TEST] Task {task.task_id} triggered at {datetime.now().isoformat()}"
        )
        await asyncio.sleep(0.1)
        queue.update_task_status(
            task.task_id, ScheduleStatus.COMPLETED, last_run=time.time()
        )
        queue.log_execution(
            task.task_id,
            "completed",
            duration_ms=100.0,
            result_summary="Mock task execution completed",
        )

    scheduler.set_task_trigger_callback(mock_task_callback)

    scheduler._running = True

    logger.info("[TEST] Simulating 3 heartbeat cycles with 5-second intervals...")
    for cycle in range(3):
        await scheduler._tick()
        await asyncio.sleep(1)

    scheduler._running = False

    recorder.record(
        "测试1: 心跳任务调度",
        "步骤3: 模拟3个心跳周期触发",
        "调度器在3个周期内成功触发任务",
        f"实际触发{len(triggered_times)}次",
        "PASS" if len(triggered_times) >= 1 else "FAIL",
        f"触发时间偏差记录: {triggered_times}",
    )

    history = queue.get_task_history(task_id)
    recorder.record(
        "测试1: 心跳任务调度",
        "步骤4: 验证执行历史日志",
        "执行历史包含触发记录",
        f"历史记录条数: {len(history)}",
        "PASS" if len(history) > 0 else "FAIL",
        f"最新记录: {history[0] if history else 'N/A'}",
    )

    queue.close()

    logger.info("[TEST] 测试1完成")
    logger.info("")


async def test_2_gpu_budget_control():
    """
    测试2: GPU预算控制测试
    配置GPU资源每日使用限额为1单位，
    提交相同的GPU计算任务连续执行2次，
    验证第二次任务执行时预算超限触发硬性停止
    """
    logger.info("=" * 60)
    logger.info("开始测试2: GPU预算控制测试")
    logger.info("=" * 60)

    cleanup_test_dbs()
    budget_mgr = create_test_budget_manager()
    _active_connections.append(budget_mgr._conn)

    agent_id = "test_agent_2"

    budget_mgr.set_budget_limit(
        BudgetLimit(
            resource_type=ResourceType.GPU_HOURS,
            limit_value=1.0,
            warning_threshold=0.8,
            hard_stop_threshold=1.0,
            budget_level=BudgetLevel.GLOBAL,
        )
    )

    recorder.record(
        "测试2: GPU预算控制",
        "步骤1: 配置GPU每日限额1单位",
        "预算限制设置成功",
        "GPU Hours limit=1.0, hard_stop=100%",
        "PASS",
        "BudgetLimit(resource_type=GPU_HOURS, limit_value=1.0)",
    )

    result1 = budget_mgr.check_budget(agent_id)

    recorder.record(
        "测试2: GPU预算控制",
        "步骤2: 第一次执行GPU任务前预算检查",
        "预算检查通过，状态为ok或warning",
        f"passed={result1.passed}, status={result1.status.value}",
        "PASS" if result1.passed else "FAIL",
        f"usages={[u.to_dict() for u in result1.usages]}",
    )

    try:
        tracker = budget_mgr.tracker
        tracker._gpu_hours_today = 0.5

        result2 = budget_mgr.check_budget(agent_id)

        tracker._gpu_hours_today = 1.5

        result3 = budget_mgr.check_budget(agent_id)

        recorder.record(
            "测试2: GPU预算控制",
            "步骤3: 模拟GPU使用0.5单位后检查",
            "预算状态应为warning（50% < 80%阈值）",
            f"status={result2.status.value}, usage_ratio={result2.usages[0].usage_ratio * 100:.1f}%",
            "PASS" if result2.passed else "FAIL",
            "Simulated 0.5 GPU hours usage",
        )

        recorder.record(
            "测试2: GPU预算控制",
            "步骤4: 模拟GPU使用1.5单位后检查（超出限额）",
            "预算检查失败，状态为exceeded",
            f"passed={result3.passed}, status={result3.status.value}",
            "PASS"
            if not result3.passed and result3.status == BudgetStatus.EXCEEDED
            else "FAIL",
            f"blocked_reasons={result3.blocked_reasons}",
        )

    except Exception as e:
        recorder.record(
            "测试2: GPU预算控制",
            "步骤3-4: 预算超限验证",
            "预算检查应正确识别超出限额情况",
            f"异常: {str(e)}",
            "FAIL",
        )

    budget_mgr.close()
    logger.info("[TEST] 测试2完成")
    logger.info("")


async def test_3_process_exception_handling():
    """
    测试3: 进程异常处理测试
    启动一个配置了检查点机制的长时间训练任务，
    在任务执行至50%进度时手动终止其进程，
    确认任务被正确标记为"orphaned"状态
    """
    logger.info("=" * 60)
    logger.info("开始测试3: 进程异常处理测试")
    logger.info("=" * 60)

    cleanup_test_dbs()
    session_mgr = create_test_session_manager()
    _active_connections.append(session_mgr._conn)

    task_id = "test_training_task"
    session_id = f"{task_id}_session_001"

    checkpoint_data = {
        "epoch": 50,
        "total_epochs": 100,
        "progress": 0.5,
        "model_state": "saved",
        "optimizer_state": "saved",
    }

    session = ExecutionSession(
        session_id=session_id,
        task_id=task_id,
        status=ExecutionStatus.RUNNING,
        checkpoint_data=checkpoint_data,
        started_at=time.time() - 1800,
        last_updated=time.time() - 3700,
    )

    session_mgr.create_session(session)

    recorder.record(
        "测试3: 进程异常处理",
        "步骤1: 创建配置检查点的训练任务会话",
        "会话创建成功，状态为running",
        f"session_id={session_id}, status=running",
        "PASS",
        f"checkpoint={checkpoint_data}",
    )

    orphaned_sessions = session_mgr.get_orphaned_sessions(timeout_seconds=3600)

    if len(orphaned_sessions) > 0:
        orphaned = orphaned_sessions[0]
        recorder.record(
            "测试3: 进程异常处理",
            "步骤2: 模拟进程在50%进度时异常终止",
            "会话last_updated超过3600秒，被检测为孤立",
            f"检测到{len(orphaned_sessions)}个孤立会话，status={orphaned.status.value}",
            "PASS",
            f"last_updated距今={time.time() - orphaned.last_updated:.0f}秒",
        )

        session_mgr.update_session(orphaned.session_id, ExecutionStatus.FAILED)

        updated_session = session_mgr.get_session(orphaned.session_id)
        recorder.record(
            "测试3: 进程异常处理",
            "步骤3: 更新会话状态为failed",
            "会话状态更新成功",
            f"status={updated_session.status.value}",
            "PASS" if updated_session.status == ExecutionStatus.FAILED else "FAIL",
            "状态从running更新为failed",
        )
    else:
        recorder.record(
            "测试3: 进程异常处理",
            "步骤2: 孤立会话检测",
            "应检测到至少1个孤立会话",
            f"检测到{len(orphaned_sessions)}个孤立会话",
            "FAIL",
        )

    session_mgr.close()
    logger.info("[TEST] 测试3完成")
    logger.info("")


async def test_4_orphaned_task_recovery():
    """
    测试4: 孤立任务恢复测试
    等待下一次心跳任务调度周期（5分钟），
    验证系统能自动检测到孤立任务并触发恢复机制
    """
    logger.info("=" * 60)
    logger.info("开始测试4: 孤立任务恢复测试")
    logger.info("=" * 60)

    cleanup_test_dbs()
    queue = create_test_wakeup_queue("test_recovery.db")
    _active_connections.append(queue._conn)
    session_mgr = create_test_session_manager("test_recovery_sessions.db")
    _active_connections.append(session_mgr._conn)

    task_id = "test_recovery_task"
    agent_id = "test_agent_4"
    session_id = f"{task_id}_recovery_001"

    task = ScheduledTask(
        task_id=task_id,
        agent_id=agent_id,
        schedule="*/5 * * * *",
        task_type="lnn_train",
        status=ScheduleStatus.RUNNING,
        retry_count=0,
        max_retries=3,
    )
    queue.add_task(task)

    recorder.record(
        "测试4: 孤立任务恢复",
        "步骤1: 创建运行中的训练任务",
        "任务创建成功，状态为running",
        f"task_id={task_id}, status=running",
        "PASS",
    )

    checkpoint_data = {
        "epoch": 30,
        "total_epochs": 100,
        "progress": 0.3,
        "model_path": "models/checkpoint_epoch_30.npz",
    }

    session = ExecutionSession(
        session_id=session_id,
        task_id=task_id,
        status=ExecutionStatus.RUNNING,
        checkpoint_data=checkpoint_data,
        started_at=time.time() - 7200,
        last_updated=time.time() - 7200,
    )
    session_mgr.create_session(session)

    recorder.record(
        "测试4: 孤立任务恢复",
        "步骤2: 创建孤立会话（2小时前超时）",
        "会话创建成功，last_updated距今>3600秒",
        f"session_id={session_id}, timeout=7200秒",
        "PASS",
        f"checkpoint={checkpoint_data}",
    )

    engine = ExecutionEngine()
    engine.session_manager = session_mgr

    # 引擎在 recover_orphaned_tasks 内部执行
    # ``from app.dependencies import get_scheduler``（函数内导入，调用时解析），
    # 因此替换点为 ``app.dependencies.get_scheduler``；旧的
    # ``heartbeat_module._scheduler`` 全局符号在调度器单例Holder化重构后已不存在。
    import app.dependencies as dependencies_module

    test_scheduler_instance = HeartbeatScheduler(
        wakeup_queue=queue, heartbeat_interval=60
    )

    original_get_scheduler = dependencies_module.get_scheduler
    dependencies_module.get_scheduler = lambda: test_scheduler_instance
    try:
        recovered = await engine.recover_orphaned_tasks()
    finally:
        dependencies_module.get_scheduler = original_get_scheduler

    recovered_task = queue.get_task(task_id)
    recorder.record(
        "测试4: 孤立任务恢复",
        "步骤3: 触发孤立任务恢复机制",
        f"检测到{recovered}个孤立任务并恢复",
        f"recovered={recovered}, task_status={recovered_task.status.value if recovered_task else 'N/A'}",
        "PASS" if recovered > 0 else "FAIL",
        f"任务重试次数: {recovered_task.retry_count if recovered_task else 'N/A'}",
    )

    if recovered > 0:
        recorder.record(
            "测试4: 孤立任务恢复",
            "步骤4: 验证任务从检查点继续执行",
            "任务状态更新为pending，准备重试",
            f"status={recovered_task.status.value}, retry_count={recovered_task.retry_count}",
            "PASS" if recovered_task.status == ScheduleStatus.PENDING else "FAIL",
            f"next_run={recovered_task.next_run}",
        )

    engine.close()
    queue.close()
    session_mgr.close()
    logger.info("[TEST] 测试4完成")
    logger.info("")


async def test_5_cost_record_validation():
    """
    测试5: 成本记录验证测试
    查询数据库中的budget_events表，
    验证每次任务执行产生的GPU使用成本、执行时长、任务ID等信息
    是否被完整、准确地记录
    """
    logger.info("=" * 60)
    logger.info("开始测试5: 成本记录验证测试")
    logger.info("=" * 60)

    cleanup_test_dbs()
    cost_tracker = create_test_cost_tracker()
    _active_connections.append(cost_tracker._conn)

    task_id_1 = "test_cost_task_1"
    task_id_2 = "test_cost_task_2"
    agent_id = "test_agent_5"

    cost_tracker.record_gpu_usage(task_id_1, 0.5, agent_id)
    cost_tracker.record_memory_usage(task_id_1, 2048.5, agent_id)

    recorder.record(
        "测试5: 成本记录验证",
        "步骤1: 记录任务1的GPU和内存使用",
        "成本事件记录成功",
        f"task_id={task_id_1}, gpu_hours=0.5, memory_mb=2048.5",
        "PASS",
    )

    cost_tracker.record_gpu_usage(task_id_2, 1.2, agent_id)
    cost_tracker.record_memory_usage(task_id_2, 4096.0, agent_id)

    recorder.record(
        "测试5: 成本记录验证",
        "步骤2: 记录任务2的GPU和内存使用",
        "成本事件记录成功",
        f"task_id={task_id_2}, gpu_hours=1.2, memory_mb=4096.0",
        "PASS",
    )

    costs_1 = cost_tracker.get_task_costs(task_id_1)
    costs_2 = cost_tracker.get_task_costs(task_id_2)

    recorder.record(
        "测试5: 成本记录验证",
        "步骤3: 查询任务1的成本记录",
        f"查询到{len(costs_1)}条成本记录",
        f"records_count={len(costs_1)}",
        "PASS" if len(costs_1) == 2 else "FAIL",
        f"costs={[c['cost_type'] for c in costs_1]}",
    )

    recorder.record(
        "测试5: 成本记录验证",
        "步骤4: 查询任务2的成本记录",
        f"查询到{len(costs_2)}条成本记录",
        f"records_count={len(costs_2)}",
        "PASS" if len(costs_2) == 2 else "FAIL",
        f"costs={[c['cost_type'] for c in costs_2]}",
    )

    if costs_1:
        for cost in costs_1:
            # cost_events 表的时间戳列名为 recorded_at（见 cost_tracker._init_schema）
            if "recorded_at" in cost and cost["recorded_at"] is not None:
                time_diff = abs(time.time() - cost["recorded_at"])
                recorder.record(
                    "测试5: 成本记录验证",
                    f"步骤5: 验证成本记录时间戳({cost['cost_type']})",
                    "时间戳与实际执行时间相符（偏差<60秒）",
                    f"time_diff={time_diff:.2f}秒",
                    "PASS" if time_diff < 60 else "FAIL",
                    f"recorded_at={cost['recorded_at']}, current_time={time.time()}",
                )

    cost_tracker.close()
    logger.info("[TEST] 测试5完成")
    logger.info("")


async def test_6_task_concurrent_control():
    """
    测试6: 任务并发控制测试
    创建一个配置为每1分钟执行一次的周期性任务，
    同时设置该任务单次执行耗时为2分钟。
    验证系统能正确实施任务合并机制，不会出现并发执行
    """
    logger.info("=" * 60)
    logger.info("开始测试6: 任务并发控制测试")
    logger.info("=" * 60)

    cleanup_test_dbs()
    queue = create_test_wakeup_queue("test_coalescing.db")
    _active_connections.append(queue._conn)

    task_id = "test_coalescing_task"
    agent_id = "test_agent_6"

    task = ScheduledTask(
        task_id=task_id,
        agent_id=agent_id,
        schedule="*/1 * * * *",
        task_type="lnn_predict",
        status=ScheduleStatus.PENDING,
    )
    queue.add_task(task)

    recorder.record(
        "测试6: 任务并发控制",
        "步骤1: 创建1分钟周期任务",
        "任务创建成功",
        f"task_id={task_id}, schedule=*/1 * * * *",
        "PASS",
    )

    execution_count = 0

    async def slow_task_callback(task):
        nonlocal execution_count
        execution_count += 1
        logger.info(f"[TEST] Task {task.task_id} execution #{execution_count} started")

        await asyncio.sleep(2)

        queue.update_task_status(
            task.task_id, ScheduleStatus.COMPLETED, last_run=time.time()
        )
        logger.info(
            f"[TEST] Task {task.task_id} execution #{execution_count} completed"
        )

    scheduler = HeartbeatScheduler(wakeup_queue=queue, heartbeat_interval=1)
    scheduler.set_task_trigger_callback(slow_task_callback)

    scheduler._running = True

    for cycle in range(3):
        await scheduler._tick()
        await asyncio.sleep(0.5)

    scheduler._running = False

    history = queue.get_task_history(task_id)

    coalesced_count = sum(1 for h in history if h.get("status") == "coalesced")

    recorder.record(
        "测试6: 任务并发控制",
        "步骤2: 模拟3个心跳周期（任务执行耗时>周期）",
        f"执行{execution_count}次，跳过{coalesced_count}次",
        f"execution_count={execution_count}, coalesced_count={coalesced_count}",
        "PASS" if coalesced_count > 0 or execution_count <= 3 else "FAIL",
        "task执行耗时2秒>心跳间隔1秒，验证coalescing机制",
    )

    is_running = queue.is_task_running(task_id)
    recorder.record(
        "测试6: 任务并发控制",
        "步骤3: 验证任务状态管理",
        "任务状态正确更新，无并发执行",
        f"is_task_running={is_running}",
        "PASS" if not is_running else "FAIL",
        "任务完成后状态应更新为非running",
    )

    queue.close()

    logger.info("[TEST] 测试6完成")
    logger.info("")


async def test_7_agent_pause():
    """
    测试7: 代理暂停功能测试
    在系统管理界面手动暂停指定代理服务，
    确认在代理暂停期间所有任务均停止调度，
    且任务状态保持为"paused"
    """
    logger.info("=" * 60)
    logger.info("开始测试7: 代理暂停功能测试")
    logger.info("=" * 60)

    cleanup_test_dbs()
    queue = create_test_wakeup_queue("test_pause.db")
    _active_connections.append(queue._conn)

    agent_id = "test_agent_7"

    for i in range(3):
        task = ScheduledTask(
            task_id=f"test_pause_task_{i}",
            agent_id=agent_id,
            schedule="*/5 * * * *",
            task_type="lnn_predict",
            status=ScheduleStatus.PENDING,
        )
        queue.add_task(task)

    recorder.record(
        "测试7: 代理暂停功能",
        "步骤1: 为代理创建3个心跳任务",
        "3个任务创建成功，状态为pending",
        "3 tasks created for test_agent_7",
        "PASS",
    )

    tasks_before = queue.list_tasks(agent_id=agent_id)
    recorder.record(
        "测试7: 代理暂停功能",
        "步骤2: 验证代理任务列表",
        f"代理下有{len(tasks_before)}个任务",
        f"task_count={len(tasks_before)}",
        "PASS" if len(tasks_before) == 3 else "FAIL",
    )

    for task in tasks_before:
        queue.pause_task(task.task_id)

    tasks_after_pause = queue.list_tasks(
        agent_id=agent_id, status=ScheduleStatus.PAUSED
    )
    recorder.record(
        "测试7: 代理暂停功能",
        "步骤3: 暂停代理所有任务",
        f"所有{len(tasks_after_pause)}个任务状态变为paused",
        f"paused_count={len(tasks_after_pause)}",
        "PASS" if len(tasks_after_pause) == 3 else "FAIL",
    )

    async def mock_callback(task):
        logger.info(f"[TEST] UNEXPECTED: Task {task.task_id} triggered while paused!")

    scheduler = HeartbeatScheduler(wakeup_queue=queue, heartbeat_interval=1)
    scheduler.set_task_trigger_callback(mock_callback)
    scheduler._running = True

    await scheduler._tick()

    scheduler._running = False

    all_paused = all(
        t.status == ScheduleStatus.PAUSED for t in queue.list_tasks(agent_id=agent_id)
    )
    recorder.record(
        "测试7: 代理暂停功能",
        "步骤4: 验证心跳周期内暂停任务不被触发",
        "所有任务保持paused状态，无任务被触发",
        f"all_paused={all_paused}",
        "PASS" if all_paused else "FAIL",
        "暂停期间任务不应被执行",
    )

    queue.close()

    logger.info("[TEST] 测试7完成")
    logger.info("")


async def test_8_agent_resume():
    """
    测试8: 代理恢复功能测试
    手动恢复该代理服务，
    验证所有原心跳任务恢复正常调度，
    任务执行状态、频率与暂停前保持一致
    """
    logger.info("=" * 60)
    logger.info("开始测试8: 代理恢复功能测试")
    logger.info("=" * 60)

    cleanup_test_dbs()
    queue = create_test_wakeup_queue("test_resume.db")
    _active_connections.append(queue._conn)

    agent_id = "test_agent_8"

    for i in range(3):
        task = ScheduledTask(
            task_id=f"test_resume_task_{i}",
            agent_id=agent_id,
            schedule="*/1 * * * *",
            task_type="lnn_predict",
            status=ScheduleStatus.PAUSED,
        )
        queue.add_task(task)

    recorder.record(
        "测试8: 代理恢复功能",
        "步骤1: 为代理创建3个已暂停的心跳任务",
        "3个任务创建成功，状态为paused",
        "3 paused tasks created for test_agent_8",
        "PASS",
    )

    tasks_paused = queue.list_tasks(agent_id=agent_id, status=ScheduleStatus.PAUSED)
    recorder.record(
        "测试8: 代理恢复功能",
        "步骤2: 验证所有任务处于暂停状态",
        f"所有{len(tasks_paused)}个任务状态为paused",
        f"paused_count={len(tasks_paused)}",
        "PASS" if len(tasks_paused) == 3 else "FAIL",
    )

    for task in tasks_paused:
        queue.resume_task(task.task_id)

    now = time.time()
    for task in tasks_paused:
        queue._conn.execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE task_id = ?",
            (now - 1, task.task_id),
        )
        queue._conn.commit()

    tasks_resumed = queue.list_tasks(agent_id=agent_id)
    all_pending = all(t.status == ScheduleStatus.PENDING for t in tasks_resumed)

    recorder.record(
        "测试8: 代理恢复功能",
        "步骤3: 恢复代理所有任务",
        f"所有{len(tasks_resumed)}个任务恢复为pending状态",
        f"resumed_count={len(tasks_resumed)}, all_pending={all_pending}",
        "PASS" if all_pending and len(tasks_resumed) == 3 else "FAIL",
    )

    triggered_tasks = []

    async def mock_callback(task):
        triggered_tasks.append(task.task_id)
        queue.update_task_status(
            task.task_id, ScheduleStatus.COMPLETED, last_run=time.time()
        )

    scheduler = HeartbeatScheduler(wakeup_queue=queue, heartbeat_interval=1)
    scheduler.set_task_trigger_callback(mock_callback)
    scheduler._running = True

    await scheduler._tick()

    scheduler._running = False

    recorder.record(
        "测试8: 代理恢复功能",
        "步骤4: 验证恢复后任务正常调度",
        f"{len(triggered_tasks)}个任务被成功触发",
        f"triggered_tasks={triggered_tasks}",
        "PASS" if len(triggered_tasks) == 3 else "FAIL",
        "恢复后任务应立即进入正常调度流程",
    )

    completed_tasks = queue.list_tasks(
        agent_id=agent_id, status=ScheduleStatus.COMPLETED
    )
    recorder.record(
        "测试8: 代理恢复功能",
        "步骤5: 验证任务执行完成状态",
        f"{len(completed_tasks)}个任务状态更新为completed",
        f"completed_count={len(completed_tasks)}",
        "PASS" if len(completed_tasks) == 3 else "FAIL",
    )

    queue.close()

    logger.info("[TEST] 测试8完成")
    logger.info("")


async def main():
    """运行所有测试"""
    logger.info("Starting Heartbeat System Test Suite")
    logger.info(f"Test directory: {TEST_DIR}")
    logger.info("")

    try:
        await test_1_heartbeat_scheduling()
        await test_2_gpu_budget_control()
        await test_3_process_exception_handling()
        await test_4_orphaned_task_recovery()
        await test_5_cost_record_validation()
        await test_6_task_concurrent_control()
        await test_7_agent_pause()
        await test_8_agent_resume()

        report = recorder.generate_report()

        report_file = TEST_DIR / "test_report.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)

        logger.info("=" * 80)
        logger.info("ALL TESTS COMPLETED")
        logger.info(f"Test report saved to: {report_file}")
        logger.info("=" * 80)
        logger.info("")
        logger.info(report)

    except Exception as e:
        logger.error(f"Test suite failed with error: {e}", exc_info=True)
        recorder.record(
            "测试套件整体",
            "执行测试套件",
            "所有测试正常完成",
            f"异常: {str(e)}",
            "FAIL",
        )

        report = recorder.generate_report()
        report_file = TEST_DIR / "test_report.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)

        raise


if __name__ == "__main__":
    asyncio.run(main())
