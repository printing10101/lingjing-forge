"""Pure ASGI Unified Authentication Middleware (re-export shim).

This module was previously a 1112-line "God class" file mixing authentication,
audit logging, rate limiting, and idempotency concerns. It has been split into
five focused submodules:

  - ``app.auth.permissions``      : PermissionLevel enum + public path / scope constants
  - ``app.auth.audit``            : AgentAuditEntry + AgentAuditLog
  - ``app.auth.rate_limiter``     : AgentRateLimiter
  - ``app.auth.idempotency``      : IdempotencyStore
  - ``app.auth.middleware``       : UnifiedAuthMiddleware + LNN/JWT/agent helpers

This file remains only to preserve backward compatibility: every public symbol
previously importable from ``app.auth.unified_auth`` is re-exported here, so
existing ``from app.auth.unified_auth import X`` statements keep working
unchanged. New code should import directly from the relevant submodule.

All three token types are supported by the unified middleware:
  1. LNN flat token  (Bearer lj_... or any UUID-style token)
  2. JWT access token (Bearer eyJ...)
  3. Agent token     (Bearer lj_agent_...)
"""

from __future__ import annotations

# permissions.py — PermissionLevel + path / scope constants
from app.auth.permissions import (
    AGENT_ENDPOINT_PERMISSIONS,
    AUTH_PUBLIC_PATHS,
    AUTH_PUBLIC_PREFIXES,
    PERMISSION_HIERARCHY,
    PUBLIC_PATHS,
    PUBLIC_PREFIXES,
    PermissionLevel,
    WRITE_SCOPES,
    _check_scope,
    _get_permission_class,
    _is_public_path,
    _JWT_PUBLIC_PREFIXES,
    _PUBLIC_ENDPOINTS_LNN,
)

# audit.py — Agent audit log
from app.auth.audit import (
    AgentAuditEntry,
    AgentAuditLog,
    agent_audit_log,
)

# rate_limiter.py — Agent rate limiter
from app.auth.rate_limiter import (
    AgentRateLimiter,
    agent_rate_limiter,
)

# idempotency.py — Idempotency store
from app.auth.idempotency import (
    IdempotencyStore,
    idempotency_store,
)

# middleware.py — UnifiedAuthMiddleware + LNN/JWT/agent helpers
from app.auth.middleware import (
    UnifiedAuthMiddleware,
    _decode_token,
    _decode_token_strict,
    _generate_token,
    _get_agent_token_store,
    _get_client_ip,
    _get_token_ban_list,
    _get_token_file_path,
    _get_token_metadata,
    _initialize_token,
    _load_token,
    _log_access,
    _make_json_response,
    _save_token,
    _send_json_response,
)

__all__ = [
    # permissions
    "PermissionLevel",
    "PERMISSION_HIERARCHY",
    "PUBLIC_PATHS",
    "PUBLIC_PREFIXES",
    "AUTH_PUBLIC_PATHS",
    "AUTH_PUBLIC_PREFIXES",
    "_JWT_PUBLIC_PREFIXES",
    "_PUBLIC_ENDPOINTS_LNN",
    "AGENT_ENDPOINT_PERMISSIONS",
    "WRITE_SCOPES",
    "_is_public_path",
    "_get_permission_class",
    "_check_scope",
    # audit
    "AgentAuditEntry",
    "AgentAuditLog",
    "agent_audit_log",
    # rate limiter
    "AgentRateLimiter",
    "agent_rate_limiter",
    # idempotency
    "IdempotencyStore",
    "idempotency_store",
    # middleware
    "UnifiedAuthMiddleware",
    "_decode_token",
    "_decode_token_strict",
    "_generate_token",
    "_get_agent_token_store",
    "_get_client_ip",
    "_get_token_ban_list",
    "_get_token_file_path",
    "_get_token_metadata",
    "_initialize_token",
    "_load_token",
    "_log_access",
    "_make_json_response",
    "_save_token",
    "_send_json_response",
]
