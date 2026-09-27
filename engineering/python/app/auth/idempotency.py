"""Idempotency store (re-export shim).

P2 整改：本模块原为独立的 ``IdempotencyStore`` 实现（带 max_entries 上限保护
和惰性清理），与 ``app.agent.middleware.IdempotencyStore``（无 entries 上限，
OOM 风险）功能重复。

合并策略：将本模块的更优实现（max_entries + _maybe_cleanup_locked）迁移到
``app.agent.middleware``，本模块改为 re-export shim，确保：

1. 全进程只有一个 ``IdempotencyStore`` 实例 → 幂等键一致
2. ``app.auth.middleware`` / ``app.auth.unified_auth`` 等调用方无需修改导入路径
   （向后兼容）
"""

from __future__ import annotations

# ruff: noqa: F822 # re-export shim：__all__ 符号经 __getattr__ 惰性解析（见模块 docstring）

__all__ = ["IdempotencyStore", "idempotency_store"]


def __getattr__(name: str):
    if name == "IdempotencyStore":
        from app.agent.middleware import IdempotencyStore

        return IdempotencyStore
    if name == "idempotency_store":
        from app.agent.middleware import idempotency_store

        return idempotency_store
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
