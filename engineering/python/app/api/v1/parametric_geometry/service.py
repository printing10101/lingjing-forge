"""parametric_geometry 路由业务逻辑（从 routes.py 抽取，波次2 S4）。

端点参数校验/鉴权由 routes.py 负责，本模块仅承载业务实现。
"""

from __future__ import annotations

from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from app.api.v1.parametric_geometry.schemas import (
    ReviewRequest,
    TaskCreateRequest,
)
from app.auth.permissions import require_permission
from app.config import config
from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message
from app.parametric_geometry import (
    ParametricGeometryError,
    ParametricGeometryPipeline,
    ParametricGeometryTaskStatus,
    StepReviewError,
    StepReviewStatus,
    build_step_disclaimer,
    get_task_store,
)


logger = logging.getLogger(__name__)

# 后台任务引用集合（防 GC 回收，与原 routes 行为一致）
_background_tasks: set = set()


def _spawn(coro):
    """启动后台任务并保存引用，避免被 Python GC 回收。"""
    t = asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)
    return t


router = APIRouter(
    prefix="/api/v1/parametric_geometry",
    tags=["Parametric Geometry (Engineer-Assisted STEP)"],
    dependencies=[Depends(require_permission("parametric_geometry:read"))],
)

# pipeline 单例（懒加载，避免模块导入期就触发 pythonOCC/FreeCAD 可选依赖加载）
_pipeline: ParametricGeometryPipeline | None = None


def _get_pipeline() -> ParametricGeometryPipeline:
    """获取 pipeline 单例。"""
    global _pipeline
    if _pipeline is None:
        _pipeline = ParametricGeometryPipeline(
            cfg=config.parametric_geometry,
        )
    return _pipeline


def _disclaimer_dict(
    task: Any = None,
    mesh_calibrated: bool = False,
    feature_source: str = "external_upload",
    precision_tier: str = "standard",
    engine_used: str | None = None,
) -> dict[str, Any]:
    """构造精度告知字段。

    优先使用 pipeline.get_disclaimer(task)（带 task 上下文），
    无 task 时使用默认值（用于 precision_info 端点）。
    """
    if task is not None:
        return _get_pipeline().get_disclaimer(task).to_dict()
    return build_step_disclaimer(
        cfg=config.parametric_geometry,
        mesh_calibrated=mesh_calibrated,
        feature_source=feature_source,
        precision_tier=precision_tier,
        engine_used=engine_used or "unavailable",
    ).to_dict()


