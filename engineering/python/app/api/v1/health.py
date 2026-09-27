"""System health check API endpoints."""

from __future__ import annotations

import datetime
import json
import logging
import platform
import sys
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends

from app.dependencies import get_config
from app.config import AppConfig
from app.version import VERSION as PY_VERSION
from app.database.connection import check_db_health
from app.services.redis_client import check_redis_health
from app.services.tdengine_client import check_tdengine_health

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/health", tags=["Health Check"])
simple_health_router = APIRouter(tags=["Health Check"])

APP_START = time.time()

# 健康检查 HTTP 请求超时（秒），用于 Ollama 等外部服务状态探测
HEALTH_CHECK_TIMEOUT = 5


def _get_python_info() -> dict[str, Any]:
    return {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "arch": platform.machine(),
        "app_version": PY_VERSION,
    }


def _get_package_versions() -> dict[str, str]:
    packages = ["fastapi", "httpx", "pydantic", "uvicorn", "numpy", "torch"]
    versions: dict[str, str] = {}
    for pkg in packages:
        try:
            mod = __import__(pkg)
            versions[pkg] = getattr(mod, "__version__", "unknown")
        except ImportError:
            versions[pkg] = "not installed"
    return versions


async def _get_ollama_status(cfg: AppConfig) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=HEALTH_CHECK_TIMEOUT) as client:
            response = await client.get(f"{cfg.ai.ollama_base_url}/api/tags")
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                return {
                    "running": True,
                    "base_url": cfg.ai.ollama_base_url,
                    "model_count": len(models),
                    "models": [
                        {
                            "name": m.get("name", "unknown"),
                            "size_mb": round(m.get("size", 0) / 1_048_576, 1),
                        }
                        for m in models
                    ],
                }
            return {"running": False, "error": f"HTTP {response.status_code}"}
    except httpx.ConnectError:
        return {"running": False, "error": "Connection refused — Ollama is not running"}
    except httpx.TimeoutException:
        return {"running": False, "error": "Connection timeout"}
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as e:
        # 兜底捕获：HTTP 请求可能抛出网络层/序列化层未预期的异常
        # 健康检查端点应始终返回结构化数据，不应向上抛 5xx
        # 修复：仅返回异常类型名，避免泄露内部路径/库版本等敏感信息
        logger.debug("Ollama健康检查异常: %s", e, exc_info=True)
        return {"running": False, "error": f"unhealthy: {type(e).__name__}"}


def _get_disk_info() -> dict[str, Any]:
    try:
        import shutil

        usage = shutil.disk_usage("/")
        return {
            "total_gb": round(usage.total / 1_073_741_824, 1),
            "used_gb": round(usage.used / 1_073_741_824, 1),
            "free_gb": round(usage.free / 1_073_741_824, 1),
            "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0,
        }
    except (OSError, PermissionError, ValueError) as e:
        # 兜底捕获：磁盘信息读取可能因 OSError/权限错误失败
        # 修复：仅返回异常类型名
        logger.warning("Failed to get disk info: %s", e, exc_info=True)
        return {"error": f"unavailable: {type(e).__name__}"}


def _get_memory_info() -> dict[str, Any]:
    try:
        import psutil

        mem = psutil.virtual_memory()
        return {
            "total_mb": round(mem.total / 1_048_576, 1),
            "available_mb": round(mem.available / 1_048_576, 1),
            "used_percent": mem.percent,
        }
    except ImportError:
        return {"error": "psutil not installed"}
    except (OSError, AttributeError, ValueError) as e:
        # 兜底捕获：psutil 内部可能因 /proc 不可读抛出 OSError
        # 修复：仅返回异常类型名
        logger.warning("Failed to get memory info: %s", e, exc_info=True)
        return {"error": f"unavailable: {type(e).__name__}"}


