"""Project-Level Git Sync API - 项目级 Git 同步 REST 接口.

对应 ADR-011 阶段 6 p6-2：项目级 Git 同步。

端点总览：
    POST   /api/v1/project-sync/projects                            创建项目（含 git init）
    GET    /api/v1/project-sync/projects                            项目列表（分页/过滤）
    GET    /api/v1/project-sync/projects/{project_id}               项目详情（含资源引用 + 同步记录）
    DELETE /api/v1/project-sync/projects/{project_id}               删除项目（含仓库目录）
    GET    /api/v1/project-sync/projects/{project_id}/status        查询 Git 状态（clean/dirty/ahead/...）
    POST   /api/v1/project-sync/projects/{project_id}/commit        提交变更（更新清单 + git commit）
    POST   /api/v1/project-sync/projects/{project_id}/push          推送到远端
    POST   /api/v1/project-sync/projects/{project_id}/pull          拉取远端更新
    POST   /api/v1/project-sync/projects/{project_id}/resources     添加资源引用
    DELETE /api/v1/project-sync/projects/{project_id}/resources     删除资源引用（按 resource_uri 查询参数）
    GET    /api/v1/project-sync/projects/{project_id}/resources     列出项目资源引用
    GET    /api/v1/project-sync/projects/{project_id}/records       查询同步记录
    POST   /api/v1/project-sync/clone                               克隆远端项目

权限模型：
    project_sync:read   —— 查询 / 列表 / 详情 / 状态 / 资源列表 / 同步记录
    project_sync:write  —— 创建 / 提交 / 推送 / 拉取 / 克隆 / 添加资源 / 删除资源
    project_sync:delete —— 删除项目

路由顺序注意：
    /projects 是 GET 集合端点，必须定义在 /projects/{project_id} 之前；
    /clone 是 POST 独立端点，与 /projects 区分，定义顺序无强约束，但放在末尾更清晰。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.dependencies import get_project_sync_service
from app.contracts.project_sync import (
    RESOURCE_TYPES,
    SYNC_STRATEGIES,
    parse_resource_uri,
)
from app.services.project_sync_service._exceptions import (
    GitOperationError,
    GitUnavailableError,
    InvalidProjectStateError,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    ProjectSyncError,
    ResourceRefAlreadyExistsError,
    ResourceRefNotFoundError,
)

logger = logging.getLogger(__name__)

# 注意：本 router 尚未接入（main/router_registry 未引用本文件），路由未挂载。
router = APIRouter(prefix="/api/v1/project-sync", tags=["Project Sync"])


# Pydantic 请求模型


class CreateProjectRequest(BaseModel):
    """创建项目请求体."""

    name: str = Field(..., min_length=1, max_length=256, description="项目显示名")
    description: str = Field(default="", max_length=2048, description="项目描述")
    author: str = Field(default="", max_length=128, description="项目作者")
    remote_url: str = Field(
        default="",
        max_length=1024,
        description="远端仓库 URL（空表示纯本地仓库）",
    )
    branch: str = Field(default="main", max_length=128, description="初始分支名")
    initial_commit: bool = Field(default=True, description="是否在创建时生成首个 commit")


class CloneProjectRequest(BaseModel):
    """克隆远端项目请求体."""

    remote_url: str = Field(..., min_length=1, max_length=1024, description="远端仓库 URL")
    name: str = Field(..., min_length=1, max_length=256, description="项目显示名")
    branch: str = Field(default="main", max_length=128, description="检出分支名")
    description: str = Field(default="", max_length=2048, description="项目描述")
    author: str = Field(default="", max_length=128, description="项目作者")


class CommitRequest(BaseModel):
    """提交变更请求体."""

    message: str = Field(..., min_length=1, max_length=2048, description="commit message")


class AddResourceRequest(BaseModel):
    """添加资源引用请求体."""

    resource_type: str = Field(..., description=f"资源类型（{RESOURCE_TYPES.all()}）")
    resource_uri: str = Field(
        ...,
        max_length=512,
        description="资源 URI（如 dataset://phm2010/v3）",
    )
    sync_strategy: str = Field(
        default=SYNC_STRATEGIES.HASH_REFERENCED,
        description=f"同步策略（{SYNC_STRATEGIES.all()}）",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")


# 辅助函数


def _handle_service_exception(e: Exception, *, action: str):
    """统一处理服务层异常 → API 错误响应.

    Args:
        e: 服务层抛出的异常
        action: 当前操作描述（用于日志）

    Returns:
        error() 响应对象
    """
    if isinstance(e, ProjectNotFoundError):
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    if isinstance(e, ProjectAlreadyExistsError):
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    if isinstance(e, ResourceRefNotFoundError):
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    if isinstance(e, ResourceRefAlreadyExistsError):
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    if isinstance(e, InvalidProjectStateError):
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    if isinstance(e, GitUnavailableError):
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=str(e),
            suggestion="请安装 Git 并将其加入 PATH 环境变量",
        )
    if isinstance(e, GitOperationError):
        logger.error(
            "Git operation failed during %s (args=%s, rc=%s): %s",
            action,
            e.args if hasattr(e, "args") else "?",
            getattr(e, "returncode", -1),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"Git 操作失败：{e}",
            suggestion="查看后端日志获取 git stderr 详情",
        )
    if isinstance(e, ProjectSyncError):
        logger.error("Project sync error during %s: %s", action, e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    # 兜底：未识别的异常
    logger.error("Unexpected error during %s: %s", action, e, exc_info=True)
    return error(
        code=ErrorCode.INTERNAL_ERROR,
        message=f"{action} 失败",
        detail=str(e),
    )


# 端点实现


@router.post(
    "/projects",
    dependencies=[Depends(require_permission("project_sync:write"))],
)
async def create_project(request: CreateProjectRequest):
    """创建项目（执行 git init + 写入 .lomo-project.yaml + 可选首 commit）.

    - 检查同名项目不存在
    - 创建本地 Git 仓库（默认分支 main）
    - 若提供 remote_url，自动 git remote add origin
    - 写入初始清单文件 .lomo-project.yaml
    - 若 initial_commit=True，生成首个 commit
    """
    service = get_project_sync_service()
    try:
        result = await service.create_project(
            name=request.name,
            description=request.description,
            author=request.author,
            remote_url=request.remote_url,
            branch=request.branch,
            initial_commit=request.initial_commit,
        )
    except Exception as e:
        return _handle_service_exception(e, action="创建项目")

    return success(
        data=result,
        message=f"项目 '{request.name}' 已创建",
    )


@router.get("/projects")
async def list_projects(
    status_filter: str | None = Query(
        None, alias="status", description="按状态过滤（clean/dirty/ahead/behind/conflict/error）"
    ),
    author: str | None = Query(None, description="按作者过滤"),
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """分页列出项目（支持状态/作者过滤）."""
    service = get_project_sync_service()
    try:
        result = await service.list_projects(
            limit=limit,
            offset=offset,
            status_filter=status_filter,
            author=author,
        )
    except Exception as e:
        return _handle_service_exception(e, action="列出项目")

    return success(data=result, message="项目列表已获取")


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    include_refs: bool = Query(True, description="是否包含资源引用列表"),
    include_records: bool = Query(False, description="是否包含同步记录列表（默认不包含，避免大查询）"),
):
    """获取项目详情（含当前状态 + 可选资源引用 / 同步记录）."""
    service = get_project_sync_service()
    try:
        result = await service.get_project(
            project_id,
            include_refs=include_refs,
            include_records=include_records,
        )
    except Exception as e:
        return _handle_service_exception(e, action="获取项目详情")

    return success(data=result, message="项目详情已获取")


@router.delete(
    "/projects/{project_id}",
    dependencies=[Depends(require_permission("project_sync:delete"))],
)
async def delete_project(
    project_id: str,
    purge_repo: bool = Query(False, description="是否物理删除仓库目录（默认仅删除 DB 记录，保留仓库文件）"),
):
    """删除项目.

    - purge_repo=False：仅删除 DB 记录，仓库目录保留在 output_dir/project_sync/
    - purge_repo=True：同时物理删除仓库目录（不可恢复，慎用）

    设计原则：默认保留仓库文件以防误删，工程师确认无误后再手动清理。
    """
    service = get_project_sync_service()
    try:
        result = await service.delete_project(project_id, purge_repo=purge_repo)
    except Exception as e:
        return _handle_service_exception(e, action="删除项目")

    return success(data=result, message=f"项目 {project_id} 已删除")


@router.get("/projects/{project_id}/status")
async def get_project_status(project_id: str):
    """查询项目的 Git 状态（执行 git status 推导状态机）.

    返回字段：
        - status: clean/dirty/ahead/behind/conflict/error
        - current_branch: 当前分支
        - current_commit: 当前 HEAD sha
        - ahead_count: 领先远端的 commit 数
        - behind_count: 落后远端的 commit 数
        - changed_files: 变更文件列表（dirty 状态时）
    """
    service = get_project_sync_service()
    try:
        result = await service.get_project_status(project_id)
    except Exception as e:
        return _handle_service_exception(e, action="查询项目状态")

    return success(data=result, message="项目状态已获取")


@router.post(
    "/projects/{project_id}/commit",
    dependencies=[Depends(require_permission("project_sync:write"))],
)
async def commit_project(project_id: str, request: CommitRequest):
    """提交变更（重新计算资源 hash → 更新清单 → git add → git commit）.

    - 重新计算所有资源引用的 content_hash
    - 检测 hash 变更，更新 .lomo-project.yaml 清单
    - git add .lomo-project.yaml
    - 若有变更，git commit -m <message>
    - 更新 DB current_commit + status=CLEAN
    - 写入 COMMIT SyncRecord
    """
    service = get_project_sync_service()
    try:
        result = await service.commit_project(project_id, message=request.message)
    except Exception as e:
        return _handle_service_exception(e, action="提交变更")

    return success(
        data=result,
        message=f"变更已提交：{result.get('commit_sha', '')[:8]}",
    )


@router.post(
    "/projects/{project_id}/push",
    dependencies=[Depends(require_permission("project_sync:write"))],
)
async def push_project(project_id: str):
    """推送到远端仓库（git push origin <branch>）.

    要求项目配置了 remote_url，否则返回 InvalidProjectStateError。
    推送成功后写入 PUSH SyncRecord。
    """
    service = get_project_sync_service()
    try:
        result = await service.push_project(project_id)
    except Exception as e:
        return _handle_service_exception(e, action="推送远端")

    return success(data=result, message="已推送到远端")


@router.post(
    "/projects/{project_id}/pull",
    dependencies=[Depends(require_permission("project_sync:write"))],
)
async def pull_project(project_id: str):
    """拉取远端更新（git pull origin <branch>）.

    要求项目配置了 remote_url，否则返回 InvalidProjectStateError。
    若发生 merge 冲突，状态置为 conflict，由工程师手动解决。
    拉取成功后写入 PULL SyncRecord。
    """
    service = get_project_sync_service()
    try:
        result = await service.pull_project(project_id)
    except Exception as e:
        return _handle_service_exception(e, action="拉取远端")

    return success(data=result, message="已拉取远端更新")


@router.post(
    "/projects/{project_id}/resources",
    dependencies=[Depends(require_permission("project_sync:write"))],
)
async def add_resource_ref(project_id: str, request: AddResourceRequest):
    """添加资源引用到项目.

    - 校验 resource_type 合法（dataset/model/workflow/config/snapshot/template）
    - 校验 resource_uri scheme 与 resource_type 一致
    - 同一项目内 resource_uri 唯一（重复抛 ResourceRefAlreadyExistsError）
    - 立即计算 content_hash 并写入 DB
    - 不直接 git add，等待下次 commit_project 时统一处理
    """
    # 前置校验：resource_type 合法性
    if not RESOURCE_TYPES.is_valid(request.resource_type):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"资源类型不支持: {request.resource_type}（支持: {RESOURCE_TYPES.all()}）",
        )

    # 前置校验：URI scheme 与 resource_type 一致
    try:
        scheme, _ = parse_resource_uri(request.resource_uri)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    if scheme != request.resource_type:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"URI scheme ({scheme}) 与 resource_type ({request.resource_type}) 不匹配",
        )

    # 前置校验：sync_strategy 合法性
    if not SYNC_STRATEGIES.is_valid(request.sync_strategy):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"同步策略不支持: {request.sync_strategy}（支持: {SYNC_STRATEGIES.all()}）",
        )

    service = get_project_sync_service()
    try:
        result = await service.add_resource_ref(
            project_id,
            resource_type=request.resource_type,
            resource_uri=request.resource_uri,
            sync_strategy=request.sync_strategy,
            metadata=request.metadata,
        )
    except Exception as e:
        return _handle_service_exception(e, action="添加资源引用")

    return success(data=result, message="资源引用已添加")


@router.get("/projects/{project_id}/resources")
async def list_resource_refs(
    project_id: str,
    resource_type: str | None = Query(
        None, description="按资源类型过滤（dataset/model/workflow/config/snapshot/template）"
    ),
):
    """列出项目的资源引用（可选按类型过滤）."""
    if resource_type and not RESOURCE_TYPES.is_valid(resource_type):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"资源类型不支持: {resource_type}",
        )

    service = get_project_sync_service()
    try:
        result = await service.list_resource_refs(project_id, resource_type=resource_type)
    except Exception as e:
        return _handle_service_exception(e, action="列出资源引用")

    return success(data=result, message="资源引用列表已获取")


@router.delete(
    "/projects/{project_id}/resources",
    dependencies=[Depends(require_permission("project_sync:write"))],
)
async def remove_resource_ref(
    project_id: str,
    resource_uri: str = Query(..., description="要删除的资源 URI"),
):
    """删除项目的资源引用（按 resource_uri 精确匹配）.

    删除后项目的 .lomo-project.yaml 不会立即更新，等待下次 commit_project 时同步。
    """
    if not resource_uri:
        return error(code=ErrorCode.INVALID_REQUEST, message="resource_uri 不能为空")

    service = get_project_sync_service()
    try:
        result = await service.remove_resource_ref(project_id, resource_uri=resource_uri)
    except Exception as e:
        return _handle_service_exception(e, action="删除资源引用")

    return success(data=result, message="资源引用已删除")


@router.get("/projects/{project_id}/records")
async def list_sync_records(
    project_id: str,
    direction: str | None = Query(None, description="按同步方向过滤（init/commit/push/pull/clone）"),
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """查询项目的同步记录（按时间倒序）.

    每条记录对应一次 Git 写操作（init/commit/push/pull/clone），
    用于审计与回溯。
    """
    service = get_project_sync_service()
    try:
        result = await service.list_sync_records(
            project_id,
            limit=limit,
            offset=offset,
            direction=direction,
        )
    except Exception as e:
        return _handle_service_exception(e, action="查询同步记录")

    return success(data=result, message="同步记录已获取")


@router.post(
    "/clone",
    dependencies=[Depends(require_permission("project_sync:write"))],
)
async def clone_project(request: CloneProjectRequest):
    """克隆远端项目（git clone + 注册到 DB）.

    - 执行 git clone --branch <branch> <remote_url> <repo_path>
    - 若克隆后仓库含 .lomo-project.yaml，读取清单更新 DB
    - 否则创建空白清单
    - 写入 CLONE SyncRecord
    """
    service = get_project_sync_service()
    try:
        result = await service.clone_project(
            remote_url=request.remote_url,
            name=request.name,
            branch=request.branch,
            description=request.description,
            author=request.author,
        )
    except Exception as e:
        return _handle_service_exception(e, action="克隆远端项目")

    return success(
        data=result,
        message=f"远端项目 '{request.name}' 已克隆",
    )
