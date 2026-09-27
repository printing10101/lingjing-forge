"""LNN 模型管理与缓存/性能端点。

从 routes.py 拆分而来（P0-2.3 子路由拆分）。本模块承载模型列表、
模型信息、模型校验、模型大小、缓存统计/清理、性能统计等端点，
模块级状态（model_registry / model_cache 等）集中在 ``dependencies.py``。

注意：``_format_size`` 已废弃，统一使用 ``app.utils.utils.format_bytes``。
"""

import logging
import os

from fastapi import APIRouter, Depends

from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.auth.permissions import require_permission
from app.core.api_response import api_response
from app.utils.utils import format_bytes
from app.models.schemas import LNNModelSizeResponse
from app.ai.lnn.inference.predictor import LNNPredictor
from app.ai.lnn.inference.registry import get_quantized_model_name

from app.api.v1.lnn.dependencies import (
    model_registry,
    model_cache,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/models")
@api_response
async def list_lnn_models():
    models = model_registry.list_models(return_objects=True)

    models_list = []
    for model_info in models:
        models_list.append(
            {
                "name": model_info.name,
                "model_type": model_info.model_type,
                "version": model_info.version,
                "input_features": model_info.input_features,
                "output_features": model_info.output_features,
            }
        )

    return success(
        data={"models": models_list, "total": len(models_list)},
        message="Models retrieved successfully",
    )


@router.get("/models/{model_name}/info")
@api_response
async def get_model_info(model_name: str):
    entry = model_registry.registry.get(model_name)
    if not entry:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"Model '{model_name}' not found",
        )

    model_info = entry.info
    if model_info is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"Model '{model_name}' info not available",
        )

    validation_result = model_registry.validate_model(model_name)

    info_data = {
        "name": model_info.name,
        "model_type": model_info.model_type,
        "model_path": model_info.model_path,
        "input_features": model_info.input_features,
        "output_features": model_info.output_features,
        "version": model_info.version,
        "is_loaded": entry.is_loaded,
        "access_count": entry.access_count,
        "validation": validation_result,
    }

    return success(data=info_data, message="Model info retrieved successfully")


@router.post("/models/{model_name}/validate", dependencies=[Depends(require_permission("lnn:write"))])
async def validate_model(model_name: str):
    try:
        entry = model_registry.registry.get(model_name)
        if not entry:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Model '{model_name}' not found",
            )

        validation_result = model_registry.validate_model(model_name)

        if not validation_result["valid"]:
            return success(
                data={
                    "model_name": model_name,
                    "valid": False,
                    "validation_details": validation_result,
                    "message": "Model validation failed",
                },
                message="Model validation completed with errors",
            )

        model_info = entry.info
        if model_info is None:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Model '{model_name}' info not available",
            )
        info_data = {
            "model_name": model_name,
            "valid": True,
            "validation_details": validation_result,
            "model_type": model_info.model_type,
            "version": model_info.version,
            "input_dimensions": len(model_info.input_features),
            "output_dimensions": len(model_info.output_features),
        }

        return success(data=info_data, message="Model validation completed successfully")

    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
        safe = safe_error_message(e, context=f"lnn.validate_model[{model_name}]")
        logger.warning("Model validation failed: %s", e)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.get("/models/{model_name}/size")
async def get_model_size(model_name: str):
    """获取模型及其量化版本的大小信息。"""
    try:
        entry = model_registry.registry.get(model_name)
        if not entry:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Model '{model_name}' not found",
            )

        if entry.info is None:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Model '{model_name}' info not available",
            )
        original_path = entry.info.model_path
        original_size = os.path.getsize(original_path) if os.path.exists(original_path) else 0

        quantized_model_name = get_quantized_model_name(model_name)
        quantized_entry = model_registry.registry.get(quantized_model_name)
        quantized_size = None
        quantized_path = None

        if quantized_entry and quantized_entry.info is not None:
            quantized_path = quantized_entry.info.model_path
            if os.path.exists(quantized_path):
                quantized_size = os.path.getsize(quantized_path)

        response = LNNModelSizeResponse(
            original_size_bytes=original_size,
            quantized_size_bytes=quantized_size,
            original_size_human=format_bytes(original_size),
            quantized_size_human=format_bytes(quantized_size) if quantized_size else None,
            size_reduction_bytes=original_size - quantized_size if quantized_size else None,
            size_reduction_percent=round((1.0 - quantized_size / original_size) * 100, 2)
            if quantized_size and original_size > 0
            else None,
        )

        return success(data=response.model_dump(), message="Model size retrieved successfully")

    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
        safe = safe_error_message(e, context=f"lnn.get_model_size[{model_name}]")
        logger.warning("Get model size failed: %s", e)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.get("/cache/stats")
@api_response
async def get_cache_stats():
    """获取模型缓存统计信息"""
    stats = model_cache.get_stats()

    return success(
        data={
            "cached_models": stats["cached_models"],
            "model_details": stats["model_details"],
            "total_cache_size_bytes": stats["total_cache_size_bytes"],
            "total_cache_size_mb": stats["total_cache_size_mb"],
            "hit_rate": stats["hit_rate"],
            "cache_hits": stats["cache_hits"],
            "cache_misses": stats["cache_misses"],
            "total_requests": stats["total_requests"],
            "max_size": stats["max_size"],
        },
        message="Cache statistics retrieved successfully",
    )


@router.delete("/cache/clear", dependencies=[Depends(require_permission("lnn:write"))])
@api_response
async def clear_cache():
    """清空所有模型缓存"""
    count, memory_freed = model_cache.clear()

    return success(
        data={
            "models_cleared": count,
            "memory_freed_bytes": memory_freed,
            "memory_freed_mb": round(memory_freed / (1024 * 1024), 2),
        },
        message=f"Cache cleared successfully: {count} models removed",
    )


@router.get("/performance")
@api_response
async def get_performance(model: str | None = None):
    candidate_models: list[str] = []
    if model:
        candidate_models = [model]
    else:
        candidate_models = list(model_registry.registry.keys())

    results: list[dict] = []
    for m_name in candidate_models:
        cached_predictor = model_cache.get(m_name)
        if cached_predictor is None:
            continue
        try:
            entry = model_registry.registry.get(m_name)
            base_model = entry.model if entry else cached_predictor
            if not isinstance(base_model, LNNPredictor) and base_model is not None:
                from app.ai.lnn.inference.predictor import LNNPredictor as P

                if isinstance(base_model, P):
                    perf = base_model.get_performance()
                    results.append(perf)
                    continue
        except (KeyError, AttributeError, RuntimeError, ValueError) as e:
            logger.debug(
                f"Failed to collect performance for {m_name}: {e}",
                exc_info=True,
            )

        if isinstance(cached_predictor, LNNPredictor):
            perf = cached_predictor.get_performance()
            results.append(perf)

    summary = {
        "total_models_tracked": len(results),
        "models": results,
    }
    return success(data=summary, message="Performance stats retrieved")