def _get_lnn_weights_status() -> dict[str, Any]:
    """LNN 随包权重装载状态（weights_source 显式化）。

    叙事红线配套：随机初始化权重必须可辨识，不得被当作 AI 能力对外宣称
    （docs/development/lnn-权重训练与分发接线方案.md WP1/WP4）。
    """
    try:
        from app.ai.lnn.inference.registry import LNNModelRegistry
        from app.services.model_registry_service import _default_registry_model_dir

        registry = LNNModelRegistry(model_dir=_default_registry_model_dir())
        models: dict[str, Any] = {}
        trained = 0
        for name in registry.list_models():
            verdict = registry.validate_model(name)
            models[name] = {
                "weights_source": verdict.get("weights_source"),
                "weights_loaded": bool(verdict.get("load_test_passed")),
                "path": verdict.get("model_path"),
            }
            if verdict.get("weights_source") == "trained":
                trained += 1
        return {"trained_count": trained, "total": len(models), "models": models}
    except (OSError, ValueError, RuntimeError, ImportError) as e:
        logger.warning("Failed to get LNN weights status: %s", e, exc_info=True)
        return {"error": f"unavailable: {type(e).__name__}"}


@router.get("/system")
async def system_health(config: AppConfig = Depends(get_config)):
    """Full system health check — returns status of all components."""
    ollama = await _get_ollama_status(config)
    disk = _get_disk_info()
    memory = _get_memory_info()
    python_info = _get_python_info()
    packages = _get_package_versions()

    # 数据库健康检查
    db_health = await check_db_health()
    redis_health = await check_redis_health()
    tdengine_health = await check_tdengine_health()

    items = []

    items.append(
        {
            "component": "python",
            "name": "Python 运行环境",
            "status": "ok",
            "version": python_info["version"],
            "details": python_info,
        }
    )

    if ollama["running"]:
        items.append(
            {
                "component": "ollama",
                "name": "Ollama AI 服务",
                "status": "ok",
                "version": ollama.get("base_url", ""),
                "details": ollama,
            }
        )
    else:
        items.append(
            {
                "component": "ollama",
                "name": "Ollama AI 服务",
                "status": "error",
                "version": None,
                "details": ollama,
            }
        )

    if ollama.get("model_count", 0) > 0:
        items.append(
            {
                "component": "models",
                "name": "AI 模型文件",
                "status": "ok",
                "version": str(ollama["model_count"]),
                "details": ollama.get("models", []),
            }
        )
    elif ollama["running"]:
        items.append(
            {
                "component": "models",
                "name": "AI 模型文件",
                "status": "error",
                "version": "0",
                "details": {"models": [], "suggestion": "run: ollama pull qwen3.5:35b-128k"},
            }
        )
    else:
        items.append(
            {
                "component": "models",
                "name": "AI 模型文件",
                "status": "warning",
                "version": None,
                "details": {"models": [], "note": "Ollama not running, cannot check models"},
            }
        )

    if disk.get("error") is None:
        free_gb = disk.get("free_gb", 0)
        status = "ok" if free_gb > 5 else ("warning" if free_gb > 1 else "error")
        items.append(
            {
                "component": "disk",
                "name": "系统磁盘空间",
                "status": status,
                "version": None,
                "details": disk,
            }
        )
    else:
        items.append(
            {
                "component": "disk",
                "name": "系统磁盘空间",
                "status": "warning",
                "version": None,
                "details": disk,
            }
        )

    if memory.get("error") is None:
        used_pct = memory.get("used_percent", 0)
        status = "ok" if used_pct < 80 else ("warning" if used_pct < 95 else "error")
        items.append(
            {
                "component": "memory",
                "name": "系统内存",
                "status": status,
                "version": None,
                "details": memory,
            }
        )
    else:
        items.append(
            {
                "component": "memory",
                "name": "系统内存",
                "status": "warning",
                "version": None,
                "details": memory,
            }
        )

    items.append(
        {
            "component": "packages",
            "name": "Python 依赖包",
            "status": "ok",
            "version": None,
            "details": packages,
        }
    )

    # PostgreSQL 健康检查
    db_status = db_health.get("status", "disabled")
    items.append(
        {
            "component": "postgresql",
            "name": "PostgreSQL 数据库",
            "status": "ok" if db_status == "healthy" else ("warning" if db_status == "disabled" else "error"),
            "version": None,
            "details": db_health,
        }
    )

    # Redis 健康检查
    redis_status = redis_health.get("status", "disabled")
    items.append(
        {
            "component": "redis",
            "name": "Redis 缓存",
            "status": "ok" if redis_status == "healthy" else ("warning" if redis_status == "disabled" else "error"),
            "version": None,
            "details": redis_health,
        }
    )

    # TDengine 健康检查
    tdengine_status = tdengine_health.get("status", "disabled")
    items.append(
        {
            "component": "tdengine",
            "name": "TDengine 时序数据库",
            "status": "ok"
            if tdengine_status == "healthy"
            else ("warning" if tdengine_status == "disabled" else "error"),
            "version": None,
            "details": tdengine_health,
        }
    )

    # LNN 随包权重（无已训练权重为 warning 而非 error：随机初始化可降级运行，
    # 但状态必须显式可辨）
    lnn_weights = _get_lnn_weights_status()
    items.append(
        {
            "component": "lnn_weights",
            "name": "LNN 随包权重",
            "status": "ok" if lnn_weights.get("trained_count") else "warning",
            "version": None,
            "details": lnn_weights,
        }
    )

    overall_status = "ok"
    for item in items:
        if item["status"] == "error":
            overall_status = "error"
            break
        elif item["status"] == "warning":
            overall_status = "warning"

    return {
        "status": overall_status,
        "timestamp": time.time(),
        "app_version": PY_VERSION,
        "uptime_seconds": round(time.time() - APP_START, 1),
        "items": items,
    }


