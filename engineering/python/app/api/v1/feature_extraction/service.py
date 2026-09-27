"""feature_extraction 路由业务逻辑（从 routes.py 抽取，波次2 S4）。

端点参数校验/鉴权由 routes.py 负责，本模块仅承载业务实现。
"""

from __future__ import annotations
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any
from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from app.api.v1.feature_extraction.schemas import (
    ReviewRequest,
    TaskCreateFromPathRequest,
)
from app.config import config
from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message
from app.utils.utils import get_upload_dir, sanitize_filename
from app.utils.upload_security import validate_upload
from app.feature_extraction import (
    FeatureExtractionTaskStatus,
    FeatureReviewError,
    FeatureReviewStatus,
    MeshLoadError,
    get_feature_store,
)
from app.api.v1.feature_extraction._helpers import (
    _spawn,
    _get_pipeline,
    _disclaimer_dict,
    _resolve_upstream_calibrated,
)


logger = logging.getLogger(__name__)


# mesh 上传校验常量（F821 修复：历史实现引用但从未定义，端点运行期会 NameError）
ALLOWED_MESH_EXTENSIONS: set[str] = {".stl", ".obj", ".step", ".stp", ".iges", ".igs"}
MAX_MESH_SIZE: int = 200 * 1024 * 1024  # 200 MB（单位：字节，见 validate_upload）

# mesh 上传目录（用于外部上传的 mesh 文件，区别于 image_to_3d 链路）
UPLOAD_DIR = get_upload_dir("feature_extraction")


