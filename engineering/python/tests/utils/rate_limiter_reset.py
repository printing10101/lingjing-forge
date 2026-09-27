"""限流器状态清理工具（测试隔离用）.

背景（2026-09 工程体检）：``app/api/v1/auth.py`` 在一次 pytest 进程内可能被
执行两次（conftest 的懒加载桩 ``_resolve("auth")`` 一次 + 正常 import 链一次，
是否发生取决于模块收集顺序）。slowapi 的 ``Limiter`` 按
``"模块路径.函数名"`` 在 ``_route_limits`` 中 **追加** 限流条目，双次执行会
产生两条相同的 ``3/hour`` 条目 → 每个请求计数两次 → 注册端点 3/hour 实际
变成 1.5/hour，``test_auth_register`` 的 409/429 断言被提前触发的 429 击穿
（非确定性、依赖导入顺序，全量跑时偶发）。

本模块提供统一清理入口，供根 conftest 的 autouse fixture 在每个测试前后调用：
1. ``slowapi`` 计数存储清零（``Limiter.reset()``）；
2. ``_route_limits`` 按 ``(限额字符串, scope)`` 去重，消除双次执行残留；
3. 自研 ``permission_checker._rate_limiter`` IP 计数清零。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _dedupe_limits_entry(lims: list[Any]) -> list[Any]:
    """按 ``(限额字符串, scope)`` 去重限流条目，保持原有顺序.

    对缺失属性的对象（异常条目）原样保留，绝不因清理失败抛错。
    """
    seen: set[tuple[str, Any]] = set()
    result: list[Any] = []
    for lim in lims:
        identity = (str(getattr(lim, "limit", lim)), getattr(lim, "scope", None))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(lim)
    return result


def reset_all_rate_limiters() -> None:
    """重置全部限流器状态（计数清零 + 路由限流条目去重）.

    任何一步失败都不抛出——清理失败不应把无关测试搞挂，但会记日志。
    """
    # 1) slowapi 共享 limiter（auth/login/register 等端点 3/hour、5/minute 等）
    try:
        from app.middleware.rate_limiter import limiter as _slowapi_limiter

        _slowapi_limiter.reset()
        route_limits = getattr(_slowapi_limiter, "_route_limits", None)
        if isinstance(route_limits, dict):
            for name, lims in list(route_limits.items()):
                if isinstance(lims, list) and len(lims) > 1:
                    deduped = _dedupe_limits_entry(lims)
                    if len(deduped) != len(lims):
                        logger.debug("route_limits[%s] 去重: %d -> %d", name, len(lims), len(deduped))
                        route_limits[name] = deduped
    except Exception as e:  # noqa: BLE001
        logger.debug("slowapi limiter 清理失败（不影响测试继续）: %s", e)

    # 2) 自研权限层 IP 计数器
    try:
        from app.auth.permissions import permission_checker

        permission_checker._rate_limiter.clear()
    except Exception as e:  # noqa: BLE001
        logger.debug("permission_checker 限流清理失败（不影响测试继续）: %s", e)
