from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.models.user import UserResponse
from app.dependencies import get_user_store
from app.models.schemas import (
    PermissionCheckResult,
    RoleAssignRequest,
    UserListItem,
    UserListResponse,
    UserStatusRequest,
)
from app.auth.permissions import (
    require_permission,
    get_user_permissions,
    rbac_cache,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _get_username(request: Request) -> str:
    if not hasattr(request.state, "username") or not request.state.username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return request.state.username


@router.get("", response_model=dict, dependencies=[Depends(require_permission("user:manage"))])
async def list_users():
    store = get_user_store()
    records = store.list_users()
    user_items = [
        UserListItem(
            username=r.username,
            role=r.role,
            is_active=r.is_active,
            created_at=r.created_at,
            last_login=r.last_login,
        )
        for r in records
    ]
    return {
        "code": 0,
        "message": "OK",
        "data": UserListResponse(total=len(user_items), users=user_items).model_dump(),
    }


@router.get("/me/permissions", response_model=dict)
async def my_permissions(request: Request):
    username = _get_username(request)
    perms = await get_user_permissions(username)
    return {
        "code": 0,
        "message": "OK",
        "data": PermissionCheckResult(
            has_permission=True,
            user_permissions=sorted(perms),
        ).model_dump(),
    }


@router.put("/{username}/role", response_model=dict, dependencies=[Depends(require_permission("user:manage"))])
async def assign_role(username: str, body: RoleAssignRequest):
    store = get_user_store()
    user = store.get_user(username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    old_role = user.role
    store.set_role(username, body.role_code)
    rbac_cache.invalidate(old_role)
    rbac_cache.invalidate(body.role_code)

    logger.info("Role changed for user %s: %s -> %s", username, old_role, body.role_code)
    return {
        "code": 0,
        "message": "Role assigned successfully",
        "data": UserResponse(
            username=user.username,
            role=body.role_code,
            created_at=user.created_at,
            last_login=user.last_login,
        ).model_dump(),
    }


@router.put("/{username}/status", response_model=dict, dependencies=[Depends(require_permission("user:manage"))])
async def set_user_status(username: str, body: UserStatusRequest):
    store = get_user_store()
    user = store.get_user(username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    store.set_active(username, body.is_active)
    action = "enabled" if body.is_active else "disabled"
    logger.info("User %s %s", username, action)
    return {
        "code": 0,
        "message": f"User {action} successfully",
        "data": UserResponse(
            username=user.username,
            role=user.role,
            created_at=user.created_at,
            last_login=user.last_login,
        ).model_dump(),
    }
