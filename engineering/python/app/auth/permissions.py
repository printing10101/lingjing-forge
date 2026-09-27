"""
Capability-Based Permission Model + RBAC Permission Code Check

Implements R/W/B/N/C/T six-level permission classification:
- R (Read): LNN prediction queries, model lists, dataset info - default allow
- W (Workspace Write): Save predictions, create projects - default allow
- B (Batch/Training): LNN model training, batch inference - default allow
- N (Notification): Training completion notifications - default allow (rate limited)
- C (Credentials): System config, API key management - default deny, admin only
- T (Execute): Process parameter dispatch to machines - default deny, explicit auth required

Also provides RBAC permission-code-based decorators and dependency injection.

Reference: QuantDinger permission model design
"""

from __future__ import annotations
import os
import time
import logging
import threading
from enum import Enum
from functools import wraps

from collections.abc import Callable

from dataclasses import dataclass, field
from fastapi import HTTPException, status, Request
from typing import Any
import builtins

logger = logging.getLogger(__name__)


class PermissionLevel(str, Enum):
    R = "R"
    W = "W"
    B = "B"
    N = "N"
    C = "C"
    T = "T"


PERMISSION_HIERARCHY = {
    PermissionLevel.R: 0,
    PermissionLevel.W: 1,
    PermissionLevel.B: 2,
    PermissionLevel.N: 3,
    PermissionLevel.C: 4,
    PermissionLevel.T: 5,
}


@dataclass
class RateLimitConfig:
    max_requests: int = 100
    window_seconds: int = 60


@dataclass
class RateLimitState:
    requests: list = field(default_factory=list)

    def is_allowed(self, config: RateLimitConfig) -> bool:
        now = time.time()
        cutoff = now - config.window_seconds
        self.requests = [t for t in self.requests if t > cutoff]
        return len(self.requests) < config.max_requests

    def record(self):
        self.requests.append(time.time())


class PermissionChecker:
    ENDPOINT_PERMISSIONS: dict[str, PermissionLevel] = {
        "GET /api/v1/lnn/predict": PermissionLevel.R,
        "GET /api/v1/lnn/models": PermissionLevel.R,
        "GET /api/v1/lnn/tasks": PermissionLevel.R,
        "GET /api/v1/lnn/tasks/{task_id}": PermissionLevel.R,
        "GET /api/v1/wear/predict": PermissionLevel.R,
        "POST /api/v1/wear/predict": PermissionLevel.R,
        "GET /api/v1/datasets": PermissionLevel.R,
        "GET /api/v1/datasets/{dataset_id}": PermissionLevel.R,
        "GET /api/v1/datasets/{dataset_id}/info": PermissionLevel.R,
        "POST /api/v1/lnn/predict": PermissionLevel.W,
        "POST /api/v1/lnn/save_prediction": PermissionLevel.W,
        "POST /api/v1/projects": PermissionLevel.W,
        "PUT /api/v1/projects/{project_id}": PermissionLevel.W,
        "POST /api/v1/lnn/train": PermissionLevel.B,
        "POST /api/v1/lnn/batch_predict": PermissionLevel.B,
        "POST /api/v1/wear/train": PermissionLevel.B,
        "POST /api/v1/notifications": PermissionLevel.N,
        "GET /api/v1/notifications": PermissionLevel.N,
        "GET /api/v1/config": PermissionLevel.C,
        "PUT /api/v1/config": PermissionLevel.C,
        "POST /api/v1/api-keys": PermissionLevel.C,
        "DELETE /api/v1/api-keys/{key_id}": PermissionLevel.C,
        "POST /api/v1/machine/params": PermissionLevel.T,
        "POST /api/v1/machine/execute": PermissionLevel.T,
        "PUT /api/v1/machine/{machine_id}/params": PermissionLevel.T,
    }

    DEFAULT_PERMISSIONS = {
        "GET": PermissionLevel.R,
        "POST": PermissionLevel.W,
        "PUT": PermissionLevel.W,
        "DELETE": PermissionLevel.C,
        "PATCH": PermissionLevel.W,
    }

    def __init__(self):
        self._rate_limiter: dict[str, RateLimitState] = {}
        self._rate_limit_config = RateLimitConfig()

    def has_permission(self, token_level: PermissionLevel, endpoint: str, method: str) -> bool:
        key = f"{method} {endpoint}"
        required_level = self.ENDPOINT_PERMISSIONS.get(key)

        if required_level is None:
            required_level = self.DEFAULT_PERMISSIONS.get(method, None)

        if required_level is None:
            # 未知方法/路径：默认拒绝（安全收紧，大小写敏感）
            return False

        token_level_value = PERMISSION_HIERARCHY.get(token_level, 0)
        required_level_value = PERMISSION_HIERARCHY.get(required_level, 0)

        return token_level_value >= required_level_value

    def check_rate_limit(self, token_id: str) -> bool:
        if token_id not in self._rate_limiter:
            self._rate_limiter[token_id] = RateLimitState()

        state = self._rate_limiter[token_id]

        if not state.is_allowed(self._rate_limit_config):
            logger.warning("Rate limit exceeded for token %s", token_id)
            return False

        state.record()
        return True

    def get_required_permission(self, method: str, path: str) -> PermissionLevel:
        key = f"{method} {path}"
        return self.ENDPOINT_PERMISSIONS.get(key, self.DEFAULT_PERMISSIONS.get(method, PermissionLevel.R))


