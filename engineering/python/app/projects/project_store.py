"""工程文件存储引擎。

基于ZIP压缩的结构化打包格式（.ljm），管理加工工程的全部数据。
工程文件内部结构：
    project.json          — 核心描述文件（版本号、元数据、资源清单）
    resources/            — 资源文件目录
        drawings/         — 图纸文件 (*.dxf, *.dwg, *.step)
        models/           — 3D模型文件 (*.stl, *.obj)
        toolpaths/        — 刀路数据 (*.nc, *.gcode)
        simulation/       — 仿真结果文件
        postprocessors/   — 后处理器配置
        extensions/       — 扩展数据（LNN模型、自定义插件等）

project.json 核心Schema:
{
    "version": "1.0",
    "metadata": {
        "name": "string",
        "created_at": "ISO8601",
        "modified_at": "ISO8601",
        "author": "string",
        "description": "string"
    },
    "resources": [
        {"id": "uuid", "type": "drawing|model|toolpath|simulation|postprocessor|extension",
         "path": "相对路径", "original_name": "原始文件名", "mime_type": "string",
         "added_at": "ISO8601", "metadata": {}}
    ],
    "data": {
        "stock_definition": {},
        "tool_selection": [],
        "process_steps": [],
        "toolpath_config": {},
        "postprocessor_config": {},
        "simulation_config": {}
    },
    "extensions": {}
}
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_FORMAT_VERSION = "1.0"
PROJECT_FILE_EXTENSION = ".vrm"

_RESOURCE_TYPES = (
    "drawing",
    "model",
    "toolpath",
    "simulation",
    "postprocessor",
    "extension",
)

_PROJECT_JSON_FILENAME = "project.json"
_RESOURCES_DIR = "resources"


@dataclass
class ProjectMetadata:
    """工程元数据。"""

    name: str = "未命名工程"
    created_at: str = ""
    modified_at: str = ""
    author: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.modified_at:
            self.modified_at = now

    def touch(self) -> None:
        self.modified_at = datetime.now(timezone.utc).isoformat()


@dataclass
class ResourceEntry:
    """资源文件条目。

    记录工程中包含的每个资源文件的信息。
    """

    id: str = ""
    type: str = ""
    path: str = ""
    original_name: str = ""
    mime_type: str = ""
    added_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.added_at:
            self.added_at = datetime.now(timezone.utc).isoformat()

    def validate(self) -> bool:
        if self.type not in _RESOURCE_TYPES:
            return False
        if not self.path:
            return False
        return True


@dataclass
class ProjectManifest:
    """工程清单——project.json 的完整数据结构。

    这是工程文件的核心描述，定义版本号、元数据、资源清单、
    加工数据和扩展字段。

    Attributes:
        version: 格式版本号（"1.0"）,用于版本控制和兼容性处理
        metadata: 工程元数据（名称、时间戳、作者等）
        resources: 资源文件清单，列出所有关联的外部资源
        data: 工程加工数据（毛坯/刀具/工艺/刀路/后处理/仿真配置）
        extensions: 预留扩展字段，支持未来数据类型无缝集成
    """

    version: str = PROJECT_FORMAT_VERSION
    metadata: ProjectMetadata | None = None
    resources: list[ResourceEntry] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "metadata": asdict(self.metadata) if self.metadata else asdict(ProjectMetadata()),
            "resources": [self._resource_to_dict(r) for r in self.resources],
            "data": self.data,
            "extensions": self.extensions,
        }

    @staticmethod
    def _resource_to_dict(r: ResourceEntry) -> dict[str, Any]:
        return {
            "id": r.id,
            "type": r.type,
            "path": r.path,
            "original_name": r.original_name,
            "mime_type": r.mime_type,
            "added_at": r.added_at,
            "metadata": r.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProjectManifest:
        meta_raw = d.get("metadata", {})
        metadata = ProjectMetadata(
            name=meta_raw.get("name", "未命名工程"),
            created_at=meta_raw.get("created_at", ""),
            modified_at=meta_raw.get("modified_at", ""),
            author=meta_raw.get("author", ""),
            description=meta_raw.get("description", ""),
        )
        resources = []
        for r in d.get("resources", []):
            resources.append(
                ResourceEntry(
                    id=r.get("id", ""),
                    type=r.get("type", ""),
                    path=r.get("path", ""),
                    original_name=r.get("original_name", ""),
                    mime_type=r.get("mime_type", ""),
                    added_at=r.get("added_at", ""),
                    metadata=r.get("metadata", {}),
                )
            )
        return cls(
            version=d.get("version", PROJECT_FORMAT_VERSION),
            metadata=metadata,
            resources=resources,
            data=d.get("data", {}),
            extensions=d.get("extensions", {}),
        )

    def add_resource(
        self,
        resource_type: str,
        relative_path: str,
        original_name: str = "",
        mime_type: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ResourceEntry:
        entry = ResourceEntry(
            type=resource_type,
            path=relative_path,
            original_name=original_name,
            mime_type=mime_type,
            metadata=metadata or {},
        )
        self.resources.append(entry)
        return entry

    def find_resource(self, resource_id: str) -> ResourceEntry | None:
        for r in self.resources:
            if r.id == resource_id:
                return r
        return None

    def remove_resource(self, resource_id: str) -> bool:
        for i, r in enumerate(self.resources):
            if r.id == resource_id:
                self.resources.pop(i)
                return True
        return False

    def get_resources_by_type(self, resource_type: str) -> list[ResourceEntry]:
        return [r for r in self.resources if r.type == resource_type]


class ProjectStore:
    """工程文件存储引擎。

    管理 .ljm 工程文件的创建、打开、保存和另存为操作。
    内部使用ZIP压缩格式组织数据。

    Usage:
        store = ProjectStore()
        manifest = store.create_project("铣削加工-001", author="张三")
        store.add_resource_file(manifest, "model", Path("stock.stl"))
        store.save_project(manifest, output_path)
        loaded = store.open_project(output_path)
    """

    def __init__(self, workspace_dir: str | Path = "") -> None:
        workspace_dir = (
            str(workspace_dir)
            if workspace_dir
            else os.path.join(os.path.dirname(__file__), "..", "..", "output", "projects")
        )
        self._workspace_dir = Path(workspace_dir)
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

    def create_project(
        self,
        name: str = "未命名工程",
        author: str = "",
        description: str = "",
    ) -> ProjectManifest:
        """创建新工程，返回初始化的工程清单。

        Args:
            name: 工程名称
            author: 作者
            description: 工程描述

        Returns:
            初始化的ProjectManifest
        """
        manifest = ProjectManifest(
            metadata=ProjectMetadata(
                name=name,
                author=author,
                description=description,
            ),
            data={
                "stock_definition": {},
                "tool_selection": [],
                "process_steps": [],
                "toolpath_config": {},
                "postprocessor_config": {},
                "simulation_config": {},
            },
        )
        return manifest

    def open_project(self, file_path: str | Path) -> ProjectManifest:
        """打开 .ljm 工程文件，解析并返回工程清单。

        Args:
            file_path: .ljm 文件路径

        Returns:
            解析后的ProjectManifest

        Raises:
            ValueError: 文件格式无效或版本不兼容
            FileNotFoundError: 文件不存在
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"工程文件不存在: {file_path}")
        if file_path.suffix.lower() != PROJECT_FILE_EXTENSION:
            raise ValueError(f"不支持的文件格式: {file_path.suffix}，仅支持 {PROJECT_FILE_EXTENSION}")

        with zipfile.ZipFile(str(file_path), "r") as zf:
            names = zf.namelist()
            if _PROJECT_JSON_FILENAME not in names:
                raise ValueError(f"工程文件损坏: 缺少 {_PROJECT_JSON_FILENAME}")

            manifest_json = zf.read(_PROJECT_JSON_FILENAME).decode("utf-8")
            try:
                data = json.loads(manifest_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"project.json 解析失败: {e}")

        self._validate_version(data.get("version", ""))
        manifest = ProjectManifest.from_dict(data)

        # from_dict 总是构造 metadata（默认"未命名工程"）；断言收窄 Optional
        assert manifest.metadata is not None
        manifest.metadata.touch()
        return manifest

    def save_project(
        self,
        manifest: ProjectManifest,
        output_path: str | Path,
        resource_files: dict[str, str | Path] | None = None,
    ) -> str:
        """保存工程为 .ljm 文件。

        将工程清单序列化为 project.json，与提供的资源文件一起打包成ZIP。

        Args:
            manifest: 工程清单
            output_path: 输出文件路径
            resource_files: {资源相对路径: 源文件绝对路径} 的映射

        Returns:
            输出文件的完整路径

        Raises:
            ValueError: 清单数据无效
            OSError: 写入失败
        """
        if manifest.metadata is None:
            manifest.metadata = ProjectMetadata()
        manifest.metadata.touch()

        manifest_dict = manifest.to_dict()
        manifest_json = json.dumps(manifest_dict, indent=2, ensure_ascii=False)

        output_path = Path(output_path)
        if output_path.suffix.lower() != PROJECT_FILE_EXTENSION:
            output_path = output_path.with_suffix(PROJECT_FILE_EXTENSION)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_base = Path(tmp_dir)

            project_json_path = tmp_base / _PROJECT_JSON_FILENAME
            project_json_path.write_text(manifest_json, encoding="utf-8")

            resources_dir = tmp_base / _RESOURCES_DIR
            if resource_files:
                for rel_path, src_path in resource_files.items():
                    dest = resources_dir / rel_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src_path), str(dest))

            with zipfile.ZipFile(str(output_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(str(project_json_path), _PROJECT_JSON_FILENAME)
                if resource_files:
                    for rel_path in resource_files:
                        full = resources_dir / rel_path
                        if full.exists():
                            zf.write(
                                str(full),
                                f"{_RESOURCES_DIR}/{rel_path}".replace("\\", "/"),
                            )

        return str(output_path.resolve())

    def save_as_project(
        self,
        manifest: ProjectManifest,
        output_path: str | Path,
        resource_files: dict[str, str | Path] | None = None,
    ) -> str:
        """另存为工程文件——创建新的工程文件副本。

        与 save_project 行为相同，但强制要求新的输出路径。

        Args:
            manifest: 工程清单
            output_path: 新输出文件路径
            resource_files: {资源相对路径: 源文件绝对路径} 的映射

        Returns:
            新文件的完整路径
        """
        assert manifest.metadata is not None  # 同 load_project：metadata 总是存在
        manifest.metadata.touch()
        return self.save_project(manifest, output_path, resource_files)

    def add_resource_file(
        self,
        manifest: ProjectManifest,
        resource_type: str,
        source_path: str | Path,
        metadata: dict[str, Any] | None = None,
    ) -> ResourceEntry:
        """向工程清单添加资源文件记录。

        Args:
            manifest: 工程清单
            resource_type: 资源类型（drawing/model/toolpath/simulation/postprocessor/extension）
            source_path: 源文件路径
            metadata: 附加元数据

        Returns:
            新建的ResourceEntry
        """
        if resource_type not in _RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}，有效类型: {_RESOURCE_TYPES}")

        source_path = Path(source_path)
        sub_dir = f"{resource_type}s"
        dest_rel = f"{sub_dir}/{source_path.name}"

        ext = source_path.suffix.lower()
        mime_map = {
            ".stl": "application/sla",
            ".step": "application/step",
            ".stp": "application/step",
            ".obj": "application/wavefront-obj",
            ".dxf": "application/dxf",
            ".dwg": "application/acad",
            ".nc": "text/x-gcode",
            ".gcode": "text/x-gcode",
            ".json": "application/json",
        }

        return manifest.add_resource(
            resource_type=resource_type,
            relative_path=dest_rel,
            original_name=source_path.name,
            mime_type=mime_map.get(ext, "application/octet-stream"),
            metadata=metadata,
        )

    def extract_resource(
        self,
        file_path: str | Path,
        resource_path: str,
        output_dir: str | Path,
    ) -> str:
        """从 .ljm 文件中提取指定资源文件到本地目录。

        Args:
            file_path: .ljm 工程文件路径
            resource_path: 资源在ZIP中的相对路径
            output_dir: 输出目录

        Returns:
            提取后的文件路径
        """
        file_path = Path(file_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        zip_resource_path = f"{_RESOURCES_DIR}/{resource_path}".replace("\\", "/")

        with zipfile.ZipFile(str(file_path), "r") as zf:
            if zip_resource_path not in zf.namelist():
                zip_resource_path_alt = resource_path.replace("\\", "/")
                if zip_resource_path_alt not in zf.namelist():
                    raise FileNotFoundError(f"资源不存在于工程文件中: {resource_path}")
                zip_resource_path = zip_resource_path_alt

            dest = output_dir / resource_path.replace("/", os.sep).split(os.sep)[-1]
            src_info = zf.getinfo(zip_resource_path)
            src_info.filename = dest.name
            zf.extract(src_info, str(dest.parent))
            return str(dest)

    def _validate_version(self, version: str) -> None:
        """验证工程文件版本兼容性。

        当前支持版本: "1.0"
        版本号比较规则: 主版本号必须匹配，次版本号较新可兼容。

        Args:
            version: 文件中的版本字符串

        Raises:
            ValueError: 版本不兼容
        """
        if not version:
            raise ValueError("工程文件中缺少 version 字段")

        try:
            parts = version.split(".")
            current_parts = PROJECT_FORMAT_VERSION.split(".")
            file_major = int(parts[0])
            current_major = int(current_parts[0])

            if file_major > current_major:
                raise ValueError(
                    f"工程文件版本 ({version}) 高于当前软件支持的版本 "
                    f"({PROJECT_FORMAT_VERSION})，请升级软件后打开此工程文件"
                )
        except (ValueError, IndexError) as e:
            if "高于" in str(e) or "升级" in str(e):
                raise
            raise ValueError(f"无效的版本号格式: '{version}'。期望格式: 主版本号.次版本号（如 '1.0'）")

    def list_projects(self) -> list[dict[str, Any]]:
        """列出工作目录下的所有工程文件摘要信息。

        Returns:
            工程文件摘要列表
        """
        projects: list[dict[str, Any]] = []
        for f in self._workspace_dir.glob(f"*{PROJECT_FILE_EXTENSION}"):
            try:
                manifest = self.open_project(f)
                projects.append(
                    {
                        "path": str(f),
                        "name": manifest.metadata.name if manifest.metadata else "",
                        "created_at": manifest.metadata.created_at if manifest.metadata else "",
                        "modified_at": manifest.metadata.modified_at if manifest.metadata else "",
                        "resource_count": len(manifest.resources),
                        "file_size": f.stat().st_size,
                    }
                )
            except (OSError, ValueError, TypeError, KeyError, RuntimeError):
                projects.append(
                    {
                        "path": str(f),
                        "name": f.stem,
                        "error": "无法解析工程文件",
                        "file_size": f.stat().st_size if f.exists() else 0,
                    }
                )
        projects.sort(key=lambda p: p.get("modified_at", ""), reverse=True)
        return projects

    def delete_project(self, file_path: str | Path) -> bool:
        file_path = Path(file_path)
        if file_path.exists() and file_path.suffix == PROJECT_FILE_EXTENSION:
            file_path.unlink()
            return True
        return False
