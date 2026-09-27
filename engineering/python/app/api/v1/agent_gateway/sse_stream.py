"""SSE 流式响应 + 心跳逻辑。

P1-7：从原 ``agent_gateway.py`` 拆分而来，包含：
- ``GET /train/{job_id}/stream`` —— 训练进度 SSE 流
- ``_sse_stream`` —— SSE 生成器（含心跳超时，复用 ``SSE_HEARTBEAT_TIMEOUT``）
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.v1.agent_gateway._state import (
    SSE_HEARTBEAT_TIMEOUT,
    training_tasks,
)
from app.api.v1.sse import sse_manager
from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent Gateway"])


async def _sse_stream(task_id: str, client_id: str):
    client = await sse_manager.subscribe(task_id, client_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(client.queue.get(), timeout=SSE_HEARTBEAT_TIMEOUT)
                yield event
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    except asyncio.CancelledError:
        # SSE 连接被客户端主动关闭时退出（业务预期行为）。
        # P1-6 修复：不静默 pass——记录 debug 日志便于排查 SSE 生命周期异常，
        # 如频繁取消可能暗示客户端连接泄漏或心跳超时配置不当。
        logger.debug("SSE stream cancelled for task_id=%s, client_id=%s", task_id, client_id)
    finally:
        await sse_manager.unsubscribe(task_id, client_id)


@router.get("/train/{job_id}/stream", dependencies=[Depends(require_permission("agent:read"))])
async def stream_training(job_id: str):
    """训练进度SSE流（R类）"""
    if job_id not in training_tasks:
        return error(code=ErrorCode.NOT_FOUND, message=f"Training task '{job_id}' not found")

    client_id = f"agent_{uuid.uuid4().hex[:8]}"
    return StreamingResponse(
        _sse_stream(job_id, client_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
