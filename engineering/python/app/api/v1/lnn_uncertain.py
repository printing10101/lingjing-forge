"""LNN Uncertainty Quantification API endpoint.

提供 ``/api/v1/lnn/predict-uncertain`` 端点，基于 Bayesian LNN（MC Dropout）
返回预测值、真实不确定性（标准差）和置信度。

实现要点：
- 不确定性来自 MC Dropout 的多次前向传播样本标准差，而非从置信度反推；
- 置信度计算 ``1 - std/|mean|`` 仅在 MC 样本数 >= 2 时生效，否则使用模型
  自身的 softmax/sigmoid 置信度并显式标注 ``uncertainty_method``；
- 当 LNN 模型未配置 dropout（``dropout_rate == 0``）时，回退到单次推理并
  显式标注 ``mc_n_samples=1``、``uncertainty_method="single_pass"``，绝不
  伪造 std。
"""

import logging
import numpy as np
from fastapi import APIRouter, Depends, Request
from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.auth.permissions import require_permission

# P2-4-5 修复：引入共享速率限制器，MC Dropout 推理消耗 30 次前向传播，
# 资源消耗远高于单次推理，需速率限制防止 DoS。
from app.middleware.rate_limiter import limiter
from app.models.schemas import LNNPredictRequest, UncertaintyResponse
from app.ai.lnn.inference.predictor import LNNPredictor, PredictionResult
from app.dependencies import get_model_registry_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/lnn",
    tags=["LNN Models"],
    dependencies=[Depends(require_permission("lnn:read"))],
)

registry_service = get_model_registry_service()
model_registry = registry_service.model_registry
model_cache = registry_service.model_cache

# MC Dropout 默认样本数；过低会降低不确定性估计的稳定性，
# 过高会增加延迟。30 是 Bayesian 深度学习常用折中值。
DEFAULT_MC_SAMPLES = 30
# 当模型 dropout_rate <= 该阈值时认为模型未启用 dropout，
# MC Dropout 不会产生有意义的不确定性，回退到单次推理。
MIN_DROPOUT_FOR_MC = 1e-3


@router.post("/predict-uncertain", dependencies=[Depends(require_permission("lnn:read"))])
# P2-4-5 修复：MC Dropout 默认 30 次前向传播，资源消耗约为单次推理的 30 倍，
# 限制为 20/minute（比 predict 的 60/minute 更严格）。
@limiter.limit("20/minute")
async def predict_uncertain(request: Request, body: LNNPredictRequest):
    """基于 Bayesian LNN（MC Dropout）的预测 + 不确定性量化。

    返回结构：
        - ``prediction``: 预测均值；
        - ``uncertainty``: MC 样本标准差（认知不确定性）；
        - ``confidence``: ``1 - uncertainty/|prediction|``，裁剪到 [0,1]；
        - ``uncertainty_method``: ``mc_dropout`` / ``single_pass`` / ``fallback``。
    """
    try:
        entry = model_registry.registry.get(body.model_name)
        if not entry:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Model '{body.model_name}' not found",
            )

        if not body.input_data:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message="输入数据必须为非空列表",
            )

        if any(not isinstance(x, (int, float)) for x in body.input_data):
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message="输入数据必须为数值类型",
            )

        model_info = entry.info
        expected_dim = len(model_info.input_features) if model_info.input_features else None
        if expected_dim:
            input_len = len(body.input_data)
            if input_len != expected_dim and input_len % expected_dim != 0:
                return error(
                    code=ErrorCode.INVALID_REQUEST,
                    message=f"输入维度不匹配: 期望{expected_dim}维或其倍数，实际{input_len}维",
                )

        predictor = model_cache.get(body.model_name)
        if predictor is None:
            predictor = LNNPredictor.from_registry(
                registry=model_registry,
                model_name=body.model_name,
                use_amp=True,
                auto_device=True,
            )
            model_cache.put(body.model_name, predictor)

        # 判定模型是否启用了 dropout；未启用则 MC Dropout 无意义。
        dropout_rate = _resolve_dropout_rate(predictor)
        use_mc = dropout_rate is not None and dropout_rate > MIN_DROPOUT_FOR_MC

        try:
            if use_mc:
                result = predictor.predict_mc_dropout(
                    input_data=body.input_data,
                    n_samples=DEFAULT_MC_SAMPLES,
                )
                uncertainty_method = "mc_dropout"
            else:
                result = predictor.predict(
                    input_data=body.input_data,
                    return_confidence=True,
                )
                uncertainty_method = "single_pass"
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as model_err:
            safe = safe_error_message(
                model_err,
                context=f"lnn.predict_uncertain_inference[{body.model_name}]",
            )
            logger.error(
                "Model inference error | model=%s | error_id=%s | exc=%s",
                body.model_name,
                safe.get("error_id"),
                model_err,
                exc_info=True,
            )
            return error(
                code=ErrorCode.INTERNAL_ERROR,
                message=safe["message"],
                detail=safe.get("detail"),
            )

        if not isinstance(result, PredictionResult):
            result = PredictionResult(
                value=result,
                confidence=0.0,
                inference_time=0.0,
            )

        value = result.value
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, list) and len(value) == 1:
            value = value[0]

        # 从 model_info 中读取 MC Dropout 真实标准差，避免循环伪造
        mc_std = result.model_info.get("mc_std") if result.model_info else None

        if isinstance(value, list):
            prediction = float(np.mean(value))
            # 多输出场景：使用输出间方差作为 aleatoric 不确定性
            std = float(np.std(value)) if len(value) > 1 else 0.0
            if mc_std is not None and mc_std > 0:
                # 取 MC epistemic 与输出 aleatoric 的最大值作为总不确定性
                std = max(std, float(mc_std))
        else:
            prediction = float(value)
            # 真实 std 来自 MC Dropout；若未启用 MC，则显式标注 0 并降级置信度
            std = float(mc_std) if mc_std is not None else 0.0

        mean_abs = abs(prediction) if prediction != 0 else 1.0
        ratio = std / mean_abs
        clamped_ratio = min(max(ratio, 0.0), 1.0)
        confidence = 1.0 - clamped_ratio

        # 当未启用 MC Dropout 时，使用模型自身置信度作为兜底，但仍标注方法
        if uncertainty_method == "single_pass":
            model_conf = float(result.confidence) if result.confidence is not None else 0.0
            # 取两者中较低值，避免过度自信
            confidence = min(confidence, model_conf) if model_conf > 0 else confidence

        response = UncertaintyResponse(
            prediction=prediction,
            uncertainty=std,
            confidence=confidence,
        )

        response_data = response.model_dump()
        response_data["uncertainty_method"] = uncertainty_method
        response_data["mc_n_samples"] = result.model_info.get("mc_n_samples", 1) if result.model_info else 1

        return success(
            data=response_data,
            message="Uncertainty prediction completed successfully",
        )

    except KeyError:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"Model '{body.model_name}' not found in registry",
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        safe = safe_error_message(
            e,
            context=f"lnn.predict_uncertain[{body.model_name}]",
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


def _resolve_dropout_rate(predictor: LNNPredictor) -> float | None:
    """从预测器内部模型读取 dropout_rate，无法读取时返回 None。"""
    model = getattr(predictor, "model", None)
    if model is None:
        return None
    dropout = getattr(model, "dropout_rate", None)
    if dropout is None:
        dropout = getattr(model, "dropout", None)
    try:
        return float(dropout) if dropout is not None else None
    except (TypeError, ValueError):
        return None
