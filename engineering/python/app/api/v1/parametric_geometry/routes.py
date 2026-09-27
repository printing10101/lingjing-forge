"""参数化几何输出模块 API 路由实现。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.v1.parametric_geometry.schemas import (
    FinalizeResponse,
    ReviewRequest,
    ReviewResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskResultResponse,
    TaskStatusResponse,
)
from app.auth.permissions import require_permission

from app.parametric_geometry import (
    ParametricGeometryPipeline,
)

logger = logging.getLogger(__name__)

# 后台任务引用集合（C5 修复：asyncio.create_task 不保存引用会被 GC 回收）
_background_tasks: set = set()


router = APIRouter(
    prefix="/api/v1/parametric_geometry",
    tags=["Parametric Geometry (Engineer-Assisted STEP)"],
    dependencies=[Depends(require_permission("parametric_geometry:read"))],
)

# pipeline 单例（懒加载，避免模块导入期就触发 pythonOCC/FreeCAD 可选依赖加载）
_pipeline: ParametricGeometryPipeline | None = None


# 端点实现


@router.get("/precision_info")
async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息与工业硬门槛（不创建任务）。

    前端在用户进入参数化几何页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d mesh + feature_extraction 特征）
    - 适用 / 不适用场景
    - 工业生产硬门槛
    - 两轮工程师审核流程说明
    """
    return await get_precision_info_service()


@router.post(
    "/tasks",
    response_model=TaskCreateResponse,
    summary="创建参数化几何任务（输入阶段 2 confirmed_features.json 路径）",
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
    return await create_task_service(body)


@router.post(
    "/tasks/{task_id}/run",
    summary="异步触发参数化几何流水线执行",
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
    return await run_task_service(task_id)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询任务状态",
)
async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、审核进度、STEP 路径、精度告知字段。"""
    return await get_task_status_service(task_id)


@router.get(
    "/tasks",
    summary="列出最近任务",
)
async def list_tasks(limit: int = 20) -> dict[str, Any]:
    """列出最近的参数化几何任务（按创建时间倒序）。"""
    return await list_tasks_service(limit)


@router.get(
    "/tasks/{task_id}/result",
    response_model=TaskResultResponse,
    summary="获取 STEP 生成结果 + 装配摘要 + 特征列表",
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
    return await get_task_result_service(task_id)


@router.post(
    "/tasks/{task_id}/review",
    response_model=ReviewResponse,
    summary="工程师审核单个特征在 STEP 中的表达（第一轮审核）",
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
    return await review_step_feature_service(task_id, feature_id, body)


@router.post(
    "/tasks/{task_id}/finalize",
    response_model=FinalizeResponse,
    summary="基于审核结果重新生成最终 STEP（第二轮 STEP 生成）",
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
    return await finalize_step_service(task_id)


@router.get(
    "/tasks/{task_id}/step/download",
    summary="下载 STEP 文件",
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
    return await download_step_file_service(task_id, final)


@router.delete(
    "/tasks/{task_id}",
    summary="取消/删除任务",
)
async def delete_task(task_id: str) -> dict[str, Any]:
    """取消或删除参数化几何任务。

    - 非终态任务：先取消（CANCELLED），再删除任务元信息
    - 终态任务（SUCCEEDED / FAILED / CANCELLED）：直接删除任务元信息
    - SUCCEEDED 状态任务禁止删除（避免误删下游 CAM 模块已引用的最终 STEP）

    注意：STEP 文件与 workspace 目录不会被自动删除，
    避免误删下游链路已引用的资源。
    """
    return await delete_task_service(task_id)


from .service import (  # noqa: E402
    _disclaimer_dict,  # noqa: F401 - 测试专用 re-export
    _resolve_upstream_calibrated,  # noqa: F401 - 测试专用 re-export
    get_precision_info as get_precision_info_service,
    create_task as create_task_service,
    run_task as run_task_service,
    get_task_status as get_task_status_service,
    list_tasks as list_tasks_service,
    get_task_result as get_task_result_service,
    review_step_feature as review_step_feature_service,
    finalize_step as finalize_step_service,
    download_step_file as download_step_file_service,
    delete_task as delete_task_service,
)