permission_checker = PermissionChecker()


class RBACPermissionCache:
    _instance: RBACPermissionCache | None = None
    _instance_lock = threading.Lock()
    _cache_lock: threading.Lock = threading.Lock()
    _cache: dict[str, tuple[builtins.set[str], float]] = {}
    _ttl: float = 60.0

    def __new__(cls):
        # 安全修复：双重检查锁，防止并发创建多个实例
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._cache_lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    def get(self, role_code: str) -> builtins.set[str] | None:
        # 安全修复：缓存读取加锁，防止与 set/invalidate 并发修改导致字典状态不一致
        with self._cache_lock:
            entry = self._cache.get(role_code)
            if entry is None:
                return None
            perms, expiry = entry
            if time.time() > expiry:
                self._cache.pop(role_code, None)
                return None
            return perms

    def set(self, role_code: str, permissions: builtins.set[str]):
        with self._cache_lock:
            self._cache[role_code] = (permissions, time.time() + self._ttl)

    def invalidate(self, role_code: str | None = None):
        with self._cache_lock:
            if role_code:
                self._cache.pop(role_code, None)
            else:
                self._cache.clear()


rbac_cache = RBACPermissionCache()


async def _get_role_permissions_from_db(role_code: str) -> set[str]:
    from app.database.connection import get_sessionmaker
    from sqlalchemy import select
    from app.database.models import Role, Permission, RolePermission

    cached = rbac_cache.get(role_code)
    if cached is not None:
        return cached

    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        logger.warning("Database not configured, using default empty permissions for role: %s", role_code)
        return set()

    async with sessionmaker() as session:
        stmt = select(Role).where(Role.code == role_code)
        result = await session.execute(stmt)
        role = result.scalar_one_or_none()
        if role is None:
            return set()

        perm_stmt = (
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role.id)
        )
        result = await session.execute(perm_stmt)
        perms = {row[0] for row in result.fetchall()}

    rbac_cache.set(role_code, perms)
    return perms


