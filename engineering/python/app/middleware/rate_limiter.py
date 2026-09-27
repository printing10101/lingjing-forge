"""Shared rate limiter instance for the whole application.

Uses slowapi with in-memory storage (no Redis required).
Imported by main.py, auth.py, and lnn.py to apply consistent rate limits.
"""

from __future__ import annotations

import logging
import re

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)

# P2-4-2 修复：提取魔法数字为模块级常量
_DEFAULT_RETRY_WINDOW_SECONDS = 60

# P2-4-1 修复：自定义 key 函数处理 X-Forwarded-For，支持反向代理场景。
# slowapi 默认的 get_remote_address 直接读取 request.client.host（TCP 对端），
# 在 Nginx/CDN/负载均衡后所有请求 IP 相同，限速失效且可被伪造 X-Forwarded-For 绕过。
# 此函数取 X-Forwarded-For 首段（最原始客户端），回退到 connection IP。
# 注意：必须配合可信代理配置（Nginx 设置 X-Real-IP），否则攻击者可伪造该头。


def _get_real_client_ip(request: Request) -> str:
    """从 X-Forwarded-For 提取真实客户端 IP，回退到 connection IP。

    取 X-Forwarded-For 首段（最原始的客户端 IP）。若头不存在则回退到
    ``request.client.host``。返回 "unknown" 仅在 client 信息完全缺失时。
    """
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        # 取第一个 IP（最原始的客户端），strip 防止首尾空格干扰
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


# Shared rate limiter instance (in-memory, keyed by real client IP)
limiter = Limiter(key_func=_get_real_client_ip)


async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """自定义速率限制错误处理器，返回中文友好提示消息。

    根据不同的速率限制窗口（秒/分钟/小时）动态生成提示文本。
    返回429状态码，并携带 Retry-After 响应头。
    """
    detail = str(exc.detail) if exc.detail else ""

    # 从 slowapi 的 detail 信息中提取限制窗口大小（如 "5 per 1 minute"）
    # P2-4-2 修复：使用模块级常量替代魔法数字
    retry_window_seconds = _DEFAULT_RETRY_WINDOW_SECONDS
    match = re.search(r"per\s+(\d+)\s+(second|minute|hour)", detail, re.IGNORECASE)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        unit_map = {"second": 1, "minute": 60, "hour": 3600}
        retry_window_seconds = amount * unit_map.get(unit, 60)

    # 生成中文友好提示
    if retry_window_seconds >= 3600:
        hours = retry_window_seconds // 3600
        message = f"请求过于频繁，请在{hours}小时后重试"
    elif retry_window_seconds >= 60:
        minutes = retry_window_seconds // 60
        message = f"请求过于频繁，请在{minutes}分钟后重试"
    else:
        message = f"请求过于频繁，请在{retry_window_seconds}秒后重试"

    logger.warning(
        "Rate limit exceeded: path=%s client=%s window=%ds detail=%s",
        request.url.path,
        request.client.host if request.client else "unknown",
        retry_window_seconds,
        detail,
    )

    return JSONResponse(
        status_code=429,
        content={
            # P1-18 修复：移除冗余 "error" 字段——它与 "message" 值相同且语义冲突
            # （unified_auth 中 error 是 snake_case 标识，这里是中文消息）。
            # 全局标准格式（response.py error_response）不含 error 字段。
            "message": message,
            "code": 1007,
            "request_id": get_request_id(),
        },
        headers={"Retry-After": str(retry_window_seconds)},
    )
