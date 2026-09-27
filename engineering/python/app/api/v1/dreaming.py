"""Dreaming 离线反思 API（ADR-021，W7.2 最小 API 面）。

把「AI 自我改进」从地下室能力变成可操作的产品面：
- POST  /reflect                触发完整反思（提取→反思→合成→报告，draft 状态）
- GET   /rules                  规则草稿 × 灰度状态联查
- POST  /rules/{id}/publish     人工放行：草稿进入灰度 SHADOW（AI 起草，人确认）
- POST  /rules/{id}/promote     灰度晋级（可带效果指标；FULL 强制双重沙箱校验）
- POST  /rules/{id}/rollback    一键回滚（经 RollbackManager，含冷却期与历史留痕）
- GET   /status                 子系统状态（阶段分布 / 草稿数 / 最近反思）
- GET   /learnings              「它这周学会了什么」：窗口期内的新增草稿与灰度事件流

信任边界（与 ADR-021 硬约束一致）：
- 反思合成的规则一律 draft 状态，未经人工 publish 不产生任何实际影响；
- FULL 晋级由 ProgressivePublisher 强制重新沙箱校验；
- 回滚走 RollbackManager（硬约束违反直接 DEPRECATED）。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.dreaming.progressive_publisher import ProgressivePublisher, PublicationStage
from app.dreaming.rollback_manager import RollbackManager
from app.dreaming.rule_synthesizer import RuleSynthesizer

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/dreaming",
    tags=["Dreaming 离线反思"],
    dependencies=[Depends(require_permission("governance:read"))],
)

# 可注入获取器（测试通过 monkeypatch 替换；生产用默认相对路径，
# 与 DreamingCLI 的 python/outputs/... 约定一致）


def _get_publisher() -> ProgressivePublisher:
    return ProgressivePublisher()


def _get_rollback_manager(publisher: ProgressivePublisher) -> RollbackManager:
    return RollbackManager(publisher=publisher)


def _get_drafts_store() -> RuleSynthesizer:
    return RuleSynthesizer(output_dir=os.path.join("python", "outputs", "dreaming", "rules"))


def _get_reports_dir() -> Path:
    return Path("python", "outputs", "dreaming", "reports")


# ---- 请求模型 ----


class ReflectionRequest(BaseModel):
    """反思触发参数（与 DreamingCLI reflect 子命令对齐）。"""

    lookback_days: int = Field(30, ge=1, le=365, description="回看天数")
    max_sessions: int | None = Field(None, ge=1, description="最多提取的 Session 数")
    instructions: str | None = Field(None, description="反思指令（自然语言）")
    enable_llm: bool = Field(True, description="启用 LLM 反思（False = 规则统计降级）")
    include_ar_02: bool = Field(False, description="包含 AR-02 修复前数据")


class PromoteRequest(BaseModel):
    """灰度晋级参数。"""

    target_stage: str | None = Field(
        None, description="目标阶段（shadow/canary/rolling_10/rolling_50/full），None=下一阶段"
    )
    metrics: dict[str, Any] | None = Field(
        None, description="效果指标快照（accuracy/false_positive_rate/sample_size/error_rate）"
    )


class RollbackRequest(BaseModel):
    """一键回滚参数。"""

    reason: str = Field(..., min_length=1, max_length=500, description="回滚原因（必填，入审计）")
    severity: str = Field("manual", description="严重级别（hard_constraint 立即废弃）")
    fully_deprecate: bool = Field(False, description="强制完全废弃")


# ---- 端点 ----


@router.post(
    "/reflect",
    dependencies=[Depends(require_permission("governance:write"))],
)
async def trigger_reflection(req: ReflectionRequest):
    """触发一次完整离线反思。

    合成的规则一律为 draft 状态——必须经人工 `POST /rules/{id}/publish`
    放行后才进入灰度管线（AI 起草、人确认、物理闸门兜底）。
    """
    from app.dreaming.service import run_reflection

    summary = await run_reflection(
        lookback_days=req.lookback_days,
        max_sessions=req.max_sessions,
        instructions=req.instructions,
        enable_llm=req.enable_llm,
        include_ar_02=req.include_ar_02,
    )
    if not summary.ok:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=summary.error or "反思未完成",
            detail=summary.to_dict(),
            suggestion="请检查数据源配置（MLflow / CAM 报告 / 审计日志目录）后重试",
        )
    return success(
        data=summary.to_dict(),
        message=f"反思完成：{summary.session_count} 个 Session，合成 {summary.draft_rule_count} 条规则草稿",
    )


@router.get("/rules")
async def list_rules(
    status: str | None = Query(None, description="按草稿状态过滤（如 draft）"),
):
    """规则草稿 × 灰度状态联查。"""
    publisher = _get_publisher()
    store = _get_drafts_store()
    drafts = store.load_rules(status=status)
    rules = []
    for draft in drafts:
        record = publisher.get_record(draft.rule_id)
        rules.append(
            {
                **draft.to_dict(),
                "publication": record.to_dict() if record else None,
            }
        )
    return success(
        data={"rules": rules, "total": len(rules)},
        message=f"共 {len(rules)} 条规则",
    )


@router.post(
    "/rules/{rule_id}/publish",
    dependencies=[Depends(require_permission("governance:write"))],
)
async def publish_rule(rule_id: str):
    """人工放行规则草稿进入灰度 SHADOW 阶段（影子模式，0% 流量仅记录）。"""
    draft = _find_draft(rule_id)
    if draft is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"规则草稿不存在：{rule_id}")
    result = _get_publisher().publish(draft, stage=PublicationStage.SHADOW)
    if not result.success:
        return error(code=ErrorCode.INVALID_REQUEST, message=result.error or "发布失败")
    return success(data=result.to_dict(), message=f"规则 {rule_id} 已进入灰度 SHADOW 阶段")


@router.post(
    "/rules/{rule_id}/promote",
    dependencies=[Depends(require_permission("governance:write"))],
)
async def promote_rule(rule_id: str, req: PromoteRequest):
    """灰度晋级。FULL 阶段由发布器强制重新沙箱校验（双重校验）。"""
    publisher = _get_publisher()
    record = publisher.get_record(rule_id)
    if record is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"规则未发布，无法晋级：{rule_id}")

    target_stage: PublicationStage | None = None
    if req.target_stage:
        try:
            target_stage = PublicationStage(req.target_stage)
        except ValueError:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=f"未知灰度阶段：{req.target_stage}",
                suggestion="可选：shadow / canary / rolling_10 / rolling_50 / full",
            )

    # 与发布器契约对齐：仅当晋级目标是 FULL 时才需要附上规则草稿（重新校验用）
    resolved = target_stage or record.current_stage.next_stage
    draft = _find_draft(rule_id) if resolved == PublicationStage.FULL else None
    if resolved == PublicationStage.FULL and draft is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"晋级 FULL 需要规则草稿用于重新沙箱校验，但未找到：{rule_id}",
        )

    result = publisher.promote(
        rule_id,
        target_stage=target_stage,
        metrics_snapshot=req.metrics,
        rule=draft,
    )
    if not result.success:
        return error(code=ErrorCode.INVALID_REQUEST, message=result.error or "晋级失败")
    return success(data=result.to_dict(), message=f"规则 {rule_id} 晋级成功")


@router.post(
    "/rules/{rule_id}/rollback",
    dependencies=[Depends(require_permission("governance:write"))],
)
async def rollback_rule(rule_id: str, req: RollbackRequest):
    """一键回滚：硬约束违反直接废弃；普通异常经灰度逐级降级。含冷却期与审计。"""
    publisher = _get_publisher()
    manager = _get_rollback_manager(publisher)
    result = manager.rollback_rule(
        rule_id,
        reason=req.reason,
        severity=req.severity,
        fully_deprecate=req.fully_deprecate,
    )
    return success(
        data=result.to_dict(),
        message=f"规则 {rule_id} 已回滚：{result.previous_stage} → {result.current_stage}",
    )


@router.get("/status")
async def get_status():
    """子系统状态：灰度阶段分布、草稿规模、最近一次反思。"""
    publisher = _get_publisher()
    records = publisher.list_publications()
    stage_counts: dict[str, int] = {}
    for record in records:
        stage_counts[record.current_stage.value] = stage_counts.get(record.current_stage.value, 0) + 1

    drafts = _get_drafts_store().load_rules()
    last_reflection = _latest_reflection_info()

    return success(
        data={
            "draft_count": len(drafts),
            "published_count": len(records),
            "stage_counts": stage_counts,
            "last_reflection": last_reflection,
        },
        message="Dreaming 子系统状态",
    )


@router.get("/learnings")
async def get_learnings(
    days: int = Query(7, ge=1, le=90, description="回看窗口（天）"),
):
    """「它这周学会了什么」：窗口期内的新增规则草稿 + 灰度事件流（看板数据源）。"""
    publisher = _get_publisher()
    store = _get_drafts_store()
    window_start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    events: list[dict[str, Any]] = []
    for draft in store.load_rules():
        if draft.created_at >= window_start:
            events.append(
                {
                    "type": "draft_created",
                    "rule_id": draft.rule_id,
                    "description": draft.description,
                    "confidence": draft.confidence,
                    "operated_at": draft.created_at,
                    "source_insight_category": draft.source_insight_category,
                }
            )
    for record in publisher.list_publications():
        for entry in record.stage_history:
            if entry.get("operated_at", "") >= window_start:
                events.append(
                    {
                        "type": f"stage_{entry.get('action', 'change')}",
                        "rule_id": record.rule_id,
                        "from_stage": entry.get("from") or entry.get("stage"),
                        "to_stage": entry.get("to") or entry.get("stage"),
                        "traffic_percentage": entry.get("traffic_percentage"),
                        "reason": entry.get("reason"),
                        "operated_at": entry.get("operated_at", ""),
                    }
                )

    events.sort(key=lambda e: e.get("operated_at", ""), reverse=True)
    return success(
        data={"days": days, "events": events, "total": len(events)},
        message=f"最近 {days} 天共 {len(events)} 条学习事件",
    )


# ---- 内部辅助 ----


def _find_draft(rule_id: str):
    store = _get_drafts_store()
    for draft in store.load_rules():
        if draft.rule_id == rule_id:
            return draft
    return None


def _latest_reflection_info() -> dict[str, Any] | None:
    reports_dir = _get_reports_dir()
    if not reports_dir.exists():
        return None
    candidates = sorted(reports_dir.glob("reflection_*.json"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        return None
    latest = candidates[-1]
    return {
        "file": latest.name,
        "modified_at": datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).isoformat(),
    }
