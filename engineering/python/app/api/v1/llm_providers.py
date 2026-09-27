"""LLM Provider 管理 API 端点。

提供 Provider 配置的 CRUD、自动探测、激活管理、健康检查、模型列表、
路由状态等接口，供前端"系统设置 → AI 引擎"模块使用。

权限模型：
- 读取类操作（list/get/status/models/health/detect）：要求登录（统一鉴权中间件保障）
- 写入类操作（create/update/delete/activate/enable/import/test）：要求 `system:config` 权限
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.ai.llm.provider_base import (
    ProviderCapability,
    ProviderConfig,
    ProviderType,
)
from app.ai.llm.provider_registry import get_registry
from app.ai.llm.auto_detect import get_detector
from app.ai.llm.router import (
    RoutingStrategy,
    get_router,
)
from app.auth.permissions import require_permission
from app.core.response_models import ErrorResponse
from app.core.safe_errors import safe_error_message
import time

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/llm-providers", tags=["LLM Providers"])

# 自动探测 LLM 服务的统一超时（秒）。detect_preview 与 detect_and_import 共享。
LLM_DETECT_TIMEOUT_SEC: float = 30.0


# Pydantic 请求模型


class ProviderCreateRequest(BaseModel):
    """创建/更新 Provider 请求体。"""

    provider_id: str = Field(..., description="Provider 唯一标识")
    name: str = Field(..., description="显示名称")
    provider_type: str = Field(..., description="Provider 类型（ollama/openai/...）")
    base_url: str = Field("", description="API 基地址")
    api_key: str = Field("", description="API Key（云端 Provider 用，明文传入，服务端加密存储）")
    default_model: str = Field("", description="默认模型名称")
    timeout: int = Field(60, ge=5, le=600)
    max_retries: int = Field(3, ge=0, le=10)
    retry_delay: float = Field(1.0, ge=0.0, le=30.0)
    enabled: bool = Field(True)
    priority: int = Field(0, ge=0, le=100)
    capabilities: list[str] = Field(
        default_factory=lambda: ["chat"],
        description="能力标签列表",
    )
    extra: dict[str, Any] = Field(default_factory=dict)


class ProviderUpdateRequest(BaseModel):
    """部分更新 Provider 请求体（所有字段可选）。"""

    name: str | None = None
    base_url: str | None = None
    api_key: str | None = Field(None, description="留空表示不更新；显式传空串表示清除")
    default_model: str | None = None
    timeout: int | None = Field(None, ge=5, le=600)
    max_retries: int | None = Field(None, ge=0, le=10)
    retry_delay: float | None = Field(None, ge=0.0, le=30.0)
    enabled: bool | None = None
    priority: int | None = Field(None, ge=0, le=100)
    capabilities: list[str] | None = None
    extra: dict[str, Any] | None = None


class ChatTestRequest(BaseModel):
    """Provider 调用测试请求体。"""

    messages: list[dict[str, str]] = Field(..., description="消息列表，例如 [{'role':'user','content':'hello'}]")
    max_tokens: int = Field(256, ge=1, le=8192)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    model: str | None = Field(None, description="可选，覆盖默认模型")


# 工具函数


def _parse_provider_type(value: str) -> ProviderType:
    """将字符串解析为 ProviderType 枚举，失败时抛 400。"""
    try:
        return ProviderType(value)
    except ValueError as e:
        logger.info("不支持的 provider_type: %s", value)
        raise HTTPException(
            status_code=400,
            detail="不支持的 provider_type",
        ) from e


def _parse_capabilities(values: list[str]) -> list[ProviderCapability]:
    """将字符串列表解析为 ProviderCapability 枚举列表。"""
    result: list[ProviderCapability] = []
    for v in values:
        try:
            result.append(ProviderCapability(v))
        except ValueError as e:
            logger.info("不支持的能力标签: %s", v)
            raise HTTPException(
                status_code=400,
                detail="不支持的能力标签",
            ) from e
    return result


def _build_config(req: ProviderCreateRequest) -> ProviderConfig:
    """从请求体构造 ProviderConfig。"""
    return ProviderConfig(
        provider_id=req.provider_id,
        name=req.name,
        provider_type=_parse_provider_type(req.provider_type),
        base_url=req.base_url,
        api_key=req.api_key,
        default_model=req.default_model,
        timeout=req.timeout,
        max_retries=req.max_retries,
        retry_delay=req.retry_delay,
        enabled=req.enabled,
        priority=req.priority,
        capabilities=_parse_capabilities(req.capabilities),
        extra=req.extra,
    )


def _apply_update(config: ProviderConfig, req: ProviderUpdateRequest) -> ProviderConfig:
    """将部分更新应用到现有 ProviderConfig。"""
    if req.name is not None:
        config.name = req.name
    if req.base_url is not None:
        config.base_url = req.base_url
    if req.api_key is not None:
        config.api_key = req.api_key
    if req.default_model is not None:
        config.default_model = req.default_model
    if req.timeout is not None:
        config.timeout = req.timeout
    if req.max_retries is not None:
        config.max_retries = req.max_retries
    if req.retry_delay is not None:
        config.retry_delay = req.retry_delay
    if req.enabled is not None:
        config.enabled = req.enabled
    if req.priority is not None:
        config.priority = req.priority
    if req.capabilities is not None:
        config.capabilities = _parse_capabilities(req.capabilities)
    if req.extra is not None:
        config.extra = req.extra
    return config


# 读取类端点（登录可见）


@router.get("", summary="列出所有 Provider 配置", responses={500: {"model": ErrorResponse}})
async def list_providers(
    include_disabled: bool = Query(True, description="是否包含已禁用的 Provider"),
):
    registry = get_registry()
    configs = registry.list_providers(include_disabled=include_disabled)
    return {
        "ok": True,
        "data": [c.to_dict() for c in configs],
        "total": len(configs),
    }


@router.get("/status", summary="Provider 注册表状态摘要", responses={500: {"model": ErrorResponse}})
async def get_status():
    registry = get_registry()
    return {"ok": True, "data": registry.get_status_summary()}


@router.get("/active", summary="获取当前激活的 Provider", responses={500: {"model": ErrorResponse}})
async def get_active_provider():
    registry = get_registry()
    config = registry.get_active_provider_config()
    if config is None:
        return {"ok": True, "data": None}
    return {"ok": True, "data": config.to_dict()}


# 静态路径端点（必须在 /{provider_id} 之前注册，避免被路径参数吞掉）


@router.get("/types", summary="列出所有支持的 Provider 类型", responses={500: {"model": ErrorResponse}})
async def list_provider_types():
    """返回所有支持的 Provider 类型及其中文描述。"""
    descriptions = {
        ProviderType.OLLAMA: "Ollama（本地）",
        ProviderType.LMSTUDIO: "LM Studio（本地）",
        ProviderType.LLAMACPP: "llama.cpp（本地）",
        ProviderType.VLLM: "vLLM（本地）",
        ProviderType.TGI: "Text Generation Inference（本地）",
        ProviderType.KOBOLDCPP: "KoboldCpp（本地）",
        ProviderType.OPENAI: "OpenAI（云端）",
        ProviderType.ANTHROPIC: "Anthropic Claude（云端）",
        ProviderType.DEEPSEEK: "DeepSeek（云端）",
        ProviderType.QWEN: "通义千问 Qwen（云端）",
        ProviderType.GEMINI: "Google Gemini（云端）",
        ProviderType.OPENAI_COMPATIBLE: "OpenAI 兼容 API（自定义）",
    }
    return {
        "ok": True,
        "data": [
            {
                "value": t.value,
                "label": descriptions.get(t, t.value),
                "is_local": t.is_local,
                "is_cloud": t.is_cloud,
            }
            for t in ProviderType
        ],
    }


@router.get("/capabilities", summary="列出所有支持的能力标签")
async def list_capabilities():
    return {
        "ok": True,
        "data": [{"value": c.value, "label": c.value} for c in ProviderCapability],
    }


@router.get("/detect/preview", summary="预览自动探测结果（不写入数据库）")
async def detect_preview():
    """扫描本机 LLM 服务，返回探测结果，不修改任何配置。"""
    detector = get_detector()
    try:
        results = await asyncio.wait_for(detector.detect_all(), timeout=LLM_DETECT_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"自动探测超时（{int(LLM_DETECT_TIMEOUT_SEC)}s），请检查本地服务状态",
        )
    return {
        "ok": True,
        "data": [r.to_dict() for r in results],
        "available_count": sum(1 for r in results if r.is_available),
    }


@router.post(
    "/detect/import",
    summary="执行自动探测并导入可用 Provider",
    dependencies=[Depends(require_permission("system:config"))],
)
async def detect_and_import():
    """扫描本机 LLM 服务，将可用 Provider 导入数据库。

    - 仅导入 provider_id 不存在的配置（不覆盖用户已有配置）
    - 如果当前无激活 Provider，自动激活第一个探测到的本地 Provider
    """
    detector = get_detector()
    try:
        results = await asyncio.wait_for(detector.detect_all(), timeout=LLM_DETECT_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"自动探测超时（{int(LLM_DETECT_TIMEOUT_SEC)}s）",
        )

    configs = detector.generate_provider_configs(results)
    registry = get_registry()
    imported = registry.import_from_detection(configs)

    return {
        "ok": True,
        "data": {
            "detected_total": len(results),
            "detected_available": sum(1 for r in results if r.is_available),
            "imported": imported,
            "details": [r.to_dict() for r in results],
        },
    }


@router.get("/router/status", summary="获取 Provider 路由器状态")
async def get_router_status():
    """返回路由器当前状态，包括各 Provider 的延迟统计。"""
    router_instance = get_router()
    return {"ok": True, "data": router_instance.get_status()}


@router.get(
    "/router/strategies",
    summary="列出所有支持的路由策略",
)
async def list_routing_strategies():
    descriptions = {
        RoutingStrategy.ACTIVE_ONLY: "仅使用激活的 Provider",
        RoutingStrategy.PRIORITY_FALLBACK: "按优先级降级",
        RoutingStrategy.CAPABILITY_MATCH: "按能力匹配",
        RoutingStrategy.LATENCY_FIRST: "延迟优先",
        RoutingStrategy.LOCAL_FIRST: "本地优先（成本最低）",
        RoutingStrategy.CLOUD_FIRST: "云端优先（质量最高）",
    }
    return {
        "ok": True,
        "data": [
            {
                "value": s,
                "label": descriptions.get(s, s),
            }
            for s in [
                RoutingStrategy.ACTIVE_ONLY,
                RoutingStrategy.PRIORITY_FALLBACK,
                RoutingStrategy.CAPABILITY_MATCH,
                RoutingStrategy.LATENCY_FIRST,
                RoutingStrategy.LOCAL_FIRST,
                RoutingStrategy.CLOUD_FIRST,
            ]
        ],
    }


# 路径参数端点（/{provider_id} 必须放在所有静态路径之后）


@router.get("/{provider_id}", summary="获取指定 Provider 配置")
async def get_provider(provider_id: str):
    registry = get_registry()
    config = registry.get_provider(provider_id)
    if config is None:
        logger.info("Provider 不存在: %s", provider_id)
        raise HTTPException(404, "Provider 不存在")
    return {"ok": True, "data": config.to_dict()}


@router.get("/{provider_id}/models", summary="列出指定 Provider 可用的模型")
async def list_provider_models(provider_id: str):
    """动态拉取 Provider 当前可用的模型列表。"""
    registry = get_registry()
    instance = registry.get_provider_instance(provider_id)
    if instance is None:
        logger.info("Provider 不存在或未启用: %s", provider_id)
        raise HTTPException(
            status_code=404,
            detail="Provider 不存在或未启用",
        )
    try:
        models = await instance.list_models()
    except Exception as e:
        # [P0-18] 避免异常类型名泄露：502 错误仅返回通用提示 + error_id，
        # safe_error_message 内部已 logger.exception 记录堆栈与 context
        safe = safe_error_message(
            e,
            fallback="列出模型失败，请检查 Provider 服务状态",
            context=f"llm_providers.list_models[{provider_id}]",
        )
        raise HTTPException(status_code=502, detail=safe) from e
    return {"ok": True, "data": models, "count": len(models)}


@router.get("/{provider_id}/health", summary="健康检查指定 Provider")
async def health_check_provider(provider_id: str):
    registry = get_registry()
    instance = registry.get_provider_instance(provider_id)
    if instance is None:
        logger.info("Provider 不存在或未启用: %s", provider_id)
        raise HTTPException(
            status_code=404,
            detail="Provider 不存在或未启用",
        )
    try:
        status = await instance.health_check()
    except Exception as e:
        logger.warning("健康检查失败 (%s): %s", provider_id, e, exc_info=True)
        return {
            "ok": True,
            "data": {
                "status": "unknown",
                "error": type(e).__name__,
                "latency_ms": None,
            },
        }
    return {
        "ok": True,
        "data": {
            "status": status.value,
            "latency_ms": instance.latency_ms,
        },
    }


# 写入类端点（需要 system:config 权限）


@router.post(
    "",
    summary="新增 Provider 配置",
    dependencies=[Depends(require_permission("system:config"))],
)
async def create_provider(req: ProviderCreateRequest):
    registry = get_registry()
    # 检查是否已存在
    if registry.get_provider(req.provider_id) is not None:
        logger.info("Provider 已存在: %s", req.provider_id)
        raise HTTPException(
            status_code=409,
            detail="Provider 已存在，请使用 PUT 更新",
        )
    config = _build_config(req)
    registry.upsert_provider(config)
    return {"ok": True, "data": config.to_dict()}


@router.put(
    "/{provider_id}",
    summary="更新 Provider 配置",
    dependencies=[Depends(require_permission("system:config"))],
)
async def update_provider(provider_id: str, req: ProviderUpdateRequest):
    registry = get_registry()
    existing = registry.get_provider(provider_id)
    if existing is None:
        logger.info("Provider 不存在: %s", provider_id)
        raise HTTPException(404, "Provider 不存在")
    # 防止通过 PUT 修改 provider_id 或 provider_type
    updated = _apply_update(existing, req)
    updated.provider_id = provider_id  # 强制保持一致
    registry.upsert_provider(updated)
    return {"ok": True, "data": updated.to_dict()}


@router.delete(
    "/{provider_id}",
    summary="删除 Provider 配置",
    dependencies=[Depends(require_permission("system:config"))],
)
async def delete_provider(provider_id: str):
    registry = get_registry()
    deleted = registry.delete_provider(provider_id)
    if not deleted:
        logger.info("Provider 不存在: %s", provider_id)
        raise HTTPException(404, "Provider 不存在")
    return {"ok": True, "message": f"Provider 已删除: {provider_id}"}


@router.post(
    "/{provider_id}/activate",
    summary="激活指定 Provider（互斥）",
    dependencies=[Depends(require_permission("system:config"))],
)
async def activate_provider(provider_id: str):
    registry = get_registry()
    success = registry.set_active(provider_id)
    if not success:
        logger.info("激活失败，Provider 不存在或未启用: %s", provider_id)
        raise HTTPException(
            status_code=400,
            detail="Provider 不存在或未启用",
        )
    return {"ok": True, "message": f"已激活 Provider: {provider_id}"}


@router.post(
    "/{provider_id}/enable",
    summary="启用/禁用 Provider",
    dependencies=[Depends(require_permission("system:config"))],
)
async def enable_provider(provider_id: str, enabled: bool = Query(..., description="True=启用，False=禁用")):
    registry = get_registry()
    success = registry.set_enabled(provider_id, enabled)
    if not success:
        logger.info("Provider 不存在: %s", provider_id)
        raise HTTPException(404, "Provider 不存在")
    return {
        "ok": True,
        "message": f"Provider {provider_id} 已{'启用' if enabled else '禁用'}",
    }


@router.post(
    "/{provider_id}/test",
    summary="测试 Provider 调用（发送一条对话）",
    dependencies=[Depends(require_permission("system:config"))],
)
async def test_provider(provider_id: str, req: ChatTestRequest):
    """向指定 Provider 发送一条测试对话，返回响应内容与延迟。"""
    registry = get_registry()
    instance = registry.get_provider_instance(provider_id)
    if instance is None:
        logger.info("Provider 不存在或未启用: %s", provider_id)
        raise HTTPException(
            status_code=404,
            detail="Provider 不存在或未启用",
        )

    start = time.time()
    try:
        result = await instance.chat_completion(
            messages=req.messages,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            model=req.model,
        )
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        logger.warning("Provider 测试失败 (%s): %s", provider_id, e, exc_info=True)
        # 包装异常消息，避免直接回显内部错误细节
        safe = safe_error_message(
            e, context=f"llm_providers.test_provider[{provider_id}]", fallback="Provider 测试失败"
        )
        return {
            "ok": False,
            "data": {
                "error": type(e).__name__,
                "message": safe["message"],
                "error_id": safe["error_id"],
                "elapsed_ms": round(elapsed_ms, 2),
            },
        }
    elapsed_ms = (time.time() - start) * 1000
    result["elapsed_ms"] = round(elapsed_ms, 2)
    return {"ok": True, "data": result}