# 自助注册用户与访客的默认权限策略（2026-08-23 注册/访客功能落地）：
# - 自助注册用户默认角色为 "user"，而 DB 预设角色仅含 admin/engineer/operator，
# 若不加处理将导致注册用户权限为空、所有 require_permission 端点返回 403。
# - 访客（guest）为临时身份，不落用户存储（username 形如 guest_<hex>）。
#
# 权限策略（2026-09 安全评审 P1 修订）：
# - 角色 "user"（本地桌面自助注册，主使用场景）→ 授予全部功能权限码；
# - 角色 "guest" / guest_ 前缀临时身份 → 仅授予**只读**权限码
#   （动作 ∈ {read, view}）。背景：/auth/guest 位于公开路径白名单，
#   若授予全量权限码，任何能触达服务的调用方（远程 MCP/SSE 模式下含外部代理）
#   可自助获得 dataset:write、backup:export 等写权限，RBAC 形同虚设。
# 敏感能力（T 级机床执行、系统配置等）仍由 PaperOnlyGuard
# （LNN_LIVE_EXECUTION_ENABLED）与 require_role("admin") 单独把关。
_SELF_SERVICE_ROLES = frozenset({"user", "guest"})

# 访客允许的权限动作白名单（只读）
_GUEST_ALLOWED_ACTIONS = frozenset({"read", "view"})


def _default_full_permissions() -> set[str]:
    """返回全部功能权限码集合（与 PRESET_PERMISSIONS 保持同步）。"""
    from app.database.models._presets import PRESET_PERMISSIONS

    return {p["code"] for p in PRESET_PERMISSIONS}


def _guest_read_permissions() -> set[str]:
    """返回访客只读权限码集合（动作 ∈ {read, view}，与 PRESET_PERMISSIONS 保持同步）。"""
    from app.database.models._presets import PRESET_PERMISSIONS

    return {p["code"] for p in PRESET_PERMISSIONS if p["code"].rsplit(":", 1)[-1] in _GUEST_ALLOWED_ACTIONS}


async def get_user_permissions(username: str) -> set[str]:
    from app.dependencies import get_user_store

    store = get_user_store()
    user = store.get_user(username)
    if user is None:
        # 访客：临时身份不落用户存储（guest_ 前缀），仅授予只读权限（安全评审 P1）
        if username.startswith("guest_"):
            return _guest_read_permissions()
        return set()

    if user.role in _SELF_SERVICE_ROLES:
        # 自助注册用户（默认角色 user）→ 全部功能权限；
        # 显式 guest 角色 → 只读权限
        if user.role == "guest":
            return _guest_read_permissions()
        return _default_full_permissions()

    return await _get_role_permissions_from_db(user.role)


async def check_user_has_permission(username: str, required: str) -> bool:
    perms = await get_user_permissions(username)
    return required in perms


async def check_user_has_any_permission(username: str, required: list[str]) -> bool:
    perms = await get_user_permissions(username)
    return any(p in perms for p in required)


async def check_user_has_all_permissions(username: str, required: list[str]) -> bool:
    perms = await get_user_permissions(username)
    return all(p in perms for p in required)


def require_permission(permission: str):
    """
    FastAPI dependency: check single permission.
    Usage: @router.get("/path", dependencies=[Depends(require_permission("project:create"))])
    """

    async def checker(request: Request):
        # 权限强制检查关闭时放行（与 UnifiedAuthMiddleware 语义一致）：
        import os as _os

        if _os.environ.get("LNN_PERMISSION_ENFORCED", "true").strip().lower() in ("0", "false", "no", "off"):
            return
        if not hasattr(request.state, "username") or not request.state.username:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        username = request.state.username
        has_perm = await check_user_has_permission(username, permission)
        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permission: {permission}",
            )

    return checker


def require_any_permission(*permissions: str):
    """
    FastAPI dependency: check if user has at least one of the given permissions (OR logic).
    """

    async def checker(request: Request):
        # 权限强制检查关闭时放行（与 UnifiedAuthMiddleware 语义一致）：
        import os as _os

        if _os.environ.get("LNN_PERMISSION_ENFORCED", "true").strip().lower() in ("0", "false", "no", "off"):
            return
        if not hasattr(request.state, "username") or not request.state.username:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        username = request.state.username
        has_perm = await check_user_has_any_permission(username, list(permissions))
        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permission: need any of {permissions}",
            )

    return checker


