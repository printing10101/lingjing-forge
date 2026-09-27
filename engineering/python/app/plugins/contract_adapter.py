"""插件契约适配器：legacy PluginLifecycleManager → IPlugin / IExtensionRegistry 契约.

对应 ADR-005 第 5 章 + core-contracts-design.md 阶段 3 p3-1。

本模块不重写 legacy 插件系统，而是通过适配器模式把
``app/plugins/plugin_system.py`` 的 PluginMetadata / PluginLifecycleManager /
legacy 插件实例包装为 ``app/contracts/plugin.py`` 定义的契约接口：

    ┌──────────────────────────────────────────────────────────────┐
    │  契约层（app/contracts/plugin.py）                            │
    │  PluginManifest / IPlugin / PluginContext / Capability        │
    │       ▲                                                       │
    │       │ 适配（本文件）                                         │
    │       │                                                       │
    │  ┌────┴──────────────────────────────────────────────────┐  │
    │  │ LegacyPluginMetadataAdapter   → PluginManifest         │  │
    │  │ LegacyPluginInstanceAdapter   → IPlugin                │  │
    │  │ PluginLifecycleManagerAdapter → 加载/卸载/启停入口      │  │
    │  │ PluginStatusMapper            → 状态枚举映射           │  │
    │  │ ExtensionPointNameMapper      → 扩展点命名映射         │  │
    │  └────────────────────────────────────────────────────────┘  │
    │       │                                                       │
    │       ▼                                                       │
    │  legacy 层（app/plugins/plugin_system.py）                    │
    │  PluginMetadata / PluginLifecycleManager / PluginLoader       │
    └──────────────────────────────────────────────────────────────┘

契约稳定性：本适配器属于"实现层"，不进入契约目录，可随 legacy 演进调整。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

from app.contracts.plugin import (
    BUILTIN_EXTENSION_POINTS,
    IPlugin,
    PluginContext,
    PluginManifest,
    validate_capability_request,
)
from app.plugins.plugin_system import (
    PluginLifecycleManager,
    PluginLoader,
    PluginMetadata,
    PluginRegistry,
    PluginStatus,
)

logger = logging.getLogger(__name__)


# 状态枚举映射：legacy PluginStatus 契约友好字符串


class PluginStatusMapper:
    """legacy PluginStatus ↔ 契约/前端友好字符串映射.

    legacy 枚举（DISCOVERED/REGISTERED/INITIALIZED/ENABLED/DISABLED/
    UNINSTALLED/ERROR）粒度过细，契约层对外只暴露 4 个稳定状态：
    installed / enabled / disabled / error / uninstalled.

    这是单向投影：legacy → 契约，不丢失信息（原始状态仍可通过 metadata 查询）。
    """

    _MAP: dict[PluginStatus, str] = {
        PluginStatus.DISCOVERED: "installed",
        PluginStatus.REGISTERED: "installed",
        PluginStatus.INITIALIZED: "installed",
        PluginStatus.ENABLED: "enabled",
        PluginStatus.DISABLED: "disabled",
        PluginStatus.UNINSTALLED: "uninstalled",
        PluginStatus.ERROR: "error",
    }

    @classmethod
    def to_contract_status(cls, legacy: PluginStatus) -> str:
        """把 legacy 状态投影到契约层状态字符串."""
        return cls._MAP.get(legacy, "error")

    @classmethod
    def all_contract_statuses(cls) -> list[str]:
        """返回契约层所有合法状态字符串."""
        return ["installed", "enabled", "disabled", "error", "uninstalled"]


# 扩展点命名映射：core.ui.workspace_panel workspace.panel


class ExtensionPointNameMapper:
    """扩展点命名双向映射.

    后端契约（BUILTIN_EXTENSION_POINTS）使用点号分层命名（core.ui.workspace_panel），
    前端 src/contracts/plugin.ts 使用简短命名（workspace.panel）.
    本映射器负责在两个命名空间之间转换，避免在业务代码中硬编码字符串。
    """

    _BACKEND_TO_FRONTEND: dict[str, str] = {
        BUILTIN_EXTENSION_POINTS.TASK_HANDLER: "task_handler",
        BUILTIN_EXTENSION_POINTS.DATASET_READER: "dataset_reader",
        BUILTIN_EXTENSION_POINTS.MODEL_REGISTRY: "model_registry",
        BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE: "workflow_template",
        BUILTIN_EXTENSION_POINTS.UI_WORKSPACE_PANEL: "workspace.panel",
        BUILTIN_EXTENSION_POINTS.UI_SETTINGS_TAB: "settings.tab",
        BUILTIN_EXTENSION_POINTS.CHAT_COMMAND: "chat_command",
    }

    _FRONTEND_TO_BACKEND: dict[str, str] = {v: k for k, v in _BACKEND_TO_FRONTEND.items()}

    @classmethod
    def to_frontend_name(cls, backend: str) -> str:
        """core.ui.workspace_panel → workspace.panel.

        未知扩展点名原样返回（向后兼容第三方扩展点）。
        """
        return cls._BACKEND_TO_FRONTEND.get(backend, backend)

    @classmethod
    def to_backend_name(cls, frontend: str) -> str:
        """workspace.panel → core.ui.workspace_panel.

        未知扩展点名原样返回。
        """
        return cls._FRONTEND_TO_BACKEND.get(frontend, frontend)

    @classmethod
    def all_backend_names(cls) -> list[str]:
        return list(cls._BACKEND_TO_FRONTEND.keys())


# Manifest 适配：legacy PluginMetadata 契约 PluginManifest


def _legacy_entrypoint_to_contract(plugin_id: str, legacy_entry: str) -> str:
    """把 legacy entry_point（文件名 "main.py"）转换为契约 entrypoint（"module:Class"）.

    legacy 用文件路径 + importlib.util.spec_from_file_location 加载，
    契约要求 "module.path:ClassName" 格式以便 importlib.import_module 加载。
    适配器统一用 legacy loader 的 module_name 约定：``plugin_<id>``。
    """
    # legacy loader 的 module_name 约定：plugin_{metadata.id}
    # 但 id 可能含非合法字符，loader 实际用原样字符串作为 key
    safe_id = plugin_id.replace("-", "_").replace(".", "_")
    return f"plugin_{safe_id}:Plugin"


def adapt_metadata_to_manifest(metadata: PluginMetadata) -> PluginManifest:
    """把 legacy PluginMetadata 适配为契约 PluginManifest.

    字段映射：
        id            → id
        name          → name
        version       → version
        author        → author
        description   → description
        entry_point   → entrypoint（格式转换）
        capabilities  → required_capabilities（仅保留契约内置能力）
        dependencies  → dependencies（PluginDependency.name 列表）
        config_schema → config_schema
        (无)          → license（默认 "MIT"）
        (无)          → required_contracts（默认空，legacy 无契约依赖概念）
    """
    # 过滤 capabilities：只保留契约内置清单中的能力
    required_caps = [c for c in metadata.capabilities if validate_capability_request(c)]
    if len(required_caps) < len(metadata.capabilities):
        dropped = set(metadata.capabilities) - set(required_caps)
        logger.warning(
            "Plugin '%s' 声明了非内置能力，已过滤: %s",
            metadata.id,
            sorted(dropped),
        )

    return PluginManifest(
        id=metadata.id,
        name=metadata.name,
        version=metadata.version,
        description=metadata.description or "",
        author=metadata.author or "",
        license="MIT",  # legacy 无 license 字段，默认 MIT
        entrypoint=_legacy_entrypoint_to_contract(metadata.id, metadata.entry_point),
        required_contracts=[],  # legacy 无契约依赖概念
        required_capabilities=required_caps,
        optional_capabilities=[],
        dependencies=[d.name for d in metadata.dependencies],
        config_schema=metadata.config_schema or {},
        homepage="",
        tags=[metadata.plugin_type] if metadata.plugin_type else [],
    )


# 插件实例适配：legacy 插件实例 契约 IPlugin


class LegacyPluginInstanceAdapter(IPlugin):
    """把 legacy 插件实例适配为契约 IPlugin.

    legacy 插件实现以下钩子（可选）：
        initialize(context: dict) → None
        on_enable() → None
        on_disable() → None
        shutdown() → None
        set_metadata(metadata) → None
        set_config(config) → None

    契约 IPlugin 要求：
        manifest() → PluginManifest
        async on_load(context: PluginContext) → None
        async on_unload() → None
        health_check() → dict

    生命周期映射：
        契约 on_load   ← legacy initialize + on_enable
        契约 on_unload ← legacy on_disable + shutdown
    """

    def __init__(
        self,
        legacy_instance: Any,
        metadata: PluginMetadata,
        *,
        manifest: PluginManifest | None = None,
    ) -> None:
        self._legacy = legacy_instance
        self._metadata = metadata
        self._manifest = manifest or adapt_metadata_to_manifest(metadata)
        self._loaded = False
        self._lock = threading.Lock()

    def manifest(self) -> PluginManifest:
        return self._manifest

    async def on_load(self, context: PluginContext) -> None:
        """契约加载：调用 legacy initialize + on_enable.

        如果 legacy 插件没有这些方法，静默跳过（向后兼容裸模块插件）。
        """
        with self._lock:
            if self._loaded:
                logger.warning(
                    "Plugin '%s' already loaded, skip duplicate on_load",
                    self._metadata.id,
                )
                return

            # legacy initialize 接收的是 dict context，不是 PluginContext dataclass
            legacy_ctx: dict[str, Any] = {
                "plugin_id": context.plugin_id,
                "config": context.config,
                "data_dir": context.data_dir,
                "task_registry": context.task_registry,
                "dataset_store": context.dataset_store,
                "observability": context.observability,
                "logger": context.logger,
            }

            if hasattr(self._legacy, "initialize"):
                self._legacy.initialize(legacy_ctx)

            if hasattr(self._legacy, "on_enable"):
                self._legacy.on_enable()

            self._loaded = True
            logger.info(
                "Legacy plugin '%s' loaded via contract adapter",
                self._metadata.id,
            )

    async def on_unload(self) -> None:
        """契约卸载：调用 legacy on_disable + shutdown."""
        with self._lock:
            if not self._loaded:
                # 未加载也允许卸载（幂等），仅清理标记
                self._loaded = False
                return

            if hasattr(self._legacy, "on_disable"):
                try:
                    self._legacy.on_disable()
                except (RuntimeError, OSError, ValueError) as e:
                    # 卸载时单个钩子失败不应阻塞后续清理
                    logger.warning(
                        "Legacy on_disable failed for '%s': %s",
                        self._metadata.id,
                        e,
                        exc_info=True,
                    )

            if hasattr(self._legacy, "shutdown"):
                try:
                    self._legacy.shutdown()
                except (RuntimeError, OSError, ValueError) as e:
                    logger.warning(
                        "Legacy shutdown failed for '%s': %s",
                        self._metadata.id,
                        e,
                        exc_info=True,
                    )

            self._loaded = False
            logger.info(
                "Legacy plugin '%s' unloaded via contract adapter",
                self._metadata.id,
            )

    def health_check(self) -> dict[str, Any]:
        """健康检查：优先调用 legacy health_check，否则返回基础信息."""
        if hasattr(self._legacy, "health_check"):
            try:
                result = self._legacy.health_check()
                if isinstance(result, dict):
                    return result
            except (RuntimeError, OSError, ValueError) as e:
                logger.warning(
                    "Legacy health_check failed for '%s': %s",
                    self._metadata.id,
                    e,
                    exc_info=True,
                )

        return {
            "healthy": self._loaded,
            "checks": {
                "loaded": self._loaded,
                "legacy_instance": self._legacy is not None,
            },
            "message": "legacy adapter default health",
        }

    @property
    def legacy_instance(self) -> Any:
        """暴露 legacy 实例供高级用途（如反射调用非契约方法）."""
        return self._legacy

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# PluginContext 构造：从核心基础设施构造 PluginContext


@dataclass
class PluginContextFactory:
    """PluginContext 构造工厂.

    核心层在加载插件时通过此工厂构造 PluginContext，注入核心接口实例。
    """

    task_registry: Any = None
    dataset_store: Any = None
    observability: Any = None
    logger_factory: Callable[[str], Any] = field(default=lambda pid: logging.getLogger(f"plugin.{pid}"))
    data_dir_root: str = ""

    def build(
        self,
        plugin_id: str,
        config: dict[str, Any] | None = None,
    ) -> PluginContext:
        return PluginContext(
            plugin_id=plugin_id,
            config=config or {},
            task_registry=self.task_registry,
            dataset_store=self.dataset_store,
            observability=self.observability,
            logger=self.logger_factory(plugin_id),
            data_dir=self._plugin_data_dir(plugin_id),
        )

    def _plugin_data_dir(self, plugin_id: str) -> str:
        """返回插件私有数据目录路径.

        契约要求 data_dir 不能为空，若无 root 配置则用相对路径 ".plugin_data/<id>".
        """
        from pathlib import Path

        if self.data_dir_root:
            return str(Path(self.data_dir_root) / plugin_id)
        return str(Path(".plugin_data") / plugin_id)


# PluginLifecycleManagerAdapter：顶层契约入口


class PluginLifecycleManagerAdapter:
    """把 legacy PluginLifecycleManager 适配为契约层入口.

    提供以下契约能力：
        list_manifests()              → List[PluginManifest]
        get_manifest(plugin_id)       → PluginManifest
        load_plugin_as_contract(id)   → IPlugin
        get_contract_status(id)       → str
        to_contract_info(id)          → dict（API 友好）
        install / enable / disable / uninstall（透传 legacy）

    本适配器不持有 legacy 管理器的所有权，只持有引用，避免双重生命周期管理。
    """

    def __init__(
        self,
        legacy_manager: PluginLifecycleManager,
        registry: PluginRegistry | None = None,
        loader: PluginLoader | None = None,
        context_factory: PluginContextFactory | None = None,
    ) -> None:
        self._mgr = legacy_manager
        self._registry = registry or PluginRegistry.get_instance()
        self._loader = loader or PluginLoader(self._registry)
        self._context_factory = context_factory or PluginContextFactory()
        self._adapter_cache: dict[str, LegacyPluginInstanceAdapter] = {}
        self._lock = threading.Lock()

    # 查询接口

    def list_manifests(self, *, include_uninstalled: bool = False) -> list[PluginManifest]:
        """列出所有插件的 manifest（契约视图）."""
        result: list[PluginManifest] = []
        for metadata in self._registry.list_plugins():
            if not include_uninstalled and metadata.status == PluginStatus.UNINSTALLED:
                continue
            result.append(adapt_metadata_to_manifest(metadata))
        return result

    def get_manifest(self, plugin_id: str) -> PluginManifest | None:
        """获取单个插件 manifest，不存在返回 None."""
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            return None
        return adapt_metadata_to_manifest(metadata)

    def get_contract_status(self, plugin_id: str) -> str:
        """获取契约层状态字符串，不存在返回 'uninstalled'."""
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            return "uninstalled"
        return PluginStatusMapper.to_contract_status(metadata.status)

    def to_contract_info(self, plugin_id: str) -> dict[str, Any]:
        """转换为 API 友好的契约信息字典.

        不在错误消息中回显 plugin_id（防枚举攻击），调用方负责权限校验。
        """
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            raise KeyError("Plugin not found")

        manifest = adapt_metadata_to_manifest(metadata)
        adapter = self._get_or_create_adapter(metadata)

        return {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "author": manifest.author,
            "license": manifest.license,
            "entrypoint": manifest.entrypoint,
            "required_contracts": manifest.required_contracts,
            "required_capabilities": manifest.required_capabilities,
            "optional_capabilities": manifest.optional_capabilities,
            "dependencies": manifest.dependencies,
            "config_schema": manifest.config_schema,
            "tags": manifest.tags,
            "status": PluginStatusMapper.to_contract_status(metadata.status),
            "legacy_status": metadata.status.value,
            "loaded": adapter.is_loaded,
            "plugin_type": metadata.plugin_type,
            "installed_at": metadata.installed_at,
            "enabled_at": metadata.enabled_at,
            "disabled_at": metadata.disabled_at,
        }

    # 加载/卸载（契约入口）

    def load_plugin_as_contract(self, plugin_id: str) -> IPlugin:
        """以契约 IPlugin 形式加载插件.

        返回的 IPlugin 实例尚未调用 on_load，由调用方决定何时触发
        （通常在 enable_plugin 时调用 adapter.on_load(context)）.
        """
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            raise KeyError("Plugin not found")

        # 复用 legacy loader 的实例缓存，避免重复加载
        instance = self._registry.get_plugin_instance(plugin_id)
        if instance is None:
            instance = self._loader.load_plugin(metadata)

        return self._get_or_create_adapter(metadata, instance)

    async def load_and_enable(
        self,
        plugin_id: str,
        config: dict[str, Any] | None = None,
    ) -> IPlugin:
        """一步加载 + on_load：契约层启用入口.

        流程：
            1. legacy initialize_plugin（如果尚未初始化）
            2. legacy enable_plugin（调用 on_enable）
            3. 契约 adapter.on_load(context)（再次触发 initialize/on_enable，幂等）
               注：legacy loader 已实例化，重复 initialize 会被 legacy 插件自行处理
        """
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            raise KeyError("Plugin not found")

        # legacy 侧先走完 initialize + enable 流程（保持向后兼容）
        if metadata.status in (PluginStatus.REGISTERED, PluginStatus.DISABLED):
            self._mgr.enable_plugin(plugin_id)

        # 契约侧再走 on_load
        adapter = self._get_or_create_adapter(metadata)
        if not adapter.is_loaded:
            context = self._context_factory.build(plugin_id, config)
            await adapter.on_load(context)

        return adapter

    async def unload_and_disable(self, plugin_id: str) -> bool:
        """契约层禁用入口：on_unload + legacy disable."""
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            return False

        adapter = self._get_or_create_adapter(metadata)
        if adapter.is_loaded:
            await adapter.on_unload()

        if metadata.status == PluginStatus.ENABLED:
            self._mgr.disable_plugin(plugin_id)

        return True

    # 安装/卸载（透传 legacy）

    def install(self, metadata: PluginMetadata) -> None:
        """注册新插件到 registry（不触发加载）."""
        if not self._registry.has_plugin(metadata.id):
            self._registry.register(metadata)

    def uninstall(self, plugin_id: str) -> None:
        """卸载插件：先契约 on_unload，再 legacy uninstall_plugin."""
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            return

        adapter = self._adapter_cache.pop(plugin_id, None)
        if adapter and adapter.is_loaded:
            # 异步钩子在这里同步调用（uninstall 通常是同步 API）
            # 若 legacy 插件 on_disable/shutdown 是 async，调用方需在外层 await
            import asyncio

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                # Python 3.11+: 当前线程无事件循环时 get_event_loop() 抛
                # RuntimeError（旧版本会隐式创建）。此时直接新建 loop
                # 同步执行异步 on_unload，避免卸载时静默丢失清理钩子。
                # 注意：不能 return——下方 legacy uninstall_plugin 仍需执行。
                try:
                    asyncio.run(adapter.on_unload())
                except OSError as e:
                    logger.warning(
                        "Async on_unload failed during uninstall: %s",
                        e,
                        exc_info=True,
                    )
            else:
                try:
                    if loop.is_running():
                        # 在运行中的事件循环里不能 sync 调用 async，回退到 legacy shutdown
                        logger.warning(
                            "Cannot await async on_unload in running loop for '%s', falling back to legacy shutdown only",
                            plugin_id,
                        )
                    else:
                        loop.run_until_complete(adapter.on_unload())
                except (RuntimeError, OSError) as e:
                    logger.warning(
                        "Async on_unload failed during uninstall: %s",
                        e,
                        exc_info=True,
                    )

        self._mgr.uninstall_plugin(plugin_id)

    # 内部辅助

    def _get_or_create_adapter(
        self,
        metadata: PluginMetadata,
        legacy_instance: Any | None = None,
    ) -> LegacyPluginInstanceAdapter:
        with self._lock:
            adapter = self._adapter_cache.get(metadata.id)
            if adapter is not None:
                return adapter

            if legacy_instance is None:
                legacy_instance = self._registry.get_plugin_instance(metadata.id)

            # 若 legacy 实例还不存在，构造一个占位 adapter（manifest 仍可用）
            # 实际加载由 load_plugin_as_contract 触发
            adapter = LegacyPluginInstanceAdapter(
                legacy_instance=legacy_instance,
                metadata=metadata,
            )
            self._adapter_cache[metadata.id] = adapter
            return adapter

    def clear_cache(self) -> None:
        """清空 adapter 缓存（主要用于测试）."""
        with self._lock:
            self._adapter_cache.clear()


# 模块级单例访问（与 legacy get_plugin_manager 对齐）


_adapter_singleton: PluginLifecycleManagerAdapter | None = None
_adapter_lock = threading.Lock()


def get_plugin_contract_adapter() -> PluginLifecycleManagerAdapter:
    """获取插件契约适配器单例.

    延迟初始化：首次调用时从 legacy get_plugin_manager() 构造适配器。
    后续调用返回同一实例。

    Raises:
        RuntimeError: 如果 legacy 插件系统尚未初始化（init_plugin_system 未调用）.
    """
    global _adapter_singleton
    if _adapter_singleton is not None:
        return _adapter_singleton

    with _adapter_lock:
        if _adapter_singleton is None:
            # 延迟导入避免循环依赖
            from app.dependencies import get_plugin_manager

            legacy_mgr = get_plugin_manager()  # 可能抛 RuntimeError
            _adapter_singleton = PluginLifecycleManagerAdapter(legacy_mgr)
        return _adapter_singleton


def reset_plugin_contract_adapter() -> None:
    """重置单例（主要用于测试）."""
    global _adapter_singleton
    with _adapter_lock:
        _adapter_singleton = None


__all__ = [
    "PluginStatusMapper",
    "ExtensionPointNameMapper",
    "adapt_metadata_to_manifest",
    "LegacyPluginInstanceAdapter",
    "PluginContextFactory",
    "PluginLifecycleManagerAdapter",
    "get_plugin_contract_adapter",
    "reset_plugin_contract_adapter",
]
