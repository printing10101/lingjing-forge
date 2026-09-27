"""CAM 校验路由业务逻辑（从 routes.py 抽取，S4）。

端点参数校验/鉴权由 routes.py 负责，本模块仅承载业务实现。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi.responses import FileResponse, JSONResponse

from app.api.v1._shared.task_infra import (
    build_file_download_response,
    build_not_found_response,
    spawn_background_task as _spawn,
)
from app.api.v1.cam_validation._helpers import (
    _disclaimer_dict,
    _get_pipeline,
    _resolve_upstream_gcode_calibrated,
)
from app.api.v1.cam_validation._schemas import (
    ReviewRequest,
    TaskCreateRequest,
)
from app.cam_validation import (
    CamReviewStatus,
    CamValidationError,
    CamValidationPipelineError,
    CamValidationTaskStatus,
    ReviewError,
    VALID_CAM_BACKENDS,
    get_task_store,
    is_valid_cam_backend,
)
from app.config import config
from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)


async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息、可用 CAM 后端与工业硬门槛（不创建任务）。

    前端在用户进入 CAM 校验页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d → ... → gcode_generation）
    - 支持的 CAM 后端策略（5 个）
    - 工业生产硬门槛（CAM 二次校验强制 + 操作员资质 + 导师签字）
    - 工程师审核流程说明
    """
    return success(
        data={
            "current_tier": config.cam_validation.precision_tier,
            "available_tiers": {
                "coarse": "粗加工档位，大切深 + 低精度，常配合 roughing 使用",
                "standard": "标准档位，平衡切深与精度（默认）",
                "high": "精加工档位，小切深 + 高精度，常配合 finishing 使用",
                "mesh_calibrated": "网格标定档位（上游 mesh 已做尺度归一化，最高精度）",
            },
            "module_parameters": {
                "default_cam_backend": config.cam_validation.default_cam_backend,
                "allow_delete_succeeded": config.cam_validation.allow_delete_succeeded,
                "cam_validation_required": config.cam_validation.cam_validation_required,
                "max_concurrent": config.cam_validation.max_concurrent,
                "task_timeout_seconds": config.cam_validation.task_timeout_seconds,
                "task_retention_hours": config.cam_validation.task_retention_hours,
                "nx_open_executable": config.cam_validation.nx_open_executable,
                "powermill_executable": config.cam_validation.powermill_executable,
                "pycam_executable": config.cam_validation.pycam_executable,
            },
            "supported_cam_backends": {
                "internal_only": ("仅内部预校验（CollisionDetector AABB 包围盒），不调用 CAM 软件。用于快速预筛。"),
                "pycam": (
                    "PyCAM 开源刀轨校验（subprocess 调用包装器脚本，4 项基础检查），适合无 NX/PowerMill 许可证场景。"
                ),
                "nx_open": (
                    "Siemens NX Open（高端 CAM 软件，支持 5-axis + 后处理器语法校验），需配置 nx_open_executable 路径。"
                ),
                "powermill": ("Autodesk PowerMill（模具五轴 CAM），需配置 powermill_executable 路径。"),
                "manual": ("人工校验（CAM 软件不可用时的降级策略），生成校验清单 + 工程师手动回填结果。"),
            },
            "industrial_hard_gates": [
                "系统定位「工程师助手」，非「全自动 CAM 校验器」，最终决策权在工程师",
                "内部预校验（CollisionDetector）是 AABB 包围盒级别快速预筛，不可替代 CAM 软件二次校验",
                "系统绝不直接接口 CNC 控制器，CAM 软件调用通过 subprocess",
                "CAM 二次校验强制：cam_validation_required 始终 True，不可由环境变量关闭",
                "阶段 7 产物终止于「CAM 校验报告 JSON」，不触及物理机床",
                "实际加工必须经持证操作员 + 导师签字 + 保险，大一独立项目不可独立完成机床执行",
                "SUCCEEDED 状态禁止删除（cam_report.json 是链路最终产物，供审计追溯）",
                "HRC52 pending_calibration 由阶段 5 标注，阶段 7 仅继承并体现在告知文本",
                "极限切深为理论值，实际加工必须留 20% 安全裕度（SAFETY_MARGIN_RATIO=0.8）",
                "CAM 软件不可用时自动降级到 manual，追加警告，不阻塞任务",
            ],
            "cam_disclaimer": _disclaimer_dict(),
            "workflow_summary": {
                "step_1": (
                    "POST /tasks 创建任务（输入 G 代码报告路径 + G 代码文件路径 + 控制器类型 + 材料名称 + CAM 后端）"
                ),
                "step_2": ("POST /tasks/{task_id}/run 异步触发双层校验流水线（内部预校验 + CAM 软件二次校验）"),
                "step_3": "GET /tasks/{task_id} 轮询状态（PENDING → RUNNING → VALIDATED）",
                "step_4": ("POST /tasks/{task_id}/review?feature_id=... 工程师逐条审核校验结果"),
                "step_5": (
                    "POST /tasks/{task_id}/confirm 确认任务（REVIEWED → SUCCEEDED + "
                    "导出 cam_report.json + internal_report.json）"
                ),
                "step_6": (
                    "GET /tasks/{task_id}/report/download 下载 CAM 校验报告 JSON，"
                    "供审计追溯；GET /tasks/{task_id}/internal_report/download "
                    "下载内部预校验详细报告（供前端可视化）"
                ),
            },
        },
    )


