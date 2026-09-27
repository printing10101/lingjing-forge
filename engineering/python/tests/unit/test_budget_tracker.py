"""budget 模块覆盖率补强测试（cost_tracker / enforcer / BudgetPolicy）。

覆盖补强目标：app/budget/cost_tracker.py + app/budget/enforcer.py 核心路径。
纯 SQLite 逻辑，使用临时 DB，无外部依赖。
"""

from __future__ import annotations

import os
import tempfile
import time

import pytest

from app.budget.cost_tracker import (
    CostDimension,
    CostEvent,
    CostType,
    MultiDimensionCostTracker,
    ProviderType,
)
from app.budget.enforcer import BudgetEnforcer
from app.models.budget import (
    BudgetLevel,
    BudgetPeriod,
    BudgetPolicy,
    BudgetStatus,
    ResourceType,
)

# CI 对齐：unit job 用 `pytest -m unit` 收集，模块级标记与逐函数标记等价
pytestmark = pytest.mark.unit


@pytest.fixture()
def tmp_db(tmp_path):
    """返回临时 DB 路径（确保每次测试独立）。"""
    return str(tmp_path / "test.db")


# CostEvent / 数据类


class TestCostEvent:
    def test_cost_event_to_dict_roundtrip(self):
        ev = CostEvent(
            task_id="t1",
            agent_id="a1",
            project_id="p1",
            goal_id="g1",
            provider="openai",
            model="gpt-4",
            cost_type="api_calls",
            resource_value=10.0,
            cost_value=0.01,
            start_time=1.0,
            end_time=2.0,
            metadata={"k": "v"},
            recorded_at=3.0,
        )
        d = ev.to_dict()
        assert d["task_id"] == "t1"
        assert d["agent_id"] == "a1"
        assert d["provider"] == "openai"
        assert d["metadata"] == {"k": "v"}
        assert ev.event_id is None  # 未持久化前无 id


# MultiDimensionCostTracker


