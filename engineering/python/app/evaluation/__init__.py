"""统一评分服务（自进化 M0）。

全项目唯一的「好不好」出口：回归门控、成功案例收割、进化报告与
未来的 RL reward 都消费这里的 ``EvalReport``，不得另建评分口径。

用法::

    from app.evaluation import GcodeEvaluationRequest, evaluate_gcode

    report = evaluate_gcode(
        GcodeEvaluationRequest(
            gcode_text=text,
            controller_type="fanuc_0i",
            safe_z=50.0, stock_top_z=5.0,
            stock_length=100.0, stock_width=80.0, stock_height=5.0,
            cutting_params={"spindle_rpm": 6000, "feed_rate": 800, "depth_of_cut": 2.0},
        )
    )
    report.passed            # 硬门禁
    report.composite_score   # 复合分 0-100
    report.failure_classes   # 结构化失败类别
"""

from app.evaluation.service import (
    DEFAULT_WEIGHTS,
    DimensionResult,
    EvalReport,
    EvaluationService,
    GcodeEvaluationRequest,
    evaluate_gcode,
    get_evaluation_service,
    reset_evaluation_service,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "DimensionResult",
    "EvalReport",
    "EvaluationService",
    "GcodeEvaluationRequest",
    "evaluate_gcode",
    "get_evaluation_service",
    "reset_evaluation_service",
]
