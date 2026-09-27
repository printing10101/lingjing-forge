"""几何特征辅助提取模块 API 路由实现。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse

from app.api.v1.feature_extraction.schemas import (
    ExportResponse,
    ReviewRequest,
    ReviewResponse,
    TaskCreateFromPathRequest,
    TaskCreateResponse,
    TaskResultResponse,
    TaskStatusResponse,
)
from app.auth.permissions import require_permission
from app.utils.utils import get_upload_dir


logger = logging.getLogger(__name__)

# 后台任务引用集合（C5 修复：asyncio.create_task 不保存引用会被 GC 回收）
_background_tasks: set = set()

# mesh 上传校验常量（F821 修复：历史实现引用但从未定义，端点运行期会 NameError）
ALLOWED_MESH_EXTENSIONS: set[str] = {".stl", ".obj", ".step", ".stp", ".iges", ".igs"}
MAX_MESH_SIZE: int = 200 * 1024 * 1024  # 200 MB（单位：字节，见 validate_upload）

router = APIRouter(
    prefix="/api/v1/feature_extraction",
    tags=["Feature Extraction (Engineer-Assisted)"],
    dependencies=[Depends(require_permission("feature_extraction:read"))],
)

# mesh 上传目录（用于外部上传的 mesh 文件，区别于 image_to_3d 链路）
UPLOAD_DIR = get_upload_dir("feature_extraction")


@router.get("/precision_info")
async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息与工业硬门槛（不创建任务）。

    前端在用户进入特征提取页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d mesh）
    - 适用 / 不适用场景
    - 工业生产硬门槛
    - 工程师审核流程说明
    """
    return await get_precision_info_service()


@router.post(
    "/tasks",
    response_model=TaskCreateResponse,
    summary="通过 mesh 路径创建特征提取任务（链路模式）",
)
async def create_task_from_path(
    body: TaskCreateFromPathRequest,
) -> dict[str, Any]:
    """通过 mesh 文件路径 + 可选关联重建任务 ID 创建特征提取任务。

    适用场景：
    - 阶段 1 拍照重建已输出 mesh，本阶段直接读取该 mesh 路径
    - 用户从外部 CAD/扫描设备导入 mesh

    若提供 ``source_reconstruction_task_id`` 且上游任务已 SUCCEEDED，
    系统会自动查询上游 mesh 是否已做尺度归一化（calibrated），
    用于决定本模块输出的几何参数单位是 mm 还是无量纲。

    本端点只创建任务（PENDING 状态），不立即执行。
    需随后调用 POST /tasks/{task_id}/run 触发执行。
    """
    return await create_task_from_path_service(body)


@router.post(
    "/tasks/upload",
    response_model=TaskCreateResponse,
    summary="通过上传 mesh 文件创建特征提取任务（外部导入模式）",
)
async def create_task_from_upload(
    request: Request,
    file: UploadFile = File(..., description="mesh 文件（PLY/STL/GLB/OBJ）"),
    source_reconstruction_task_id: str = Form(
        default="",
        description="关联的拍照重建任务 ID（可选，用于追溯 mesh 来源）",
    ),
) -> dict[str, Any]:
    """通过上传 mesh 文件创建特征提取任务。

    适用场景：
    - 用户从外部 CAD 软件导出 mesh
    - 用户使用其他三维扫描设备获取 mesh

    上传的 mesh 默认视为「未标定」（mesh_calibrated=False），
    即几何参数为无量纲值，仅可用于可视化。
    若需得到 mm 尺度的几何参数，请通过阶段 1 拍照重建路径放置标定块。
    """
    return await create_task_from_upload_service(request, file, source_reconstruction_task_id)


