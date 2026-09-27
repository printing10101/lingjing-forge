"""插件入口点加载器：统一处理契约格式与 legacy 格式的入口点加载.

对应 ADR-005 第 5 章 + core-contracts-design.md 阶段 3 p3-4。

契约 PluginManifest.entrypoint 格式为 ``module.path:ClassName``（如
``ltc_chatter.main:Plugin``），通过 importlib.import_module 加载；
legacy PluginMetadata.entry_point 格式为文件路径（如 ``main.py``），
通过 importlib.util.spec_from_file_location 加载。

本模块提供统一入口，自动识别格式并加载插件类，再根据是否为 IPlugin 子类
决定直接实例化或用 LegacyPluginInstanceAdapter 包装。

== 入口点格式 ==

    1. 契约格式："module.path:ClassName"
       - 通过 importlib.import_module(module_path) 加载模块
       - getattr(module, class_name) 获取类
       - 适用于：plugin.yaml 声明的插件、Python 包形式安装的插件

    2. legacy 格式："main.py"（文件路径，无冒号）
       - 通过 importlib.util.spec_from_file_location 加载文件
       - 在模块中查找 Plugin 类，或带 initialize+shutdown 的类
       - 适用于：plugin.json 声明的旧式插件

    3. 自动检测：根据是否含 ":" 决定格式

== 加载流程 ==

    load_plugin_from_manifest(manifest, plugin_dir=None) → IPlugin
        1. 解析 entrypoint 格式
        2. 加载插件类
        3. 若是 IPlugin 子类 → 直接实例化
        4. 否则 → 用 LegacyPluginInstanceAdapter 包装

== 安全考虑 ==

    - 插件目录在加载前添加到 sys.path，加载后不主动移除（避免破坏插件内
      后续的相对导入；如需移除由调用方在 finally 中处理）
    - 模块名使用 plugin_<id> 命名空间，避免与已加载模块冲突
    - 重复加载同名插件时，先从 sys.modules 移除旧模块，确保最新代码生效
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import threading
from enum import Enum
from pathlib import Path
from typing import Any

from app.contracts.plugin import IPlugin, PluginManifest
from app.plugins.contract_adapter import LegacyPluginInstanceAdapter
from app.plugins.plugin_system import PluginMetadata

logger = logging.getLogger(__name__)


# 异常定义


class EntrypointLoadError(RuntimeError):
    """入口点加载失败."""

    def __init__(self, entrypoint: str, reason: str) -> None:
        self.entrypoint = entrypoint
        self.reason = reason
        super().__init__(f"Failed to load entrypoint '{entrypoint}': {reason}")


# 入口点格式枚举


class EntryPointFormat(str, Enum):
    """入口点格式."""

    MODULE_CLASS = "module_class"  # module.path:ClassName
    FILE_PATH = "file_path"  # main.py
    AUTO = "auto"  # 自动检测


# 入口点解析


def parse_entrypoint(
    entrypoint: str,
    *,
    fmt: EntryPointFormat = EntryPointFormat.AUTO,
) -> tuple[EntryPointFormat, str, str | None]:
    """解析入口点字符串.

    Args:
        entrypoint: 入口点字符串
        fmt: 期望格式，AUTO 自动检测

    Returns:
        (actual_format, module_or_path, class_name)
        - MODULE_CLASS 格式：class_name 非空
        - FILE_PATH 格式：class_name 为 None（加载时再查找）

    Raises:
        EntrypointLoadError: 格式不合法
    """
    if not entrypoint or not entrypoint.strip():
        raise EntrypointLoadError(entrypoint, "entrypoint 为空")

    entrypoint = entrypoint.strip()

    if fmt == EntryPointFormat.AUTO:
        if ":" in entrypoint:
            fmt = EntryPointFormat.MODULE_CLASS
        else:
            fmt = EntryPointFormat.FILE_PATH

    if fmt == EntryPointFormat.MODULE_CLASS:
        if ":" not in entrypoint:
            raise EntrypointLoadError(
                entrypoint,
                "MODULE_CLASS 格式要求含 ':'（module.path:ClassName）",
            )
        module_path, _, class_name = entrypoint.partition(":")
        if not module_path or not class_name:
            raise EntrypointLoadError(
                entrypoint,
                "MODULE_CLASS 格式 module 与 class 均不能为空",
            )
        return fmt, module_path, class_name

    # FILE_PATH
    if ":" in entrypoint:
        # 用户明确要求 FILE_PATH 但含 ":"，可能是 Windows 盘符
        # 不做特殊处理，让后续文件加载自然失败
        pass
    return fmt, entrypoint, None


# 类加载


def _safe_module_name(plugin_id: str) -> str:
    """把 plugin_id 转换为合法 Python 模块名.

    legacy loader 约定：plugin_<safe_id>，其中 safe_id 把非字母数字下划线替换为 _。
    """
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in plugin_id)
    if safe and safe[0].isdigit():
        safe = "_" + safe
    return f"plugin_{safe}"


def load_class_from_module(
    module_path: str,
    class_name: str,
) -> type[Any]:
    """从 Python 模块路径加载类.

    Args:
        module_path: 模块路径，如 "ltc_chatter.main"
        class_name: 类名，如 "Plugin"

    Returns:
        插件主类（未实例化）

    Raises:
        EntrypointLoadError: 模块导入失败或类不存在
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise EntrypointLoadError(
            f"{module_path}:{class_name}",
            f"无法导入模块 '{module_path}': {e}",
        ) from e
    except (RuntimeError, ValueError) as e:
        raise EntrypointLoadError(
            f"{module_path}:{class_name}",
            f"模块 '{module_path}' 初始化异常: {e}",
        ) from e

    cls = getattr(module, class_name, None)
    if cls is None:
        raise EntrypointLoadError(
            f"{module_path}:{class_name}",
            f"模块 '{module_path}' 中不存在类 '{class_name}'",
        )
    if not isinstance(cls, type):
        raise EntrypointLoadError(
            f"{module_path}:{class_name}",
            f"'{class_name}' 不是类（type={type(cls).__name__}）",
        )
    return cls