async def get_precision_info() -> dict[str, Any]:
    """查询当前精度档位信息与工业硬门槛（不创建任务）。

    前端在用户进入特征提取页面前应先调用此端点，向用户展示：
    - 当前精度档位（继承自上游 image_to_3d mesh）
    - 适用 / 不适用场景
    - 工业生产硬门槛
    - 工程师审核流程说明
    """
    return success(
        data={
            "current_tier": config.feature_extraction.precision_tier,
            "available_tiers": ["coarse", "standard", "high"],
            "tier_specs": {
                "coarse": "0.5-2.0mm，特征参数误差较大，仅可用于工艺理解",
                "standard": "0.1-1.0mm，非配合面特征可用，配合面仍不可达",
                "high": "0.1-0.5mm，小零件细节特征可用，仍达不到工业级配合面公差",
            },
            "extraction_parameters": {
                "plane_ransac_threshold_mm": config.feature_extraction.plane_ransac_threshold_mm,
                "plane_min_inliers": config.feature_extraction.plane_min_inliers,
                "plane_max_features": config.feature_extraction.plane_max_features,
                "cylinder_min_radius_mm": config.feature_extraction.cylinder_min_radius_mm,
                "cylinder_max_radius_mm": config.feature_extraction.cylinder_max_radius_mm,
                "hole_min_radius_mm": config.feature_extraction.hole_min_radius_mm,
                "hole_max_radius_mm": config.feature_extraction.hole_max_radius_mm,
            },
            "review_workflow": {
                "step_1": "算法提取平面 / 圆柱 / 孔 / 凸台 → 状态 FEATURES_EXTRACTED",
                "step_2": "工程师逐条审核：confirmed / rejected / edited",
                "step_3": "全部审核完毕 → 状态 REVIEWED",
                "step_4": "导出已确认特征集 JSON → 状态 SUCCEEDED",
                "step_5": "JSON 交阶段 3 参数化 STEP 生成（人工确认特征）",
            },
            "industrial_hard_gates": [
                "mesh → 参数化 CAD 自动转换工业上未解决：本模块输出「算法建议特征」",
                "工程师必须审核每个特征（confirmed / rejected / edited）后才允许进入阶段 3",
                "良品率要求：0 缺陷容忍",
                "配合面公差：0.01mm（手机摄影测量物理上不可达）",
                "CNC 操作资质：需持证操作员",
                "导师签字 + 保险：大一独立项目无法独立完成此环节",
                "G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验",
                "本系统定位为「工程师助手」，非「全自动生产线」",
            ],
            "feature_disclaimer": _disclaimer_dict(),
        },
        message="特征提取精度档位与工业硬门槛信息",
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
    if not config.feature_extraction.enabled:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="特征提取模块未启用",
            suggestion="请在配置中设置 LNN_FE_ENABLED=true",
        )

    mesh_path = Path(body.mesh_path)
    if not mesh_path.is_absolute():
        # 相对路径按 output/feature_extraction/ 解析，避免任意路径读取
        mesh_path = Path(config.feature_extraction.output_dir).parent / mesh_path

    if not mesh_path.exists():
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"mesh 文件不存在: {mesh_path}",
            suggestion=("请确认 mesh 路径正确，或改用 POST /tasks/upload 上传 mesh 文件。"),
        )

    suffix = mesh_path.suffix.lower()
    if suffix not in ALLOWED_MESH_EXTENSIONS:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"不支持的 mesh 格式: {suffix}，仅支持 {sorted(ALLOWED_MESH_EXTENSIONS)}"),
        )

    # 查询上游 image_to_3d 任务的标定状态（软依赖）
    mesh_calibrated, mesh_source = _resolve_upstream_calibrated(body.source_reconstruction_task_id)

    try:
        pipeline = _get_pipeline()
        task = await pipeline.create_task(
            mesh_path=mesh_path,
            source_reconstruction_task_id=body.source_reconstruction_task_id,
            mesh_calibrated=mesh_calibrated,
        )
    except MeshLoadError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="请检查 mesh 文件是否完整、格式是否正确",
        )
    except Exception as e:
        safe = safe_error_message(e, context="feature_extraction.create_task_from_path")
        logger.error(
            "创建特征提取任务失败 | error_id=%s | exc=%s",
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
            "input_mesh_path": task.input_mesh_path,
            "source_reconstruction_task_id": task.source_reconstruction_task_id,
            "mesh_calibrated": mesh_calibrated,
            "feature_disclaimer": _disclaimer_dict(
                mesh_calibrated=mesh_calibrated,
                mesh_source=mesh_source,
            ),
        },
        message=(f"任务已创建 task_id={task.task_id}，请调用 POST /tasks/{task.task_id}/run 触发执行"),
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
    if not config.feature_extraction.enabled:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="特征提取模块未启用",
            suggestion="请在配置中设置 LNN_FE_ENABLED=true",
        )

    try:
        content = await validate_upload(
            file,
            max_size=MAX_MESH_SIZE,
            allowed_extensions=ALLOWED_MESH_EXTENSIONS,
            allowed_mimes=None,  # mesh MIME 类型多样，不做强制校验
        )
    except HTTPException as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e.detail))

    safe_name = sanitize_filename(file.filename or "mesh.ply")
    if not safe_name:
        safe_name = f"mesh_{uuid.uuid4().hex[:8]}.ply"
    unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    save_path = UPLOAD_DIR / unique_name
    try:
        await asyncio.to_thread(save_path.write_bytes, content)
    except OSError as e:
        safe = safe_error_message(e, context="feature_extraction.upload.save")
        logger.error(
            "保存上传 mesh 失败 | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            suggestion="请检查磁盘空间或文件权限",
        )

    # 上传文件默认未标定
    mesh_calibrated, mesh_source = _resolve_upstream_calibrated(source_reconstruction_task_id)
    # 即便上游任务存在且 calibrated=True，本地上传的 mesh 也可能与上游不同
    # 此处保守起见，若用户明确上传了文件，则按上游查询结果处理（不强置 False）

    try:
        pipeline = _get_pipeline()
        task = await pipeline.create_task(
            mesh_path=save_path,
            source_reconstruction_task_id=source_reconstruction_task_id,
            mesh_calibrated=mesh_calibrated,
        )
    except Exception as e:
        safe = safe_error_message(e, context="feature_extraction.create_task_from_upload")
        logger.error(
            "创建特征提取任务失败 | error_id=%s | exc=%s",
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
            "input_mesh_path": task.input_mesh_path,
            "source_reconstruction_task_id": task.source_reconstruction_task_id,
            "mesh_calibrated": mesh_calibrated,
            "feature_disclaimer": _disclaimer_dict(
                mesh_calibrated=mesh_calibrated,
                mesh_source=mesh_source,
            ),
        },
        message=(f"任务已创建 task_id={task.task_id}，请调用 POST /tasks/{task.task_id}/run 触发执行"),
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
    store = get_feature_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    if task.status not in (
        FeatureExtractionTaskStatus.PENDING.value,
        FeatureExtractionTaskStatus.FAILED.value,
    ):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态不允许执行当前操作 status={task.status}。仅 PENDING / FAILED 状态可触发执行。"),
        )

    # 重试场景：清空错误信息
    if task.status == FeatureExtractionTaskStatus.FAILED.value:
        store.update(task_id, error_message="")

    pipeline = _get_pipeline()
    _spawn(pipeline.run_task(task_id))

    return success(
        data={
            "task_id": task_id,
            "status": FeatureExtractionTaskStatus.RUNNING.value,
            "message": (
                "任务已开始执行，请轮询 GET /tasks/{task_id} 获取状态。"
                "执行完成后状态将变为 FEATURES_EXTRACTED，等待工程师审核。"
            ),
        },
        message="任务已开始执行",
    )