def _resolve_upstream_calibrated(
    source_feature_extraction_task_id: str,
) -> tuple[bool, str]:
    """从上游 feature_extraction 任务追溯 mesh 标定状态。

    精度继承链：阶段 1 image_to_3d → 阶段 2 feature_extraction → 阶段 3
    本方法通过阶段 2 任务的 source_reconstruction_task_id 查询阶段 1 任务的
    calibrated 字段，确保精度信息不出现断层。

    Returns:
        (calibrated, feature_source)
        - 若上游链路完整且阶段 1 任务已 SUCCEEDED：返回 (task.calibrated, fe_task_id)
        - 若上游任务不存在或未完成：返回 (False, "external_upload")，并记日志

    设计意图：避免硬依赖 feature_extraction / image_to_3d 模块（桌面轻量档位
    下可能未启用），通过 try/except ImportError 实现软依赖。
    """
    if not source_feature_extraction_task_id:
        return False, "external_upload"

    # 1. 查询阶段 2 feature_extraction 任务
    try:
        from app.feature_extraction import get_feature_store
    except ImportError:
        logger.warning(
            "feature_extraction 模块未启用，无法追溯上游任务 calibrated 状态 source_fe_task_id=%s，按未标定处理",
            source_feature_extraction_task_id,
        )
        return False, "external_upload"

    try:
        fe_store = get_feature_store()
        fe_task = fe_store.get(source_feature_extraction_task_id)
        if fe_task is None:
            logger.warning(
                "上游 feature_extraction 任务不存在 task_id=%s，按未标定处理",
                source_feature_extraction_task_id,
            )
            return False, "external_upload"

        # 2. 通过阶段 2 任务的 source_reconstruction_task_id 查询阶段 1 任务
        source_reconstruction_task_id = getattr(fe_task, "source_reconstruction_task_id", "")
        if not source_reconstruction_task_id:
            logger.warning(
                "上游 feature_extraction 任务 %s 未关联 image_to_3d 任务，按未标定处理",
                source_feature_extraction_task_id,
            )
            return False, source_feature_extraction_task_id

        try:
            from app.image_to_3d import get_task_store as get_i2t3d_store
            from app.image_to_3d.task_store import ReconstructionTaskStatus
        except ImportError:
            logger.warning(
                "image_to_3d 模块未启用，无法追溯阶段 1 任务 calibrated 状态 "
                "source_reconstruction_task_id=%s，按未标定处理",
                source_reconstruction_task_id,
            )
            return False, source_feature_extraction_task_id

        upstream = get_i2t3d_store().get(source_reconstruction_task_id)
        if upstream is None:
            logger.warning(
                "上游 image_to_3d 任务不存在 task_id=%s，按未标定处理",
                source_reconstruction_task_id,
            )
            return False, source_feature_extraction_task_id

        if upstream.status != ReconstructionTaskStatus.SUCCEEDED.value:
            logger.warning(
                "上游 image_to_3d 任务未完成 task_id=%s status=%s，按未标定处理",
                source_reconstruction_task_id,
                upstream.status,
            )
            return False, source_feature_extraction_task_id

        return bool(upstream.calibrated), source_feature_extraction_task_id

    except Exception as e:
        safe = safe_error_message(e, context="parametric_geometry.resolve_upstream_calibrated")
        logger.warning(
            "查询上游任务异常 source_fe_task_id=%s error_id=%s，按未标定处理",
            source_feature_extraction_task_id,
            safe.get("error_id"),
        )
        return False, "external_upload"


