"""G 代码生成路由业务逻辑（从 routes.py 抽取，S4b）。

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
from app.api.v1.gcode_generation._helpers import (
    _disclaimer_dict,
    _get_pipeline,
    _resolve_upstream_chatter_report,
    _resolve_upstream_operation_plan,
)
from app.api.v1.gcode_generation.schemas import (
    ReviewRequest,
    TaskCreateRequest,
)
from app.config import config
from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.gcode_generation import (
    GCodeGenerationError,
    GCodeGenerationPipelineError,
    GCodeGenerationTaskStatus,
    GCodeReviewError,
    GCodeReviewStatus,
    ReviewError,
    get_file_extension,
    get_task_store,
)

logger = logging.getLogger(__name__)


async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息、控制器类型与工业硬门槛（不创建任务）。

    前端在用户进入 G 代码生成页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d → feature_extraction → parametric_geometry
      → cutting_parameters → chatter_prediction）
    - 支持的 CNC 控制器类型
    - 工业生产硬门槛（CAM 二次校验强制 + 操作员资质 + 导师签字）
    - 工程师审核流程说明
    """
    return success(
        data={
            "current_tier": config.gcode_generation.precision_tier,
            "available_tiers": {
                "coarse": "粗加工档位，大切深 + 低精度，常配合 roughing 使用",
                "standard": "标准档位，平衡切深与精度（默认）",
                "high": "精加工档位，小切深 + 高精度，常配合 finishing 使用",
                "mesh_calibrated": "网格标定档位（上游 mesh 已做尺度归一化，最高精度）",
            },
            "module_parameters": {
                "default_controller_type": config.gcode_generation.default_controller_type,
                "default_mesh_calibrated": config.gcode_generation.default_mesh_calibrated,
                "allow_delete_succeeded": config.gcode_generation.allow_delete_succeeded,
                "cam_validation_required": config.gcode_generation.cam_validation_required,
                "max_concurrent": config.gcode_generation.max_concurrent,
                "task_timeout_seconds": config.gcode_generation.task_timeout_seconds,
            },
            "supported_controllers": {
                "fanuc_0i": "Fanuc 0i-MB（.nc 扩展名，国内主流三轴铣）",
                "siemens_840d": "Siemens 840D（.mpf 扩展名，五轴高端）",
                "heidenhain_tnc": "Heidenhain TNC 640（.h 扩展名，模具五轴）",
                "xmachine_xm100": "xMachine XM100（.nc 扩展名，国产替代）",
            },
            "industrial_hard_gates": [
                "系统定位「工程师助手」，非「全自动 G 代码生成器」，最终决策权在工程师",
                "生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机床",
                "系统绝不直接接口 CNC 控制器，G 代码文件需手动加载到 CAM 软件",
                "CAM 二次校验强制：cam_validation_required 始终 True，不可由环境变量关闭",
                "stable == False 的特征禁止生成 G 代码，强制回阶段 5 降低切深或主轴转速",
                "极限切深为理论值，实际加工必须留 20% 安全裕度（SAFETY_MARGIN_RATIO=0.8）",
                "CNC 机床操作需持证操作员 + 导师签字 + 保险，大一独立项目不可独立完成机床执行",
                "SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）",
                "复用现有 GCodeGenerator（212 个测试用例覆盖），不重写后处理器",
            ],
            "gcode_disclaimer": _disclaimer_dict(),
            "workflow_summary": {
                "step_1": (
                    "POST /tasks 创建任务（输入 ChatterReport 路径 + OperationPlan 路径 + 控制器类型 + 材料名称）"
                ),
                "step_2": "POST /tasks/{task_id}/run 异步触发 G 代码生成流水线",
                "step_3": "GET /tasks/{task_id} 轮询状态（PENDING → RUNNING → GENERATED）",
                "step_4": ("POST /tasks/{task_id}/review?feature_id=... 工程师逐条审核 G 代码段"),
                "step_5": (
                    "POST /tasks/{task_id}/confirm 确认任务（REVIEWED → SUCCEEDED + 导出 G 代码文件 + 报告 JSON）"
                ),
                "step_6": ("GET /tasks/{task_id}/gcode/download 下载 G 代码文件，手动加载到 CAM 软件进行二次校验"),
            },
        },
    )


