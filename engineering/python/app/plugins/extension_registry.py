"""扩展点注册表实现：IExtensionRegistry 契约实现.

对应 ADR-005 第 5 章 + core-contracts-design.md 阶段 3 p3-2。

本模块实现 ``app/contracts/plugin.py`` 定义的 ``IExtensionRegistry`` 抽象基类，
作为插件向核心注入能力的统一通道。

设计要点：
    1. 线程安全：所有读写操作通过 ``threading.RLock`` 保护（可重入锁，允许嵌套调用）
    2. handler 兼容同步与异步：``invoke`` 统一 await 异步结果，同步 handler 原样返回
    3. 顺序保证：同一扩展点内贡献按注册顺序调用
    4. 元信息隔离：``list`` 返回不含 handler 引用，防内存泄漏
    5. 插件卸载联动：``unregister(plugin_id)`` 一键取消该插件所有贡献
    6. 扩展点校验：内置扩展点常量校验，第三方扩展点放行但记录日志

契约稳定性：本实现属于"实现层"，不进入契约目录，可独立演进。
"""

from __future__ import annotations

import inspect
import logging
import threading
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

from app.contracts.plugin import (
    BUILTIN_EXTENSION_POINTS,
    ExtensionPointContribution,
    IExtensionRegistry,
)
import builtins

logger = logging.getLogger(__name__)


# 内部数据结构：注册表项


