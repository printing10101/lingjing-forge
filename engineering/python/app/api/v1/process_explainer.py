"""工艺 / NC 代码对话式解释 API。

落地竞品分析中识别的 SolidWorks AURA 式 LLM 对话解释补强点。

端点前缀：/api/v1/process-explainer

端点列表：
1. POST /explain-process       解释工艺规划
2. POST /explain-nc            解释 NC / G 代码
3. POST /chat                  多轮对话（基于会话历史）
4. POST /sessions              创建新会话
5. GET  /sessions/{id}         获取会话历史
6. DELETE /sessions/{id}       清空会话
7. POST /cleanup               清理过期会话
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.core.response import success
from app.core.endpoint_handler import safe_endpoint
from app.auth.permissions import require_permission

# P2-4-5 修复：引入共享速率限制器，LLM 对话解释消耗大量推理资源，需速率限制防止 DoS。
from app.middleware.rate_limiter import limiter
from app.ai.process_explainer import get_process_explainer

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/process-explainer",
    tags=["ProcessExplainer"],
    dependencies=[Depends(require_permission("explainer:read"))],
)


# 请求模型


class ExplainProcessRequest(BaseModel):
    """工艺规划解释请求。"""

    process_plan: dict[str, Any] = Field(..., description="工艺规划 JSON")
    user_question: str = Field(default="", description="用户上下文问题")
    material: str = Field(default="", description="工件材料")
    blank_size: str = Field(default="", description="毛坯尺寸描述")
    feature_count: int | None = Field(default=None, ge=0, description="加工特征数（None 自动推断）")
    session_id: str | None = Field(default=None, description="会话 ID（None 新建）")


class ExplainNCRequest(BaseModel):
    """NC 代码解释请求。"""

    nc_code: str = Field(..., min_length=1, description="NC/G 代码文本")
    controller_type: str = Field(default="fanuc", description="控制器类型（fanuc/siemens/heidenhain 等）")
    user_question: str = Field(default="", description="用户上下文问题")
    session_id: str | None = Field(default=None, description="会话 ID（None 新建）")


class ChatRequest(BaseModel):
    """多轮对话请求。"""

    message: str = Field(..., min_length=1, description="用户消息")
    session_id: str | None = Field(default=None, description="会话 ID（None 新建）")


# 1. 解释工艺规划


@router.post("/explain-process", dependencies=[Depends(require_permission("explainer:read"))])
# P2-4-5 修复：LLM 对话解释消耗推理资源，限制为 20/minute。
@limiter.limit("20/minute")
@safe_endpoint(context="process_explainer.explain_process", fallback="解释失败")
async def explain_process(request: Request, req: ExplainProcessRequest):
    """将工艺规划（特征→工艺→刀具→参数）转为自然语言解释。"""
    explainer = get_process_explainer()
    result = await explainer.explain_process(
        process_plan=req.process_plan,
        user_question=req.user_question,
        material=req.material,
        blank_size=req.blank_size,
        feature_count=req.feature_count,
        session_id=req.session_id,
    )
    return success(
        data=result.to_dict(),
        message="工艺解释完成" if result.mode == "llm" else "已降级为规则化解释",
    )


# 2. 解释 NC 代码


@router.post("/explain-nc", dependencies=[Depends(require_permission("explainer:read"))])
# P2-4-5 修复：LLM 对话解释消耗推理资源，限制为 20/minute。
@limiter.limit("20/minute")
@safe_endpoint(context="process_explainer.explain_nc", fallback="解释失败")
async def explain_nc(request: Request, req: ExplainNCRequest):
    """解释 NC / G 代码（结合 ToolpathParser 结构化解析）。"""
    explainer = get_process_explainer()
    result = await explainer.explain_nc_code(
        nc_code=req.nc_code,
        controller_type=req.controller_type,
        user_question=req.user_question,
        session_id=req.session_id,
    )
    return success(
        data=result.to_dict(),
        message="NC 代码解释完成" if result.mode == "llm" else "已降级为规则化解释",
    )


# 3. 多轮对话


@router.post("/chat", dependencies=[Depends(require_permission("explainer:read"))])
# P2-4-5 修复：LLM 多轮对话消耗推理资源，限制为 20/minute。
@limiter.limit("20/minute")
@safe_endpoint(context="process_explainer.chat", fallback="对话失败")
async def chat(request: Request, req: ChatRequest):
    """多轮对话：基于会话历史的上下文追问。"""
    explainer = get_process_explainer()
    result = await explainer.chat(
        user_message=req.message,
        session_id=req.session_id,
    )
    return success(
        data=result.to_dict(),
        message="对话回复完成" if result.mode == "llm" else "已降级为规则化解释",
    )


# 4. 创建新会话


@router.post("/sessions", dependencies=[Depends(require_permission("explainer:write"))])
@safe_endpoint(context="process_explainer.create_session", fallback="创建会话失败")
async def create_session():
    """创建新的对话会话，返回 session_id。"""
    explainer = get_process_explainer()
    store = explainer._store
    session_id = await store.create_session()
    return success(
        data={"session_id": session_id},
        message="会话已创建",
    )


# 5. 获取会话历史


@router.get("/sessions/{session_id}")
@safe_endpoint(context="process_explainer.get_session", fallback="获取历史失败")
async def get_session(
    session_id: str,
    # P2-批次2 修复：裸参数改用 Query 校验，避免负数/超大值穿透到服务层。
    limit: int = Query(20, ge=1, le=100, description="返回消息数量（1-100）"),
):
    """获取指定会话的历史消息。"""
    explainer = get_process_explainer()
    history = await explainer.get_session_history(session_id, limit)
    return success(
        data={
            "session_id": session_id,
            "message_count": len(history),
            "messages": history,
        }
    )


# 6. 清空会话


@router.delete("/sessions/{session_id}", dependencies=[Depends(require_permission("explainer:write"))])
@safe_endpoint(context="process_explainer.clear_session", fallback="清空会话失败")
async def clear_session(session_id: str):
    """清空指定会话的所有消息。"""
    explainer = get_process_explainer()
    deleted = await explainer.clear_session(session_id)
    return success(
        data={"session_id": session_id, "deleted_count": deleted},
        message=f"已清空 {deleted} 条消息",
    )


# 7. 清理过期会话


@router.post("/cleanup", dependencies=[Depends(require_permission("explainer:write"))])
@safe_endpoint(context="process_explainer.cleanup_expired", fallback="清理失败")
async def cleanup_expired():
    """清理过期会话（默认 7 天前的会话）。"""
    explainer = get_process_explainer()
    deleted = await explainer._store.cleanup_expired()
    return success(
        data={"deleted_count": deleted},
        message=f"已清理 {deleted} 条过期消息",
    )
