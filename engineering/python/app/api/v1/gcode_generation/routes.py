"""G 代码生成接入模块 API 路由实现（阶段 6）。

数据流：阶段 5 ChatterReport JSON + 阶段 3 OperationPlan JSON
    → ChatterReportLoader.load() 加载特征稳定性 + 安全裕度
    → GeneratorAdapter.adapt() 封装现有 GCodeGenerator 生成基础 G 代码
    → stable == False 的特征使 is_valid == False → FAILED（强制回阶段 5）
    → 工程师审核每个特征 G 代码段（confirmed / rejected / edited）
    → 全部审核完毕 → REVIEWED → confirm_task → SUCCEEDED
    → 导出 G 代码文件 + 审核记录 JSON（供阶段 7 CAM 校验使用）

工业硬约束（项目记忆）：
- 系统定位「工程师助手」，非「全自动 G 代码生成器」
- 生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机床
- 系统绝不直接接口 CNC 控制器，G 代码文件需手动加载到 CAM 软件
- cam_validation_required 始终 True（项目记忆硬约束，不可关闭）
- SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）
- allow_delete_succeeded 强制 False（不可由环境变量开启）
- 复用现有 GCodeGenerator（212 个测试用例覆盖），不重写
- K_s（cutting_force_coeff）直接来自阶段 4，不二次拟合（阶段 6 不涉及拟合）
- HRC52 pending_calibration 标注由阶段 5 完成，阶段 6 仅继承
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse

from app.api.v1.gcode_generation.schemas import (
    ConfirmTaskResponse,
    ReviewRequest,
    ReviewResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskResultResponse,
    TaskStatusResponse,
)
from .service import (
    get_precision_info as get_precision_info_service,
    create_task as create_task_service,
    run_task as run_task_service,
    get_task_status as get_task_status_service,
    list_tasks as list_tasks_service,
    get_task_result as get_task_result_service,
    review_feature as review_feature_service,
    confirm_task as confirm_task_service,
    download_gcode as download_gcode_service,
    download_report as download_report_service,
    delete_task as delete_task_service,
)

from app.auth.permissions import require_permission


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/gcode-generation",
    tags=["G-Code Generation (Engineer-Assisted CAM Handoff)"],
    dependencies=[Depends(require_permission("gcode_generation:read"))],
)

# 端点实现
# Pydantic 请求 / 响应模型已迁移至 ``app.api.v1.gcode_generation.schemas``，
# 与同级 stage 模块（chatter_prediction / cutting_parameters 等）约定一致。


@router.get("/precision_info")
async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息、控制器类型与工业硬门槛（不创建任务）。

    前端在用户进入 G 代码生成页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d → feature_extraction → parametric_geometry
      → cutting_parameters → chatter_prediction）
    - 支持的 CNC 控制器类型
    - 工业生产硬门槛（CAM 二次校验强制 + 操作员资质 + 导师签字）
    - 工程师审核流程说明
    """
    return await get_precision_info_service()


@router.post(
    "/tasks",
    response_model=TaskCreateResponse,
    summary="创建 G 代码生成任务",
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
    return await create_task_service(body)


@router.post(
    "/tasks/{task_id}/run",
    summary="异步触发 G 代码生成流水线执行",
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
    return await run_task_service(task_id)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询任务状态",
)
async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、审核进度、G 代码文件路径、精度告知字段。"""
    return await get_task_status_service(task_id)


@router.get(
    "/tasks",
    summary="列出最近任务",
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
    return await list_tasks_service(limit, status_filter)


@router.get(
    "/tasks/{task_id}/result",
    response_model=TaskResultResponse,
    summary="获取 G 代码生成结果列表 + 审核状态",
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
    return await get_task_result_service(task_id)


@router.post(
    "/tasks/{task_id}/review",
    response_model=ReviewResponse,
    summary="工程师审核单个特征的 G 代码段",
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
    return await review_feature_service(task_id, feature_id, body)


@router.post(
    "/tasks/{task_id}/confirm",
    response_model=ConfirmTaskResponse,
    summary="确认任务（REVIEWED → SUCCEEDED + 导出 G 代码 + 报告 JSON）",
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
    return await confirm_task_service(task_id, reviewer)


@router.get(
    "/tasks/{task_id}/gcode/download",
    summary="下载 G 代码文件",
    response_model=None,  # 修复：FileResponse|JSONResponse 联合注解无法生成 response model（2026-08-03 安装验证发现）
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
    return await download_gcode_service(task_id)


@router.get(
    "/tasks/{task_id}/report/download",
    summary="下载审核记录 JSON",
    response_model=None,  # 修复：FileResponse|JSONResponse 联合注解无法生成 response model（2026-08-03 安装验证发现）
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
    return await download_report_service(task_id)


@router.delete(
    "/tasks/{task_id}",
    summary="取消/删除任务",
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
    return await delete_task_service(task_id)
