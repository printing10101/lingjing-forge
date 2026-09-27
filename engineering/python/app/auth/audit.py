"""Agent audit log (re-export shim).

P2-1 修复：本模块原为独立的 ``AgentAuditLog`` 实现（简化版，无哈希链、
每次 open/close），与 ``app.agent.middleware.AgentAuditLog``（增强版，
带 SHA-256 哈希链防篡改、文件句柄缓存）功能重复且不一致。

合并后本模块仅作为 re-export shim，所有公开符号从 ``app.agent.middleware``
导入，确保：

1. 全进程只有一个 ``AgentAuditLog`` 实例 → 哈希链状态一致
2. 审计日志写入同一文件 → 便于查询与完整性校验
3. ``app.auth.middleware`` / ``app.api.v1.agent_gateway.inference`` 等
   调用方无需修改导入路径（向后兼容）

调用方优先使用 ``get_agent_audit_log()`` 工厂函数；模块级
``agent_audit_log`` 仍可导入使用（指向同一实例）。

注意：使用延迟导入避免 ``auth → agent`` 循环依赖；所有符号通过
``__getattr__`` 惰性解析。
"""

from __future__ import annotations

# ruff: noqa: F822 # re-export shim：__all__ 符号经 __getattr__ 惰性解析（见模块 docstring）

__all__ = [
    "AgentAuditEntry",
    "AgentAuditLog",
    "agent_audit_log",
    "get_agent_audit_log",
]


def __getattr__(name: str):
    if name in __all__:
        from app.agent.middleware import (
            AgentAuditEntry,
            AgentAuditLog,
            agent_audit_log,
            get_agent_audit_log,
        )

        _ns = {
            "AgentAuditEntry": AgentAuditEntry,
            "AgentAuditLog": AgentAuditLog,
            "agent_audit_log": agent_audit_log,
            "get_agent_audit_log": get_agent_audit_log,
        }
        return _ns[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
