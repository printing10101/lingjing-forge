"""刀路动态调参闭环 API。

落地竞品分析中识别的 MachineMetrics / 工业 CNC 监控系统补强点：
基于实时刀具磨损状态，动态调整切削参数并改写 NC 代码。

端点前缀：/api/v1/dynamic-adjustment

端点列表：
1. POST /decide           根据磨损状态生成参数调整决策（含机床能力限幅）
2. POST /rewrite-nc       按决策改写 NC 代码中的主轴转速 / 进给速度
3. POST /closed-loop      端到端闭环：磨损 → 决策 → NC 改写（单次调用）
4. POST /calibrate-wear   使用实时传感器数据 EWMA 校正磨损预测
5. GET  /health           健康检查
"""

# 注意：不可加 from __future__ import annotations！
# 该 import + @safe_endpoint 装饰器 + 本地 Pydantic 模型参数 Pydantic 前向引用
# 解析失败（PydanticUndefinedAnnotation: name 'X' is not defined），详见运维手册。

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.response import success, error, ErrorCode
from app.core.endpoint_handler import safe_endpoint
from app.core.safe_errors import safe_error_message  # rewrite_nc_code 内层 try/except 仍需要
from app.auth.permissions import require_permission
from app.toolpath.dynamic_adjustment import (
    CurrentParameters,
    WearState,
    get_dynamic_adjustment_orchestrator,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/dynamic-adjustment",
    tags=["DynamicAdjustment"],
    dependencies=[Depends(require_permission("adjust:read"))],
)


# 请求 / 响应模型


class WearStateRequest(BaseModel):
    """刀具磨损状态请求。"""

    tool_id: int = Field(..., description="刀具 ID")
    wear_amount: float = Field(..., ge=0.0, description="当前磨损量 VB (mm)")
    usage_time: float = Field(..., ge=0.0, description="累计使用时间 (分钟)")
    wear_threshold: float = Field(default=0.3, gt=0.0, description="更换阈值 (mm)")
    material_type: str = Field(default="steel_45", description="材料类型")
    tool_type: str = Field(default="carbide", description="刀具类型")
    tool_diameter: float = Field(default=10.0, gt=0.0, description="刀具直径 (mm)")
    flute_count: int = Field(default=2, ge=1, description="齿数")


class CurrentParametersRequest(BaseModel):
    """当前切削参数请求。"""

    cutting_speed: float = Field(..., gt=0.0, description="切削速度 (m/min)")
    feed_rate: float = Field(..., gt=0.0, description="每转进给 (mm/rev)")
    depth_of_cut: float = Field(..., gt=0.0, description="轴向切深 ap (mm)")
    width_of_cut: float = Field(default=0.0, ge=0.0, description="径向切深 ae (mm)")
    spindle_rpm: float | None = Field(default=None, ge=0.0, description="主轴转速 (RPM, None 时由切削速度反算)")
    coolant_flow: float = Field(default=10.0, ge=0.0, description="冷却液流量 (L/min)")


class MachineCapabilities(BaseModel):
    """机床能力上限。"""

    max_spindle_speed: float | None = Field(default=None, ge=0.0, description="最大主轴转速 (RPM)")
    max_feed_rate: float | None = Field(default=None, ge=0.0, description="最大进给速度 (mm/min)")
    max_power: float | None = Field(default=None, ge=0.0, description="最大功率 (kW)")
    max_torque: float | None = Field(default=None, ge=0.0, description="最大扭矩 (N·m)")


class CalibrationInput(BaseModel):
    """实时磨损校正入参（启用「实时信号 → 磨损模型在线校正 → 决策」闭环）。

    与 ToolWearPredictor.calibrate_with_real_time_data 对齐。
    schema 与 SignalFusionKnowledgeBase.SignalSample.sensor_features 完全兼容。
    """

    real_time_wear: float = Field(..., ge=0.0, description="实测磨损量 (mm)")
    sensor_features: dict[str, float] = Field(
        ...,
        description=(
            "传感器特征字典，支持 vibration_rms (g) / cutting_force (N) / temperature (°C) / acoustic_emission 等字段"
        ),
    )
    elapsed_time: float = Field(..., gt=0.0, description="自上次校正以来的加工时间 (min)")


