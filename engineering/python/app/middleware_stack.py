"""中间件链装配：注册顺序与期望执行顺序分离，便于维护.

注册顺序（内→外，后注册先执行）与期望执行顺序（外→内）对齐：
    期望执行顺序（外→内）：
      1. RequestIdMiddleware        - 生成 X-Request-ID，所有后续日志可关联
      2. SecurityHeadersMiddleware  - 纯 ASGI，添加安全响应头
      3. CORSMiddleware             - 处理预检 OPTIONS，必须早于 auth
      4. MetricsMiddleware          - 记录请求指标（BaseHTTPMiddleware）
      5. UnifiedAuthMiddleware      - 纯 ASGI，LNN+JWT+Agent 鉴权
      6. IdleAutoShutdownMiddleware - 空闲追踪（最内层）

关键：CORS 必须在 UnifiedAuth 外层，否则浏览器 OPTIONS 预检请求
会因缺少 Authorization 头被 auth 拦截返回 401，导致跨域前端无法工作。
RequestId 在最外层确保所有中间件日志都可关联同一请求 ID。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.auth.security_headers_asgi import SecurityHeadersMiddleware
from app.auth.unified_auth import UnifiedAuthMiddleware
from app.config import config
from app.core.request_id import RequestIdMiddleware
from app.middleware.cors_config import cors_settings
from app.middleware.metrics_middleware import (
    MetricsMiddleware,
    register_metrics_endpoint,
)
from app.middleware.rate_limiter import limiter, rate_limit_handler
from app.sidecar.sidecar_lifecycle import IdleAutoShutdownMiddleware

logger = logging.getLogger(__name__)


def register_middleware_stack(
    app: FastAPI,
    *,
    metrics,
    ring_log,
    state_file_path: str,
    idle_auto_shutdown_enabled: bool,
    idle_timeout_seconds: int,
) -> None:
    """注册所有中间件（注册顺序与期望执行顺序相反）.

    Args:
        app: FastAPI 应用实例
        metrics: MetricsCollector 实例
        ring_log: RingLogBuffer 实例
        state_file_path: sidecar 状态文件路径
        idle_auto_shutdown_enabled: 是否启用空闲自动关机
        idle_timeout_seconds: 空闲超时秒数
    """
    # 1. IdleAutoShutdownMiddleware（最内层，条件注册）
    # P1-1 修复：桌面 sidecar 模式下默认禁用（LNN_IDLE_AUTO_SHUTDOWN=false）
    if idle_auto_shutdown_enabled:
        app.add_middleware(
            IdleAutoShutdownMiddleware,
            idle_timeout=idle_timeout_seconds,
            state_file_path=state_file_path,
        )
        logger.info(
            "IdleAutoShutdownMiddleware enabled (timeout=%ds, state_file=%s)",
            idle_timeout_seconds,
            state_file_path,
        )
    else:
        logger.warning(
            "IdleAutoShutdownMiddleware disabled (LNN_IDLE_AUTO_SHUTDOWN=false, desktop mode)",
            exc_info=False,
        )

    # 2. UnifiedAuthMiddleware（鉴权，CORS 内层）
    app.add_middleware(
        UnifiedAuthMiddleware,
        lnn_auth_enabled=config.security.auth_enabled,
        lnn_permission_enforced=config.security.permission_enforced,
        jwt_auth_enabled=config.security.jwt_auth_enabled,
        agent_auth_enabled=config.security.agent_auth_enabled,
    )

    # 3. MetricsMiddleware（构造函数注入 metrics / ring_log，避免闭包依赖）
    app.add_middleware(MetricsMiddleware, metrics=metrics, ring_log=ring_log)

    # 4. CORSMiddleware（必须在 UnifiedAuth 外层，正确处理 OPTIONS 预检）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_settings.get_origins(),
        allow_origin_regex=cors_settings.get_origin_regex(),
        allow_credentials=cors_settings.allow_credentials,
        allow_methods=cors_settings.get_methods(),
        allow_headers=cors_settings.get_headers(),
        expose_headers=cors_settings.get_expose_headers(),
        max_age=cors_settings.max_age,
    )

    # 5. SecurityHeadersMiddleware（纯 ASGI，无 body 缓冲）
    app.add_middleware(SecurityHeadersMiddleware)

    # 6. RequestIdMiddleware（最外层，最先执行，生成 X-Request-ID）
    app.add_middleware(RequestIdMiddleware)

    # Rate limiting with slowapi
    if config.security.rate_limit_enabled:
        app.state.limiter = limiter
        # RateLimitExceeded 是 Exception 子类；starlette 泛型签名不接受更具体的
        # 处理器类型（运行时正常），类型层面 ignore
        app.add_exception_handler(RateLimitExceeded, rate_limit_handler)  # type: ignore[arg-type]
        logger.info("Rate limiting enabled (default: 100 req/min per IP, per-endpoint overrides apply)")
    else:
        logger.warning(
            "Rate limiting disabled via config (SECURITY: LLM/Jobs endpoints unprotected)",
            exc_info=False,
        )

    # P1-12 修复：/api/metrics 暴露运行时指标，三层鉴权全部放行，
    # 此处增加 IP 白名单作为终端防护。实现已迁移至 middleware.metrics_middleware。
    register_metrics_endpoint(app, metrics)
