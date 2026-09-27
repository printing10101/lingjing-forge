import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission

from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.services.tool_wear_predictor import ToolWearPredictor

# P2-4-5 修复：引入共享速率限制器，磨损预测/训练端点消耗计算资源，需速率限制防止 DoS。
from app.middleware.rate_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/wear",
    tags=["Tool Wear Prediction"],
    dependencies=[Depends(require_permission("wear:read"))],
)

predictor = ToolWearPredictor()


class WearPredictRequest(BaseModel):
    cutting_speed: float = Field(default=150.0, description="切削速度 (m/min)")
    feed_rate: float = Field(default=0.2, description="进给量 (mm/rev)")
    depth_of_cut: float = Field(default=1.5, description="切削深度 (mm)")
    material_type: str = Field(default="steel_45", description="材料类型")
    tool_type: str = Field(default="carbide", description="刀具类型")
    current_wear: float = Field(default=0.0, description="当前磨损量 (mm)")
    time_step: float = Field(default=1.0, description="时间步长 (min)")
    max_time: float = Field(default=300.0, description="最大预测时间 (min)")


class RemainingLifeRequest(BaseModel):
    current_wear: float = Field(default=0.1, description="当前磨损量 (mm)")
    cutting_speed: float = Field(default=150.0, description="切削速度 (m/min)")
    feed_rate: float = Field(default=0.2, description="进给量 (mm/rev)")
    depth_of_cut: float = Field(default=1.5, description="切削深度 (mm)")
    material_type: str = Field(default="steel_45", description="材料类型")
    tool_type: str = Field(default="carbide", description="刀具类型")


class SuggestRequest(BaseModel):
    current_wear: float = Field(default=0.15, description="当前磨损量 (mm)")
    remaining_life: float = Field(default=50.0, description="剩余寿命 (min)")
    cutting_speed: float = Field(default=150.0, description="切削速度 (m/min)")
    feed_rate: float = Field(default=0.2, description="进给量 (mm/rev)")
    depth_of_cut: float = Field(default=1.5, description="切削深度 (mm)")
    coolant_flow: float = Field(default=10.0, description="冷却液流量 (L/min)")
    material_type: str = Field(default="steel_45", description="材料类型")
    tool_type: str = Field(default="carbide", description="刀具类型")


class CalibrateRequest(BaseModel):
    measured_wear: float = Field(default=0.1, description="实测磨损量 (mm)")
    elapsed_time: float = Field(default=30.0, description="已加工时间 (min)")
    cutting_speed: float = Field(default=150.0, description="切削速度 (m/min)")
    feed_rate: float = Field(default=0.2, description="进给量 (mm/rev)")
    depth_of_cut: float = Field(default=1.5, description="切削深度 (mm)")
    material_type: str = Field(default="steel_45", description="材料类型")
    tool_type: str = Field(default="carbide", description="刀具类型")


class RealTimeCalibrateRequest(BaseModel):
    real_time_wear: float = Field(default=0.12, description="实时磨损量 (mm)")
    elapsed_time: float = Field(default=30.0, description="已加工时间 (min)")
    sensor_features: dict[str, float] = Field(
        default_factory=dict,
        description="传感器特征（vibration_rms, cutting_force, temperature, acoustic_emission）",
    )
    cutting_speed: float = Field(default=150.0, description="切削速度 (m/min)")
    feed_rate: float = Field(default=0.2, description="进给量 (mm/rev)")
    depth_of_cut: float = Field(default=1.5, description="切削深度 (mm)")
    material_type: str = Field(default="steel_45", description="材料类型")
    tool_type: str = Field(default="carbide", description="刀具类型")


class CompensationRequest(BaseModel):
    current_wear: float = Field(default=0.15, description="当前磨损量 (mm)")
    cutting_speed: float = Field(default=150.0, description="切削速度 (m/min)")
    feed_rate: float = Field(default=0.2, description="进给量 (mm/rev)")
    depth_of_cut: float = Field(default=1.5, description="切削深度 (mm)")
    material_type: str = Field(default="steel_45", description="材料类型")
    tool_type: str = Field(default="carbide", description="刀具类型")
    tool_diameter: float = Field(default=10.0, description="刀具直径 (mm)")
    machine_capabilities: dict[str, float] | None = Field(default=None, description="机床能力限制（可选）")