async def create_task(body: TaskCreateRequest) -> dict[str, Any]:
    """创建 G 代码生成任务。

    创建后状态为 PENDING，需调用 POST /tasks/{task_id}/run 触发执行。

    输入解析优先级：
    1. 若 source_chatter_prediction_task_id 对应的阶段 5 任务已 SUCCEEDED，
       自动读取 chatter_report_path / material_name / prediction_method。
    2. 若 source_parametric_geometry_task_id 对应的阶段 3 任务已 SUCCEEDED，
       自动读取 operation_plan_path。
    3. 若上游任务不存在或未完成，必须显式提供 chatter_report_path + operation_plan_path。

    工业硬约束（项目记忆）：
    - cam_validation_required 始终 True（强制 CAM 二次校验）
    - stable == False 的特征会在流水线执行时触发 FAILED（强制回阶段 5）
    """
    # 从上游阶段 5 任务追溯 ChatterReport 路径
    (
        upstream_chatter_report_path,
        upstream_material_name,
        upstream_prediction_method,
        upstream_pending_calibration,
        upstream_default_controller,
    ) = _resolve_upstream_chatter_report(body.source_chatter_prediction_task_id)

    # 从上游阶段 3 任务追溯 OperationPlan 路径
    upstream_operation_plan_path = _resolve_upstream_operation_plan(body.source_parametric_geometry_task_id)

    # 解析 chatter_report_path（显式 > 上游 > 报错）
    chatter_report_path = body.chatter_report_path or upstream_chatter_report_path
    if not chatter_report_path:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(
                "chatter_report_path 为空且无法从上游阶段 5 任务读取 "
                f"source_chatter_prediction_task_id="
                f"{body.source_chatter_prediction_task_id}"
            ),
            suggestion=(
                "请显式提供 chatter_report_path，或确认上游阶段 5 任务已 SUCCEEDED 且已导出 ChatterReport JSON。"
            ),
        )

    # 校验 ChatterReport JSON 文件存在
    if not Path(chatter_report_path).exists():
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"阶段 5 ChatterReport JSON 不存在 path={chatter_report_path}",
            suggestion="请先在阶段 5 完成审核并导出 ChatterReport JSON。",
        )

    # 解析 operation_plan_path（显式 > 上游 > 报错）
    operation_plan_path = body.operation_plan_path or upstream_operation_plan_path
    if not operation_plan_path:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(
                "operation_plan_path 为空且无法从上游阶段 3 任务读取 "
                f"source_parametric_geometry_task_id="
                f"{body.source_parametric_geometry_task_id}"
            ),
            suggestion=(
                "请显式提供 operation_plan_path，或确认上游阶段 3 任务已 SUCCEEDED 且已导出 OperationPlan JSON。"
            ),
        )

    # 校验 OperationPlan JSON 文件存在
    if not Path(operation_plan_path).exists():
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"阶段 3 OperationPlan JSON 不存在 path={operation_plan_path}",
            suggestion="请先在阶段 3 完成参数化几何建模并导出 OperationPlan JSON。",
        )

    # 解析 material_name（显式 > 上游 > 默认值）
    material_name = body.material_name or upstream_material_name or "45#钢"

    # 解析 controller_type（显式 > 上游 > 配置默认值）
    controller_type = (
        body.controller_type or upstream_default_controller or config.gcode_generation.default_controller_type
    )

    try:
        pipeline = _get_pipeline()
        task = pipeline.create_task(
            source_chatter_report_path=chatter_report_path,
            source_operation_plan_path=operation_plan_path,
            controller_type=controller_type,
            material_name=material_name,
            program_number=body.program_number,
            safe_z=body.safe_z,
            stock_top_z=body.stock_top_z,
        )
    except Exception as e:
        safe = safe_error_message(e, context="gcode_generation.create_task")
        logger.error(
            "创建任务失败 chatter_report=%s op_plan=%s | error_id=%s | exc=%s",
            chatter_report_path,
            operation_plan_path,
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
            "source_chatter_report_path": task.source_chatter_report_path,
            "source_operation_plan_path": task.source_operation_plan_path,
            "controller_type": task.controller_type,
            "material_name": task.material_name,
            "program_number": task.program_number,
            "safe_z": task.safe_z,
            "stock_top_z": task.stock_top_z,
            "cam_validation_required": task.cam_validation_required,
            "gcode_disclaimer": _disclaimer_dict(task=task),
        },
        message=(f"任务已创建 task_id={task.task_id}，请调用 POST /tasks/{task.task_id}/run 触发执行"),
    )


