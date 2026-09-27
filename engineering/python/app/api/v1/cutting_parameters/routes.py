"""切削参数推荐模块 API 路由实现（阶段 4）。

数据流：阶段 3 STEP + 阶段 2 confirmed_features.json + material_id
    → MaterialResolver 查询材料基线
    → CuttingParamRecommender 推荐切削参数
    → 工程师审核（confirmed / rejected / edited）
    → 导出 ChatterParams JSON（供阶段 5 颤振预测）

工业硬约束（项目记忆）：
- 本模块输出 ChatterParams 仅供阶段 5 颤振预测参考，不可直接用于机床
- 实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字
- 系统定位「工程师助手」，非「全自动切削参数生成器」
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.v1.cutting_parameters.schemas import (
    ExportChatterParamsResponse,
    ReviewRequest,
    ReviewResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskResultResponse,
    TaskStatusResponse,
)
from app.auth.permissions import require_permission

from app.cutting_parameters import (
    CuttingParametersPipeline,
)

logger = logging.getLogger(__name__)

# 后台任务引用集合（C5 修复：asyncio.create_task 不保存引用会被 GC 回收）
_background_tasks: set = set()


router = APIRouter(
    prefix="/api/v1/cutting_parameters",
    tags=["Cutting Parameters (Engineer-Assisted Recommendation)"],
    dependencies=[Depends(require_permission("cutting_parameters:read"))],
)

# pipeline 单例（懒加载，避免模块导入期触发材料库 / 推荐器初始化）
_pipeline: CuttingParametersPipeline | None = None

# 端点实现


@router.get("/precision_info")
async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息、材料列表与工业硬门槛（不创建任务）。

    前端在用户进入切削参数页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d mesh + feature_extraction 特征）
    - 可用材料列表（含 HRC52 待校准状态）
    - 工业生产硬门槛
    - 工程师审核流程说明
    """
    return await get_precision_info_service()


@router.post(
    "/tasks",
    response_model=TaskCreateResponse,
    summary="创建切削参数推荐任务",
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
    return await create_task_service(body)


@router.post(
    "/tasks/{task_id}/run",
    summary="异步触发切削参数推荐流水线执行",
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
    return await run_task_service(task_id)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询任务状态",
)
async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、审核进度、ChatterParams 路径、精度告知字段。"""
    return await get_task_status_service(task_id)


@router.get(
    "/tasks",
    summary="列出最近任务",
)
async def list_tasks(limit: int = 20) -> dict[str, Any]:
    """列出最近的切削参数任务（按创建时间倒序）。"""
    return await list_tasks_service(limit)


@router.get(
    "/tasks/{task_id}/result",
    response_model=TaskResultResponse,
    summary="获取推荐参数列表 + 审核状态",
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
    return await get_task_result_service(task_id)


@router.post(
    "/tasks/{task_id}/review",
    response_model=ReviewResponse,
    summary="工程师审核单个特征的切削参数",
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
    return await review_params_service(task_id, feature_id, body)


@router.post(
    "/tasks/{task_id}/export",
    response_model=ExportChatterParamsResponse,
    summary="导出 ChatterParams JSON（供阶段 5 颤振预测）",
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
    return await export_chatter_params_service(task_id)


@router.get(
    "/tasks/{task_id}/chatter_params/download",
    summary="下载 ChatterParams JSON 文件",
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
    return await download_chatter_params_service(task_id)


@router.delete(
    "/tasks/{task_id}",
    summary="取消/删除任务",
)
async def delete_task(task_id: str) -> dict[str, Any]:
    """取消或删除切削参数推荐任务。

    - 非终态任务：将状态置为 CANCELLED 后删除任务元信息
    - 终态任务（FAILED / CANCELLED）：直接删除任务元信息
    - SUCCEEDED 状态任务禁止删除（项目记忆硬约束：阶段 5 颤振预测可能已引用其 ChatterParams）

    注意：ChatterParams JSON 文件与 workspace 目录不会被自动删除，
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
    review_params as review_params_service,
    export_chatter_params as export_chatter_params_service,
    download_chatter_params as download_chatter_params_service,
    delete_task as delete_task_service,
)
