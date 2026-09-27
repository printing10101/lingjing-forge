import hmac
import uuid
import secrets
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.models.user import UserCreate, UserLogin, UserResponse
from app.dependencies import get_user_store
from app.auth.dependencies import get_current_user
from app.dependencies import get_token_ban_list

from app.dependencies import get_config
from app.config import AppConfig
from typing import Any
from app.middleware.rate_limiter import limiter
from app.core.request_id import get_request_id as _get_request_id
from app.core.safe_errors import safe_error_message
from app.audit.audit_log import get_audit_log, OperationStatus

# token/密码工具为登录/刷新/鉴权路径的真实运行时依赖，不可移除。
from app.auth.security import (
    create_access_token,
    create_refresh_token,
    decode_token_strict,
    hash_password,
    verify_password,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _extract_request_meta(request: Request) -> dict[str, str]:
    """提取客户端请求元数据用于审计日志（已脱敏）。

    P0-17 修复：登录/登出审计日志需要记录客户端 IP 和 User-Agent 以满足
    FDA 21 CFR Part 11 §11.10(d) 访问控制事件追溯要求。本函数确保：
    1. IP 优先取 X-Forwarded-For 首段（反向代理场景），回退到 client.host
    2. User-Agent 截断至 200 字符防止日志膨胀
    3. 不记录密码、token 等敏感字段
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    user_agent = request.headers.get("User-Agent", "")[:200]
    return {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "request_id": _get_request_id(),
    }


def _audit_auth_event(
    event_type: str,
    operation_status: OperationStatus,
    username: str | None,
    request: Request,
    **extra_metadata: Any,
) -> None:
    """安全审计事件写入辅助函数（P0-17 修复）。

    包装 get_audit_log().log_security_event，统一处理异常：
    审计日志写入失败不应阻断业务流程（登录/登出仍需完成），
    但必须记录 error 级别日志以便运维感知审计链异常。

    Args:
        event_type: 事件类型（"auth_login"/"auth_logout"/"auth_refresh"/
            "auth_register"/"auth_login_failed" 等）
        operation_status: OperationStatus.SUCCESS / FAILED
        username: 目标用户名（登录失败时可能为请求体中的用户名）
        request: FastAPI Request 对象，用于提取 IP/UA
        **extra_metadata: 额外元数据（如失败原因 failure_reason）
    """
    try:
        meta = _extract_request_meta(request)
        meta.update(extra_metadata)
        get_audit_log().log_security_event(
            event_type=event_type,
            operation_status=operation_status,
            username=username,
            input_parameters={"username": username} if username else {},
            metadata=meta,
        )
    except Exception as audit_err:
        # 审计日志失败不阻断业务，但必须告警
        logger.error(
            "[AUDIT] Failed to write auth audit log (type=%s, user=%s): %s",
            event_type,
            username,
            audit_err,
            exc_info=True,
        )


# B13 安全修复：Pydantic 请求模型替换 body: dict 弱验证


class TokenRequest(BaseModel):
    """令牌请求模型。

    用于 refresh_token 和 logout 端点，替换原 body: dict 弱验证。
    两个字段均默认空字符串以兼容 logout 端点的可选语义；
    refresh_token 端点会在函数体内显式校验非空。
    """

    refresh_token: str = Field("", description="刷新令牌")
    access_token: str = Field("", description="访问令牌")


# 认证依赖 (已迁移到 app.auth.dependencies，此处为兼容导入)


def require_role(*roles: str):
    async def role_checker(current_user: dict = Depends(get_current_user)):
        if current_user["role"] not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return current_user

    return role_checker


@router.post("/register", response_model=dict)
@limiter.limit("3/hour")
async def register(request: Request, body: UserCreate, config: AppConfig = Depends(get_config)):
    """注册新用户。

    安全控制（按执行顺序）：
    1. 邀请码环境变量检查：当 ``LNN_REGISTRATION_CODE`` 为空时视为**开放注册**，
       任意用户可直接注册；设置非空值时进入邀请码模式。
    2. 邀请码验证（仅邀请码模式）：请求体中的 ``invite_code`` 必须与环境变量值
       完全匹配（使用 ``hmac.compare_digest`` 防止时序攻击）。
    3. 速率限制（slowapi）：同一 IP 在 1 小时内最多允许 3 次注册尝试；超限返回 429，
       响应头携带 ``Retry-After`` 字段，由 slowapi 中间件统一处理。
    4. 用户名唯一性检查：用户名已存在时返回 409。
    """
    # 1) 邀请码环境变量检查：未配置时视为开放注册，跳过邀请码校验
    # 修复 [B39]：通过 config.security.registration_code 统一读取，
    # 避免在业务代码中直接调用 os.environ.get() 绕过配置审计
    reg_code = config.security.registration_code

    # 2) 邀请码验证（仅在配置了邀请码时执行）
    if reg_code:
        invite_code = body.invite_code or ""
        if not invite_code or not hmac.compare_digest(invite_code, reg_code):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"code": 1003, "message": "无效的邀请码", "request_id": _get_request_id()},
            )

    # 3) 速率限制由 slowapi @limiter.limit("3/hour") 装饰器统一处理

    # 4) 用户名唯一性检查
    store = get_user_store()
    if store.get_user(body.username) is not None:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": 1009, "message": "用户名已存在", "request_id": _get_request_id()},
        )

    try:
        hashed = hash_password(body.password)
        record = store.create_user(body.username, hashed)
        logger.info("User registered: %s", body.username)
        # P0-17 修复：注册成功写入哈希链审计日志（FDA 21 CFR Part 11 §11.10(d)）
        _audit_auth_event(
            "auth_register",
            OperationStatus.SUCCESS,
            body.username,
            request,
        )
        # P1-17 修复：register 成功响应格式与 login/refresh/logout 对齐，
        # 使用 "code": 0 而非 "status": 200，避免前端需要为注册单独写解析逻辑。
        # 同时补充 request_id 字段，与该端点的错误响应结构对称。
        return {
            "code": 0,
            "message": "注册成功",
            "data": UserResponse(
                username=record.username,
                role=record.role,
                created_at=record.created_at,
                last_login=record.last_login,
            ).model_dump(),
            "request_id": _get_request_id(),
        }
    except ValueError as e:
        # 修复 [B27]：避免 str(e) 直接进入响应，泄露内部异常详情（如数据库错误、库版本等）
        # 使用 safe_error_message 包装，仅在 debug 模式下保留原始信息
        # P1-17 修复：修正 context 参数 copy-paste 错误（原为 "auth.refresh_token"）
        safe = safe_error_message(e, context="auth.register", fallback="注册服务异常，请稍后重试")
        logger.error("[auth.register] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        # P0-17 修复：注册失败也需写入审计日志
        _audit_auth_event(
            "auth_register",
            OperationStatus.FAILED,
            body.username,
            request,
            failure_reason="user_creation_error",
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": 1009,
                "message": safe["message"],
                "request_id": _get_request_id(),
                "error_id": safe["error_id"],
            },
        )


@router.post("/guest", response_model=dict)
@limiter.limit("10/minute")
async def guest_login(request: Request, config: AppConfig = Depends(get_config)):
    """访客模式登录：无需注册即可获得临时访问身份。

    安全控制：
    1. 开关检查：``LNN_GUEST_ENABLED`` 关闭时返回 403。
    2. 速率限制（slowapi）：同一 IP 每分钟最多 10 次访客登录。
    3. 临时身份：每次访客登录生成唯一 ``guest_<随机串>`` 标识，
       令牌仅含访问令牌（不签发 refresh token），有效期由
       ``LNN_GUEST_EXPIRE_HOURS`` 控制（默认 24 小时）。
    4. 审计：访客登录写入哈希链审计日志，便于追溯。
    """
    # 1) 访客模式开关检查
    if not config.security.guest_enabled:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"code": 1003, "message": "访客模式已关闭", "request_id": _get_request_id()},
        )

    # 2) 生成唯一访客身份（sub 即令牌身份标识，用于登出/审计追溯）
    guest_id = f"guest_{secrets.token_hex(4)}"
    jti = str(uuid.uuid4())
    expires = timedelta(hours=max(1, config.security.guest_expire_hours))
    access_token = create_access_token(
        {"sub": guest_id, "role": "guest", "is_guest": True, "jti": jti},
        expires_delta=expires,
    )

    logger.info("Guest session created: %s", guest_id)
    _audit_auth_event(
        "auth_guest_login",
        OperationStatus.SUCCESS,
        guest_id,
        request,
        role="guest",
    )
    return {
        "code": 0,
        "message": "访客登录成功",
        "data": {
            "access_token": access_token,
            "token_type": "bearer",
            "user": UserResponse(
                username=guest_id,
                role="guest",
                is_guest=True,
            ).model_dump(),
        },
    }


@router.post("/login", response_model=dict)
@limiter.limit("5/minute")
async def login(request: Request, body: UserLogin):
    store = get_user_store()
    user = store.get_user(body.username)

    if user is None or not user.is_active:
        # P0-17 修复：登录失败写入哈希链审计日志（SOC 2 CC6.1）
        _audit_auth_event(
            "auth_login",
            OperationStatus.FAILED,
            body.username,
            request,
            failure_reason="user_not_found_or_inactive",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    if not verify_password(body.password, user.password_hash):
        # P0-17 修复：密码错误写入审计日志
        _audit_auth_event(
            "auth_login",
            OperationStatus.FAILED,
            body.username,
            request,
            failure_reason="invalid_password",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    store.update_last_login(body.username)

    jti = str(uuid.uuid4())
    access_token = create_access_token({"sub": user.username, "role": user.role, "jti": jti})
    refresh_token = create_refresh_token({"sub": user.username, "jti": str(uuid.uuid4())})

    logger.info("User logged in: %s", body.username)
    # P0-17 修复：登录成功写入哈希链审计日志（FDA 21 CFR Part 11 §11.10(d)）
    _audit_auth_event(
        "auth_login",
        OperationStatus.SUCCESS,
        body.username,
        request,
        role=user.role,
    )
    return {
        "code": 0,
        "message": "登录成功",
        "data": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": UserResponse(
                username=user.username,
                role=user.role,
                created_at=user.created_at,
                last_login=user.last_login,
            ).model_dump(),
        },
    }


@router.post("/refresh", response_model=dict)
@limiter.limit("10/minute")
async def refresh_token(request: Request, body: TokenRequest):
    refresh_token_str = body.refresh_token
    if not refresh_token_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少refresh_token")

    ban_list = get_token_ban_list()
    if ban_list.is_banned(refresh_token_str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="RefreshToken已被撤销，请重新登录")

    payload = decode_token_strict(refresh_token_str, expected_type="refresh")
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="RefreshToken无效或已过期")

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token载荷无效")

    store = get_user_store()
    user = store.get_user(username)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")

    # P1-19 修复：refresh 时同步撤销旧 access token，避免旧 access token 在
    # 过期前继续有效形成"令牌重叠窗口"。客户端应在 refresh 请求体中携带
    # access_token 字段；未提供时仅撤销 refresh token（向后兼容）。
    old_access_token_str = body.access_token
    ban_list.ban(refresh_token_str)
    if old_access_token_str:
        try:
            ban_list.ban(old_access_token_str)
        except Exception as exc:
            # 撤销旧 access token 失败不应阻断 refresh 主流程（新令牌已签发），
            # 但需记录 warning 以便运维感知黑名单写入异常。
            logger.warning(
                "[auth.refresh] 撤销旧 access token 失败（user=%s）: %s",
                username,
                exc,
                exc_info=True,
            )
    new_jti = str(uuid.uuid4())
    new_access = create_access_token({"sub": username, "role": user.role, "jti": new_jti})
    new_refresh = create_refresh_token({"sub": username, "jti": str(uuid.uuid4())})

    logger.info("Token refreshed for user: %s", username)
    # P0-17 修复：令牌刷新成功写入审计日志
    _audit_auth_event(
        "auth_refresh",
        OperationStatus.SUCCESS,
        username,
        request,
    )
    return {
        "code": 0,
        "message": "Token刷新成功",
        "data": {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "bearer",
        },
    }


@router.post("/logout", response_model=dict)
@limiter.limit("20/minute")
async def logout(request: Request, body: TokenRequest):
    access_token_str = body.access_token
    refresh_token_str = body.refresh_token

    # 尝试从 access_token 解析用户名用于审计日志（登出前 token 可能已失效，
    # 解析失败时 username 为 None，不影响登出业务流程）
    logout_username: str | None = None
    if access_token_str:
        try:
            payload = decode_token_strict(access_token_str, expected_type="access")
            if payload is not None:
                logout_username = payload.get("sub")
        except Exception as exc:
            # P1-5 修复：token 已失效或无效时无法解析用户名，仅记录匿名登出。
            # 不得静默 pass——JWT 库异常可能暗示密钥配置错误或 token 格式篡改，
            # debug 级日志便于安全审计回溯，但不影响登出主流程。
            logger.debug("logout token 解析失败，匿名登出: %s", exc, exc_info=True)

    ban_list = get_token_ban_list()
    if access_token_str:
        ban_list.ban(access_token_str)
    if refresh_token_str:
        ban_list.ban(refresh_token_str)

    logger.info("User logged out")
    # P0-17 修复：登出写入哈希链审计日志（FDA 21 CFR Part 11 §11.10(d)）
    _audit_auth_event(
        "auth_logout",
        OperationStatus.SUCCESS,
        logout_username,
        request,
    )
    return {"code": 0, "message": "登出成功"}


@router.get("/me", response_model=dict)
async def get_me(current_user: dict = Depends(get_current_user)):
    username = current_user["username"]
    # 访客：临时身份不落用户存储，直接从 current_user 返回
    if current_user.get("is_guest"):
        return {
            "code": 0,
            "message": "OK",
            "data": UserResponse(
                username=username,
                role=current_user.get("role", "guest"),
                is_guest=True,
            ).model_dump(),
        }
    store = get_user_store()
    user = store.get_user(username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return {
        "code": 0,
        "message": "OK",
        "data": UserResponse(
            username=user.username,
            role=user.role,
            created_at=user.created_at,
            last_login=user.last_login,
        ).model_dump(),
    }
