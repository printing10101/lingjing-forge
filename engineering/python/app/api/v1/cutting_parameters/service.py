"""cutting_parameters 路由业务逻辑（从 routes.py 抽取，波次2 S4）。

端点参数校验/鉴权由 routes.py 负责，本模块仅承载业务实现。
"""

from __future__ import annotations

from __future__ import annotations
import logging
from pathlib import Path
from typing import Any
from fastapi import HTTPException
from fastapi.responses import FileResponse
from app.api.v1.cutting_parameters.schemas import (
    ReviewRequest,
    TaskCreateRequest,
)
from app.config import config
from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message
from app.cutting_parameters import (
    CuttingParametersPipelineError,
    CuttingParametersTaskStatus,
    CuttingReviewError,
    CuttingReviewStatus,
    MaterialResolverError,
    get_material_resolver,
    get_task_store,
)
from app.api.v1.cutting_parameters._helpers import (
    _spawn,
    _get_pipeline,
    _disclaimer_dict,
    _resolve_upstream_calibrated,
)


logger = logging.getLogger(__name__)


async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息、材料列表与工业硬门槛（不创建任务）。

    前端在用户进入切削参数页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d mesh + feature_extraction 特征）
    - 可用材料列表（含 HRC52 待校准状态）
    - 工业生产硬门槛
    - 工程师审核流程说明
    """
    # 列出材料数据库中的可用材料
    try:
        resolver = get_material_resolver()
        available_materials = [
            {
                "id": m.id,
                "name": m.name,
                "category": m.category,
                "hardness_hrc": m.hardness_hrc,
                "calibration_status": m.calibration_status,
                "data_source": m.data_source,
            }
            for m in resolver.list_materials()
        ]
    except Exception as e:
        safe = safe_error_message(e, context="cutting_parameters.list_materials")
        logger.warning(
            "查询材料列表失败 error_id=%s，返回空列表",
            safe.get("error_id"),
        )
        available_materials = []

    return success(
        data={
            "current_tier": config.cutting_parameters.precision_tier,
            "available_tiers": {
                "coarse": "粗加工档位，大切深 + 低精度，operation=roughing",
                "standard": "标准档位，平衡切深与精度，operation=roughing（hole/boss 例外）",
                "high": "精加工档位，小切深 + 高精度，operation=finishing",
            },
            "module_parameters": {
                "default_tool_diameter_mm": config.cutting_parameters.default_tool_diameter_mm,
                "default_num_flutes": config.cutting_parameters.default_num_flutes,
                "default_machine_type": config.cutting_parameters.default_machine_type,
                "max_concurrent": config.cutting_parameters.max_concurrent,
                "task_timeout_seconds": config.cutting_parameters.task_timeout_seconds,
                "task_retention_hours": config.cutting_parameters.task_retention_hours,
                "default_mesh_calibrated": config.cutting_parameters.default_mesh_calibrated,
            },
            "review_workflow": {
                "step_1": "阶段 3 输出 STEP + 阶段 2 confirmed_features.json + material_id → 创建任务（PENDING）",
                "step_2": "异步触发 run_pipeline → 状态 PARAMS_RECOMMENDED（参数已生成，待审核）",
                "step_3": "工程师逐条审核参数：confirmed / rejected / edited",
                "step_4": "全部审核完毕 → 状态 REVIEWED",
                "step_5": "调用 export → 导出 ChatterParams JSON → SUCCEEDED",
                "step_6": "阶段 5 颤振预测读取 ChatterParams JSON 进行稳定性极限计算",
            },
            "available_materials": available_materials,
            "industrial_hard_gates": [
                "切削参数必须经工程师审核后才可导出 ChatterParams",
                "导出的 ChatterParams 仅供阶段 5 颤振预测参考，不可直接用于机床",
                "实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验",
                "G 代码必须经 CAM 软件二次校验后才允许上机床",
                "良品率要求：0 缺陷容忍",
                "配合面公差：0.01mm（手机摄影测量物理上不可达）",
                "CNC 操作资质：需持证操作员",
                "导师签字 + 保险：大一独立项目无法独立完成此环节",
                "材料 K_s（specific_cutting_force）影响颤振预测精度，HRC52 数据待自采校准",
                "本系统定位为「工程师助手」，非「全自动切削参数生成器」",
            ],
            "cutting_disclaimer": _disclaimer_dict(),
        },
        message="切削参数推荐精度档位与工业硬门槛信息",
    )


async def create_task(body: TaskCreateRequest) -> dict[str, Any]:
    """创建切削参数推荐任务。

    适用场景：
    - 阶段 3 parametric_geometry 已输出 STEP 文件
    - 阶段 2 feature_extraction 已导出 confirmed_features.json
    - 用户指定材料 ID（al_6061 / ti_tc4 / steel_hrc52 等）

    若提供 ``source_parametric_geometry_task_id`` 且上游链路完整
    （pg_task 已 SUCCEEDED），系统会自动查询阶段 3 任务的 mesh_calibrated 字段，
    用于决定精度告知中的「上游 mesh 是否已标定」。

    也可显式传入 ``mesh_calibrated`` 覆盖自动查询结果（用于外部导入场景）。

    本端点只创建任务（PENDING 状态），不立即执行。
    需随后调用 POST /tasks/{task_id}/run 触发执行。
    """
    if not config.cutting_parameters.enabled:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="切削参数推荐模块未启用",
            suggestion="请在配置中设置 LNN_CP_ENABLED=true",
        )

    # 校验精度档位
    valid_tiers = {"coarse", "standard", "high"}
    if body.precision_tier not in valid_tiers:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"非法 precision_tier: {body.precision_tier}，应为 {sorted(valid_tiers)}"),
        )

    # 校验材料 ID 存在性（提前失败）
    resolver = get_material_resolver()
    try:
        resolver.get_material(body.material_id)
    except MaterialResolverError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"材料 ID 不存在: {body.material_id}",
            suggestion=str(e),
        )

    # 校验 input_features_path 存在性
    features_path = Path(body.input_features_path)
    if not features_path.is_absolute():
        features_path = Path(config.cutting_parameters.output_dir).parent / features_path

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

    # STEP 路径仅作追溯用，存在性校验为软提示（不阻塞任务创建，便于跨机部署）
    step_path = Path(body.step_file_path)
    if not step_path.exists():
        logger.warning(
            "STEP 文件路径不存在 step_file_path=%s（仅追溯用，不阻塞任务创建）",
            body.step_file_path,
        )

    # 解析 mesh_calibrated：显式传入优先，否则查询上游链路
    if body.mesh_calibrated is not None:
        mesh_calibrated = bool(body.mesh_calibrated)
        step_source = body.step_file_path
    else:
        mesh_calibrated, step_source = _resolve_upstream_calibrated(body.source_parametric_geometry_task_id)

    # default_mesh_calibrated 兜底（保守默认 False）
    if not mesh_calibrated and config.cutting_parameters.default_mesh_calibrated:
        mesh_calibrated = True
        logger.info(
            "mesh_calibrated 未明确确认，按 default_mesh_calibrated=True 兜底 step_source=%s",
            step_source,
        )

    try:
        pipeline = _get_pipeline()
        task = pipeline.create_task(
            source_parametric_geometry_task_id=body.source_parametric_geometry_task_id,
            step_file_path=body.step_file_path,
            input_features_path=str(features_path),
            material_id=body.material_id,
            precision_tier=body.precision_tier,
            mesh_calibrated=mesh_calibrated,
            machine_type=body.machine_type,
            tool_diameter_mm=body.tool_diameter_mm,
            num_flutes=body.num_flutes,
        )
    except Exception as e:
        safe = safe_error_message(e, context="cutting_parameters.create_task")
        logger.error(
            "创建切削参数任务失败 | error_id=%s | exc=%s",
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
            "source_parametric_geometry_task_id": task.source_parametric_geometry_task_id,
            "step_file_path": task.step_file_path,
            "input_features_path": task.input_features_path,
            "material_id": task.material_id,
            "precision_tier": task.precision_tier,
            "mesh_calibrated": mesh_calibrated,
            "machine_type": task.machine_type,
            "tool_diameter_mm": task.tool_diameter_mm,
            "num_flutes": task.num_flutes,
            "cutting_disclaimer": _disclaimer_dict(task=task),
        },
        message=(f"任务已创建 task_id={task.task_id}，请调用 POST /tasks/{task.task_id}/run 触发执行"),
    )


async def run_task(task_id: str) -> dict[str, Any]:
    """异步触发切削参数推荐流水线执行。

    执行流程：
    1. 加载阶段 2 confirmed_features.json → 特征列表
    2. MaterialResolver 查询材料切削参数基线（含 HRC52 补充）
    3. CuttingParamRecommender.recommend() 为每个特征推荐切削参数
    4. 状态置为 PARAMS_RECOMMENDED（等待工程师审核）

    仅 PENDING / FAILED 状态可触发执行（FAILED 允许重试）。
    """
    store = get_task_store()
    task = store.get_task(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    if task.status not in (
        CuttingParametersTaskStatus.PENDING.value,
        CuttingParametersTaskStatus.FAILED.value,
    ):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态不允许执行当前操作 status={task.status}。仅 PENDING / FAILED 状态可触发执行。"),
        )

    # 重试场景：清空错误信息
    if task.status == CuttingParametersTaskStatus.FAILED.value:
        task.error_message = ""
        store.update_task(task)

    pipeline = _get_pipeline()
    _spawn(pipeline.run_pipeline(task_id))

    return success(
        data={
            "task_id": task_id,
            "status": CuttingParametersTaskStatus.RUNNING.value,
            "message": (
                "任务已开始执行，请轮询 GET /tasks/{task_id} 获取状态。"
                "执行完成后状态将变为 PARAMS_RECOMMENDED，等待工程师审核切削参数。"
            ),
        },
        message="任务已开始执行",
    )


async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、审核进度、ChatterParams 路径、精度告知字段。"""
    store = get_task_store()
    task = store.get_task(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    # 统计审核进度
    pending_count = sum(1 for p in task.recommended_params if p.review_status == CuttingReviewStatus.PENDING.value)
    confirmed_count = sum(1 for p in task.recommended_params if p.review_status == CuttingReviewStatus.CONFIRMED.value)
    rejected_count = sum(1 for p in task.recommended_params if p.review_status == CuttingReviewStatus.REJECTED.value)
    edited_count = sum(1 for p in task.recommended_params if p.review_status == CuttingReviewStatus.EDITED.value)

    chatter_params_ready = bool(task.chatter_params_path)

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "source_parametric_geometry_task_id": task.source_parametric_geometry_task_id,
            "step_file_path": task.step_file_path,
            "input_features_path": task.input_features_path,
            "material_id": task.material_id,
            "precision_tier": task.precision_tier,
            "mesh_calibrated": task.mesh_calibrated,
            "machine_type": task.machine_type,
            "tool_diameter_mm": task.tool_diameter_mm,
            "num_flutes": task.num_flutes,
            "feature_count": len(task.recommended_params),
            "recommended_count": len(task.recommended_params),
            "pending_count": pending_count,
            "confirmed_count": confirmed_count,
            "rejected_count": rejected_count,
            "edited_count": edited_count,
            "cam_validation_required": task.cam_validation_required,
            "chatter_params_path": task.chatter_params_path,
            "error_message": task.error_message,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "cutting_disclaimer": _disclaimer_dict(task=task, chatter_params_ready=chatter_params_ready),
        },
    )