async def run_task(task_id: str) -> dict[str, Any]:
    """异步触发 G 代码生成流水线执行。

    执行流程：
    1. 加载阶段 5 ChatterReport JSON → 特征稳定性 + 安全裕度
    2. 加载阶段 3 OperationPlan JSON → 操作序列
    3. GeneratorAdapter.adapt() 调用现有 GCodeGenerator.generate() 生成基础 G 代码
    4. 遍历 ChatterReport.feature_results 计算安全裕度（SAFETY_MARGIN_RATIO=0.8）
    5. 若含 unstable 特征 → FAILED（强制回阶段 5 降低切深）
    6. 否则 → GENERATED（等待工程师审核）

    仅 PENDING / FAILED 状态可触发执行（FAILED 允许重试）。
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except GCodeGenerationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    if task.status not in (
        GCodeGenerationTaskStatus.PENDING.value,
        GCodeGenerationTaskStatus.FAILED.value,
    ):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态不允许执行当前操作 status={task.status}。仅 PENDING / FAILED 状态可触发执行。"),
        )

    # 重试场景：清空错误信息
    if task.status == GCodeGenerationTaskStatus.FAILED.value:
        task.error_message = ""
        task.errors = []
        task.warnings = []
        store.update_task(task)

    pipeline = _get_pipeline()
    _spawn(pipeline.run_pipeline(task_id))

    return success(
        data={
            "task_id": task_id,
            "status": GCodeGenerationTaskStatus.RUNNING.value,
            "message": (
                "任务已开始执行，请轮询 GET /tasks/{task_id} 获取状态。"
                "执行完成后状态将变为 GENERATED（含 unstable 特征时为 FAILED），"
                "等待工程师审核 G 代码段。"
            ),
        },
        message="任务已开始执行",
    )


async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、审核进度、G 代码文件路径、精度告知字段。"""
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except GCodeGenerationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    # 统计审核进度
    pending_review_count = sum(
        1 for r in task.feature_gcode_results if r.review_status == GCodeReviewStatus.PENDING.value
    )
    confirmed_count = sum(1 for r in task.feature_gcode_results if r.review_status == GCodeReviewStatus.CONFIRMED.value)
    rejected_count = sum(1 for r in task.feature_gcode_results if r.review_status == GCodeReviewStatus.REJECTED.value)
    edited_count = sum(1 for r in task.feature_gcode_results if r.review_status == GCodeReviewStatus.EDITED.value)

    gcode_file_exported = bool(task.gcode_file_path)

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "source_chatter_report_path": task.source_chatter_report_path,
            "source_operation_plan_path": task.source_operation_plan_path,
            "controller_type": task.controller_type,
            "material_name": task.material_name,
            "program_number": task.program_number,
            "safe_z": task.safe_z,
            "stock_top_z": task.stock_top_z,
            "feature_count": len(task.feature_gcode_results),
            "stable_features": task.stable_features,
            "unstable_features": task.unstable_features,
            "pending_calibration": task.pending_calibration,
            "prediction_method": task.prediction_method,
            "pending_review_count": pending_review_count,
            "confirmed_count": confirmed_count,
            "rejected_count": rejected_count,
            "edited_count": edited_count,
            "cam_validation_required": task.cam_validation_required,
            "gcode_file_path": task.gcode_file_path,
            "gcode_report_path": task.gcode_report_path,
            "error_message": task.error_message,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "reviewed_by": task.reviewed_by,
            "reviewed_at": task.reviewed_at,
            "warnings": list(task.warnings),
            "errors": list(task.errors),
            "gcode_disclaimer": _disclaimer_dict(task=task, gcode_file_exported=gcode_file_exported),
        },
    )