async def get_task_status(task_id: str) -> dict[str, Any]:
    """查询任务当前状态、各阶段耗时、特征统计、精度告知字段。"""
    store = get_feature_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    # 查询 mesh_calibrated（软依赖上游 image_to_3d）
    mesh_calibrated, mesh_source = _resolve_upstream_calibrated(task.source_reconstruction_task_id)

    # 统计各类特征数量
    plane_count = sum(1 for f in task.features if f.feature_type == "plane")
    cylinder_count = sum(1 for f in task.features if f.feature_type == "cylinder")
    hole_count = sum(1 for f in task.features if f.feature_type == "hole")
    boss_count = sum(1 for f in task.features if f.feature_type == "boss")

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "input_mesh_path": task.input_mesh_path,
            "source_reconstruction_task_id": task.source_reconstruction_task_id,
            "vertex_count": task.vertex_count,
            "face_count": task.face_count,
            "feature_count": len(task.features),
            "plane_count": plane_count,
            "cylinder_count": cylinder_count,
            "hole_count": hole_count,
            "boss_count": boss_count,
            "plane_duration_seconds": round(task.plane_duration_seconds, 2),
            "cylinder_duration_seconds": round(task.cylinder_duration_seconds, 2),
            "hole_duration_seconds": round(task.hole_duration_seconds, 2),
            "total_duration_seconds": round(task.total_duration_seconds, 2),
            "error_message": task.error_message,
            "reviewed_by": task.reviewed_by,
            "reviewed_at": task.reviewed_at,
            "exported_features_path": task.exported_features_path,
            "mesh_calibrated": mesh_calibrated,
            "feature_disclaimer": _disclaimer_dict(
                mesh_calibrated=mesh_calibrated,
                mesh_source=mesh_source,
            ),
        },
    )


