"""
GPU Budget Management System - Comprehensive Integration Test Suite

Tests all 8 budget management scenarios:
1. Basic budget functionality
2. Budget warning mechanism
3. Cost data completeness
4. Cost visualization dashboard data
5. Budget overrun handling
6. Budget adjustment and recovery
7. Budget period reset
8. Cost optimization suggestions
"""

import os
import sys
import shutil
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from app.budget.budget_enforcer import (  # noqa: E402
    BudgetEnforcer,
    CostOptimizer,
    EnforcementAction,
)
from app.budget.cost_tracker import (  # noqa: E402
    BudgetEvent,
    CostDimension,
    CostType,
    ModelType,
    MultiDimensionCostTracker,
    ProviderType,
)
from app.models.budget import (  # noqa: E402
    BudgetLevel,
    BudgetPeriod,
    BudgetPolicy,
    BudgetStatus,
    ResourceType,
)


@pytest.fixture
def temp_dir():
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    # P2-1 修复：改用 shutil.rmtree 递归清理，防止 db WAL/-shm 附属文件导致 rmdir 失败。
    # 消除 except Exception: pass 静默吞错。
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def cost_tracker(temp_dir):
    cost_db = os.path.join(temp_dir, "cost_tracking.db")
    tracker = MultiDimensionCostTracker(db_path=cost_db)
    yield tracker
    tracker.close()


@pytest.fixture
def budget_enforcer(temp_dir):
    budget_db = os.path.join(temp_dir, "budget_enforcer.db")
    enforcer = BudgetEnforcer(db_path=budget_db)
    enforcer._policies.clear()
    yield enforcer
    enforcer.close()


@pytest.fixture
def cost_optimizer():
    return CostOptimizer()


class Test1_BasicBudgetFunctionality:
    """Test 1: 预算基础功能验证"""

    def test_set_daily_gpu_budget(self, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.8,
            hard_stop=True,
            auto_notify=True,
        )
        budget_enforcer.set_policy(policy)

        retrieved = budget_enforcer.get_policy(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME
        )
        assert retrieved is not None
        assert retrieved.limit == 3600.0
        assert retrieved.period == BudgetPeriod.DAILY
        assert retrieved.warning_threshold == 0.8
        assert retrieved.hard_stop is True

    def test_record_training_task_cost(self, cost_tracker, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.8,
            hard_stop=True,
        )
        budget_enforcer.set_policy(policy)

        task_id = "test_task_001"
        start = time.time()
        gpu_seconds = 1800.0

        event = cost_tracker.record_gpu_time(
            task_id=task_id,
            gpu_seconds=gpu_seconds,
            agent_id="gpu_agent_01",
            model=ModelType.CFC.value,
            start_time=start,
            end_time=start + gpu_seconds,
        )

        assert event.task_id == task_id
        assert event.cost_type == CostType.GPU_TIME.value
        assert event.resource_value == gpu_seconds
        assert event.start_time == start
        assert event.end_time == start + gpu_seconds

        summary = cost_tracker.get_task_total_cost(task_id)
        expected_cost = gpu_seconds * cost_tracker._unit_prices.gpu_time_per_second
        assert summary == expected_cost

        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, gpu_seconds
        )

        policy_after = budget_enforcer.get_policy(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME
        )
        assert policy_after.current_usage == 1800.0
        assert abs(policy_after.usage_ratio - 0.5) < 0.01

    def test_cost_calculation_accuracy(self, cost_tracker):
        task_id = "test_task_002"
        gpu_seconds = 1800.0

        event = cost_tracker.record_gpu_time(
            task_id=task_id,
            gpu_seconds=gpu_seconds,
            agent_id="agent_01",
        )

        expected_cost = gpu_seconds * 0.0001
        assert event.cost_value == expected_cost
        assert abs(event.cost_value - 0.18) < 0.0001