async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息与工业硬门槛（不创建任务）。

    前端在用户进入参数化几何页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d mesh + feature_extraction 特征）
    - 适用 / 不适用场景
    - 工业生产硬门槛
    - 两轮工程师审核流程说明
    """
    return success(
        data={
            "current_tier": config.parametric_geometry.precision_tier,
            "available_tiers": ["coarse", "standard", "high"],
            "tier_specs": {
                "coarse": "0.5-2.0mm，特征参数误差较大，STEP 仅可用于工艺理解",
                "standard": "0.1-1.0mm，非配合面 STEP 可用，配合面仍不可达",
                "high": "0.1-0.5mm，小零件细节 STEP 可用，仍达不到工业级配合面公差",
            },
            "module_parameters": {
                "blank_margin_mm": config.parametric_geometry.blank_margin_mm,
                "max_concurrent": config.parametric_geometry.max_concurrent,
                "task_timeout_seconds": config.parametric_geometry.task_timeout_seconds,
                "task_retention_hours": config.parametric_geometry.task_retention_hours,
                "default_mesh_calibrated": config.parametric_geometry.default_mesh_calibrated,
            },
            "review_workflow": {
                "step_1": "阶段 2 导出 confirmed_features.json → 创建任务（PENDING）",
                "step_2": "异步触发 run_pipeline → 状态 STEP_GENERATED（第一轮 STEP 已生成）",
                "step_3": "工程师逐条审核 STEP 中特征表达：confirmed / rejected / edited",
                "step_4": "全部审核完毕 → 状态 REVIEWED",
                "step_5": "调用 finalize → 基于 effective_params 重新生成最终 STEP → SUCCEEDED",
                "step_6": "下载最终 STEP，交 CAM 软件（NX/PowerMill/PyCAM）二次校验",
            },
            "engine_fallback_chain": [
                "pythonOCC（OpenCASCADE Python 绑定，工业级 STEP 表达）",
                "FreeCAD Python API（基于 OpenCASCADE，需安装 FreeCAD）",
                "简易 STEP 模板（手工拼接 ISO 10303-21 字符串，零依赖兜底）",
            ],
            "industrial_hard_gates": [
                "mesh → 参数化 CAD 自动转换工业上未解决：本模块输出「算法建议 STEP」",
                "工程师必须两轮审核（STEP_GENERATED + finalize）后才允许下载最终 STEP",
                "即便审核通过，最终 STEP 必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验",
                "G 代码必须经 CAM 软件二次校验后才允许上机床",
                "良品率要求：0 缺陷容忍",
                "配合面公差：0.01mm（手机摄影测量物理上不可达）",
                "CNC 操作资质：需持证操作员",
                "导师签字 + 保险：大一独立项目无法独立完成此环节",
                "本系统定位为「工程师助手」，非「全自动生产线」",
            ],
            "step_disclaimer": _disclaimer_dict(),
        },
        message="参数化几何精度档位与工业硬门槛信息",
    )


async def create_task(body: TaskCreateRequest) -> dict[str, Any]:
    """创建参数化几何任务。

    适用场景：
    - 阶段 2 feature_extraction 已导出 confirmed_features.json，本阶段直接读取该路径
    - 用户从外部 CAD 系统导出的特征 JSON（需符合阶段 2 导出格式）

    若提供 ``source_feature_extraction_task_id`` 且上游链路完整
    （fe_task → i2t3d_task SUCCEEDED），系统会自动查询阶段 1 mesh 是否已做
    尺度归一化（calibrated），用于决定本模块输出 STEP 的单位是 mm 还是无量纲。

    也可显式传入 ``mesh_calibrated`` 覆盖自动查询结果（用于外部导入场景）。

    本端点只创建任务（PENDING 状态），不立即执行。
    需随后调用 POST /tasks/{task_id}/run 触发执行。
    """
    if not config.parametric_geometry.enabled:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="参数化几何模块未启用",
            suggestion="请在配置中设置 LNN_PG_ENABLED=true",
        )

    # 校验精度档位
    valid_tiers = {"coarse", "standard", "high"}
    if body.precision_tier not in valid_tiers:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"非法 precision_tier: {body.precision_tier}，应为 {sorted(valid_tiers)}"),
        )

    # 校验 input_features_path 存在性
    features_path = Path(body.input_features_path)
    if not features_path.is_absolute():
        # 相对路径按 output/parametric_geometry/ 的父目录解析，
        # 避免任意路径读取（与 feature_extraction 风格一致）
        features_path = Path(config.parametric_geometry.output_dir).parent / features_path

    if not features_path.exists():
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"confirmed_features.json 不存在: {features_path}",
            suggestion=("请确认路径正确，或先调用 GET /api/v1/feature_extraction/tasks/{fe_task_id}/export 导出。"),
        )

    if features_path.suffix.lower() != ".json":
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"input_features_path 必须是 .json 文件: {features_path}",
        )

    # 解析 mesh_calibrated：显式传入优先，否则查询上游链路
    if body.mesh_calibrated is not None:
        mesh_calibrated = bool(body.mesh_calibrated)
        feature_source = body.source_feature_extraction_task_id or "external_upload"
    else:
        mesh_calibrated, feature_source = _resolve_upstream_calibrated(body.source_feature_extraction_task_id)

    # 应用 default_mesh_calibrated 兜底（保守默认 False）
    if not mesh_calibrated and config.parametric_geometry.default_mesh_calibrated:
        mesh_calibrated = True
        logger.info(
            "mesh_calibrated 未明确确认，按 default_mesh_calibrated=True 兜底 task_source=%s",
            feature_source,
        )

    try:
        pipeline = _get_pipeline()
        task = pipeline.create_task(
            source_feature_extraction_task_id=body.source_feature_extraction_task_id,
            input_features_path=str(features_path),
            precision_tier=body.precision_tier,
            mesh_calibrated=mesh_calibrated,
        )
    except Exception as e:
        safe = safe_error_message(e, context="parametric_geometry.create_task")
        logger.error(
            "创建参数化几何任务失败 | error_id=%s | exc=%s",
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
            "source_feature_extraction_task_id": task.source_feature_extraction_task_id,
            "input_features_path": task.input_features_path,
            "precision_tier": task.precision_tier,
            "mesh_calibrated": mesh_calibrated,
            "step_disclaimer": _disclaimer_dict(
                task=task,
                mesh_calibrated=mesh_calibrated,
                feature_source=feature_source,
                precision_tier=task.precision_tier,
            ),
        },
        message=(f"任务已创建 task_id={task.task_id}，请调用 POST /tasks/{task.task_id}/run 触发执行"),
    )


async def run_task(task_id: str) -> dict[str, Any]:
    """异步触发参数化几何流水线执行。

    执行流程：
    1. 加载阶段 2 confirmed_features.json → ReviewedFeatureRef 列表
    2. feature_to_brep.convert_features_to_brep() → BrepShape 列表
    3. assembly_builder.build_assembly_plan() → AssemblyPlan
    4. step_writer.write_step_with_fallback() → STEP 文件（三级降级）
    5. 持久化 assembly_plan.json + brep_shapes.json
    6. 状态置为 STEP_GENERATED（等待工程师第一轮审核）

    仅 PENDING / FAILED 状态可触发执行（FAILED 允许重试）。
    """
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    if task.status not in (
        ParametricGeometryTaskStatus.PENDING.value,
        ParametricGeometryTaskStatus.FAILED.value,
    ):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态不允许执行当前操作 status={task.status}。仅 PENDING / FAILED 状态可触发执行。"),
        )

    # 重试场景：清空错误信息
    if task.status == ParametricGeometryTaskStatus.FAILED.value:
        store.update(task_id, error_message=None)

    pipeline = _get_pipeline()
    _spawn(pipeline.run_pipeline(task_id))

    return success(
        data={
            "task_id": task_id,
            "status": ParametricGeometryTaskStatus.RUNNING.value,
            "message": (
                "任务已开始执行，请轮询 GET /tasks/{task_id} 获取状态。"
                "执行完成后状态将变为 STEP_GENERATED，等待工程师审核 STEP 中特征表达。"
            ),
        },
        message="任务已开始执行",
    )


async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、审核进度、STEP 路径、精度告知字段。"""
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    # 统计审核进度
    pending_count = sum(1 for f in task.input_features if f.review_status == StepReviewStatus.PENDING.value)
    confirmed_count = sum(1 for f in task.input_features if f.review_status == StepReviewStatus.CONFIRMED.value)
    rejected_count = sum(1 for f in task.input_features if f.review_status == StepReviewStatus.REJECTED.value)
    edited_count = sum(1 for f in task.input_features if f.review_status == StepReviewStatus.EDITED.value)

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "source_feature_extraction_task_id": task.source_feature_extraction_task_id,
            "input_features_path": task.input_features_path,
            "precision_tier": task.precision_tier,
            "mesh_calibrated": task.mesh_calibrated,
            "feature_count": len(task.input_features),
            "pending_count": pending_count,
            "confirmed_count": confirmed_count,
            "rejected_count": rejected_count,
            "edited_count": edited_count,
            "step_output_path": task.step_output_path,
            "final_step_path": task.final_step_path,
            "engine_used": task.engine_used,
            "cam_validation_required": task.cam_validation_required,
            "error_message": task.error_message,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "step_disclaimer": _disclaimer_dict(task=task),
        },
    )