async def list_tasks(limit: int = 20) -> dict[str, Any]:
    """列出最近的切削参数任务（按创建时间倒序）。"""
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
                    "source_parametric_geometry_task_id": t.source_parametric_geometry_task_id,
                    "material_id": t.material_id,
                    "feature_count": len(t.recommended_params),
                    "precision_tier": t.precision_tier,
                    "mesh_calibrated": t.mesh_calibrated,
                    "machine_type": t.machine_type,
                    "tool_diameter_mm": t.tool_diameter_mm,
                    "num_flutes": t.num_flutes,
                    "chatter_params_path": t.chatter_params_path,
                    "created_at": t.created_at,
                    "completed_at": t.completed_at,
                }
                for t in tasks
            ],
            "total": len(tasks),
        },
    )


async def get_task_result(task_id: str) -> dict[str, Any]:
    """获取任务结果摘要与完整推荐参数列表（含审核状态）。

    仅当任务状态为 PARAMS_RECOMMENDED / REVIEWED / SUCCEEDED 时可调用。
    返回的每条推荐参数包含：
    - feature_id / feature_type / operation
    - spindle_speed_rpm / feed_rate_mm_per_min / feed_per_tooth_mm
    - cutting_speed_m_per_min / axial_depth_mm / radial_depth_mm
    - review_status（pending / confirmed / rejected / edited）
    - effective_params（合并 edited_params 后的生效参数）
    - warnings（推荐时生成的告警，如材料 pending_calibration / 切速越界）
    """
    store = get_task_store()
    task = store.get_task(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    allowed_states = {
        CuttingParametersTaskStatus.PARAMS_RECOMMENDED.value,
        CuttingParametersTaskStatus.REVIEWED.value,
        CuttingParametersTaskStatus.SUCCEEDED.value,
    }
    if task.status not in allowed_states:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许获取结果，仅 {sorted(allowed_states)} 状态可获取。"),
            suggestion="请等待状态变为 params_recommended 后再调用此端点",
        )

    chatter_params_ready = bool(task.chatter_params_path)

    recommended_params_data = [
        {
            "feature_id": p.feature_id,
            "feature_type": p.feature_type,
            "operation": p.operation,
            "spindle_speed_rpm": p.spindle_speed_rpm,
            "feed_rate_mm_per_min": p.feed_rate_mm_per_min,
            "feed_per_tooth_mm": p.feed_per_tooth_mm,
            "cutting_speed_m_per_min": p.cutting_speed_m_per_min,
            "axial_depth_mm": p.axial_depth_mm,
            "radial_depth_mm": p.radial_depth_mm,
            "estimated_cutting_time_s": p.estimated_cutting_time_s,
            "tool_life_estimate_min": p.tool_life_estimate_min,
            "warnings": list(p.warnings),
            "review_status": p.review_status,
            "edited_params": dict(p.edited_params),
            "effective_params": p.effective_params(),
            "reviewed_by": p.reviewed_by,
            "reviewed_at": p.reviewed_at,
            "engineer_notes": p.engineer_notes,
            "material_id": p.material_id,
            "tool_diameter_mm": p.tool_diameter_mm,
            "num_flutes": p.num_flutes,
        }
        for p in task.recommended_params
    ]

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "source_parametric_geometry_task_id": task.source_parametric_geometry_task_id,
            "material_id": task.material_id,
            "precision_tier": task.precision_tier,
            "mesh_calibrated": task.mesh_calibrated,
            "feature_count": len(task.recommended_params),
            "recommended_count": len(task.recommended_params),
            "cam_validation_required": task.cam_validation_required,
            "chatter_params_path": task.chatter_params_path,
            "error_message": task.error_message or None,
            "recommended_params": recommended_params_data,
            "cutting_disclaimer": _disclaimer_dict(task=task, chatter_params_ready=chatter_params_ready),
        },
    )


