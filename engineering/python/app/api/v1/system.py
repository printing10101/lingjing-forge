"""系统信息端点（version / info / backup）。

- /api/v1/system/version：带前缀的系统版本（供内部模块调用）
- /api/v1/version：兼容旧版根路径的版本端点（顶层路由测试依赖）
- /api/v1/system/backup：创建桌面 SQLite 数据备份（admin）
- /api/v1/system/backups：列出历史备份（admin）
- /api/v1/system/backup/{id}/restore：恢复到指定目录（admin）
"""

from fastapi import APIRouter, Depends, HTTPException

from app.auth.permissions import require_role
from app.services.backup_service import get_backup_service
from app.version import get_version_info

router = APIRouter(prefix="/api/v1/system", tags=["System"])
version_router = APIRouter(prefix="/api/v1", tags=["System"])


@router.get("/version")
async def system_version():
    return get_version_info()


@router.get("/update-check", summary="检查 GitHub Releases 最新版本")
async def update_check():
    """「关于」页检查更新：对比当前版本与 GitHub latest release（过渡方案）。

    失败不抛错（fail-soft），通过响应 error 字段返回短代码
    （"network" / "parse"），由前端转译为本地化提示。
    """
    from app.services.update_check import check_for_updates

    return await check_for_updates()


@version_router.get("/version")
async def api_version():
    """兼容端点：GET /api/v1/version（旧版顶层版本接口）。"""
    return get_version_info()


@router.post("/backup", dependencies=[Depends(require_role("admin"))])
async def create_backup():
    """创建一次桌面 SQLite 数据全量备份。"""
    try:
        return get_backup_service().create_backup()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/backups", dependencies=[Depends(require_role("admin"))])
async def list_backups():
    """列出历史备份（按时间倒序）。"""
    return {"backups": get_backup_service().list_backups()}


@router.post("/backup/{backup_id}/restore", dependencies=[Depends(require_role("admin"))])
async def restore_backup(backup_id: str, target_dir: str = "./restore"):
    """将指定备份恢复到目标目录（不覆盖同名文件）。"""
    try:
        return get_backup_service().restore_backup(backup_id, target_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