async def list_tasks(limit: int = 20) -> dict[str, Any]:
    """列出最近的特征提取任务（按创建时间倒序）。"""
    if limit < 1 or limit > 100:
        limit = max(1, min(100, limit))

    store = get_feature_store()
    tasks = store.list_all(limit=limit)
    return success(
        data={
            "tasks": [
                {
                    "task_id": t.task_id,
                    "status": t.status,
                    "input_mesh_path": t.input_mesh_path,
                    "source_reconstruction_task_id": t.source_reconstruction_task_id,
                    "feature_count": len(t.features),
                    "vertex_count": t.vertex_count,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                    "reviewed_by": t.reviewed_by,
                }
                for t in tasks
            ],
            "total": len(tasks),
        },
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
    store = get_feature_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    allowed_states = {
        FeatureExtractionTaskStatus.FEATURES_EXTRACTED.value,
        FeatureExtractionTaskStatus.REVIEWED.value,
        FeatureExtractionTaskStatus.SUCCEEDED.value,
    }
    if task.status not in allowed_states:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许获取结果，仅 {sorted(allowed_states)} 状态可获取。"),
            suggestion="请等待状态变为 features_extracted 后再调用此端点",
        )

    mesh_calibrated, mesh_source = _resolve_upstream_calibrated(task.source_reconstruction_task_id)

    plane_count = sum(1 for f in task.features if f.feature_type == "plane")
    cylinder_count = sum(1 for f in task.features if f.feature_type == "cylinder")
    hole_count = sum(1 for f in task.features if f.feature_type == "hole")
    boss_count = sum(1 for f in task.features if f.feature_type == "boss")

    features_data = [
        {
            "feature_id": f.feature_id,
            "feature_type": f.feature_type,
            "params": f.params,
            "confidence": round(float(f.confidence), 4),
            "review_status": f.review_status,
            "engineer_notes": f.engineer_notes,
            "edited": f.review_status == FeatureReviewStatus.EDITED.value,
            "edited_params": f.edited_params,
        }
        for f in task.features
    ]

    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "features": features_data,
            "feature_count": len(task.features),
            "plane_count": plane_count,
            "cylinder_count": cylinder_count,
            "hole_count": hole_count,
            "boss_count": boss_count,
            "mesh_calibrated": mesh_calibrated,
            "feature_disclaimer": _disclaimer_dict(
                mesh_calibrated=mesh_calibrated,
                mesh_source=mesh_source,
            ),
        },
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
    store = get_feature_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    if task.status != FeatureExtractionTaskStatus.FEATURES_EXTRACTED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(
                f"任务状态 {task.status} 不允许审核，"
                f"仅 {FeatureExtractionTaskStatus.FEATURES_EXTRACTED.value} 状态可审核"
            ),
            suggestion="请等待算法提取完成（状态变为 features_extracted）后再审核",
        )

    # 校验 action
    valid_actions = {
        FeatureReviewStatus.CONFIRMED.value,
        FeatureReviewStatus.REJECTED.value,
        FeatureReviewStatus.EDITED.value,
    }
    if body.action not in valid_actions:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"非法 action: {body.action}，应为 {sorted(valid_actions)}",
        )

    # edited 动作必须提供 edited_params
    if body.action == FeatureReviewStatus.EDITED.value and not body.edited_params:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="action=edited 时必须提供 edited_params",
            suggestion="请提供编辑后的完整参数（字段结构需与原始 params 一致）",
        )

    try:
        pipeline = _get_pipeline()
        reviewed_feature = pipeline.review_feature(
            task_id=task_id,
            feature_id=feature_id,
            action=body.action,
            edited_params=body.edited_params,
            engineer_notes=body.engineer_notes,
            reviewed_by=body.reviewed_by,
        )
    except FeatureReviewError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )
    except Exception as e:
        safe = safe_error_message(e, context="feature_extraction.review_feature")
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
    task_after = store.get(task_id)
    if task_after is None:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="审核后任务丢失，请检查任务存储",
        )

    all_reviewed = all(f.review_status != FeatureReviewStatus.PENDING.value for f in task_after.features)

    mesh_calibrated, mesh_source = _resolve_upstream_calibrated(task_after.source_reconstruction_task_id)

    return success(
        data={
            "task_id": task_id,
            "feature_id": reviewed_feature.feature_id,
            "feature_type": reviewed_feature.feature_type,
            "review_status": reviewed_feature.review_status,
            "effective_params": reviewed_feature.effective_params(),
            "all_reviewed": all_reviewed,
            "task_status": task_after.status,
            "feature_disclaimer": _disclaimer_dict(
                mesh_calibrated=mesh_calibrated,
                mesh_source=mesh_source,
            ),
        },
        message=(
            f"特征 {feature_id} 已审核（action={body.action}）。"
            + (
                " 全部特征已审核完毕，可调用 GET /tasks/{task_id}/export 导出。"
                if all_reviewed
                else " 仍有特征待审核。"
            )
        ),
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
    store = get_feature_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    allowed_states = {
        FeatureExtractionTaskStatus.FEATURES_EXTRACTED.value,
        FeatureExtractionTaskStatus.REVIEWED.value,
        FeatureExtractionTaskStatus.SUCCEEDED.value,
    }
    if task.status not in allowed_states:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务状态 {task.status} 不允许导出，仅 {sorted(allowed_states)} 状态可导出"),
            suggestion="请等待算法提取完成并至少审核一个特征后再导出",
        )

    pipeline = _get_pipeline()

    # SUCCEEDED 状态：若已导出过则直接返回下载链接
    if (
        task.status == FeatureExtractionTaskStatus.SUCCEEDED.value
        and task.exported_features_path
        and Path(task.exported_features_path).exists()
    ):
        exported_path = Path(task.exported_features_path)
        # 重新统计已确认特征数
        confirmed_count = sum(
            1
            for f in task.features
            if f.review_status
            in (
                FeatureReviewStatus.CONFIRMED.value,
                FeatureReviewStatus.EDITED.value,
            )
        )
    else:
        try:
            exported_path = await asyncio.to_thread(pipeline.export_confirmed_features, task_id)
        except FeatureReviewError as e:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=str(e),
            )
        except Exception as e:
            safe = safe_error_message(e, context="feature_extraction.export_confirmed_features")
            logger.error(
                "导出已确认特征失败 task_id=%s | error_id=%s | exc=%s",
                task_id,
                safe.get("error_id"),
                e,
                exc_info=True,
            )
            return error(
                code=ErrorCode.INTERNAL_ERROR,
                message=safe["message"],
            )
        # 重新查询（export_confirmed_features 内部已更新任务状态）
        task_after = store.get(task_id)
        if task_after is None:
            return error(
                code=ErrorCode.INTERNAL_ERROR,
                message="导出后任务丢失，请检查任务存储",
            )
        confirmed_count = sum(
            1
            for f in task_after.features
            if f.review_status
            in (
                FeatureReviewStatus.CONFIRMED.value,
                FeatureReviewStatus.EDITED.value,
            )
        )

    mesh_calibrated, mesh_source = _resolve_upstream_calibrated(task.source_reconstruction_task_id)

    return success(
        data={
            "task_id": task_id,
            "status": FeatureExtractionTaskStatus.SUCCEEDED.value,
            "exported_features_path": str(exported_path),
            "confirmed_feature_count": confirmed_count,
            "download_url": (f"/api/v1/feature_extraction/tasks/{task_id}/export/download"),
            "feature_disclaimer": _disclaimer_dict(
                mesh_calibrated=mesh_calibrated,
                mesh_source=mesh_source,
            ),
        },
        message=(
            f"已导出 {confirmed_count} 条已确认特征。"
            "JSON 文件可下载，并交阶段 3 参数化 STEP 生成模块使用。"
            "注意：生成的 STEP / G 代码必须经 CAM 软件二次校验后才允许上机床。"
        ),
    )


