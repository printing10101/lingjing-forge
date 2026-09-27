"""Agent rate limiter (re-export shim).

P2 整改：本模块原为独立的 ``AgentRateLimiter`` 实现（无锁版），与
``app.agent.middleware.AgentRateLimiter``（线程安全版，加 threading.Lock
保护并发访问）功能重复且不一致。

合并后本模块仅作为 re-export shim，所有公开符号从 ``app.agent.middleware``
导入，确保：

1. 全进程只有一个 ``AgentRateLimiter`` 实例 → 限流计数一致
2. ``app.auth.middleware`` / ``app.auth.unified_auth`` 等调用方无需修改导入路径
   （向后兼容）
"""

from __future__ import annotations

# ruff: noqa: F822 # re-export shim：__all__ 符号经 __getattr__ 惰性解析（见模块 docstring）

__all__ = ["AgentRateLimiter", "agent_rate_limiter"]


def __getattr__(name: str):
    if name == "AgentRateLimiter":
        from app.agent.middleware import AgentRateLimiter

        return AgentRateLimiter
    if name == "agent_rate_limiter":
        from app.agent.middleware import agent_rate_limiter

        return agent_rate_limiter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
