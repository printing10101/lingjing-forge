"""
Skill Management API Routes

Provides RESTful interfaces for skill CRUD, hot-reload,
version management, and skill marketplace operations.
"""

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.plugins.skill_loader import (
    SkillLevel,
    get_skill_loader,
    init_skill_loader,
    inject_skills as inject_skills_fn,
)
from app.plugins.skill_marketplace import get_marketplace
from app.core.response import ErrorCode, success, error
from app.core.safe_errors import safe_error_message
from app.auth.permissions import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/skills",
    tags=["skills"],
    dependencies=[Depends(require_permission("skills:read"))],
)


class SkillContentRequest(BaseModel):
    skill_id: str = Field(..., description="技能唯一标识符")
    content: str = Field(..., description="技能 Markdown 完整内容")
    level: str = Field(default="project", description="技能层级: global/project/agent")
    sub_id: str | None = Field(default=None, description="项目ID或代理ID")


class SkillExportRequest(BaseModel):
    skill_id: str = Field(..., description="要导出的技能ID")


class SkillImportRequest(BaseModel):
    skill_package: dict[str, Any] = Field(..., description="技能包数据")
    level: str = Field(default="project", description="导入层级")
    sub_id: str | None = Field(default=None, description="项目ID或代理ID")


class SkillRatingRequest(BaseModel):
    skill_id: str = Field(..., description="技能ID")
    rating: float = Field(..., ge=0, le=5, description="评分 (0-5)")


class SkillPublishRequest(BaseModel):
    skill_id: str = Field(..., description="技能ID")
    author: str = Field(..., description="发布者")


class SkillDownloadRequest(BaseModel):
    skill_id: str = Field(..., description="技能ID")
    target_level: str = Field(default="project", description="目标层级")
    target_sub_id: str | None = Field(default=None, description="目标项目/代理ID")


class SkillMarketplaceRateRequest(BaseModel):
    skill_id: str = Field(..., description="技能ID")
    rating: float = Field(..., ge=0, le=5, description="评分 (0-5)")
    agent_id: str = Field(default="", description="评分的代理ID")


def _parse_level(level_str: str) -> SkillLevel:
    level_map = {
        "global": SkillLevel.GLOBAL,
        "project": SkillLevel.PROJECT,
        "agent": SkillLevel.AGENT,
    }
    level = level_map.get(level_str.lower())
    if level is None:
        logger.info("Invalid skill level: %s", level_str)
        raise HTTPException(status_code=400, detail="Invalid skill level")
    return level


