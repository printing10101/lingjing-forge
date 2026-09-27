"""
Governance & Approval Management API Routes

Endpoints for approval workflow, risk assessment, emergency override, and governance reports.
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field

import logging
import re
import time

from app.dependencies import get_approval_engine

from app.dependencies import get_risk_identifier
from app.dependencies import get_sovereignty_policy

from app.models.governance import (
    ApprovalStatus,
    AgentRole,
)
from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.services.sovereignty import (
    AUTONOMY_CONFIDENCE_THRESHOLD,
    AUTONOMY_LABELS,
    ACTION_TYPES,
)

logger = logging.getLogger(__name__)

# P2-批次2 修复：operation_type 正则白名单。
# risk_identifier.assess_risk 基于关键字子串匹配（如 "machine" in operation_type），
# 因此不能用 Literal 枚举；但需防止空字符串、超长字符串、注入特殊字符。
# 允许字母、数字、下划线、连字符，长度 1-100。
_OPERATION_TYPE_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

router = APIRouter(
    prefix="/api/v1/governance",
    tags=["Governance & Approval"],
    dependencies=[Depends(require_permission("governance:read"))],
)


# ---- W9.1 AI 主权/自主等级（后端真实行为，前端 useSovereigntySettings 同步源） ----


class SovereigntyUpdateRequest(BaseModel):
    """主权设置部分更新：仅提交需要修改的字段。"""

    ai_autonomy_level: int | None = Field(None, ge=0, le=4, description="自主等级 0-4")
    require_confirmation_for_predict: bool | None = Field(None, description="预测动作是否需确认")
    require_confirmation_for_train: bool | None = Field(None, description="训练动作是否需确认")
    show_confidence_indicator: bool | None = None
    show_alternatives: bool | None = None
    show_reasoning: bool | None = None


@router.get("/sovereignty")
async def get_sovereignty_settings():
    """读取 AI 主权设置（前端设置面板的权威数据源）。"""
    settings = get_sovereignty_policy().get_settings()
    return success(
        data={
            "settings": settings.to_dict(),
            "autonomy_labels": list(AUTONOMY_LABELS),
            "confidence_threshold": AUTONOMY_CONFIDENCE_THRESHOLD,
            "action_types": list(ACTION_TYPES),
            "storage": "backend",  # 标识设置已后端持久化，前端据此显示同步状态
        },
        message="AI 主权设置已读取",
    )


@router.put(
    "/sovereignty",
    dependencies=[Depends(require_permission("governance:write"))],
)
async def update_sovereignty_settings(req: SovereigntyUpdateRequest):
    """更新 AI 主权设置（部分更新），立即持久化并影响后端 AI 动作决策。"""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return error(code=ErrorCode.INVALID_REQUEST, message="未提供任何需更新的字段")
    try:
        settings = get_sovereignty_policy().update_settings(updates)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    return success(
        data={"settings": settings.to_dict()},
        message=f"AI 主权设置已更新：自主等级 {settings.ai_autonomy_level}",
    )


@router.post(
    "/sovereignty/reset",
    dependencies=[Depends(require_permission("governance:write"))],
)
async def reset_sovereignty_settings():
    """恢复默认主权设置（推荐模式，等级 2）。"""
    settings = get_sovereignty_policy().reset()
    return success(
        data={"settings": settings.to_dict()},
        message="AI 主权设置已恢复默认（推荐模式）",
    )


@router.get("/approval-requests")
async def list_approval_requests(
    status: str | None = Query(None, description="筛选状态: pending/under_review/approved/rejected/escalated"),
    requester: str | None = Query(None, description="请求人"),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    engine = get_approval_engine()

    if status:
        try:
            status_enum = ApprovalStatus(status)
            requests = engine.get_requests_by_status(status_enum, limit, offset)
        except ValueError:
            logger.info("Invalid status: %s", status)
            raise HTTPException(400, "Invalid status")
    elif requester:
        requests = engine.get_requests_by_requester(requester, limit, offset)
    else:
        requests = engine.get_requests_by_status(ApprovalStatus.PENDING, limit, offset)

    return {"ok": True, "data": [r.to_dict() for r in requests]}


@router.get("/approval-requests/{request_id}")
async def get_approval_request(request_id: str):
    engine = get_approval_engine()
    request = engine.get_request(request_id)
    if request is None:
        logger.info("Approval request not found: %s", request_id)
        raise HTTPException(404, "Approval request not found")
    return {"ok": True, "data": request.to_dict()}


class CreateApprovalRequest(BaseModel):
    """创建审批请求模型。"""

    task_id: str = Field(..., description="任务 ID")
    requester: str = Field(..., description="请求人")
    context: dict = Field(default_factory=dict, description="上下文")
    # P2-批次2 修复：budget_amount 添加 ge=0 约束，防止负数预算绕过审批阈值。
    budget_amount: float = Field(default=0.0, ge=0.0, description="预算金额（必须 >=0）")
    agent_role: str = Field(default="engineer", description="代理角色")


@router.post("/approval-requests", dependencies=[Depends(require_permission("governance:write"))])
async def create_approval_request(data: CreateApprovalRequest):
    engine = get_approval_engine()
    risk_identifier = get_risk_identifier()

    try:
        task_id = data.task_id
        requester = data.requester
        context = data.context
        operation_type = context.get("operation_type", "unknown")
        # P2-批次2 修复：operation_type 正则校验，防止空字符串/注入特殊字符。
        if not _OPERATION_TYPE_RE.match(str(operation_type)):
            raise HTTPException(400, "Invalid operation_type in context")
        budget_amount = float(data.budget_amount)
        agent_role = AgentRole(data.agent_role)

        assessment = risk_identifier.assess_risk(
            operation_id=f"OP-{int(time.time())}",
            operation_type=operation_type,
            context=context,
            requester_role=agent_role,
            budget_amount=budget_amount,
        )

        priority = assessment.suggested_priority
        approvers = assessment.suggested_approvers
        required_approvals = 1
        expires_at = time.time() + 24 * 3600

        request = engine.create_approval_request(
            task_id=task_id,
            requester=requester,
            context={**context, "risk_assessment": assessment.to_dict()},
            priority=priority,
            approvers=approvers,
            required_approvals=required_approvals,
            risk_score=assessment.risk_score,
            risk_factors=[f.name for f in assessment.risk_factors],
            suggested_decision=assessment.suggested_strategy.value,
            expires_at=expires_at,
        )

        return {
            "ok": True,
            "data": request.to_dict(),
            "risk_assessment": assessment.to_dict(),
        }
    except (ValueError, KeyError):
        # 修复：不回显异常详情
        raise HTTPException(400, "Invalid request data")


class AssignApproverRequest(BaseModel):
    """指派审批人请求模型。"""

    approver_id: str | None = Field(default=None, description="审批人 ID")


@router.post("/approval-requests/{request_id}/assign", dependencies=[Depends(require_permission("governance:write"))])
async def assign_approver(request_id: str, data: AssignApproverRequest):
    engine = get_approval_engine()
    approver_id = data.approver_id
    if not approver_id:
        raise HTTPException(400, "approver_id is required")

    request = engine.assign_approver(request_id, approver_id)
    if request is None:
        logger.info("Approval request not found: %s", request_id)
        raise HTTPException(404, "Approval request not found")
    return {"ok": True, "data": request.to_dict()}


class MakeDecisionRequest(BaseModel):
    """审批决策请求模型。"""

    approver_id: str = Field(..., description="审批人 ID")
    decision: str = Field(..., description="决策: approved/rejected/escalated/request_info")
    comment: str = Field(default="", description="备注")


@router.post("/approval-requests/{request_id}/decide", dependencies=[Depends(require_permission("governance:write"))])
async def make_decision(request_id: str, data: MakeDecisionRequest):
    engine = get_approval_engine()

    try:
        approver_id = data.approver_id
        decision = data.decision
        if decision not in ("approved", "rejected", "escalated", "request_info"):
            raise HTTPException(
                400,
                "Invalid decision. Must be: approved/rejected/escalated/request_info",
            )

        comment = data.comment
        request = engine.make_decision(request_id, approver_id, decision, comment)
        if request is None:
            logger.info("Approval request not found: %s", request_id)
            raise HTTPException(404, "Approval request not found")

        return {"ok": True, "data": request.to_dict()}
    except HTTPException:
        raise
    except (ValueError, KeyError):
        # 修复：不回显异常详情
        raise HTTPException(400, "Invalid decision data")


class EscalateRequest(BaseModel):
    """审批升级请求模型。"""

    escalator_id: str = Field(default="system", description="升级操作人 ID")
    reason: str = Field(default="", description="升级原因")


@router.post("/approval-requests/{request_id}/escalate", dependencies=[Depends(require_permission("governance:write"))])
async def escalate_request(request_id: str, data: EscalateRequest):
    engine = get_approval_engine()
    escalator_id = data.escalator_id
    reason = data.reason

    request = engine.escalate_request(request_id, escalator_id, reason)
    if request is None:
        logger.info("Approval request not found: %s", request_id)
        raise HTTPException(404, "Approval request not found")
    return {"ok": True, "data": request.to_dict()}


@router.post("/approval-timeout-handler", dependencies=[Depends(require_permission("governance:write"))])
async def handle_approval_timeout():
    engine = get_approval_engine()
    handled_count = engine.handle_timeout()
    return {"ok": True, "data": {"handled_count": handled_count}}


@router.get("/approval-requests/my")
async def get_my_approval_requests(
    approver_id: str = Query(..., description="审批人ID"),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    engine = get_approval_engine()
    requests = engine.get_requests_by_approver(approver_id, limit, offset)
    return {"ok": True, "data": [r.to_dict() for r in requests]}


@router.get("/approval-dashboard")
async def get_approval_dashboard():
    engine = get_approval_engine()

    pending = engine.get_requests_by_status(ApprovalStatus.PENDING, 100)
    under_review = engine.get_requests_by_status(ApprovalStatus.UNDER_REVIEW, 100)
    approved = engine.get_requests_by_status(ApprovalStatus.APPROVED, 50)
    rejected = engine.get_requests_by_status(ApprovalStatus.REJECTED, 50)

    return {
        "ok": True,
        "data": {
            "pending": [r.to_dict() for r in pending],
            "under_review": [r.to_dict() for r in under_review],
            "approved": [r.to_dict() for r in approved],
            "rejected": [r.to_dict() for r in rejected],
            "counts": {
                "pending": len(pending),
                "under_review": len(under_review),
                "approved": len(approved),
                "rejected": len(rejected),
            },
        },
    }


class AssessRiskRequest(BaseModel):
    """风险评估请求模型。"""

    operation_id: str | None = Field(default=None, description="操作 ID（None 自动生成）")
    # P2-批次2 修复：operation_type 添加正则约束，防止空字符串/注入特殊字符。
    # 使用 pattern 而非 Literal，因为 risk_identifier 基于关键字子串匹配。
    operation_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="操作类型（仅允许字母、数字、下划线、连字符）",
    )
    context: dict = Field(default_factory=dict, description="上下文")
    requester_role: str = Field(default="engineer", description="请求人角色")
    # P2-批次2 修复：budget_amount 添加 ge=0 约束，防止负数预算绕过风险评估。
    budget_amount: float = Field(default=0.0, ge=0.0, description="预算金额（必须 >=0）")


@router.post("/risk-assess", dependencies=[Depends(require_permission("governance:write"))])
async def assess_operation_risk(data: AssessRiskRequest):
    risk_identifier = get_risk_identifier()

    try:
        operation_id = data.operation_id or f"OP-{int(time.time())}"
        operation_type = data.operation_type
        context = data.context
        requester_role = AgentRole(data.requester_role)
        budget_amount = float(data.budget_amount)

        assessment = risk_identifier.assess_risk(
            operation_id=operation_id,
            operation_type=operation_type,
            context=context,
            requester_role=requester_role,
            budget_amount=budget_amount,
        )

        return {"ok": True, "data": assessment.to_dict()}
    except HTTPException:
        raise
    except (ValueError, KeyError):
        # 修复：不回显异常详情
        raise HTTPException(400, "Invalid assessment data")


@router.get("/risk-categories")
async def get_risk_categories():
    risk_identifier = get_risk_identifier()
    return {
        "ok": True,
        "data": {
            "T_TYPE": list(risk_identifier.T_TYPE_OPERATIONS),
            "C_TYPE": list(risk_identifier.C_TYPE_OPERATIONS),
            "M_TYPE": list(risk_identifier.M_TYPE_OPERATIONS),
            "D_TYPE": list(risk_identifier.D_TYPE_OPERATIONS),
            "B_TYPE": list(risk_identifier.B_TYPE_OPERATIONS),
        },
    }


class EmergencyOverrideRequest(BaseModel):
    """紧急覆盖请求模型。"""

    request_id: str = Field(..., description="关联的审批请求ID")
    task_id: str = Field(..., description="任务ID")
    operator_id: str = Field(..., description="操作员ID")
    reason: str = Field(..., description="紧急覆盖原因")
    emergency_type: str = Field("production_halt", description="紧急类型")


@router.post(
    "/emergency-override",
    dependencies=[Depends(require_permission("governance:emergency"))],
)
async def emergency_override(request: EmergencyOverrideRequest):
    engine = get_approval_engine()

    try:
        result = engine.record_emergency_operation(
            request_id=request.request_id,
            task_id=request.task_id,
            operator_id=request.operator_id,
            reason=request.reason,
            emergency_type=request.emergency_type,
        )

        return {"ok": True, "data": result}
    except HTTPException:
        raise
    except (ValueError, KeyError):
        # 修复：不回显异常详情
        raise HTTPException(400, "Invalid emergency override data")


class CompleteRetroactiveRequest(BaseModel):
    """完成事后审批请求模型。"""

    emergency_id: str | None = Field(default=None, description="紧急操作 ID")


@router.post("/emergency-retroactive-approval", dependencies=[Depends(require_permission("governance:write"))])
async def complete_retroactive_approval(data: CompleteRetroactiveRequest):
    engine = get_approval_engine()
    emergency_id = data.emergency_id
    if not emergency_id:
        raise HTTPException(400, "emergency_id is required")

    success = engine.complete_retroactive_approval(emergency_id)
    if not success:
        logger.info("Emergency operation not found: %s", emergency_id)
        raise HTTPException(404, "Emergency operation not found")
    return {"ok": True, "message": "Retroactive approval completed"}


@router.get("/delegations")
async def get_delegations(user_id: str = Query(..., description="用户ID")):
    engine = get_approval_engine()
    active_delegation = engine.get_active_delegation(user_id)
    delegates_for = engine.get_delegates_for_user(user_id)

    return {
        "ok": True,
        "data": {
            "active_delegation": active_delegation.to_dict() if active_delegation else None,
            "delegates_for": delegates_for,
        },
    }


class CreateDelegationRequest(BaseModel):
    """创建委托请求模型。"""

    delegator_id: str = Field(..., description="委托人 ID")
    delegate_id: str = Field(..., description="被委托人 ID")
    start_time: float = Field(..., description="开始时间（Unix 时间戳）")
    end_time: float = Field(..., description="结束时间（Unix 时间戳）")
    reason: str = Field(default="", description="委托原因")


@router.post("/delegations", dependencies=[Depends(require_permission("governance:write"))])
async def create_delegation(data: CreateDelegationRequest):
    engine = get_approval_engine()

    try:
        delegator_id = data.delegator_id
        delegate_id = data.delegate_id
        start_time = float(data.start_time)
        end_time = float(data.end_time)
        reason = data.reason

        delegation = engine.delegate_approval(
            delegator_id=delegator_id,
            delegate_id=delegate_id,
            start_time=start_time,
            end_time=end_time,
            reason=reason,
        )

        return {"ok": True, "data": delegation.to_dict()}
    except HTTPException:
        raise
    except (ValueError, KeyError):
        # 修复：不回显异常详情
        raise HTTPException(400, "Invalid delegation data")


@router.get("/reports/governance")
async def get_governance_report(
    days: int = Query(30, ge=1, le=365, description="报告天数"),
):
    engine = get_approval_engine()
    now = time.time()
    period_start = now - days * 24 * 3600

    report = engine.generate_governance_report(period_start=period_start, period_end=now)
    return {"ok": True, "data": report.to_dict()}


@router.get("/audit-log/export")
async def export_audit_log(
    format: str = Query("json", description="导出格式: json/csv"),
    start_time: float | None = Query(None, description="起始时间"),
    end_time: float | None = Query(None, description="结束时间"),
):
    engine = get_approval_engine()

    try:
        log_data = engine.export_audit_log(start_time, end_time, format)
        return {"ok": True, "data": log_data, "format": format}
    except HTTPException:
        raise
    except ValueError:
        # 修复：不回显异常详情（可能含文件路径/库版本信息）
        raise HTTPException(400, "Invalid export request")
