"""Project Packages API - 项目导入导出（``.lomo`` 包格式）REST 接口.

对应 ADR-015 阶段 6 p6-4：项目导入导出。

端点总览（prefix: ``/api/v1/project-packages``）：
    POST   /export                       导出项目为 .lomo 包（同步执行，返回导出记录）
    POST   /import                        导入 .lomo 包到目标项目（multipart 上传）
    POST   /validate                      校验 .lomo 包完整性（multipart 上传，不实际导入）
    POST   /preview                       预览 .lomo 包内容（multipart 上传，返回 manifest）
    GET    /exports                       列出导出记录（分页 + 过滤）
    GET    /exports/{export_id}           查询导出记录详情（支持 ?download=true 下载 .lomo 文件）
    DELETE /exports/{export_id}           删除导出包文件 + 记录
    GET    /imports                       列出导入记录（分页 + 过滤）

权限模型：
    project_package:read   —— 校验/预览包、查询导出/导入记录
    project_package:write  —— 导出项目、导入项目、删除导出包

长任务模式：
    导出/导入可能耗时数分钟（大项目含模型文件时）。当前实现为同步执行
    （请求阻塞至任务完成），服务层在执行前将记录状态置为 running，
    完成后置为 completed/failed。前端可通过 HTTP 长连接等待结果，
    或后续扩展为 BackgroundTasks + 轮询模式。

文件上传：
    POST /import / POST /validate / POST /preview 使用 multipart/form-data，
    前端通过 ``<input type="file">`` 选择 ``.lomo`` 文件上传。后端流式
    保存到 ``<output_dir>/package_uploads/`` 目录，再传给服务层处理。
"""

from __future__ import annotations

import logging
import os
import uuid


from app.utils.time import utcnow

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.config import config
from app.config.limits import STREAM_CHUNK_SIZE
from app.core.response import ErrorCode, error, success
from app.contracts.project_package import (
    ContentPolicy,
    ConflictStrategy,
    DEFAULT_MAX_FILE_SIZE_BYTES,
    ExportOptions,
    ImportOptions,
    PackageFormatVersion,
    PackageTaskStatus,
)
from app.dependencies import get_project_package_service
from app.services.project_package_service import (
    ExportRecordNotFoundError,
    ImportRecordNotFoundError,
    PackageChecksumError,
    PackageConflictError,
    PackageFormatError,
    PackageNotFoundError,
    ProjectNotFoundError,
    ProjectPackageError,
)

logger = logging.getLogger(__name__)

# 注意：本 router 尚未接入（main/router_registry 未引用本文件），路由未挂载。
router = APIRouter(
    prefix="/api/v1/project-packages",
    tags=["Project Packages (Import/Export)"],
)


# Pydantic 请求模型


class ExportProjectRequest(BaseModel):
    """导出项目请求体（JSON）.

    将项目及其引用资源打包为 ``.lomo`` 文件。
    """

    project_id: str = Field(..., min_length=1, max_length=64, description="源项目 ID")
    exported_by: str = Field(..., min_length=1, max_length=128, description="导出者（user_id 或 plugin_id）")
    output_dir: str = Field(
        default="",
        max_length=512,
        description="输出目录（空字符串表示使用服务层默认目录）",
    )
    content_policy: str = Field(
        default=ContentPolicy.INCLUDE_CONTENT,
        description=f"内容策略（{ContentPolicy.all()}，默认 include_content）",
    )
    include_datasets: bool = Field(default=True, description="是否打包数据集资源")
    include_models: bool = Field(default=True, description="是否打包模型产物资源")
    include_workflows: bool = Field(default=True, description="是否打包工作流定义")
    include_configs: bool = Field(default=True, description="是否打包配置规格")
    include_snapshots: bool = Field(default=True, description="是否打包实验快照元数据")
    include_lineage: bool = Field(default=True, description="是否打包血缘记录")
    max_file_size_bytes: int = Field(
        default=DEFAULT_MAX_FILE_SIZE_BYTES,
        ge=1,
        description="small_files_only 策略下的文件大小阈值（字节，默认 10MB）",
    )
    output_filename: str = Field(
        default="",
        max_length=256,
        description="自定义输出文件名（不含路径，空字符串使用默认模板）",
    )


# 辅助函数


def _handle_service_exception(e: Exception, *, action: str):
    """统一处理服务层异常 → API 错误响应.

    风格与 resource_cards.py / project_sync.py 对齐。

    Args:
        e: 服务层抛出的异常
        action: 当前操作描述（用于日志）

    Returns:
        error() 响应对象
    """
    if isinstance(
        e, (ProjectNotFoundError, PackageNotFoundError, ExportRecordNotFoundError, ImportRecordNotFoundError)
    ):
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    if isinstance(e, (PackageFormatError,)):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="请检查包格式版本是否受支持，或重新导出包",
        )
    if isinstance(e, PackageConflictError):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="目标机器已存在同 URI 资源，可改用 rename 策略或先清理冲突资源",
        )
    if isinstance(e, PackageChecksumError):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"包校验和校验失败：{e}",
            suggestion="包文件可能在传输中损坏，请重新导出或重新上传",
        )
    if isinstance(e, ValueError):
        # 参数校验失败（含策略非法、版本不兼容、URI 格式错误等）
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    if isinstance(e, ProjectPackageError):
        logger.error("Project package error during %s: %s", action, e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    # 兜底：未识别的异常
    logger.error("Unexpected error during %s: %s", action, e, exc_info=True)
    return error(
        code=ErrorCode.INTERNAL_ERROR,
        message=f"{action} 失败",
        detail=str(e),
    )


def _save_upload_file(upload_file: UploadFile, *, suffix: str = ".lomo") -> str:
    """流式保存上传的 ``.lomo`` 文件到磁盘，返回保存路径.

    保存位置：``<output_dir>/package_uploads/upload_<uuid><suffix>``

    Args:
        upload_file: FastAPI UploadFile 对象
        suffix: 文件后缀（默认 ``.lomo``）

    Returns:
        保存后的绝对路径

    Raises:
        ValueError: 上传文件为空或读取失败
        OSError: 磁盘写入失败
    """
    uploads_dir = os.path.join(os.path.abspath(config.storage.output_dir), "package_uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    target_path = os.path.join(uploads_dir, f"upload_{uuid.uuid4().hex}{suffix}")

    # 流式写入（64KB 缓冲，避免内存爆炸）
    buffer_size = STREAM_CHUNK_SIZE
    total_bytes = 0
    try:
        with open(target_path, "wb") as f:
            while True:
                chunk = upload_file.file.read(buffer_size)
                if not chunk:
                    break
                f.write(chunk)
                total_bytes += len(chunk)
    except OSError:
        # 写入失败时清理半成品文件
        if os.path.exists(target_path):
            try:
                os.remove(target_path)
            except OSError:
                pass
        raise

    if total_bytes == 0:
        # 清理空文件
        try:
            os.remove(target_path)
        except OSError:
            pass
        raise ValueError("上传文件为空")

    logger.info(
        "Saved upload file: %s (%d bytes, original_name=%s)",
        target_path,
        total_bytes,
        upload_file.filename,
    )
    return target_path


def _build_export_options(request: ExportProjectRequest) -> ExportOptions:
    """从请求体构造 ExportOptions dataclass.

    契约层 __post_init__ 会校验 content_policy / max_file_size_bytes 合法性。
    """
    return ExportOptions(
        content_policy=request.content_policy,
        include_datasets=request.include_datasets,
        include_models=request.include_models,
        include_workflows=request.include_workflows,
        include_configs=request.include_configs,
        include_snapshots=request.include_snapshots,
        include_lineage=request.include_lineage,
        max_file_size_bytes=request.max_file_size_bytes,
        output_filename=request.output_filename,
    )


def _build_import_options(
    conflict_strategy: str,
    target_owner_id: str,
    reinit_git: bool,
    dry_run: bool,
    target_project_name: str,
) -> ImportOptions:
    """从表单字段构造 ImportOptions dataclass."""
    return ImportOptions(
        conflict_strategy=conflict_strategy,
        target_owner_id=target_owner_id,
        reinit_git=reinit_git,
        dry_run=dry_run,
        target_project_name=target_project_name,
    )


# 端点 1: POST /export —— 导出项目


@router.post(
    "/export",
    dependencies=[Depends(require_permission("project_package:write"))],
)
async def export_project(request: ExportProjectRequest):
    """导出项目为 ``.lomo`` 包.

    流程：
        1. 前置校验 content_policy 合法性
        2. 创建 pending 导出记录
        3. 更新记录为 running
        4. 调用 ``service.export_project()`` 流式打包
        5. 成功 → 更新记录为 completed；失败 → 更新记录为 failed
        6. 返回导出结果（含 export_id / package_path / manifest / status）

    权限：``project_package:write``
    """
    # 前置校验：content_policy 合法性
    if not ContentPolicy.is_valid(request.content_policy):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"content_policy 不支持: {request.content_policy}（支持: {ContentPolicy.all()}）",
        )

    service = get_project_package_service()

    # 构造导出选项（契约层 __post_init__ 会校验）
    try:
        options = _build_export_options(request)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))

    # 创建 pending 导出记录
    try:
        export_record = await service.create_export_record(
            project_id=request.project_id,
            package_path="",  # 占位，导出完成后回填
            format_version=PackageFormatVersion.CURRENT,
            content_policy=request.content_policy,
            exported_by=request.exported_by,
        )
    except (ProjectNotFoundError, ValueError, ProjectPackageError) as e:
        return _handle_service_exception(e, action="创建导出记录")
    except Exception as e:
        return _handle_service_exception(e, action="创建导出记录")

    export_id = export_record.id

    # 更新为 running
    try:
        await service.update_export_record(export_id, status=PackageTaskStatus.RUNNING)
    except Exception as e:
        logger.warning("Failed to mark export %s as running: %s", export_id, e)

    # 执行导出
    try:
        result = await service.export_project(
            project_id=request.project_id,
            output_dir=request.output_dir,
            options=options,
            exported_by=request.exported_by,
        )
    except Exception as e:
        # 更新记录为 failed
        error_msg = f"{type(e).__name__}: {e}"
        try:
            await service.update_export_record(
                export_id,
                status=PackageTaskStatus.FAILED,
                error_message=error_msg,
                completed_at=utcnow(),
            )
        except Exception as update_err:
            logger.error("Failed to mark export %s as failed: %s", export_id, update_err)
        return _handle_service_exception(e, action="导出项目")

    # 更新记录为 completed
    try:
        await service.update_export_record(
            export_id,
            package_path=result.package_path,
            resource_count=result.resource_count,
            total_size_bytes=result.package_size_bytes,
            checksum=result.manifest.checksum,
            status=PackageTaskStatus.COMPLETED,
            completed_at=utcnow(),
        )
    except Exception as e:
        logger.error("Failed to mark export %s as completed: %s", export_id, e)

    payload = result.to_dict()
    # 追加 download_url 供前端下载
    payload["download_url"] = f"/api/v1/project-packages/exports/{export_id}?download=true"
    return success(
        data=payload,
        message=(f"项目已导出: {result.resource_count} 个资源，包大小 {result.package_size_bytes} 字节"),
    )


# 端点 2: POST /import —— 导入项目


@router.post(
    "/import",
    dependencies=[Depends(require_permission("project_package:write"))],
)
async def import_project(
    file: UploadFile = File(..., description=".lomo 包文件"),
    imported_by: str = Form(..., min_length=1, max_length=128, description="导入者（user_id 或 plugin_id）"),
    conflict_strategy: str = Form(
        ConflictStrategy.SKIP,
        description=f"冲突策略（{ConflictStrategy.all()}，默认 skip）",
    ),
    target_owner_id: str = Form(
        "",
        max_length=128,
        description="导入资源的目标所有者（空字符串继承源 manifest.exported_by）",
    ),
    reinit_git: bool = Form(True, description="导入后是否重新 git init"),
    dry_run: bool = Form(False, description="仅校验不实际写入"),
    target_project_name: str = Form(
        "",
        max_length=128,
        description="目标项目名（空字符串使用源 manifest.project.name）",
    ),
):
    """导入 ``.lomo`` 包到目标项目.

    流程：
        1. 前置校验 conflict_strategy 合法性
        2. 流式保存上传文件到 ``<output_dir>/package_uploads/``
        3. 创建 pending 导入记录（target_project_id 在导入完成后回填）
        4. 调用 ``service.import_project()`` 解压并导入资源
        5. 更新导入记录为 completed（含 imported/skipped/renamed/failed 计数）
        6. 返回导入结果

    权限：``project_package:write``
    """
    # 前置校验：conflict_strategy 合法性
    if not ConflictStrategy.is_valid(conflict_strategy):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"conflict_strategy 不支持: {conflict_strategy}（支持: {ConflictStrategy.all()}）",
        )

    # 流式保存上传文件
    try:
        package_path = _save_upload_file(file)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except OSError as e:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"保存上传文件失败: {e}",
        )

    service = get_project_package_service()

    # 构造导入选项
    try:
        options = _build_import_options(
            conflict_strategy=conflict_strategy,
            target_owner_id=target_owner_id,
            reinit_git=reinit_git,
            dry_run=dry_run,
            target_project_name=target_project_name,
        )
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))

    # 执行导入（dry_run 模式下不创建记录）
    try:
        result = await service.import_project(
            package_path=package_path,
            options=options,
            imported_by=imported_by,
        )
    except Exception as e:
        return _handle_service_exception(e, action="导入项目")

    # 非 dry_run 模式：创建导入记录
    if not dry_run:
        try:
            await service.create_import_record(
                source_package_path=package_path,
                source_project_id=result.project_id,
                target_project_id=result.target_project_id,
                format_version=result.format_version,
                conflict_strategy=conflict_strategy,
                imported_by=imported_by,
            )
        except Exception as e:
            # 记录创建失败不影响导入结果返回
            logger.error("Failed to create import record: %s", e, exc_info=True)

    payload = result.to_dict()
    return success(
        data=payload,
        message=(
            f"项目导入完成: 成功 {len(result.imported_resources)} 个，"
            f"跳过 {len(result.skipped_resources)} 个，"
            f"重命名 {len(result.renamed_resources)} 个，"
            f"失败 {len(result.failed_resources)} 个"
        ),
    )


# 端点 3: POST /validate —— 校验包完整性


@router.post("/validate")
async def validate_package(
    file: UploadFile = File(..., description=".lomo 包文件"),
):
    """校验 ``.lomo`` 包完整性（不实际导入）.

    校验内容：
        1. ``manifest.json`` 可解析
        2. ``format_version`` 受支持
        3. ``checksum`` 与重新计算的 sha256 一致
        4. 每个资源条目的 ``content_hash`` 与包内文件实际 sha256 一致
        5. ``path_in_package`` 指向的文件存在于包内

    权限：``project_package:read``
    """
    # 流式保存上传文件
    try:
        package_path = _save_upload_file(file)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except OSError as e:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"保存上传文件失败: {e}",
        )

    service = get_project_package_service()
    try:
        result = service.validate_package(package_path)
    except Exception as e:
        return _handle_service_exception(e, action="校验包完整性")

    message = (
        f"包校验通过（{result.verified_count}/{result.resource_count} 个资源验证成功）"
        if result.is_valid
        else f"包校验失败（{len(result.errors)} 个错误）"
    )
    return success(data=result.to_dict(), message=message)


# 端点 4: POST /preview —— 预览包内容


@router.post("/preview")
async def preview_import(
    file: UploadFile = File(..., description=".lomo 包文件"),
):
    """预览 ``.lomo`` 包内容（返回 manifest，不实际导入）.

    返回 ``PackageManifest`` 完整清单，前端用于展示"即将导入的内容"对话框：
        - format_version / exported_at / exported_by / source_machine
        - project（项目元数据）
        - resources（资源清单：URI / hash / 包内路径 / 大小 / 元数据）
        - content_policy / total_size_bytes / checksum

    权限：``project_package:read``
    """
    # 流式保存上传文件
    try:
        package_path = _save_upload_file(file)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except OSError as e:
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"保存上传文件失败: {e}",
        )

    service = get_project_package_service()
    try:
        manifest = service.preview_import(package_path)
    except Exception as e:
        return _handle_service_exception(e, action="预览包内容")

    return success(
        data=manifest.to_dict(),
        message=(f"包预览已获取: {manifest.resource_count} 个资源，总大小 {manifest.total_size_bytes} 字节"),
    )


# 端点 5: GET /exports —— 列出导出记录


