"""颤振预测接入模块 API 路由实现（阶段 5）。

数据流：阶段 4 ChatterParams JSON + material_id
    → ChatterPredictorAdapter 双路径预测：
        路径 A: Tlusty 解析法（compute_stability_limit，工程可用，默认路径）
        路径 B: LTC 神经网络（实验性，chatter_model.pt 不存在时自动回退到路径 A）
        路径 C: 兜底默认值（保守 limit_depth=1.0mm，confidence=0.3）
    → HRC52 材料 pending_calibration 时强制降低置信度（0.8 → 0.5）
    → 工程师审核每个特征的稳定性预测结果（confirmed / rejected / edited）
    → 导出 ChatterReport JSON（供阶段 6 G 代码生成使用）

工业硬约束（项目记忆）：
- 本模块输出 ChatterReport 仅供阶段 6 G 代码生成参考，不可直接用于机床
- 实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字
- 系统定位「工程师助手」，非「全自动颤振预测器」
- K_s（cutting_force_coeff）直接取自阶段 4，不二次拟合（项目记忆硬约束）
- SUCCEEDED 状态禁止删除（阶段 6 G 代码生成可能已引用其 ChatterReport）
- cam_validation_required 始终 True（项目记忆硬约束，不可关闭）
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.v1.chatter_prediction.schemas import (
    ExportChatterReportResponse,
    ReviewRequest,
    ReviewResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskResultResponse,
    TaskStatusResponse,
)
from app.auth.permissions import require_permission


logger = logging.getLogger(__name__)

# 后台任务引用集合（C5 修复：asyncio.create_task 不保存引用会被 GC 回收）
_background_tasks: set = set()


router = APIRouter(
    prefix="/api/v1/chatter_prediction",
    tags=["Chatter Prediction (Engineer-Assisted LTC Integration)"],
    dependencies=[Depends(require_permission("chatter_prediction:read"))],
)

# 端点实现


@router.get("/precision_info")
async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息、LTC 模型可用性与工业硬门槛（不创建任务）。

    前端在用户进入颤振预测页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d mesh + feature_extraction + parametric_geometry + cutting_parameters）
    - LTC 神经网络模型可用性（chatter_model.pt 是否存在）
    - 预测方法说明（解析法工程可用，LTC 实验性）
    - 工业生产硬门槛
    - 工程师审核流程说明
    """
    return await get_precision_info_service()


@router.post(
    "/tasks",
    response_model=TaskCreateResponse,
    summary="创建颤振预测任务",
)
async def create_task(body: TaskCreateRequest) -> dict[str, Any]:
    """创建颤振预测任务。

    创建后状态为 PENDING，需调用 POST /tasks/{task_id}/run 触发执行。

    输入解析优先级：
    1. 若 source_cutting_parameters_task_id 对应的阶段 4 任务已 SUCCEEDED，
       自动读取 chatter_params_path / material_id / mesh_calibrated。
    2. 若上游任务不存在或未完成，必须显式提供 chatter_params_path + material_id。
    3. mesh_calibrated 显式提供时优先采用，否则从上游任务读取（读不到默认 False）。
    """
    return await create_task_service(body)