class DecideRequest(BaseModel):
    """参数调整决策请求。"""

    wear: WearStateRequest
    current: CurrentParametersRequest
    machine_capabilities: MachineCapabilities | None = Field(default=None, description="机床能力上限（None 使用默认）")
    optimization_goal: str = Field(
        default="tool_life",
        description="优化目标：efficiency / tool_life / surface_finish",
    )
    calibration: CalibrationInput | None = Field(
        default=None,
        description=("可选实时校正入参。提供时启用 EWMA 校正闭环，用校正后磨损值驱动决策；未提供时走原始磨损值路径"),
    )


class AdjustmentDecisionInput(BaseModel):
    """P2-批次2 修复：``RewriteNCRequest.decision`` 的强类型替代裸 dict。

    原 ``decision: dict[str, Any]`` 允许任意键穿透到 ``AdjustmentDecision``
    构造，存在字段缺失/类型错误仅在运行时暴露的风险。改为 Pydantic 子模型
    后，请求体在进入端点前即完成结构与类型校验。
    """

    strategy: Literal[
        "no_adjustment",
        "slight_compensation",
        "moderate_compensation",
        "aggressive_compensation",
        "replace_tool",
    ] = "no_adjustment"
    urgency: Literal["normal", "warning", "critical"] = "normal"
    new_cutting_speed: float = 0.0
    new_feed_rate: float = 0.0
    new_depth_of_cut: float = 0.0
    new_spindle_rpm: float = 0.0
    new_feed_rate_mm_min: float = 0.0
    life_extension_pct: float = 0.0
    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list)


class RewriteNCRequest(BaseModel):
    """NC 代码改写请求。"""

    nc_code: str = Field(..., min_length=1, description="NC/G 代码文本")
    # P2-批次2 修复：裸 dict 改为强类型子模型，强制校验字段类型与枚举值。
    decision: AdjustmentDecisionInput = Field(..., description="由 /decide 返回的决策对象")
    controller_type: str = Field(default="fanuc", description="控制器方言 (fanuc/siemens/heidenhain)")
    apply_to_motion_only: bool = Field(default=True, description="仅改写切削进给段（G01/G02/G03），跳过 G00")


class ClosedLoopRequest(BaseModel):
    """端到端闭环请求。"""

    wear: WearStateRequest
    current: CurrentParametersRequest
    nc_code: str = Field(..., min_length=1, description="待改写的 NC/G 代码文本")
    machine_capabilities: MachineCapabilities | None = Field(default=None)
    optimization_goal: str = Field(default="tool_life")
    controller_type: str = Field(default="fanuc")
    apply_to_motion_only: bool = Field(default=True)
    calibration: CalibrationInput | None = Field(
        default=None,
        description=("可选实时校正入参。提供时启用 EWMA 校正闭环，用校正后磨损值驱动决策与 NC 改写"),
    )


class CalibrateWearRequest(BaseModel):
    """实时磨损校正请求。"""

    real_time_wear: float = Field(..., ge=0.0, description="实测磨损量 (mm)")
    sensor_features: dict[str, float] = Field(
        ..., description="传感器特征（vibration_rms / cutting_force / temperature 等）"
    )
    elapsed_time: float = Field(..., gt=0.0, description="自上次校正以来的时间 (分钟)")
    input_parameters: dict[str, Any] = Field(
        ...,
        description="当前切削参数（cutting_speed/feed_rate/depth_of_cut/material_type/tool_type/tool_diameter/current_wear）",
    )


# 辅助函数


def _to_wear_state(req: WearStateRequest) -> WearState:
    return WearState(
        tool_id=req.tool_id,
        wear_amount=req.wear_amount,
        usage_time=req.usage_time,
        wear_threshold=req.wear_threshold,
        material_type=req.material_type,
        tool_type=req.tool_type,
        tool_diameter=req.tool_diameter,
        flute_count=req.flute_count,
    )


def _to_current_params(req: CurrentParametersRequest) -> CurrentParameters:
    return CurrentParameters(
        cutting_speed=req.cutting_speed,
        feed_rate=req.feed_rate,
        depth_of_cut=req.depth_of_cut,
        width_of_cut=req.width_of_cut,
        spindle_rpm=req.spindle_rpm,
        coolant_flow=req.coolant_flow,
    )