async def review_params(
    task_id: str,
    feature_id: str,
    body: ReviewRequest,
) -> dict[str, Any]:
    """工程师审核单个特征的切削参数。

    本端点是 human-in-the-loop 的核心入口（项目记忆硬约束：
    系统定位「工程师助手」，非「全自动切削参数生成器」）。

    审核动作：
    - ``confirmed``: 推荐参数无误
    - ``rejected``:  拒绝该特征（不进入最终 ChatterParams）
    - ``edited``:    参数需修正，需同时提供 ``edited_params``

    当所有特征都被审核（confirmed / rejected / edited）后，
    任务状态自动从 PARAMS_RECOMMENDED 转为 REVIEWED，
    随后可调用 POST /tasks/{task_id}/export 导出 ChatterParams。

    请求体中 ``feature_id`` 作为查询参数传入，便于 RESTful 路径表达。
    """
    store = get_task_store()
    task = store.get_task(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    if task.status != CuttingParametersTaskStatus.PARAMS_RECOMMENDED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(
                f"任务状态 {task.status} 不允许审核，"
                f"仅 {CuttingParametersTaskStatus.PARAMS_RECOMMENDED.value} 状态可审核"
            ),
            suggestion="请等待流水线执行完成（状态变为 params_recommended）后再审核",
        )

    # 校验 action
    valid_actions = {
        CuttingReviewStatus.CONFIRMED.value,
        CuttingReviewStatus.REJECTED.value,
        CuttingReviewStatus.EDITED.value,
    }
    if body.action not in valid_actions:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"非法 action: {body.action}，应为 {sorted(valid_actions)}",
        )

    # edited 动作必须提供 edited_params
    if body.action == CuttingReviewStatus.EDITED.value and not body.edited_params:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="action=edited 时必须提供 edited_params",
            suggestion="请提供编辑后的参数（字段可为 spindle_speed_rpm / feed_rate_mm_per_min "
            "/ feed_per_tooth_mm / cutting_speed_m_per_min / axial_depth_mm "
            "/ radial_depth_mm 的子集）",
        )

    try:
        pipeline = _get_pipeline()
        reviewed_params = pipeline.review_params(
            task_id=task_id,
            feature_id=feature_id,
            review_status=body.action,
            reviewed_by=body.reviewed_by,
            edited_params=body.edited_params,
            engineer_notes=body.engineer_notes,
        )
    except CuttingReviewError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="cutting_parameters.review_params")
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

    # 重新查询任务状态（review_params 内部可能已将状态置为 REVIEWED）
    task_after = store.get_task(task_id)
    if task_after is None:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="审核后任务丢失，请检查任务存储",
        )

    all_reviewed = all(p.review_status != CuttingReviewStatus.PENDING.value for p in task_after.recommended_params)

    return success(
        data={
            "task_id": task_id,
            "feature_id": reviewed_params.feature_id,
            "feature_type": reviewed_params.feature_type,
            "review_status": reviewed_params.review_status,
            "effective_params": reviewed_params.effective_params(),
            "all_reviewed": all_reviewed,
            "task_status": task_after.status,
            "cutting_disclaimer": _disclaimer_dict(task=task_after),
        },
        message=(
            f"特征 {feature_id} 已审核（action={body.action}）。"
            + (
                " 全部特征已审核完毕，可调用 POST /tasks/{task_id}/export 导出 ChatterParams JSON。"
                if all_reviewed
                else " 仍有特征待审核。"
            )
        ),
    )


