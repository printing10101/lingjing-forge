"""自进化 REST 接口（自进化 M1 · 闭环运转的人工操作面）。

提供：
- 统计快照与提示词版本总览（GET /stats）
- 提案清单（GET /proposals）
- 演化循环手动触发（POST /loop/run，提案制：只统计+提案+报告）
- 提案审核（promote = 门控发布 / reject / rollback）
- 进化报告读取（GET /reports）

触发类端点与心跳 cron 走同一引擎；promote 内部跑 replay 门控
（含真实 pipeline），一律经 ``asyncio.to_thread`` 调度。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.core.response_models import ErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/evolution",
    tags=["Evolution"],
    dependencies=[Depends(require_permission("governance:write"))],
)


class LoopRunRequest(BaseModel):
    """演化循环触发请求。"""

    target_prompt_id: str | None = Field(None, description="提案目标提示词 ID（默认 orchestrator.gcode_repair.system）")


def _engine() -> Any:
    from app.evolution.loop import get_evolution_engine

    return get_evolution_engine()


@router.get("/stats", responses={500: {"model": ErrorResponse}})
async def get_evolution_stats():
    """失败案例库统计 + 失败 TOP 类别 + 提示词版本总览。"""
    engine = _engine()
    collected = engine.collect_stats()
    registry_versions: dict[str, list[int]] = {}
    try:
        from app.ai.prompts import get_prompt_registry

        registry = get_prompt_registry()
        for pid in registry.list_ids():
            registry_versions[pid] = registry.versions(pid)
    except Exception as e:  # noqa: BLE001
        logger.warning("提示词版本总览读取失败: %s", e)
    return success(
        data={
            **collected,
            "prompt_versions": registry_versions,
        },
        message="演化统计获取成功",
    )


@router.get("/proposals", responses={500: {"model": ErrorResponse}})
async def list_proposals():
    """全部提示词候选/应用版本记录（proposed/applied/rejected/rolled_back）。"""
    from app.ai.prompts import persistence as prompt_persistence

    return success(
        data={"records": prompt_persistence.load_records()},
        message="提案清单获取成功",
    )


@router.post("/loop/run", responses={500: {"model": ErrorResponse}})
async def run_evolution_loop(req: LoopRunRequest | None = None):
    """手动触发一次演化循环（统计→提案→报告；提案制不自动发布）。"""
    from app.evolution.loop import DEFAULT_TARGET_PROMPT_ID

    target = (req.target_prompt_id if req else None) or DEFAULT_TARGET_PROMPT_ID
    result = await _engine().run_loop(target_prompt_id=target)
    return success(
        data=result.to_dict(),
        message="演化循环执行完成" if result.ok else "演化循环执行失败",
    )


def _load_record_or_404(prompt_id: str, version: int) -> dict[str, Any]:
    from app.ai.prompts import persistence as prompt_persistence

    record = prompt_persistence.find_record(prompt_id, version)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"提案记录不存在: {prompt_id} v{version}",
        )
    return record


@router.post(
    "/proposals/{prompt_id}/{version}/promote",
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def promote_proposal(prompt_id: str, version: int):
    """候选版本门控发布：热更新上线 → replay 回归门控 → FAIL 自动回滚。"""
    _load_record_or_404(prompt_id, version)
    # 门控内部跑真实 pipeline（asyncio.run），必须放线程执行
    result = await asyncio.to_thread(_engine().promote, prompt_id, version)
    if not result.ok:
        return error(code=ErrorCode.INVALID_REQUEST, message=result.error)
    return success(data=result.to_dict(), message=f"提案处理完成: {result.final_status}")


@router.post(
    "/proposals/{prompt_id}/{version}/reject",
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def reject_proposal(prompt_id: str, version: int):
    """拒绝提案（候选不进线上注册表，仅留痕）。"""
    _load_record_or_404(prompt_id, version)
    result = _engine().reject(prompt_id, version)
    if not result.ok:
        return error(code=ErrorCode.INVALID_REQUEST, message=result.error)
    return success(data=result.to_dict(), message="提案已拒绝")


@router.post(
    "/proposals/{prompt_id}/{version}/rollback",
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def rollback_proposal(prompt_id: str, version: int):
    """回滚已应用的提示词版本（线上立即回到剩余最高版本）。"""
    _load_record_or_404(prompt_id, version)
    result = _engine().rollback(prompt_id, version)
    if not result.ok:
        return error(code=ErrorCode.INVALID_REQUEST, message=result.error)
    return success(data=result.to_dict(), message="版本已回滚")


@router.get("/reports", responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
async def list_reports():
    """最近一份进化报告内容 + 报告文件列表。"""
    from app.evolution.task_handler import latest_report
    from app.evolution.loop import _report_dir

    files = sorted(p.name for p in _report_dir().glob("evolution_*.json"))
    return success(
        data={**latest_report(), "files": files[-20:]},
        message="进化报告获取成功",
    )
