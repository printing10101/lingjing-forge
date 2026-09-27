"""
Cost & Budget Management API Routes

Endpoints for cost tracking, budget enforcement, alerts, and optimization suggestions.
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field

import logging

from app.dependencies import get_budget_enforcer, get_cost_optimizer, get_cost_tracker

from app.models.budget import (
    BudgetLevel,
    BudgetPeriod,
    ResourceType,
    BudgetPolicy,
)
from app.auth.permissions import require_permission
from app.budget.cost_tracker import CostDimension

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/cost-budget",
    tags=["Cost & Budget"],
    # 安全修复 B2：为所有财务端点添加统一认证依赖，避免越权访问
    dependencies=[Depends(require_permission("cost:budget"))],
)


# B13 安全修复：Pydantic 请求模型替换 data: dict 弱验证


class SetUnitPriceRequest(BaseModel):
    """设置单价请求模型。"""

    key: str = Field(..., min_length=1, description="单价键名")
    value: float = Field(..., description="单价数值")


class SetBudgetPolicyRequest(BaseModel):
    """设置预算策略请求模型。"""

    level: str = Field("global", description="预算层级")
    scope_id: str = Field("default", description="范围ID")
    resource_type: str = Field("total_cost", description="资源类型")
    limit: float = Field(100.0, description="预算上限")
    period: str = Field("daily", description="预算周期")
    warning_threshold: float = Field(0.8, description="预警阈值")
    hard_stop: bool = Field(True, description="是否硬性停止")
    auto_notify: bool = Field(True, description="是否自动通知")
    enabled: bool = Field(True, description="是否启用")


class AdjustBudgetRequest(BaseModel):
    """调整预算请求模型。"""

    level: str = Field(..., description="预算层级")
    scope_id: str = Field("default", description="范围ID")
    resource_type: str = Field(..., description="资源类型")
    new_limit: float = Field(..., description="新预算上限")
    reason: str = Field("", description="调整原因")
    adjusted_by: str = Field("admin", description="调整人")


class CheckBudgetRequest(BaseModel):
    """检查预算请求模型。"""

    level: str = Field("global", description="预算层级")
    scope_id: str = Field("default", description="范围ID")
    resource_type: str = Field("total_cost", description="资源类型")
    planned_usage: float = Field(0.0, description="计划用量")


class CheckBudgetCascadeRequest(BaseModel):
    """级联检查预算请求模型。"""

    agent_id: str = Field("", description="Agent ID")
    project_id: str = Field("default", description="项目ID")
    resource_type: str = Field("total_cost", description="资源类型")
    planned_usage: float = Field(0.0, description="计划用量")


class EnforceBudgetRequest(BaseModel):
    """强制预算请求模型。"""

    level: str = Field("global", description="预算层级")
    scope_id: str = Field("default", description="范围ID")
    resource_type: str = Field("total_cost", description="资源类型")
    planned_usage: float = Field(0.0, description="计划用量")


class ResetBudgetPeriodRequest(BaseModel):
    """重置预算周期请求模型。"""

    level: str = Field(..., description="预算层级")
    scope_id: str = Field("default", description="范围ID")
    resource_type: str = Field(..., description="资源类型")


@router.get("/summary")
def get_cost_summary(
    dimension: str = Query("agent", description="汇总维度: agent/project/goal/task/provider/model"),
    scope_id: str = Query("", description="范围ID"),
    start_time: float | None = Query(None, description="起始Unix时间戳"),
    end_time: float | None = Query(None, description="结束Unix时间戳"),
):
    try:
        dim = CostDimension(dimension)
    except ValueError:
        logger.info("Invalid dimension: %s", dimension)
        raise HTTPException(400, "Invalid dimension")

    tracker = get_cost_tracker()

    if scope_id:
        summary = tracker.get_cost_summary(dim, scope_id, start_time, end_time)
        return {"ok": True, "data": summary.to_dict()}
    else:
        summaries = tracker.get_all_summaries(dim, start_time, end_time)
        return {"ok": True, "data": [s.to_dict() for s in summaries]}


@router.get("/task/{task_id}")
def get_task_costs(task_id: str):
    tracker = get_cost_tracker()
    costs = tracker.get_task_costs(task_id)
    total = tracker.get_task_total_cost(task_id)
    return {
        "ok": True,
        "data": {
            "task_id": task_id,
            "total_cost": total,
            "events": costs,
        },
    }


@router.get("/trend")
def get_cost_trend(
    days: int = Query(30, ge=1, le=365, description="查询天数"),
    interval_hours: int = Query(24, ge=1, le=168, description="数据间隔（小时）"),
):
    tracker = get_cost_tracker()
    trend = tracker.get_cost_trend(days, interval_hours)
    return {"ok": True, "data": trend}


@router.get("/unit-prices")
def get_unit_prices():
    tracker = get_cost_tracker()
    return {"ok": True, "data": tracker.get_unit_prices()}


@router.post("/unit-prices")
def set_unit_price(payload: SetUnitPriceRequest):
    key = payload.key
    value = payload.value

    valid_keys = [
        "gpu_time_per_second",
        "gpu_memory_per_gb_second",
        "api_call_per_request",
        "data_transfer_per_mb",
    ]
    if key not in valid_keys:
        logger.info("Invalid key: %s", key)
        raise HTTPException(400, "Invalid key")

    tracker = get_cost_tracker()
    tracker.set_unit_price(key, value)

    return {"ok": True, "data": tracker.get_unit_prices()}


@router.get("/policies")
def get_budget_policies(
    level: str | None = Query(None, description="预算层级"),
    scope_id: str | None = Query(None, description="范围ID"),
):
    enforcer = get_budget_enforcer()

    query_level = BudgetLevel(level) if level else None
    policies = enforcer.get_all_policies(query_level, scope_id)

    return {"ok": True, "data": [p.to_dict() for p in policies]}


@router.post("/policies")
def set_budget_policy(payload: SetBudgetPolicyRequest):
    enforcer = get_budget_enforcer()

    try:
        policy = BudgetPolicy(
            level=BudgetLevel(payload.level),
            scope_id=payload.scope_id,
            resource_type=ResourceType(payload.resource_type),
            limit=payload.limit,
            period=BudgetPeriod(payload.period),
            warning_threshold=payload.warning_threshold,
            hard_stop=payload.hard_stop,
            auto_notify=payload.auto_notify,
            enabled=payload.enabled,
        )
    except (ValueError, KeyError):
        # 修复：参数验证失败时不应回显 e 给客户端（可能含键名/结构信息），
        # 仅返回通用提示，详细错误可由服务端日志分析。
        raise HTTPException(400, "Invalid policy data")

    enforcer.set_policy(policy)
    return {"ok": True, "data": policy.to_dict()}


@router.post("/adjust-budget")
def adjust_budget(payload: AdjustBudgetRequest):
    enforcer = get_budget_enforcer()

    try:
        level = BudgetLevel(payload.level)
        scope_id = payload.scope_id
        resource_type = ResourceType(payload.resource_type)
        new_limit = payload.new_limit
        reason = payload.reason
        adjusted_by = payload.adjusted_by
    except (ValueError, KeyError):
        # 修复：不回显异常详情
        raise HTTPException(400, "Invalid adjustment data")

    updated = enforcer.adjust_budget(level, scope_id, resource_type, new_limit, reason, adjusted_by)
    return {"ok": True, "data": updated.to_dict() if updated else None}


@router.get("/adjustment-history")
def get_adjustment_history(limit: int = Query(50, ge=1, le=100)):
    tracker = get_cost_tracker()
    history = tracker.get_budget_adjustments(limit)
    return {"ok": True, "data": history}


@router.post("/check")
def check_budget(payload: CheckBudgetRequest):
    enforcer = get_budget_enforcer()

    try:
        level = BudgetLevel(payload.level)
        scope_id = payload.scope_id
        resource_type = ResourceType(payload.resource_type)
        planned_usage = payload.planned_usage
    except (ValueError, KeyError):
        # 修复：不回显异常详情
        raise HTTPException(400, "Invalid check data")

    result = enforcer.check_budget(level, scope_id, resource_type, planned_usage)
    return {"ok": True, "data": result.to_dict()}


@router.post("/check-cascade")
def check_budget_cascade(payload: CheckBudgetCascadeRequest):
    enforcer = get_budget_enforcer()

    agent_id = payload.agent_id
    project_id = payload.project_id
    resource_type_str = payload.resource_type
    planned_usage = payload.planned_usage

    try:
        resource_type = ResourceType(resource_type_str)
    except ValueError:
        logger.info("Invalid resource_type: %s", resource_type_str)
        raise HTTPException(400, "Invalid resource_type")

    result = enforcer.check_budget_cascade(agent_id, project_id, resource_type, planned_usage)
    return {"ok": True, "data": result.to_dict()}


@router.post("/enforce")
def enforce_budget(payload: EnforceBudgetRequest):
    enforcer = get_budget_enforcer()

    try:
        level = BudgetLevel(payload.level)
        scope_id = payload.scope_id
        resource_type = ResourceType(payload.resource_type)
        planned_usage = payload.planned_usage
    except (ValueError, KeyError):
        # 修复：不回显异常详情
        raise HTTPException(400, "Invalid enforce data")

    result = enforcer.enforce(level, scope_id, resource_type, planned_usage)
    return {
        "ok": True,
        "data": {
            "actions": [a.value for a in result.actions_taken],
            "passed": result.check_result.passed if result.check_result else False,
            "status": result.check_result.status.value if result.check_result else "unknown",
            "alerts": [a.to_dict() for a in result.alerts_generated],
        },
    }


@router.post("/reset")
def reset_budget_period(payload: ResetBudgetPeriodRequest):
    enforcer = get_budget_enforcer()

    try:
        level = BudgetLevel(payload.level)
        scope_id = payload.scope_id
        resource_type = ResourceType(payload.resource_type)
    except (ValueError, KeyError):
        # 修复：不回显异常详情
        raise HTTPException(400, "Invalid reset data")

    enforcer.reset_period(level, scope_id, resource_type)
    return {
        "ok": True,
        "message": f"Reset completed: {level.value}/{scope_id}/{resource_type.value}",
    }


@router.get("/alerts")
def get_budget_alerts(
    status: str | None = Query(None, description="筛选状态: warning/exceeded"),
    unread_only: bool = Query(False, description="仅未读"),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    enforcer = get_budget_enforcer()
    alerts = enforcer.get_alerts(status, unread_only, limit, offset)
    return {"ok": True, "data": alerts}


@router.post("/alerts/{alert_id}/read")
def mark_alert_read(alert_id: int):
    enforcer = get_budget_enforcer()
    enforcer.mark_alert_read(alert_id)
    return {"ok": True, "message": "Alert marked as read"}


@router.post("/alerts/read-all")
def mark_all_alerts_read():
    enforcer = get_budget_enforcer()
    enforcer.mark_all_alerts_read()
    return {"ok": True, "message": "All alerts marked as read"}


@router.delete("/alerts/{alert_id}")
def delete_alert(alert_id: int):
    enforcer = get_budget_enforcer()
    enforcer.delete_alert(alert_id)
    return {"ok": True, "message": "Alert deleted"}


@router.get("/suggestions")
def get_optimization_suggestions():
    optimizer = get_cost_optimizer()
    tracker = get_cost_tracker()
    optimizer.set_cost_tracker(tracker)

    suggestions = optimizer.generate_all_suggestions()
    return {"ok": True, "data": [s.to_dict() for s in suggestions]}


@router.get("/enforcement-log")
def get_enforcement_log(limit: int = Query(100, ge=1, le=100)):
    enforcer = get_budget_enforcer()
    log_entries = enforcer.get_enforcement_log(limit)
    return {"ok": True, "data": log_entries}


@router.get("/reset-log")
def get_reset_log(limit: int = Query(100, ge=1, le=100)):
    enforcer = get_budget_enforcer()
    log_entries = enforcer.get_reset_log(limit)
    return {"ok": True, "data": log_entries}