async def list_tasks(limit: int = 20) -> dict[str, Any]:
    """列出最近的参数化几何任务（按创建时间倒序）。"""
    if limit < 1 or limit > 100:
        limit = max(1, min(100, limit))

    store = get_task_store()
    tasks = store.list_tasks(limit=limit)
    return success(
        data={
            "tasks": [
                {
                    "task_id": t.task_id,
                    "status": t.status,
                    "source_feature_extraction_task_id": t.source_feature_extraction_task_id,
                    "feature_count": len(t.input_features),
                    "precision_tier": t.precision_tier,
                    "mesh_calibrated": t.mesh_calibrated,
                    "engine_used": t.engine_used,
                    "step_output_path": t.step_output_path,
                    "final_step_path": t.final_step_path,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                }
                for t in tasks
            ],
            "total": len(tasks),
        },
    )


async def get_task_result(task_id: str) -> dict[str, Any]:
    """获取任务结果摘要、装配信息与完整特征列表（含审核状态）。

    仅当任务状态为 STEP_GENERATED / REVIEWED / SUCCEEDED 时可调用。
    返回的特征列表中每条包含：
    - feature_id / feature_type
    - source_params（阶段 2 导出的原始参数）
    - review_status（pending / confirmed / rejected / edited）
    - effective_params（合并 edited_params 后的生效参数）
    - edited_params / engineer_notes（工程师审核后填充）
    """
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    allowed_states = {
        ParametricGeometryTaskStatus.STEP_GENERATED.value,
        ParametricGeometryTaskStatus.REVIEWED.value,
        ParametricGeometryTaskStatus.SUCCEEDED.value,
    }
    if task.status not in allowed_states:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许获取结果，仅 {sorted(allowed_states)} 状态可获取。"),
            suggestion="请等待状态变为 step_generated 后再调用此端点",
        )

    pipeline = _get_pipeline()
    result_summary = pipeline.get_result_summary(task_id)

    features_data = [
        {
            "feature_id": f.feature_id,
            "feature_type": f.feature_type,
            "source_params": f.source_params,
            "review_status": f.review_status,
            "edited_params": f.edited_params,
            "effective_params": f.effective_params(),
            "engineer_notes": f.engineer_notes,
            "reviewed_by": f.reviewed_by,
            "reviewed_at": f.reviewed_at,
        }
        for f in task.input_features
    ]

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "source_feature_extraction_task_id": task.source_feature_extraction_task_id,
            "feature_count": len(task.input_features),
            "brep_shape_count": result_summary.brep_shape_count,
            "engine_used": task.engine_used,
            "step_output_path": task.step_output_path,
            "final_step_path": task.final_step_path,
            "precision_tier": task.precision_tier,
            "mesh_calibrated": task.mesh_calibrated,
            "cam_validation_required": task.cam_validation_required,
            "error_message": task.error_message,
            "assembly_summary": result_summary.assembly_summary,
            "features": features_data,
            "step_disclaimer": _disclaimer_dict(task=task),
        },
    )