async def create_task(body: TaskCreateRequest) -> dict[str, Any]:
    """创建 CAM 校验任务。

    创建后状态为 PENDING，需调用 POST /tasks/{task_id}/run 触发执行。

    输入解析优先级：
    1. 若 source_gcode_generation_task_id 对应的阶段 6 任务已 SUCCEEDED，
       自动读取 gcode_report_path / gcode_file_path / controller_type /
       material_name / safe_z / stock_top_z。
    2. 若上游任务不存在或未完成，必须显式提供 gcode_report_path。
    3. gcode_file_path 可留空（从 report.json 的 gcode_file_path 字段读取）。

    工业硬约束（项目记忆）：
    - cam_validation_required 始终 True（强制 CAM 二次校验）
    - cam_backend 非法时返回 INVALID_REQUEST
    """
    # 从上游阶段 6 任务追溯 G 代码报告 + 上下文
    (
        upstream_gcode_report_path,
        upstream_gcode_file_path,
        upstream_controller_type,
        upstream_material_name,
        upstream_safe_z,
        upstream_stock_top_z,
        _upstream_pending_calibration,
        _upstream_prediction_method,
    ) = _resolve_upstream_gcode_calibrated(body.source_gcode_generation_task_id)

    # 解析 gcode_report_path（显式 > 上游 > 报错）
    gcode_report_path = body.gcode_report_path or upstream_gcode_report_path
    if not gcode_report_path:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(
                "gcode_report_path 为空且无法从上游阶段 6 任务读取 "
                f"source_gcode_generation_task_id="
                f"{body.source_gcode_generation_task_id}"
            ),
            suggestion=("请显式提供 gcode_report_path，或确认上游阶段 6 任务已 SUCCEEDED 且已导出 G 代码报告 JSON。"),
        )

    # 校验 G 代码报告 JSON 文件存在
    if not Path(gcode_report_path).exists():
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"阶段 6 G 代码报告 JSON 不存在 path={gcode_report_path}",
            suggestion="请先在阶段 6 完成审核并导出 G 代码报告 JSON。",
        )

    # 解析 gcode_file_path（显式 > 上游 > 留空由 pipeline 从 report 读取）
    gcode_file_path = body.gcode_file_path or upstream_gcode_file_path

    # 解析 controller_type（显式 > 上游 > 默认值）
    controller_type = body.controller_type or upstream_controller_type or "fanuc_0i"

    # 解析 material_name（显式 > 上游 > 默认值）
    material_name = body.material_name or upstream_material_name or "45#钢"

    # 解析 safe_z / stock_top_z（显式 > 上游 > 默认值）
    safe_z = body.safe_z if body.safe_z != 80.0 else upstream_safe_z
    stock_top_z = body.stock_top_z if body.stock_top_z != 50.0 else upstream_stock_top_z

    # 校验 cam_backend 合法性
    if not is_valid_cam_backend(body.cam_backend):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"非法 cam_backend: {body.cam_backend}，合法值：{sorted(VALID_CAM_BACKENDS)}"),
        )

    try:
        pipeline = _get_pipeline()
        task = pipeline.create_task(
            source_gcode_report_path=gcode_report_path,
            source_gcode_file_path=gcode_file_path,
            controller_type=controller_type,
            material_name=material_name,
            safe_z=safe_z,
            stock_top_z=stock_top_z,
            cam_backend=body.cam_backend,
            tool_diameter_mm=body.tool_diameter_mm,
            tool_type=body.tool_type,
        )
    except CamValidationPipelineError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="cam_validation.create_task")
        logger.error(
            "创建任务失败 gcode_report=%s | error_id=%s | exc=%s",
            gcode_report_path,
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
            "source_gcode_report_path": task.source_gcode_report_path,
            "source_gcode_file_path": task.source_gcode_file_path,
            "controller_type": task.controller_type,
            "material_name": task.material_name,
            "safe_z": task.safe_z,
            "stock_top_z": task.stock_top_z,
            "cam_backend_requested": task.cam_backend_requested,
            "cam_validation_required": task.cam_validation_required,
            "cam_disclaimer": _disclaimer_dict(task=task),
        },
        message=(f"任务已创建 task_id={task.task_id}，请调用 POST /tasks/{task.task_id}/run 触发执行"),
    )


