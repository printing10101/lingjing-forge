"""拍照重建模块 API 路由实现。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.config import config
from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message
from app.utils.utils import get_upload_dir, sanitize_filename
from app.utils.upload_security import validate_upload

from app.image_to_3d import (
    ReconstructionPipeline,
    get_task_store,
    build_precision_disclaimer,
)
from app.image_to_3d.task_store import (
    ReconstructionTaskStatus,
)

logger = logging.getLogger(__name__)

# 后台任务引用集合（C5 修复：asyncio.create_task 不保存引用会被 GC 回收）
_background_tasks: set = set()


def _spawn(coro):
    """启动后台任务并保存引用，避免被 Python GC 回收。"""
    t = asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)
    return t


router = APIRouter(
    prefix="/api/v1/image_to_3d",
    tags=["Image-to-3D Reconstruction"],
    dependencies=[Depends(require_permission("image_to_3d:read"))],
)

UPLOAD_DIR = get_upload_dir("image_to_3d")

# 单张照片上限：30MB（手机原图 JPEG 通常 5-15MB）
MAX_PHOTO_SIZE = 30 * 1024 * 1024
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp"}

# pipeline 单例（懒加载）
_pipeline: ReconstructionPipeline | None = None


def _get_pipeline() -> ReconstructionPipeline:
    """获取 pipeline 单例。"""
    global _pipeline
    if _pipeline is None:
        _pipeline = ReconstructionPipeline(
            task_store=get_task_store(),
            cfg=config.image_to_3d,
        )
    return _pipeline


def _disclaimer_dict(calibrated: bool = False, scale_factor: float = 1.0) -> dict[str, Any]:
    """构造精度告知字段。"""
    return build_precision_disclaimer(
        config.image_to_3d,
        calibrated=calibrated,
        scale_factor=scale_factor,
    ).to_dict()


# 请求 / 响应模型


class TaskCreateRequest(BaseModel):
    """创建任务时的可选元数据。"""

    calibration_anchor_distance: float | None = Field(
        default=None,
        description=(
            "标定块在 SfM 无量纲坐标系下的距离。"
            "None 表示未提供，输出无量纲 mesh（仅可视化用）。"
            "如需得到带 mm 尺度的 mesh，请通过 GET /precision_info 了解标定块放置方法。"
        ),
    )


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    photo_count: int
    precision_disclaimer: dict[str, Any]


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    photo_count: int
    precision_tier: str
    num_images_registered: int
    calibrated: bool
    scale_factor: float
    colmap_duration_seconds: float
    openmvs_duration_seconds: float
    total_duration_seconds: float
    error_message: str
    precision_disclaimer: dict[str, Any]


# 端点实现


@router.get("/precision_info")
async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息（不创建任务）。

    前端在用户进入拍照重建页面前应先调用此端点，向用户展示：
    - 当前精度档位与预期精度
    - 适用 / 不适用场景
    - 工业生产硬门槛
    - 标定块放置说明
    """
    return success(
        data={
            "current_tier": config.image_to_3d.precision_tier,
            "available_tiers": ["coarse", "standard", "high"],
            "specs": config.image_to_3d.precision_specs,
            "calibration_block_mm": config.image_to_3d.calibration_block_mm,
            "calibration_block_guidance": {
                "recommended": "30mm 量块（量具店约 50 元，精度 ±0.0005mm）",
                "purpose": (
                    "放在拍摄场景中，重建后用作尺度归一化的参照物。没有标定块的 mesh 是无量纲的，无法用于工艺仿真。"
                ),
                "placement": (
                    "1) 标定块与零件在同一水平面上；"
                    "2) 标定块长边对齐零件主轴方向；"
                    "3) 至少在 5 张照片中清晰可见标定块两个端点；"
                    "4) 标定块不要被零件遮挡。"
                ),
            },
            "precision_disclaimer": _disclaimer_dict(),
        },
        message="精度档位信息",
    )


