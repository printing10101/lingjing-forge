"""World Model API - 世界模型 REST 接口.

对应 ADR-017（世界模型与 RL 模块）第 8 节：世界模型 REST API 端点。

端点总览（prefix: ``/api/v1/world-model``）：
    GET    /versions              列出世界模型版本（分页 + active_only 过滤）
    GET    /versions/{version}    查询版本详情
    POST   /predict               直接预测（不走工作流，调用 WorldModelService.predict）
    POST   /preview               物理预演卡（W6：预测 + 阈值带 + 安全盾裁决 + 主权决策）

权限模型：
    world_model:read   —— 列出版本 / 查询版本详情
    world_model:write  —— 直接预测 / 物理预演（触发模型推理，消耗资源）

设计说明
--------
    - ``GET /versions`` 返回分页结构 ``{items, total, limit, offset}``
    - ``POST /predict`` 不持久化预测结果（按需生成，避免大数组膨胀数据库），
      前端如需保存轨迹可走工作流 ``wm_predict_state`` 任务类型
    - 预测端点为同步执行（horizon ≤ 100，单次 < 2s）
    - 服务层异常通过 ``_handle_service_exception`` 统一映射为 API 错误响应
    - ``POST /preview`` 是确认页「物理预演卡」的数据源：在用户确认前展示
      AI 对物理结果的预判（颤振/磨损/粗糙度 vs 阈值带），把「敢上机」从
      事后闸门延伸到事前预演
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.dependencies import get_world_model_service
from app.dependencies import get_sovereignty_policy
from app.contracts.world_model import (
    DEFAULT_HORIZON,
    InvalidStateError,
    MAX_HORIZON,
    MIN_HORIZON,
    ModelNotFoundError,
    PredictionError,
    WorldModelError,
)
from app.plugins.rl_agent.safety_shield import SafetyShield

logger = logging.getLogger(__name__)

# 注意：本 router 尚未接入（main/router_registry 未引用本文件），路由未挂载。
router = APIRouter(prefix="/api/v1/world-model", tags=["World Model"])


# Pydantic 请求模型


class WorldModelPredictRequest(BaseModel):
    """世界模型预测请求体.

    与 ``app.contracts.world_model.WorldModelPredictRequest`` 对齐，
    但使用 Pydantic 以获得自动校验和 OpenAPI 文档。
    """

    current_state: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "当前加工状态（字段名见 StateField，至少包含全部 8 个状态字段）。"
            "融合模式下可为空（由 unified_state 提供状态信息）"
        ),
    )
    candidate_action: dict[str, float] = Field(
        ...,
        description="候选切削参数调整量（字段名见 ActionField，4 个 delta 字段）",
    )
    horizon: int = Field(
        default=DEFAULT_HORIZON,
        ge=MIN_HORIZON,
        le=MAX_HORIZON,
        description=f"预测步长（{MIN_HORIZON}~{MAX_HORIZON}，默认 {DEFAULT_HORIZON}）",
    )
    model_uri: str = Field(
        default="model://world_model/1.0.0",
        min_length=1,
        max_length=256,
        description="世界模型 URI",
    )
    unified_state: dict[str, Any] | None = Field(
        default=None,
        description=(
            "ADR-020 思路 1 融合模式可选输入。包含几何特征（ADR-007）与"
            "动力学状态（ADR-013）的统一状态字典。提供时走融合路径"
            "（GeometryEncoder/DynamicsEncoder/FusionLayer）。"
            "为 None 时走原始 state_dim 字段拼接路径（向后兼容）。"
            "需配合环境变量 WORLD_MODEL_USE_FUSION=true 使用"
        ),
    )


# 辅助函数


def _handle_service_exception(e: Exception, *, action: str):
    """统一处理服务层异常 → API 错误响应.

    风格与 explainability.py 对齐。

    Args:
        e: 服务层抛出的异常
        action: 当前操作描述（用于日志）

    Returns:
        error() 响应对象
    """
    if isinstance(e, ModelNotFoundError):
        return error(
            code=ErrorCode.NOT_FOUND,
            message=str(e),
            suggestion="请确认版本号正确，或通过 GET /versions 查看可用版本",
        )
    if isinstance(e, InvalidStateError):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="请检查 current_state / candidate_action 字段是否完整且为合法数值",
        )
    if isinstance(e, PredictionError):
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=str(e),
            suggestion="预测失败：模型权重可能未加载或维度不匹配，请检查 model_uri",
        )
    if isinstance(e, ValueError):
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    if isinstance(e, WorldModelError):
        logger.error("WorldModel error during %s: %s", action, e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    # 兜底：未识别的异常
    logger.error("Unexpected error during %s: %s", action, e, exc_info=True)
    return error(
        code=ErrorCode.INTERNAL_ERROR,
        message=f"{action} 失败",
        detail=str(e),
    )


# 端点 1: GET /versions —— 列出世界模型版本


@router.get("/versions")
async def list_versions(
    active_only: bool = Query(False, description="为 true 时仅返回当前激活版本"),
    limit: int = Query(50, ge=1, le=500, description="每页数量（1-500，默认 50）"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """分页列出世界模型版本.

    返回字段：
        - items: list[dict]（每个版本记录的 to_dict()）
        - total / limit / offset

    权限：``world_model:read``
    """
    service = get_world_model_service()
    try:
        versions, total = await service.list_versions(
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        return _handle_service_exception(e, action="列出世界模型版本")

    items = [v.to_dict() for v in versions]
    return success(
        data={
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        message=f"世界模型版本列表已获取（{len(items)} 条）",
    )


# 端点 2: GET /versions/{version} —— 查询版本详情


@router.get("/versions/{version}")
async def get_version(version: str):
    """查询世界模型版本详情.

    权限：``world_model:read``
    """
    service = get_world_model_service()
    try:
        version_record = await service.get_version(version)
    except Exception as e:
        return _handle_service_exception(e, action="查询世界模型版本详情")

    return success(
        data=version_record.to_dict(),
        message="世界模型版本详情已获取",
    )


# 端点 3: POST /predict —— 直接预测（不走工作流）


@router.post(
    "/predict",
    dependencies=[Depends(require_permission("world_model:write"))],
)
async def predict(request: WorldModelPredictRequest):
    """执行世界模型轨迹预测（不走工作流，直接调用服务层）.

    流程：
        1. Pydantic 自动校验 horizon 范围 / 字段非空
        2. 调用 ``WorldModelService.predict()`` 执行轨迹预测
        3. 返回结构化响应（含 predicted_trajectory / trajectory_metrics / model_info）

    权限：``world_model:write``（触发模型推理，消耗资源）
    """
    service = get_world_model_service()

    # 构造契约层 dataclass（再次校验，与 Pydantic 互补）
    from app.contracts.world_model import WorldModelPredictRequest as _ContractReq

    try:
        contract_req = _ContractReq(
            current_state=request.current_state,
            candidate_action=request.candidate_action,
            horizon=request.horizon,
            model_uri=request.model_uri,
            unified_state=request.unified_state,
        )
    except ValueError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="请检查 current_state / candidate_action / unified_state 字段",
        )

    try:
        response = await service.predict(contract_req)
    except Exception as e:
        return _handle_service_exception(e, action="世界模型预测")

    payload = response.to_dict()
    return success(
        data=payload,
        message=(
            f"世界模型预测完成: horizon={request.horizon}，轨迹步数={len(payload.get('predicted_trajectory', []))}"
        ),
    )


# 端点 4: POST /preview —— 物理预演卡（W6：预测 + 阈值带 + 安全盾 + 主权）


class WorldModelPreviewRequest(BaseModel):
    """物理预演请求体（与 /predict 同构，另加安全盾开关）."""

    current_state: dict[str, float] = Field(..., description="当前加工状态（字段名见 StateField）")
    candidate_action: dict[str, float] = Field(..., description="候选切削参数调整量（ActionField delta，[-1, 1]）")
    horizon: int = Field(DEFAULT_HORIZON, ge=MIN_HORIZON, le=MAX_HORIZON)
    unified_state: dict[str, Any] | None = Field(None, description="ADR-020 融合模式可选输入")
    apply_shield: bool = Field(True, description="是否经 SafetyShield 过滤（默认开启，仅供测试关闭）")


# 物理阈值带（全部引用仓库既有判据，不发明新数值）：
# - 颤振概率：0.3 取自 rl_agent 契约层 max_chatter_probability（动作评估阈值），
#   0.5 取自训练奖励层 chatter_critical_threshold；
# - 刀具磨损：0.2mm 为仓库刀具磨损预测的 VB 判据（tool_wear curve_predictor）。
_CHATTER_SAFE_BELOW = 0.3
_CHATTER_DANGER_AT = 0.5
_TOOL_WEAR_DANGER_AT = 0.2
_MODEL_CONFIDENCE_FLOOR = 0.5


@router.post(
    "/preview",
    dependencies=[Depends(require_permission("world_model:write"))],
)
async def preview(request: WorldModelPreviewRequest):
    """物理预演卡：确认前展示 AI 对物理结果的预判。

    流程：
        1. WorldModelService.predict 轨迹预测（颤振/磨损/粗糙度/置信度）
        2. SafetyShield 硬约束过滤候选动作（物理边界，任何自主等级不放行违规）
        3. 主权保守缩放（等级越低动作越保守，W6b）
        4. 主权决策（该推荐能否被自动应用）

    返回卡片数据：verdict（safe/warning/danger）+ 指标 vs 阈值带 + 轨迹 +
    护盾裁决 + 最终动作 + 主权决策。
    """
    from app.contracts.world_model import ActionField, WorldModelPredictRequest as _ContractReq

    service = get_world_model_service()
    try:
        contract_req = _ContractReq(
            current_state=request.current_state,
            candidate_action=request.candidate_action,
            horizon=request.horizon,
            unified_state=request.unified_state,
        )
        response = await service.predict(contract_req)
    except (ModelNotFoundError, PredictionError, InvalidStateError, WorldModelError) as e:
        return _handle_service_exception(e, action="物理预演")

    metrics = response.trajectory_metrics.to_dict()
    steps = [s.to_dict() for s in response.predicted_trajectory]
    confidence_values = [float(s.get("confidence", 0.0)) for s in steps]
    confidence_mean = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0

    # 安全盾：物理边界硬约束。预演评估的是单个候选动作（非在线动作序列），
    # 因此 prev_action 取候选自身——只做绝对物理边界校验，序列变化率限制
    # 属于在线执行路径（RLAgentService.act），不应在此误伤候选动作。
    action_keys = ActionField.all()
    raw_vec = np.array([float(request.candidate_action.get(k, 0.0)) for k in action_keys], dtype=np.float32)
    shield = SafetyShield()
    _safe_vec, shield_result = shield.filter(raw_vec, prev_action=raw_vec)
    safe_action = {k: round(float(v), 6) for k, v in zip(action_keys, shield_result.action, strict=True)}

    # W6b 主权保守缩放：等级越低，调整越向当前参数收敛
    sovereignty_engine = get_sovereignty_policy()
    autonomy_level = sovereignty_engine.get_settings().ai_autonomy_level
    from app.services.sovereignty import apply_conservatism

    final_action, conservatism_info = apply_conservatism(safe_action, autonomy_level)

    # 主权决策：轨迹平均置信度作为决策置信度
    decision = sovereignty_engine.evaluate_action("agent_action", confidence=confidence_mean)

    max_chatter = float(metrics.get("max_chatter_probability", 0.0))
    cumulative_wear = float(metrics.get("cumulative_tool_wear", 0.0))

    reasons: list[str] = []
    verdict = "safe"
    if shield_result.violated:
        verdict = "danger"
        reasons.append("安全盾拦截：" + "、".join(shield_result.violations))
    if max_chatter >= _CHATTER_DANGER_AT:
        verdict = "danger"
        reasons.append(f"最大颤振概率 {max_chatter:.2f} ≥ 危险线 {_CHATTER_DANGER_AT}")
    elif max_chatter >= _CHATTER_SAFE_BELOW and verdict != "danger":
        verdict = "warning"
        reasons.append(f"最大颤振概率 {max_chatter:.2f} 超出安全线 {_CHATTER_SAFE_BELOW}")
    if cumulative_wear >= _TOOL_WEAR_DANGER_AT and verdict != "danger":
        verdict = "warning"
        reasons.append(f"轨迹累计磨损 {cumulative_wear:.3f}mm 达到 VB 判据 {_TOOL_WEAR_DANGER_AT}mm")
    if confidence_mean < _MODEL_CONFIDENCE_FLOOR and verdict == "safe":
        verdict = "warning"
        reasons.append(f"模型平均置信度偏低（{confidence_mean:.2f}），预演结果仅供参考")

    return success(
        data={
            "verdict": verdict,
            "reasons": reasons,
            "metrics": {
                "max_chatter_probability": max_chatter,
                "mean_chatter_probability": float(metrics.get("mean_chatter_probability", 0.0)),
                "cumulative_tool_wear": cumulative_wear,
                "final_surface_roughness": float(metrics.get("final_surface_roughness", 0.0)),
                "confidence_mean": round(confidence_mean, 4),
            },
            "bands": {
                "chatter_probability": {"safe_below": _CHATTER_SAFE_BELOW, "danger_at": _CHATTER_DANGER_AT},
                "tool_wear": {"danger_at": _TOOL_WEAR_DANGER_AT},
                "model_confidence": {"floor": _MODEL_CONFIDENCE_FLOOR},
            },
            "trajectory": steps,
            "shield": {
                **shield_result.to_dict(),
                "note": "SafetyShield 为物理硬约束，任何自主等级下均不放行违规动作",
            },
            "conservatism": conservatism_info,
            "final_action": final_action,
            "sovereignty": decision.to_dict(),
            "model_info": response.model_info.to_dict(),
        },
        message=f"物理预演完成：{verdict}",
    )


__all__ = ["router"]