async def export_chatter_params(task_id: str) -> dict[str, Any]:
    """导出 ChatterParams JSON 文件供阶段 5 颤振预测使用。

    本端点在所有特征审核完毕（状态 REVIEWED）后调用：
    - 仅导出 confirmed + edited 的特征参数（rejected 排除）
    - 调用 to_chatter_params_dict() 转换为阶段 5 接口契约
    - 写入 {task_id}_chatter_params.json
    - 状态置为 SUCCEEDED

    导出后，可通过 GET /tasks/{task_id}/chatter_params/download 下载 JSON 文件。

    工业硬约束（项目记忆）：
    - 导出的 ChatterParams 仅供阶段 5 颤振预测参考，不可直接用于机床
    - 实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字
    - K_s（cutting_force_coeff）直接取自材料数据库，HRC52 数据待自采校准
    """
    store = get_task_store()
    task = store.get_task(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    if task.status != CuttingParametersTaskStatus.REVIEWED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许导出，仅 {CuttingParametersTaskStatus.REVIEWED.value} 状态可导出"),
            suggestion="请先完成所有特征的审核（状态变为 reviewed）后再导出",
        )

    try:
        pipeline = _get_pipeline()
        chatter_params_path = pipeline.export_chatter_params(task_id)
    except CuttingParametersPipelineError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="cutting_parameters.export_chatter_params")
        logger.error(
            "导出 ChatterParams 失败 task_id=%s | error_id=%s | exc=%s",
            task_id,
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
        )

    # 重新查询任务获取最新状态
    task_after = store.get_task(task_id)
    if task_after is None:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="导出后任务丢失，请检查任务存储",
        )

    download_url = f"/api/v1/cutting_parameters/tasks/{task_id}/chatter_params/download"

    return success(
        data={
            "task_id": task_after.task_id,
            "status": task_after.status,
            "source_parametric_geometry_task_id": task_after.source_parametric_geometry_task_id,
            "material_id": task_after.material_id,
            "feature_count": len(task_after.recommended_params),
            "chatter_params_path": chatter_params_path,
            "download_url": download_url,
            "chatter_params_ready": True,
            "cutting_disclaimer": _disclaimer_dict(task=task_after, chatter_params_ready=True),
        },
        message=(
            f"ChatterParams 已导出 path={chatter_params_path}。"
            "可通过 download_url 下载，并供阶段 5 颤振预测使用。"
            "注意：实际加工必须经 CAM 软件二次校验后才允许上机床。"
        ),
    )