async def run_task(task_id: str) -> dict[str, Any]:
    """异步触发 CAM 校验流水线执行。

    执行流程：
    1. GCodeLoader.load_from_report() 加载阶段 6 G 代码 + feature_results
    2. InternalValidator.validate() 复用 CollisionDetector 执行内部预校验
       + 按 block_number 归因到 feature_results.line_range
    3. CamAdapter.validate() 调用 CAM 软件二次校验（_cam_call_lock 串行化）
    4. 合并两层校验结果到 feature_validation_results
    5. 写入 internal_report + cam_software_report
    6. 状态置为 VALIDATED（等待工程师审核）

    仅 PENDING / FAILED 状态可触发执行（FAILED 允许重试）。
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except CamValidationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    if task.status not in (
        CamValidationTaskStatus.PENDING.value,
        CamValidationTaskStatus.FAILED.value,
    ):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态不允许执行当前操作 status={task.status}。仅 PENDING / FAILED 状态可触发执行。"),
        )

    # 重试场景：清空错误信息
    if task.status == CamValidationTaskStatus.FAILED.value:
        task.error_message = ""
        task.errors = []
        task.warnings = []
        store.update_task(task)

    pipeline = _get_pipeline()
    _spawn(pipeline.run_pipeline(task_id))

    return success(
        data={
            "task_id": task_id,
            "status": CamValidationTaskStatus.RUNNING.value,
            "message": (
                "任务已开始执行，请轮询 GET /tasks/{task_id} 获取状态。"
                "执行完成后状态将变为 VALIDATED（双层校验完成，等待工程师审核），"
                "失败时状态为 FAILED。"
            ),
        },
        message="任务已开始执行",
    )


async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、审核进度、CAM 校验统计、导出产物路径。"""
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except CamValidationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    # 统计审核进度
    pending_review_count = sum(
        1 for r in task.feature_validation_results if r.review_status == CamReviewStatus.PENDING.value
    )
    confirmed_count = sum(
        1 for r in task.feature_validation_results if r.review_status == CamReviewStatus.CONFIRMED.value
    )
    rejected_count = sum(
        1 for r in task.feature_validation_results if r.review_status == CamReviewStatus.REJECTED.value
    )
    edited_count = sum(1 for r in task.feature_validation_results if r.review_status == CamReviewStatus.EDITED.value)

    cam_report_exported = bool(task.cam_report_path)

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "source_gcode_report_path": task.source_gcode_report_path,
            "source_gcode_file_path": task.source_gcode_file_path,
            "controller_type": task.controller_type,
            "material_name": task.material_name,
            "safe_z": task.safe_z,
            "stock_top_z": task.stock_top_z,
            "gcode_total_lines": task.gcode_total_lines,
            "total_features": task.total_features,
            "passed_features": task.passed_features,
            "failed_features": task.failed_features,
            "pending_calibration": task.pending_calibration,
            "prediction_method": task.prediction_method,
            "cam_backend_requested": task.cam_backend_requested,
            "cam_backend_used": task.cam_backend_used,
            "cam_backend_fallback_reason": task.cam_backend_fallback_reason,
            # 体素材料去除仿真（闭环强制层；None=未执行，DNC 闸门按未通过处理）
            "voxel_check_passed": task.voxel_check_passed,
            "voxel_collision_count": task.voxel_collision_count,
            "voxel_engine": task.voxel_engine,
            "pending_review_count": pending_review_count,
            "confirmed_count": confirmed_count,
            "rejected_count": rejected_count,
            "edited_count": edited_count,
            "cam_validation_required": task.cam_validation_required,
            "cam_report_path": task.cam_report_path or "",
            "internal_report_path": task.internal_report_path or "",
            "error_message": task.error_message or "",
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "reviewed_by": task.reviewed_by,
            "reviewed_at": task.reviewed_at,
            "warnings": list(task.warnings),
            "errors": list(task.errors),
            "cam_disclaimer": _disclaimer_dict(task=task, cam_report_exported=cam_report_exported),
        },
    )