def require_all_permissions(*permissions: str):
    """
    FastAPI dependency: check if user has ALL given permissions (AND logic).
    """

    async def checker(request: Request):
        # 权限强制检查关闭时放行（与 UnifiedAuthMiddleware 语义一致）：
        import os as _os

        if _os.environ.get("LNN_PERMISSION_ENFORCED", "true").strip().lower() in ("0", "false", "no", "off"):
            return
        if not hasattr(request.state, "username") or not request.state.username:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        username = request.state.username
        has_perm = await check_user_has_all_permissions(username, list(permissions))
        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permission: need all of {permissions}",
            )

    return checker


def permission_required(permission: str):
    """
    Decorator for route functions: check single permission.
    Usage:
        @router.get("/path")
        @permission_required("project:create")
        async def my_route(...):
            ...
    """

    def decorator(func: Callable):
        setattr(func, "_required_permission", permission)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = kwargs.get("request")
            if request is None:
                for arg in args:
                    if hasattr(arg, "state") and hasattr(arg.state, "username"):
                        request = arg
                        break

            if request is not None and hasattr(request.state, "username") and request.state.username:
                username = request.state.username
                has_perm = await check_user_has_permission(username, permission)
                if not has_perm:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Insufficient permission: {permission}",
                    )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_role(*roles: str):
    """
    FastAPI dependency: check user role.
    Usage: @router.get("/path", dependencies=[Depends(require_role("admin"))])
    """

    async def role_checker(request: Request):
        # 权限强制检查关闭时放行（与 UnifiedAuthMiddleware 语义一致）：
        import os as _os

        if _os.environ.get("LNN_PERMISSION_ENFORCED", "true").strip().lower() in ("0", "false", "no", "off"):
            return
        if not hasattr(request.state, "username") or not request.state.username:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        role = request.state.user_role
        if role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient role: need {roles}",
            )

    return role_checker


# [F-P0-4] 防复发：T 级操作敏感字段脱敏白名单
# NC 程序内容、API Key、密码等不得写入审计日志或普通日志
_SENSITIVE_FIELDS: tuple[str, ...] = (
    "api_key",
    "password",
    "token",
    "secret",
    "credential",
    "nc_program",
    "g_code",
    "nc_code",  # NC 程序可能含商业机密
    "signature",
    "private_key",
)

# [F-P0-4] 防复发：T 级操作机床安全前置状态字段
# 依据 ISO 10218 工业机器人安全标准 + 用户三方评估 F-P0-4
# 实模式执行前必须校验所有物理安全信号
REQUIRED_MACHINE_SAFETY_FIELDS: tuple[str, ...] = (
    "emergency_stop_active",  # 急停是否触发（True=危险，必须阻止）
    "guard_door_closed",  # 防护门是否关闭
    "light_curtain_clear",  # 光幕是否畅通
    "operator_present",  # 操作员是否在场
)


