r"""G-Code 生成路由层辅助函数（从 routes.py 抽取，D5 God 模块拆分）。

承接阶段 6 路由层中与 HTTP 无关的纯逻辑：
- pipeline 单例（懒加载，避免模块导入期触发 GCodeGenerator 初始化）
- 精度告知字段构造
- 上游阶段 5 chatter_prediction / 阶段 3 parametric_geometry 精度继承链追溯

抽取后 routes.py 仅保留端点处理函数，行为零变更（函数签名与原位置一致）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import config
from app.core.safe_errors import safe_error_message
from app.gcode_generation import (
    GCodeGenerationPipeline,
    GCodeGenerationTask,
    build_gcode_disclaimer,
)

logger = logging.getLogger(__name__)

# pipeline 单例（懒加载，避免模块导入期触发 GCodeGenerator 初始化）
_pipeline: GCodeGenerationPipeline | None = None


def _get_pipeline() -> GCodeGenerationPipeline:
    """获取 pipeline 单例。"""
    global _pipeline
    if _pipeline is None:
        _pipeline = GCodeGenerationPipeline(cfg=config.gcode_generation)
    return _pipeline


def _disclaimer_dict(
    task: GCodeGenerationTask | None = None,
    gcode_file_exported: bool = False,
) -> dict[str, Any]:
    """构造精度告知字段。

    优先用 task 上下文构造（覆盖 controller / 材料校准状态 / LTC 实验性路径）；
    无 task 时返回通用默认值（用于 precision_info 端点）。

    项目记忆硬约束：requires_cam_validation 始终 True，不可由参数关闭。
    """
    if task is not None:
        material_calibration_status = "pending_calibration" if task.pending_calibration else "calibrated"
        ltc_experiment_used = task.prediction_method in (
            "neural_network",
            "mixed",
        )
        return build_gcode_disclaimer(
            precision_tier=config.gcode_generation.precision_tier,
            controller_type=task.controller_type,
            material_name=task.material_name,
            material_calibration_status=material_calibration_status,
            chatter_report_source=task.source_chatter_report_path,
            operation_plan_source=task.source_operation_plan_path,
            prediction_method=task.prediction_method or "analytical",
            total_features=task.total_features,
            stable_features=task.stable_features,
            unstable_features=task.unstable_features,
            pending_calibration=task.pending_calibration,
            ltc_experiment_used=ltc_experiment_used,
            gcode_file_exported=gcode_file_exported or bool(task.gcode_file_path),
        ).to_dict()

    # 无 task 上下文（precision_info 端点默认值）
    return build_gcode_disclaimer(
        precision_tier=config.gcode_generation.precision_tier,
        controller_type=config.gcode_generation.default_controller_type,
        material_name="unknown",
        material_calibration_status="pending_calibration",
        chatter_report_source="external_upload",
        operation_plan_source="external_upload",
        prediction_method="analytical",
        total_features=0,
        stable_features=0,
        unstable_features=0,
        pending_calibration=False,
        ltc_experiment_used=False,
        gcode_file_exported=False,
    ).to_dict()


def _resolve_upstream_chatter_report(
    source_chatter_prediction_task_id: str,
) -> tuple[str, str, str, bool, str]:
    """从上游阶段 5 任务追溯 ChatterReport 路径 + 材料信息。

    精度继承链：阶段 5 chatter_prediction → 阶段 6 gcode_generation（本模块）
    本方法查询阶段 5 任务的 chatter_report_path / material_id / prediction_method
    / pending_calibration / controller_type（用于默认值兜底）。

    Returns:
        (chatter_report_path, material_name, prediction_method,
         pending_calibration, default_controller_type)
        - 上游任务存在且为 SUCCEEDED：返回 task 字段
        - 上游任务不存在 / 未完成：返回空字符串 + 默认值，并记日志
    """
    empty_result = ("", "", "analytical", False, "")

    if not source_chatter_prediction_task_id:
        return empty_result

    try:
        from app.chatter_prediction import (
            get_task_store as get_cp_store,
            ChatterPredictionTaskStatus,
        )
    except ImportError:
        logger.warning(
            "chatter_prediction 模块未启用，无法追溯上游 ChatterReport 路径 "
            "source_cp_task_id=%s，需显式提供 chatter_report_path",
            source_chatter_prediction_task_id,
        )
        return empty_result

    try:
        cp_task = get_cp_store().get_task(source_chatter_prediction_task_id)
        if cp_task is None:
            logger.warning(
                "上游 chatter_prediction 任务不存在 task_id=%s，按未提供处理",
                source_chatter_prediction_task_id,
            )
            return empty_result

        if cp_task.status != ChatterPredictionTaskStatus.SUCCEEDED.value:
            logger.warning(
                "上游 chatter_prediction 任务未 SUCCEEDED task_id=%s status=%s，按未提供处理",
                source_chatter_prediction_task_id,
                cp_task.status,
            )
            return empty_result

        if not cp_task.chatter_report_path:
            logger.warning(
                "上游 chatter_prediction 任务已 SUCCEEDED 但 chatter_report_path 为空 task_id=%s，按未提供处理",
                source_chatter_prediction_task_id,
            )
            return empty_result

        # 阶段 5 material_id 转换为材料名称（阶段 6 用 material_name 字段）
        material_name = getattr(cp_task, "material_id", "unknown") or "unknown"
        prediction_method = getattr(cp_task, "prediction_method", "") or "analytical"
        # pending_calibration 由阶段 5 ChatterReport 内部决定，此处仅作兜底
        pending_calibration = bool(getattr(cp_task, "pending_calibration", False))
        # 阶段 5 不存储 controller_type，返回空让调用方使用默认值
        default_controller_type = ""

        return (
            cp_task.chatter_report_path,
            material_name,
            prediction_method,
            pending_calibration,
            default_controller_type,
        )

    except Exception as e:
        safe = safe_error_message(e, context="gcode_generation.resolve_upstream_chatter_report")
        logger.warning(
            "查询上游 chatter_prediction 任务异常 source_cp_task_id=%s error_id=%s，按未提供处理",
            source_chatter_prediction_task_id,
            safe.get("error_id"),
        )
        return empty_result


def _resolve_upstream_operation_plan(
    source_parametric_geometry_task_id: str,
) -> str:
    """从上游阶段 3 任务追溯 OperationPlan 路径。

    精度继承链：阶段 3 parametric_geometry → 阶段 6 gcode_generation（本模块）
    本方法查询阶段 3 任务的 operation_plan_path（或类似字段）。

    Returns:
        operation_plan_path：上游任务存在且为 SUCCEEDED 时返回路径，否则返回空字符串。
    """
    if not source_parametric_geometry_task_id:
        return ""

    try:
        from app.parametric_geometry import (
            get_task_store as get_pg_store,
        )
    except ImportError:
        logger.warning(
            "parametric_geometry 模块未启用，无法追溯上游 OperationPlan 路径 "
            "source_pg_task_id=%s，需显式提供 operation_plan_path",
            source_parametric_geometry_task_id,
        )
        return ""

    try:
        pg_task = get_pg_store().get_task(  # type: ignore[attr-defined]
            source_parametric_geometry_task_id
        )
        if pg_task is None:
            logger.warning(
                "上游 parametric_geometry 任务不存在 task_id=%s，按未提供处理",
                source_parametric_geometry_task_id,
            )
            return ""

        # 尝试多种可能的字段名（兼容不同版本）
        op_plan_path = (
            getattr(pg_task, "operation_plan_path", None)
            or getattr(pg_task, "op_plan_path", None)
            or getattr(pg_task, "output_path", None)
            or ""
        )
        if not op_plan_path:
            logger.warning(
                "上游 parametric_geometry 任务已存在但 operation_plan_path 为空 task_id=%s，按未提供处理",
                source_parametric_geometry_task_id,
            )
            return ""

        # 校验上游任务状态（如果有 status 字段）
        pg_status = getattr(pg_task, "status", "")
        if pg_status and pg_status != "succeeded":
            logger.warning(
                "上游 parametric_geometry 任务未 SUCCEEDED task_id=%s status=%s，按未提供处理",
                source_parametric_geometry_task_id,
                pg_status,
            )
            return ""

        return op_plan_path

    except Exception as e:
        safe = safe_error_message(e, context="gcode_generation.resolve_upstream_operation_plan")
        logger.warning(
            "查询上游 parametric_geometry 任务异常 source_pg_task_id=%s error_id=%s，按未提供处理",
            source_parametric_geometry_task_id,
            safe.get("error_id"),
        )
        return ""
