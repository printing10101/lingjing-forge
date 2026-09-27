"""认证相关的 FastAPI 依赖注入工厂。

本模块将 ``get_current_user`` 和 ``security_scheme`` 从 ``api/v1/auth.py``
提升到 ``app/auth/`` 层，使域层模块（simulation/rag/cad 等)可以从
``app.auth.dependencies`` 导入认证依赖，而无需通过 API 层，从而消除
``simulation → api`` 循环依赖。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.security import HTTPBearer
from starlette import status
from starlette.exceptions import HTTPException

from app.auth.security import decode_token_strict, get_token_ban_list
from app.models.user import get_user_store

# 安全方案：HTTP Bearer Token 认证（单例，供 security_scheme 兼容导出）
security_scheme = HTTPBearer(auto_error=False)


def _permission_enforced() -> bool:
    """权限强制检查开关（与 config.security.permission_enforced 同源，实时读取）。"""
    import os

    val = os.environ.get("LNN_PERMISSION_ENFORCED", "true").strip().lower()
    return val not in ("0", "false", "no", "off")


def get_current_user(request: Request) -> dict[str, Any]:
    """FastAPI 依赖：从 Authorization Header 解析当前用户。

    权限强制检查关闭（LNN_PERMISSION_ENFORCED=false，测试/降级环境）时
    直接放行匿名用户，与 UnifiedAuthMiddleware / require_permission 语义一致；
    否则按完整 JWT 流程验证。
    """
    if not _permission_enforced():
        return {"username": "_anonymous_", "role": "T"}

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    token = auth_header[len("Bearer ") :]
    ban_list = get_token_ban_list()
    if ban_list.is_banned(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token已被撤销")

    payload = decode_token_strict(token, expected_type="access")
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或过期的Token")

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token载荷无效")

    # 访客 token：访客不落用户存储，直接从 JWT 载荷返回临时身份
    if payload.get("is_guest"):
        return {
            "username": username,
            "role": payload.get("role", "guest"),
            "is_guest": True,
        }

    store = get_user_store()
    user = store.get_user(username)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")

    return {"username": username, "role": user.role}
