r"""CAM 校验路由层辅助函数（从 routes.py 抽取，D5 God 模块拆分）。

本模块承接阶段 7 路由层中与 HTTP 无关的纯逻辑：
- pipeline 单例（懒加载，避免模块导入期触发 InternalValidator / CamAdapter 初始化）
- 精度告知字段构造
- 上游 gcode_generation 任务的精度继承链追溯

抽取后 routes.py 仅保留端点处理函数与 Pydantic 模型，降低单文件体量、
提升可测性，且不改变任何运行时行为（函数签名与原位置完全一致）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.cam_validation import (
    CamValidationPipeline,
    build_cam_disclaimer,
    CamValidationTask,
)
from app.config import config
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

# pipeline 单例（懒加载，避免模块导入期触发 InternalValidator / CamAdapter 初始化）
_pipeline: CamValidationPipeline | None = None


def _get_pipeline() -> CamValidationPipeline:
    """获取 pipeline 单例。"""
    global _pipeline
    if _pipeline is None:
        _pipeline = CamValidationPipeline(cfg=config.cam_validation)
    return _pipeline


def _disclaimer_dict(
    task: CamValidationTask | None = None,
    cam_report_exported: bool = False,
) -> dict[str, Any]:
    """构造精度告知字段。

    优先用 task 上下文构造（覆盖 controller / 材料校准状态 / LTC 实验性路径 /
    CAM 后端降级情况）；无 task 时返回通用默认值（用于 precision_info 端点）。

    项目记忆硬约束：cam_validation_required 始终 True，不可由参数关闭。
    """
    if task is not None:
        material_calibration_status = "pending_calibration" if task.pending_calibration else "calibrated"
        ltc_experiment_used = task.prediction_method in (
            "neural_network",
            "mixed",
        )
        return build_cam_disclaimer(
            precision_tier=config.cam_validation.precision_tier,
            controller_type=task.controller_type,
            material_name=task.material_name,
            material_calibration_status=material_calibration_status,
            gcode_report_source=task.source_gcode_report_path,
            gcode_file_source=task.source_gcode_file_path,
            prediction_method=task.prediction_method or "analytical",
            total_features=task.total_features,
            passed_features=task.passed_features,
            failed_features=task.failed_features,
            pending_calibration=task.pending_calibration,
            ltc_experiment_used=ltc_experiment_used,
            cam_backend_used=task.cam_backend_used or "internal_only",
            cam_backend_fallback_reason=task.cam_backend_fallback_reason or "",
            cam_backend_requested=task.cam_backend_requested or "internal_only",
            cam_report_exported=cam_report_exported or bool(task.cam_report_path),
        ).to_dict()

    # 无 task 上下文（precision_info 端点默认值）
    return build_cam_disclaimer(
        precision_tier=config.cam_validation.precision_tier,
        controller_type=config.cam_validation.default_cam_backend,  # 占位（无 controller 概念）
        material_name="unknown",
        material_calibration_status="pending_calibration",
        gcode_report_source="external_upload",
        gcode_file_source="external_upload",
        prediction_method="analytical",
        total_features=0,
        passed_features=0,
        failed_features=0,
        pending_calibration=False,
        ltc_experiment_used=False,
        cam_backend_used=config.cam_validation.default_cam_backend,
        cam_backend_fallback_reason="",
        cam_backend_requested=config.cam_validation.default_cam_backend,
        cam_report_exported=False,
    ).to_dict()


def _resolve_upstream_gcode_calibrated(
    source_gcode_generation_task_id: str,
) -> tuple[str, str, str, str, float, float, bool, str]:
    """从上游阶段 6 任务追溯 G 代码报告路径 + G 代码文件路径 + 上下文。

    精度继承链：阶段 6 gcode_generation → 阶段 7 cam_validation（本模块）
    本方法查询阶段 6 任务的 gcode_report_path / gcode_file_path /
    controller_type / material_name / safe_z / stock_top_z /
    pending_calibration / prediction_method。

    Returns:
        (gcode_report_path, gcode_file_path, controller_type, material_name,
         safe_z, stock_top_z, pending_calibration, prediction_method)
        - 上游任务存在且为 SUCCEEDED：返回 task 字段
        - 上游任务不存在 / 未完成：返回空字符串 + 默认值，并记日志
    """
    empty_result: tuple[str, str, str, str, float, float, bool, str] = (
        "",
        "",
        "",
        "",
        80.0,
        50.0,
        False,
        "analytical",
    )

    if not source_gcode_generation_task_id:
        return empty_result

    try:
        from app.gcode_generation import (
            get_task_store as get_gcode_store,
            GCodeGenerationTaskStatus,
        )
    except ImportError:
        logger.warning(
            "gcode_generation 模块未启用，无法追溯上游 G 代码报告路径 "
            "source_gcode_task_id=%s，需显式提供 gcode_report_path",
            source_gcode_generation_task_id,
        )
        return empty_result

    try:
        gcode_task = get_gcode_store().get_task(source_gcode_generation_task_id)
        if gcode_task is None:
            logger.warning(
                "上游 gcode_generation 任务不存在 task_id=%s，按未提供处理",
                source_gcode_generation_task_id,
            )
            return empty_result

        if gcode_task.status != GCodeGenerationTaskStatus.SUCCEEDED.value:
            logger.warning(
                "上游 gcode_generation 任务未 SUCCEEDED task_id=%s status=%s，按未提供处理",
                source_gcode_generation_task_id,
                gcode_task.status,
            )
            return empty_result

        if not gcode_task.gcode_report_path:
            logger.warning(
                "上游 gcode_generation 任务已 SUCCEEDED 但 gcode_report_path 为空 task_id=%s，按未提供处理",
                source_gcode_generation_task_id,
            )
            return empty_result

        # 阶段 6 字段直接继承（阶段 7 不二次拟合 / 不重算）
        return (
            gcode_task.gcode_report_path,
            gcode_task.gcode_file_path or "",
            gcode_task.controller_type,
            gcode_task.material_name,
            gcode_task.safe_z,
            gcode_task.stock_top_z,
            bool(gcode_task.pending_calibration),
            gcode_task.prediction_method or "analytical",
        )

    except Exception as e:
        safe = safe_error_message(e, context="cam_validation.resolve_upstream_gcode_calibrated")
        logger.warning(
            "查询上游 gcode_generation 任务异常 source_gcode_task_id=%s error_id=%s，按未提供处理",
            source_gcode_generation_task_id,
            safe.get("error_id"),
        )
        return empty_result