class PaperOnlyGuard:
    """T 级操作（机床执行）守卫，确保 Paper-Only 模式安全。

    设计依据：
    - ISO 10218 工业机器人安全标准
    - FDA 21 CFR Part 11 电子记录/电子签名
    - 用户三方评估 F-P0-4：实模式必须双因子确认 + 物理急停硬联锁

    防复发机制：
    1. 配置热刷新：每次从环境变量读取，避免启动时固化导致配置无法热切换
    2. 双因子确认：操作员权限 + 班长确认（supervisor_confirmed）
    3. 机床状态前置校验：检查急停/防护门/光幕/操作员在场
    4. 审计日志：T 级操作必须留痕，敏感字段脱敏
    """

    def __init__(self):
        # 兼容旧代码：保留实例字段，但 is_live_execution_allowed 改为实时读取
        # 避免启动时固化配置导致无法热切换
        self._live_execution_cached: bool | None = None

    @staticmethod
    def _read_live_execution_enabled() -> bool:
        """实时读取环境变量，支持热刷新（不再启动时固化）。"""
        return os.environ.get("LNN_LIVE_EXECUTION_ENABLED", "false").lower() == "true"

    def is_live_execution_allowed(self) -> bool:
        """是否允许实模式执行（实时读取环境变量）。"""
        return self._read_live_execution_enabled()

    def check_t_operation(
        self,
        has_t_permission: bool,
        ui_confirmed: bool,
        supervisor_confirmed: bool = False,
        machine_safety_status: dict[str, bool] | None = None,
    ) -> tuple[bool, str]:
        """T 级操作前置校验。

        Args:
            has_t_permission: 操作员是否具备 T 级权限
            ui_confirmed: 操作员 UI 确认
            supervisor_confirmed: 班长双因子确认（F-P0-4 新增，默认 False
                以强制调用方显式传入，避免遗漏）
            machine_safety_status: 机床安全状态字典，包含：
                - emergency_stop_active: 急停是否触发（True=危险，禁止执行）
                - guard_door_closed: 防护门是否关闭
                - light_curtain_clear: 光幕是否畅通
                - operator_present: 操作员是否在场

        Returns:
            (是否允许执行, 原因说明)
        """
        # 1. Paper-Only 模式快速拒绝
        if not self.is_live_execution_allowed():
            return False, "Paper-Only mode: T operations are simulated"

        # 2. 操作员权限校验
        if not has_t_permission:
            return False, "Insufficient permission: T-level required"

        # 3. 操作员 UI 确认
        if not ui_confirmed:
            return False, "UI confirmation required for T operations"

        # 4. 双因子确认（班长）—— F-P0-4 核心修复
        if not supervisor_confirmed:
            return False, "Supervisor dual-factor confirmation required for T operations"

        # 5. 机床安全状态前置校验 —— F-P0-4 物理联锁
        if machine_safety_status is not None:
            if machine_safety_status.get("emergency_stop_active", True):
                return False, "Machine emergency stop is active; T operation blocked"
            if not machine_safety_status.get("guard_door_closed", False):
                return False, "Guard door is open; T operation blocked"
            if not machine_safety_status.get("light_curtain_clear", True):
                return False, "Light curtain is blocked; T operation blocked"
            if not machine_safety_status.get("operator_present", False):
                return False, "Operator not present; T operation blocked"

        return True, "T operation approved"

    def simulate_t_operation(self, operation: dict[str, Any], operator: str = "unknown") -> dict[str, Any]:
        """模拟 T 级操作，记录审计日志（脱敏）。

        Args:
            operation: 操作字典
            operator: 操作员标识

        Returns:
            模拟结果字典
        """
        # 1. 脱敏：移除敏感字段后记录
        sanitized = self._sanitize_operation(operation)
        logger.info(
            "SIMULATED T operation (Paper-Only mode) by %s: %s",
            operator,
            sanitized,
        )

        # 2. 写入审计日志（延迟导入避免循环依赖）
        # 即使是模拟操作也必须留痕，满足 FDA 21 CFR Part 11 合规要求
        try:
            from app.audit.audit_log import (
                AuditLog,
                AIModule,
                UserDecision,
                OperationStatus,
            )
            from app.utils.utils import get_output_dir

            audit = AuditLog(log_dir=str(get_output_dir("logs") / "audit"))
            audit.log_decision(
                ai_module=AIModule.PROCESS_OPTIMIZE,
                ai_recommendation=sanitized,
                user_decision=UserDecision.AUTO_EXECUTED,
                final_execution={"executed": False, "mode": "paper-only"},
                operation_status=OperationStatus.PENDING,
                user_id=operator,
                metadata={"operation_type": "t_operation_simulated"},
            )
        except ImportError:
            logger.debug("Audit log module not available; skipping audit record for simulated T operation")
        except Exception as exc:
            # 审计日志写入失败不应阻断模拟流程，但必须告警
            logger.warning("Failed to write audit log for simulated T operation: %s", exc)

        return {
            "status": "simulated",
            "message": "Operation recorded but not executed (Paper-Only mode)",
            "operation": sanitized,
        }

    @staticmethod
    def _sanitize_operation(operation: dict[str, Any]) -> dict[str, Any]:
        """脱敏操作字典，移除敏感字段值。

        依据 F-P0-4 安全要求：NC 程序内容、API Key、密码等
        不得明文写入审计日志或普通日志。
        """
        sanitized: dict[str, Any] = {}
        for key, value in operation.items():
            key_lower = str(key).lower()
            if any(sensitive in key_lower for sensitive in _SENSITIVE_FIELDS):
                sanitized[key] = "***REDACTED***"
            else:
                sanitized[key] = value
        return sanitized