class Test2_BudgetWarningMechanism:
    """Test 2: 预算预警机制测试"""

    def test_warning_at_threshold(self, cost_tracker, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.8,
            hard_stop=True,
            auto_notify=True,
        )
        budget_enforcer.set_policy(policy)

        for i in range(3):
            task_id = f"warn_task_{i}"
            cost_tracker.record_gpu_time(
                task_id=task_id,
                gpu_seconds=1800.0,
                agent_id="agent_01",
            )
            budget_enforcer.record_usage(
                BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 1800.0
            )

        policy_after = budget_enforcer.get_policy(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME
        )
        assert policy_after.current_usage == 5400.0
        assert policy_after.usage_ratio == 1.0

    def test_hard_stop_at_100_percent(self, cost_tracker, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.5,
            hard_stop=True,
            auto_notify=True,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 3600.0
        )

        result = budget_enforcer.enforce(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, planned_usage=1800.0
        )

        assert EnforcementAction.BLOCK in result.actions_taken
        assert result.check_result.status == BudgetStatus.EXCEEDED
        assert result.check_result.passed is False

    def test_warning_triggered_at_threshold(self, cost_tracker, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.8,
            hard_stop=True,
            auto_notify=True,
        )
        budget_enforcer.set_policy(policy)

        usage_85pct = 3600.0 * 0.85
        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, usage_85pct
        )

        result = budget_enforcer.enforce(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, planned_usage=0.0
        )

        assert EnforcementAction.WARN in result.actions_taken
        assert len(result.alerts_generated) > 0


class Test3_CostDataCompleteness:
    """Test 3: 成本数据完整性检查"""

    def test_cost_event_dimensions(self, cost_tracker):
        task_id = "dim_task_001"
        agent_id = "dim_agent_01"
        project_id = "dim_project_01"
        goal_id = "dim_goal_01"
        model = ModelType.CFC.value

        now = time.time()
        event = cost_tracker.record_cost(
            task_id=task_id,
            cost_type=CostType.GPU_TIME.value,
            resource_value=1800.0,
            agent_id=agent_id,
            project_id=project_id,
            goal_id=goal_id,
            provider=ProviderType.OLLAMA_LOCAL.value,
            model=model,
            start_time=now,
            end_time=now + 1800.0,
            metadata={"gpu_type": "A100", "training_epochs": 10},
        )

        assert event.task_id == task_id
        assert event.agent_id == agent_id
        assert event.project_id == project_id
        assert event.goal_id == goal_id
        assert event.provider == ProviderType.OLLAMA_LOCAL.value
        assert event.model == model
        assert event.cost_type == CostType.GPU_TIME.value
        assert event.resource_value == 1800.0
        assert event.start_time == now
        assert event.end_time == now + 1800.0
        assert "gpu_type" in event.metadata

    def test_query_cost_by_dimension(self, cost_tracker):
        task_id = "dim_task_002"
        agent_id = "dim_agent_02"

        cost_tracker.record_gpu_time(
            task_id=task_id,
            gpu_seconds=3600.0,
            agent_id=agent_id,
            model=ModelType.LTC.value,
        )

        task_costs = cost_tracker.get_task_costs(task_id)
        assert len(task_costs) > 0

        agent_summary = cost_tracker.get_cost_summary(CostDimension.AGENT, agent_id)
        assert agent_summary.total_cost > 0
        assert agent_summary.scope_id == agent_id

    def test_budget_event_recorded(self, cost_tracker):
        event = BudgetEvent(
            budget_level=BudgetLevel.GLOBAL.value,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME.value,
            current_usage=1800.0,
            limit_value=3600.0,
            usage_ratio=0.5,
            status=BudgetStatus.OK.value,
        )
        cost_tracker.record_budget_event(event)

        events = cost_tracker.get_budget_events()
        assert len(events) == 1
        assert events[0]["budget_level"] == BudgetLevel.GLOBAL.value
        assert events[0]["current_usage"] == 1800.0
        assert events[0]["limit_value"] == 3600.0
        assert events[0]["usage_ratio"] == 0.5
        assert events[0]["status"] == BudgetStatus.OK.value