@router.get("/quick")
async def quick_health():
    """Quick health check — returns a simple OK/ERROR status."""
    return {"status": "ok", "version": PY_VERSION, "uptime": round(time.time() - APP_START, 1)}


# 标准化健康检查端点（容器/探针专用）
# 标准化端点设计：
# - GET /api/health — 主健康检查，返回标准 JSON（status/version/timestamp）
# - GET /api/health/ping — 轻量级存活探测，仅返回 {"ping": true}，
# 用于 Docker HEALTHCHECK 等高频探活场景
# 两个端点均为公开访问路径（unified_auth 的 PUBLIC_PATHS 中已注册），
# 不需要任何身份验证或授权校验。
# 旧的根路径 /health 已在主程序中彻底移除，避免端点重复和潜在混淆。


@simple_health_router.get(
    "/api/health",
    summary="标准化主健康检查",
    description='返回标准格式：{"status": "ok", "version": "x.x.x", "timestamp": "<ISO 8601>"}',
)
async def main_health():
    """主健康检查端点 — 返回统一格式的健康状态。

    P1-13 修复：主健康端点必须检查关键依赖（DB）连通性，
    否则 K8s readinessProbe 在 DB 不可达时仍返回 ok，导致流量
    被路由到无法服务的实例。轻量 DB ping 失败时：
    - status 降级为 "degraded"
    - HTTP 状态码返回 503（Service Unavailable），确保 K8s
      readinessProbe 正确将 Pod 标记为 NotReady，停止路由流量
    - response body 仍包含完整状态信息，供监控系统告警使用

    返回格式: {"status": "ok"|"degraded", "version": "x.x.x", "timestamp": "..."}
    - status: "ok" 表示 DB 可达；"degraded" 表示 DB 不可达
    - version: 动态获取的应用版本号（来自 app.version）
    - timestamp: ISO 8601 格式（UTC）的当前时间戳
    """
    from fastapi import Response

    # P1-13：轻量 DB 连通性检查（仅 SELECT 1）
    # check_db_health 返回 dict：{"status": "healthy"|"unhealthy"|"disabled", ...}
    # - "healthy"：DB 可达
    # - "disabled"：DB_URL 未配置（桌面模式可能不使用 DB，视为 ok）
    # - "unhealthy"：DB 不可达
    db_ok = False
    try:
        db_status = await check_db_health()
        db_ok = db_status.get("status") in ("healthy", "disabled")
    except Exception as exc:
        logger.warning("主健康检查 DB ping 失败: %s", exc, exc_info=True)

    body = {
        "status": "ok" if db_ok else "degraded",
        "version": PY_VERSION,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "dependencies": {"database": "ok" if db_ok else "error"},
    }
    # P1-13：DB 不可达时返回 503，确保 K8s readinessProbe 将 Pod 标记为 NotReady
    return Response(
        content=json.dumps(body),
        media_type="application/json",
        status_code=200 if db_ok else 503,
    )


@simple_health_router.get(
    "/api/health/ping",
    summary="轻量级健康探针",
    description='轻量级存活探测，严格返回 {"ping": true}，用于 Docker HEALTHCHECK',
)
async def ping():
    """轻量级 ping 检查端点 — 返回简单的存活状态。

    用于 Docker HEALTHCHECK 等轻量级健康探测场景。
    返回格式: {"ping": true}
    """
    return {"ping": True}
