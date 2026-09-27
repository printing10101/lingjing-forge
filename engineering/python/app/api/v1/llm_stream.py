"""LLM 流式对话端点（SSE）。

2026-09 全量升格（AI 深度参与）的地基设施：此前全后端 LLM 调用硬编码
``stream=False``，但 ``ProviderCapability.STREAMING`` 能力标签处处声明——
声明与实现不符。本模块补上端到端流式链路的最后一环：

    前端 SSE → POST /api/v1/llm/chat/stream
            → get_llm_client()（Provider 网关或 legacy 客户端）
            → chat_completion_stream()（原生 NDJSON/SSE 流或伪流式降级）
            → 韧性层（熔断快速失败 + 6xxx 异常桥接）

事件契约（``data: <json>`` 帧）：
    - 内容帧：``{"type": "content", "content": str, "model": str, "done": bool,
      "stream_mode": "native"|"pseudo"}``
    - 错误帧：``{"type": "error", "code": 6xxx|9xxx, "message": str,
      "level": str, "retryable": bool, "hint": str}``（6xxx 分级异常直通）
    - 终止帧：``data: [DONE]``
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.auth.permissions import require_permission
from app.core.exceptions import AppException
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/llm", tags=["LLM Stream"])

#: 允许的对话角色（非法 role 会在上游产生难解的 4xx，前置校验）
_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})


class ChatStreamRequest(BaseModel):
    """流式对话请求。"""

    messages: list[dict[str, str]] = Field(..., description="消息列表 [{role, content}]")
    max_tokens: int = Field(2048, ge=1, le=32768)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    model: str | None = Field(None, description="模型名，None 使用当前激活模型")

    @field_validator("messages")
    @classmethod
    def _validate_messages(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        if not value:
            raise ValueError("messages 不能为空")
        for i, msg in enumerate(value):
            role = msg.get("role", "")
            if role not in _ALLOWED_ROLES:
                raise ValueError(f"messages[{i}].role 非法: {role!r}（允许 {sorted(_ALLOWED_ROLES)}）")
            if not isinstance(msg.get("content"), str):
                raise ValueError(f"messages[{i}].content 必须为字符串")
        return value


def _sse_frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _chat_stream_events(req: ChatStreamRequest):
    """SSE 事件生成器：内容帧 → （错误帧）→ [DONE]。

    异常处理契约：
    - AppException（6xxx/9xxx 分级异常，由韧性层桥接产生）：错误帧直通
      code/level/retryable/hint，前端可按分级提示；
    - 其他异常：经 safe_error_message 脱敏后以 6001 兜底错误帧返回，
      不向客户端泄露内部堆栈。
    """
    from app.ai.llm_client import get_llm_client

    try:
        client = await get_llm_client()
    except AppException as exc:
        yield _sse_frame({"type": "error", **exc.to_dict()})
        yield "data: [DONE]\n\n"
        return
    except Exception as exc:
        info = safe_error_message(exc)
        yield _sse_frame(
            {
                "type": "error",
                "code": 6001,
                "message": info.get("message", str(exc)),
                "level": "error",
                "retryable": True,
                "hint": "请检查 AI 服务状态",
            }
        )
        yield "data: [DONE]\n\n"
        return

    try:
        async for chunk in client.chat_completion_stream(
            req.messages,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            model=req.model,
        ):
            yield _sse_frame({"type": "content", **chunk})
    except AppException as exc:
        logger.warning("LLM 流式调用分级异常 code=%s: %s", exc.code, exc.message)
        yield _sse_frame({"type": "error", **exc.to_dict()})
    except Exception as exc:
        info = safe_error_message(exc)
        logger.warning("LLM 流式调用异常: %s", info.get("message", str(exc)))
        yield _sse_frame(
            {
                "type": "error",
                "code": 6001,
                "message": info.get("message", str(exc)),
                "level": "error",
                "retryable": True,
                "hint": "请检查 AI 服务状态",
            }
        )
    yield "data: [DONE]\n\n"


@router.post(
    "/chat/stream",
    dependencies=[Depends(require_permission("llm:chat"))],
)
async def chat_stream(req: ChatStreamRequest):
    """LLM 流式对话补全（SSE，text/event-stream）。

    支持原生流式的后端（Ollama NDJSON / OpenAI 兼容 SSE）逐 token 输出
    （stream_mode=native）；其余后端自动降级为伪流式（单 chunk，
    stream_mode=pseudo），契约统一，前端无需分支处理。
    """
    return StreamingResponse(
        _chat_stream_events(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
