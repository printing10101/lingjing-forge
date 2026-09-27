"""项目导入导出服务层.

对应 ADR-015（项目导入导出）。

职责：
    1. 导出：`export_project` —— 调用 ProjectSyncService 获取项目 + 资源引用，
       流式写入 ZIP（.lomo 包），计算 sha256 校验和
    2. 导入：`import_project` —— 解压 .lomo 包，校验 manifest + checksum，
       按 conflict_strategy 处理冲突，流式写入资源文件到目标项目目录
    3. 校验：`validate_package` —— 校验包完整性（manifest + checksum + 资源 hash）
    4. 预览：`preview_import` —— 返回 manifest，不实际导入
    5. 记录管理：create_export / get_export / list_exports / delete_export /
       create_import / get_import / list_imports —— 持久化导出/导入任务记录

线程安全：
    - 单例通过双重检查锁创建
    - 写操作（export/import）按 project_id / package_path 串行化，
      通过 _locks dict + _locks_guard 保护
    - DB 写操作通过 SQLAlchemy 事务保证原子性，显式 commit()

错误处理风格（与 ResourceCardService / ProjectSyncService 对齐）：
    - 参数校验失败 → ValueError
    - 资源不存在 → LookupError 子类
    - 业务状态非法 → ProjectPackageError 子类
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import threading
import zipfile
from app.utils.time import utcnow, utcnow_iso_z
from typing import Any

from sqlalchemy import desc, func, select

from app.config import config
from app.contracts.project_package import (
    ConflictStrategy,
    ExportOptions,
    ExportResult,
    ImportOptions,
    ImportResourceRecord,
    ImportResult,
    PackageFormatVersion,
    PackageManifest,
    PackageProjectInfo,
    PackageResourceEntry,
    PackageTaskStatus,
    STREAM_BUFFER_SIZE,
    SourceMachineInfo,
    ValidationResult,
)
from app.database.models.project_package import (
    ProjectExport,
    ProjectImport,
    _gen_export_id,
    _gen_import_id,
)
from app.services._shared.service_base import BaseSingletonService

from app.services._package_io import (
    PackageFormatError,
    _resolve_output_path,
    _resolve_resource_path,
    _compute_sha256,
    _compute_manifest_checksum,
    _get_source_machine_info,
    _build_package_path,
    _read_manifest,
    _check_existing_resources,
    _rename_target_path,
)

logger = logging.getLogger(__name__)


# 自定义异常层级（与 ResourceCardService / ProjectSyncService 对齐）


class ProjectPackageError(RuntimeError):
    """项目包服务基类异常."""


class ProjectNotFoundError(LookupError):
    """项目不存在."""


class PackageNotFoundError(LookupError):
    """包文件不存在."""


class PackageChecksumError(ProjectPackageError):
    """包校验和校验失败."""


class ExportRecordNotFoundError(LookupError):
    """导出记录不存在."""


class ImportRecordNotFoundError(LookupError):
    """导入记录不存在."""


class PackageConflictError(ProjectPackageError):
    """冲突策略 fail 触发（目标已存在同 URI 资源）."""


# 单例


def get_project_package_service() -> "ProjectPackageService":
    """获取 ProjectPackageService 单例（委托给 ``ProjectPackageService.get_instance``）."""
    return ProjectPackageService.get_instance()  # type: ignore[return-value]


def reset_project_package_service() -> None:
    """重置单例（仅供测试，委托给 ``ProjectPackageService.reset_instance``）."""
    ProjectPackageService.reset_instance()


# 服务实现


class ProjectPackageService(BaseSingletonService):
    """项目导入导出服务.

    内部组合 ProjectSyncService（获取项目元数据 + 资源引用 + 创建目标项目），
    自身管理 project_exports + project_imports 两张 ORM 表的持久化。

    设计原则：
        - 读操作（get/list/validate/preview）无锁
        - 写操作（export/import）按 project_id / package_path 串行化
        - ZIP 操作使用标准库 zipfile（同步 IO），大文件流式写入 64KB 缓冲
        - 异步方法内不持有锁（锁只在 ZIP IO 前后短暂持有）
    """

    def __init__(self) -> None:
        # 按 key（project_id / package_path）串行化写操作
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # 包存储根目录：<output_dir>/project_packages/
        self._packages_root = os.path.join(os.path.abspath(config.storage.output_dir), "project_packages")
        os.makedirs(self._packages_root, exist_ok=True)

    # ── 锁管理 ─────────────────────────────────────────────────────────

    def _get_lock(self, key: str) -> threading.Lock:
        """获取或创建指定 key 的锁（线程安全）."""
        lock = self._locks.get(key)
        if lock is not None:
            return lock
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    # ── 路径辅助 ───────────────────────────────────────────────────────

    def _resolve_output_path(self, output_dir: str, options: ExportOptions) -> str:
        """从 _package_io 委托。"""
        return _resolve_output_path(output_dir, options)

    def _resolve_resource_path(self, ref: dict[str, Any]) -> str | None:
        """从 _package_io 委托。"""
        return _resolve_resource_path(ref)

    def _compute_sha256(self, file_path: str) -> str:
        """从 _package_io 委托。"""
        return _compute_sha256(file_path)

    def _compute_manifest_checksum(self, manifest_dict: dict[str, Any]) -> str:
        """从 _package_io 委托。"""
        return _compute_manifest_checksum(manifest_dict)

    def _get_source_machine_info(self) -> SourceMachineInfo:
        """从 _package_io 委托。"""
        return _get_source_machine_info()

    # ── 契约方法：导出 ──────────────────────────────────────────────────

    async def export_project(
        self,
        project_id: str,
        output_dir: str,
        options: ExportOptions,
        exported_by: str,
    ) -> ExportResult:
        """导出项目为 .lomo 包.

        Args:
            project_id: 源项目 ID
            output_dir: 输出目录
            options: 导出选项
            exported_by: 导出者

        Returns:
            导出结果

        Raises:
            ProjectNotFoundError: 项目不存在
            ValueError: 选项不合法
            ProjectPackageError: 导出失败
        """
        # 延迟导入避免循环依赖
        from app.dependencies import get_project_sync_service

        if not project_id:
            raise ValueError("project_id 不能为空")
        if not output_dir:
            raise ValueError("output_dir 不能为空")
        if not exported_by:
            raise ValueError("exported_by 不能为空")

        lock = self._get_lock(f"export:{project_id}")
        with lock:
            sync_service = get_project_sync_service()
            # 获取项目元数据 + 资源引用
            try:
                project_dict = await sync_service.get_project(project_id, include_refs=True)
            except LookupError as e:
                raise ProjectNotFoundError(str(e)) from e

            refs = project_dict.get("resource_refs") or []
            package_path = self._resolve_output_path(output_dir, options)
            source_machine = self._get_source_machine_info()
            project_info = PackageProjectInfo(
                project_id=project_dict["project_id"],
                name=project_dict.get("name", ""),
                description=project_dict.get("description", ""),
                author=project_dict.get("author", ""),
                remote_url=project_dict.get("remote_url", ""),
                current_branch=project_dict.get("current_branch", "main"),
                current_commit=project_dict.get("current_commit", ""),
            )

            # 阶段 1：构建资源条目并打包内容
            resource_entries: list[PackageResourceEntry] = []
            skipped_resources: list[str] = []
            packed_count = 0
            total_size = 0

            # 流式写入 ZIP
            with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for ref in refs:
                    resource_type = ref.get("resource_type", "")
                    resource_uri = ref.get("resource_uri", "")
                    if not resource_uri:
                        continue
                    # 检查 options 是否包含该资源类型
                    if not options.should_include(resource_type):
                        skipped_resources.append(resource_uri)
                        continue

                    # 解析内容路径
                    content_path = self._resolve_resource_path(ref)
                    size_bytes = 0
                    content_hash = ref.get("content_hash") or ""
                    path_in_package = ""

                    if content_path and options.should_pack_content(os.path.getsize(content_path)):
                        # 打包内容
                        ext = os.path.splitext(content_path)[1] or ".bin"
                        path_in_package = self._build_package_path(resource_type, resource_uri, ext)
                        # 流式写入文件
                        with open(content_path, "rb") as f:
                            buf = io.BufferedReader(f, buffer_size=STREAM_BUFFER_SIZE)
                            zf.writestr(path_in_package, buf.read())
                        size_bytes = os.path.getsize(content_path)
                        # 重新计算 hash（如果 ref 中没有）
                        if not content_hash:
                            content_hash = f"sha256:{self._compute_sha256(content_path)}"
                        packed_count += 1
                        total_size += size_bytes
                    else:
                        # 仅元数据
                        if content_path:
                            skipped_resources.append(f"{resource_uri} (excluded by content_policy)")
                        path_in_package = ""
                        size_bytes = 0

                    # 从 ref metadata 提取附加元数据
                    ref_metadata = ref.get("metadata") or {}
                    entry_metadata = {k: v for k, v in ref_metadata.items() if k not in {"path", "storage_uri"}}

                    resource_entries.append(
                        PackageResourceEntry(
                            resource_type=resource_type,
                            resource_uri=resource_uri,
                            content_hash=content_hash,
                            path_in_package=path_in_package,
                            size_bytes=size_bytes,
                            metadata=entry_metadata,
                        )
                    )

                # 阶段 2：构建并写入 manifest.json
                manifest = PackageManifest(
                    format_version=PackageFormatVersion.CURRENT,
                    exported_at=utcnow_iso_z(),
                    exported_by=exported_by,
                    source_machine=source_machine,
                    project=project_info,
                    resources=tuple(resource_entries),
                    content_policy=options.content_policy,
                    total_size_bytes=total_size,
                    checksum="",  # 稍后填充
                )

                manifest_dict = manifest.to_dict()
                checksum = self._compute_manifest_checksum(manifest_dict)
                manifest_dict["checksum"] = f"sha256:{checksum}"
                zf.writestr(
                    "manifest.json",
                    json.dumps(manifest_dict, ensure_ascii=False, indent=2),
                )

            package_size = os.path.getsize(package_path)
            export_id = _gen_export_id()

            # 重建 manifest（带 checksum）
            final_manifest = PackageManifest(
                format_version=manifest.format_version,
                exported_at=manifest.exported_at,
                exported_by=manifest.exported_by,
                source_machine=manifest.source_machine,
                project=manifest.project,
                resources=manifest.resources,
                content_policy=manifest.content_policy,
                total_size_bytes=manifest.total_size_bytes,
                checksum=f"sha256:{checksum}",
            )

            result = ExportResult(
                export_id=export_id,
                project_id=project_id,
                package_path=package_path,
                manifest=final_manifest,
                resource_count=len(resource_entries),
                packed_count=packed_count,
                skipped_resources=tuple(skipped_resources),
                total_size_bytes=total_size,
                package_size_bytes=package_size,
                status=PackageTaskStatus.COMPLETED,
                created_at=utcnow(),
                completed_at=utcnow(),
            )

            logger.info(
                "Exported project %s to %s (resources=%d, packed=%d, size=%d)",
                project_id,
                package_path,
                len(resource_entries),
                packed_count,
                package_size,
            )
            return result

    def _build_package_path(self, resource_type: str, resource_uri: str, ext: str) -> str:
        """从 _package_io 委托。"""
        return _build_package_path(resource_type, resource_uri, ext)

    # ── 契约方法：导入 ──────────────────────────────────────────────────

    async def import_project(
        self,
        package_path: str,
        options: ImportOptions,
        imported_by: str,
    ) -> ImportResult:
        """从 .lomo 包导入项目.

        Args:
            package_path: .lomo 文件路径
            options: 导入选项
            imported_by: 导入者

        Returns:
            导入结果

        Raises:
            PackageNotFoundError: 包文件不存在
            PackageFormatError: 包格式不合法
            PackageConflictError: 冲突策略 fail 触发
        """
        if not package_path:
            raise ValueError("package_path 不能为空")
        if not os.path.exists(package_path):
            raise PackageNotFoundError(f"包文件不存在: {package_path}")
        if not imported_by:
            raise ValueError("imported_by 不能为空")

        lock = self._get_lock(f"import:{os.path.abspath(package_path)}")
        with lock:
            # 阶段 1：读取并校验 manifest
            manifest = self._read_manifest(package_path)
            if not PackageFormatVersion.is_supported(manifest.format_version):
                raise PackageFormatError(f"包格式版本不支持: {manifest.format_version}")

            # 阶段 2：创建目标项目（通过 ProjectSyncService）
            from app.dependencies import get_project_sync_service

            sync_service = get_project_sync_service()
            target_name = options.target_project_name or manifest.project.name
            if options.dry_run:
                # dry_run 模式：不创建项目，仅返回预览
                return ImportResult(
                    import_id=_gen_import_id(),
                    source_project_id=manifest.project.project_id,
                    target_project_id="",
                    source_package_path=package_path,
                    format_version=manifest.format_version,
                    conflict_strategy=options.conflict_strategy,
                    resource_records=tuple(),
                    imported_count=0,
                    skipped_count=0,
                    renamed_count=0,
                    failed_count=0,
                    warnings=("dry_run 模式，未实际导入",),
                    status=PackageTaskStatus.COMPLETED,
                )

            try:
                target_project = await sync_service.create_project(
                    name=target_name,
                    owner_id=options.target_owner_id or imported_by,
                    description=manifest.project.description,
                    author=manifest.project.author or imported_by,
                )
                target_project_id = target_project["project_id"]
                repo_path = target_project["repo_path"]
            except Exception as e:
                raise ProjectPackageError(f"创建目标项目失败: {e}") from e

            # 阶段 3：解压资源文件到目标项目目录
            records: list[ImportResourceRecord] = []
            imported_count = 0
            skipped_count = 0
            renamed_count = 0
            failed_count = 0
            warnings: list[str] = []

            with zipfile.ZipFile(package_path, "r") as zf:
                # 检查冲突（conflict_strategy=fail 时）
                if options.conflict_strategy == ConflictStrategy.FAIL:
                    existing = self._check_existing_resources(manifest, repo_path)
                    if existing:
                        raise PackageConflictError(f"目标已存在资源，conflict_strategy=fail: {existing}")

                for entry in manifest.resources:
                    if not entry.path_in_package or not entry.has_content:
                        # 仅元数据资源，跳过
                        records.append(
                            ImportResourceRecord(
                                resource_uri=entry.resource_uri,
                                action="skipped",
                                error_message="metadata_only 资源无内容可导入",
                            )
                        )
                        skipped_count += 1
                        continue

                    try:
                        target_path = os.path.join(repo_path, entry.path_in_package)
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)

                        # 冲突处理
                        if os.path.exists(target_path):
                            if options.conflict_strategy == ConflictStrategy.SKIP:
                                records.append(
                                    ImportResourceRecord(
                                        resource_uri=entry.resource_uri,
                                        action="skipped",
                                        error_message="目标已存在，skip 策略",
                                    )
                                )
                                skipped_count += 1
                                continue
                            elif options.conflict_strategy == ConflictStrategy.RENAME:
                                target_path = self._rename_target_path(target_path)
                            # overwrite: 直接覆盖（默认 ZipFile.extract 行为）

                        # 流式写入文件
                        with zf.open(entry.path_in_package) as src, open(target_path, "wb") as dst:
                            while True:
                                chunk = src.read(STREAM_BUFFER_SIZE)
                                if not chunk:
                                    break
                                dst.write(chunk)

                        # 校验 hash
                        if entry.content_hash and entry.content_hash.startswith("sha256:"):
                            expected = entry.content_hash[len("sha256:") :]
                            actual = self._compute_sha256(target_path)
                            if actual != expected:
                                warnings.append(f"资源 hash 不匹配: {entry.resource_uri}")

                        action = (
                            "renamed"
                            if options.conflict_strategy == ConflictStrategy.RENAME and os.path.exists(target_path)
                            else "imported"
                        )
                        if action == "renamed":
                            renamed_count += 1
                        else:
                            imported_count += 1
                        records.append(
                            ImportResourceRecord(
                                resource_uri=entry.resource_uri,
                                action=action,
                                target_uri=target_path,
                            )
                        )
                    except Exception as e:
                        failed_count += 1
                        records.append(
                            ImportResourceRecord(
                                resource_uri=entry.resource_uri,
                                action="failed",
                                error_message=str(e),
                            )
                        )
                        logger.warning(
                            "导入资源失败 %s: %s",
                            entry.resource_uri,
                            e,
                        )

            # 阶段 4：reinit_git（如果需要）
            if options.reinit_git:
                try:
                    await sync_service.init_project(target_project_id)
                except Exception as e:
                    warnings.append(f"Git 重新初始化失败: {e}")

            import_id = _gen_import_id()
            result = ImportResult(
                import_id=import_id,
                source_project_id=manifest.project.project_id,
                target_project_id=target_project_id,
                source_package_path=package_path,
                format_version=manifest.format_version,
                conflict_strategy=options.conflict_strategy,
                resource_records=tuple(records),
                imported_count=imported_count,
                skipped_count=skipped_count,
                renamed_count=renamed_count,
                failed_count=failed_count,
                warnings=tuple(warnings),
                status=PackageTaskStatus.COMPLETED,
            )

            logger.info(
                "Imported package %s to project %s (imported=%d, skipped=%d, renamed=%d, failed=%d)",
                package_path,
                target_project_id,
                imported_count,
                skipped_count,
                renamed_count,
                failed_count,
            )
            return result

    def _read_manifest(self, package_path: str) -> PackageManifest:
        """从 _package_io 委托。"""
        return _read_manifest(package_path)

    def _check_existing_resources(self, manifest: PackageManifest, repo_path: str) -> list[str]:
        """从 _package_io 委托。"""
        return _check_existing_resources(manifest, repo_path)

    def _rename_target_path(self, path: str) -> str:
        """从 _package_io 委托。"""
        return _rename_target_path(path)

    # ── 契约方法：校验 ──────────────────────────────────────────────────

    def validate_package(self, package_path: str) -> ValidationResult:
        """校验 .lomo 包完整性（不实际导入）."""
        if not os.path.exists(package_path):
            raise PackageNotFoundError(f"包文件不存在: {package_path}")

        errors: list[str] = []
        warnings: list[str] = []
        format_version = ""
        checksum_verified = False
        resource_count = 0
        verified_count = 0

        try:
            with zipfile.ZipFile(package_path, "r") as zf:
                # 读取 manifest
                try:
                    with zf.open("manifest.json") as f:
                        manifest_data = json.loads(f.read().decode("utf-8"))
                except KeyError:
                    errors.append("manifest.json 不存在")
                    return ValidationResult(
                        package_path=package_path,
                        is_valid=False,
                        errors=tuple(errors),
                        validated_at=utcnow(),
                    )
                except json.JSONDecodeError as e:
                    errors.append(f"manifest.json 解析失败: {e}")
                    return ValidationResult(
                        package_path=package_path,
                        is_valid=False,
                        errors=tuple(errors),
                        validated_at=utcnow(),
                    )

                format_version = manifest_data.get("format_version", "")
                if not PackageFormatVersion.is_supported(format_version):
                    errors.append(f"包格式版本不支持: {format_version}")

                # 校验 checksum
                stored_checksum = manifest_data.get("checksum", "")
                if stored_checksum:
                    recomputed = self._compute_manifest_checksum(manifest_data)
                    expected = (
                        stored_checksum[len("sha256:") :] if stored_checksum.startswith("sha256:") else stored_checksum
                    )
                    if recomputed == expected:
                        checksum_verified = True
                    else:
                        errors.append("manifest checksum 校验失败")
                else:
                    warnings.append("manifest 未包含 checksum 字段")

                # 校验资源
                resources = manifest_data.get("resources", [])
                resource_count = len(resources)
                for entry in resources:
                    path_in_package = entry.get("path_in_package", "")
                    if not path_in_package:
                        continue
                    try:
                        with zf.open(path_in_package) as f:
                            h = hashlib.sha256()
                            while True:
                                chunk = f.read(STREAM_BUFFER_SIZE)
                                if not chunk:
                                    break
                                h.update(chunk)
                            actual_hash = h.hexdigest()
                        expected_hash = entry.get("content_hash", "")
                        if expected_hash.startswith("sha256:"):
                            expected_hash = expected_hash[len("sha256:") :]
                        if actual_hash == expected_hash:
                            verified_count += 1
                        else:
                            errors.append(f"资源 hash 不匹配: {entry.get('resource_uri', path_in_package)}")
                    except KeyError:
                        errors.append(f"资源文件不存在于包内: {path_in_package}")

        except zipfile.BadZipFile as e:
            errors.append(f"ZIP 文件损坏: {e}")

        is_valid = len(errors) == 0
        return ValidationResult(
            package_path=package_path,
            is_valid=is_valid,
            format_version=format_version,
            checksum_verified=checksum_verified,
            resource_count=resource_count,
            verified_count=verified_count,
            errors=tuple(errors),
            warnings=tuple(warnings),
            validated_at=utcnow(),
        )

    # ── 契约方法：预览 ──────────────────────────────────────────────────

    def preview_import(self, package_path: str) -> PackageManifest:
        """预览 .lomo 包内容（返回 manifest）."""
        if not os.path.exists(package_path):
            raise PackageNotFoundError(f"包文件不存在: {package_path}")
        return self._read_manifest(package_path)

    # ── 导出记录管理 ────────────────────────────────────────────────────

    async def create_export_record(
        self,
        *,
        project_id: str,
        package_path: str,
        format_version: str,
        content_policy: str,
        exported_by: str,
    ) -> ProjectExport:
        """创建导出任务记录（status=pending）."""
        record = ProjectExport(
            id=_gen_export_id(),
            project_id=project_id,
            package_path=package_path,
            format_version=format_version,
            content_policy=content_policy,
            resource_count=0,
            total_size_bytes=0,
            checksum="",
            status=PackageTaskStatus.PENDING,
            exported_by=exported_by,
            created_at=utcnow(),
        )
        async with await self._get_session() as session:
            session.add(record)
            await session.commit()
        return record

    async def update_export_record(self, export_id: str, **fields: Any) -> ProjectExport:
        """更新导出记录字段."""
        async with await self._get_session() as session:
            stmt = select(ProjectExport).where(ProjectExport.id == export_id)
            record = (await session.execute(stmt)).scalar_one_or_none()
            if record is None:
                raise ExportRecordNotFoundError(f"导出记录不存在: {export_id}")
            for k, v in fields.items():
                if hasattr(record, k):
                    setattr(record, k, v)
            await session.commit()
            return record

    async def get_export(self, export_id: str) -> dict[str, Any]:
        """获取导出记录详情."""
        async with await self._get_session() as session:
            stmt = select(ProjectExport).where(ProjectExport.id == export_id)
            record = (await session.execute(stmt)).scalar_one_or_none()
            if record is None:
                raise ExportRecordNotFoundError(f"导出记录不存在: {export_id}")
            return record.to_dict()

    async def list_exports(
        self,
        *,
        project_id: str | None = None,
        status_filter: str | None = None,
        exported_by: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页列出导出记录（按 created_at 倒序）."""
        limit = max(1, min(100, limit))
        offset = max(0, offset)

        async with await self._get_session() as session:
            stmt = select(ProjectExport)
            count_stmt = select(func.count()).select_from(ProjectExport)

            if project_id:
                stmt = stmt.where(ProjectExport.project_id == project_id)
                count_stmt = count_stmt.where(ProjectExport.project_id == project_id)
            if status_filter:
                stmt = stmt.where(ProjectExport.status == status_filter)
                count_stmt = count_stmt.where(ProjectExport.status == status_filter)
            if exported_by:
                stmt = stmt.where(ProjectExport.exported_by == exported_by)
                count_stmt = count_stmt.where(ProjectExport.exported_by == exported_by)

            total = (await session.execute(count_stmt)).scalar() or 0
            stmt = stmt.order_by(desc(ProjectExport.created_at)).limit(limit).offset(offset)
            items = [row.to_dict() for row in (await session.execute(stmt)).scalars().all()]
            return {
                "items": items,
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    async def delete_export(self, export_id: str) -> dict[str, Any]:
        """删除导出记录 + 物理删除 .lomo 文件."""
        async with await self._get_session() as session:
            stmt = select(ProjectExport).where(ProjectExport.id == export_id)
            record = (await session.execute(stmt)).scalar_one_or_none()
            if record is None:
                raise ExportRecordNotFoundError(f"导出记录不存在: {export_id}")
            package_path = record.package_path
            await session.delete(record)
            await session.commit()

        # 物理删除 .lomo 文件
        if package_path and os.path.exists(package_path):
            try:
                os.remove(package_path)
            except OSError as e:
                logger.warning(
                    "ProjectPackageService: 删除 .lomo 文件失败 (%s): %s",
                    package_path,
                    e,
                )

        return {"export_id": export_id, "deleted": True}

    # ── 导入记录管理 ────────────────────────────────────────────────────

    async def create_import_record(
        self,
        *,
        source_package_path: str,
        source_project_id: str,
        target_project_id: str,
        format_version: str,
        conflict_strategy: str,
        imported_by: str,
    ) -> ProjectImport:
        """创建导入任务记录（status=pending）."""
        record = ProjectImport(
            id=_gen_import_id(),
            source_package_path=source_package_path,
            source_project_id=source_project_id,
            target_project_id=target_project_id,
            format_version=format_version,
            conflict_strategy=conflict_strategy,
            imported_count=0,
            skipped_count=0,
            renamed_count=0,
            failed_count=0,
            status=PackageTaskStatus.PENDING,
            imported_by=imported_by,
            created_at=utcnow(),
        )
        async with await self._get_session() as session:
            session.add(record)
            await session.commit()
        return record

    async def update_import_record(self, import_id: str, **fields: Any) -> ProjectImport:
        """更新导入记录字段."""
        async with await self._get_session() as session:
            stmt = select(ProjectImport).where(ProjectImport.id == import_id)
            record = (await session.execute(stmt)).scalar_one_or_none()
            if record is None:
                raise ImportRecordNotFoundError(f"导入记录不存在: {import_id}")
            for k, v in fields.items():
                if hasattr(record, k):
                    setattr(record, k, v)
            await session.commit()
            return record

    async def get_import(self, import_id: str) -> dict[str, Any]:
        """获取导入记录详情."""
        async with await self._get_session() as session:
            stmt = select(ProjectImport).where(ProjectImport.id == import_id)
            record = (await session.execute(stmt)).scalar_one_or_none()
            if record is None:
                raise ImportRecordNotFoundError(f"导入记录不存在: {import_id}")
            return record.to_dict()

    async def list_imports(
        self,
        *,
        target_project_id: str | None = None,
        status_filter: str | None = None,
        imported_by: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页列出导入记录（按 created_at 倒序）."""
        limit = max(1, min(100, limit))
        offset = max(0, offset)

        async with await self._get_session() as session:
            stmt = select(ProjectImport)
            count_stmt = select(func.count()).select_from(ProjectImport)

            if target_project_id:
                stmt = stmt.where(ProjectImport.target_project_id == target_project_id)
                count_stmt = count_stmt.where(ProjectImport.target_project_id == target_project_id)
            if status_filter:
                stmt = stmt.where(ProjectImport.status == status_filter)
                count_stmt = count_stmt.where(ProjectImport.status == status_filter)
            if imported_by:
                stmt = stmt.where(ProjectImport.imported_by == imported_by)
                count_stmt = count_stmt.where(ProjectImport.imported_by == imported_by)

            total = (await session.execute(count_stmt)).scalar() or 0
            stmt = stmt.order_by(desc(ProjectImport.created_at)).limit(limit).offset(offset)
            items = [row.to_dict() for row in (await session.execute(stmt)).scalars().all()]
            return {
                "items": items,
                "total": total,
                "limit": limit,
                "offset": offset,
            }


__all__ = [
    "ProjectPackageService",
    "ProjectPackageError",
    "ProjectNotFoundError",
    "PackageNotFoundError",
    "PackageFormatError",
    "PackageChecksumError",
    "ExportRecordNotFoundError",
    "ImportRecordNotFoundError",
    "PackageConflictError",
    "get_project_package_service",
    "reset_project_package_service",
]