async def list_tasks(
    limit: int = 20,
    status_filter: str = "",
) -> dict[str, Any]:
    """列出最近的 CAM 校验任务（按创建时间倒序）。

    Args:
        limit: 返回数量上限（1-100）
        status_filter: 可选状态过滤（pending / running / validated / reviewed /
                       succeeded / failed / timeout / cancelled）
    """
    if limit < 1 or limit > 100:
        limit = max(1, min(100, limit))

    store = get_task_store()
    tasks = store.list_tasks(status_filter=status_filter or None)
    if limit:
        tasks = tasks[:limit]

    return success(
        data={
            "tasks": [
                {
                    "task_id": t.task_id,
                    "status": t.status,
                    "controller_type": t.controller_type,
                    "material_name": t.material_name,
                    "gcode_total_lines": t.gcode_total_lines,
                    "total_features": t.total_features,
                    "passed_features": t.passed_features,
                    "failed_features": t.failed_features,
                    "pending_calibration": t.pending_calibration,
                    "prediction_method": t.prediction_method,
                    "cam_backend_requested": t.cam_backend_requested,
                    "cam_backend_used": t.cam_backend_used,
                    "cam_report_path": t.cam_report_path or "",
                    "internal_report_path": t.internal_report_path or "",
                    "started_at": t.started_at,
                    "completed_at": t.completed_at,
                }
                for t in tasks
            ],
            "total": len(tasks),
        },
    )