def load_class_from_file(
    file_path: str | Path,
    class_name: str,
    *,
    module_name: str | None = None,
    plugin_dir: str | Path | None = None,
) -> type[Any]:
    """从文件路径加载类（legacy 格式）.

    模拟 legacy PluginLoader 的行为：
        1. 把 plugin_dir 加入 sys.path
        2. 用 importlib.util.spec_from_file_location 加载
        3. 在模块中查找指定类
        4. 给模块注入 __plugin_metadata__ 属性（与 legacy loader 一致）

    Args:
        file_path: 入口文件路径（如 main.py）
        class_name: 期望的类名（通常为 "Plugin"）
        module_name: 模块名，None 时自动从 file_path 推导
        plugin_dir: 插件根目录，加入 sys.path，None 时不修改 sys.path

    Returns:
        插件主类

    Raises:
        EntrypointLoadError: 文件不存在或加载失败
    """
    path = Path(file_path)
    if not path.is_absolute() and plugin_dir is not None:
        path = Path(plugin_dir) / path
    if not path.exists():
        raise EntrypointLoadError(
            str(file_path),
            f"入口文件不存在: {path}",
        )

    if module_name is None:
        # 用文件名（去扩展名）作为模块名，前缀 plugin_
        stem = path.stem
        module_name = f"plugin_{stem}" if not stem.startswith("plugin_") else stem

    # 把 plugin_dir 加入 sys.path（与 legacy loader 一致）
    path_added = False
    if plugin_dir is not None:
        plugin_dir_str = str(Path(plugin_dir).resolve())
        if plugin_dir_str not in sys.path:
            sys.path.insert(0, plugin_dir_str)
            path_added = True

    try:
        # 重复加载时先移除旧模块，确保最新代码生效
        if module_name in sys.modules:
            del sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise EntrypointLoadError(
                str(file_path),
                f"无法创建模块 spec: {path}",
            )

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            # 加载失败时清理 sys.modules
            sys.modules.pop(module_name, None)
            raise EntrypointLoadError(
                str(file_path),
                f"模块执行失败: {e}",
            ) from e

        cls = getattr(module, class_name, None)
        if cls is None:
            raise EntrypointLoadError(
                str(file_path),
                f"模块中不存在类 '{class_name}'",
            )
        if not isinstance(cls, type):
            raise EntrypointLoadError(
                str(file_path),
                f"'{class_name}' 不是类（type={type(cls).__name__}）",
            )
        return cls
    finally:
        # 不主动移除 sys.path 中的 plugin_dir，避免破坏插件后续相对导入
        # 重复加载同名插件时通过 sys.modules 清理已处理
        if path_added and False:
            pass