def _machine_caps_to_dict(
    caps: MachineCapabilities | None,
) -> dict[str, float] | None:
    if caps is None:
        return None
    result: dict[str, float] = {}
    if caps.max_spindle_speed is not None:
        result["max_spindle_speed"] = caps.max_spindle_speed
    if caps.max_feed_rate is not None:
        result["max_feed_rate"] = caps.max_feed_rate
    if caps.max_power is not None:
        result["max_power"] = caps.max_power
    if caps.max_torque is not None:
        result["max_torque"] = caps.max_torque
    return result or None


# 1. 参数调整决策


@router.post("/decide", dependencies=[Depends(require_permission("adjust:read"))])
@safe_endpoint(context="dynamic_adjustment.decide_adjustment", fallback="决策失败")
async def decide_adjustment(req: DecideRequest):
    """根据刀具磨损状态生成切削参数调整决策。

    链路：磨损 → ToolWearPredictor 补偿建议 → FeedRateOptimizer 进给优化
        → 后处理器机床能力限幅 → 决策结果
    """
    orchestrator = get_dynamic_adjustment_orchestrator()
    wear = _to_wear_state(req.wear)
    current = _to_current_params(req.current)
    caps = _machine_caps_to_dict(req.machine_capabilities)

    # 集成点 1：可选实时校正入参 EWMA 校正闭环
    calibration_kwargs: dict[str, Any] = {}
    if req.calibration is not None:
        calibration_kwargs = {
            "real_time_wear": req.calibration.real_time_wear,
            "sensor_features": req.calibration.sensor_features,
            "elapsed_time": req.calibration.elapsed_time,
        }

    decision = orchestrator.decide_adjustment(
        wear=wear,
        current=current,
        machine_capabilities=caps,
        optimization_goal=req.optimization_goal,
        **calibration_kwargs,
    )

    return success(
        data={
            "decision": decision.to_dict(),
            "wear_state": {
                "tool_id": wear.tool_id,
                "wear_amount": wear.wear_amount,
                "wear_ratio": wear.wear_ratio,
                "tool_wear_factor": wear.tool_wear_factor,
            },
            "original_parameters": {
                "cutting_speed": current.cutting_speed,
                "feed_rate": current.feed_rate,
                "depth_of_cut": current.depth_of_cut,
            },
            "calibration_enabled": bool(calibration_kwargs),
        },
        message=f"调整策略: {decision.strategy}（紧急度: {decision.urgency}）",
    )


# 2. NC 代码改写


@router.post("/rewrite-nc", dependencies=[Depends(require_permission("adjust:write"))])
@safe_endpoint(context="dynamic_adjustment.rewrite_nc_code", fallback="NC改写失败")
async def rewrite_nc_code(req: RewriteNCRequest):
    """按调整决策改写 NC 代码中的主轴转速与进给速度。

    仅改写切削进给段（G01/G02/G03）的 F 字段和所有运动段的 S 字段，
    保留原代码结构与注释。
    """
    orchestrator = get_dynamic_adjustment_orchestrator()

    # P2-批次2 修复：decision 现在是 AdjustmentDecisionInput 强类型模型，
    # 无需从 dict 重建，直接属性访问即可。Pydantic 已完成类型校验。
    from app.toolpath.dynamic_adjustment import AdjustmentDecision

    try:
        decision = AdjustmentDecision(
            strategy=req.decision.strategy,
            urgency=req.decision.urgency,
            new_cutting_speed=req.decision.new_cutting_speed,
            new_feed_rate=req.decision.new_feed_rate,
            new_depth_of_cut=req.decision.new_depth_of_cut,
            new_spindle_rpm=req.decision.new_spindle_rpm,
            new_feed_rate_mm_min=req.decision.new_feed_rate_mm_min,
            life_extension_pct=req.decision.life_extension_pct,
            suggestions=req.decision.suggestions,
            warnings=req.decision.warnings,
            reasoning=req.decision.reasoning,
        )
    except (KeyError, ValueError, TypeError) as e:
        safe = safe_error_message(e, context="dynamic_adjustment.rewrite_nc_code.decision", fallback="决策字段类型错误")
        return error(
            ErrorCode.INVALID_REQUEST,
            message=safe["message"],
            detail={"error_id": safe["error_id"]},
        )

    result = orchestrator.rewrite_nc_code(
        gcode_text=req.nc_code,
        decision=decision,
        controller_type=req.controller_type,
        apply_to_motion_only=req.apply_to_motion_only,
    )

    return success(
        data=result.to_dict(),
        message=f"已改写 {result.segments_adjusted}/{result.segments_total} 段",
    )