async def get_task_result(task_id: str) -> dict[str, Any]:
    """获取任务结果摘要与完整特征校验结果列表（含审核状态）。

    仅当任务状态为 VALIDATED / REVIEWED / SUCCEEDED / FAILED 时可调用。
    返回的每条特征校验结果包含：
    - feature_id / feature_type / line_range（在 G 代码中的行号区间）
    - internal_check_passed / internal_events（碰撞事件列表）
    - cam_check_passed / cam_messages（CAM 软件消息）
    - cam_backend_used（实际使用的后端，可能因降级与 requested 不同）
    - review_status（pending / confirmed / rejected / edited）
    - edited_params（工程师编辑的参数）
    - 阶段 6 上下文（spindle_rpm / axial_depth_mm / limit_depth_mm / stable /
      safety_margin_ratio / warning）
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except CamValidationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    allowed_states = {
        CamValidationTaskStatus.VALIDATED.value,
        CamValidationTaskStatus.REVIEWED.value,
        CamValidationTaskStatus.SUCCEEDED.value,
        CamValidationTaskStatus.FAILED.value,  # FAILED 也允许查看（含校验失败原因）
    }
    if task.status not in allowed_states:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许获取结果，仅 {sorted(allowed_states)} 状态可获取。"),
            suggestion="请等待状态变为 validated 后再调用此端点",
        )

    cam_report_exported = bool(task.cam_report_path)

    feature_results_data = [
        {
            "feature_id": r.feature_id,
            "feature_type": r.feature_type,
            "line_range": list(r.line_range),
            "internal_check_passed": r.internal_check_passed,
            "internal_events": list(r.internal_events),
            "voxel_check_passed": r.voxel_check_passed,
            "voxel_collision_blocks": list(r.voxel_collision_blocks),
            "cam_check_passed": r.cam_check_passed,
            "cam_messages": list(r.cam_messages),
            "cam_backend_used": r.cam_backend_used,
            "review_status": r.review_status,
            "edited_params": dict(r.edited_params),
            "spindle_rpm": round(r.spindle_rpm, 4),
            "axial_depth_mm": round(r.axial_depth_mm, 4),
            "limit_depth_mm": round(r.limit_depth_mm, 4),
            "stable": r.stable,
            "safety_margin_ratio": round(r.safety_margin_ratio, 4),
            "warning": r.warning,
        }
        for r in task.feature_validation_results
    ]

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "controller_type": task.controller_type,
            "material_name": task.material_name,
            "gcode_total_lines": task.gcode_total_lines,
            "total_features": task.total_features,
            "passed_features": task.passed_features,
            "failed_features": task.failed_features,
            "pending_calibration": task.pending_calibration,
            "prediction_method": task.prediction_method,
            "cam_backend_requested": task.cam_backend_requested,
            "cam_backend_used": task.cam_backend_used,
            "cam_backend_fallback_reason": task.cam_backend_fallback_reason,
            "cam_validation_required": task.cam_validation_required,
            # 体素材料去除仿真（闭环强制层；None=未执行，DNC 闸门按未通过处理）
            "voxel_check_passed": task.voxel_check_passed,
            "voxel_collision_count": task.voxel_collision_count,
            "voxel_engine": task.voxel_engine,
            "cam_report_path": task.cam_report_path or None,
            "internal_report_path": task.internal_report_path or None,
            "error_message": task.error_message or None,
            "feature_results": feature_results_data,
            "cam_disclaimer": _disclaimer_dict(task=task, cam_report_exported=cam_report_exported),
        },
    )


async def review_feature(
    task_id: str,
    feature_id: str,
    body: ReviewRequest,
) -> dict[str, Any]:
    """工程师审核单个特征的 CAM 校验结果。

    本端点是 human-in-the-loop 的核心入口（项目记忆硬约束：
    系统定位「工程师助手」，非「全自动 CAM 校验器」）。

    审核动作：
    - ``confirmed``: 双层校验结论无误（含已知警告可接受）
    - ``rejected``:  拒绝该特征（需阶段 6 重新生成 G 代码）
    - ``edited``:    参数需修正，需同时提供 ``edited_params``
        可编辑字段：safe_z / cam_backend / stock_top_z

    当所有特征都被审核（confirmed / rejected / edited）后，
    任务状态自动从 VALIDATED 转为 REVIEWED，
    随后可调用 POST /tasks/{task_id}/confirm 导出 cam_report + internal_report JSON。

    请求体中 ``feature_id`` 作为查询参数传入，便于 RESTful 路径表达。

    Note:
        edited 仅记录修改意图，不触发流水线重新执行。
        如需重跑，请删除任务后重新创建。
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except CamValidationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    if task.status != CamValidationTaskStatus.VALIDATED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许审核，仅 {CamValidationTaskStatus.VALIDATED.value} 状态可审核"),
            suggestion="请等待流水线执行完成（状态变为 validated）后再审核",
        )

    # 校验 action
    valid_actions = {
        CamReviewStatus.CONFIRMED.value,
        CamReviewStatus.REJECTED.value,
        CamReviewStatus.EDITED.value,
    }
    if body.action not in valid_actions:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"非法 action: {body.action}，应为 {sorted(valid_actions)}",
        )

    # edited 动作必须提供 edited_params
    if body.action == CamReviewStatus.EDITED.value and not body.edited_params:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="action=edited 时必须提供 edited_params",
            suggestion=("请提供编辑后的参数（字段可为 safe_z / cam_backend / stock_top_z 的子集）"),
        )

    try:
        pipeline = _get_pipeline()
        reviewed_result = pipeline.review_task(
            task_id=task_id,
            feature_id=feature_id,
            review_status=body.action,
            reviewed_by=body.reviewed_by,
            edited_params=body.edited_params,
            engineer_notes=body.engineer_notes,
        )
    except ReviewError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except CamValidationPipelineError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="cam_validation.review_feature")
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

    # 重新查询任务状态（review_task 内部可能已将状态置为 REVIEWED）
    try:
        task_after = store.get_task(task_id)
    except CamValidationError:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="审核后任务丢失，请检查任务存储",
        )

    all_reviewed = all(r.review_status != CamReviewStatus.PENDING.value for r in task_after.feature_validation_results)

    return success(
        data={
            "task_id": task_id,
            "feature_id": reviewed_result.feature_id,
            "feature_type": reviewed_result.feature_type,
            "review_status": reviewed_result.review_status,
            "edited_params": dict(reviewed_result.edited_params),
            "all_reviewed": all_reviewed,
            "task_status": task_after.status,
            "cam_disclaimer": _disclaimer_dict(task=task_after),
        },
        message=(
            f"特征 {feature_id} 已审核（action={body.action}）。"
            + (
                " 全部特征已审核完毕，可调用 POST /tasks/{task_id}/confirm 导出 CAM 校验报告 JSON。"
                if all_reviewed
                else " 仍有特征待审核。"
            )
        ),
    )


