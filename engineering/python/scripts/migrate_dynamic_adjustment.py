"""Migrate dynamic_adjustment.py endpoints to @safe_endpoint decorator."""

from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "dynamic_adjustment.py"
content = TARGET.read_text(encoding="utf-8")

# Replace import
content = content.replace(
    "from app.core.safe_errors import safe_error_message\n",
    "from app.core.endpoint_handler import safe_endpoint\n",
)

# Replace each endpoint
replacements = [
    # (old_try_start, new_decorator, old_except_block)
    (
        'async def decide_adjustment(req: DecideRequest):\n    """根据刀具磨损状态生成切削参数调整决策。\n\n    链路：磨损 → ToolWearPredictor 补偿建议 → FeedRateOptimizer 进给优化\n        → 后处理器机床能力限幅 → 决策结果\n    """\n    try:',
        '@safe_endpoint(context="dynamic_adjustment.decide_adjustment", fallback="决策失败")\nasync def decide_adjustment(req: DecideRequest):\n    """根据刀具磨损状态生成切削参数调整决策。\n\n    链路：磨损 → ToolWearPredictor 补偿建议 → FeedRateOptimizer 进给优化\n        → 后处理器机床能力限幅 → 决策结果\n    """',
        '    except Exception as e:\n        safe = safe_error_message(e, context="dynamic_adjustment.decide_adjustment", fallback="决策失败")\n        return error(\n            ErrorCode.INTERNAL_ERROR,\n            message=safe["message"],\n            detail={"error_id": safe["error_id"]},\n        )',
    ),
    (
        'async def rewrite_nc_code(req: RewriteNCRequest):\n    """按调整决策改写 NC 代码中的主轴转速与进给速度。\n\n    仅改写切削进给段（G01/G02/G03）的 F 字段和所有运动段的 S 字段，\n    保留原代码结构与注释。\n    """\n    try:',
        '@safe_endpoint(context="dynamic_adjustment.rewrite_nc_code", fallback="NC改写失败")\nasync def rewrite_nc_code(req: RewriteNCRequest):\n    """按调整决策改写 NC 代码中的主轴转速与进给速度。\n\n    仅改写切削进给段（G01/G02/G03）的 F 字段和所有运动段的 S 字段，\n    保留原代码结构与注释。\n    """',
        '    except Exception as e:\n        safe = safe_error_message(e, context="dynamic_adjustment.rewrite_nc_code", fallback="NC改写失败")\n        return error(\n            ErrorCode.INTERNAL_ERROR,\n            message=safe["message"],\n            detail={"error_id": safe["error_id"]},\n        )',
    ),
    (
        'async def closed_loop_adjustment(req: ClosedLoopRequest):\n    """端到端闭环：磨损检测 → 决策生成 → NC 代码改写。\n\n    合并 decide + rewrite 为单次 API 调用，减少网络延迟。\n    """\n    try:',
        '@safe_endpoint(context="dynamic_adjustment.closed_loop", fallback="闭环调整失败")\nasync def closed_loop_adjustment(req: ClosedLoopRequest):\n    """端到端闭环：磨损检测 → 决策生成 → NC 代码改写。\n\n    合并 decide + rewrite 为单次 API 调用，减少网络延迟。\n    """',
        '    except Exception as e:\n        safe = safe_error_message(e, context="dynamic_adjustment.closed_loop", fallback="闭环调整失败")\n        return error(\n            ErrorCode.INTERNAL_ERROR,\n            message=safe["message"],\n            detail={"error_id": safe["error_id"]},\n        )',
    ),
    (
        'async def calibrate_wear(req: CalibrateWearRequest):\n    """使用实时传感器数据校准模型预测的磨损值。\n\n    使用 EWMA 指数加权移动平均校正 ToolWearPredictor 的输出，\n    减少模型漂移对参数决策的影响。\n    """\n    try:',
        '@safe_endpoint(context="dynamic_adjustment.calibrate_wear", fallback="校准失败")\nasync def calibrate_wear(req: CalibrateWearRequest):\n    """使用实时传感器数据校准模型预测的磨损值。\n\n    使用 EWMA 指数加权移动平均校正 ToolWearPredictor 的输出，\n    减少模型漂移对参数决策的影响。\n    """',
        '    except Exception as e:\n        safe = safe_error_message(e, context="dynamic_adjustment.calibrate_wear", fallback="校准失败")\n        return error(\n            ErrorCode.INTERNAL_ERROR,\n            message=safe["message"],\n            detail={"error_id": safe["error_id"]},\n        )',
    ),
    (
        'async def health_check():\n    """健康检查：验证 Orchestrator 可用。"""\n    try:',
        '@safe_endpoint(context="dynamic_adjustment.health", fallback="健康检查失败")\nasync def health_check():\n    """健康检查：验证 Orchestrator 可用。"""',
        '    except Exception as e:\n        safe = safe_error_message(e, context="dynamic_adjustment.health", fallback="健康检查失败")\n        return error(\n            ErrorCode.INTERNAL_ERROR,\n            message=safe["message"],\n            detail={"error_id": safe["error_id"]},\n        )',
    ),
]

for old_start, new_start, old_except in replacements:
    if old_start in content:
        content = content.replace(old_start, new_start)
        content = content.replace(old_except, "")
    else:
        print(f"WARNING: pattern not found for {old_start[:50]}...")

TARGET.write_text(content, encoding="utf-8")
remaining = content.count("except Exception as e")
print(f"Done. Remaining 'except Exception as e': {remaining}")
print(f"Remaining 'safe_error_message': {content.count('safe_error_message')}")
