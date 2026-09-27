"""NL to CAD API routes."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission

# P2-4-5 修复：引入共享速率限制器，NL2CAD 端点调用 LLM 生成 CAD/NC 代码，
# 消耗大量推理资源，需速率限制防止 DoS。
from app.middleware.rate_limiter import limiter
from app.api.v1.nl2cad.services import get_nl2cad_service
from app.api.v1.nl2cad.orchestrator import get_nl2nc_orchestrator, PipelineStage

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/nl2cad",
    tags=["NL2CAD"],
    dependencies=[Depends(require_permission("nl2cad:read"))],
)


def _handle_service_exception(e: Exception, operation: str) -> None:
    """统一处理 NL2CAD 服务层异常并抛出对应 HTTPException。

    异常分类：
        - ``HTTPException``：原样向上抛出，避免被包装为 500
        - ``ValueError`` / ``KeyError`` / ``TypeError``：客户端输入问题 → 400
        - ``ImportError`` / ``ModuleNotFoundError``：软依赖缺失 → 503
        - 其他 ``Exception``：服务端内部错误 → 500

    Args:
        e: 服务层抛出的原始异常。
        operation: 操作名称（用于日志），如 "generate model from NL"。
    """
    # 1. HTTPException 原样抛出，避免被二次包装
    if isinstance(e, HTTPException):
        raise e

    # 2. 客户端输入错误 400
    if isinstance(e, (ValueError, KeyError, TypeError)):
        logger.error("Invalid input during %s: %s", operation, e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求参数无效，请检查输入",
        ) from e

    # 3. 软依赖缺失 503
    if isinstance(e, (ImportError, ModuleNotFoundError)):
        logger.error("Optional dependency missing during %s: %s", operation, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务依赖未就绪，请联系管理员",
        ) from e

    # 4. 其他未预期异常 500
    logger.error("Failed to %s: %s", operation, e, exc_info=True)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="处理失败，请联系管理员",
    ) from e


class NL2CADRequest(BaseModel):
    """Request model for NL to CAD conversion."""

    description: str = Field(..., description="自然语言零件描述", min_length=1, max_length=2000)
    output_format: str = Field(default="stl", description="输出格式", pattern="^(stl|step|obj|gltf)$")


class NL2CADResponse(BaseModel):
    """Response model for NL to CAD conversion."""

    model_path: str = Field(..., description="生成的模型文件路径")
    params: dict[str, Any] = Field(..., description="提取的CAD参数")
    confidence: float = Field(..., description="参数提取置信度", ge=0.0, le=1.0)
    script: str = Field(
        default="",
        description="等价参数化 CadQuery 模板脚本（顶层命名尺寸变量），供滑杆调参重执行",
    )
    parameters: dict[str, float] = Field(
        default_factory=dict,
        description="脚本可调参数表（参数名 → 当前值 mm）；为空表示脚本不可参数化",
    )


class RefineRequest(BaseModel):
    """Request model for model refinement."""

    current_params: dict[str, Any] = Field(..., description="当前模型参数")
    instruction: str = Field(..., description="微调指令", min_length=1, max_length=1000)
    output_format: str = Field(default="stl", description="输出格式", pattern="^(stl|step|obj|gltf)$")


class RefineResponse(BaseModel):
    """Response model for model refinement."""

    model_path: str = Field(..., description="更新后的模型文件路径")
    params: dict[str, Any] = Field(..., description="更新后的CAD参数")


class ExtractParamsRequest(BaseModel):
    """Request model for parameter extraction only."""

    description: str = Field(..., description="自然语言零件描述", min_length=1, max_length=2000)


class ExtractParamsResponse(BaseModel):
    """Response model for parameter extraction."""

    params: dict[str, Any] = Field(..., description="提取的CAD参数")
    confidence: float = Field(..., description="置信度", ge=0.0, le=1.0)


class RegenerateParamsRequest(BaseModel):
    """Request model for parameter-driven regeneration (no LLM)."""

    script: str = Field(
        ..., description="此前生成的 CadQuery 脚本（/generate 返回的 script）", min_length=1, max_length=50000
    )
    params: dict[str, float] = Field(..., description="要覆写的尺寸参数（键必须来自该脚本的参数表）")
    output_format: str = Field(default="step", description="输出格式", pattern="^(stl|step|obj|gltf)$")
    task_id: str | None = Field(default=None, description="任务 ID（缺省自动生成）")


class RegenerateParamsResponse(BaseModel):
    """Response model for parameter-driven regeneration."""

    model_path: str = Field(..., description="重新生成的模型文件路径")
    params: dict[str, float] = Field(..., description="覆写后的完整参数表")


class RegenerateParamsError(BaseModel):
    """Error detail for parameter-driven regeneration."""

    detail: str = Field(..., description="失败原因（未知参数 / 非法值 / 执行失败）")


class ProcessPlanningRequest(BaseModel):
    """Request model for process planning."""

    cad_params: dict[str, Any] = Field(..., description="CAD参数")
    material: str = Field(..., description="材料类型")
    machine_type: str = Field(default="cnc_mill", description="机床类型")
    precision: str = Field(default="finish", description="精度等级")


class ProcessPlanningResponse(BaseModel):
    """Response model for process planning."""

    process_plan: dict[str, Any] = Field(..., description="工艺规划结果")


class NCCodeRequest(BaseModel):
    """Request model for NC code generation."""

    process_plan: dict[str, Any] = Field(..., description="工艺规划")
    machine_type: str = Field(default="cnc_mill", description="机床类型")


class NCCodeResponse(BaseModel):
    """Response model for NC code generation."""

    nc_code: str = Field(..., description="生成的NC代码")


class FullPipelineRequest(BaseModel):
    """Request model for full pipeline execution."""

    description: str = Field(..., description="自然语言零件描述", min_length=1, max_length=2000)
    machine_type: str = Field(default="cnc_mill", description="机床类型")
    material: str = Field(default="steel", description="材料类型")


class FullPipelineResponse(BaseModel):
    """Response model for full pipeline execution."""

    model_path: str = Field(..., description="生成的模型文件路径")
    cad_params: dict[str, Any] = Field(..., description="CAD参数")
    process_plan: dict[str, Any] = Field(..., description="工艺规划")
    nc_code: str = Field(..., description="NC代码")
    simulation_result: dict[str, Any] = Field(..., description="仿真结果")


@router.post("/generate", response_model=NL2CADResponse)
# P2-4-5 修复：LLM 生成 3D 模型消耗大量推理资源，限制为 10/minute。
@limiter.limit("10/minute")
async def generate_from_nl(request: Request, body: NL2CADRequest) -> NL2CADResponse:
    """从自然语言描述生成3D模型。

    Args:
        request: HTTP 请求对象（速率限制用）
        body: 包含描述和输出格式的请求

    Returns:
        包含模型路径和参数的响应
    """
    logger.info("Received NL2CAD generate request: %s", body.description[:100])

    try:
        service = get_nl2cad_service()
        model_path, params, script, parameters = await service.generate_model_from_nl(
            description=body.description,
            output_format=body.output_format,
        )

        return NL2CADResponse(
            model_path=model_path,
            params=params,
            confidence=params.get("confidence", 0.8),
            script=script,
            parameters=parameters,
        )

    except Exception as e:
        _handle_service_exception(e, "generate model from NL")
        raise  # 不可达：_handle_service_exception 总是抛出 HTTPException


@router.post("/refine", response_model=RefineResponse)
# P2-4-5 修复：LLM 微调模型消耗推理资源，限制为 10/minute。
@limiter.limit("10/minute")
async def refine_model(request: Request, body: RefineRequest) -> RefineResponse:
    """根据用户指令微调3D模型。

    Args:
        request: HTTP 请求对象（速率限制用）
        body: 包含当前参数和微调指令的请求

    Returns:
        包含更新后模型路径和参数的响应
    """
    logger.info("Received refine request: %s", body.instruction[:100])

    try:
        service = get_nl2cad_service()
        model_path, params = await service.refine_model(
            current_params=body.current_params,
            instruction=body.instruction,
            output_format=body.output_format,
        )

        return RefineResponse(model_path=model_path, params=params)

    except Exception as e:
        _handle_service_exception(e, "refine model")
        raise  # 不可达


@router.post(
    "/regenerate-params",
    response_model=RegenerateParamsResponse,
    responses={400: {"model": RegenerateParamsError}},
)
# 参数化直调不消耗 LLM 推理（仅重执行脚本），限额放宽到 60/minute，
# 支撑前端滑杆拖动的连续调参节奏；仍限流防脚本重执行被滥用。
@limiter.limit("60/minute")
async def regenerate_with_params(request: Request, body: RegenerateParamsRequest) -> RegenerateParamsResponse:
    """按参数覆写重新执行脚本生成模型（不调用 LLM）。

    典型用法：/generate 生成成功后，前端把返回的 script 的参数表渲染为
    滑杆；用户拖动滑杆调用本端点，亚秒级拿到新模型，零推理成本。

    Raises:
        400: 未知参数 / 非法值（非正数）/ 脚本审计或执行失败。
        500: 模型文件写入失败（磁盘/权限等存储环境故障，非参数问题）。
    """
    from app.cad.parametric_model import ParameterError, regenerate_model
    from app.cad.cadquery_gen import CadQueryError

    logger.info("Received param regen request: params=%s", sorted(body.params))

    try:
        model_path, new_params = await regenerate_model(
            script=body.script,
            overrides=body.params,
            task_id=body.task_id,
            output_format=body.output_format,
        )
        return RegenerateParamsResponse(model_path=model_path, params=new_params)
    except ParameterError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except OSError as e:
        # 磁盘满 / 导出目录无权限等环境故障：归 500，避免被误报成
        # "参数超出几何可行域"误导客户端重调
        logger.error("Param regen I/O failure: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"模型文件写入失败（服务器存储异常）: {e}",
        ) from e
    except (CadQueryError, ValueError, TypeError, RuntimeError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"脚本执行失败，参数可能超出几何可行域: {e}",
        ) from e
    except Exception as e:
        _handle_service_exception(e, "regenerate model with params")
        raise  # 不可达：_handle_service_exception 总是抛出 HTTPException


@router.post("/extract-params", response_model=ExtractParamsResponse)
# P2-4-5 修复：LLM 提取参数消耗推理资源，限制为 10/minute。
@limiter.limit("10/minute")
async def extract_params(request: Request, body: ExtractParamsRequest) -> ExtractParamsResponse:
    """从自然语言描述中提取CAD参数（不生成模型）。

    Args:
        request: HTTP 请求对象（速率限制用）
        body: 包含描述请求

    Returns:
        包含提取参数的响应
    """
    logger.info("Received extract params request: %s", body.description[:100])

    try:
        service = get_nl2cad_service()
        params = await service.extract_params_from_nl(description=body.description)

        return ExtractParamsResponse(
            params=params,
            confidence=params.get("confidence", 0.8),
        )

    except Exception as e:
        _handle_service_exception(e, "extract params")
        raise  # 不可达


@router.post("/process-planning", response_model=ProcessPlanningResponse)
# P2-4-5 修复：LLM 生成工艺规划消耗推理资源，限制为 10/minute。
@limiter.limit("10/minute")
async def generate_process_plan(request: Request, body: ProcessPlanningRequest) -> ProcessPlanningResponse:
    """根据CAD参数生成工艺规划。

    Args:
        request: HTTP 请求对象（速率限制用）
        body: 包含CAD参数、材料、机床类型和精度等级的请求

    Returns:
        包含工艺规划结果的响应
    """
    logger.info("Received process planning request for material: %s", body.material)

    try:
        orchestrator = get_nl2nc_orchestrator()

        # 生成工艺规划
        process_plan = await orchestrator._generate_process_plan(
            cad_params=body.cad_params,
            material=body.material,
        )

        return ProcessPlanningResponse(process_plan=process_plan)

    except Exception as e:
        _handle_service_exception(e, "generate process plan")
        raise  # 不可达


@router.post("/generate-nc", response_model=NCCodeResponse)
# P2-4-5 修复：LLM 生成 NC 代码消耗推理资源，限制为 10/minute。
@limiter.limit("10/minute")
async def generate_nc_code(request: Request, body: NCCodeRequest) -> NCCodeResponse:
    """根据工艺规划生成NC代码。

    Args:
        request: HTTP 请求对象（速率限制用）
        body: 包含工艺规划和机床类型的请求

    Returns:
        包含生成的NC代码的响应
    """
    logger.info("Received NC code generation request for machine: %s", body.machine_type)

    try:
        orchestrator = get_nl2nc_orchestrator()

        # 生成NC代码
        nc_code = orchestrator._generate_nc_code(
            process_plan=body.process_plan,
            machine_type=body.machine_type,
        )

        return NCCodeResponse(nc_code=nc_code)

    except Exception as e:
        _handle_service_exception(e, "generate NC code")
        raise  # 不可达


@router.post("/full-pipeline", response_model=FullPipelineResponse)
# P2-4-5 修复：完整流水线串联多个 LLM 调用，资源消耗最高，限制为 5/minute。
@limiter.limit("5/minute")
async def execute_full_pipeline(request: Request, body: FullPipelineRequest) -> FullPipelineResponse:
    """执行完整的NL-to-NC流程。

    Args:
        request: HTTP 请求对象（速率限制用）
        body: 包含零件描述、机床类型和材料类型的请求

    Returns:
        包含完整流程结果的响应
    """
    logger.info("Received full pipeline request: %s", body.description[:100])

    try:
        orchestrator = get_nl2nc_orchestrator()

        # 执行完整流程
        state = await orchestrator.execute_full_pipeline(
            description=body.description,
            machine_type=body.machine_type,
            material=body.material,
        )

        if state.stage == PipelineStage.FAILED:
            raise ValueError(state.error or "Pipeline failed")

        return FullPipelineResponse(
            model_path=state.model_path or "",
            cad_params=state.cad_params or {},
            process_plan=state.process_plan or {},
            nc_code=state.nc_code or "",
            simulation_result=state.simulation_result or {},
        )

    except Exception as e:
        _handle_service_exception(e, "execute full pipeline")
        raise  # 不可达