@router.post(
    "/tasks/{task_id}/run",
    summary="异步触发颤振预测流水线执行",
)
async def run_task(task_id: str) -> dict[str, Any]:
    """异步触发颤振预测流水线执行。

    执行流程：
    1. 加载阶段 4 ChatterParams JSON → 特征列表
    2. ChatterPredictorAdapter.predict_feature() 对每个特征执行双路径预测：
       - 默认走 Tlusty 解析法（工程可用）
       - LTC 神经网络路径仅在 chatter_model.pt 存在时尝试（实验性）
       - HRC52 材料 pending_calibration 时强制降低置信度
    3. 状态置为 PREDICTED（等待工程师审核）

    仅 PENDING / FAILED 状态可触发执行（FAILED 允许重试）。
    """
    return await run_task_service(task_id)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询任务状态",
)
async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、审核进度、ChatterReport 路径、精度告知字段。"""
    return await get_task_status_service(task_id)


@router.get(
    "/tasks",
    summary="列出最近任务",
)
async def list_tasks(limit: int = 20) -> dict[str, Any]:
    """列出最近的颤振预测任务（按创建时间倒序）。"""
    return await list_tasks_service(limit)


@router.get(
    "/tasks/{task_id}/result",
    response_model=TaskResultResponse,
    summary="获取颤振预测结果列表 + 审核状态",
)
async def get_task_result(task_id: str) -> dict[str, Any]:
    """获取任务结果摘要与完整预测结果列表（含审核状态）。

    仅当任务状态为 PREDICTED / REVIEWED / SUCCEEDED 时可调用。
    返回的每条预测结果包含：
    - feature_id / feature_type / material_id
    - spindle_rpm / axial_depth_mm / limit_depth_mm / stable / stability_margin
    - method（analytical / neural_network / fallback）/ ltc_active / confidence
    - review_status（pending / confirmed / rejected / edited）
    - effective_params（合并 edited_params 后的生效参数）
    - warnings（预测时生成的告警，如 HRC52 pending_calibration / 切深超极限）
    """
    return await get_task_result_service(task_id)


@router.post(
    "/tasks/{task_id}/review",
    response_model=ReviewResponse,
    summary="工程师审核单个特征的颤振预测结果",
)
async def review_result(
    task_id: str,
    feature_id: str,
    body: ReviewRequest,
) -> dict[str, Any]:
    """工程师审核单个特征的颤振预测结果。

    本端点是 human-in-the-loop 的核心入口（项目记忆硬约束：
    系统定位「工程师助手」，非「全自动颤振预测器」）。

    审核动作：
    - ``confirmed``: 预测结果（稳定性判断 + 极限切深）无误
    - ``rejected``:  拒绝该特征（不进入最终 ChatterReport）
    - ``edited``:    参数需修正，需同时提供 ``edited_params``
        可编辑字段：limit_depth_mm / axial_depth_mm / stable（0/1）

    当所有特征都被审核（confirmed / rejected / edited）后，
    任务状态自动从 PREDICTED 转为 REVIEWED，
    随后可调用 POST /tasks/{task_id}/export 导出 ChatterReport。

    请求体中 ``feature_id`` 作为查询参数传入，便于 RESTful 路径表达。
    """
    return await review_result_service(task_id, feature_id, body)


@router.post(
    "/tasks/{task_id}/export",
    response_model=ExportChatterReportResponse,
    summary="导出 ChatterReport JSON（供阶段 6 G 代码生成）",
)
async def export_chatter_report(task_id: str) -> dict[str, Any]:
    """导出 ChatterReport JSON 文件供阶段 6 G 代码生成使用。

    本端点在所有特征审核完毕（状态 REVIEWED）后调用：
    - 仅导出 confirmed + edited 的特征预测结果（rejected 排除）
    - 写入 {task_id}_chatter_report.json
    - 状态置为 SUCCEEDED

    导出后，可通过 GET /tasks/{task_id}/chatter_report/download 下载 JSON 文件。

    工业硬约束（项目记忆）：
    - 导出的 ChatterReport 仅供阶段 6 G 代码生成参考，不可直接用于机床
    - 实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字
    - 极限切深为理论值，实际加工必须留 20% 安全裕度
    - cam_validation_required 始终 True（项目记忆硬约束，不可关闭）
    """
    return await export_chatter_report_service(task_id)


@router.get(
    "/tasks/{task_id}/chatter_report/download",
    summary="下载 ChatterReport JSON 文件",
)
async def download_chatter_report(task_id: str) -> FileResponse:
    """下载 ChatterReport JSON 文件（供阶段 6 G 代码生成读取）。

    仅 SUCCEEDED 状态可下载。

    文件结构：
    - task_id / source_cutting_parameters_task_id / material_id
    - cam_validation_required: 始终 True（项目记忆硬约束）
    - method_statistics: {analytical, neural_network, fallback}
    - feature_results: list[dict]
      每条含 feature_id / feature_type / spindle_rpm / axial_depth_mm / limit_depth_mm
      / stable / stability_margin / method / ltc_active / confidence / review_status
      / effective_params / source_cutting_params_task_id / cutting_force_coeff
    - industrial_hard_gates_note: 强制告知工业硬约束
    """
    return await download_chatter_report_service(task_id)


@router.delete(
    "/tasks/{task_id}",
    summary="取消/删除任务",
)
async def delete_task(task_id: str) -> dict[str, Any]:
    """取消或删除颤振预测任务。

    - 非终态任务：将状态置为 CANCELLED 后删除任务元信息
    - 终态任务（FAILED / CANCELLED）：直接删除任务元信息
    - SUCCEEDED 状态任务禁止删除（项目记忆硬约束：阶段 6 G 代码生成可能已引用其 ChatterReport）

    注意：ChatterReport JSON 文件与 workspace 目录不会被自动删除，
    避免误删下游链路已引用的资源。
    """
    return await delete_task_service(task_id)


from .service import (  # noqa: E402
    get_precision_info as get_precision_info_service,
    create_task as create_task_service,
    run_task as run_task_service,
    get_task_status as get_task_status_service,
    list_tasks as list_tasks_service,
    get_task_result as get_task_result_service,
    review_result as review_result_service,
    export_chatter_report as export_chatter_report_service,
    download_chatter_report as download_chatter_report_service,
    delete_task as delete_task_service,
)