async def review_step_feature(
    task_id: str,
    feature_id: str,
    body: ReviewRequest,
) -> dict[str, Any]:
    """工程师审核单个特征在 STEP 中的表达。

    本端点是 human-in-the-loop 的核心入口（项目记忆硬约束：
    mesh → 参数化 CAD 自动转换工业上未解决，必须工程师审核）。

    审核动作：
    - ``confirmed``: STEP 中该特征表达正确，参数无需修改
    - ``rejected``:  该特征在 STEP 中表达错误，从最终 STEP 中移除
    - ``edited``:    STEP 中特征表达可识别但参数需修正，需同时提供 ``edited_params``

    当所有特征都被审核（confirmed / rejected / edited）后，
    任务状态自动从 STEP_GENERATED 转为 REVIEWED，
    随后可调用 POST /tasks/{task_id}/finalize 生成最终 STEP。

    请求体中 ``feature_id`` 作为查询参数传入，便于 RESTful 路径表达。
    """
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    if task.status != ParametricGeometryTaskStatus.STEP_GENERATED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(
                f"任务状态 {task.status} 不允许审核，仅 {ParametricGeometryTaskStatus.STEP_GENERATED.value} 状态可审核"
            ),
            suggestion="请等待流水线执行完成（状态变为 step_generated）后再审核",
        )

    # 校验 action
    valid_actions = {
        StepReviewStatus.CONFIRMED.value,
        StepReviewStatus.REJECTED.value,
        StepReviewStatus.EDITED.value,
    }
    if body.action not in valid_actions:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"非法 action: {body.action}，应为 {sorted(valid_actions)}",
        )

    # edited 动作必须提供 edited_params
    if body.action == StepReviewStatus.EDITED.value and not body.edited_params:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="action=edited 时必须提供 edited_params",
            suggestion="请提供编辑后的完整参数（字段结构需与 source_params 一致）",
        )

    try:
        pipeline = _get_pipeline()
        reviewed_feature = pipeline.review_step_feature(
            task_id=task_id,
            feature_id=feature_id,
            review_status=body.action,
            edited_params=body.edited_params,
            engineer_notes=body.engineer_notes,
            reviewed_by=body.reviewed_by,
        )
    except StepReviewError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="parametric_geometry.review_step_feature")
        logger.error(
            "审核特征失败 task_id=%s feature_id=%s | error_id=%s | exc=%s",
            task_id,
            feature_id,
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
        )

    # 重新查询任务状态（review_step_feature 内部可能已将状态置为 REVIEWED）
    task_after = store.get(task_id)
    if task_after is None:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="审核后任务丢失，请检查任务存储",
        )

    all_reviewed = all(f.review_status != StepReviewStatus.PENDING.value for f in task_after.input_features)

    return success(
        data={
            "task_id": task_id,
            "feature_id": reviewed_feature.feature_id,
            "feature_type": reviewed_feature.feature_type,
            "review_status": reviewed_feature.review_status,
            "effective_params": reviewed_feature.effective_params(),
            "all_reviewed": all_reviewed,
            "task_status": task_after.status,
            "step_disclaimer": _disclaimer_dict(task=task_after),
        },
        message=(
            f"特征 {feature_id} 已审核（action={body.action}）。"
            + (
                " 全部特征已审核完毕，可调用 POST /tasks/{task_id}/finalize 生成最终 STEP。"
                if all_reviewed
                else " 仍有特征待审核。"
            )
        ),
    )


