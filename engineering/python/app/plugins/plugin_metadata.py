"""插件元数据 dataclass。

从原 ``plugin_system.py`` 拆分而来，包含 :class:`PluginDependency` 与
:class:`PluginMetadata` 两个数据类。依赖 :mod:`app.plugins.plugin_types`
中的 :class:`PluginStatus`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.plugins.plugin_types import PluginStatus


@dataclass
class PluginDependency:
    name: str
    version: str = ">=0.0.0"
    required: bool = True


@dataclass
class PluginMetadata:
    id: str
    name: str
    version: str
    author: str = ""
    description: str = ""
    entry_point: str = "main.py"
    plugin_type: str = ""
    capabilities: list[str] = field(default_factory=list)
    dependencies: list[PluginDependency] = field(default_factory=list)
    config_schema: dict[str, Any] = field(default_factory=dict)
    min_core_version: str = "1.0.0"
    max_core_version: str = "99.99.99"
    plugin_path: str = ""
    status: PluginStatus = PluginStatus.DISCOVERED
    config: dict[str, Any] = field(default_factory=dict)
    enabled_at: float | None = None
    disabled_at: float | None = None
    installed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "entry_point": self.entry_point,
            "plugin_type": self.plugin_type,
            "capabilities": self.capabilities,
            "dependencies": [{"name": d.name, "version": d.version, "required": d.required} for d in self.dependencies],
            "config_schema": self.config_schema,
            "min_core_version": self.min_core_version,
            "max_core_version": self.max_core_version,
            "plugin_path": self.plugin_path,
            "status": self.status.value,
            "config": self.config,
            "enabled_at": self.enabled_at,
            "disabled_at": self.disabled_at,
            "installed_at": self.installed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginMetadata":
        deps = []
        for d in data.get("dependencies", []):
            deps.append(
                PluginDependency(
                    name=d["name"],
                    version=d.get("version", ">=0.0.0"),
                    required=d.get("required", True),
                )
            )

        compat = data.get("compatibility", {})
        return cls(
            id=data["id"],
            name=data["name"],
            version=data["version"],
            author=data.get("author", ""),
            description=data.get("description", ""),
            entry_point=data.get("entry_point", "main.py"),
            plugin_type=data.get("plugin_type", ""),
            capabilities=data.get("capabilities", []),
            dependencies=deps,
            config_schema=data.get("config_schema", {}),
            min_core_version=compat.get("min_core_version", "1.0.0"),
            max_core_version=compat.get("max_core_version", "99.99.99"),
            plugin_path=data.get("plugin_path", ""),
            status=PluginStatus(data.get("status", "discovered")),
            config=data.get("config", {}),
        )


__all__ = ["PluginDependency", "PluginMetadata"]
