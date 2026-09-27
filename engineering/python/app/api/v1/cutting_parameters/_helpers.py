"""切削参数路由辅助函数（从 routes.py 抽取，D5 God 模块拆分）。

承接与 HTTP 无关的纯逻辑：
- _spawn: 后台任务引用保存
- _get_pipeline: pipeline 单例懒加载
- _disclaimer_dict: 精度告知字段构造
- _resolve_upstream_calibrated: 上游精度继承链追溯
抽取后 routes.py 仅保留端点，行为零变更。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import config
from app.core.safe_errors import safe_error_message
from app.cutting_parameters import (
    CuttingParametersPipeline,
    CuttingParametersTask,
    MaterialParams,
    MaterialResolverError,
    build_cutting_disclaimer,
    get_material_resolver,
)

logger = logging.getLogger(__name__)

# 后台任务引用集合（asyncio.create_task 不保存引用会被 GC 回收）
_background_tasks: set = set()


def _spawn(coro):
    """启动后台任务并保存引用，避免被 Python GC 回收。"""
    t = asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)
    return t


_pipeline: CuttingParametersPipeline | None = None


def _get_pipeline() -> CuttingParametersPipeline:
    """获取 pipeline 单例。"""
    global _pipeline
    if _pipeline is None:
        _pipeline = CuttingParametersPipeline(cfg=config.cutting_parameters)
    return _pipeline


def _disclaimer_dict(
    task: CuttingParametersTask | None = None,
    material: MaterialParams | None = None,
    chatter_params_ready: bool = False,
) -> dict[str, Any]:
    """构造精度告知字段。

    优先用 task 上下文 + 材料 metadata 构造（覆盖 mesh / 材料校准状态）；
    无 task 时返回通用默认值（用于 precision_info 端点）。
    """
    if task is not None:
        # 查询材料以获取 calibration_status（若失败则按 pending 处理）
        if material is None:
            try:
                material = get_material_resolver().get_material(task.material_id)
            except MaterialResolverError:
                material = None
        cal_status = material.calibration_status if material is not None else "pending_calibration"
        return build_cutting_disclaimer(
            mesh_calibrated=task.mesh_calibrated,
            feature_source=task.input_features_path,
            step_source=task.step_file_path,
            material_id=task.material_id,
            material_calibration_status=cal_status,
            precision_tier=task.precision_tier,
            machine_type=task.machine_type,
            tool_diameter_mm=task.tool_diameter_mm,
            chatter_params_ready=chatter_params_ready,
        ).to_dict()
    return build_cutting_disclaimer(
        mesh_calibrated=False,
        feature_source="external_upload",
        step_source="external_upload",
        material_id="unknown",
        material_calibration_status="pending_calibration",
        precision_tier=config.cutting_parameters.precision_tier,
        machine_type=config.cutting_parameters.default_machine_type,
        tool_diameter_mm=config.cutting_parameters.default_tool_diameter_mm,
        chatter_params_ready=False,
    ).to_dict()


def _resolve_upstream_calibrated(
    source_parametric_geometry_task_id: str,
) -> tuple[bool, str]:
    """从上游阶段 3 任务追溯 mesh 标定状态。

    精度继承链：阶段 1 image_to_3d → 阶段 2 feature_extraction
              → 阶段 3 parametric_geometry → 阶段 4 cutting_parameters
    本方法查询阶段 3 任务的 mesh_calibrated 字段，避免精度信息断层。

    Returns:
        (calibrated, step_source)
        - 上游任务存在且为 SUCCEEDED：(task.mesh_calibrated, pg_task_id)
        - 上游任务不存在 / 未完成：(False, "external_upload")，并记日志
    """
    if not source_parametric_geometry_task_id:
        return False, "external_upload"

    try:
        from app.parametric_geometry import get_task_store as get_pg_store
        from app.parametric_geometry import ParametricGeometryTaskStatus
    except ImportError:
        logger.warning(
            "parametric_geometry 模块未启用，无法追溯上游 mesh_calibrated 状态 source_pg_task_id=%s，按未标定处理",
            source_parametric_geometry_task_id,
        )
        return False, "external_upload"

    try:
        pg_task = get_pg_store().get(source_parametric_geometry_task_id)
        if pg_task is None:
            logger.warning(
                "上游 parametric_geometry 任务不存在 task_id=%s，按未标定处理",
                source_parametric_geometry_task_id,
            )
            return False, "external_upload"

        if pg_task.status != ParametricGeometryTaskStatus.SUCCEEDED.value:
            logger.warning(
                "上游 parametric_geometry 任务未 SUCCEEDED task_id=%s status=%s，按未标定处理",
                source_parametric_geometry_task_id,
                pg_task.status,
            )
            return False, source_parametric_geometry_task_id

        return bool(pg_task.mesh_calibrated), source_parametric_geometry_task_id

    except Exception as e:
        safe = safe_error_message(e, context="cutting_parameters.resolve_upstream_calibrated")
        logger.warning(
            "查询上游任务异常 source_pg_task_id=%s error_id=%s，按未标定处理",
            source_parametric_geometry_task_id,
            safe.get("error_id"),
        )
        return False, "external_upload"
