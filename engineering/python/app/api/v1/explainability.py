"""Explainability API - 可解释性可视化 REST 接口.

对应 ADR-016 阶段 7 p7-4：可解释性可视化。

端点总览（prefix: ``/api/v1/explainability``）：
    POST   /hidden-state                 生成隐状态投影解释（PCA/t-SNE/UMAP 降维）
    POST   /gate-dynamics                生成门控动力学解释（LTC dt 门控值时序 + 异常帧检测）
    POST   /counterfactual               生成反事实解释（单特征扰动 + 敏感度扫描）
    POST   /confidence                   生成置信度分布解释（MC dropout 多次采样）
    GET    /                             列出历史解释记录（分页 + 类型/模型过滤）
    GET    /{explanation_id}             查询解释详情（可选 ?include_payload=true 加载 payload）
    DELETE /{explanation_id}             删除解释记录（同时删除 payload 文件）
    POST   /compare                      对比两个解释（生成差异 payload）

权限模型：
    explainability:read   —— 查询/列表/对比、获取详情
    explainability:write  —— 生成解释、删除解释

设计说明
--------
    - 4 个生成端点为同步执行（解释生成通常 <5s），服务层在执行完成后
      写入 ExplanationRecord，前端通过返回的 record.id 后续轮询详情或下载 payload。
    - payload（含大型数组）以 JSON 文件存盘，``GET /{id}`` 默认仅返回元数据，
      前端按需通过 ``?include_payload=true`` 加载完整 payload 内容。
    - 对比端点要求两条解释 ``explanation_type`` 一致，否则抛
      ``ComparisonMismatchError``。
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.dependencies import get_explainability_service
from app.contracts.explainability import (
    ComparisonMismatchError,
    ComparisonType,
    ExplainabilityError,
    ExplanationLookupError,
    ExplanationType,
    ExplanationValidationError,
    ProjectionError,
    ProjectionMethod,
    SamplingError,
)

logger = logging.getLogger(__name__)

# 注意：本 router 尚未接入（main/router_registry 未引用本文件），路由未挂载。
router = APIRouter(prefix="/api/v1/explainability", tags=["Explainability"])


# Pydantic 请求模型


class GenerateHiddenStateRequest(BaseModel):
    """生成隐状态投影解释请求体."""

    model_uri: str = Field(..., min_length=1, max_length=256, description="模型 URI")
    source_snapshot_id: str | None = Field(default=None, max_length=64, description="关联实验快照 ID（可选）")
    projection_method: str = Field(
        default=ProjectionMethod.PCA,
        description=f"降维方法（{ProjectionMethod.all()}，默认 pca）",
    )
    projection_dim: int = Field(default=2, ge=2, le=3, description="投影维度（2 或 3，默认 2）")
    max_frames: int = Field(default=1000, ge=1, le=10000, description="最大帧数（超过则均匀采样）")
    created_by: str | None = Field(default=None, max_length=128, description="创建者（user_id 或 plugin_id）")


class GenerateGateDynamicsRequest(BaseModel):
    """生成门控动力学解释请求体."""

    model_uri: str = Field(..., min_length=1, max_length=256, description="模型 URI")
    source_snapshot_id: str | None = Field(default=None, max_length=64, description="关联实验快照 ID（可选）")
    anomaly_sigma: float = Field(
        default=2.0,
        ge=1.0,
        le=5.0,
        description="异常检测阈值（门控值超过 mean ± sigma*std 的帧，默认 2.0）",
    )
    created_by: str | None = Field(default=None, max_length=128, description="创建者（user_id 或 plugin_id）")


class GenerateCounterfactualRequest(BaseModel):
    """生成反事实解释请求体."""

    model_uri: str = Field(..., min_length=1, max_length=256, description="模型 URI")
    base_input: dict[str, float] = Field(..., description="基准输入（特征名 → 值），至少 1 个特征")
    perturbed_feature: str = Field(..., min_length=1, max_length=64, description="被扰动的特征名")
    perturbation_range: list[float] | None = Field(
        default=None,
        description="扰动值序列（如为空则按 perturbation_step 生成）",
    )
    perturbation_step: float = Field(
        default=0.05,
        ge=0.01,
        le=0.5,
        description="扰动步长（相对基准值的比例，默认 0.05 即 5%）",
    )
    source_snapshot_id: str | None = Field(default=None, max_length=64, description="关联实验快照 ID（可选）")
    created_by: str | None = Field(default=None, max_length=128, description="创建者（user_id 或 plugin_id）")


class GenerateConfidenceRequest(BaseModel):
    """生成置信度分布解释请求体."""

    model_uri: str = Field(..., min_length=1, max_length=256, description="模型 URI")
    input_data: dict[str, Any] = Field(..., description="输入数据（特征名 → 值）")
    sample_count: int = Field(default=30, ge=5, le=200, description="MC dropout 采样次数（默认 30）")
    source_snapshot_id: str | None = Field(default=None, max_length=64, description="关联实验快照 ID（可选）")
    created_by: str | None = Field(default=None, max_length=128, description="创建者（user_id 或 plugin_id）")


class CompareExplanationsRequest(BaseModel):
    """对比两个解释请求体."""

    base_explanation_id: str = Field(..., min_length=1, max_length=64, description="基准解释记录 ID")
    compared_explanation_id: str = Field(..., min_length=1, max_length=64, description="对比解释记录 ID")
    comparison_type: str = Field(
        default=ComparisonType.SAME_MODEL_DIFF_INPUT,
        description=f"对比类型（{ComparisonType.all()}，默认 same_model_diff_input）",
    )
    created_by: str | None = Field(default=None, max_length=128, description="创建者（user_id 或 plugin_id）")


# 辅助函数


def _handle_service_exception(e: Exception, *, action: str):
    """统一处理服务层异常 → API 错误响应.

    风格与 project_packages.py / resource_cards.py 对齐。

    Args:
        e: 服务层抛出的异常
        action: 当前操作描述（用于日志）

    Returns:
        error() 响应对象
    """
    if isinstance(e, ExplanationLookupError):
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    if isinstance(e, ExplanationValidationError):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="请检查请求参数（模型 URI / 输入数据 / 降维方法等）是否合法",
        )
    if isinstance(e, ProjectionError):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="降维投影失败：样本数可能不足或隐向量维度不匹配，请尝试减少 max_frames 或更换降维方法",
        )
    if isinstance(e, SamplingError):
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=str(e),
            suggestion="MC dropout 采样失败：模型可能未启用 dropout 层，请确认模型权重支持 MC dropout",
        )
    if isinstance(e, ComparisonMismatchError):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="两条解释的 explanation_type 不一致，无法对比。请选择相同类型的解释进行对比",
        )
    if isinstance(e, ValueError):
        # 参数校验失败
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    if isinstance(e, ExplainabilityError):
        logger.error("Explainability error during %s: %s", action, e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    # 兜底：未识别的异常
    logger.error("Unexpected error during %s: %s", action, e, exc_info=True)
    return error(
        code=ErrorCode.INTERNAL_ERROR,
        message=f"{action} 失败",
        detail=str(e),
    )


# 端点 1: POST /hidden-state —— 生成隐状态投影解释


@router.post(
    "/hidden-state",
    dependencies=[Depends(require_permission("explainability:write"))],
)
async def generate_hidden_state_explanation(request: GenerateHiddenStateRequest):
    """生成隐状态投影解释.

    流程：
        1. 前置校验 projection_method / projection_dim 合法性
        2. 调用 ``service.generate_hidden_state_explanation()`` 捕获隐状态
           并降维投影
        3. 返回解释记录（含 payload_path）

    权限：``explainability:write``
    """
    # 前置校验：projection_method 合法性
    # （projection_dim 范围由 Pydantic Field(ge=2, le=3) 保证）
    if not ProjectionMethod.is_valid(request.projection_method):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"projection_method 不支持: {request.projection_method}（支持: {ProjectionMethod.all()}）",
        )

    service = get_explainability_service()
    try:
        record = await service.generate_hidden_state_explanation(
            model_uri=request.model_uri,
            source_snapshot_id=request.source_snapshot_id,
            projection_method=request.projection_method,
            projection_dim=request.projection_dim,
            max_frames=request.max_frames,
            created_by=request.created_by,
        )
    except Exception as e:
        return _handle_service_exception(e, action="生成隐状态投影解释")

    payload = record.to_dict()
    # 元数据中包含 projection_method / projection_dim / sample_count 等信息
    meta = record.metadata_json or {}
    return success(
        data=payload,
        message=(
            f"隐状态投影解释已生成: 降维方法 {meta.get('projection_method', 'pca')}，"
            f"维度 {meta.get('projection_dim', 2)}，"
            f"样本数 {meta.get('sample_count', 0)}"
        ),
    )


# 端点 2: POST /gate-dynamics —— 生成门控动力学解释


@router.post(
    "/gate-dynamics",
    dependencies=[Depends(require_permission("explainability:write"))],
)
async def generate_gate_dynamics_explanation(request: GenerateGateDynamicsRequest):
    """生成门控动力学解释.

    流程：
        1. 调用 ``service.generate_gate_dynamics_explanation()`` 提取门控值
           与时间常数，检测异常帧
        2. 返回解释记录

    权限：``explainability:write``
    """
    service = get_explainability_service()
    try:
        record = await service.generate_gate_dynamics_explanation(
            model_uri=request.model_uri,
            source_snapshot_id=request.source_snapshot_id,
            anomaly_sigma=request.anomaly_sigma,
            created_by=request.created_by,
        )
    except Exception as e:
        return _handle_service_exception(e, action="生成门控动力学解释")

    payload = record.to_dict()
    return success(
        data=payload,
        message="门控动力学解释已生成",
    )


# 端点 3: POST /counterfactual —— 生成反事实解释


@router.post(
    "/counterfactual",
    dependencies=[Depends(require_permission("explainability:write"))],
)
async def generate_counterfactual_explanation(request: GenerateCounterfactualRequest):
    """生成反事实解释.

    流程：
        1. 前置校验 base_input 非空且包含 perturbed_feature
        2. 调用 ``service.generate_counterfactual_explanation()`` 扰动
           perturbed_feature 并扫描输出敏感性
        3. 返回解释记录

    权限：``explainability:write``
    """
    # 前置校验：base_input 非空
    if not request.base_input:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="base_input 不能为空",
        )
    # 前置校验：perturbed_feature 必须在 base_input 中
    if request.perturbed_feature not in request.base_input:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"perturbed_feature '{request.perturbed_feature}' "
            f"不在 base_input 中（可用特征: {list(request.base_input.keys())}）",
        )

    service = get_explainability_service()
    try:
        record = await service.generate_counterfactual_explanation(
            model_uri=request.model_uri,
            base_input=request.base_input,
            perturbed_feature=request.perturbed_feature,
            perturbation_range=request.perturbation_range,
            perturbation_step=request.perturbation_step,
            source_snapshot_id=request.source_snapshot_id,
            created_by=request.created_by,
        )
    except Exception as e:
        return _handle_service_exception(e, action="生成反事实解释")

    payload = record.to_dict()
    return success(
        data=payload,
        message=f"反事实解释已生成: 扰动特征 {request.perturbed_feature}",
    )


# 端点 4: POST /confidence —— 生成置信度分布解释


@router.post(
    "/confidence",
    dependencies=[Depends(require_permission("explainability:write"))],
)
async def generate_confidence_explanation(request: GenerateConfidenceRequest):
    """生成置信度分布解释（MC dropout 采样）.

    流程：
        1. 前置校验 input_data 非空
        2. 调用 ``service.generate_confidence_explanation()`` 执行
           MC dropout 多次随机前向采样
        3. 返回解释记录（含均值/标准差/分位数/直方图）

    权限：``explainability:write``
    """
    # 前置校验：input_data 非空
    if not request.input_data:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="input_data 不能为空",
        )

    service = get_explainability_service()
    try:
        record = await service.generate_confidence_explanation(
            model_uri=request.model_uri,
            input_data=request.input_data,
            sample_count=request.sample_count,
            source_snapshot_id=request.source_snapshot_id,
            created_by=request.created_by,
        )
    except Exception as e:
        return _handle_service_exception(e, action="生成置信度分布解释")

    payload = record.to_dict()
    return success(
        data=payload,
        message=f"置信度分布解释已生成: 采样 {request.sample_count} 次",
    )


# 端点 5: GET / —— 列出历史解释记录


@router.get("/")
async def list_explanations(
    explanation_type: str | None = Query(
        None,
        description=f"按解释类型过滤（{ExplanationType.all()}）",
    ),
    model_uri: str | None = Query(None, description="按模型 URI 过滤"),
    limit: int = Query(50, ge=1, le=500, description="每页数量（1-500，默认 50）"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """分页列出历史解释记录（支持 explanation_type / model_uri 过滤）.

    返回字段：
        - items: list[dict]（每个解释记录的 to_dict()）
        - total / limit / offset

    权限：``explainability:read``
    """
    # 前置校验：explanation_type 合法性
    if explanation_type is not None and not ExplanationType.is_valid(explanation_type):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"explanation_type 不支持: {explanation_type}（支持: {ExplanationType.all()}）",
        )

    service = get_explainability_service()
    try:
        records, total = await service.list_explanations(
            explanation_type=explanation_type,
            model_uri=model_uri,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        return _handle_service_exception(e, action="列出解释记录")

    items = [r.to_dict() for r in records]
    return success(
        data={
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        message=f"解释记录列表已获取（{len(items)} 条）",
    )


# 端点 6: GET /{explanation_id} —— 查询解释详情


@router.get("/{explanation_id}")
async def get_explanation(
    explanation_id: str,
    include_payload: bool = Query(False, description="为 true 时加载完整 payload 内容（含大型数组）"),
):
    """查询解释详情.

    - ``include_payload=false``（默认）：仅返回元数据（轻量）
    - ``include_payload=true``：附加 ``payload`` 字段（完整解释结果，可能较大）

    权限：``explainability:read``
    """
    service = get_explainability_service()
    try:
        record_dict = await service.get_explanation(explanation_id, include_payload=include_payload)
    except Exception as e:
        return _handle_service_exception(e, action="查询解释详情")

    return success(
        data=record_dict,
        message="解释详情已获取",
    )


# 端点 7: DELETE /{explanation_id} —— 删除解释记录


@router.delete(
    "/{explanation_id}",
    dependencies=[Depends(require_permission("explainability:write"))],
)
async def delete_explanation(explanation_id: str):
    """删除解释记录（同时删除 payload 文件）.

    权限：``explainability:write``
    """
    service = get_explainability_service()
    try:
        await service.delete_explanation(explanation_id)
    except Exception as e:
        return _handle_service_exception(e, action="删除解释记录")

    return success(
        data={"explanation_id": explanation_id, "deleted": True},
        message=f"解释记录 {explanation_id} 已删除",
    )


# 端点 8: POST /compare —— 对比两个解释


@router.post("/compare")
async def compare_explanations(request: CompareExplanationsRequest):
    """对比两个解释（生成差异 payload）.

    要求：
        - 两条解释的 ``explanation_type`` 必须一致
        - ``base_explanation_id`` 与 ``compared_explanation_id`` 不能相同
        - ``comparison_type`` 必须为 ``ComparisonType`` 常量之一

    权限：``explainability:read``
    """
    # 前置校验：comparison_type 合法性
    if not ComparisonType.is_valid(request.comparison_type):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"comparison_type 不支持: {request.comparison_type}（支持: {ComparisonType.all()}）",
        )

    # 前置校验：base 与 compared 不能相同
    if request.base_explanation_id == request.compared_explanation_id:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="base_explanation_id 与 compared_explanation_id 不能相同",
        )

    service = get_explainability_service()
    try:
        comparison = await service.compare_explanations(
            base_explanation_id=request.base_explanation_id,
            compared_explanation_id=request.compared_explanation_id,
            comparison_type=request.comparison_type,
            created_by=request.created_by,
        )
    except Exception as e:
        return _handle_service_exception(e, action="对比解释")

    payload = comparison.to_dict()
    return success(
        data=payload,
        message=f"解释对比已生成: {request.comparison_type}",
    )


__all__ = ["router"]
