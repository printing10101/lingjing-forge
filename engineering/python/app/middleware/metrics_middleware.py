"""运行时指标中间件与 ``/api/metrics`` 端点（从 ``app/main.py`` 拆分）。

将原 ``main.py`` 中内联定义的指标观测设施集中到本模块，便于：

1. ``main.py`` 仅保留应用装配逻辑，单文件行数从 898 行降至 ~720 行；
2. 指标中间件与端点可独立测试（无需启动完整 FastAPI 应用）；
3. 后续若需替换为 prometheus_client 或其他指标后端，仅修改本模块。

设计约束：
- ``MetricsMiddleware`` 通过构造函数注入 ``metrics`` / ``ring_log``，
  避免对 ``main.py`` 模块级变量的闭包依赖；
- ``/api/metrics`` 端点使用 IP 白名单鉴权（默认仅 loopback + RFC 1918），
  防止运行时指标泄露给外部攻击者；
- ``metrics`` 对象由 ``app.utils.utils.get_metrics_collector()`` 提供（单例）；
- ``ring_log`` 对象由 ``app.utils.ring_buffer.get_ring_log_buffer()`` 提供（单例）。
"""

from __future__ import annotations

import ipaddress
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


# MetricsMiddleware：请求级指标观测


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录每个 HTTP 请求的耗时、状态码与路径指标。

    设计要点（P1-11 修复）：
    - 下游异常也必须记录指标，否则最需要观测的错误请求会从指标中消失；
    - 指标记录自身异常不得吞没已生成的响应；
    - ``response`` 预初始化为 ``None``，避免 except 路径中 finally 引用未定义变量。
    """

    def __init__(
        self,
        app: Any,
        metrics: Any,
        ring_log: Any,
    ) -> None:
        """初始化中间件。

        Args:
            app: ASGI 应用（由 ``add_middleware`` 自动注入）。
            metrics: ``MetricsCollector`` 单例，需提供 ``record(path, elapsed, status)`` 方法。
            ring_log: ``RingLogBuffer`` 单例，需提供 ``append(...)`` 方法。
        """
        super().__init__(app)
        self._metrics = metrics
        self._ring_log = ring_log

    async def dispatch(
        self,
        request: Request,
        call_next: Any,
    ) -> Response:
        start = time.perf_counter()
        status_code = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            # 下游抛异常时仍记录 500 指标后重新抛出，保证错误请求可观测
            try:
                elapsed = time.perf_counter() - start
                self._metrics.record(request.url.path, elapsed, 500)
            except Exception:
                logger.warning(
                    "metrics.record failed for failed request %s",
                    request.url.path,
                    exc_info=True,
                )
            raise
        finally:
            # 仅在正常返回时记录（异常路径已在 except 中记录）
            if response is not None:
                try:
                    elapsed = time.perf_counter() - start
                    # P0-14/15 修复：传入 status_code 以便按状态码族分类计入
                    # http_requests_total{status="..."}，使 HighErrorRate 告警可正常工作
                    if status_code != 500:
                        self._metrics.record(request.url.path, elapsed, status_code)
                    self._ring_log.append(
                        "request",
                        level="INFO",
                        source=request.url.path,
                        message=f"{request.method} {request.url.path}",
                        data={
                            "method": request.method,
                            "path": request.url.path,
                            "status": status_code,
                            "elapsed_ms": round(elapsed * 1000, 3),
                        },
                    )
                except Exception:
                    logger.warning(
                        "MetricsMiddleware observability sidecar failed for %s",
                        request.url.path,
                        exc_info=True,
                    )


# /api/metrics 端点 IP 白名单鉴权
#
# P1-12 修复：/api/metrics 暴露运行时指标（路径/权限/模型/错误率），
# 三层鉴权（PUBLIC_PATHS/_PUBLIC_ENDPOINTS_LNN/AUTH_PUBLIC_PATHS）全部放行，
# 任何未认证客户端均可获取。此处增加 IP 白名单作为终端防护：
# - 默认仅允许 loopback + RFC 1918 私有网段（Prometheus scraper 通常部署在内网）
# - 通过 LNN_METRICS_ALLOW_IPS 环境变量可自定义（逗号分隔，支持 CIDR）
# - 白名单外请求返回 403，避免指标数据泄露给外部攻击者

_DEFAULT_METRICS_ALLOW_IPS = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


def load_metrics_allowlist() -> tuple[
    list[ipaddress.IPv4Network | ipaddress.IPv6Network],
    list[ipaddress.IPv4Address | ipaddress.IPv6Address],
]:
    """解析 ``LNN_METRICS_ALLOW_IPS`` 环境变量为 ``(networks, addresses)`` 二元组。

    Returns:
        networks: CIDR 网段列表（如 ``10.0.0.0/8``）。
        addresses: 单 IP 地址列表（如 ``127.0.0.1``）。
    """
    raw = os.environ.get("LNN_METRICS_ALLOW_IPS", _DEFAULT_METRICS_ALLOW_IPS)
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for token in raw.split(","):
        item = token.split("#", 1)[0].strip()
        if not item:
            continue
        try:
            if "/" in item:
                networks.append(ipaddress.ip_network(item, strict=False))
            else:
                addresses.append(ipaddress.ip_address(item))
        except ValueError as exc:
            logger.warning("LNN_METRICS_ALLOW_IPS 无效条目 '%s': %s", item, exc)
    return networks, addresses


# 模块导入时一次性解析白名单（与环境变量读取时机一致）
_METRICS_NETWORKS, METRICS_ADDRESSES = load_metrics_allowlist()


def is_metrics_allowed(client_ip: str) -> bool:
    """检查客户端 IP 是否在 metrics 白名单中。

    IPv4/IPv6 类型不匹配时 ``==`` 返回 False（不抛异常），可直接比较；
    IPv4Address in IPv6Network 会抛 TypeError，需逐个 try。
    """
    if not client_ip:
        return False
    try:
        ip_obj = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    if any(ip_obj == addr for addr in METRICS_ADDRESSES):
        return True
    for net in _METRICS_NETWORKS:
        try:
            if ip_obj in net:
                return True
        except TypeError:
            continue
    return False


# /api/metrics 端点注册


def create_metrics_router(metrics: Any) -> APIRouter:
    """创建 ``/api/metrics`` 路由器。

    Args:
        metrics: ``MetricsCollector`` 单例，需提供 ``export()`` 方法返回
            Prometheus exposition format 字符串。

    Returns:
        APIRouter: 已注册 ``GET /api/metrics`` 端点的路由器（无 prefix）。
    """
    router = APIRouter()

    @router.get("/api/metrics")
    async def get_metrics(request: Request) -> Response:
        client_ip = request.client.host if request.client else ""
        if not is_metrics_allowed(client_ip):
            logger.warning(
                "/api/metrics 访问被拒（IP 不在白名单）: client_ip=%s, path=%s",
                client_ip,
                request.url.path,
            )
            return JSONResponse(
                content={"detail": "Forbidden: metrics endpoint not accessible from this IP"},
                status_code=403,
            )
        # P0-14/15 修复：使用 Prometheus exposition format 标准 media_type
        # （version=0.0.4），确保 Prometheus scraper 正确解析。
        return Response(
            content=metrics.export(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return router


def register_metrics_endpoint(app: FastAPI, metrics: Any) -> None:
    """在应用上注册 ``/api/metrics`` 端点。

    Args:
        app: FastAPI 应用实例。
        metrics: ``MetricsCollector`` 单例。
    """
    app.include_router(create_metrics_router(metrics))
