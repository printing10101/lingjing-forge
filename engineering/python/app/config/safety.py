"""安全和认证配置（CORS、限流、JWT、权限检查）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _int_env, logger
from app.config.environment import get_lingjing_env, parse_allowed_origins


def _resolve_cors_origins() -> list[str]:
    """统一解析 CORS 允许的来源列表。

    修复 [B30]：原本 SecurityConfig.cors_origins 仅读取 CORS_ORIGINS 环境变量，
    而 main.py 实际使用 cors_config.py 中的 cors_settings（读取 ALLOWED_ORIGINS）。
    这导致两个系统读取不同的环境变量，配置不一致。

    本函数统一读取顺序为：
    1. ALLOWED_ORIGINS（与 cors_config.py 一致，逗号分隔，优先级最高）
    2. CORS_ORIGINS（向后兼容字段，逗号分隔）

    这样 config.security.cors_origins 与 cors_settings.get_origins()
    将基于相同的环境变量来源，保持单一配置源。

    P1-8 集中环境变量读取：ALLOWED_ORIGINS 分支调用 parse_allowed_origins()，
    消除重复的内联实现；CORS_ORIGINS 为本字段独有的向后兼容回退，保留内联。
    """
    # 优先级 1：ALLOWED_ORIGINS（与 cors_config.py 保持一致）
    allowed = parse_allowed_origins()
    if allowed:
        return allowed
    # 优先级 2：CORS_ORIGINS（向后兼容字段）
    legacy = _env("CORS_ORIGINS", "")
    if legacy:
        return [o.strip() for o in legacy.split(",") if o.strip()]
    return []


@dataclass
class SecurityConfig:
    # 安全修复：CORS 默认改为空列表，强制部署时显式配置允许的来源。
    # 通配符 "*" 配合 allow_credentials=True 会导致 CSRF 型凭证泄露。
    # 修复 [B30]：与 cors_config.py 统一读取 ALLOWED_ORIGINS（优先）和 CORS_ORIGINS（回退），
    # 保证 config.security.cors_origins 与 cors_settings.get_origins() 数据源一致。
    cors_origins: list[str] = field(default_factory=_resolve_cors_origins)
    allow_credentials: bool = field(default_factory=lambda: _bool_env("CORS_ALLOW_CREDENTIALS", True))
    cors_origin_regex: str | None = field(default_factory=lambda: _env("CORS_ORIGIN_REGEX", "") or None)
    # 修复 [B30]：LINGJING_ENV 用于环境感知的 CORS 默认配置，
    # 与 cors_config.py 中的 _resolve_environment() 保持一致。
    # P1-8 集中环境变量读取：调用 get_lingjing_env()，消除内联实现。
    lingjing_env: str = field(default_factory=get_lingjing_env)
    rate_limit_enabled: bool = field(default_factory=lambda: _bool_env("RATE_LIMIT_ENABLED", True))
    rate_limit_requests: int = field(default_factory=lambda: _int_env("RATE_LIMIT_REQUESTS", 100))
    rate_limit_window: int = field(default_factory=lambda: _int_env("RATE_LIMIT_WINDOW", 60))
    auth_enabled: bool = field(default_factory=lambda: _bool_env("LNN_AUTH_ENABLED", True))
    # 修复：默认开启权限检查，避免在配置缺失时出现安全盲区
    permission_enforced: bool = field(default_factory=lambda: _bool_env("LNN_PERMISSION_ENFORCED", True))
    agent_auth_enabled: bool = field(default_factory=lambda: _bool_env("AGENT_AUTH_ENABLED", True))
    # JWT 认证开关，统一通过 config 管理，避免在 main.py 中直接读取环境变量
    jwt_auth_enabled: bool = field(default_factory=lambda: _bool_env("LNN_JWT_AUTH_ENABLED", True))
    # 修复 [B32]：JWT 密钥统一在 config 中声明，便于配置审计和文档化。
    # 注意：实际的密钥验证逻辑仍由 app/auth/security.py 的
    # _validate_and_get_secret() 负责（包含长度、随机性等安全检查），
    # 此字段仅作为配置项暴露，避免在多处直接读取环境变量。
    jwt_secret: str = field(default_factory=lambda: _env("LNN_JWT_SECRET", ""))
    # 修复 [B39]：注册邀请码统一在 config 中声明，避免在 auth.py 中
    # 直接读取 os.environ.get("LNN_REGISTRATION_CODE") 绕过配置审计。
    # 语义：当该字段为空字符串时视为开放注册（任意用户可自助注册）；
    # 设置非空值时注册必须携带匹配的邀请码（invite_code）。
    registration_code: str = field(default_factory=lambda: _env("LNN_REGISTRATION_CODE", ""))
    # 访客模式开关：开启后允许通过 /api/v1/auth/guest 获得临时访客身份，
    # 访客可访问全部非管理员功能（与普通注册用户一致）。
    guest_enabled: bool = field(default_factory=lambda: _bool_env("LNN_GUEST_ENABLED", True))
    # 访客访问令牌有效期（小时）：访客不签发 refresh token，会话较短，
    # 到期后需重新进入访客模式；登出或超时后 token 即失效。
    guest_expire_hours: int = field(default_factory=lambda: _int_env("LNN_GUEST_EXPIRE_HOURS", 24))

    def __post_init__(self) -> None:
        """启动时安全审计：检测到权限检查被显式关闭时输出 WARNING。"""
        # 测试环境（conftest 中设置 ENVIRONMENT=testing）下静默，避免日志噪音
        if _env("ENVIRONMENT", "development").lower() == "testing":
            return
        if not self.permission_enforced:
            logger.warning(
                "权限检查功能已被禁用，这可能导致安全风险 (LNN_PERMISSION_ENFORCED=%s)",
                os.environ.get("LNN_PERMISSION_ENFORCED", "false"),
            )