@router.post("/predict")
# P2-4-5 修复：磨损曲线预测消耗数值计算资源，限制为 60/minute。
@limiter.limit("60/minute")
async def predict_wear(request: Request, body: WearPredictRequest):
    try:
        params = {
            "cutting_speed": body.cutting_speed,
            "feed_rate": body.feed_rate,
            "depth_of_cut": body.depth_of_cut,
            "material_type": body.material_type,
            "tool_type": body.tool_type,
            "current_wear": body.current_wear,
            "time_step": body.time_step,
            "max_time": body.max_time,
        }
        curve = predictor.predict_wear_curve(params)
        return success(data=curve.to_dict(), message="Wear curve predicted successfully")
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        # 数值计算异常：磨损曲线预测涉及大量数学运算（math.exp、除法等）
        safe = safe_error_message(e, context="wear_prediction.predict")
        logger.error(
            "Wear prediction failed | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/remaining-life")
# P2-4-5 修复：剩余寿命预测消耗数值计算资源，限制为 60/minute。
@limiter.limit("60/minute")
async def predict_remaining_life(request: Request, body: RemainingLifeRequest):
    try:
        params = {
            "cutting_speed": body.cutting_speed,
            "feed_rate": body.feed_rate,
            "depth_of_cut": body.depth_of_cut,
            "material_type": body.material_type,
            "tool_type": body.tool_type,
        }
        remaining = predictor.predict_remaining_life(body.current_wear, params)
        threshold = predictor.get_replacement_threshold(body.material_type)
        return success(
            data={
                "remaining_life": remaining,
                "current_wear": body.current_wear,
                "replacement_threshold": threshold,
            },
            message="Remaining life predicted successfully",
        )
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        # 数值计算异常：剩余寿命预测涉及磨损曲线迭代计算
        safe = safe_error_message(e, context="wear_prediction.remaining_life")
        logger.error(
            "Remaining life prediction failed | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/suggest")
