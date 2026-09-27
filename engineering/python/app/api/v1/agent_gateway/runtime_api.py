"""AgentRuntime 端点（Agent Gateway · 自进化 M2 三端接线）。

把统一 AgentRuntime 暴露给 GUI 与外部调用方——与 headless 用法共享
同一单例、同一工具面、同一轨迹存储：

- ``POST /agent-runtime/run``    —— 执行任务（ReAct 多轮工具调用），
  返回完整轨迹（status / final_answer / 每轮观测 / prompt 版本 / 模型）；
- ``GET /agent-runtime/traces``  —— 最近执行轨迹（可观测性 / 调试）。

LLM 依赖说明：runtime 走 Provider 网关的激活 Provider（本地 Ollama
或云端）。LLM 不可用时返回 ``trace.status = "llm_error"``（HTTP 仍为
200——轨迹本身就是产物，调用方据此降级展示）；连续格式非法返回
``invalid_output_circuit`` 并已按质量信号入册失败案例库（自进化 M0
口径），步数耗尽返回 ``max_steps``。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import success
from app.core.response_models import ErrorResponse, SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent Gateway"])

_MAX_TASK_CHARS = 20000


class AgentRuntimeRunRequest(BaseModel):
    """AgentRuntime 执行请求。"""

    task: str = Field(min_length=1, max_length=_MAX_TASK_CHARS, description="任务描述（自然语言）")
    record: bool = Field(default=True, description="是否把轨迹落盘（评估/回放场景可关）")


@router.post(
    "/agent-runtime/run",
    dependencies=[Depends(require_permission("agent:execute"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
async def agent_runtime_run(req: AgentRuntimeRunRequest):
    """执行统一 Agent 任务：任务描述 → 多轮工具调用 → 最终回答 + 轨迹。

    与 headless（``get_agent_runtime().run``）完全同源：同一工具注册表
    （与 mcp_server 同源后端）、同一 ReAct 提示词（Prompt Registry 版本
    可追溯）、同一 JSONL 轨迹存储（未来 RL rollout 数据源）。
    """
    from app.agent.runtime import get_agent_runtime

    try:
        trace = await get_agent_runtime().run(req.task, record=req.record)
    except Exception as e:  # noqa: BLE001 - 兜底：runtime 层不该抛，防御性收敛
        logger.error("agent-runtime/run 执行异常: %s", e, exc_info=True)
        return success(
            data={
                "trace_id": "",
                "task": req.task,
                "status": "llm_error",
                "final_answer": "",
                "error": f"{type(e).__name__}: {e}",
            },
            message="Agent 执行异常（已收敛为错误轨迹）",
        )
    return success(
        data=trace.to_dict(),
        message=f"Agent 执行完成: {trace.status}",
    )


@router.get(
    "/agent-runtime/traces",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[list[dict[str, Any]]],
    responses={500: {"model": ErrorResponse}},
)
def agent_runtime_traces(
    limit: int = Query(default=20, ge=1, le=100, description="返回条数"),
):
    """读取最近的 Agent 执行轨迹（按时间倒序）。"""
    from app.agent.runtime import get_agent_runtime

    store = get_agent_runtime().trace_store
    return success(
        data=store.list_recent(limit=limit),
        message="轨迹读取成功",
    )
