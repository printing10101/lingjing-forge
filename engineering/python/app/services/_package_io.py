"""项目包 I/O 纯辅助函数（从 project_package_service 拆分，D5）。

模块级函数，无 self 依赖；原服务方法改为薄包装调用。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import zipfile
from typing import Any

from app.config import config
from app.contracts.project_package import (
    ExportOptions,
    PackageManifest,
    SourceMachineInfo,
    PACKAGE_FILE_EXTENSION,
    PACKAGE_FILENAME_TEMPLATE,
    SOURCE_MACHINE_INFO_DEFAULTS,
    STREAM_BUFFER_SIZE,
)
from app.utils.time import utcnow_filename_suffix


class PackageFormatError(ValueError):
    """包格式不合法（manifest 解析失败 / 版本不兼容）。

    F821 修复：D5 拆分时异常类滞留在 project_package_service，本模块
    引用却从未导入；现定义于此，project_package_service 处改为 re-export。
    """


def _resolve_output_path(output_dir: str, options: ExportOptions) -> str:
    """解析导出包输出路径.

    Args:
        output_dir: 输出目录
        options: 导出选项（含 output_filename）

    Returns:
        .lomo 文件绝对路径
    """
    os.makedirs(output_dir, exist_ok=True)
    if options.output_filename:
        filename = options.output_filename
        if not filename.endswith(PACKAGE_FILE_EXTENSION):
            filename += PACKAGE_FILE_EXTENSION
    else:
        timestamp = utcnow_filename_suffix()
        filename = PACKAGE_FILENAME_TEMPLATE.format(name="project", timestamp=timestamp)
    return os.path.abspath(os.path.join(output_dir, filename))


def _resolve_resource_path(ref: dict[str, Any]) -> str | None:
    """从资源引用 metadata 解析实际文件路径.

    Args:
        ref: ProjectResourceRef.to_dict() 结果

    Returns:
        文件绝对路径，若无法解析返回 None
    """
    metadata = ref.get("metadata") or {}
    # 优先级：path > storage_uri
    path = metadata.get("path") or metadata.get("storage_uri")
    if not path:
        return None
    # 处理 file:// URI
    if path.startswith("file://"):
        path = path[len("file://") :]
    # 相对路径基于 output_dir 解析
    if not os.path.isabs(path):
        path = os.path.join(os.path.abspath(config.storage.output_dir), path)
    return path if os.path.exists(path) else None


def _compute_sha256(file_path: str) -> str:
    """计算文件 sha256（流式读取，64KB 缓冲）."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(STREAM_BUFFER_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _compute_manifest_checksum(manifest_dict: dict[str, Any]) -> str:
    """计算 manifest.json 的 sha256 校验和（排除 checksum 字段本身）.

    Args:
        manifest_dict: manifest 字典（含或不含 checksum 字段）

    Returns:
        sha256 hex 字符串
    """
    # 移除 checksum 字段后序列化
    data = {k: v for k, v in manifest_dict.items() if k != "checksum"}
    content = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_source_machine_info() -> SourceMachineInfo:
    """获取当前机器信息."""
    return SourceMachineInfo(
        hostname=socket.gethostname() or SOURCE_MACHINE_INFO_DEFAULTS["hostname"],
        app_version=config.app_version if hasattr(config, "app_version") else "4.0.0",
        platform=platform.system().lower() or SOURCE_MACHINE_INFO_DEFAULTS["platform"],
    )


def _build_package_path(resource_type: str, resource_uri: str, ext: str) -> str:
    """构造包内路径（基于 resource_type + URI）."""
    # URI 格式：<scheme>://<path>
    path_part = resource_uri.split("://", 1)[1] if "://" in resource_uri else resource_uri
    # 替换非法字符
    safe_path = path_part.replace("/", "_").replace(":", "_")
    return f"{resource_type}s/{safe_path}{ext}"


def _read_manifest(package_path: str) -> PackageManifest:
    """从 .lomo 包读取 manifest.json."""
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            with zf.open("manifest.json") as f:
                data = json.loads(f.read().decode("utf-8"))
        return PackageManifest.from_dict(data)
    except (KeyError, json.JSONDecodeError) as e:
        raise PackageFormatError(f"manifest.json 解析失败: {e}") from e
    except zipfile.BadZipFile as e:
        raise PackageFormatError(f"ZIP 文件损坏: {e}") from e


def _check_existing_resources(manifest: PackageManifest, repo_path: str) -> list[str]:
    """检查目标目录已存在的资源 URI（用于 conflict_strategy=fail）."""
    existing = []
    for entry in manifest.resources:
        if not entry.path_in_package:
            continue
        target_path = os.path.join(repo_path, entry.path_in_package)
        if os.path.exists(target_path):
            existing.append(entry.resource_uri)
    return existing


def _rename_target_path(path: str) -> str:
    """生成重命名后的目标路径（追加 _imported_<timestamp> 后缀）."""
    timestamp = utcnow_filename_suffix()
    root, ext = os.path.splitext(path)
    return f"{root}_imported_{timestamp}{ext}"