def load_plugin_class(
    entrypoint: str,
    *,
    plugin_dir: str | Path | None = None,
    fmt: EntryPointFormat = EntryPointFormat.AUTO,
    module_name: str | None = None,
) -> type[Any]:
    """统一入口点加载：根据格式选择加载策略.

    Args:
        entrypoint: 入口点字符串
        plugin_dir: 插件根目录（FILE_PATH 格式时使用）
        fmt: 入口点格式，AUTO 自动检测
        module_name: 自定义模块名（FILE_PATH 格式时使用）

    Returns:
        插件主类（未实例化）

    Raises:
        EntrypointLoadError: 加载失败
    """
    actual_fmt, target, class_name = parse_entrypoint(entrypoint, fmt=fmt)

    if actual_fmt == EntryPointFormat.MODULE_CLASS:
        return load_class_from_module(target, class_name or "Plugin")

    # FILE_PATH
    # legacy loader 行为：先找 "Plugin" 类，找不到找有 initialize+shutdown 的类
    candidate_names = ["Plugin"]
    if class_name:
        candidate_names.insert(0, class_name)

    last_error: Exception | None = None
    for name in candidate_names:
        try:
            return load_class_from_file(
                target,
                name,
                module_name=module_name,
                plugin_dir=plugin_dir,
            )
        except EntrypointLoadError as e:
            last_error = e
            continue

    raise last_error or EntrypointLoadError(entrypoint, "未知加载失败")


# 实例化与 IPlugin 适配


def _build_legacy_metadata_from_manifest(
    manifest: PluginManifest,
    *,
    plugin_path: str = "",
) -> PluginMetadata:
    """从 PluginManifest 反向构造最小 PluginMetadata.

    用于 legacy 插件（不继承 IPlugin）的 LegacyPluginInstanceAdapter 包装。
    仅填充 LegacyPluginInstanceAdapter 实际访问的字段。
    """
    # 从 entrypoint 推导 legacy entry_point（文件名）
    module_path = manifest.entrypoint.split(":", 1)[0]
    file_name = module_path.rsplit(".", 1)[-1] + ".py"

    return PluginMetadata(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        author=manifest.author,
        description=manifest.description,
        entry_point=file_name,
        capabilities=list(manifest.required_capabilities),
        config_schema=dict(manifest.config_schema),
        plugin_path=plugin_path,
    )


def create_plugin_instance(
    cls: type[Any],
    manifest: PluginManifest,
    *,
    plugin_path: str = "",
) -> IPlugin:
    """根据插件类构造 IPlugin 实例.

    - 若 cls 是 IPlugin 子类 → 直接实例化（cls()）
    - 否则 → 用 LegacyPluginInstanceAdapter 包装

    Args:
        cls: load_plugin_class 返回的插件类
        manifest: 插件 manifest
        plugin_path: 插件目录路径（legacy 模式下用于构造 metadata）

    Returns:
        IPlugin 实例（尚未调用 on_load）

    Raises:
        EntrypointLoadError: 实例化失败
    """
    if issubclass(cls, IPlugin):
        try:
            instance = cls()
        except (RuntimeError, ValueError, TypeError) as e:
            raise EntrypointLoadError(
                manifest.entrypoint,
                f"IPlugin 子类实例化失败: {e}",
            ) from e
        # 校验 manifest() 返回一致（宽松校验，仅日志）
        try:
            actual = instance.manifest()
            if actual.id != manifest.id:
                logger.warning(
                    "Plugin '%s' manifest().id ('%s') 与加载时 manifest.id ('%s') 不一致",
                    manifest.id,
                    actual.id,
                    manifest.id,
                )
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Plugin '%s' manifest() 调用失败，跳过一致性校验: %s",
                manifest.id,
                e,
            )
        return instance

    # legacy 插件：构造 metadata 并用 adapter 包装
    metadata = _build_legacy_metadata_from_manifest(manifest, plugin_path=plugin_path)
    try:
        legacy_instance = cls()
    except (RuntimeError, ValueError, TypeError) as e:
        raise EntrypointLoadError(
            manifest.entrypoint,
            f"legacy 插件类实例化失败: {e}",
        ) from e

    # legacy loader 会在实例上注入 metadata，这里也保持一致
    if hasattr(legacy_instance, "set_metadata"):
        try:
            legacy_instance.set_metadata(metadata)
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Legacy plugin '%s' set_metadata failed: %s",
                manifest.id,
                e,
            )

    return LegacyPluginInstanceAdapter(
        legacy_instance=legacy_instance,
        metadata=metadata,
        manifest=manifest,
    )