@router.post(
    "/tasks/{task_id}/run",
    summary="异步触发特征提取执行",
)
async def run_task(task_id: str) -> dict[str, Any]:
    """异步触发特征提取任务执行。

    执行流程（5 阶段）：
    1. 加载 mesh（trimesh 优先，不可用退化为简易 PLY 解析）
    2. RANSAC 平面拟合 → 候选平面集
    3. 圆柱拟合 → 候选圆柱集
    4. 孔/凸台检测 → 候选孔/凸台集
    5. 合并特征 → 状态置为 FEATURES_EXTRACTED，等待工程师审核

    本端点立即返回 202 Accepted，实际执行在后台进行。
    客户端应轮询 GET /tasks/{task_id} 获取状态。
    """
    return await run_task_service(task_id)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询任务状态",
)
async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、各阶段耗时、特征统计、精度告知字段。"""
    return await get_task_status_service(task_id)


@router.get(
    "/tasks",
    summary="列出最近任务",
)
async def list_tasks(limit: int = 20) -> dict[str, Any]:
    """列出最近的特征提取任务（按创建时间倒序）。"""
    return await list_tasks_service(limit)


@router.get(
    "/tasks/{task_id}/result",
    response_model=TaskResultResponse,
    summary="获取已提取的特征列表",
)
async def get_task_result(task_id: str) -> dict[str, Any]:
    """获取任务已提取的完整特征列表。

    仅当任务状态为 FEATURES_EXTRACTED / REVIEWED / SUCCEEDED 时可调用。
    返回的特征列表中每条包含：
    - feature_id / feature_type
    - params（算法给出的原始参数）
    - confidence（RANSAC inlier 比例，仅供参考）
    - review_status（pending / confirmed / rejected / edited）
    - engineer_notes / edited_params（工程师审核后填充）
    """
    return await get_task_result_service(task_id)


@router.post(
    "/tasks/{task_id}/review",
    response_model=ReviewResponse,
    summary="工程师审核单个特征（人工介入核心端点）",
)
async def review_feature(
    task_id: str,
    feature_id: str,
    body: ReviewRequest,
) -> dict[str, Any]:
    """工程师审核单个特征。

    本端点是 human-in-the-loop 的核心入口（项目记忆硬约束：
    mesh → 参数化 CAD 自动转换工业上未解决，必须工程师审核）。

    审核动作：
    - ``confirmed``: 算法识别正确，参数无需修改
    - ``rejected``:  误识别，丢弃此特征（不进入阶段 3）
    - ``edited``:    识别正确但参数需修正，需同时提供 ``edited_params``

    当所有特征都被审核（confirmed / rejected / edited）后，
    任务状态自动从 FEATURES_EXTRACTED 转为 REVIEWED。

    请求体中 ``feature_id`` 作为查询参数传入，便于 RESTful 路径表达。
    """
    return await review_feature_service(task_id, feature_id, body)


@router.get(
    "/tasks/{task_id}/export",
    response_model=ExportResponse,
    summary="导出已确认特征集为 JSON（供阶段 3 参数化 STEP 生成使用）",
)
async def export_confirmed_features(task_id: str) -> dict[str, Any]:
    """导出已确认（confirmed + edited）的特征集为 JSON 文件。

    适用状态：
    - FEATURES_EXTRACTED: 导出当前已审核的部分（便于增量工作）
    - REVIEWED:           导出全部已审核特征
    - SUCCEEDED:          返回已导出文件的下载链接（不重新导出）

    导出的 JSON 结构（供阶段 3 参数化 STEP 生成使用）：
    ```
    {
      "task_id": "...",
      "exported_at": 1234567890.0,
      "source_mesh_path": "...",
      "source_reconstruction_task_id": "...",
      "feature_count": 12,
      "features": [
        {
          "feature_id": "...",
          "feature_type": "plane|cylinder|hole|boss",
          "params": {...},            # 工程师审核后的生效参数
          "confidence": 0.85,
          "review_status": "confirmed|edited",
          "engineer_notes": "...",
          "edited": false
        },
        ...
      ]
    }
    ```
    """
    return await export_confirmed_features_service(task_id)


@router.get(
    "/tasks/{task_id}/export/download",
    summary="下载已导出的特征集 JSON 文件",
)
async def download_exported_features(task_id: str) -> FileResponse:
    """下载已导出的特征集 JSON 文件。

    仅当任务状态为 SUCCEEDED 且导出文件存在时可下载。
    """
    return await download_exported_features_service(task_id)


@router.delete(
    "/tasks/{task_id}",
    summary="删除任务（清理 workspace）",
)
async def delete_task(task_id: str) -> dict[str, Any]:
    """删除特征提取任务及其持久化文件。

    注意：
    - 已导出的 JSON 文件（位于 output/feature_extraction/{task_id}/）不会被自动删除，
      避免误删阶段 3 已引用的特征集。
    - 仅清理任务元信息（tasks/{task_id}.json）与内存状态。
    """
    return await delete_task_service(task_id)


from .service import (  # noqa: E402
    _disclaimer_dict,  # noqa: F401 - 测试专用 re-export
    _resolve_upstream_calibrated,  # noqa: F401 - 测试专用 re-export
    get_precision_info as get_precision_info_service,
    create_task_from_path as create_task_from_path_service,
    create_task_from_upload as create_task_from_upload_service,
    run_task as run_task_service,
    get_task_status as get_task_status_service,
    list_tasks as list_tasks_service,
    get_task_result as get_task_result_service,
    review_feature as review_feature_service,
    export_confirmed_features as export_confirmed_features_service,
    download_exported_features as download_exported_features_service,
    delete_task as delete_task_service,
)