paper_only_guard = PaperOnlyGuard()


# Unified auth path definitions (moved from unified_auth.py)
# Public path definitions (merged from all three middlewares)

PUBLIC_PATHS: set[str] = {
    "/api/health",
    "/api/health/ping",
    "/api/metrics",
    "/api/v1/auth/register",
    "/api/v1/auth/guest",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/me",
    "/health",
    "/api/openapi.json",
    "/api/docs",
    "/api/redoc",
}

PUBLIC_PREFIXES: list[str] = [
    "/api/docs",
    "/api/redoc",
    "/api/openapi",
    "/api/openapi.json",
]


def _is_public_path(path: str) -> bool:
    """Check if the path is a public (non-authenticated) path."""
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


# LNN Token Auth public endpoints (from AuthMiddleware)

_PUBLIC_ENDPOINTS_LNN = {
    "/api/health",
    "/api/health/ping",
    "/api/metrics",
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/me",
    "/health",
}

_JWT_PUBLIC_PREFIXES = [
    "/api/docs",
    "/api/redoc",
    "/api/openapi",
]


# JWT Auth public paths (from JwtAuthMiddleware)

AUTH_PUBLIC_PATHS = {
    "/api/v1/auth/register",
    "/api/v1/auth/guest",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/health",
    "/api/health/ping",
    "/api/metrics",
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
    "/health",
}

AUTH_PUBLIC_PREFIXES = [
    "/api/docs",
    "/api/redoc",
    "/api/openapi",
]


# Agent Auth permission mapping (from AgentAuthMiddleware)

AGENT_ENDPOINT_PERMISSIONS: dict[str, PermissionLevel] = {
    "GET /api/agent/v1/health": PermissionLevel.R,
    "GET /api/agent/v1/models": PermissionLevel.R,
    "GET /api/agent/v1/models/{name}/info": PermissionLevel.R,
    "POST /api/agent/v1/predict": PermissionLevel.R,
    "POST /api/agent/v1/train": PermissionLevel.B,
    "GET /api/agent/v1/train/{job_id}": PermissionLevel.R,
    "GET /api/agent/v1/train/{job_id}/stream": PermissionLevel.R,
    "POST /api/agent/v1/execute": PermissionLevel.T,
    "GET /api/agent/v1/audit-log": PermissionLevel.C,
}

WRITE_SCOPES = {"W", "B", "T"}


def _get_permission_class(method: str, path: str) -> PermissionLevel:
    """Determine the permission class for a given endpoint."""
    key = f"{method} {path}"
    if key in AGENT_ENDPOINT_PERMISSIONS:
        return AGENT_ENDPOINT_PERMISSIONS[key]
    defaults = {
        "GET": PermissionLevel.R,
        "POST": PermissionLevel.W,
        "PUT": PermissionLevel.W,
        "DELETE": PermissionLevel.C,
    }
    return defaults.get(method, PermissionLevel.R)


def _check_scope(token_scopes: list[str], required: PermissionLevel) -> bool:
    """Check if token has the required scope."""
    if required.value in token_scopes:
        return True
    hierarchy = PERMISSION_HIERARCHY
    # token_scopes 为字符串（如 "R"/"W"），转枚举后查询层级（str Enum 可哈希匹配）
    token_max = max(
        (hierarchy.get(PermissionLevel(s), 0) for s in token_scopes if s in PermissionLevel._value2member_map_),
        default=0,
    )
    required_value = hierarchy.get(required, 0)
    return token_max >= required_value
