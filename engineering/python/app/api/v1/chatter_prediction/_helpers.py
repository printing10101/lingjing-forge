"""颤振预测路由辅助函数（从 routes.py 抽取，D5 God 模块拆分）。

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
from app.chatter_prediction import (
    ChatterPredictionPipeline,
    ChatterPredictionTask,
    PredictionMethod,
    build_chatter_disclaimer,
    check_ltc_model_available,
)

logger = logging.getLogger(__name__)

# 后台任务引用集合（C5 修复：asyncio.create_task 不保存引用会被 GC 回收）
_background_tasks: set = set()


def _spawn(coro):
    """启动后台任务并保存引用，避免被 Python GC 回收。"""
    t = asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)
    return t


_pipeline: ChatterPredictionPipeline | None = None


def _get_pipeline() -> ChatterPredictionPipeline:
    """获取 pipeline 单例。"""
    global _pipeline
    if _pipeline is None:
        _pipeline = ChatterPredictionPipeline(cfg=config.chatter_prediction)
    return _pipeline


def _disclaimer_dict(
    task: ChatterPredictionTask | None = None,
    chatter_report_ready: bool = False,
) -> dict[str, Any]:
    """构造精度告知字段。

    优先用 task 上下文构造（覆盖 mesh / 材料校准状态 / LTC 实际参与比例）；
    无 task 时返回通用默认值（用于 precision_info 端点）。
    """
    if task is not None:
        # HRC52 检测：material_id 命中 PENDING_CALIBRATION_MATERIALS 时强制 pending
        from app.chatter_prediction.predictor_adapter import (
            PENDING_CALIBRATION_MATERIALS,
        )

        material_id_lower = task.material_id.lower()
        material_calibration_status = (
            "pending_calibration" if material_id_lower in PENDING_CALIBRATION_MATERIALS else "calibrated"
        )

        # 根据 feature_results 统计预测方法分布 + LTC 实际参与比例
        if task.feature_results:
            analytical_count = sum(1 for r in task.feature_results if r.method == PredictionMethod.ANALYTICAL.value)
            nn_count = sum(1 for r in task.feature_results if r.method == PredictionMethod.NEURAL_NETWORK.value)
            fb_count = sum(1 for r in task.feature_results if r.method == PredictionMethod.FALLBACK.value)
            if fb_count > 0:
                prediction_method = "fallback"
            elif nn_count > 0 and analytical_count > 0:
                prediction_method = "mixed"
            elif nn_count > 0:
                prediction_method = "neural_network"
            else:
                prediction_method = "analytical"
            ltc_active_ratio = sum(1 for r in task.feature_results if r.ltc_active) / len(task.feature_results)
        else:
            prediction_method = "analytical"
            ltc_active_ratio = 0.0

        return build_chatter_disclaimer(
            mesh_calibrated=task.mesh_calibrated,
            chatter_params_source=task.chatter_params_path,
            material_id=task.material_id,
            material_calibration_status=material_calibration_status,
            precision_tier=task.precision_tier,
            machine_type=task.machine_type,
            prediction_method=prediction_method,
            ltc_model_available=task.ltc_model_available,
            ltc_active_ratio=ltc_active_ratio,
            chatter_report_ready=chatter_report_ready,
        ).to_dict()

    # 无 task 上下文（precision_info 端点默认值）
    ltc_available = check_ltc_model_available()
    return build_chatter_disclaimer(
        mesh_calibrated=config.chatter_prediction.default_mesh_calibrated,
        chatter_params_source="external_upload",
        material_id="unknown",
        material_calibration_status="pending_calibration",
        precision_tier=config.chatter_prediction.precision_tier,
        machine_type=config.chatter_prediction.default_machine_type,
        prediction_method="analytical" if not ltc_available else "mixed",
        ltc_model_available=ltc_available,
        ltc_active_ratio=0.0,
        chatter_report_ready=False,
    ).to_dict()


def _resolve_upstream_calibrated(
    source_cutting_parameters_task_id: str,
) -> tuple[bool, str, str]:
    """从上游阶段 4 任务追溯 mesh 标定状态 + ChatterParams 路径 + 材料 ID。

    精度继承链：阶段 1 image_to_3d → 阶段 2 feature_extraction
              → 阶段 3 parametric_geometry → 阶段 4 cutting_parameters
              → 阶段 5 chatter_prediction（本模块）
    本方法查询阶段 4 任务的 mesh_calibrated / chatter_params_path / material_id，
    避免精度信息断层。仅 SUCCEEDED 状态的阶段 4 任务才被认为是可信来源。

    Returns:
        (calibrated, chatter_params_path, material_id)
        - 上游任务存在且为 SUCCEEDED：(task.mesh_calibrated, chatter_params_path, material_id)
        - 上游任务不存在 / 未完成：(False, "", "")，并记日志
    """
    if not source_cutting_parameters_task_id:
        return False, "", ""

    try:
        from app.cutting_parameters import (
            get_task_store as get_cp_store,
            CuttingParametersTaskStatus,
        )
    except ImportError:
        logger.warning(
            "cutting_parameters 模块未启用，无法追溯上游 mesh_calibrated 状态 source_cp_task_id=%s，按未标定处理",
            source_cutting_parameters_task_id,
        )
        return False, "", ""

    try:
        cp_task = get_cp_store().get_task(source_cutting_parameters_task_id)
        if cp_task is None:
            logger.warning(
                "上游 cutting_parameters 任务不存在 task_id=%s，按未标定处理",
                source_cutting_parameters_task_id,
            )
            return False, "", ""

        if cp_task.status != CuttingParametersTaskStatus.SUCCEEDED.value:
            logger.warning(
                "上游 cutting_parameters 任务未 SUCCEEDED task_id=%s status=%s，按未标定处理",
                source_cutting_parameters_task_id,
                cp_task.status,
            )
            return False, "", ""

        return (
            bool(cp_task.mesh_calibrated),
            cp_task.chatter_params_path,
            cp_task.material_id,
        )

    except Exception as e:
        safe = safe_error_message(e, context="chatter_prediction.resolve_upstream_calibrated")
        logger.warning(
            "查询上游任务异常 source_cp_task_id=%s error_id=%s，按未标定处理",
            source_cutting_parameters_task_id,
            safe.get("error_id"),
        )
        return False, "", ""