@router.post(
    "/tasks",
    summary="上传多张照片创建重建任务",
)
async def create_task(
    request: Request,
    files: list[UploadFile] = File(..., description="多角度照片（手机拍摄）"),
    calibration_anchor_distance: float | None = Form(
        default=None,
        description="标定块在无量纲坐标系下距离（可选）",
    ),
) -> dict[str, Any]:
    """上传多张照片，创建重建任务。

    要求：
    - 照片数量在 [min_photos, max_photos] 之间（默认 8-200）
    - 单张照片 ≤ 30MB
    - 支持 .jpg / .jpeg / .png / .webp
    - 推荐拍摄方式：环绕零件一周，每张照片与上一张重叠 70%

    本端点只创建任务（PENDING 状态），不立即执行。
    需随后调用 POST /tasks/{task_id}/run 触发执行。
    """
    if not config.image_to_3d.enabled:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="拍照重建模块未启用",
            suggestion="请在配置中设置 LNN_I2T3D_ENABLED=true",
        )

    if not files:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="未上传任何照片",
            suggestion="请上传至少 8 张多角度照片",
        )

    if len(files) < config.image_to_3d.min_photos:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"照片数量不足：上传 {len(files)} 张，最少需要 {config.image_to_3d.min_photos} 张"),
            suggestion=(
                f"建议拍摄 {config.image_to_3d.min_photos}-"
                f"{config.image_to_3d.max_photos} 张，环绕零件一周，"
                "每张照片与上一张重叠 70%。"
            ),
        )

    if len(files) > config.image_to_3d.max_photos:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"照片数量过多：上传 {len(files)} 张，最多允许 {config.image_to_3d.max_photos} 张"),
            suggestion=(f"SfM 在 {config.image_to_3d.max_photos} 张以上耗时显著增加，建议精简到关键视角。"),
        )

    # 校验 + 保存每张照片到临时上传目录
    saved_paths: list[Path] = []
    try:
        for f in files:
            content = await validate_upload(
                f,
                max_size=MAX_PHOTO_SIZE,
                allowed_extensions=ALLOWED_PHOTO_EXTENSIONS,
                allowed_mimes=ALLOWED_PHOTO_MIMES,
            )
            safe_name = sanitize_filename(f.filename or "photo.jpg")
            if not safe_name:
                safe_name = f"photo_{uuid.uuid4().hex[:8]}.jpg"
            # 加 uuid 前缀避免同名文件覆盖
            unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
            save_path = UPLOAD_DIR / unique_name
            await asyncio.to_thread(save_path.write_bytes, content)
            saved_paths.append(save_path)
    except HTTPException as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e.detail))
    except OSError as e:
        safe = safe_error_message(e, context="image_to_3d.create_task.save")
        logger.error(
            "保存上传照片失败 | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            suggestion="请检查磁盘空间或文件权限",
        )

    # 创建任务
    try:
        pipeline = _get_pipeline()
        task = await pipeline.create_task(
            photo_paths=saved_paths,
            calibration_anchor_distance=calibration_anchor_distance,
        )
    except Exception as e:
        safe = safe_error_message(e, context="image_to_3d.create_task.pipeline")
        logger.error(
            "创建重建任务失败 | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
        )

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "photo_count": task.photo_count,
            "precision_disclaimer": _disclaimer_dict(
                calibrated=task.calibrated,
                scale_factor=task.scale_factor,
            ),
        },
        message=f"任务已创建 task_id={task.task_id}，请调用 POST /tasks/{task.task_id}/run 触发执行",
    )


@router.post(
    "/tasks/{task_id}/run",
    summary="异步触发重建执行",
)
async def run_task(task_id: str) -> dict[str, Any]:
    """异步触发重建任务执行。

    本端点立即返回 202 Accepted，实际执行在后台进行。
    客户端应轮询 GET /tasks/{task_id} 获取状态。
    """
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    if task.status not in (
        ReconstructionTaskStatus.PENDING.value,
        ReconstructionTaskStatus.FAILED.value,
        ReconstructionTaskStatus.TIMEOUT.value,
    ):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(
                f"任务状态不允许执行当前操作 status={task.status}。仅 PENDING / FAILED / TIMEOUT 状态可触发执行。"
            ),
        )

    # 重置错误信息（重试场景）
    if task.status in (
        ReconstructionTaskStatus.FAILED.value,
        ReconstructionTaskStatus.TIMEOUT.value,
    ):
        store.update(task_id, error_message="")

    pipeline = _get_pipeline()
    # 用 _spawn 启动后台执行，不 await（C5 修复：保存引用避免 GC）
    _spawn(pipeline.run_task(task_id))

    return success(
        data={
            "task_id": task_id,
            "status": ReconstructionTaskStatus.RUNNING.value,
            "message": "任务已开始执行，请轮询 GET /tasks/{task_id} 获取状态",
        },
        message="任务已开始执行",
    )