async def download_chatter_params(task_id: str) -> FileResponse:
    """下载 ChatterParams JSON 文件（供阶段 5 颤振预测读取）。

    仅 SUCCEEDED 状态可下载。

    文件结构：
    - task_id / source_parametric_geometry_task_id / material_id
    - chatter_params_list: list[dict]
      每条含 feature_id / feature_type / operation / chatter_params
      （chatter_params 内含 spindle_rpm / machine / tool / axial_depth）
    - industrial_hard_gates_note: 强制告知工业硬约束
    """
    store = get_task_store()
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在 task_id={task_id}")

    if task.status != CuttingParametersTaskStatus.SUCCEEDED.value:
        raise HTTPException(
            status_code=400,
            detail=(
                f"任务未 SUCCEEDED status={task.status}，无法下载 ChatterParams。"
                "请先完成审核并调用 POST /tasks/{task_id}/export。"
            ),
        )

    if not task.chatter_params_path:
        raise HTTPException(
            status_code=404,
            detail="任务 ChatterParams 路径为空",
        )

    output_path = Path(task.chatter_params_path)
    if not output_path.exists():
        # [H15] 不泄露服务器文件系统路径，仅记录到服务端日志
        logger.error("ChatterParams file missing for task %s at %s", task_id, output_path)
        raise HTTPException(
            status_code=404,
            detail="ChatterParams 文件不存在，请联系管理员检查服务端存储",
        )

    return FileResponse(
        path=str(output_path),
        media_type="application/json",
        filename=f"{task_id}_chatter_params.json",
    )