async def list_tasks(
    limit: int = 20,
    status_filter: str = "",
) -> dict[str, Any]:
    """列出最近的 G 代码生成任务（按创建时间倒序）。

    Args:
        limit: 返回数量上限（1-100）
        status_filter: 可选状态过滤（pending / running / generated / reviewed /
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
                    "program_number": t.program_number,
                    "feature_count": len(t.feature_gcode_results),
                    "stable_features": t.stable_features,
                    "unstable_features": t.unstable_features,
                    "pending_calibration": t.pending_calibration,
                    "prediction_method": t.prediction_method,
                    "gcode_file_path": t.gcode_file_path,
                    "gcode_report_path": t.gcode_report_path,
                    "started_at": t.started_at,
                    "completed_at": t.completed_at,
                }
                for t in tasks
            ],
            "total": len(tasks),
        },
    )


async def get_task_result(task_id: str) -> dict[str, Any]:
    """获取任务结果摘要与完整 G 代码段列表（含审核状态）。

    仅当任务状态为 GENERATED / REVIEWED / SUCCEEDED 时可调用。
    返回的每条 G 代码段包含：
    - feature_id / feature_type / material_id
    - spindle_rpm / axial_depth_mm / limit_depth_mm / stable / safety_margin_ratio
    - gcode_lines / line_range（在最终程序中的行号范围）
    - warning（安全裕度警告，若 axial > 0.8 × limit）
    - review_status（pending / confirmed / rejected / edited）
    - edited_params / effective_params（合并 edited_params 后的生效参数）
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except GCodeGenerationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    allowed_states = {
        GCodeGenerationTaskStatus.GENERATED.value,
        GCodeGenerationTaskStatus.REVIEWED.value,
        GCodeGenerationTaskStatus.SUCCEEDED.value,
        GCodeGenerationTaskStatus.FAILED.value,  # FAILED 也允许查看（含 unstable 特征的失败结果）
    }
    if task.status not in allowed_states:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许获取结果，仅 {sorted(allowed_states)} 状态可获取。"),
            suggestion="请等待状态变为 generated 后再调用此端点",
        )

    gcode_file_exported = bool(task.gcode_file_path)

    feature_results_data = [
        {
            "feature_id": r.feature_id,
            "feature_type": r.feature_type,
            "material_id": r.material_id,
            "spindle_rpm": r.spindle_rpm,
            "axial_depth_mm": r.axial_depth_mm,
            "limit_depth_mm": r.limit_depth_mm,
            "stable": r.stable,
            "safety_margin_ratio": r.safety_margin_ratio,
            "gcode_lines": list(r.gcode_lines),
            "line_range": list(r.line_range),
            "warning": r.warning,
            "review_status": r.review_status,
            "edited_params": dict(r.edited_params),
            "effective_params": r.effective_result,
        }
        for r in task.feature_gcode_results
    ]

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "controller_type": task.controller_type,
            "material_name": task.material_name,
            "program_number": task.program_number,
            "total_features": task.total_features,
            "stable_features": task.stable_features,
            "unstable_features": task.unstable_features,
            "pending_calibration": task.pending_calibration,
            "prediction_method": task.prediction_method,
            "cam_validation_required": task.cam_validation_required,
            "gcode_file_path": task.gcode_file_path or None,
            "gcode_report_path": task.gcode_report_path or None,
            "error_message": task.error_message or None,
            "feature_results": feature_results_data,
            "gcode_disclaimer": _disclaimer_dict(task=task, gcode_file_exported=gcode_file_exported),
        },
    )