async def download_exported_features(task_id: str) -> FileResponse:
    """下载已导出的特征集 JSON 文件。

    仅当任务状态为 SUCCEEDED 且导出文件存在时可下载。
    """
    store = get_feature_store()
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在 task_id={task_id}")

    if task.status != FeatureExtractionTaskStatus.SUCCEEDED.value:
        raise HTTPException(
            status_code=400,
            detail=(
                f"任务未完成导出 status={task.status}，无法下载。请先调用 GET /tasks/{{task_id}}/export 触发导出。"
            ),
        )

    if not task.exported_features_path:
        raise HTTPException(
            status_code=404,
            detail="任务导出文件路径为空",
        )

    output_path = Path(task.exported_features_path)
    if not output_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"导出文件不存在 path={output_path}",
        )

    return FileResponse(
        path=str(output_path),
        media_type="application/json",
        filename=f"confirmed_features_{task_id}.json",
    )


async def delete_task(task_id: str) -> dict[str, Any]:
    """删除特征提取任务及其持久化文件。

    注意：
    - 已导出的 JSON 文件（位于 output/feature_extraction/{task_id}/）不会被自动删除，
      避免误删阶段 3 已引用的特征集。
    - 仅清理任务元信息（tasks/{task_id}.json）与内存状态。
    """
    store = get_feature_store()
    task = store.get(task_id)
    if task is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"任务不存在 task_id={task_id}",
        )

    # SUCCEEDED 状态的任务禁止删除（避免误删阶段 3 已引用的特征集来源）
    if task.status == FeatureExtractionTaskStatus.SUCCEEDED.value:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=(f"任务 {task_id} 已 SUCCEEDED，禁止删除。已导出的特征集可能已被阶段 3 参数化 STEP 生成模块引用。"),
            suggestion="如确需删除，请先手动清理阶段 3 引用，再删除任务",
        )

    deleted = store.delete(task_id)
    if not deleted:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"删除任务失败 task_id={task_id}",
        )

    return success(
        data={"task_id": task_id, "deleted": True},
        message="任务已删除",
    )