async def delete_task(task_id: str) -> dict[str, Any]:
    """取消或删除切削参数推荐任务。

    - 非终态任务：将状态置为 CANCELLED 后删除任务元信息
    - 终态任务（FAILED / CANCELLED）：直接删除任务元信息
    - SUCCEEDED 状态任务禁止删除（项目记忆硬约束：阶段 5 颤振预测可能已引用其 ChatterParams）

    注意：ChatterParams JSON 文件与 workspace 目录不会被自动删除，
    避免误删下游链路已引用的资源。
    """
    store = get_task_store()
    task = store.get_task(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    # SUCCEEDED 状态的任务禁止删除（避免误删阶段 5 已引用的 ChatterParams）
    if task.status == CuttingParametersTaskStatus.SUCCEEDED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务 {task_id} 已 SUCCEEDED，禁止删除。ChatterParams 可能已被阶段 5 颤振预测引用。"),
            suggestion="如确需删除，请先手动清理下游引用，再删除任务",
        )

    # 非终态任务先取消（修改状态后持久化）
    terminal_states = {
        CuttingParametersTaskStatus.FAILED.value,
        CuttingParametersTaskStatus.CANCELLED.value,
    }
    if task.status not in terminal_states:
        task.status = CuttingParametersTaskStatus.CANCELLED.value
        try:
            store.update_task(task)
        except Exception as e:
            safe = safe_error_message(e, context="cutting_parameters.delete_task.cancel")
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

    # delete_task 内部会检查 SUCCEEDED 状态并抛 ReviewError（已在前置校验中拦截）
    try:
        deleted = store.delete_task(task_id)
    except Exception as e:
        safe = safe_error_message(e, context="cutting_parameters.delete_task")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
        )

    if not deleted:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"删除任务失败 task_id={task_id}",
        )

    return success(
        data={"task_id": task_id, "deleted": True},
        message="任务已取消并删除（workspace 文件保留，可手动清理）",
    )