async def review_feature(
    task_id: str,
    feature_id: str,
    body: ReviewRequest,
) -> dict[str, Any]:
    """工程师审核单个特征的 G 代码段。

    本端点是 human-in-the-loop 的核心入口（项目记忆硬约束：
    系统定位「工程师助手」，非「全自动 G 代码生成器」）。

    审核动作：
    - ``confirmed``: G 代码段无误（含安全裕度判断）
    - ``rejected``:  拒绝该特征（不进入最终 G 代码文件）
    - ``edited``:    参数需修正，需同时提供 ``edited_params``
        可编辑字段：axial_depth_mm / limit_depth_mm / stable（bool）

    当所有特征都被审核（confirmed / rejected / edited）后，
    任务状态自动从 GENERATED 转为 REVIEWED，
    随后可调用 POST /tasks/{task_id}/confirm 导出 G 代码文件。

    请求体中 ``feature_id`` 作为查询参数传入，便于 RESTful 路径表达。

    Note:
        edited 仅记录修改意图，不触发 G 代码重新生成。
        阶段 7 CAM 校验会读取 edited_params 作为工程师修改建议。
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except GCodeGenerationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    if task.status != GCodeGenerationTaskStatus.GENERATED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许审核，仅 {GCodeGenerationTaskStatus.GENERATED.value} 状态可审核"),
            suggestion="请等待流水线执行完成（状态变为 generated）后再审核",
        )

    # 校验 action
    valid_actions = {
        GCodeReviewStatus.CONFIRMED.value,
        GCodeReviewStatus.REJECTED.value,
        GCodeReviewStatus.EDITED.value,
    }
    if body.action not in valid_actions:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"非法 action: {body.action}，应为 {sorted(valid_actions)}",
        )

    # edited 动作必须提供 edited_params
    if body.action == GCodeReviewStatus.EDITED.value and not body.edited_params:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="action=edited 时必须提供 edited_params",
            suggestion=("请提供编辑后的参数（字段可为 axial_depth_mm / limit_depth_mm / stable（bool）的子集）"),
        )

    try:
        pipeline = _get_pipeline()
        reviewed_result = pipeline.review_feature(
            task_id=task_id,
            feature_id=feature_id,
            review_status=body.action,
            reviewed_by=body.reviewed_by,
            edited_params=body.edited_params,
            engineer_notes=body.engineer_notes,
        )
    except GCodeReviewError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="gcode_generation.review_feature")
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

    # 重新查询任务状态（review_feature 内部可能已将状态置为 REVIEWED）
    try:
        task_after = store.get_task(task_id)
    except GCodeGenerationError:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="审核后任务丢失，请检查任务存储",
        )

    all_reviewed = all(r.review_status != GCodeReviewStatus.PENDING.value for r in task_after.feature_gcode_results)

    return success(
        data={
            "task_id": task_id,
            "feature_id": reviewed_result.feature_id,
            "feature_type": reviewed_result.feature_type,
            "review_status": reviewed_result.review_status,
            "effective_params": reviewed_result.effective_result,
            "all_reviewed": all_reviewed,
            "task_status": task_after.status,
            "gcode_disclaimer": _disclaimer_dict(task=task_after),
        },
        message=(
            f"特征 {feature_id} 已审核（action={body.action}）。"
            + (
                " 全部特征已审核完毕，可调用 POST /tasks/{task_id}/confirm 导出 G 代码文件。"
                if all_reviewed
                else " 仍有特征待审核。"
            )
        ),
    )


async def confirm_task(
    task_id: str,
    reviewer: str = "engineer",
) -> dict[str, Any]:
    """确认任务并导出 G 代码文件 + 审核记录 JSON。

    本端点在所有特征审核完毕（状态 REVIEWED）后调用：
    - 仅导出 confirmed + edited 的特征 G 代码段（rejected 排除）
    - 写入 G 代码文件至 {workspace_dir}/{task_id}.{ext}
      （ext 由 controller_type 决定：.nc / .mpf / .h）
    - 写入审核记录 JSON 至 {workspace_dir}/{task_id}.report.json（供阶段 7 CAM 校验读取）
    - 状态置为 SUCCEEDED

    导出后，可通过 GET /tasks/{task_id}/gcode/download 下载 G 代码文件，
    通过 GET /tasks/{task_id}/report/download 下载报告 JSON。

    工业硬约束（项目记忆）：
    - 导出的 G 代码仅供阶段 7 CAM 校验参考，不可直接用于机床
    - 实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字
    - cam_validation_required 始终 True（项目记忆硬约束，不可关闭）
    - SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except GCodeGenerationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    if task.status != GCodeGenerationTaskStatus.REVIEWED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许确认，仅 {GCodeGenerationTaskStatus.REVIEWED.value} 状态可确认"),
            suggestion="请先完成所有特征的审核（状态变为 reviewed）后再确认导出",
        )

    try:
        pipeline = _get_pipeline()
        result = pipeline.confirm_task(task_id=task_id, reviewer=reviewer)
    except GCodeReviewError as e:
        # 无可导出特征（全部 rejected）
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except GCodeGenerationPipelineError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="gcode_generation.confirm_task")
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
    except GCodeGenerationError:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="确认后任务丢失，请检查任务存储",
        )

    exported_features = sum(
        1
        for r in task_after.feature_gcode_results
        if r.review_status
        in (
            GCodeReviewStatus.CONFIRMED.value,
            GCodeReviewStatus.EDITED.value,
        )
    )

    download_url = f"/api/v1/gcode-generation/tasks/{task_id}/gcode/download"
    report_download_url = f"/api/v1/gcode-generation/tasks/{task_id}/report/download"

    return success(
        data={
            "task_id": task_after.task_id,
            "status": task_after.status,
            "controller_type": task_after.controller_type,
            "material_name": task_after.material_name,
            "program_number": task_after.program_number,
            "total_features": task_after.total_features,
            "exported_features": exported_features,
            "gcode_file_path": result.gcode_file_path or "",
            "gcode_report_path": result.gcode_report_path or "",
            "download_url": download_url,
            "report_download_url": report_download_url,
            "cam_validation_required": task_after.cam_validation_required,
            "gcode_disclaimer": _disclaimer_dict(task=task_after, gcode_file_exported=True),
        },
        message=(
            f"G 代码已导出 gcode_file={result.gcode_file_path}。"
            "可通过 download_url 下载 G 代码文件，并手动加载到 CAM 软件（NX/PowerMill/PyCAM）"
            "进行二次校验后上机床。注意：实际加工必须经持证操作员 + 导师签字。"
        ),
    )