@dataclass
class _Registration:
    """扩展点注册项（内部数据结构）.

    持有 handler 引用与元信息，不对外暴露 handler。
    """

    extension_point: str
    plugin_id: str
    handler: Callable[[dict[str, Any]], Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    order: int = 0  # 注册顺序，用于稳定排序

    def to_public_dict(self) -> dict[str, Any]:
        """转换为对外可见的字典（剥离 handler 引用）."""
        return {
            "extension_point": self.extension_point,
            "plugin_id": self.plugin_id,
            "metadata": dict(self.metadata),
            "order": self.order,
        }


# ExtensionRegistry：IExtensionRegistry 实现


class ExtensionRegistry(IExtensionRegistry):
    """扩展点注册表实现.

    线程安全，支持同步/异步 handler 混合注册。所有 invoke 调用按注册顺序执行。

    使用示例::

        registry = ExtensionRegistry()
        registry.register(
            extension_point=BUILTIN_EXTENSION_POINTS.UI_WORKSPACE_PANEL,
            plugin_id="ltc_chatter",
            handler=lambda payload: {"panel": "LTC Chatter"},
            metadata={"title": "LTC 颤振预测", "icon": "wave"},
        )
        results = await registry.invoke(
            BUILTIN_EXTENSION_POINTS.UI_WORKSPACE_PANEL,
            {"workspace_id": "main"},
        )
    """

    def __init__(self) -> None:
        # extension_point list[_Registration]
        self._registrations: dict[str, list[_Registration]] = {}
        # plugin_id set[extension_point]（反向索引，加速 unregister）
        self._plugin_index: dict[str, set] = {}
        self._lock = threading.RLock()
        self._order_counter = 0

    # 注册

    def register(
        self,
        extension_point: str,
        plugin_id: str,
        handler: Callable[[dict[str, Any]], Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """注册扩展点贡献.

        Args:
            extension_point: 扩展点名称（建议使用 BUILTIN_EXTENSION_POINTS 常量）
            plugin_id: 贡献此内容的插件 ID
            handler: 调用处理器，接收 payload dict，返回任意结果
            metadata: 可选元信息（如前端用的 title/icon/component_url 等）

        Raises:
            ValueError: extension_point 或 plugin_id 为空，或 handler 不可调用
        """
        if not extension_point:
            raise ValueError("extension_point 不能为空")
        if not plugin_id:
            raise ValueError("plugin_id 不能为空")
        if not callable(handler):
            raise ValueError("handler 必须可调用")

        # 内置扩展点校验（仅警告，不阻塞第三方扩展点）
        if extension_point not in BUILTIN_EXTENSION_POINTS.all():
            logger.debug(
                "Registering contribution to non-builtin extension point '%s'",
                extension_point,
            )

        with self._lock:
            self._order_counter += 1
            reg = _Registration(
                extension_point=extension_point,
                plugin_id=plugin_id,
                handler=handler,
                metadata=metadata or {},
                order=self._order_counter,
            )

            if extension_point not in self._registrations:
                self._registrations[extension_point] = []
            self._registrations[extension_point].append(reg)

            if plugin_id not in self._plugin_index:
                self._plugin_index[plugin_id] = set()
            self._plugin_index[plugin_id].add(extension_point)

            logger.debug(
                "Registered contribution: plugin='%s' ext_point='%s' order=%d",
                plugin_id,
                extension_point,
                reg.order,
            )

    def register_contribution(self, contribution: ExtensionPointContribution) -> None:
        """从 ExtensionPointContribution 数据类注册（便捷方法）.

        适用于插件 on_load 时批量注册扩展点贡献的场景。
        """
        if contribution.handler is None:
            raise ValueError("ExtensionPointContribution.handler 不能为空（component_url 模式请用 register_component）")
        self.register(
            extension_point=contribution.extension_point,
            plugin_id=contribution.plugin_id,
            handler=contribution.handler,
            metadata={
                **contribution.metadata,
                "props": contribution.props,
                **({"component_url": contribution.component_url} if contribution.component_url else {}),
            },
        )

    def register_component(
        self,
        extension_point: str,
        plugin_id: str,
        component_url: str,
        *,
        props: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """注册前端组件贡献（无 handler，仅 component_url）.

        专门用于 UI_WORKSPACE_PANEL / UI_SETTINGS_TAB 等前端扩展点。
        handler 字段设置为返回 component_url 的存根函数，保持数据结构一致。
        """
        if not component_url:
            raise ValueError("component_url 不能为空")

        full_meta = dict(metadata or {})
        full_meta["component_url"] = component_url
        full_meta["props"] = props or {}

        # 前端组件无后端 handler，存一个返回元信息的存根
        def _component_stub(payload: dict[str, Any]) -> dict[str, Any]:
            return {"component_url": component_url, "props": props or {}}

        self.register(
            extension_point=extension_point,
            plugin_id=plugin_id,
            handler=_component_stub,
            metadata=full_meta,
        )

    # 取消注册

    def unregister(self, plugin_id: str, extension_point: str | None = None) -> int:
        """取消注册.

        Args:
            plugin_id: 插件 ID
            extension_point: 指定扩展点则只取消该扩展点的贡献，
                             None 则取消该插件所有贡献

        Returns:
            取消的注册项数量
        """
        if not plugin_id:
            return 0

        with self._lock:
            if extension_point is not None:
                return self._unregister_one(plugin_id, extension_point)
            return self._unregister_all(plugin_id)

    def _unregister_one(self, plugin_id: str, extension_point: str) -> int:
        """取消指定扩展点的注册."""
        regs = self._registrations.get(extension_point)
        if regs is None:
            return 0

        before = len(regs)
        regs[:] = [r for r in regs if r.plugin_id != plugin_id]
        removed = before - len(regs)

        if not regs:
            self._registrations.pop(extension_point, None)

        # 更新反向索引
        ext_set = self._plugin_index.get(plugin_id)
        if ext_set is not None:
            ext_set.discard(extension_point)
            if not ext_set:
                self._plugin_index.pop(plugin_id, None)

        if removed > 0:
            logger.debug(
                "Unregistered %d contribution(s): plugin='%s' ext_point='%s'",
                removed,
                plugin_id,
                extension_point,
            )
        return removed

    def _unregister_all(self, plugin_id: str) -> int:
        """取消插件所有扩展点的注册."""
        ext_set = self._plugin_index.pop(plugin_id, None)
        if ext_set is None:
            return 0

        total = 0
        for ext_point in list(ext_set):
            total += self._unregister_one(plugin_id, ext_point)
        return total

    # 查询

    def list(self, extension_point: str) -> builtins.list[dict[str, Any]]:
        """列出某扩展点的所有贡献元信息（按注册顺序）.

        返回的字典不包含 handler 引用，可安全序列化给前端。
        """
        with self._lock:
            regs = self._registrations.get(extension_point, [])
            # 复制避免外部修改
            return [r.to_public_dict() for r in regs]

    def list_by_plugin(self, plugin_id: str) -> builtins.list[dict[str, Any]]:
        """列出某插件的所有贡献（跨扩展点）."""
        with self._lock:
            ext_set = self._plugin_index.get(plugin_id, set())
            result: list[dict[str, Any]] = []
            for ext_point in ext_set:
                regs = self._registrations.get(ext_point, [])
                for r in regs:
                    if r.plugin_id == plugin_id:
                        result.append(r.to_public_dict())
            result.sort(key=lambda d: d["order"])
            return result

    def count(self, extension_point: str | None = None) -> int:
        """统计注册数. extension_point=None 时返回总数."""
        with self._lock:
            if extension_point is not None:
                return len(self._registrations.get(extension_point, []))
            return sum(len(regs) for regs in self._registrations.values())

    # 调用

    async def invoke(self, extension_point: str, payload: dict[str, Any]) -> builtins.list[Any]:
        """调用某扩展点的所有贡献，返回结果列表（按注册顺序）.

        同步 handler 直接调用，异步 handler 自动 await。
        单个 handler 失败不阻塞其他 handler，失败项记录日志后置为 None。
        """
        with self._lock:
            # 快照注册列表（避免调用期间被修改）
            regs_snapshot = list(self._registrations.get(extension_point, []))

        if not regs_snapshot:
            return []

        results: list[Any] = []
        for reg in regs_snapshot:
            try:
                result = reg.handler(payload)
                # 如果是 awaitable，await 它
                if inspect.isawaitable(result):
                    result = await result
                results.append(result)
            except (RuntimeError, ValueError, OSError, TypeError, KeyError) as e:
                # 单个 handler 失败不阻塞其他 handler
                logger.warning(
                    "Extension handler failed: plugin='%s' ext_point='%s': %s",
                    reg.plugin_id,
                    extension_point,
                    e,
                    exc_info=True,
                )
                results.append(None)

        return results

    async def invoke_first(
        self,
        extension_point: str,
        payload: dict[str, Any],
        *,
        default: Any = None,
    ) -> Any:
        """只调用第一个 handler，返回其结果.

        无注册时返回 default。适用于"扩展点只期望一个贡献"的场景
        （如 core.model_registry）。
        """
        with self._lock:
            regs_snapshot = list(self._registrations.get(extension_point, []))

        if not regs_snapshot:
            return default

        reg = regs_snapshot[0]
        try:
            result = reg.handler(payload)
            if inspect.isawaitable(result):
                result = await result
            return result
        except (RuntimeError, ValueError, OSError, TypeError, KeyError) as e:
            logger.warning(
                "Extension handler failed: plugin='%s' ext_point='%s': %s",
                reg.plugin_id,
                extension_point,
                e,
                exc_info=True,
            )
            return default

    # 批量管理

    def clear(self) -> None:
        """清空所有注册（主要用于测试与系统关闭）."""
        with self._lock:
            self._registrations.clear()
            self._plugin_index.clear()
            self._order_counter = 0

    def all_extension_points(self) -> builtins.list[str]:
        """返回当前有注册的扩展点列表."""
        with self._lock:
            return list(self._registrations.keys())

    def all_plugin_ids(self) -> builtins.list[str]:
        """返回当前有注册的插件 ID 列表."""
        with self._lock:
            return list(self._plugin_index.keys())


# 模块级单例访问


_registry_singleton: ExtensionRegistry | None = None
_registry_lock = threading.Lock()


def get_extension_registry() -> ExtensionRegistry:
    """获取扩展点注册表单例.

    延迟初始化，线程安全。核心层启动时调用此函数获取注册表实例，
    注入到 PluginContext 中供插件使用。
    """
    global _registry_singleton
    if _registry_singleton is not None:
        return _registry_singleton

    with _registry_lock:
        if _registry_singleton is None:
            _registry_singleton = ExtensionRegistry()
        return _registry_singleton


def reset_extension_registry() -> None:
    """重置单例（主要用于测试）."""
    global _registry_singleton
    with _registry_lock:
        _registry_singleton = None


__all__ = [
    "ExtensionRegistry",
    "get_extension_registry",
    "reset_extension_registry",
]
