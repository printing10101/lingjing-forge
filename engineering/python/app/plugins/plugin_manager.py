"""插件管理器主模块。

从原 ``plugin_system.py`` 拆分而来，集中放置插件注册表、发现器、
加载器、生命周期管理器、依赖解析器以及系统级 holder 与初始化函数。

依赖 :mod:`app.plugins.plugin_types` 与 :mod:`app.plugins.plugin_metadata`。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import shutil
import sys
import time
import threading
from pathlib import Path
from typing import Any

from app.plugins.plugin_metadata import PluginMetadata
from app.plugins.plugin_types import (
    PluginStatus,
    VALID_CAPABILITIES,
)

logger = logging.getLogger(__name__)


class PluginRegistry:
    _instance: "PluginRegistry" | None = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._plugins: dict[str, PluginMetadata] = {}
        self._plugins_by_type: dict[str, list[str]] = {}
        self._plugins_by_capability: dict[str, list[str]] = {}
        self._plugin_instances: dict[str, Any] = {}
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "PluginRegistry":
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

    def register(self, metadata: PluginMetadata) -> None:
        with self._lock:
            if metadata.id in self._plugins:
                raise ValueError(f"Plugin '{metadata.id}' already registered")

            self._plugins[metadata.id] = metadata
            metadata.status = PluginStatus.REGISTERED

            ptype = metadata.plugin_type
            if ptype:
                if ptype not in self._plugins_by_type:
                    self._plugins_by_type[ptype] = []
                self._plugins_by_type[ptype].append(metadata.id)

            for cap in metadata.capabilities:
                if cap not in self._plugins_by_capability:
                    self._plugins_by_capability[cap] = []
                self._plugins_by_capability[cap].append(metadata.id)

            logger.info("Plugin registered: %s v%s", metadata.id, metadata.version)

    def unregister(self, plugin_id: str) -> None:
        with self._lock:
            if plugin_id not in self._plugins:
                raise KeyError(f"Plugin '{plugin_id}' not found")

            metadata = self._plugins[plugin_id]

            ptype = metadata.plugin_type
            if ptype and plugin_id in self._plugins_by_type.get(ptype, []):
                self._plugins_by_type[ptype].remove(plugin_id)

            for cap in metadata.capabilities:
                if plugin_id in self._plugins_by_capability.get(cap, []):
                    self._plugins_by_capability[cap].remove(plugin_id)

            self._plugin_instances.pop(plugin_id, None)
            self._plugins.pop(plugin_id, None)

            logger.info("Plugin unregistered: %s", plugin_id)

    def get(self, plugin_id: str) -> PluginMetadata | None:
        # 安全修复：保护 _plugins 字典的并发读
        with self._lock:
            return self._plugins.get(plugin_id)

    def get_plugin_instance(self, plugin_id: str) -> Any | None:
        # 安全修复：保护 _plugin_instances 字典的并发读
        with self._lock:
            return self._plugin_instances.get(plugin_id)

    def set_instance(self, plugin_id: str, instance: Any) -> None:
        # 安全修复：保护 _plugin_instances 字典的并发写
        with self._lock:
            self._plugin_instances[plugin_id] = instance

    def list_plugins(
        self,
        status: PluginStatus | None = None,
        plugin_type: str | None = None,
        capability: str | None = None,
    ) -> list[PluginMetadata]:
        # 安全修复：保护 _plugins 字典的并发读，构建快照避免迭代时被修改
        with self._lock:
            result = list(self._plugins.values())

        if status:
            result = [p for p in result if p.status == status]
        if plugin_type:
            result = [p for p in result if p.plugin_type == plugin_type]
        if capability:
            result = [p for p in result if capability in p.capabilities]

        return result

    def get_plugins_by_type(self, plugin_type: str) -> list[str]:
        # 安全修复：保护 _plugins_by_type 字典的并发读
        with self._lock:
            return list(self._plugins_by_type.get(plugin_type, []))

    def get_plugins_by_capability(self, capability: str) -> list[str]:
        # 安全修复：保护 _plugins_by_capability 字典的并发读
        with self._lock:
            return list(self._plugins_by_capability.get(capability, []))

    def has_plugin(self, plugin_id: str) -> bool:
        # 安全修复：保护 _plugins 字典的并发读
        with self._lock:
            return plugin_id in self._plugins

    def update_status(self, plugin_id: str, status: PluginStatus) -> None:
        # 安全修复：保护 _plugins 字典的并发读写
        with self._lock:
            if plugin_id in self._plugins:
                self._plugins[plugin_id].status = status
                if status == PluginStatus.ENABLED:
                    self._plugins[plugin_id].enabled_at = time.time()
                elif status == PluginStatus.DISABLED:
                    self._plugins[plugin_id].disabled_at = time.time()

    def update_config(self, plugin_id: str, config: dict[str, Any]) -> None:
        # 安全修复：保护 _plugins 字典的并发读写
        with self._lock:
            if plugin_id in self._plugins:
                self._plugins[plugin_id].config.update(config)

    def get_all_metadata(self) -> dict[str, Any]:
        # 安全修复：保护所有字典的并发读，构建快照
        with self._lock:
            return {
                "plugins": {pid: p.to_dict() for pid, p in self._plugins.items()},
                "by_type": {k: list(v) for k, v in self._plugins_by_type.items()},
                "by_capability": {k: list(v) for k, v in self._plugins_by_capability.items()},
            }


class PluginDiscovery:
    def __init__(
        self,
        plugin_dirs: list[str] | None = None,
        user_dirs: list[str] | None = None,
    ):
        self.plugin_dirs = plugin_dirs or []
        self.user_dirs = user_dirs or []
        self._registry = PluginRegistry.get_instance()

    def discover(self) -> list[PluginMetadata]:
        discovered = []

        all_dirs = self.plugin_dirs + self.user_dirs
        for directory in all_dirs:
            dir_path = Path(directory)
            if not dir_path.exists():
                logger.warning("Plugin directory not found: %s", directory)
                continue

            discovered.extend(self._scan_directory(dir_path))

        logger.info("Discovered %s plugins", len(discovered))
        return discovered

    def _scan_directory(self, directory: Path) -> list[PluginMetadata]:
        plugins = []

        for item in directory.iterdir():
            if item.is_dir():
                meta = self._load_plugin_meta(item)
                if meta:
                    plugins.append(meta)

        return plugins

    def _load_plugin_meta(self, plugin_dir: Path) -> PluginMetadata | None:
        plugin_json = plugin_dir / "plugin.json"

        if not plugin_json.exists():
            logger.debug("No plugin.json found in %s", plugin_dir)
            return None

        try:
            with open(plugin_json, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._validate_metadata(data, plugin_dir)

            data["plugin_path"] = str(plugin_dir)

            if "plugin_type" not in data:
                caps = data.get("capabilities", [])
                if "data_source" in caps:
                    data["plugin_type"] = "data_source"
                elif "machine_control" in caps:
                    data["plugin_type"] = "adapter"

            metadata = PluginMetadata.from_dict(data)
            metadata.installed_at = time.time()

            return metadata

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error("Failed to load plugin from %s: %s", plugin_dir, e)
            return None

    def _validate_metadata(self, data: dict[str, Any], plugin_dir: Path) -> None:
        required_fields = ["id", "name", "version"]
        for field_name in required_fields:
            if field_name not in data:
                raise ValueError(f"Missing required field: {field_name}")

        caps = data.get("capabilities", [])
        invalid_caps = set(caps) - VALID_CAPABILITIES
        if invalid_caps:
            raise ValueError(f"Invalid capabilities: {invalid_caps}")

        deps = data.get("dependencies", [])
        for dep in deps:
            if "name" not in dep:
                raise ValueError("Dependency missing 'name' field")

        entry = data.get("entry_point", "main.py")
        entry_path = plugin_dir / entry
        if not entry_path.exists():
            raise ValueError(f"Entry point not found: {entry}")


class PluginLoader:
    def __init__(self, registry: PluginRegistry | None = None):
        self._registry = registry or PluginRegistry.get_instance()

    def load_plugin(self, metadata: PluginMetadata) -> Any:
        plugin_dir = Path(metadata.plugin_path)
        entry_point = plugin_dir / metadata.entry_point

        if not entry_point.exists():
            raise FileNotFoundError(f"Entry point not found: {entry_point}")

        sys.path.insert(0, str(plugin_dir))

        try:
            module_name = f"plugin_{metadata.id}"

            if module_name in sys.modules:
                del sys.modules[module_name]

            spec = importlib.util.spec_from_file_location(module_name, entry_point)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load spec for {entry_point}")

            module = importlib.util.module_from_spec(spec)
            setattr(module, "__plugin_metadata__", metadata)

            spec.loader.exec_module(module)

            instance = self._instantiate_plugin(module, metadata)
            self._registry.set_instance(metadata.id, instance)

            return instance

        except (ImportError, OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
            # 兜底捕获：插件加载涉及 importlib + 用户代码 + 反射实例化，
            # 任何异常类型都应被收口并转换为 ERROR 状态后抛出
            metadata.status = PluginStatus.ERROR
            logger.error(
                f"Failed to load plugin {metadata.id}: {e}",
                exc_info=True,
            )
            raise
        finally:
            if str(plugin_dir) in sys.path:
                sys.path.remove(str(plugin_dir))

    def _instantiate_plugin(self, module: Any, metadata: PluginMetadata) -> Any:
        plugin_class = getattr(module, "Plugin", None)

        if plugin_class is None:
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and hasattr(attr, "initialize") and hasattr(attr, "shutdown"):
                    plugin_class = attr
                    break

        if plugin_class is None:
            return module

        instance = plugin_class()

        if hasattr(instance, "set_metadata"):
            instance.set_metadata(metadata)

        if hasattr(instance, "set_config"):
            instance.set_config(metadata.config)

        return instance

    def reload_plugin(self, plugin_id: str) -> Any:
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")

        module_name = f"plugin_{plugin_id}"
        if module_name in sys.modules:
            del sys.modules[module_name]

        old_instance = self._registry.get_plugin_instance(plugin_id)
        if old_instance and hasattr(old_instance, "shutdown"):
            try:
                old_instance.shutdown()
            except (RuntimeError, OSError) as e:
                # 旧实例关闭失败不应阻塞插件重载流程
                logger.warning(
                    f"Error shutting down old instance: {e}",
                    exc_info=True,
                )

        return self.load_plugin(metadata)


class PluginLifecycleManager:
    def __init__(
        self,
        registry: PluginRegistry | None = None,
        loader: PluginLoader | None = None,
    ):
        self._registry = registry or PluginRegistry.get_instance()
        self._loader = loader or PluginLoader(self._registry)
        self._context: dict[str, Any] = {}

    def set_context(self, key: str, value: Any) -> None:
        self._context[key] = value

    def get_context(self, key: str) -> Any | None:
        return self._context.get(key)

    def initialize_plugin(self, plugin_id: str) -> None:
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")

        if metadata.status not in (PluginStatus.REGISTERED, PluginStatus.DISABLED):
            raise ValueError(f"Plugin '{plugin_id}' cannot be initialized (status: {metadata.status})")

        instance = self._registry.get_plugin_instance(plugin_id)

        if instance is None:
            instance = self._loader.load_plugin(metadata)

        if hasattr(instance, "initialize"):
            instance.initialize(self._context)

        self._registry.update_status(plugin_id, PluginStatus.INITIALIZED)
        logger.info("Plugin initialized: %s", plugin_id)

    def enable_plugin(self, plugin_id: str) -> None:
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")

        if metadata.status == PluginStatus.ENABLED:
            logger.info("Plugin '%s' is already enabled", plugin_id)
            return

        if metadata.status not in (
            PluginStatus.REGISTERED,
            PluginStatus.INITIALIZED,
            PluginStatus.DISABLED,
        ):
            raise ValueError(f"Plugin '{plugin_id}' cannot be enabled (status: {metadata.status})")

        if metadata.status in (PluginStatus.REGISTERED, PluginStatus.DISABLED):
            self.initialize_plugin(plugin_id)

        instance = self._registry.get_plugin_instance(plugin_id)

        if instance is not None and hasattr(instance, "on_enable"):
            instance.on_enable()

        self._registry.update_status(plugin_id, PluginStatus.ENABLED)
        logger.info("Plugin enabled: %s", plugin_id)

    def disable_plugin(self, plugin_id: str) -> None:
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")

        if metadata.status != PluginStatus.ENABLED:
            logger.info("Plugin '%s' is not enabled", plugin_id)
            return

        instance = self._registry.get_plugin_instance(plugin_id)

        if instance is not None and hasattr(instance, "on_disable"):
            instance.on_disable()

        self._registry.update_status(plugin_id, PluginStatus.DISABLED)
        logger.info("Plugin disabled: %s", plugin_id)

    def uninstall_plugin(self, plugin_id: str) -> None:
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")

        instance = self._registry.get_plugin_instance(plugin_id)

        if instance and hasattr(instance, "shutdown"):
            try:
                instance.shutdown()
            except (RuntimeError, OSError) as e:
                # 卸载时插件关闭失败不应阻塞文件清理
                logger.warning(
                    f"Error during plugin shutdown: {e}",
                    exc_info=True,
                )

        plugin_dir = Path(metadata.plugin_path)

        if plugin_dir.exists():
            shutil.rmtree(plugin_dir, ignore_errors=True)

        self._registry.unregister(plugin_id)
        logger.info("Plugin uninstalled: %s", plugin_id)

    def discover_and_register_all(self, plugin_dirs: list[str], user_dirs: list[str] | None = None) -> int:
        discovery = PluginDiscovery(plugin_dirs=plugin_dirs, user_dirs=user_dirs)
        plugins = discovery.discover()

        count = 0
        for metadata in plugins:
            if not self._registry.has_plugin(metadata.id):
                self._registry.register(metadata)
                count += 1

        logger.info("Registered %s new plugins", count)
        return count

    def initialize_all(self) -> int:
        count = 0
        for metadata in self._registry.list_plugins(status=PluginStatus.REGISTERED):
            try:
                self.initialize_plugin(metadata.id)
                count += 1
            except (RuntimeError, ValueError, ImportError, OSError) as e:
                # 批量初始化时单个插件失败不应阻塞其他插件
                logger.error(
                    f"Failed to initialize plugin {metadata.id}: {e}",
                    exc_info=True,
                )

        return count

    def enable_all(self) -> int:
        count = 0
        for metadata in self._registry.list_plugins(status=PluginStatus.INITIALIZED):
            try:
                self.enable_plugin(metadata.id)
                count += 1
            except (RuntimeError, ValueError, OSError) as e:
                # 批量启用时单个插件失败不应阻塞其他插件
                logger.error(
                    f"Failed to enable plugin {metadata.id}: {e}",
                    exc_info=True,
                )

        return count

    def shutdown_all(self) -> None:
        for metadata in self._registry.list_plugins():
            if metadata.status == PluginStatus.ENABLED:
                try:
                    self.disable_plugin(metadata.id)
                except (RuntimeError, OSError) as e:
                    # 关闭过程中单个插件失败不应阻塞整体清理
                    logger.error(
                        f"Error disabling plugin {metadata.id}: {e}",
                        exc_info=True,
                    )

        for metadata in self._registry.list_plugins():
            try:
                instance = self._registry.get_plugin_instance(metadata.id)
                if instance and hasattr(instance, "shutdown"):
                    instance.shutdown()
            except (RuntimeError, OSError) as e:
                # 关闭过程中单个插件失败不应阻塞整体清理
                logger.error(
                    f"Error shutting down plugin {metadata.id}: {e}",
                    exc_info=True,
                )

    def get_plugin_info(self, plugin_id: str) -> dict[str, Any]:
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")

        return {
            "metadata": metadata.to_dict(),
            "has_instance": self._registry.get_plugin_instance(plugin_id) is not None,
            "context_keys": list(self._context.keys()),
        }


class DependencyResolver:
    def __init__(self, registry: PluginRegistry | None = None):
        self._registry = registry or PluginRegistry.get_instance()

    def resolve_dependencies(self, plugin_id: str) -> list[str]:
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            raise KeyError(f"Plugin '{plugin_id}' not found")

        visited: set[str] = set()
        order: list[str] = []
        self._dfs(plugin_id, visited, order)

        return order

    def _dfs(self, plugin_id: str, visited: set[str], order: list[str]) -> None:
        if plugin_id in visited:
            return

        visited.add(plugin_id)

        metadata = self._registry.get(plugin_id)
        if metadata is None:
            return

        for dep in metadata.dependencies:
            if self._registry.has_plugin(dep.name):
                self._dfs(dep.name, visited, order)
            elif dep.required:
                raise ValueError(f"Required dependency '{dep.name}' not found for plugin '{plugin_id}'")

        order.append(plugin_id)

    def check_compatibility(self, plugin_id: str, core_version: str = "1.6.0") -> bool:
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            return False

        try:
            from packaging import version as pkg_version

            core_v = pkg_version.parse(core_version)
            min_v = pkg_version.parse(metadata.min_core_version)
            max_v = pkg_version.parse(metadata.max_core_version)

            return min_v <= core_v <= max_v
        except (ImportError, ValueError, TypeError):
            # packaging 不可用或版本字符串格式异常时，宽松视为兼容
            return True

    def get_dependency_tree(self, plugin_id: str, depth: int = 0) -> dict[str, Any]:
        metadata = self._registry.get(plugin_id)
        if metadata is None:
            return {}

        tree: dict[str, Any] = {
            "id": plugin_id,
            "name": metadata.name,
            "version": metadata.version,
            "dependencies": [],
        }

        if depth < 3:
            for dep in metadata.dependencies:
                if self._registry.has_plugin(dep.name):
                    tree["dependencies"].append(self.get_dependency_tree(dep.name, depth + 1))
                else:
                    tree["dependencies"].append(
                        {
                            "id": dep.name,
                            "version": dep.version,
                            "required": dep.required,
                            "status": "missing" if dep.required else "optional",
                        }
                    )

        return tree


class _PluginSystemHolder:
    """Thread-safe holder for plugin system singletons (initialized externally)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plugin_manager: PluginLifecycleManager | None = None
        self._dependency_resolver: DependencyResolver | None = None

    def init(
        self,
        plugin_dirs: list[str] | None = None,
        user_dirs: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> PluginLifecycleManager:
        """强制初始化插件系统（与重构前 init_plugin_system 行为一致）。"""
        with self._lock:
            registry = PluginRegistry.get_instance()
            loader = PluginLoader(registry)

            self._plugin_manager = PluginLifecycleManager(registry, loader)
            self._dependency_resolver = DependencyResolver(registry)

            if context:
                for key, value in context.items():
                    self._plugin_manager.set_context(key, value)

            count = self._plugin_manager.discover_and_register_all(
                plugin_dirs=plugin_dirs or [],
                user_dirs=user_dirs or [],
            )

            self._plugin_manager.initialize_all()
            self._plugin_manager.enable_all()

            logger.info("Plugin system initialized with %s plugins", count)
            return self._plugin_manager

    def get_plugin_manager(self) -> PluginLifecycleManager:
        # 快速路径
        if self._plugin_manager is not None:
            return self._plugin_manager
        with self._lock:
            if self._plugin_manager is None:
                raise RuntimeError("Plugin system not initialized. Call init_plugin_system() first.")
            return self._plugin_manager

    def get_dependency_resolver(self) -> DependencyResolver:
        # 快速路径
        if self._dependency_resolver is not None:
            return self._dependency_resolver
        with self._lock:
            if self._dependency_resolver is None:
                raise RuntimeError("Dependency resolver not initialized.")
            return self._dependency_resolver

    def shutdown(self) -> None:
        with self._lock:
            if self._plugin_manager:
                self._plugin_manager.shutdown_all()
                self._plugin_manager = None

            self._dependency_resolver = None
            PluginRegistry.reset()
            logger.info("Plugin system shutdown")

    def reset(self) -> None:
        """Reset the cached state (mainly for tests)."""
        with self._lock:
            self._plugin_manager = None
            self._dependency_resolver = None


_holder = _PluginSystemHolder()


def init_plugin_system(
    plugin_dirs: list[str] | None = None,
    user_dirs: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> PluginLifecycleManager:
    """初始化插件系统，行为与重构前完全一致。"""
    return _holder.init(
        plugin_dirs=plugin_dirs,
        user_dirs=user_dirs,
        context=context,
    )


def get_plugin_manager() -> PluginLifecycleManager:
    """获取共享的 :class:`PluginLifecycleManager` 单例。

    Returns:
        :class:`PluginLifecycleManager` 实例。

    Raises:
        RuntimeError: 如果尚未通过 :func:`init_plugin_system` 初始化则抛出。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_plugin_manager)``。
    """
    return _holder.get_plugin_manager()


def get_dependency_resolver() -> DependencyResolver:
    """获取共享的 :class:`DependencyResolver` 单例。

    Returns:
        :class:`DependencyResolver` 实例。

    Raises:
        RuntimeError: 如果尚未初始化则抛出。
    """
    return _holder.get_dependency_resolver()


def shutdown_plugin_system() -> None:
    """关闭插件系统，行为与重构前完全一致。"""
    _holder.shutdown()


__all__ = [
    "PluginRegistry",
    "PluginDiscovery",
    "PluginLoader",
    "PluginLifecycleManager",
    "DependencyResolver",
    "init_plugin_system",
    "get_plugin_manager",
    "get_dependency_resolver",
    "shutdown_plugin_system",
]