@router.get("")
async def list_skills(
    level: str | None = Query(None, description="按层级筛选: global/project/agent"),
    project_id: str | None = Query(None, description="项目ID"),
    agent_id: str | None = Query(None, description="代理ID"),
    task_type: str | None = Query(None, description="任务类型"),
):
    try:
        loader = get_skill_loader()
        if task_type:
            skills = loader.get_skills_for_task(
                task_type=task_type,
                project_id=project_id,
                agent_id=agent_id,
            )
        elif level:
            parsed_level = _parse_level(level)
            skills = loader.registry.get_by_level(parsed_level)
        else:
            skills = loader.registry.list_all()

        result = []
        for s in skills:
            result.append(
                {
                    "skill_id": s.metadata.skill_id,
                    "name": s.metadata.name,
                    "display_name": s.metadata.display_name,
                    "version": s.metadata.version,
                    "description": s.metadata.description,
                    "level": s.metadata.level.value,
                    "priority": s.metadata.priority.value,
                    "applicable_tasks": s.metadata.applicable_tasks,
                    "required_context": list(s.metadata.required_context),
                    "tags": s.metadata.tags,
                    "parameters": s.metadata.parameters,
                    "ratings": s.metadata.ratings,
                    "active": s.is_active,
                    "source_path": s.metadata.source_path,
                }
            )

        return success(data=result, message=f"共 {len(result)} 个技能")
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.list", fallback="技能列表查询失败，请稍后重试")
        logger.error("[skills.list] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.get("/stats")
async def get_skill_stats():
    try:
        loader = get_skill_loader()
        stats = loader.get_stats()
        marketplace = get_marketplace()
        market_stats = marketplace.get_stats()
        stats["marketplace"] = market_stats
        return success(data=stats, message="技能系统统计")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.stats", fallback="技能统计查询失败，请稍后重试")
        logger.error("[skills.stats] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/create", dependencies=[Depends(require_permission("skills:write"))])
async def create_skill(request: SkillContentRequest):
    try:
        level = _parse_level(request.level)
        loader = get_skill_loader()
        file_path = loader.save_skill_file(
            skill_id=request.skill_id,
            content=request.content,
            level=level,
            sub_id=request.sub_id,
        )
        return success(
            data={"skill_id": request.skill_id, "file_path": file_path},
            message=f"技能已创建: {request.skill_id}",
        )
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：技能创建涉及文件 IO + 路径校验 + 注册
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.create", fallback="技能创建失败，请稍后重试")
        logger.error("[skills.create] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.put("/{skill_id}", dependencies=[Depends(require_permission("skills:write"))])
async def update_skill(skill_id: str, request: SkillContentRequest):
    try:
        level = _parse_level(request.level)
        loader = get_skill_loader()
        file_path = loader.save_skill_file(
            skill_id=skill_id,
            content=request.content,
            level=level,
            sub_id=request.sub_id,
        )
        return success(
            data={"skill_id": skill_id, "file_path": file_path},
            message=f"技能已更新: {skill_id}",
        )
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.update", fallback="技能更新失败，请稍后重试")
        logger.error("[skills.update] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.delete("/{skill_id}", dependencies=[Depends(require_permission("skills:write"))])
async def delete_skill(skill_id: str):
    try:
        loader = get_skill_loader()
        skill = loader.registry.get(skill_id)
        if skill is None:
            return error(ErrorCode.NOT_FOUND, f"技能不存在: {skill_id}")

        file_path = skill.metadata.source_path
        loader.registry.remove(skill_id)

        if file_path and os.path.exists(file_path):
            os.remove(file_path)

        return success(data={"skill_id": skill_id}, message=f"技能已删除: {skill_id}")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：技能删除涉及文件系统 + 注册表清理
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.delete", fallback="技能删除失败，请稍后重试")
        logger.error("[skills.delete] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.get("/{skill_id}")
async def get_skill(skill_id: str):
    try:
        loader = get_skill_loader()
        skill = loader.registry.get(skill_id)
        if skill is None:
            return error(ErrorCode.NOT_FOUND, f"技能不存在: {skill_id}")

        return success(
            data={
                "skill_id": skill.metadata.skill_id,
                "name": skill.metadata.name,
                "display_name": skill.metadata.display_name,
                "version": skill.metadata.version,
                "description": skill.metadata.description,
                "level": skill.metadata.level.value,
                "priority": skill.metadata.priority.value,
                "applicable_tasks": skill.metadata.applicable_tasks,
                "required_context": list(skill.metadata.required_context),
                "tags": skill.metadata.tags,
                "parameters": skill.metadata.parameters,
                "ratings": skill.metadata.ratings,
                "active": skill.is_active,
                "source_path": skill.metadata.source_path,
                "body": skill.body[:5000] if skill.body else "",
                "code_blocks": [(lang, code[:500]) for lang, code in skill.code_blocks[:5]],
                "version_count": len(skill.versions),
            },
            message="技能详情",
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.get", fallback="技能详情查询失败，请稍后重试")
        logger.error("[skills.get] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/reload", dependencies=[Depends(require_permission("skills:write"))])
async def reload_skills(skill_id: str | None = None):
    try:
        loader = get_skill_loader()
        result = loader.hot_reload(skill_id)
        if skill_id:
            return success(data=result, message=f"技能热重载: {skill_id}")
        init_skill_loader()
        return success(data=result, message="全量技能重新加载完成")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.reload", fallback="技能重载失败，请稍后重试")
        logger.error("[skills.reload] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.get("/{skill_id}/versions")
async def get_skill_versions(skill_id: str):
    try:
        loader = get_skill_loader()
        history = loader.get_version_history(skill_id)
        if history is None:
            return error(ErrorCode.NOT_FOUND, f"技能不存在: {skill_id}")

        return success(
            data={
                "skill_id": skill_id,
                "version_count": len(history),
                "versions": history,
            },
            message="版本历史",
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：版本历史查询涉及文件 IO + 注册表
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.get_versions", fallback="技能版本历史查询失败，请稍后重试")
        logger.error("[skills.get_versions] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/export", dependencies=[Depends(require_permission("skills:write"))])
async def export_skill(request: SkillExportRequest):
    try:
        loader = get_skill_loader()
        package = loader.export_skill(request.skill_id)
        if package is None:
            return error(ErrorCode.NOT_FOUND, f"技能不存在: {request.skill_id}")

        return success(data=package, message=f"技能已导出: {request.skill_id}")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.export", fallback="技能导出失败，请稍后重试")
        logger.error("[skills.export] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/import", dependencies=[Depends(require_permission("skills:write"))])
async def import_skill(request: SkillImportRequest):
    try:
        level = _parse_level(request.level)
        loader = get_skill_loader()
        imported = loader.import_skill(
            request.skill_package,
            level=level,
            sub_id=request.sub_id,
        )
        if imported is None:
            return error(ErrorCode.INVALID_REQUEST, "技能导入失败：解析错误")

        return success(
            data={
                "skill_id": imported.metadata.skill_id,
                "name": imported.metadata.name,
            },
            message=f"技能已导入: {imported.metadata.name}",
        )
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.import", fallback="技能导入失败，请稍后重试")
        logger.error("[skills.import] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/rate", dependencies=[Depends(require_permission("skills:write"))])
async def rate_skill(request: SkillRatingRequest):
    try:
        loader = get_skill_loader()
        result = loader.rate_skill(request.skill_id, request.rating)
        return success(data=result, message="评分已记录")
    except KeyError as e:
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="skills.rate.not_found", fallback="技能不存在")
        logger.error("[skills.rate.not_found] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.NOT_FOUND, message=safe["message"], detail={"error_id": safe["error_id"]})
    except ValueError as e:
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="skills.rate.invalid", fallback="请求参数无效")
        logger.error("[skills.rate.invalid] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INVALID_REQUEST, message=safe["message"], detail={"error_id": safe["error_id"]})
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.rate", fallback="技能评分失败，请稍后重试")
        logger.error("[skills.rate] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/inject", dependencies=[Depends(require_permission("skills:write"))])
async def inject_skills_endpoint(
    task_type: str = Query(..., description="任务类型"),
    project_id: str | None = Query(None, description="项目ID"),
    agent_id: str | None = Query(None, description="代理ID"),
    available_context: list[str] | None = Query(None, description="可用上下文键列表"),
):
    try:
        ctx_set = set(available_context) if available_context else set()
        context_str = await inject_skills_fn(task_type, project_id, agent_id, ctx_set)
        return success(
            data={
                "task_type": task_type,
                "skill_context": context_str,
            },
            message="技能注入完成",
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.inject", fallback="技能注入失败，请稍后重试")
        logger.error("[skills.inject] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# ─── 技能市场 ───


@router.get("/marketplace/list")
async def marketplace_list(tag: str | None = Query(None, description="按标签筛选")):
    try:
        marketplace = get_marketplace()
        items = marketplace.list_available(tag)
        return success(data=items, message=f"市场共 {len(items)} 个技能")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：市场列表查询涉及存储 + 缓存
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.marketplace_list", fallback="技能市场列表查询失败，请稍后重试")
        logger.error("[skills.marketplace_list] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.get("/marketplace/search")
async def marketplace_search(query: str = Query(..., description="搜索关键词")):
    try:
        marketplace = get_marketplace()
        items = marketplace.search(query)
        return success(data=items, message=f"搜索到 {len(items)} 个技能")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：市场搜索涉及文本匹配 + 索引
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.marketplace_search", fallback="技能市场搜索失败，请稍后重试")
        logger.error("[skills.marketplace_search] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/marketplace/publish", dependencies=[Depends(require_permission("skills:write"))])
async def marketplace_publish(request: SkillPublishRequest):
    try:
        marketplace = get_marketplace()
        result = marketplace.publish(request.skill_id, request.author)
        if result is None:
            return error(ErrorCode.NOT_FOUND, f"技能不存在: {request.skill_id}")

        return success(data=result, message=f"已发布: {request.skill_id}")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：发布操作涉及网络 + 存储
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.marketplace_publish", fallback="技能发布失败，请稍后重试")
        logger.error("[skills.marketplace_publish] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/marketplace/download", dependencies=[Depends(require_permission("skills:write"))])
async def marketplace_download(request: SkillDownloadRequest):
    try:
        level = _parse_level(request.target_level)
        marketplace = get_marketplace()
        result = marketplace.download(request.skill_id, level, request.target_sub_id)
        if result is None:
            return error(ErrorCode.NOT_FOUND, f"市场不存在该技能: {request.skill_id}")

        return success(data=result, message=f"已下载并导入: {request.skill_id}")
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 兜底捕获：下载涉及网络 + 文件 IO
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.marketplace_download", fallback="技能下载失败，请稍后重试")
        logger.error("[skills.marketplace_download] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.post("/marketplace/rate", dependencies=[Depends(require_permission("skills:write"))])
async def marketplace_rate(request: SkillMarketplaceRateRequest):
    try:
        marketplace = get_marketplace()
        result = marketplace.rate_skill(request.skill_id, request.rating, request.agent_id)
        return success(data=result, message="评分已记录")
    except KeyError as e:
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="skills.marketplace_rate.not_found", fallback="技能不存在")
        logger.error("[skills.marketplace_rate.not_found] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.NOT_FOUND, message=safe["message"], detail={"error_id": safe["error_id"]})
    except ValueError as e:
        # 使用安全错误消息，避免泄露内部异常详情
        safe = safe_error_message(e, context="skills.marketplace_rate.invalid", fallback="请求参数无效")
        logger.error("[skills.marketplace_rate.invalid] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INVALID_REQUEST, message=safe["message"], detail={"error_id": safe["error_id"]})
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.marketplace_rate", fallback="市场技能评分失败，请稍后重试")
        logger.error("[skills.marketplace_rate] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


@router.delete("/marketplace/{skill_id}", dependencies=[Depends(require_permission("skills:write"))])
async def marketplace_unpublish(skill_id: str):
    try:
        marketplace = get_marketplace()
        ok = marketplace.unpublish(skill_id)
        if ok:
            return success(data={"skill_id": skill_id}, message=f"已下架: {skill_id}")
        return error(ErrorCode.NOT_FOUND, f"市场中不存在该技能: {skill_id}")
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        # 使用安全错误消息，避免泄露内部异常详情（safe_error_message 内部已记录堆栈）
        safe = safe_error_message(e, context="skills.marketplace_unpublish", fallback="技能下架失败，请稍后重试")
        logger.error("[skills.marketplace_unpublish] error_id=%s: %s", safe["error_id"], e)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})
