"""插件系统接线测试（遗留项②：init_plugin_system 安全接线）。

验证：
- init_plugin_system() 无参调用安全（空 plugin_dirs → 0 插件，不触发 torch 依赖）
- 初始化后 get_plugin_manager() 可用（不再抛 RuntimeError）
- shutdown_plugin_system() 幂等（未初始化时调用不抛错）
- 插件 API 返回空而非异常（前端插件页不再死数据）

接线代码位于 app/main.py startup_event（Step 5）与 shutdown_event（5.5），
本测试直接验证其调用的底层函数行为（mini-app 不依赖 cadquery）。
"""

from __future__ import annotations

import pytest

from app.plugins import plugin_manager as pm


@pytest.fixture(autouse=True)
def _reset_plugin_system():
    """每个用例前后重置插件系统单例，避免相互污染。"""
    pm._holder.reset()
    yield
    pm._holder.reset()


@pytest.mark.unit
@pytest.mark.plugins
class TestPluginSystemWiring:
    def test_init_without_args_is_safe(self):
        """无参 init：空 plugin_dirs → 0 插件，管理器可用。"""
        mgr = pm.init_plugin_system()
        assert mgr is not None
        assert pm.get_plugin_manager() is mgr
        # 空注册表（不触发 torch 依赖插件）
        assert mgr._registry.list_plugins() == []

    def test_get_plugin_manager_before_init_raises(self):
        """未初始化时 get_plugin_manager 抛 RuntimeError（接线前的老行为）。"""
        with pytest.raises(RuntimeError):
            pm.get_plugin_manager()

    def test_shutdown_is_idempotent(self):
        """shutdown 后 get_plugin_manager 再次抛错（状态复位），且二次 shutdown 不抛。"""
        pm.init_plugin_system()
        pm.shutdown_plugin_system()
        with pytest.raises(RuntimeError):
            pm.get_plugin_manager()
        # 幂等：再次 shutdown 不抛
        pm.shutdown_plugin_system()

    def test_plugins_api_returns_empty_after_init(self):
        """初始化后插件列表 API 数据源可用（返回空而非异常）。"""
        pm.init_plugin_system()
        mgr = pm.get_plugin_manager()
        plugins = mgr._registry.list_plugins()
        assert isinstance(plugins, list)
        assert len(plugins) == 0
