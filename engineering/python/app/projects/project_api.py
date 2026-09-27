"""工程管理 REST API。

提供工程文件的 CRUD 端点：
- POST   /api/projects/new          — 新建空白工程
- POST   /api/projects/open         — 打开 .ljm 工程文件
- POST   /api/projects/save         — 保存工程
- POST   /api/projects/save-as      — 另存为工程
- GET    /api/projects/list         — 列出工程
- DELETE /api/projects/{project_id} — 删除工程
- POST   /api/projects/upload-resource — 上传资源文件
- GET    /api/projects/download/{filename} — 下载工程文件
"""

import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.auth.permissions import require_permission
from app.core.response import error, ErrorCode, success
from app.core.safe_errors import safe_error_message
from app.utils.utils import get_output_dir, get_upload_dir, validate_user_path
from app.utils.upload_security import validate_upload
from app.projects.project_store import (
    ProjectStore,
    ProjectManifest,
    PROJECT_FORMAT_VERSION,
    PROJECT_FILE_EXTENSION,
)
from app.config.limits import MAX_UPLOAD_SIZE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["Project Management"])

OUTPUT_DIR = get_output_dir("projects")
TEMP_UPLOAD_DIR = get_upload_dir("projects")

# 修复 [路径遍历]：解析后的安全输出根目录。所有下载/删除接口必须把请求路径解析
# 后与该根做 is_relative_to 校验，避免 ../ 等逃逸字符触达工作区之外的文件。
_OUTPUT_DIR_RESOLVED = OUTPUT_DIR.resolve()

# 修复 [B9] /open 端点路径遍历：允许打开 .ljm 工程文件的根目录白名单。
# 1. OUTPUT_DIR（output/projects）—— 服务端保存的工程文件默认位置；
# 2. 项目根目录 —— 兼容用户从仓库内 fixtures/示例 目录打开工程；
# 3. 通过环境变量 LNN_PROJECT_OPEN_BASE_DIRS 注入的额外目录（多路径用 os.pathsep 分隔）。
# 任意 file_path 在 resolve() 后必须位于以下根目录之一，否则视为路径遍历攻击。
_ALLOWED_PROJECT_FILE_BASE_DIRS: list[Path] = [
    _OUTPUT_DIR_RESOLVED,
    Path(__file__).resolve().parents[3],  # python/ 项目根
]
_extra_open_dirs = os.getenv("LNN_PROJECT_OPEN_BASE_DIRS", "")
if _extra_open_dirs:
    for _d in _extra_open_dirs.split(os.pathsep):
        _d = _d.strip()
        if _d:
            _ALLOWED_PROJECT_FILE_BASE_DIRS.append(Path(_d).resolve())

_store = ProjectStore(str(OUTPUT_DIR))


def _safe_project_path(project_name: str) -> Path:
    """校验工程名拼装出的路径严格位于 OUTPUT_DIR 内。

    修复 [路径遍历]：原实现直接将 ``OUTPUT_DIR / project_name`` 暴露给删除/下载
    端点，攻击者可构造 ``../../../etc/passwd`` 等输入越权访问或删除工作区外文件。
    这里采用 ``resolve()`` + ``is_relative_to()`` 双重校验，确保最终路径一定在
    ``OUTPUT_DIR`` 之内。
    """
    if not project_name:
        raise HTTPException(status_code=400, detail="无效的工程名")
    # 仅保留 ``Path.name`` 兼容层，剥离任何目录分隔符；后续 resolve() 兜底
    base_name = Path(project_name).name
    if base_name != project_name and (os.sep in project_name or "/" in project_name):
        raise HTTPException(status_code=400, detail="无效的工程名")
    candidate = (OUTPUT_DIR / project_name).resolve()
    if not candidate.is_relative_to(_OUTPUT_DIR_RESOLVED):
        raise HTTPException(status_code=400, detail="无效的工程路径")
    return candidate


