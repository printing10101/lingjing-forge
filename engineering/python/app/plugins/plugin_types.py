"""插件类型与能力常量。

从原 ``plugin_system.py`` 拆分而来，提供插件状态枚举、类型枚举以及
能力标识常量。本模块不依赖其他插件模块，可被 ``plugin_metadata`` /
``plugin_manager`` 等模块安全导入。
"""

from __future__ import annotations

from enum import Enum


class PluginStatus(str, Enum):
    DISCOVERED = "discovered"
    REGISTERED = "registered"
    INITIALIZED = "initialized"
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNINSTALLED = "uninstalled"
    ERROR = "error"


class PluginType(str, Enum):
    ADAPTER = "adapter"
    DATA_SOURCE = "data_source"
    ANALYZER = "analyzer"
    VISUALIZATION = "visualization"


CAPABILITY_DATA_SOURCE = "data_source"
CAPABILITY_MACHINE_CONTROL = "machine_control"
CAPABILITY_FILE_ACCESS = "file_access"
CAPABILITY_NETWORK_ACCESS = "network_access"
CAPABILITY_GPU_ACCESS = "gpu_access"

VALID_CAPABILITIES = {
    CAPABILITY_DATA_SOURCE,
    CAPABILITY_MACHINE_CONTROL,
    CAPABILITY_FILE_ACCESS,
    CAPABILITY_NETWORK_ACCESS,
    CAPABILITY_GPU_ACCESS,
}


__all__ = [
    "PluginStatus",
    "PluginType",
    "CAPABILITY_DATA_SOURCE",
    "CAPABILITY_MACHINE_CONTROL",
    "CAPABILITY_FILE_ACCESS",
    "CAPABILITY_NETWORK_ACCESS",
    "CAPABILITY_GPU_ACCESS",
    "VALID_CAPABILITIES",
]