# 3. 端到端闭环


@router.post("/closed-loop", dependencies=[Depends(require_permission("adjust:write"))])
@safe_endpoint(context="dynamic_adjustment.closed_loop_adjustment", fallback="闭环失败")
async def closed_loop_adjustment(req: ClosedLoopRequest):
    """端到端闭环：磨损 → 决策 → NC 改写（单次调用完成全链路）。

    适用于在线监测系统直接触发闭环调整的场景。
    """
    orchestrator = get_dynamic_adjustment_orchestrator()
    wear = _to_wear_state(req.wear)
    current = _to_current_params(req.current)
    caps = _machine_caps_to_dict(req.machine_capabilities)

    # 集成点 1：可选实时校正入参 EWMA 校正闭环
    calibration_kwargs: dict[str, Any] = {}
    if req.calibration is not None:
        calibration_kwargs = {
            "real_time_wear": req.calibration.real_time_wear,
            "sensor_features": req.calibration.sensor_features,
            "elapsed_time": req.calibration.elapsed_time,
        }

    # 决策（含可选 EWMA 校正）
    decision = orchestrator.decide_adjustment(
        wear=wear,
        current=current,
        machine_capabilities=caps,
        optimization_goal=req.optimization_goal,
        **calibration_kwargs,
    )

    # NC 改写
    rewrite = orchestrator.rewrite_nc_code(
        gcode_text=req.nc_code,
        decision=decision,
        controller_type=req.controller_type,
        apply_to_motion_only=req.apply_to_motion_only,
    )

    return success(
        data={
            "decision": decision.to_dict(),
            "rewrite": rewrite.to_dict(),
            "wear_state": {
                "tool_id": wear.tool_id,
                "wear_amount": wear.wear_amount,
                "wear_ratio": wear.wear_ratio,
                "tool_wear_factor": wear.tool_wear_factor,
            },
            "calibration_enabled": bool(calibration_kwargs),
        },
        message=(f"闭环完成：策略={decision.strategy}, 改写={rewrite.segments_adjusted}/{rewrite.segments_total} 段"),
    )


# 4. 实时磨损校正


@router.post("/calibrate-wear", dependencies=[Depends(require_permission("adjust:write"))])
@safe_endpoint(context="dynamic_adjustment.calibrate_wear", fallback="校准失败")
async def calibrate_wear(req: CalibrateWearRequest):
    """使用实时传感器数据 EWMA 校正磨损预测。

    链路：实测磨损 + 传感器特征 → ToolWearPredictor.calibrate_with_real_time_data
        → 校正后的磨损值 + 不确定度
    """
    orchestrator = get_dynamic_adjustment_orchestrator()
    result = orchestrator.wear_predictor.calibrate_with_real_time_data(
        real_time_wear=req.real_time_wear,
        sensor_features=req.sensor_features,
        elapsed_time=req.elapsed_time,
        input_parameters=req.input_parameters,
    )
    return success(
        data=result,
        message="磨损预测已基于实时数据 EWMA 校正",
    )


# 5. 健康检查


@router.get("/health")
@safe_endpoint(context="dynamic_adjustment.health", fallback="健康检查失败")
async def health():
    """动态调参闭环模块健康检查。"""
    orchestrator = get_dynamic_adjustment_orchestrator()
    # 探测关键依赖是否可用
    from app.postprocessor.registry import PostProcessorRegistry

    registry = PostProcessorRegistry()
    controllers = registry.list_controllers()
    return success(
        data={
            "module": "dynamic_adjustment",
            "status": "ok",
            "available_controllers": controllers,
            "wear_predictor": type(orchestrator.wear_predictor).__name__,
            "feed_optimizer": type(orchestrator.feed_optimizer).__name__,
        }
    )