# P2-4-5 修复：参数建议涉及 Taylor 公式计算，限制为 60/minute。
@limiter.limit("60/minute")
async def suggest_adjustment(request: Request, body: SuggestRequest):
    try:
        current_params = {
            "cutting_speed": body.cutting_speed,
            "feed_rate": body.feed_rate,
            "depth_of_cut": body.depth_of_cut,
            "coolant_flow": body.coolant_flow,
            "material_type": body.material_type,
            "tool_type": body.tool_type,
        }
        suggestion = predictor.suggest_parameter_adjustment(body.current_wear, body.remaining_life, current_params)
        return success(data=suggestion.to_dict(), message="Adjustment suggestions generated")
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        # 数值计算异常：参数建议涉及Taylor公式计算与磨损率评估
        safe = safe_error_message(e, context="wear_prediction.suggest")
        logger.error(
            "Wear suggestion failed | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.get("/models")
# P2-4-5 修复：查询端点添加速率限制防止滥用，限制为 120/minute。
@limiter.limit("120/minute")
async def get_supported_models(request: Request):
    try:
        models = predictor.get_supported_models()
        materials = {
            key: {
                "name": val.name,
                "taylor_n": val.taylor_n,
                "taylor_C": val.taylor_C,
                "hardness_factor": val.hardness_factor,
            }
            for key, val in predictor.material_params.items()
        }
        tools = dict(predictor.tool_params.items())
        return success(
            data={
                "models": models,
                "supported_materials": materials,
                "supported_tools": tools,
            },
            message="Supported models retrieved successfully",
        )
    except (AttributeError, KeyError) as e:
        safe = safe_error_message(e, context="wear_prediction.get_models")
        logger.error(
            "Failed to get supported models | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/threshold")
# P2-4-5 修复：查询端点添加速率限制防止滥用，限制为 120/minute。
@limiter.limit("120/minute")
async def get_threshold(request: Request, material_type: str = "default"):
    try:
        threshold = predictor.get_replacement_threshold(material_type)
        return success(
            data={"material_type": material_type, "threshold": threshold},
            message="Replacement threshold retrieved",
        )
    except (KeyError, AttributeError) as e:
        safe = safe_error_message(e, context="wear_prediction.get_threshold")
        logger.error(
            "Failed to get replacement threshold | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/calibrate")
# P2-4-5 修复：标定涉及磨损曲线预测，限制为 60/minute。
@limiter.limit("60/minute")
async def calibrate_prediction(request: Request, body: CalibrateRequest):
    try:
        params = {
            "cutting_speed": body.cutting_speed,
            "feed_rate": body.feed_rate,
            "depth_of_cut": body.depth_of_cut,
            "material_type": body.material_type,
            "tool_type": body.tool_type,
        }
        result = predictor.calibrate_with_measurement(body.measured_wear, body.elapsed_time, params)
        return success(data=result, message="Prediction calibrated successfully")
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        # 数值计算异常：标定涉及磨损曲线预测与偏差计算
        safe = safe_error_message(e, context="wear_prediction.calibrate")
        logger.error(
            "Wear calibration failed | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/calibrate-realtime")
# P2-4-5 修复：实时校正涉及 EWMA 融合计算，限制为 60/minute。
@limiter.limit("60/minute")
async def calibrate_with_real_time_data(request: Request, body: RealTimeCalibrateRequest):
    try:
        params = {
            "cutting_speed": body.cutting_speed,
            "feed_rate": body.feed_rate,
            "depth_of_cut": body.depth_of_cut,
            "material_type": body.material_type,
            "tool_type": body.tool_type,
        }
        result = predictor.calibrate_with_real_time_data(
            real_time_wear=body.real_time_wear,
            sensor_features=body.sensor_features,
            elapsed_time=body.elapsed_time,
            input_parameters=params,
        )
        return success(data=result, message="Real-time calibration completed successfully")
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        # 数值计算异常：实时校正涉及 EWMA 融合与传感器修正
        safe = safe_error_message(e, context="wear_prediction.calibrate_realtime")
        logger.error(
            "Real-time calibration failed | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/compensation")
# P2-4-5 修复：补偿建议涉及 Taylor 公式计算，限制为 60/minute。
@limiter.limit("60/minute")
async def get_compensation_suggestions(request: Request, body: CompensationRequest):
    try:
        params = {
            "cutting_speed": body.cutting_speed,
            "feed_rate": body.feed_rate,
            "depth_of_cut": body.depth_of_cut,
            "material_type": body.material_type,
            "tool_type": body.tool_type,
            "tool_diameter": body.tool_diameter,
        }
        result = predictor.get_compensation_recommendations(
            current_wear=body.current_wear,
            input_parameters=params,
            machine_capabilities=body.machine_capabilities,
        )
        return success(data=result, message="Compensation suggestions generated successfully")
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        # 数值计算异常：补偿建议涉及 Taylor 公式与机床能力校验
        safe = safe_error_message(e, context="wear_prediction.compensation")
        logger.error(
            "Compensation suggestion failed | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/train-uniwear")
# P2-4-5 修复：模型训练消耗大量计算资源，限制为 5/hour。
@limiter.limit("5/hour")
async def train_uniwear_model(
    request: Request,
    data_dir: str = "python/data/uniwear",
    model_type: str = "random_forest",
):
    try:
        result = predictor.train_with_uniwear_data(data_dir=data_dir, model_type=model_type)
        return success(data=result, message="Uniwear model training completed")
    except (OSError, ImportError, ValueError, RuntimeError) as e:
        # 文件 I/O 或依赖导入异常：Uniwear 训练涉及数据加载与模型训练
        safe = safe_error_message(e, context="wear_prediction.train_uniwear")
        logger.error(
            "Uniwear training failed | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/predict-from-signals")
# P2-4-5 修复：信号特征预测涉及模型推理，限制为 60/minute。
@limiter.limit("60/minute")
async def predict_wear_from_signal_features(
    request: Request,
    features: dict[str, float],
    material: str = "tc4",
):
    try:
        result = predictor.predict_wear_from_signals(signal_features=features, material=material)
        return success(data=result, message="Wear predicted from signal features")
    except (ValueError, TypeError, RuntimeError) as e:
        # 数值计算或模型推理异常：信号特征预测涉及 numpy 运算与模型推理
        safe = safe_error_message(e, context="wear_prediction.predict_signals")
        logger.error(
            "Signal prediction failed | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.get("/cross-dataset-analysis")
# P2-4-5 修复：查询端点添加速率限制防止滥用，限制为 120/minute。
@limiter.limit("120/minute")
async def get_cross_dataset_analysis(request: Request):
    try:
        analysis = predictor.cross_dataset_analysis()
        return success(data=analysis, message="Cross-dataset analysis completed")
    except (KeyError, AttributeError) as e:
        safe = safe_error_message(e, context="wear_prediction.cross_dataset")
        logger.error(
            "Cross-dataset analysis failed | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.get("/uniwear-materials")
# P2-4-5 修复：查询端点添加速率限制防止滥用，限制为 120/minute。
@limiter.limit("120/minute")
async def get_uniwear_materials(request: Request):
    try:
        materials = predictor.get_uniwear_material_params()
        return success(data=materials, message="Uniwear material parameters retrieved")
    except (KeyError, AttributeError) as e:
        safe = safe_error_message(e, context="wear_prediction.uniwear_materials")
        logger.error(
            "Failed to get uniwear materials | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )
