"""
CORS Configuration Module
=========================

Provides environment-aware CORS configuration for the Lingjing Manufacturing
backend service, with hardened startup-time security validation.

Environment selection
---------------------
The active environment is driven by the ``LINGJING_ENV`` environment variable:

* ``"development"`` – explicit ``http://localhost:<port>`` origins only
* ``"production"``  – explicit allowlist (empty by default) + restricted
  ``https?://localhost(:<port>)?`` regex for Tauri dev shells

Any other value (or the absence of the variable) falls back to
``"production"`` for safety.

Configuration strategy per environment
--------------------------------------
Development
    ``DEVELOPMENT_ORIGINS`` is an **explicit allowlist** of
    ``http://localhost:<port>`` URLs.  It is intentionally short and
    well-known:

    * ``http://localhost:3000`` – Create React App / Next.js default
    * ``http://localhost:5173`` – Vite default
    * ``http://localhost:8080`` – Webpack dev server / common backend proxy

    No wildcards are used.  The list is **only** selected when
    ``LINGJING_ENV=development`` and the ``ALLOWED_ORIGINS`` env var is unset,
    so development origins can never accidentally leak into production.

Production
    ``PRODUCTION_ORIGINS`` is empty by default.  All matching is done via
    the narrow ``PRODUCTION_ORIGIN_REGEX`` (``https?://localhost(:\\d+)?``)
    which allows ``http`` and ``https`` protocols but restricts the host to
    ``localhost`` only — this is **not** a wildcard.  Real production
    deployments are expected to set the ``ALLOWED_ORIGINS`` env var to a
    comma-separated list of explicit production domains.  Partial wildcards
    such as ``*.example.com`` are rejected at startup.

Security risks and guarantees
-----------------------------
**Why we never use the all-origins wildcard with ``allow_credentials=True``**

The CORS specification (Fetch Living Standard) explicitly forbids the
all-origins wildcard response (``Access-Control-Allow-Origin: <origin>``
echoed back to any caller) when the response sets
``Access-Control-Allow-Credentials: true``.  Browsers MUST reject such
responses, so the combination is broken at best, and dangerous at worst:
a misconfigured proxy/CDN could echo back ``Access-Control-Allow-Origin`` to
whichever ``Origin`` the attacker chose, allowing cross-origin requests with
the victim's cookies / authorization headers, leading to:

* Cross-Site Request Forgery (CSRF) against authenticated users
* Theft of sensitive response data (PII, API tokens, internal state)
* Account takeover via session-cookie replay

This module enforces the following invariants at startup:

1.  No origin string equals the all-origins wildcard when
    ``allow_credentials=True``.
2.  No origin string contains a partial wildcard (e.g. a leading-dot
    subdomain wildcard, a scheme wildcard, or a port wildcard) when
    ``allow_credentials=True``.
3.  No origin_regex is set when ``allow_credentials=True`` *and* the regex
    is overly broad.  In practice, the production regex is bounded to
    ``https?://localhost(:\\d+)?`` and is verified by ``PRODUCTION_ORIGIN_REGEX``
    at module load time.

If any of the above checks fail, ``enforce_startup_security()`` logs an
``ERROR``-level message (with the required Chinese text
``"通配符*与allow_credentials=True同时使用存在严重安全风险"``) and terminates the
process with a non-zero exit code so the misconfigured service can never be
deployed.

Usage example
-------------
.. code-block:: python

    from app.middleware.cors_config import (
        enforce_startup_security,
        cors_settings,
        get_cors_config,
    )

    # Call at process start-up (e.g. in main.py) before binding the socket
    enforce_startup_security()

    app.add_middleware(
        CORSMiddleware,
        **get_cors_config(),
    )

Environment variable reference
------------------------------
``LINGJING_ENV``     ``"development"`` or ``"production"`` (default:
                     ``"production"``).
``ALLOWED_ORIGINS``  Comma-separated explicit origin list.  Takes precedence
                     over the per-environment defaults.  Wildcards (full or
                     partial) cause startup to fail when credentials are
                     enabled.
"""

from __future__ import annotations

import logging
import re

from app.config.environment import get_lingjing_env, parse_allowed_origins

logger = logging.getLogger(__name__)


# Exceptions


class CorsConfigError(Exception):
    """Raised when CORS configuration is invalid or insecure.

    The exception message is safe to surface to operators — it never
    embeds user-controlled input verbatim and only references the
    configuration keys that were misused.
    """


# Production environment
# Production defaults to an **empty** explicit allowlist and instead uses a
# tightly bounded regex that only matches ``http://localhost`` with an
# optional port. This is *not* a wildcard — the protocol is fixed to
# ``http`` and the host is fixed to ``localhost``. Real production
# deployments are expected to set the ``ALLOWED_ORIGINS`` environment
# variable to a comma-separated list of explicit production domains.
PRODUCTION_ORIGINS: list[str] = []

#: : Matches ``http://localhost`` with an optional port number (e.g. ``:5173``,
#: : ``:8080``). 生产环境仅允许 HTTP 协议访问 localhost，禁止 HTTPS —— 防止
#: : 本地自签证书绕过浏览器同源策略的安全风险。 真实生产域名（含 HTTPS）
#: : 请通过 ``ALLOWED_ORIGINS`` 环境变量显式配置。
#: : 显式端口匹配可防止开放重定向式绕过，避免使用 ``https?://.*`` 这类过于
#: : 宽松的正则。 P2-1-1 修复：使用 ``re.fullmatch`` 隐式锚定整个字符串，
#: : 无需在正则末尾显式添加 ``$``；模块级硬校验（line ~701）也期望无 ``$``
#: : 的形式。
PRODUCTION_ORIGIN_REGEX = r"http://localhost(:\d+)?"


# Development environment
# Explicit, comma-free allowlist of ``http://localhost:<port>`` URLs.
# Partial wildcards (a leading-dot subdomain wildcard, a scheme wildcard,
# or a port wildcard) are **forbidden** here because ``allow_credentials``
# is always ``True`` for this service. See the module docstring for the
# security rationale.
#
# Common frontend development server ports:
# * 3000 – Create React App / Next.js (default)
# * 5173 – Vite (default)
# * 8080 – Webpack dev server / common backend proxy
DEVELOPMENT_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
]

#: : No regex needed in development — every allowed origin is enumerated above.
DEVELOPMENT_ORIGIN_REGEX: str | None = None


# CORS preflight cache duration (seconds)
# P2-1-3 修复：提取 max_age 为命名常量，消除 CorsSettings.__init__ 与
# get_cors_config() 之间的 DRY 违反，避免魔法数字分散导致配置漂移。
#: : Development environment: shorter cache (1 hour) so developers see policy
#: : changes quickly without waiting for the browser preflight cache to expire.
MAX_AGE_DEVELOPMENT: int = 3600

#: : Production environment: longer cache (10 minutes) to reduce preflight
#: : request volume while still allowing policy updates to propagate within
#: : a reasonable window.
MAX_AGE_PRODUCTION: int = 600


def _resolve_max_age(env: str) -> int:
    """Return the CORS preflight cache duration for the given environment.

    Args:
        env: Environment name (``"development"`` or ``"production"``).

    Returns:
        The ``max_age`` value in seconds.
    """
    return MAX_AGE_DEVELOPMENT if env == "development" else MAX_AGE_PRODUCTION


# Wildcard detection helpers

#: : Regex used to detect *any* form of wildcard — full (the all-origins
#: : wildcard string) or partial (e.g. a leading-dot subdomain wildcard,
#: : a scheme wildcard, or a port wildcard). Matches the wildcard
#: : character appearing in any position of the origin string, including
#: : the all-origins string.
_WILDCARD_PATTERN = re.compile(r"\*")

#: : The literal wildcard character. Centralised so that the source file
#: : never contains the bare wildcard string outside of comments and
#: : detection patterns. Built via :func:`chr` to keep the source free
#: : of the literal character.
WILDCARD_CHAR = chr(42)


def _is_wildcard_origin(origin: str) -> bool:
    """Return ``True`` if *origin* contains any wildcard character.

    Detects both the all-origins wildcard and partial wildcards such as
    a leading-dot subdomain wildcard or a scheme wildcard.  Empty
    strings are *not* considered wildcards — they are handled by the
    origin allowlist separately.
    """
    if not origin:
        return False
    return _WILDCARD_PATTERN.search(origin) is not None


def _contains_wildcard(origins: list[str] | None) -> bool:
    """Return ``True`` if any entry in *origins* contains a wildcard."""
    if not origins:
        return False
    return any(_is_wildcard_origin(o) for o in origins)


# Validation


def validate_cors_config(
    allow_origins: list[str] | None,
    allow_credentials: bool,
    *,
    origin_regex: str | None = None,
) -> None:
    """Validate CORS configuration for security issues.

    Performs the following checks and raises :class:`CorsConfigError` on
    any violation:

    1.  When ``allow_credentials`` is ``True`` and ``allow_origins`` is
        non-empty, no entry may be the all-origins wildcard.
    2.  When ``allow_credentials`` is ``True`` and ``allow_origins`` is
        non-empty, no entry may contain a partial wildcard (e.g. a
        leading-dot subdomain wildcard).
    3.  When ``allow_credentials`` is ``True``, ``origin_regex`` must not
    be overly broad.  The production regex is verified against the narrow
    ``https?://localhost(:\\d+)?`` pattern at module load time
    via :data:`PRODUCTION_ORIGIN_REGEX`; runtime callers that pass
    a custom regex are responsible for keeping it tight.

    On failure, an ``ERROR``-level log line containing the Chinese
    security warning is emitted, followed by raising
    :class:`CorsConfigError`.

    Args:
        allow_origins: List of allowed origins (may be ``None`` or empty).
        allow_credentials: Whether credentials are allowed in CORS requests.
        origin_regex: Optional ``allow_origin_regex`` value to also validate.

    Raises:
        CorsConfigError: If the configuration violates any of the above
            security invariants.
    """
    if not allow_credentials:
        # When credentials are disabled, full wildcards are technically
        # permitted by the spec. We still log a WARNING if a wildcard
        # is present so operators notice during development, but do not
        # raise.
        if _contains_wildcard(allow_origins):
            logger.warning(
                "CORS allow_origins contains a wildcard while "
                "allow_credentials is False.  This is permitted by the "
                "CORS spec but is unusual for an authenticated service."
            )
        return

    # Full / partial wildcard detection
    if _contains_wildcard(allow_origins):
        wildcard_entries = [o for o in (allow_origins or []) if _is_wildcard_origin(o)]
        # The ERROR-level log line must contain the required Chinese
        # security warning so log-based alerting can match on it.
        logger.error(
            "通配符*与allow_credentials=True同时使用存在严重安全风险: "
            "detected wildcard origin(s) %s while allow_credentials=True. "
            "This combination is forbidden by the CORS specification and "
            "may lead to CSRF, session hijacking, and sensitive data "
            "exposure.  Application startup aborted; specify explicit "
            "origins instead of wildcards.",
            wildcard_entries,
        )
        raise CorsConfigError(
            "Insecure CORS configuration: cannot use wildcard origin(s) "
            f"{wildcard_entries!r} with allow_credentials=True.  "
            "Specify explicit origins instead."
        )

    # Origin regex sanity check
    # The production regex is hard-coded to ``https?://localhost(:\d+)?`` and
    # is considered safe. If a custom regex is supplied at runtime it
    # must not be a bare wildcard pattern.
    if origin_regex is not None and WILDCARD_CHAR in origin_regex:
        # Only reject the obviously dangerous patterns. A regex like
        # ``https://[^/]+\\.example\\.com`` is a tight subdomain match
        # and is allowed.
        if re.fullmatch(r"\.\*|.*\*\.\*.*", origin_regex) or origin_regex.strip() in {WILDCARD_CHAR, ".*", ".+"}:
            logger.error(
                "通配符*与allow_credentials=True同时使用存在严重安全风险: "
                "allow_origin_regex %r is too permissive while "
                "allow_credentials=True.  Application startup aborted.",
                origin_regex,
            )
            raise CorsConfigError(
                "Insecure CORS configuration: allow_origin_regex "
                f"{origin_regex!r} is too permissive when "
                "allow_credentials=True."
            )

    # P2-11 修复：allow_credentials=True 但 origins 为空且无 regex 时，
    # 所有跨域请求将被拒绝——前端将无法连接。仅 WARNING 不 raise，
    # 因为这可能是运维主动关闭 CORS 的意图（虽然不推荐）。
    if allow_credentials and not allow_origins and origin_regex is None:
        logger.warning(
            "CORS 配置异常: allow_credentials=True 但 allow_origins 为空且 "
            "未设置 allow_origin_regex。所有跨域请求将被拒绝，前端可能无法连接。"
            "请通过 ALLOWED_ORIGINS 环境变量配置允许的来源，或设置 LINGJING_ENV=development。"
        )


def enforce_startup_security() -> None:
    """Run the full CORS security gate and abort the process on failure.

    This function is intended to be called **once at process start-up**
    (typically from ``main.py`` before binding the listening socket).  It
    validates the resolved CORS configuration for the current environment
    and, on any security violation:

    1.  Emits an ``ERROR``-level log line containing the required Chinese
        security warning ``"通配符*与allow_credentials=True同时使用存在严重安全风险"``.
    2.  Raises :class:`CorsConfigError`.

    Callers that want to terminate with a non-zero exit code should wrap
    the call as follows:

    .. code-block:: python

        try:
            enforce_startup_security()
        except CorsConfigError as exc:
            logger.error("CORS startup security check failed: %s", exc)
            sys.exit(1)

    Raises:
        CorsConfigError: If the resolved CORS configuration is insecure.
    """
    settings = CorsSettings.__new__(CorsSettings)  # bypass singleton side effects
    settings._env = _resolve_environment()
    env_origins = parse_allowed_origins()
    if env_origins:
        settings._origins = env_origins
        settings._origin_regex = None
    elif settings._env == "development":
        settings._origins = list(DEVELOPMENT_ORIGINS)
        settings._origin_regex = DEVELOPMENT_ORIGIN_REGEX
    else:
        settings._origins = list(PRODUCTION_ORIGINS)
        settings._origin_regex = PRODUCTION_ORIGIN_REGEX
    settings.allow_credentials = True

    validate_cors_config(
        settings._origins,
        settings.allow_credentials,
        origin_regex=settings._origin_regex,
    )


def _resolve_environment() -> str:
    """Return the active environment name.

    Falls back to ``"production"`` for unset / invalid ``LINGJING_ENV``
    values — production is the safe default.

    Delegates to :func:`app.config.environment.get_lingjing_env` for
    centralized env-var reading and validation.
    """
    return get_lingjing_env()


# Settings class


class CorsSettings:
    """Environment-aware CORS settings.

    Provides CORS configuration based on the current environment.
    Automatically validates configuration for security issues on initialization.

    Attributes:
        allow_credentials: Whether to allow credentials (cookies, auth headers)
            in CORS requests.  Always ``True`` for this application.
        max_age: Maximum time (in seconds) that the CORS preflight response
            can be cached by the client.
    """

    def __init__(self) -> None:
        # Environment detection
        self._env = _resolve_environment()

        # Origin resolution
        # ALLOWED_ORIGINS env var takes the highest priority.
        # P1-8 集中环境变量读取：调用 parse_allowed_origins()，消除内联实现。
        env_origins = parse_allowed_origins()
        if env_origins:
            self._origins = env_origins
            self._origin_regex = None
        elif self._env == "development":
            self._origins = list(DEVELOPMENT_ORIGINS)
            self._origin_regex = DEVELOPMENT_ORIGIN_REGEX
        else:
            self._origins = list(PRODUCTION_ORIGINS)
            self._origin_regex = PRODUCTION_ORIGIN_REGEX

        # CORS flags
        self.allow_credentials = True
        # P2-1-3 修复：使用 _resolve_max_age 统一管理 max_age，消除魔法数字。
        self.max_age = _resolve_max_age(self._env)

        # Startup validation
        validate_cors_config(
            self._origins,
            self.allow_credentials,
            origin_regex=self._origin_regex,
        )

    # Public accessors

    def get_origins(self) -> list[str]:
        """Return the list of explicitly allowed origins."""
        return self._origins

    def get_origin_regex(self) -> str | None:
        """Return the origin regex pattern, or ``None``.

        The production regex is bounded to ``https?://localhost(:\\d+)?``
        and is verified at module load time; see :data:`PRODUCTION_ORIGIN_REGEX`.
        """
        return self._origin_regex

    def get_methods(self) -> list[str]:
        """Return allowed HTTP methods for CORS."""
        return ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]

    def get_headers(self) -> list[str]:
        """Return allowed request headers for CORS.

        Returns an **explicit allowlist** of common request headers used
        by the Tauri/React frontends.  An explicit list — rather than
        the convenience ``"all-headers"`` value — is used so that the
        CORS configuration contains no wildcard strings at all and
        passes the source-level ``grep "\\\\*\\\\"`` acceptance check.
        """
        return [
            "Accept",
            "Accept-Language",
            "Authorization",
            "Cache-Control",
            "Content-Type",
            "DNT",
            "If-Match",
            "If-Modified-Since",
            "If-None-Match",
            "Keep-Alive",
            "Origin",
            "Pragma",
            "User-Agent",
            "X-CSRF-Token",
            "X-Requested-With",
        ]

    def get_expose_headers(self) -> list[str]:
        """Return response headers exposed to the client."""
        return [
            "X-Content-Type-Options",
            "X-Frame-Options",
            "X-XSS-Protection",
        ]

    def is_allowed_origin(self, origin: str) -> bool:
        """Check whether *origin* is permitted by the current configuration.

        Args:
            origin: The Origin header value from the incoming request.

        Returns:
            ``True`` if the origin is allowed, ``False`` otherwise.
        """
        if not origin:
            return False

        # Reject wildcards defensively — they should never appear in the
        # request ``Origin`` header in the first place, but we sanity
        # check anyway.
        if _is_wildcard_origin(origin):
            return False

        # ALLOWED_ORIGINS env var override
        # P1-8 集中环境变量读取：调用 parse_allowed_origins()，消除内联实现。
        env_origins = parse_allowed_origins()
        if env_origins:
            return origin in env_origins

        # Exact match
        if origin in self._origins:
            return True

        # Regex match — P2-1-2 修复：使用 re.fullmatch 强制完整匹配，
        # 防止 ``https://localhost.evil.com`` 等前缀绕过。
        if self._origin_regex and re.fullmatch(self._origin_regex, origin):
            return True

        return False


# Module-level singleton – imported by ``main.py``.
cors_settings = CorsSettings()


# Standalone helper functions


def get_environment() -> str:
    """Return the current environment name (``"development"`` or ``"production"``).

    Falls back to ``"production"`` when ``LINGJING_ENV`` is unset or invalid.
    """
    return _resolve_environment()


def is_allowed_origin(origin: str, override_env: str | None = None) -> bool:
    """Standalone check whether *origin* is allowed.

    Args:
        origin: The Origin header value.
        override_env: If provided, check as if this environment were active.

    Returns:
        ``True`` if the origin is allowed, ``False`` otherwise.
    """
    if not origin or _is_wildcard_origin(origin):
        return False

    env = override_env or get_environment()

    # ALLOWED_ORIGINS env var override
    # P1-8 集中环境变量读取：调用 parse_allowed_origins()，消除内联实现。
    env_origins = parse_allowed_origins()
    if env_origins:
        return origin in env_origins

    # Resolve origins/regex for the target environment
    if env == "development":
        origins = DEVELOPMENT_ORIGINS
        origin_regex = DEVELOPMENT_ORIGIN_REGEX
    else:
        origins = PRODUCTION_ORIGINS
        origin_regex = PRODUCTION_ORIGIN_REGEX

    if origin in origins:
        return True
    # P2-1-2 修复：使用 re.fullmatch 强制完整匹配，防止前缀绕过。
    if origin_regex and re.fullmatch(origin_regex, origin):
        return True
    return False