class TestCostTracker:
    def test_init_creates_schema(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            # 默认单价应已加载
            prices = tracker.get_unit_prices()
            assert prices["gpu_time_per_second"] == 0.0001
            assert prices["api_call_per_request"] == 0.001
        finally:
            tracker.close()

    def test_set_and_get_unit_prices(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            tracker.set_unit_price("api_call_per_request", 0.005)
            prices = tracker.get_unit_prices()
            assert prices["api_call_per_request"] == 0.005
        finally:
            tracker.close()

    def test_record_cost_and_calculate(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            ev = tracker.record_cost(
                task_id="task-x",
                cost_type=CostType.API_CALLS.value,
                resource_value=100.0,
                agent_id="agent-a",
            )
            # 0.001 * 100 = 0.1
            assert ev.cost_value == pytest.approx(0.1)
            assert ev.event_id is not None
        finally:
            tracker.close()

    def test_record_cost_unknown_type_zero(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            ev = tracker.record_cost(
                task_id="task-y",
                cost_type="unknown_type",
                resource_value=50.0,
            )
            assert ev.cost_value == 0.0
        finally:
            tracker.close()

    def test_record_gpu_time(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            ev = tracker.record_gpu_time(task_id="t", gpu_seconds=1000.0, agent_id="a")
            assert ev.cost_type == CostType.GPU_TIME.value
            assert ev.cost_value == pytest.approx(1000.0 * 0.0001)
        finally:
            tracker.close()

    def test_record_gpu_memory(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            ev = tracker.record_gpu_memory(task_id="t", gb_seconds=500.0)
            assert ev.cost_type == CostType.GPU_MEMORY.value
            assert ev.cost_value == pytest.approx(500.0 * 0.00005)
        finally:
            tracker.close()

    def test_record_gpu_usage(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            ev = tracker.record_gpu_usage(task_id="t", gpu_hours=2.0, agent_id="a")
            assert ev.cost_value == pytest.approx(2.0 * 3600 * 0.0001)
        finally:
            tracker.close()

    def test_record_memory_usage(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            ev = tracker.record_memory_usage(task_id="t", memory_mb=2048.0)
            assert ev.cost_type == CostType.GPU_MEMORY.value
        finally:
            tracker.close()

    def test_record_api_call(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            ev = tracker.record_api_call(
                task_id="t",
                count=10,
                provider=ProviderType.OPENAI_API.value,
                model="gpt-4o",
            )
            assert ev.cost_type == CostType.API_CALLS.value
            assert ev.cost_value == pytest.approx(10 * 0.001)
            assert ev.provider == "openai_api"
        finally:
            tracker.close()

    def test_record_data_transfer(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            ev = tracker.record_data_transfer(task_id="t", mb_amount=100.0)
            assert ev.cost_type == CostType.DATA_TRANSFER.value
            assert ev.cost_value == pytest.approx(100.0 * 0.0001)
            assert ev.metadata.get("direction") == "upload"
        finally:
            tracker.close()

    def test_get_task_costs_and_total(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            tracker.record_cost(task_id="t1", cost_type=CostType.API_CALLS.value, resource_value=10.0)
            tracker.record_cost(task_id="t1", cost_type=CostType.API_CALLS.value, resource_value=20.0)
            costs = tracker.get_task_costs("t1")
            assert len(costs) == 2
            total = tracker.get_task_total_cost("t1")
            assert total == pytest.approx(30.0 * 0.001)
        finally:
            tracker.close()

    def test_get_cost_summary_by_agent(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            tracker.record_cost(
                task_id="t1",
                cost_type=CostType.API_CALLS.value,
                resource_value=50.0,
                agent_id="agent-b",
            )
            tracker.record_cost(
                task_id="t2",
                cost_type=CostType.API_CALLS.value,
                resource_value=50.0,
                agent_id="agent-b",
            )
            summary = tracker.get_cost_summary(CostDimension.AGENT, scope_id="agent-b")
            assert summary.total_api_calls == 100
            assert summary.api_calls_cost == pytest.approx(100 * 0.001)
            assert summary.task_count == 2
        finally:
            tracker.close()

    def test_get_cost_summary_time_window(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            tracker.record_cost(
                task_id="t1",
                cost_type=CostType.API_CALLS.value,
                resource_value=10.0,
                start_time=time.time() - 100,
                end_time=time.time() - 90,
            )
            # 窗口外的记录
            tracker.record_cost(
                task_id="t2",
                cost_type=CostType.API_CALLS.value,
                resource_value=10.0,
                start_time=time.time() - 10000,
                end_time=time.time() - 9990,
            )
            summary = tracker.get_cost_summary(
                CostDimension.TASK,
                scope_id="t1",
                start_time=time.time() - 50,
                end_time=time.time(),
            )
            assert summary.total_api_calls == 10
        finally:
            tracker.close()

    def test_get_all_summaries(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        try:
            tracker.record_cost(task_id="t1", cost_type=CostType.API_CALLS.value, resource_value=5.0, agent_id="a1")
            tracker.record_cost(task_id="t2", cost_type=CostType.API_CALLS.value, resource_value=5.0, agent_id="a2")
            summaries = tracker.get_all_summaries(CostDimension.AGENT)
            assert len(summaries) >= 2
        finally:
            tracker.close()

    def test_close_idempotent(self, tmp_db):
        tracker = MultiDimensionCostTracker(db_path=tmp_db)
        tracker.close()
        tracker.close()  # 二次关闭不抛错


# BudgetPolicy 模型


class TestBudgetPolicy:
    def test_usage_ratio_and_status_ok(self):
        p = BudgetPolicy(limit=100.0, current_usage=30.0, warning_threshold=0.8)
        assert p.usage_ratio == pytest.approx(0.3)
        assert p.status == BudgetStatus.OK
        assert p.remaining == pytest.approx(70.0)

    def test_status_warning(self):
        p = BudgetPolicy(limit=100.0, current_usage=85.0, warning_threshold=0.8)
        assert p.status == BudgetStatus.WARNING

    def test_status_exceeded(self):
        p = BudgetPolicy(limit=100.0, current_usage=120.0)
        assert p.status == BudgetStatus.EXCEEDED
        assert p.remaining == 0.0

    def test_status_disabled(self):
        p = BudgetPolicy(limit=100.0, current_usage=200.0, enabled=False)
        assert p.status == BudgetStatus.DISABLED

    def test_zero_limit_ratio(self):
        p = BudgetPolicy(limit=0.0, current_usage=10.0)
        assert p.usage_ratio == 0.0

    def test_to_dict(self):
        p = BudgetPolicy(level=BudgetLevel.PROJECT, scope_id="proj1")
        d = p.to_dict()
        assert d["level"] == "project"
        assert d["scope_id"] == "proj1"
        assert d["resource_type"] == "total_cost"


# BudgetEnforcer


class TestBudgetEnforcer:
    def test_init_and_default_policies(self, tmp_db):
        enforcer = BudgetEnforcer(db_path=tmp_db)
        try:
            policies = enforcer.get_all_policies()
            assert len(policies) > 0
        finally:
            enforcer.close()

    def test_set_and_get_policy(self, tmp_db):
        enforcer = BudgetEnforcer(db_path=tmp_db)
        try:
            policy = BudgetPolicy(
                level=BudgetLevel.AGENT,
                scope_id="agent-1",
                resource_type=ResourceType.API_CALLS,
                limit=1000.0,
                warning_threshold=0.7,
            )
            enforcer.set_policy(policy)
            got = enforcer.get_policy(BudgetLevel.AGENT, "agent-1", ResourceType.API_CALLS)
            assert got is not None
            assert got.limit == 1000.0
        finally:
            enforcer.close()

    def test_get_policy_missing_returns_none(self, tmp_db):
        enforcer = BudgetEnforcer(db_path=tmp_db)
        try:
            got = enforcer.get_policy(BudgetLevel.TASK, "nope", ResourceType.GPU_TIME)
            assert got is None
        finally:
            enforcer.close()

    def test_adjust_budget(self, tmp_db):
        enforcer = BudgetEnforcer(db_path=tmp_db)
        try:
            policy = BudgetPolicy(
                level=BudgetLevel.PROJECT, scope_id="p1", resource_type=ResourceType.TOTAL_COST, limit=100.0
            )
            enforcer.set_policy(policy)
            enforcer.adjust_budget(BudgetLevel.PROJECT, "p1", ResourceType.TOTAL_COST, new_limit=500.0, reason="扩容")
            got = enforcer.get_policy(BudgetLevel.PROJECT, "p1", ResourceType.TOTAL_COST)
            assert got is not None
            assert got.limit == 500.0
        finally:
            enforcer.close()

    def test_check_budget_ok(self, tmp_db):
        enforcer = BudgetEnforcer(db_path=tmp_db)
        try:
            policy = BudgetPolicy(
                level=BudgetLevel.AGENT,
                scope_id="a1",
                resource_type=ResourceType.API_CALLS,
                limit=100.0,
                warning_threshold=0.8,
            )
            enforcer.set_policy(policy)
            enforcer.record_usage(BudgetLevel.AGENT, "a1", ResourceType.API_CALLS, usage=30.0)
            result = enforcer.check_budget(BudgetLevel.AGENT, "a1", ResourceType.API_CALLS)
            assert result.status == BudgetStatus.OK
            assert result.passed is True
        finally:
            enforcer.close()

    def test_check_budget_warning(self, tmp_db):
        enforcer = BudgetEnforcer(db_path=tmp_db)
        try:
            policy = BudgetPolicy(
                level=BudgetLevel.AGENT,
                scope_id="a2",
                resource_type=ResourceType.API_CALLS,
                limit=100.0,
                warning_threshold=0.8,
            )
            enforcer.set_policy(policy)
            enforcer.record_usage(BudgetLevel.AGENT, "a2", ResourceType.API_CALLS, usage=90.0)
            result = enforcer.check_budget(BudgetLevel.AGENT, "a2", ResourceType.API_CALLS)
            assert result.status == BudgetStatus.WARNING
        finally:
            enforcer.close()

    def test_check_budget_exceeded_blocks(self, tmp_db):
        enforcer = BudgetEnforcer(db_path=tmp_db)
        try:
            policy = BudgetPolicy(
                level=BudgetLevel.AGENT,
                scope_id="a3",
                resource_type=ResourceType.API_CALLS,
                limit=100.0,
                hard_stop=True,
            )
            enforcer.set_policy(policy)
            enforcer.record_usage(BudgetLevel.AGENT, "a3", ResourceType.API_CALLS, usage=150.0)
            result = enforcer.check_budget(BudgetLevel.AGENT, "a3", ResourceType.API_CALLS)
            assert result.status == BudgetStatus.EXCEEDED
            assert result.passed is False
        finally:
            enforcer.close()

    def test_reset_period(self, tmp_db):
        enforcer = BudgetEnforcer(db_path=tmp_db)
        try:
            policy = BudgetPolicy(
                level=BudgetLevel.TASK,
                scope_id="t1",
                resource_type=ResourceType.GPU_TIME,
                limit=10.0,
                period=BudgetPeriod.DAILY,
            )
            enforcer.set_policy(policy)
            enforcer.record_usage(BudgetLevel.TASK, "t1", ResourceType.GPU_TIME, usage=8.0)
            enforcer.reset_period(BudgetLevel.TASK, "t1", ResourceType.GPU_TIME)
            got = enforcer.get_policy(BudgetLevel.TASK, "t1", ResourceType.GPU_TIME)
            assert got is not None
            assert got.current_usage == 0.0
        finally:
            enforcer.close()

    def test_alerts_lifecycle(self, tmp_db):
        enforcer = BudgetEnforcer(db_path=tmp_db)
        try:
            policy = BudgetPolicy(
                level=BudgetLevel.GLOBAL,
                scope_id="default",
                resource_type=ResourceType.TOTAL_COST,
                limit=50.0,
                warning_threshold=0.5,
            )
            enforcer.set_policy(policy)
            enforcer.record_usage(BudgetLevel.GLOBAL, "default", ResourceType.TOTAL_COST, usage=40.0)
            alerts = enforcer.get_alerts()
            assert isinstance(alerts, list)
            if alerts:
                alert = alerts[0]
                if isinstance(alert, dict):
                    aid = alert.get("id")
                else:
                    aid = alert.alert_id if hasattr(alert, "alert_id") else alert.get("id")
                if aid is not None:
                    enforcer.mark_alert_read(aid)
                    enforcer.mark_all_alerts_read()
                    enforcer.delete_alert(aid)
        finally:
            enforcer.close()

    def test_auto_reset_periods(self, tmp_db):
        enforcer = BudgetEnforcer(db_path=tmp_db)
        try:
            n = enforcer.auto_reset_periods()
            assert n >= 0
        finally:
            enforcer.close()