async def finalize_step(task_id: str) -> dict[str, Any]:
    """基于工程师审核结果重新生成最终 STEP 文件。

    本端点在所有特征审核完毕（状态 REVIEWED）后调用：
    - ReviewedFeatureRef.effective_params() 自动合并 edited_params
    - rejected 的特征会被 convert_features_to_brep 自动跳过
    - 重新装配 + 写入最终 STEP（{task_id}_final.step）
    - 状态置为 SUCCEEDED

    最终 STEP 生成后，可通过 GET /tasks/{task_id}/step/download 下载。

    工业硬约束（项目记忆）：
    - 即便最终 STEP 已生成，也必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床
    - 本系统定位为「工程师助手」，非「全自动生产线」
    """
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    if task.status != ParametricGeometryTaskStatus.REVIEWED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(
                f"任务状态 {task.status} 不允许最终化，仅 {ParametricGeometryTaskStatus.REVIEWED.value} 状态可最终化"
            ),
            suggestion="请先完成所有特征的审核（状态变为 reviewed）后再最终化",
        )

    try:
        pipeline = _get_pipeline()
        result = await pipeline.finalize_step(task_id)
    except ParametricGeometryError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="parametric_geometry.finalize_step")
        logger.error(
            "最终化 STEP 失败 task_id=%s | error_id=%s | exc=%s",
            task_id,
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
        )

    # 重新查询任务获取最新状态（finalize_step 内部已 update）
    task_after = store.get(task_id)
    if task_after is None:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="最终化后任务丢失，请检查任务存储",
        )

    download_url = f"/api/v1/parametric_geometry/tasks/{task_id}/step/download"

    return success(
        data={
            "task_id": result.task_id,
            "status": result.status,
            "source_feature_extraction_task_id": result.source_feature_extraction_task_id,
            "feature_count": result.feature_count,
            "brep_shape_count": result.brep_shape_count,
            "engine_used": result.engine_used,
            "step_output_path": result.step_output_path,
            "final_step_path": result.final_step_path,
            "precision_tier": result.precision_tier,
            "mesh_calibrated": result.mesh_calibrated,
            "assembly_summary": result.assembly_summary,
            "download_url": download_url,
            "step_disclaimer": _disclaimer_dict(task=task_after),
        },
        message=(
            f"最终 STEP 已生成 path={result.final_step_path}。"
            "可通过 download_url 下载。"
            "注意：最终 STEP 必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床。"
        ),
    )