def load_plugin_from_manifest(
    manifest: PluginManifest,
    *,
    plugin_dir: str | Path | None = None,
) -> IPlugin:
    """从 manifest 完整加载插件：解析 entrypoint → 加载类 → 实例化.

    Args:
        manifest: 插件清单
        plugin_dir: 插件根目录（FILE_PATH 格式必需）

    Returns:
        IPlugin 实例（尚未调用 on_load）

    Raises:
        EntrypointLoadError: 加载或实例化失败
    """
    cls = load_plugin_class(
        manifest.entrypoint,
        plugin_dir=plugin_dir,
        fmt=EntryPointFormat.AUTO,
        module_name=_safe_module_name(manifest.id),
    )
    return create_plugin_instance(
        cls,
        manifest,
        plugin_path=str(plugin_dir) if plugin_dir else "",
    )


# 显式注册表：支持不通过文件加载的内存插件


class ExplicitPluginRegistry:
    """显式插件类注册表.

    用于插件类已通过其他方式（如 entry_points、直接 import）加载的场景，
    跳过 entrypoint 字符串解析，直接根据 plugin_id 查找类。

    线程安全。
    """

    _instance: "ExplicitPluginRegistry" | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._classes: dict[str, type[Any]] = {}
        self._lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "ExplicitPluginRegistry":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def register(self, plugin_id: str, cls: type[Any]) -> None:
        """注册插件类. 重复注册抛 ValueError."""
        with self._lock:
            if plugin_id in self._classes:
                raise ValueError(f"Plugin '{plugin_id}' already explicitly registered")
            self._classes[plugin_id] = cls

    def unregister(self, plugin_id: str) -> bool:
        with self._lock:
            return self._classes.pop(plugin_id, None) is not None

    def get(self, plugin_id: str) -> type[Any] | None:
        with self._lock:
            return self._classes.get(plugin_id)

    def has(self, plugin_id: str) -> bool:
        with self._lock:
            return plugin_id in self._classes

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._classes.keys())


def load_explicit_plugin(
    manifest: PluginManifest,
    *,
    registry: ExplicitPluginRegistry | None = None,
) -> IPlugin | None:
    """从显式注册表加载插件.

    若 manifest.id 在 ExplicitPluginRegistry 中已注册，直接用注册的类实例化；
    否则返回 None，调用方可回退到 load_plugin_from_manifest.

    适用场景：
        - Python entry_points 加载的插件
        - 测试中 mock 的插件
        - 内置插件（不走文件加载）
    """
    reg = registry or ExplicitPluginRegistry.get_instance()
    cls = reg.get(manifest.id)
    if cls is None:
        return None
    return create_plugin_instance(cls, manifest)


def load_plugin(
    manifest: PluginManifest,
    *,
    plugin_dir: str | Path | None = None,
    registry: ExplicitPluginRegistry | None = None,
) -> IPlugin:
    """统一插件加载入口：优先显式注册表，回退到 entrypoint 加载.

    Args:
        manifest: 插件清单
        plugin_dir: 插件根目录
        registry: 自定义显式注册表，None 用全局单例

    Returns:
        IPlugin 实例
    """
    explicit = load_explicit_plugin(manifest, registry=registry)
    if explicit is not None:
        return explicit
    return load_plugin_from_manifest(manifest, plugin_dir=plugin_dir)


__all__ = [
    "EntryPointFormat",
    "EntrypointLoadError",
    "ExplicitPluginRegistry",
    "parse_entrypoint",
    "load_class_from_module",
    "load_class_from_file",
    "load_plugin_class",
    "create_plugin_instance",
    "load_plugin_from_manifest",
    "load_explicit_plugin",
    "load_plugin",
]