async def confirm_task(
    task_id: str,
    reviewer: str = "engineer",
) -> dict[str, Any]:
    """确认任务并导出 CAM 校验报告 JSON + 内部预校验详细报告 JSON。

    本端点在所有特征审核完毕（状态 REVIEWED）后调用：
    - 导出 cam_report.json 至 {workspace_dir}/{task_id}.cam_report.json
      （最终结论，供审计追溯）
    - 导出 internal_report.json 至 {workspace_dir}/{task_id}.internal_report.json
      （调试细节，供前端可视化）
    - 状态置为 SUCCEEDED

    导出后，可通过 GET /tasks/{task_id}/report/download 下载 cam_report.json，
    通过 GET /tasks/{task_id}/internal_report/download 下载 internal_report.json。

    工业硬约束（项目记忆）：
    - cam_report.json 是 7 阶段链路的最终产物，不触及物理机床
    - 实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字
    - cam_validation_required 始终 True（项目记忆硬约束，不可关闭）
    - SUCCEEDED 状态禁止删除（链路最终产物，需保留供审计追溯）
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except CamValidationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    if task.status != CamValidationTaskStatus.REVIEWED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许确认，仅 {CamValidationTaskStatus.REVIEWED.value} 状态可确认"),
            suggestion="请先完成所有特征的审核（状态变为 reviewed）后再确认导出",
        )

    try:
        pipeline = _get_pipeline()
        result = pipeline.confirm_task(task_id=task_id, reviewer=reviewer)
    except ReviewError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except CamValidationPipelineError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="cam_validation.confirm_task")
        logger.error(
            "确认任务失败 task_id=%s | error_id=%s | exc=%s",
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
    try:
        task_after = store.get_task(task_id)
    except CamValidationError:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="确认后任务丢失，请检查任务存储",
        )

    report_download_url = f"/api/v1/cam-validation/tasks/{task_id}/report/download"
    internal_report_download_url = f"/api/v1/cam-validation/tasks/{task_id}/internal_report/download"

    return success(
        data={
            "task_id": task_after.task_id,
            "status": task_after.status,
            "controller_type": task_after.controller_type,
            "material_name": task_after.material_name,
            "total_features": task_after.total_features,
            "passed_features": task_after.passed_features,
            "failed_features": task_after.failed_features,
            "cam_backend_used": task_after.cam_backend_used,
            "cam_report_path": result.cam_report_path or "",
            "internal_report_path": result.internal_report_path or "",
            "report_download_url": report_download_url,
            "internal_report_download_url": internal_report_download_url,
            "cam_validation_required": task_after.cam_validation_required,
            "cam_disclaimer": _disclaimer_dict(task=task_after, cam_report_exported=True),
        },
        message=(
            f"CAM 校验报告已导出 cam_report={result.cam_report_path}。"
            "可通过 report_download_url 下载 CAM 校验报告 JSON（链路最终产物），"
            "通过 internal_report_download_url 下载内部预校验详细报告（供前端可视化）。"
            "注意：实际加工必须经持证操作员 + 导师签字 + 保险。"
        ),
    )


async def download_cam_report(task_id: str) -> FileResponse | JSONResponse:
    """下载 CAM 校验报告 JSON（链路最终产物，供审计追溯）。

    仅 SUCCEEDED 状态可下载。

    文件结构（cam_report.json）：
    - task_id / task_status / exported_at / reviewer
    - controller_type / material_name / gcode_total_lines
    - source_gcode_report_path / source_gcode_file_path
    - prediction_method / pending_calibration
    - cam_validation_required（始终 True）
    - cam_backend_requested / cam_backend_used / cam_backend_fallback_reason
    - total_features / passed_features / failed_features
    - feature_validation_results（每条特征的双层校验结论 + 审核状态）
    - industrial_hard_gates_note（工业硬门槛告知）

    工业硬约束（项目记忆）：
    - cam_report.json 是 7 阶段链路最终产物，不触及物理机床
    - 实际加工必须经持证操作员 + 导师签字 + 保险
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except CamValidationError:
        # 安全约束：不回显 task_id
        return build_not_found_response()

    if task.status != CamValidationTaskStatus.SUCCEEDED.value:
        return JSONResponse(
            status_code=400,
            content=error(
                code=ErrorCode.INVALID_REQUEST,
                message="任务未完成审核，无法下载 CAM 校验报告。请先调用 POST /tasks/{task_id}/confirm。",
            ),
        )

    return build_file_download_response(
        task.cam_report_path,
        media_type="application/json",
        filename=f"{task_id}_cam_report.json",
    )


async def download_internal_report(task_id: str) -> FileResponse | JSONResponse:
    """下载内部预校验详细报告 JSON（供前端可视化）。

    仅 SUCCEEDED 状态可下载。

    文件结构（internal_report.json）：
    - task_id / task_status / exported_at
    - gcode_text（完整 G 代码文本，供前端高亮显示）
    - gcode_total_lines
    - toolpath_segments（ToolpathParser 解析的刀轨段）
    - collision_events（CollisionDetector 检测的碰撞事件，含 AABB 详情）
    - feature_internal_reports（每条特征的内部预校验详情）
    - workspace_limits / stock_model（毛坯尺寸 + 工作空间限制）

    工业硬约束（项目记忆）：
    - 内部预校验是 AABB 包围盒级别快速预筛，不可替代 CAM 软件二次校验
    - internal_report.json 仅用于调试 / 可视化，不作为最终产物
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except CamValidationError:
        # 安全约束：不回显 task_id
        return build_not_found_response()

    if task.status != CamValidationTaskStatus.SUCCEEDED.value:
        return JSONResponse(
            status_code=400,
            content=error(
                code=ErrorCode.INVALID_REQUEST,
                message="任务未完成审核，无法下载内部预校验报告。请先调用 POST /tasks/{task_id}/confirm。",
            ),
        )

    return build_file_download_response(
        task.internal_report_path,
        media_type="application/json",
        filename=f"{task_id}_internal_report.json",
    )


async def delete_task(task_id: str) -> dict[str, Any]:
    """取消或删除 CAM 校验任务。

    - 非终态任务：将状态置为 CANCELLED 后删除任务元信息
    - 终态任务（FAILED / CANCELLED / TIMEOUT）：直接删除任务元信息
    - SUCCEEDED 状态任务禁止删除（项目记忆硬约束：cam_report.json 是链路最终产物，
      需保留供审计追溯）
    - allow_delete_succeeded 强制 False，不可由环境变量开启

    注意：cam_report.json / internal_report.json 与 workspace 目录不会被自动删除，
    避免误删下游链路已引用的资源。
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except CamValidationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    # SUCCEEDED 状态的任务禁止删除（避免误删链路最终产物）
    if task.status == CamValidationTaskStatus.SUCCEEDED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务 {task_id} 已 SUCCEEDED，禁止删除。cam_report.json 是链路最终产物，需保留供审计追溯。"),
            suggestion="如确需删除，请先手动清理审计引用，再删除任务",
        )

    # 非终态任务先取消（修改状态后持久化）
    terminal_states = {
        CamValidationTaskStatus.FAILED.value,
        CamValidationTaskStatus.CANCELLED.value,
        CamValidationTaskStatus.TIMEOUT.value,
    }
    if task.status not in terminal_states:
        task.status = CamValidationTaskStatus.CANCELLED.value
        try:
            store.update_task(task)
        except Exception as e:
            safe = safe_error_message(e, context="cam_validation.delete_task.cancel")
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

    # 删除任务
    try:
        pipeline = _get_pipeline()
        pipeline.delete_task(task_id)
    except ReviewError as e:
        # SUCCEEDED 禁删硬约束在 pipeline 层兜底（API 层已先检查）
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except CamValidationPipelineError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="cam_validation.delete_task")
        logger.error(
            "删除任务失败 task_id=%s | error_id=%s | exc=%s",
            task_id,
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
            "task_id": task_id,
            "deleted": True,
            "note": (
                "任务元信息已删除，cam_report.json / internal_report.json "
                "与 workspace 目录未自动清理，避免误删审计引用资源。"
            ),
        },
        message=f"任务 {task_id} 已删除",
    )