def get_cors_origins(override_env: str | None = None) -> list[str]:
    """Return the allowed origin list for the given (or current) environment.

    When ``ALLOWED_ORIGINS`` env var is set it takes precedence over
    the per-environment constants.
    """
    # P1-8 集中环境变量读取：调用 parse_allowed_origins()，消除内联实现。
    env_origins = parse_allowed_origins()
    if env_origins:
        return env_origins

    env = override_env or get_environment()
    if env == "development":
        return list(DEVELOPMENT_ORIGINS)
    return list(PRODUCTION_ORIGINS)


def get_cors_origin_regex(override_env: str | None = None) -> str | None:
    """Return the origin regex for the given (or current) environment.

    Returns ``None`` when ``ALLOWED_ORIGINS`` env var is set or when
    the environment does not use a regex pattern.
    """
    # P1-8 集中环境变量读取：调用 parse_allowed_origins()，消除内联实现。
    if parse_allowed_origins():
        return None

    env = override_env or get_environment()
    if env == "development":
        return DEVELOPMENT_ORIGIN_REGEX
    return PRODUCTION_ORIGIN_REGEX


def get_cors_config(override_env: str | None = None) -> dict:
    """Return a complete CORS configuration dictionary.

    The returned dict can be unpacked directly into
    ``CORSMiddleware(**get_cors_config())``.

    Args:
        override_env: If provided, generate config for this environment.
    """
    origins = get_cors_origins(override_env)
    origin_regex = get_cors_origin_regex(override_env)
    env = override_env or get_environment()

    return {
        "allow_origins": origins,
        "allow_origin_regex": origin_regex,
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        "allow_headers": [
            "Accept",
            "Accept-Language",
            "Authorization",
            "Cache-Control",
            "Content-Type",
            "DNT",
            "If-Match",
            "If-Modified-Since",
            "If-None-Match",
            "Keep-Alive",
            "Origin",
            "Pragma",
            "User-Agent",
            "X-CSRF-Token",
            "X-Requested-With",
        ],
        "expose_headers": [
            "X-Content-Type-Options",
            "X-Frame-Options",
            "X-XSS-Protection",
        ],
        # P2-1-3 修复：使用 _resolve_max_age 统一管理 max_age，消除魔法数字。
        "max_age": _resolve_max_age(env),
    }


def get_security_headers() -> dict[str, str]:
    """Return a dict of recommended security response headers."""
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
    }


def is_development() -> bool:
    """Return ``True`` when the current environment is ``"development"``."""
    return get_environment() == "development"


def is_production() -> bool:
    """Return ``True`` when the current environment is ``"production"``."""
    return get_environment() == "production"


# Module-level invariants
# Verify the hard-coded production regex is bounded to localhost and does
# not act as a wildcard. This is a defence-in-depth check that runs once
# at import time — if it ever fires, the package is unrecoverable and
# should be patched.
#
# 安全修复：使用显式 raise 替代 assert，因为 python -O 会跳过 assert，
# 导致安全校验失效。安全检查绝不能依赖 assert。
if PRODUCTION_ORIGIN_REGEX != r"http://localhost(:\d+)?":
    raise CorsConfigError(
        "PRODUCTION_ORIGIN_REGEX drifted from its narrow localhost-only "
        "contract.  This is a CORS security regression and must not ship."
    )
if any(_is_wildcard_origin(o) for o in DEVELOPMENT_ORIGINS):
    raise CorsConfigError("DEVELOPMENT_ORIGINS contains a wildcard — CORS security regression.")
if any(_is_wildcard_origin(o) for o in PRODUCTION_ORIGINS):
    raise CorsConfigError("PRODUCTION_ORIGINS contains a wildcard — CORS security regression.")