@router.get(
    "/tasks/{task_id}",
    summary="查询任务状态",
)
async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、各阶段耗时、精度告知字段。"""
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "photo_count": task.photo_count,
            "precision_tier": task.precision_tier,
            "num_images_registered": task.num_images_registered,
            "calibrated": task.calibrated,
            "scale_factor": task.scale_factor,
            "colmap_duration_seconds": round(task.colmap_duration_seconds, 2),
            "openmvs_duration_seconds": round(task.openmvs_duration_seconds, 2),
            "total_duration_seconds": round(task.total_duration_seconds, 2),
            "error_message": task.error_message,
            "precision_disclaimer": _disclaimer_dict(
                calibrated=task.calibrated,
                scale_factor=task.scale_factor,
            ),
        },
    )


@router.get(
    "/tasks",
    summary="列出最近任务",
)
async def list_tasks(limit: int = 20) -> dict[str, Any]:
    """列出最近的任务（按创建时间倒序）。"""
    if limit < 1 or limit > 100:
        limit = max(1, min(100, limit))

    store = get_task_store()
    tasks = store.list_all(limit=limit)
    return success(
        data={
            "tasks": [
                {
                    "task_id": t.task_id,
                    "status": t.status,
                    "photo_count": t.photo_count,
                    "precision_tier": t.precision_tier,
                    "calibrated": t.calibrated,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                }
                for t in tasks
            ],
            "total": len(tasks),
        },
    )


@router.get(
    "/tasks/{task_id}/result",
    summary="下载最终 mesh 文件",
)
async def download_result(task_id: str) -> FileResponse:
    """下载任务最终输出的 mesh 文件（PLY 格式，已做尺度归一化）。

    仅当任务状态为 SUCCEEDED 时可下载。
    """
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在 task_id={task_id}")

    if task.status != ReconstructionTaskStatus.SUCCEEDED.value:
        raise HTTPException(
            status_code=400,
            detail=(f"任务未完成 status={task.status}，无法下载结果。请等待 status=succeeded 后重试。"),
        )

    if not task.output_mesh_path:
        raise HTTPException(
            status_code=404,
            detail="任务输出文件路径为空",
        )

    output_path = Path(task.output_mesh_path)
    if not output_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"输出文件不存在 path={output_path}",
        )

    return FileResponse(
        path=str(output_path),
        media_type="application/octet-stream",
        filename=f"reconstruction_{task_id}.ply",
    )


@router.get(
    "/tasks/{task_id}/sparse",
    summary="下载稀疏点云（COLMAP 输出）",
)
async def download_sparse(task_id: str) -> FileResponse:
    """下载稀疏点云 PLY 文件（COLMAP 输出，便于人眼检查重建质量）。"""
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在 task_id={task_id}")

    if not task.sparse_ply_path:
        raise HTTPException(
            status_code=400,
            detail="稀疏点云尚未生成（COLMAP 阶段未完成）",
        )

    sparse_path = Path(task.sparse_ply_path)
    if not sparse_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"稀疏点云文件不存在 path={sparse_path}",
        )

    return FileResponse(
        path=str(sparse_path),
        media_type="application/octet-stream",
        filename=f"sparse_{task_id}.ply",
    )


@router.delete(
    "/tasks/{task_id}",
    summary="删除任务（清理 workspace）",
)
async def delete_task(task_id: str) -> dict[str, Any]:
    """删除任务及其 workspace 目录。

    注意：workspace 包含上传的照片、COLMAP 数据库、中间 mesh 等大文件，
    删除后不可恢复。
    """
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    # 清理 workspace 目录
    workspace_dir = Path(task.workspace_dir)
    if workspace_dir.exists():
        try:
            import shutil

            await asyncio.to_thread(shutil.rmtree, workspace_dir, ignore_errors=True)
        except OSError as e:
            logger.warning("清理 workspace 失败 task_id=%s: %s", task_id, e)

    store.delete(task_id)
    return success(
        data={"task_id": task_id, "deleted": True},
        message="任务已删除",
    )
