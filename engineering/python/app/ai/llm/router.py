"""LLM Provider 智能路由器。

负责根据请求需求选择最合适的 Provider 实例，支持：
- 用户显式选择（指定 provider_id）
- 按能力路由（function_calling / vision / streaming）
- 按延迟路由（最近一次健康检查的延迟）
- 按成本路由（云端优先/本地优先）
- 按优先级降级（primary 不可用时自动切换到 fallback）

设计原则：
- 优先使用用户激活的 Provider（显式选择）
- 无显式选择时按 capability + priority + latency 综合打分
- 失败时按优先级列表自动降级
- 不抛异常：所有 Provider 不可用时返回 None，由调用方决定降级到规则

本模块为门面：实现已拆分至 _router_models / _latency_cache / _router_core。
"""

from __future__ import annotations

import threading

from app.ai.llm._latency_cache import LatencyCache  # noqa: F401
from app.ai.llm._router_core import ProviderRouter  # noqa: F401
from app.ai.llm._router_models import (  # noqa: F401
    RoutingRequest,
    RoutingResult,
    RoutingStrategy,
)

# 全局单例

_router: ProviderRouter | None = None
_router_lock = threading.Lock()


def get_router() -> ProviderRouter:
    """获取全局 ProviderRouter 实例（双重检查锁，线程安全）。"""
    global _router
    if _router is not None:
        return _router
    with _router_lock:
        if _router is None:
            _router = ProviderRouter()
    return _router


def reset_router() -> None:
    """重置全局路由器（主要供测试使用）。"""
    global _router
    with _router_lock:
        _router = None