@router.get("/exports")
async def list_exports(
    project_id: str | None = Query(None, description="按项目 ID 过滤"),
    status: str | None = Query(
        None,
        description=f"按状态过滤（{PackageTaskStatus.all()}）",
    ),
    exported_by: str | None = Query(None, description="按导出者过滤"),
    limit: int = Query(50, ge=1, le=500, description="每页数量（1-500，默认 50）"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """分页列出导出记录（支持 project_id / status / exported_by 过滤）.

    返回字段：
        - items: list[dict]（每个导出记录的 to_dict()）
        - total / limit / offset

    权限：``project_package:read``
    """
    # 前置校验：status 合法性
    if status is not None and not PackageTaskStatus.is_valid(status):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"status 不支持: {status}（支持: {PackageTaskStatus.all()}）",
        )

    service = get_project_package_service()
    try:
        result = await service.list_exports(
            project_id=project_id,
            status_filter=status,
            exported_by=exported_by,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        return _handle_service_exception(e, action="列出导出记录")

    return success(data=result, message="导出记录列表已获取")


# 端点 6: GET /exports/{export_id} —— 查询导出详情（支持下载）


@router.get("/exports/{export_id}")
async def get_export(
    export_id: str,
    download: bool = Query(False, description="为 true 时流式下载 .lomo 文件（要求 status=completed）"),
):
    """查询导出记录详情，或下载 ``.lomo`` 文件.

    - ``download=false``（默认）：返回导出记录详情（JSON）
    - ``download=true``：流式返回 ``.lomo`` 文件（要求 ``status=completed``）

    下载响应头：
        - ``Content-Type: application/octet-stream``
        - ``Content-Disposition: attachment; filename=<original_name>.lomo``

    权限：``project_package:read``
    """
    service = get_project_package_service()
    try:
        record = await service.get_export(export_id)
    except Exception as e:
        return _handle_service_exception(e, action="查询导出记录")

    # 非下载模式：返回 JSON 详情
    if not download:
        # 追加 download_url 字段
        payload = dict(record)
        payload["download_url"] = f"/api/v1/project-packages/exports/{export_id}?download=true"
        return success(data=payload, message="导出记录详情已获取")

    # 下载模式：流式返回 .lomo 文件
    if record.get("status") != PackageTaskStatus.COMPLETED:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"导出记录状态非 completed，无法下载: {record.get('status')}",
            suggestion="请等待导出任务完成后再下载",
        )

    package_path = record.get("package_path", "")
    if not package_path or not os.path.exists(package_path):
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f".lomo 文件不存在: {package_path}",
            suggestion="包文件可能已被删除，请重新导出",
        )

    # 构造下载文件名
    project_name = record.get("project", {}).get("name", "project")
    # 安全化文件名（移除路径分隔符）
    safe_name = project_name.replace("/", "_").replace("\\", "_")
    download_filename = f"{safe_name}_{export_id}.lomo"

    def _iter_file():
        """流式读取文件（64KB 缓冲）."""
        buffer_size = STREAM_CHUNK_SIZE
        with open(package_path, "rb") as f:
            while True:
                chunk = f.read(buffer_size)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{download_filename}"',
            "Content-Length": str(os.path.getsize(package_path)),
        },
    )


# 端点 7: DELETE /exports/{export_id} —— 删除导出包


@router.delete(
    "/exports/{export_id}",
    dependencies=[Depends(require_permission("project_package:write"))],
)
async def delete_export(export_id: str):
    """删除导出包文件 + 记录.

    设计原则：
        - 同时删除 ``.lomo`` 文件与 DB 记录（避免磁盘泄漏）
        - 删除后不可恢复，调用方需在前端二次确认
        - 若文件已不存在（手动删除），仅清理 DB 记录

    权限：``project_package:write``
    """
    service = get_project_package_service()
    try:
        result = await service.delete_export(export_id)
    except Exception as e:
        return _handle_service_exception(e, action="删除导出包")

    return success(
        data=result,
        message=f"导出包已删除: {export_id}",
    )


# 端点 8: GET /imports —— 列出导入记录


@router.get("/imports")
async def list_imports(
    target_project_id: str | None = Query(None, description="按目标项目 ID 过滤"),
    status: str | None = Query(
        None,
        description=f"按状态过滤（{PackageTaskStatus.all()}）",
    ),
    imported_by: str | None = Query(None, description="按导入者过滤"),
    limit: int = Query(50, ge=1, le=500, description="每页数量（1-500，默认 50）"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """分页列出导入记录（支持 target_project_id / status / imported_by 过滤）.

    返回字段：
        - items: list[dict]（每个导入记录的 to_dict()）
        - total / limit / offset

    权限：``project_package:read``
    """
    # 前置校验：status 合法性
    if status is not None and not PackageTaskStatus.is_valid(status):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"status 不支持: {status}（支持: {PackageTaskStatus.all()}）",
        )

    service = get_project_package_service()
    try:
        result = await service.list_imports(
            target_project_id=target_project_id,
            status_filter=status,
            imported_by=imported_by,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        return _handle_service_exception(e, action="列出导入记录")

    return success(data=result, message="导入记录列表已获取")


__all__ = ["router"]
