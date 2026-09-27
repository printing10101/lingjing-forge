r"""CAM 校验 API 路由层（阶段 7）。

prefix: /api/v1/cam-validation
11 个端点（与阶段 5/6 结构对齐）：

| # | 方法 | 路径                                  | 说明 |
|---|------|--------------------------------------|------|
| 1 | GET  | /precision_info                       | 查询可用 CAM 后端 + 工业硬门槛（不创建任务，前端入口展示） |
| 2 | POST | /tasks                                | 创建 CAM 校验任务（PENDING） |
| 3 | POST | /tasks/{task_id}/run                  | 异步触发流水线执行（PENDING → RUNNING → VALIDATED） |
| 4 | GET  | /tasks/{task_id}                       | 查询任务状态（含审核进度 + 校验统计） |
| 5 | GET  | /tasks                                | 列出最近任务（支持状态过滤） |
| 6 | GET  | /tasks/{task_id}/result                | 获取 CAM 校验结果列表 + 审核状态 |
| 7 | POST | /tasks/{task_id}/review                | 工程师审核单个特征校验结果（VALIDATED → REVIEWED） |
| 8 | POST | /tasks/{task_id}/confirm               | 确认任务（REVIEWED → SUCCEEDED + 导出 cam_report + internal_report JSON） |
| 9 | GET  | /tasks/{task_id}/report/download       | 下载 CAM 校验报告 JSON（FileResponse，仅 SUCCEEDED 可下载） |
| 10| GET  | /tasks/{task_id}/internal_report/download | 下载内部预校验详细报告 JSON（FileResponse，可视化用） |
| 11| DELETE | /tasks/{task_id}                     | 取消/删除任务（仅 PENDING / FAILED / TIMEOUT 可删，SUCCEEDED 禁删） |

安全约束（项目记忆硬约束）：
    - 所有 NOT_FOUND 错误响应不回显 task_id，统一返回 "任务不存在或已被删除"，
      防止枚举攻击
    - 下载端点（#9 / #10）在任务不存在 / 状态非 SUCCEEDED / 文件路径为空 /
      文件不存在 时统一返回 JSONResponse(status_code=4xx, content=error(...))，
      不抛 HTTPException，与模块其他端点响应格式保持一致
    - cam_validation_required 始终 True（断言覆盖所有响应）

权限要求（项目记忆硬约束：权限检查动态验证，需要 seed 数据）：
    - GET  /precision_info                     : cam_validation:read
    - POST /tasks                              : cam_validation:create
    - POST /tasks/{task_id}/run                : cam_validation:run
    - GET  /tasks/{task_id}                    : cam_validation:read
    - GET  /tasks                             : cam_validation:read
    - GET  /tasks/{task_id}/result             : cam_validation:read
    - POST /tasks/{task_id}/review             : cam_validation:review
    - POST /tasks/{task_id}/confirm            : cam_validation:confirm
    - GET  /tasks/{task_id}/report/download    : cam_validation:download
    - GET  /tasks/{task_id}/internal_report/download : cam_validation:download
    - DELETE /tasks/{task_id}                  : cam_validation:delete
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse

from app.auth.permissions import require_permission


logger = logging.getLogger(__name__)

from app.api.v1.cam_validation._schemas import (
    TaskCreateRequest,
    TaskCreateResponse,
    TaskStatusResponse,
    TaskResultResponse,
    ReviewRequest,
    ReviewResponse,
    ConfirmTaskResponse,
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
    download_cam_report as download_cam_report_service,
    download_internal_report as download_internal_report_service,
    delete_task as delete_task_service,
)


router = APIRouter(
    prefix="/api/v1/cam-validation",
    tags=["CAM Validation (Engineer-Assisted Production Handoff)"],
    dependencies=[Depends(require_permission("cam_validation:read"))],
)

# 请求 / 响应模型


# 端点实现


@router.get("/precision_info")
async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息、可用 CAM 后端与工业硬门槛（不创建任务）。

    前端在用户进入 CAM 校验页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d → ... → gcode_generation）
    - 支持的 CAM 后端策略（5 个）
    - 工业生产硬门槛（CAM 二次校验强制 + 操作员资质 + 导师签字）
    - 工程师审核流程说明
    """
    return await get_precision_info_service()


@router.post(
    "/tasks",
    response_model=TaskCreateResponse,
    summary="创建 CAM 校验任务",
    dependencies=[Depends(require_permission("cam_validation:create"))],
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
    return await create_task_service(body)


@router.post(
    "/tasks/{task_id}/run",
    summary="异步触发 CAM 校验流水线执行",
    dependencies=[Depends(require_permission("cam_validation:run"))],
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
    return await run_task_service(task_id)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询任务状态",
)
async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、审核进度、CAM 校验统计、导出产物路径。"""
    return await get_task_status_service(task_id)


@router.get(
    "/tasks",
    summary="列出最近任务",
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
    return await list_tasks_service(limit, status_filter)


@router.get(
    "/tasks/{task_id}/result",
    response_model=TaskResultResponse,
    summary="获取 CAM 校验结果列表 + 审核状态",
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
    return await get_task_result_service(task_id)


@router.post(
    "/tasks/{task_id}/review",
    response_model=ReviewResponse,
    summary="工程师审核单个特征的 CAM 校验结果",
    dependencies=[Depends(require_permission("cam_validation:review"))],
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
    return await review_feature_service(task_id, feature_id, body)


@router.post(
    "/tasks/{task_id}/confirm",
    response_model=ConfirmTaskResponse,
    summary="确认任务（REVIEWED → SUCCEEDED + 导出 cam_report + internal_report JSON）",
    dependencies=[Depends(require_permission("cam_validation:confirm"))],
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
    return await confirm_task_service(task_id, reviewer)


@router.get(
    "/tasks/{task_id}/report/download",
    summary="下载 CAM 校验报告 JSON",
    dependencies=[Depends(require_permission("cam_validation:download"))],
    response_model=None,  # 修复：FileResponse|JSONResponse 联合注解（2026-08-03 安装验证发现）
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
    return await download_cam_report_service(task_id)


@router.get(
    "/tasks/{task_id}/internal_report/download",
    summary="下载内部预校验详细报告 JSON",
    dependencies=[Depends(require_permission("cam_validation:download"))],
    response_model=None,  # 修复：FileResponse|JSONResponse 联合注解（2026-08-03 安装验证发现）
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
    return await download_internal_report_service(task_id)


@router.delete(
    "/tasks/{task_id}",
    summary="取消/删除任务",
    dependencies=[Depends(require_permission("cam_validation:delete"))],
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
    return await delete_task_service(task_id)