def _validate_project_file_path(user_path: str) -> Path:
    """校验 /open 端点接收的 .ljm 工程文件路径，防止路径遍历攻击。

    修复 [B9]：原 ``/open`` 端点直接将 ``request.file_path`` 透传给
    ``_store.open_project()``，攻击者可构造 ``/etc/passwd``、
    ``../../../app.db`` 等任意路径读取服务器文件（包括 .ljm 之外的任意文件）。
    本函数委托给统一的 ``app.utils.utils.validate_user_path``，在 ``resolve()``
    后强制校验绝对路径必须位于 ``_ALLOWED_PROJECT_FILE_BASE_DIRS`` 之一之下，
    并要求扩展名为 ``.ljm``，从而将可读取范围严格限制在工程目录内。

    Args:
        user_path: 用户提交的 .ljm 工程文件路径（相对或绝对）

    Returns:
        校验通过后的 Path 对象

    Raises:
        HTTPException: 400 当路径为空、扩展名非 .ljm 或解析后超出允许目录范围
    """
    try:
        return validate_user_path(
            user_path=user_path,
            allowed_base_dirs=_ALLOWED_PROJECT_FILE_BASE_DIRS,
            allowed_extensions={PROJECT_FILE_EXTENSION.lower()},
            project_root=Path(__file__).resolve().parents[3],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# 请求/响应模型


class ProjectMetadataRequest(BaseModel):
    name: str = Field(default="未命名工程", max_length=128, description="工程名称")
    author: str = Field(default="", max_length=64, description="作者")
    description: str = Field(default="", max_length=512, description="工程描述")


class SaveRequest(BaseModel):
    manifest: dict = Field(description="完整的工程清单数据(project.json内容)")
    project_id: str = Field(default="", description="工程ID（保存已有工程时使用）")
    output_name: str = Field(default="", description="输出文件名（另存为时使用）")


class OpenRequest(BaseModel):
    file_path: str = Field(default="", description="要打开的 .ljm 文件路径")
    upload_data: str | None = Field(default=None, description="Base64编码的.ljm文件数据")


class ResourceUploadMeta(BaseModel):
    resource_type: str = Field(
        default="model",
        pattern="^(drawing|model|toolpath|simulation|postprocessor|extension)$",
        description="资源类型",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="额外元数据")


# API 端点


@router.post("/new", dependencies=[Depends(require_permission("project:write"))])
async def create_project(request: ProjectMetadataRequest) -> dict:
    """创建新的空白工程。

    返回初始化的 project.json 内容，供前端填充后续数据。

    Args:
        request: 工程元数据（名称、作者、描述）

    Returns:
        新工程的 project.json 数据 + 工程ID
    """
    try:
        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        manifest = _store.create_project(
            name=request.name,
            author=request.author,
            description=request.description,
        )
        return success(
            data={
                "project_id": project_id,
                "manifest": manifest.to_dict(),
                "version": PROJECT_FORMAT_VERSION,
            },
            message=f'工程 "{request.name}" 创建成功',
        )
    except (ValueError, TypeError, KeyError, OSError, IOError) as e:
        # 修复：使用 safe_error_message 包装，避免将内部异常细节（堆栈/路径/库版本）暴露给前端
        safe = safe_error_message(e, context="projects.create", fallback="创建工程失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.post("/open", dependencies=[Depends(get_current_user)])
async def open_project(
    request: OpenRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """打开 .ljm 工程文件。

    支持两种打开方式：
    1. 指定服务端文件路径
    2. 上传 Base64 编码的文件数据

    Args:
        request: 文件路径或Base64数据
        background_tasks: 后台任务管理器

    Returns:
        解析后的工程清单数据
    """
    tmp_path: Path | None = None

    try:
        if request.upload_data:
            import base64

            tmp_fd, tmp_name = tempfile.mkstemp(suffix=PROJECT_FILE_EXTENSION)
            os.close(tmp_fd)
            tmp_path = Path(tmp_name)
            tmp_path.write_bytes(base64.b64decode(request.upload_data))
            file_path = str(tmp_path)
        elif request.file_path:
            # 修复 [B9]：对 file_path 做路径遍历校验，确保最终路径
            # 位于工程允许的根目录下且扩展名为 .ljm，
            # 避免攻击者读取服务器任意文件（如 /etc/passwd、app.db）。
            validated_path = _validate_project_file_path(request.file_path)
            file_path = str(validated_path)
        else:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message="请提供 file_path 或 upload_data 参数",
            )

        manifest = _store.open_project(file_path)

        if tmp_path:
            background_tasks.add_task(lambda: tmp_path.unlink(missing_ok=True))

        return success(
            data={
                "manifest": manifest.to_dict(),
                "file_path": file_path,
                "version": manifest.version,
            },
            message=f'工程 "{manifest.metadata.name if manifest.metadata else ""}" 打开成功',
        )
    except FileNotFoundError:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        return error(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"工程文件未找到: {request.file_path}",
            recoverable=True,
        )
    except ValueError as e:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        logger.warning("Invalid project open request: %s", e)
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="工程文件参数无效或路径校验失败",
            recoverable=True,
        )
    except (ValueError, TypeError, KeyError, OSError, IOError) as e:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        # 修复：避免 str(e) 直接进入响应，泄露内部异常细节
        safe = safe_error_message(e, context="projects.open", fallback="打开工程失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except (OSError, FileNotFoundError) as cleanup_err:
                # 临时文件清理失败不应阻塞响应，记录以便后续排查
                logger.debug(
                    "Failed to cleanup project tmp file %s: %s",
                    tmp_path,
                    cleanup_err,
                    exc_info=True,
                )


@router.post("/save")
async def save_project(
    request: SaveRequest,
    _perm: None = Depends(require_permission("project:write")),
) -> dict:
    """保存工程为 .ljm 文件。

    接收完整的工程清单数据，将其打包为ZIP格式并存储。

    修复 [B23]：原端点无认证，任意未登录调用方可保存工程文件。
    通过 Depends(require_permission("project:write")) 强制认证 + 权限校验，
    未登录调用方将得到 401，权限不足将得到 403。

    Args:
        request: 工程清单数据 + 可选输出文件名

    Returns:
        保存后的文件路径和工程ID
    """
    try:
        manifest = ProjectManifest.from_dict(request.manifest)
        project_id = request.project_id or f"proj_{uuid.uuid4().hex[:12]}"
        output_name = request.output_name or (
            f"{manifest.metadata.name if manifest.metadata else 'project'}{PROJECT_FILE_EXTENSION}"
        )
        if not output_name.endswith(PROJECT_FILE_EXTENSION):
            output_name += PROJECT_FILE_EXTENSION

        output_path = OUTPUT_DIR / output_name

        saved_path = _store.save_project(manifest, output_path)
        file_size = Path(saved_path).stat().st_size if Path(saved_path).exists() else 0

        return success(
            data={
                "project_id": project_id,
                "file_path": saved_path,
                "file_name": Path(saved_path).name,
                "file_size": file_size,
                "version": manifest.version,
            },
            message="工程保存成功",
        )
    except (OSError, ValueError, TypeError, KeyError) as e:
        logger.error("保存工程失败: %s", e, exc_info=True)
        # 修复：避免 str(e) 直接进入响应
        safe = safe_error_message(e, context="projects.save", fallback="保存工程失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.post("/save-as")
async def save_as_project(
    request: SaveRequest,
    _perm: None = Depends(require_permission("project:write")),
) -> dict:
    """另存为工程文件。

    创建工程文件的新副本，不覆盖原文件。

    修复 [B23]：原端点无认证，任意未登录调用方可另存工程文件。
    通过 Depends(require_permission("project:write")) 强制认证 + 权限校验，
    未登录调用方将得到 401，权限不足将得到 403。

    Args:
        request: 工程清单数据 + 输出文件名

    Returns:
        新文件的路径
    """
    try:
        manifest = ProjectManifest.from_dict(request.manifest)
        output_name = request.output_name
        if not output_name:
            output_name = f"{manifest.metadata.name if manifest.metadata else 'project'}_copy{PROJECT_FILE_EXTENSION}"
        if not output_name.endswith(PROJECT_FILE_EXTENSION):
            output_name += PROJECT_FILE_EXTENSION

        output_path = OUTPUT_DIR / output_name
        if output_path.exists():
            base = output_path.stem
            suffix = output_path.suffix
            counter = 1
            while output_path.exists():
                output_path = OUTPUT_DIR / f"{base}({counter}){suffix}"
                counter += 1

        saved_path = _store.save_as_project(manifest, output_path)

        return success(
            data={
                "file_path": saved_path,
                "file_name": Path(saved_path).name,
                "file_size": Path(saved_path).stat().st_size,
                "version": manifest.version,
            },
            message=f'工程另存为 "{Path(saved_path).name}" 成功',
        )
    except (OSError, ValueError, TypeError, KeyError) as e:
        logger.error("另存为工程失败: %s", e, exc_info=True)
        # 修复：避免 str(e) 直接进入响应
        safe = safe_error_message(e, context="projects.save_as", fallback="另存为工程失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.get("/list", dependencies=[Depends(get_current_user)])
async def list_projects() -> dict:
    """列出所有工程文件。

    Returns:
        工程文件摘要列表
    """
    try:
        projects = _store.list_projects()
        return success(
            data={
                "total": len(projects),
                "items": projects,
            },
            message="OK",
        )
    except (OSError, ValueError, TypeError) as e:
        logger.error("获取工程列表失败: %s", e, exc_info=True)
        # 修复：避免 str(e) 直接进入响应
        safe = safe_error_message(e, context="projects.list", fallback="获取工程列表失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.delete("/{project_name}")
async def delete_project(
    project_name: str,
    _perm: None = Depends(require_permission("project:write")),
) -> dict:
    """删除指定的工程文件。

    修复 [B23]：原端点无认证，任意未登录调用方可删除工程文件。
    通过 Depends(require_permission("project:write")) 强制认证 + 权限校验，
    未登录调用方将得到 401，权限不足将得到 403。

    Args:
        project_name: 工程文件名（含 .ljm 扩展名）

    Returns:
        操作结果
    """
    try:
        if not project_name.endswith(PROJECT_FILE_EXTENSION):
            project_name += PROJECT_FILE_EXTENSION
        # 修复 [路径遍历]：先校验拼装出的路径在 OUTPUT_DIR 内，再交给 store。
        file_path = _safe_project_path(project_name)
        if _store.delete_project(file_path):
            return success(message=f"工程 {project_name} 已删除")
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"工程文件不存在: {project_name}",
            recoverable=True,
        )
    except HTTPException:
        raise
    except (OSError, ValueError, TypeError) as e:
        logger.error("删除工程失败: %s", e, exc_info=True)
        # 修复：避免 str(e) 直接进入响应
        safe = safe_error_message(e, context="projects.delete", fallback="删除工程失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.get("/download/{project_name}", dependencies=[Depends(get_current_user)])
async def download_project(project_name: str) -> FileResponse:
    """下载工程文件。

    Args:
        project_name: 工程文件名（含 .ljm 扩展名）

    Returns:
        文件流响应
    """
    if not project_name.endswith(PROJECT_FILE_EXTENSION):
        project_name += PROJECT_FILE_EXTENSION
    # 修复 [路径遍历]：阻止 ``../`` 等逃逸字符越权读取 OUTPUT_DIR 之外的文件。
    file_path = _safe_project_path(project_name)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="工程文件不存在")
    return FileResponse(
        path=str(file_path),
        media_type="application/zip",
        filename=file_path.name,
    )


# 文件上传限制
# P0-12 修复：100MB 全量入内存 50MB 分块流式读取 + magic bytes 校验
# ``MAX_UPLOAD_SIZE`` 由 ``app.config.limits`` 集中管理，与 dxf/step_import/
# upload_security 等模块共享同一基准值。
ALLOWED_UPLOAD_EXTENSIONS = {".step", ".stp", ".dxf", ".igs", ".iges", ".stl", ".obj"}
# 各扩展名对应的允许 MIME 集合（由 upload_security.EXTENSION_TO_MIME 推导）
_ALLOWED_UPLOAD_MIMES = {
    "application/step",
    "application/iges",
    "application/dxf",
    "application/sla",
    "application/octet-stream",
}


@router.post("/upload-resource")
async def upload_resource(
    file: UploadFile,
    resource_type: str = Query(default="model"),
    _perm: None = Depends(require_permission("project:write")),
) -> dict:
    """上传资源文件到临时目录。

    上传的文件将在保存工程时被打包进 .ljm 文件中。

    修复 [P0-12]：
    1. 添加 ``Depends(require_permission("project:write"))`` 强制认证 + 权限校验，
       未登录调用方将得到 401，权限不足将得到 403。
    2. 将 ``await file.read()`` 全量入内存改为 ``validate_upload`` 分块流式读取，
       超过 50MB 立即中止并返回 413，避免 OOM/DoS。

    修复 [P0-13]：
    3. 通过 ``validate_upload`` 执行 magic bytes 签名校验，防止伪装的 PE/脚本文件
       仅靠扩展名白名单绕过。

    Args:
        file: 上传的文件
        resource_type: 资源类型

    Returns:
        资源ID和临时路径
    """
    try:
        # 验证文件名
        if not file.filename:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message="文件名不能为空",
            )

        # P0-12/P0-13 修复：统一上传校验（扩展名 + magic bytes + 分块读取 + 大小限制）
        # validate_upload 抛出 HTTPException(413/415/400)，由 except HTTPException 透传
        content = await validate_upload(
            file,
            max_size=MAX_UPLOAD_SIZE,
            allowed_extensions=ALLOWED_UPLOAD_EXTENSIONS,
            allowed_mimes=_ALLOWED_UPLOAD_MIMES,
        )

        ext = Path(file.filename).suffix.lower()

        # 生成安全的文件名（防止路径遍历）
        resource_id = f"res_{uuid.uuid4().hex[:12]}"
        tmp_name = f"{resource_id}{ext}"
        tmp_path = TEMP_UPLOAD_DIR / tmp_name

        tmp_path.write_bytes(content)

        return success(
            data={
                "resource_id": resource_id,
                "temp_path": str(tmp_path),
                "file_name": file.filename,
                "file_size": len(content),
                "resource_type": resource_type,
            },
            message="资源上传成功",
        )
    except HTTPException:
        # validate_upload 抛出的 413/415/400 直接透传给客户端
        raise
    except (OSError, ValueError, TypeError, KeyError) as e:
        logger.error("资源上传失败: %s", e, exc_info=True)
        # 修复：避免 str(e) 直接进入响应
        safe = safe_error_message(e, context="projects.upload_resource", fallback="资源上传失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )
