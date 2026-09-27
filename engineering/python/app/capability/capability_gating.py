from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CapabilityLevel(str, Enum):
    NONE = "none"
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    FULL_CONTROL = "full_control"


@dataclass
class FileAccessRule:
    path_pattern: str
    level: CapabilityLevel = CapabilityLevel.READ_ONLY

    def matches(self, path: str) -> bool:
        import fnmatch

        return fnmatch.fnmatch(path, self.path_pattern)


@dataclass
class NetworkAccessRule:
    host_pattern: str
    port_range: tuple | None = None
    protocol: str = "*"

    def matches(self, host: str, port: int = 0) -> bool:
        import fnmatch

        host_match = fnmatch.fnmatch(host, self.host_pattern)

        if self.port_range is None:
            return host_match

        return host_match and (self.port_range[0] <= port <= self.port_range[1])


@dataclass
class GpuResourceLimit:
    max_memory_mb: float = 1024.0
    max_utilization_percent: float = 50.0
    allowed_devices: list[int] = field(default_factory=lambda: [0])


@dataclass
class CapabilityGrant:
    capability: str
    level: CapabilityLevel = CapabilityLevel.READ_ONLY
    file_rules: list[FileAccessRule] = field(default_factory=list)
    network_rules: list[NetworkAccessRule] = field(default_factory=list)
    gpu_limits: GpuResourceLimit | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CapabilityGatekeeper:
    _instance: "CapabilityGatekeeper" | None = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._grants: dict[str, dict[str, CapabilityGrant]] = {}
        self._default_grants: dict[str, CapabilityGrant] = self._create_default_grants()
        # 安全修复：保护 _grants 字典的并发读写
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "CapabilityGatekeeper":
        # 安全修复：双重检查锁，防止并发创建多个实例
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._instance_lock:
            cls._instance = None

    def _create_default_grants(self) -> dict[str, CapabilityGrant]:
        return {
            "data_source": CapabilityGrant(
                capability="data_source",
                level=CapabilityLevel.READ_ONLY,
                network_rules=[NetworkAccessRule(host_pattern="localhost", port_range=(1, 65535))],
            ),
            "machine_control": CapabilityGrant(
                capability="machine_control",
                level=CapabilityLevel.READ_WRITE,
                network_rules=[NetworkAccessRule(host_pattern="localhost", port_range=(1, 65535))],
            ),
            "file_access": CapabilityGrant(
                capability="file_access",
                level=CapabilityLevel.READ_ONLY,
                file_rules=[FileAccessRule(path_pattern="*.txt", level=CapabilityLevel.READ_ONLY)],
            ),
            "network_access": CapabilityGrant(
                capability="network_access",
                level=CapabilityLevel.READ_ONLY,
                network_rules=[NetworkAccessRule(host_pattern="*")],
            ),
            "gpu_access": CapabilityGrant(
                capability="gpu_access",
                level=CapabilityLevel.READ_ONLY,
                gpu_limits=GpuResourceLimit(
                    max_memory_mb=512.0,
                    max_utilization_percent=30.0,
                ),
            ),
        }

    def grant_capabilities(self, plugin_id: str, capabilities: list[str]) -> list[CapabilityGrant]:
        # 安全修复：保护 _grants 字典的并发读写
        with self._lock:
            if plugin_id not in self._grants:
                self._grants[plugin_id] = {}

            granted = []
            for cap in capabilities:
                if cap in self._default_grants:
                    grant = self._default_grants[cap]
                    self._grants[plugin_id][cap] = grant
                    granted.append(grant)
                else:
                    logger.warning("Unknown capability '%s' for plugin '%s'", cap, plugin_id)

        logger.info("Granted %s capabilities to plugin '%s'", len(granted), plugin_id)
        return granted

    def revoke_capabilities(self, plugin_id: str, capabilities: list[str]) -> None:
        # 安全修复：保护 _grants 字典的并发读写
        with self._lock:
            if plugin_id in self._grants:
                for cap in capabilities:
                    self._grants[plugin_id].pop(cap, None)

                if not self._grants[plugin_id]:
                    self._grants.pop(plugin_id, None)

        logger.info("Revoked capabilities from plugin '%s'", plugin_id)

    def has_capability(self, plugin_id: str, capability: str) -> bool:
        # 安全修复：保护 _grants 字典的并发读
        with self._lock:
            return plugin_id in self._grants and capability in self._grants[plugin_id]

    def get_grant(self, plugin_id: str, capability: str) -> CapabilityGrant | None:
        # 安全修复：保护 _grants 字典的并发读
        with self._lock:
            if plugin_id in self._grants:
                return self._grants[plugin_id].get(capability)
        return None

    def check_file_access(self, plugin_id: str, path: str, operation: str = "read") -> bool:
        grant = self.get_grant(plugin_id, "file_access")
        if grant is None:
            return False

        for rule in grant.file_rules:
            if rule.matches(path):
                if operation == "read" and grant.level in (
                    CapabilityLevel.READ_ONLY,
                    CapabilityLevel.READ_WRITE,
                    CapabilityLevel.FULL_CONTROL,
                ):
                    return True
                if operation == "write" and grant.level in (
                    CapabilityLevel.READ_WRITE,
                    CapabilityLevel.FULL_CONTROL,
                ):
                    return True

        return False

    def check_network_access(self, plugin_id: str, host: str, port: int = 0) -> bool:
        grant = self.get_grant(plugin_id, "network_access")
        if grant is None:
            grant = self.get_grant(plugin_id, "data_source")
            if grant is None:
                return False

        for rule in grant.network_rules:
            if rule.matches(host, port):
                return True

        return False

    def check_gpu_access(self, plugin_id: str) -> GpuResourceLimit | None:
        grant = self.get_grant(plugin_id, "gpu_access")
        if grant is None:
            return None

        return grant.gpu_limits

    def get_plugin_capabilities(self, plugin_id: str) -> list[str]:
        # 安全修复：保护 _grants 字典的并发读
        with self._lock:
            if plugin_id in self._grants:
                return list(self._grants[plugin_id].keys())
        return []

    def get_all_grants(self) -> dict[str, list[dict[str, Any]]]:
        # 安全修复：保护 _grants 字典的并发读，构建快照避免迭代时被修改
        with self._lock:
            result: dict[str, list[dict[str, Any]]] = {}
            for plugin_id, caps in self._grants.items():
                result[plugin_id] = []
                for cap_name, grant in caps.items():
                    result[plugin_id].append(
                        {
                            "capability": cap_name,
                            "level": grant.level.value,
                            "file_rules": [
                                {"pattern": r.path_pattern, "level": r.level.value} for r in grant.file_rules
                            ],
                            "network_rules": [
                                {"host": r.host_pattern, "port_range": r.port_range} for r in grant.network_rules
                            ],
                            "gpu_limits": {
                                "max_memory_mb": grant.gpu_limits.max_memory_mb,
                                "max_utilization_percent": grant.gpu_limits.max_utilization_percent,
                            }
                            if grant.gpu_limits
                            else None,
                        }
                    )
        return result

    def update_grant_rules(
        self,
        plugin_id: str,
        capability: str,
        file_rules: list[dict] | None = None,
        network_rules: list[dict] | None = None,
        gpu_limits: dict | None = None,
    ) -> None:
        # 安全修复：保护 _grants 字典的并发读写
        with self._lock:
            grant = self._grants.get(plugin_id, {}).get(capability)
            if grant is None:
                raise ValueError(f"No grant found for plugin '{plugin_id}' capability '{capability}'")

            if file_rules is not None:
                grant.file_rules = [
                    FileAccessRule(
                        path_pattern=r.get("path_pattern", "*"),
                        level=CapabilityLevel(r.get("level", "read_only")),
                    )
                    for r in file_rules
                ]

            if network_rules is not None:
                grant.network_rules = [
                    NetworkAccessRule(
                        host_pattern=r.get("host_pattern", "*"),
                        port_range=tuple(r.get("port_range", (1, 65535))) if r.get("port_range") else None,
                    )
                    for r in network_rules
                ]

            if gpu_limits is not None:
                grant.gpu_limits = GpuResourceLimit(
                    max_memory_mb=gpu_limits.get("max_memory_mb", 1024.0),
                    max_utilization_percent=gpu_limits.get("max_utilization_percent", 50.0),
                )

        logger.info(f"Updated grant rules for plugin '{plugin_id}' capability '{capability}'")


def get_capability_gatekeeper() -> CapabilityGatekeeper:
    return CapabilityGatekeeper.get_instance()
