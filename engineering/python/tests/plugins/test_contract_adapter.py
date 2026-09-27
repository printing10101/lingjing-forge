"""PluginLifecycleManagerAdapter.uninstall 异步清理钩子回归测试。

背景：Python 3.11+ 移除了 asyncio.get_event_loop() 在主线程无事件循环时
的隐式创建（直接抛 RuntimeError）。修复前，uninstall() 中该异常被
except (RuntimeError, OSError) 吞掉 → 插件的异步 on_unload 清理钩子
被静默跳过，且 legacy uninstall_plugin 仍会执行，造成"卸载了但没清理"。

修复：无 loop 时改用 asyncio.run() 执行 on_unload，并确保 legacy
uninstall_plugin 一定执行（回归防护：不能 return 提前退出）。

覆盖分支：
    1. 无事件循环（3.11 核心场景）→ on_unload 执行 + uninstall_plugin 执行
    2. 有 loop 未运行 → run_until_complete 路径（旧行为保持）
    3. 有 loop 运行中 → 回退 warning，不执行 on_unload（旧行为保持）
    4. 无 loop + on_unload 抛 OSError → 不崩溃，uninstall_plugin 仍执行
"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.plugins.contract_adapter import PluginLifecycleManagerAdapter


def _make_adapter(*, on_unload: AsyncMock | None = None) -> PluginLifecycleManagerAdapter:
    """构造 adapter 实例（绕过 __init__，避免单例 registry 污染）。"""
    inst = PluginLifecycleManagerAdapter.__new__(PluginLifecycleManagerAdapter)
    inst._registry = MagicMock()
    inst._registry.get.return_value = MagicMock()  # metadata 非 None
    inst._mgr = MagicMock()
    inst._loader = MagicMock()
    inst._context_factory = MagicMock()
    inst._adapter_cache = {"demo": MagicMock(is_loaded=True, on_unload=on_unload or AsyncMock())}
    return inst


def _mgr_of(inst: PluginLifecycleManagerAdapter) -> Any:
    """读取 _mgr 的 mock 视图（类推断类型为真实管理器，运行时是 MagicMock）。"""
    return cast(Any, inst._mgr)


@pytest.mark.unit
class TestUninstallAsyncCleanup:
    def test_no_loop_runs_on_unload_and_legacy_uninstall(self) -> None:
        """核心场景（Python 3.11+）：无事件循环时 on_unload 必须执行，
        且 legacy uninstall_plugin 不能被跳过（回归防护）。"""
        on_unload = AsyncMock()
        inst = _make_adapter(on_unload=on_unload)

        inst.uninstall("demo")

        on_unload.assert_awaited_once()
        _mgr_of(inst).uninstall_plugin.assert_called_once_with("demo")

    def test_stopped_loop_uses_run_until_complete(self) -> None:
        """有 loop 但未运行：走 run_until_complete 路径，on_unload 执行。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            on_unload = AsyncMock()
            inst = _make_adapter(on_unload=on_unload)

            inst.uninstall("demo")

            on_unload.assert_awaited_once()
            _mgr_of(inst).uninstall_plugin.assert_called_once_with("demo")
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    def test_running_loop_falls_back_without_on_unload(self) -> None:
        """有 loop 运行中：不能同步 await，回退 legacy shutdown，
        on_unload 不执行，但 uninstall_plugin 仍执行。"""
        on_unload = AsyncMock()
        inst = _make_adapter(on_unload=on_unload)

        async def _inner() -> None:
            inst.uninstall("demo")

        asyncio.run(_inner())

        on_unload.assert_not_awaited()
        _mgr_of(inst).uninstall_plugin.assert_called_once_with("demo")

    def test_no_loop_on_unload_oserror_still_uninstalls(self) -> None:
        """无 loop + on_unload 抛 OSError：不崩溃、有 warning、
        legacy uninstall_plugin 仍执行。"""

        def _boom() -> AsyncMock:
            m = AsyncMock()
            m.side_effect = OSError("cleanup failed")
            return m

        inst = _make_adapter(on_unload=_boom())

        inst.uninstall("demo")  # 不应抛异常

        _mgr_of(inst).uninstall_plugin.assert_called_once_with("demo")