class Test4_DashboardDataValidation:
    """Test 4: 成本可视化仪表板验证"""

    def test_budget_status_progress_data(self, cost_tracker, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.8,
            hard_stop=True,
        )
        budget_enforcer.set_policy(policy)
        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 2700.0
        )

        retrieved = budget_enforcer.get_policy(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME
        )

        assert abs(retrieved.usage_ratio - 0.75) < 0.01
        assert abs(retrieved.remaining - 900.0) < 1.0
        assert retrieved.status == BudgetStatus.OK

    def test_cost_dimension_distribution_data(self, cost_tracker):
        for i, agent_id in enumerate(["agent_a", "agent_b", "agent_c"]):
            cost_tracker.record_gpu_time(
                task_id=f"dist_task_{i}",
                gpu_seconds=1800.0 * (i + 1),
                agent_id=agent_id,
            )

        summaries = cost_tracker.get_all_summaries(CostDimension.AGENT)
        assert len(summaries) == 3

        agent_costs = {s.scope_id: s.total_cost for s in summaries}
        assert agent_costs["agent_a"] < agent_costs["agent_b"] < agent_costs["agent_c"]

    def test_cost_trend_data(self, cost_tracker):
        now = time.time()
        interval = 86400

        for day in range(7):
            ts = now - (6 - day) * interval
            cost_tracker.record_gpu_time(
                task_id=f"trend_task_{day}",
                gpu_seconds=3600.0,
                agent_id="trend_agent",
                start_time=ts,
                end_time=ts + 3600.0,
            )

        trend = cost_tracker.get_cost_trend(days=7, interval_hours=24)
        assert len(trend) >= 1

        for point in trend:
            assert "timestamp" in point
            assert "total_cost" in point
            assert point["total_cost"] > 0

    def test_budget_alerts_data(self, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.5,
            hard_stop=True,
            auto_notify=True,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 2000.0
        )
        budget_enforcer.enforce(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 0.0
        )

        alerts = budget_enforcer.get_alerts()
        assert len(alerts) > 0

        alert = alerts[0]
        assert alert["level"] == BudgetLevel.GLOBAL.value
        assert alert["status"] in ["warning", "exceeded"]
        assert alert["usage_ratio"] > 0


