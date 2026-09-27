"""身份域路由注册：认证 / 用户 / 用户主权.

注册端点：
- POST /api/v1/auth/login        - 登录
- POST /api/v1/auth/register     - 注册
- POST /api/v1/auth/refresh      - 刷新令牌
- GET  /api/v1/users/me          - 当前用户信息
- GET  /api/v1/users/            - 用户列表（管理员）
- 用户主权相关端点（数据可携带权 / 被遗忘权等）

设计约束：
- 登录端点受速率限制（防爆破）
- 用户主权操作需二次认证
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import auth, users, user_sovereignty


def register(app: FastAPI) -> None:
    """注册身份域路由."""
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(user_sovereignty.router)