async def download_gcode(task_id: str) -> FileResponse | JSONResponse:
    """下载 G 代码文件（供阶段 7 CAM 校验加载）。

    仅 SUCCEEDED 状态可下载。

    文件扩展名由 controller_type 决定：
    - fanuc_0i / xmachine_xm100 → .nc
    - siemens_840d → .mpf
    - heidenhain_tnc → .h

    工业硬约束（项目记忆）：
    - 下载的 G 代码必须经 CAM 软件二次校验后方可上机床
    - 系统绝不直接接口 CNC 控制器，G 代码文件需手动加载到 CAM 软件
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except GCodeGenerationError:
        # 安全约束：不回显 task_id
        return build_not_found_response()

    if task.status != GCodeGenerationTaskStatus.SUCCEEDED.value:
        return JSONResponse(
            status_code=400,
            content=error(
                code=ErrorCode.INVALID_REQUEST,
                message="任务未完成审核，无法下载 G 代码。请先调用 POST /tasks/{task_id}/confirm。",
            ),
        )

    ext = get_file_extension(task.controller_type)
    filename = f"{task_id}_gcode{ext}"

    return build_file_download_response(
        task.gcode_file_path,
        media_type="application/octet-stream",
        filename=filename,
    )


async def download_report(task_id: str) -> FileResponse | JSONResponse:
    """下载审核记录 JSON（供阶段 7 CAM 校验读取）。

    仅 SUCCEEDED 状态可下载。

    文件结构：
    - task_id / task_status / exported_at / reviewer
    - controller_type / material_name / program_number
    - source_chatter_report_path / source_operation_plan_path
    - prediction_method / pending_calibration
    - cam_validation_required（始终 True）
    - gcode_file_path / gcode_total_lines
    - feature_results（每条特征的 G 代码段 + 审核状态 + edited_params）
    - industrial_hard_gates_note（工业硬门槛告知）
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except GCodeGenerationError:
        # 安全约束：不回显 task_id
        return build_not_found_response()

    if task.status != GCodeGenerationTaskStatus.SUCCEEDED.value:
        return JSONResponse(
            status_code=400,
            content=error(
                code=ErrorCode.INVALID_REQUEST,
                message="任务未完成审核，无法下载报告。请先调用 POST /tasks/{task_id}/confirm。",
            ),
        )

    return build_file_download_response(
        task.gcode_report_path,
        media_type="application/json",
        filename=f"{task_id}_report.json",
    )