async def download_step_file(task_id: str, final: bool = True) -> FileResponse:
    """下载 STEP 文件。

    Args:
        task_id: 任务 ID
        final: 是否下载最终 STEP（默认 True）。
            - True:  下载 {task_id}_final.step（SUCCEEDED 状态可下载）
            - False: 下载 {task_id}.step（STEP_GENERATED 及之后状态可下载，用于第一轮审核参考）

    返回 STEP 文件（ISO 10303-21 AP214 格式）。
    """
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在 task_id={task_id}")

    if final:
        # 下载最终 STEP
        if task.status != ParametricGeometryTaskStatus.SUCCEEDED.value:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"任务未 SUCCEEDED status={task.status}，无法下载最终 STEP。"
                    "请先完成审核并调用 POST /tasks/{task_id}/finalize。"
                    "如需下载初版 STEP 供审核参考，请使用 ?final=false。"
                ),
            )

        if not task.final_step_path:
            raise HTTPException(
                status_code=404,
                detail="任务最终 STEP 路径为空",
            )

        output_path = Path(task.final_step_path)
        if not output_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"最终 STEP 文件不存在 path={output_path}",
            )

        return FileResponse(
            path=str(output_path),
            media_type="application/step",
            filename=f"{task_id}_final.step",
        )
    else:
        # 下载初版 STEP（供第一轮审核参考）
        allowed_states = {
            ParametricGeometryTaskStatus.STEP_GENERATED.value,
            ParametricGeometryTaskStatus.REVIEWED.value,
            ParametricGeometryTaskStatus.SUCCEEDED.value,
            ParametricGeometryTaskStatus.FAILED.value,
        }
        if task.status not in allowed_states:
            raise HTTPException(
                status_code=400,
                detail=(f"任务状态 {task.status} 不允许下载初版 STEP，仅 {sorted(allowed_states)} 状态可下载。"),
            )

        if not task.step_output_path:
            raise HTTPException(
                status_code=404,
                detail="任务初版 STEP 路径为空（可能尚未执行 run）",
            )

        output_path = Path(task.step_output_path)
        if not output_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"初版 STEP 文件不存在 path={output_path}",
            )

        return FileResponse(
            path=str(output_path),
            media_type="application/step",
            filename=f"{task_id}.step",
        )


async def delete_task(task_id: str) -> dict[str, Any]:
    """取消或删除参数化几何任务。

    - 非终态任务：先取消（CANCELLED），再删除任务元信息
    - 终态任务（SUCCEEDED / FAILED / CANCELLED）：直接删除任务元信息
    - SUCCEEDED 状态任务禁止删除（避免误删下游 CAM 模块已引用的最终 STEP）

    注意：STEP 文件与 workspace 目录不会被自动删除，
    避免误删下游链路已引用的资源。
    """
    store = get_task_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    # SUCCEEDED 状态的任务禁止删除（避免误删下游 CAM 模块已引用的最终 STEP）
    if task.status == ParametricGeometryTaskStatus.SUCCEEDED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务 {task_id} 已 SUCCEEDED，禁止删除。最终 STEP 可能已被下游 CAM 模块引用。"),
            suggestion="如确需删除，请先手动清理下游引用，再删除任务",
        )

    pipeline = _get_pipeline()

    # 非终态任务先取消
    terminal_states = {
        ParametricGeometryTaskStatus.SUCCEEDED.value,
        ParametricGeometryTaskStatus.FAILED.value,
        ParametricGeometryTaskStatus.CANCELLED.value,
    }
    if task.status not in terminal_states:
        try:
            pipeline.cancel_task(task_id)
        except ParametricGeometryError as e:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=str(e),
            )
        except Exception as e:
            safe = safe_error_message(e, context="parametric_geometry.delete_task.cancel")
            logger.error(
                "取消任务失败 task_id=%s | error_id=%s | exc=%s",
                task_id,
                safe.get("error_id"),
                e,
                exc_info=True,
            )
            return error(
                code=ErrorCode.INTERNAL_ERROR,
                message=safe["message"],
            )

    deleted = store.delete(task_id)
    if not deleted:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"删除任务失败 task_id={task_id}",
        )

    return success(
        data={"task_id": task_id, "deleted": True},
        message="任务已取消并删除（workspace 文件保留，可手动清理）",
    )