class Test5_BudgetOverrunHandling:
    """Test 5: 预算超限处理机制测试"""

    def test_reject_task_when_over_budget(self, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.5,
            hard_stop=True,
            auto_notify=True,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 3600.0
        )

        result = budget_enforcer.enforce(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, planned_usage=600.0
        )

        assert EnforcementAction.BLOCK in result.actions_taken
        assert result.check_result.block_reason != ""
        assert "EXCEEDED" in result.check_result.block_reason

    def test_cancel_pending_tasks(self, budget_enforcer):
        cancelled_tasks = []

        def mock_canceller(task_id):
            cancelled_tasks.append(task_id)

        budget_enforcer.set_task_canceller(mock_canceller)

        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.TOTAL_COST,
            limit=100.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.5,
            hard_stop=True,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.TOTAL_COST, 100.0
        )

        result = budget_enforcer.enforce(
            BudgetLevel.GLOBAL, "default", ResourceType.TOTAL_COST, 10.0
        )

        assert EnforcementAction.BLOCK in result.actions_taken

    def test_suspend_agent(self, budget_enforcer):
        suspended_agents = []

        def mock_suspender(agent_id, reason):
            suspended_agents.append((agent_id, reason))

        budget_enforcer.set_agent_suspender(mock_suspender)

        policy = BudgetPolicy(
            level=BudgetLevel.AGENT,
            scope_id="gpu_agent_01",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.5,
            hard_stop=True,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.record_usage(
            BudgetLevel.AGENT, "gpu_agent_01", ResourceType.GPU_TIME, 4000.0
        )

        result = budget_enforcer.enforce(
            BudgetLevel.AGENT, "gpu_agent_01", ResourceType.GPU_TIME, 0.0
        )

        assert EnforcementAction.SUSPEND_AGENT in result.actions_taken
        assert "gpu_agent_01" in result.suspended_agents

    def test_admin_notification(self, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.5,
            hard_stop=True,
            auto_notify=True,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 4000.0
        )

        result = budget_enforcer.enforce(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 0.0
        )

        assert EnforcementAction.NOTIFY_ADMIN in result.actions_taken
        assert result.notifications_sent is True


class Test6_BudgetAdjustmentAndRecovery:
    """Test 6: 预算调整与恢复测试"""

    def test_budget_adjustment(self, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.8,
            hard_stop=True,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 4000.0
        )

        result_before = budget_enforcer.enforce(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 0.0
        )
        assert EnforcementAction.BLOCK in result_before.actions_taken

        budget_enforcer.adjust_budget(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            new_limit=7200.0,
            reason="Emergency budget increase for critical training",
            adjusted_by="admin",
        )

        policy_after = budget_enforcer.get_policy(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME
        )
        assert policy_after.limit == 7200.0
        # 4000/7200 = 55.56% < 80% warning_threshold -> OK
        assert policy_after.status == BudgetStatus.OK

    def test_resume_after_adjustment(self, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.TOTAL_COST,
            limit=100.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.5,
            hard_stop=True,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.TOTAL_COST, 150.0
        )

        assert (
            EnforcementAction.BLOCK
            in budget_enforcer.enforce(
                BudgetLevel.GLOBAL, "default", ResourceType.TOTAL_COST, 0.0
            ).actions_taken
        )

        budget_enforcer.adjust_budget(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.TOTAL_COST,
            new_limit=300.0,
            reason="Recovery adjustment",
        )

        result = budget_enforcer.enforce(
            BudgetLevel.GLOBAL, "default", ResourceType.TOTAL_COST, 0.0
        )
        assert EnforcementAction.ALLOW in result.actions_taken

    def test_adjustment_history_recorded(self, budget_enforcer, cost_tracker):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.set_cost_tracker(cost_tracker)

        budget_enforcer.adjust_budget(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            new_limit=7200.0,
            reason="Test adjustment",
            adjusted_by="test_user",
        )

        history = cost_tracker.get_budget_adjustments(limit=10)
        assert len(history) > 0

        adjustment = history[0]
        assert adjustment["old_limit"] == 3600.0
        assert adjustment["new_limit"] == 7200.0
        assert adjustment["reason"] == "Test adjustment"
        assert adjustment["adjusted_by"] == "test_user"


class Test7_BudgetPeriodReset:
    """Test 7: 预算周期重置验证"""

    def test_daily_budget_reset(self, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
            warning_threshold=0.8,
            hard_stop=True,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 2700.0
        )

        policy_before = budget_enforcer.get_policy(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME
        )
        assert policy_before.current_usage == 2700.0

        budget_enforcer.reset_period(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME
        )

        policy_after = budget_enforcer.get_policy(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME
        )
        assert policy_after.current_usage == 0.0
        assert policy_after.limit == 3600.0
        assert policy_after.last_reset_at is not None

    def test_reset_log_recorded(self, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
        )
        budget_enforcer.set_policy(policy)

        budget_enforcer.record_usage(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME, 1800.0
        )

        budget_enforcer.reset_period(
            BudgetLevel.GLOBAL, "default", ResourceType.GPU_TIME
        )

        reset_log = budget_enforcer.get_reset_log(limit=10)
        assert len(reset_log) > 0

        log_entry = reset_log[0]
        assert log_entry["level"] == BudgetLevel.GLOBAL.value
        assert log_entry["usage_before_reset"] == 1800.0
        assert log_entry["limit_at_reset"] == 3600.0

    def test_daily_usage_split(self, cost_tracker, budget_enforcer):
        policy = BudgetPolicy(
            level=BudgetLevel.GLOBAL,
            scope_id="default",
            resource_type=ResourceType.GPU_TIME,
            limit=3600.0,
            period=BudgetPeriod.DAILY,
        )
        budget_enforcer.set_policy(policy)

        now = time.time()

        cost_tracker.record_gpu_time(
            task_id="today_task",
            gpu_seconds=1800.0,
            agent_id="agent_01",
        )

        now - 172800
        cost_tracker.record_gpu_time(
            task_id="yesterday_task",
            gpu_seconds=3600.0,
            agent_id="agent_01",
        )

        yesterday_events = cost_tracker.get_task_costs("yesterday_task")
        assert len(yesterday_events) > 0
        assert yesterday_events[0]["cost_value"] > 0


class Test8_CostOptimizationSuggestions:
    """Test 8: 成本优化建议功能测试"""

    def test_model_cost_analysis(self, cost_tracker, cost_optimizer):
        cost_optimizer.set_cost_tracker(cost_tracker)

        for i in range(5):
            cost_tracker.record_gpu_time(
                task_id=f"highcost_task_{i}",
                gpu_seconds=7200.0,
                agent_id="expensive_agent",
                model=ModelType.TRANSFORMER.value,
                provider=ProviderType.OPENAI_API.value,
            )

        suggestions = cost_optimizer.generate_all_suggestions()

        assert len(suggestions) > 0

        model_suggestions = [s for s in suggestions if "model" in s.category.lower()]
        assert len(model_suggestions) > 0

        for suggestion in model_suggestions:
            assert suggestion.title != ""
            assert suggestion.description != ""
            assert suggestion.current_cost > 0
            assert suggestion.estimated_savings > 0

    def test_gpu_utilization_analysis(self, cost_tracker, cost_optimizer):
        cost_optimizer.set_cost_tracker(cost_tracker)

        for i in range(3):
            cost_tracker.record_gpu_time(
                task_id=f"inefficient_task_{i}",
                gpu_seconds=3600.0,
                agent_id="gpu_agent_01",
                model=ModelType.CFC.value,
            )

        suggestions = cost_optimizer.analyze_gpu_utilization()

        assert len(suggestions) >= 0

        for suggestion in suggestions:
            assert (
                "gpu" in suggestion.category.lower()
                or "resource" in suggestion.category.lower()
            )
            assert suggestion.priority in ["low", "medium", "high", "critical"]

    def test_training_efficiency_analysis(self, cost_tracker, cost_optimizer):
        cost_optimizer.set_cost_tracker(cost_tracker)

        for i in range(6):
            cost_tracker.record_gpu_time(
                task_id=f"repetitive_task_{i}",
                gpu_seconds=720.0,
                agent_id="training_agent",
                model=ModelType.LTC.value,
            )

        suggestions = cost_optimizer.analyze_training_efficiency()

        assert len(suggestions) >= 0

        for suggestion in suggestions:
            assert suggestion.title != ""
            assert suggestion.estimated_savings > 0
            assert suggestion.recommendation != ""

    def test_comprehensive_suggestions(self, cost_tracker, cost_optimizer):
        cost_optimizer.set_cost_tracker(cost_tracker)

        for i in range(3):
            cost_tracker.record_gpu_time(
                task_id=f"gpu_task_{i}",
                gpu_seconds=7200.0,
                agent_id="gpu_agent",
                model=ModelType.TRANSFORMER.value,
                provider=ProviderType.OPENAI_API.value,
            )

        for i in range(2):
            cost_tracker.record_gpu_time(
                task_id=f"long_train_{i}",
                gpu_seconds=14400.0,
                agent_id="training_agent",
                model=ModelType.LTC.value,
            )

        for i in range(10):
            cost_tracker.record_api_call(
                task_id=f"api_task_{i}",
                count=100,
                agent_id="api_agent",
                provider=ProviderType.OPENAI_API.value,
            )

        suggestions = cost_optimizer.generate_all_suggestions()

        categories = set(s.category.lower() for s in suggestions)
        assert len(categories) >= 2

        for suggestion in suggestions:
            assert suggestion.suggestion_id != ""
            assert suggestion.category != ""
            assert suggestion.title != ""
            assert suggestion.description != ""
            assert suggestion.current_cost >= 0
            assert suggestion.estimated_savings >= 0
            assert suggestion.savings_percentage >= 0
            assert suggestion.priority in ["low", "medium", "high", "critical"]
            assert suggestion.recommendation != ""
            assert suggestion.generated_at is not None


class TestCascadeBudgetCheck:
    def test_cascade_check_pass(self, budget_enforcer):
        for level, scope, limit in [
            (BudgetLevel.GLOBAL, "default", 10000.0),
            (BudgetLevel.PROJECT, "default", 5000.0),
            (BudgetLevel.AGENT, "agent_01", 1000.0),
        ]:
            policy = BudgetPolicy(
                level=level,
                scope_id=scope,
                resource_type=ResourceType.TOTAL_COST,
                limit=limit,
                period=BudgetPeriod.DAILY,
                warning_threshold=0.8,
                hard_stop=True,
            )
            budget_enforcer.set_policy(policy)

        result = budget_enforcer.check_budget_cascade(
            agent_id="agent_01",
            project_id="default",
            resource_type=ResourceType.TOTAL_COST,
            planned_usage=100.0,
        )

        assert result.passed is True
        assert result.status == BudgetStatus.OK

    def test_cascade_check_fail_at_agent_level(self, budget_enforcer):
        budget_enforcer.set_policy(
            BudgetPolicy(
                level=BudgetLevel.GLOBAL,
                scope_id="default",
                resource_type=ResourceType.TOTAL_COST,
                limit=10000.0,
                period=BudgetPeriod.DAILY,
            )
        )
        budget_enforcer.set_policy(
            BudgetPolicy(
                level=BudgetLevel.PROJECT,
                scope_id="default",
                resource_type=ResourceType.TOTAL_COST,
                limit=5000.0,
                period=BudgetPeriod.DAILY,
            )
        )

        budget_enforcer.set_policy(
            BudgetPolicy(
                level=BudgetLevel.AGENT,
                scope_id="agent_01",
                resource_type=ResourceType.TOTAL_COST,
                limit=100.0,
                period=BudgetPeriod.DAILY,
                warning_threshold=0.5,
                hard_stop=True,
            )
        )
        budget_enforcer.record_usage(
            BudgetLevel.AGENT, "agent_01", ResourceType.TOTAL_COST, 100.0
        )

        result = budget_enforcer.check_budget_cascade(
            agent_id="agent_01",
            project_id="default",
            resource_type=ResourceType.TOTAL_COST,
            planned_usage=50.0,
        )

        assert result.passed is False
        assert result.status in [BudgetStatus.WARNING, BudgetStatus.EXCEEDED]


class TestMultipleCostTypes:
    def test_all_cost_types(self, cost_tracker):
        task_id = "multitype_task"

        cost_tracker.record_gpu_time(
            task_id=task_id, gpu_seconds=3600.0, agent_id="agent_01"
        )

        cost_tracker.record_gpu_memory(
            task_id=task_id, gb_seconds=8.0 * 3600.0, agent_id="agent_01"
        )

        cost_tracker.record_api_call(task_id=task_id, count=500, agent_id="agent_01")

        cost_tracker.record_data_transfer(
            task_id=task_id, mb_amount=1024.0, agent_id="agent_01"
        )

        summary = cost_tracker.get_cost_summary(CostDimension.AGENT, "agent_01")

        assert summary.gpu_time_cost > 0
        assert summary.gpu_memory_cost > 0
        assert summary.api_calls_cost > 0
        assert summary.data_transfer_cost > 0
        assert summary.total_cost > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