async def delete_task(task_id: str) -> dict[str, Any]:
    """取消或删除 G 代码生成任务。

    - 非终态任务：将状态置为 CANCELLED 后删除任务元信息
    - 终态任务（FAILED / CANCELLED）：直接删除任务元信息
    - SUCCEEDED 状态任务禁止删除（项目记忆硬约束：阶段 7 CAM 校验可能已引用 G 代码产物）
    - allow_delete_succeeded 强制 False，不可由环境变量开启

    注意：G 代码文件与 workspace 目录不会被自动删除，
    避免误删下游链路已引用的资源。
    """
    store = get_task_store()
    try:
        task = store.get_task(task_id)
    except GCodeGenerationError:
        # 安全约束：不回显 task_id 以防止枚举攻击
        return error(
            code=ErrorCode.NOT_FOUND,
            message="任务不存在或已被删除",
        )

    # SUCCEEDED 状态的任务禁止删除（避免误删阶段 7 已引用的 G 代码产物）
    if task.status == GCodeGenerationTaskStatus.SUCCEEDED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务 {task_id} 已 SUCCEEDED，禁止删除。G 代码产物可能已被阶段 7 CAM 校验引用。"),
            suggestion="如确需删除，请先手动清理下游引用，再删除任务",
        )

    # 非终态任务先取消（修改状态后持久化）
    terminal_states = {
        GCodeGenerationTaskStatus.FAILED.value,
        GCodeGenerationTaskStatus.CANCELLED.value,
        GCodeGenerationTaskStatus.TIMEOUT.value,
    }
    if task.status not in terminal_states:
        task.status = GCodeGenerationTaskStatus.CANCELLED.value
        try:
            store.update_task(task)
        except Exception as e:
            safe = safe_error_message(e, context="gcode_generation.delete_task.cancel")
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
        store.delete_task(task_id, allow_delete_succeeded=False)
    except ReviewError as e:
        # SUCCEEDED 禁删硬约束在 store 层兜底（API 层已先检查）
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="gcode_generation.delete_task")
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
            "note": ("任务元信息已删除，G 代码文件与 workspace 目录未自动清理，避免误删下游链路已引用的资源。"),
        },
        message=f"任务 {task_id} 已删除",
    )
