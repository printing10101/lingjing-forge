"""插件系统模块（re-export shim）。

本模块原为 846 行的 God class，已按职责拆分为：
- :mod:`app.plugins.plugin_types` — 枚举（PluginStatus、PluginType）+ 能力常量
- :mod:`app.plugins.plugin_metadata` — dataclass（PluginMetadata、PluginDependency）
- :mod:`app.plugins.plugin_manager` — 注册表/发现器/加载器/生命周期/依赖解析/Holder

为保持向后兼容，所有原公开符号仍可从 ``app.plugins.plugin_system`` 路径导入。
新代码应直接从拆分后的子模块导入。
"""

from __future__ import annotations

from app.dependencies import get_plugin_manager

from app.plugins.plugin_metadata import (
    PluginDependency,
    PluginMetadata,
)
from app.plugins.plugin_manager import (
    DependencyResolver,
    PluginDiscovery,
    PluginLifecycleManager,
    PluginLoader,
    PluginRegistry,
    get_dependency_resolver,
    init_plugin_system,
    shutdown_plugin_system,
)
from app.plugins.plugin_types import (
    CAPABILITY_DATA_SOURCE,
    CAPABILITY_FILE_ACCESS,
    CAPABILITY_GPU_ACCESS,
    CAPABILITY_MACHINE_CONTROL,
    CAPABILITY_NETWORK_ACCESS,
    PluginStatus,
    PluginType,
    VALID_CAPABILITIES,
)

__all__ = [
    # 类型与常量
    "PluginStatus",
    "PluginType",
    "CAPABILITY_DATA_SOURCE",
    "CAPABILITY_MACHINE_CONTROL",
    "CAPABILITY_FILE_ACCESS",
    "CAPABILITY_NETWORK_ACCESS",
    "CAPABILITY_GPU_ACCESS",
    "VALID_CAPABILITIES",
    # 元数据
    "PluginDependency",
    "PluginMetadata",
    # 管理器
    "PluginRegistry",
    "PluginDiscovery",
    "PluginLoader",
    "PluginLifecycleManager",
    "DependencyResolver",
    # 系统级 holder 与函数
    "init_plugin_system",
    "get_plugin_manager",
    "get_dependency_resolver",
    "shutdown_plugin_system",
]
