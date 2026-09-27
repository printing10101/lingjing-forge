"""数据飞轮插件（plugins/data_flywheel）单元测试.

对应 core-contracts-design.md 阶段 4 p4-1。

覆盖：
    - plugin.yaml 被 manifest_loader 正确加载（字段、契约、能力声明）
    - entrypoint ``plugins.data_flywheel.main:Plugin`` 被 entrypoint_loader 正确实例化
    - on_load 后注册 3 个扩展点贡献（UI_WORKSPACE_PANEL + WORKFLOW_TEMPLATE + TASK_HANDLER）
    - invoke WORKFLOW_TEMPLATE 返回 ready 状态描述（p4-3 已填充完整 WorkflowSpec）
    - on_unload 后扩展点贡献被清理
    - health_check 在未加载/已加载两种状态下的正确性
    - config_schema 4 个顶层 section 完整（feedback_collection / model_iteration / hot_update / metrics）

本测试不依赖网络、数据库、torch。所有断言基于插件骨架的纯逻辑行为。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from app.contracts.plugin import (
    BUILTIN_CAPABILITIES,
    BUILTIN_EXTENSION_POINTS,
    PluginContext,
    PluginManifest,
)
from app.plugins.entrypoint_loader import (
    EntryPointFormat,
    load_plugin_class,
)
from app.plugins.extension_registry import (
    ExtensionRegistry,
    reset_extension_registry,
)
from app.plugins.manifest_loader import load_manifest_from_dir

# 数据飞轮插件目录（python/plugins/data_flywheel）
_PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugins" / "data_flywheel"


# Fixtures


@pytest.fixture
def fresh_registry() -> ExtensionRegistry:
    """每个测试使用独立的 ExtensionRegistry 实例（避免单例污染）."""
    reset_extension_registry()
    registry = ExtensionRegistry()
    # 替换单例，让插件 on_load 内部 get_extension_registry() 拿到本测试实例
    import app.plugins.extension_registry as er_mod

    er_mod._registry_singleton = registry
    yield registry
    reset_extension_registry()


@pytest.fixture
def plugin_instance(fresh_registry: ExtensionRegistry):
    """加载并返回 Plugin 类实例（未调用 on_load）.

    通过 entrypoint_loader 加载，模拟生产环境的插件加载路径，
    而非直接 import，确保 entrypoint 字符串本身被验证。
    """
    cls = load_plugin_class(
        "plugins.data_flywheel.main:Plugin",
        fmt=EntryPointFormat.MODULE_CLASS,
    )
    instance = cls()
    return instance


def _make_context(plugin_id: str = "data_flywheel") -> PluginContext:
    """构造完整的 PluginContext（所有依赖项均非 None）."""
    return PluginContext(
        plugin_id=plugin_id,
        config={},
        task_registry=object(),  # 非 None 即可
        dataset_store=object(),
        observability=object(),
        logger=logging.getLogger("test.data_flywheel"),
        data_dir=str(_PLUGIN_DIR / "_test_data"),
    )


def _run(coro):
    """同步运行异步协程（测试用）.

    Python 3.11+ 移除了 get_event_loop() 在主线程无 loop 时的隐式创建，
    统一用 asyncio.run（每次新建并关闭 loop，对独立测试安全）。
    """
    return asyncio.run(coro)


# manifest 加载与字段校验


@pytest.mark.unit
@pytest.mark.contracts
class TestDataFlywheelManifest:
    """plugin.yaml manifest 加载与字段校验."""

    def test_plugin_dir_exists(self):
        """插件目录存在（防止路径漂移）."""
        assert _PLUGIN_DIR.exists(), f"插件目录不存在: {_PLUGIN_DIR}"
        assert (_PLUGIN_DIR / "plugin.yaml").exists()

    def test_manifest_loads_without_error(self):
        """plugin.yaml 能被 load_manifest_from_dir 正确加载."""
        manifest = load_manifest_from_dir(_PLUGIN_DIR)
        assert isinstance(manifest, PluginManifest)

    def test_manifest_core_fields(self):
        """manifest 核心字段值正确."""
        m = load_manifest_from_dir(_PLUGIN_DIR)
        assert m.id == "data_flywheel"
        assert m.name == "数据飞轮"
        assert m.version == "0.1.0"
        assert m.author == "灵境制造团队"
        assert m.license == "MIT"
        assert m.entrypoint == "plugins.data_flywheel.main:Plugin"

    def test_manifest_required_contracts_format(self):
        """required_contracts 每项均为 name@version 格式."""
        m = load_manifest_from_dir(_PLUGIN_DIR)
        assert len(m.required_contracts) == 3
        for req in m.required_contracts:
            assert "@" in req, f"contract 条目缺少 @: {req}"
            name, _, version = req.partition("@")
            assert name, f"contract 名称为空: {req}"
            assert version, f"contract 版本为空: {req}"

    def test_manifest_required_capabilities_in_builtin(self):
        """required_capabilities 全部在内置能力清单中."""
        m = load_manifest_from_dir(_PLUGIN_DIR)
        assert len(m.required_capabilities) >= 1
        for cap in m.required_capabilities:
            assert cap in BUILTIN_CAPABILITIES, f"required_capability '{cap}' 不在内置能力清单"

    def test_manifest_optional_capabilities_in_builtin(self):
        """optional_capabilities 全部在内置能力清单中."""
        m = load_manifest_from_dir(_PLUGIN_DIR)
        for cap in m.optional_capabilities:
            assert cap in BUILTIN_CAPABILITIES, f"optional_capability '{cap}' 不在内置能力清单"

    def test_manifest_config_schema_top_level_sections(self):
        """config_schema 包含 4 个顶层 section 且全部 required."""
        m = load_manifest_from_dir(_PLUGIN_DIR)
        assert m.config_schema.get("type") == "object"
        props = m.config_schema.get("properties", {})
        required = m.config_schema.get("required", [])

        expected_sections = [
            "feedback_collection",
            "model_iteration",
            "hot_update",
            "metrics",
        ]
        for section in expected_sections:
            assert section in props, f"config_schema 缺少 section: {section}"
            assert section in required, f"section '{section}' 未列入 required"

    def test_config_schema_feedback_collection_defaults(self):
        """feedback_collection 默认值符合工程约束（窗口/最小样本/批次）."""
        m = load_manifest_from_dir(_PLUGIN_DIR)
        fc = m.config_schema["properties"]["feedback_collection"]["properties"]
        assert fc["window_hours"]["default"] == 24
        assert fc["min_samples_for_training"]["default"] == 50
        assert fc["batch_size"]["default"] == 100

    def test_config_schema_model_iteration_safety(self):
        """model_iteration 默认 auto_retrain=false（需人工触发），防止过拟合训练循环."""
        m = load_manifest_from_dir(_PLUGIN_DIR)
        mi = m.config_schema["properties"]["model_iteration"]["properties"]
        assert mi["auto_retrain"]["default"] is False
        assert mi["max_retrain_per_day"]["default"] == 1
        assert mi["min_improvement"]["default"] == 0.02

    def test_config_schema_hot_update_canary_bounds(self):
        """hot_update canary_ratio 在 [0, 1] 区间，默认 10% 灰度."""
        m = load_manifest_from_dir(_PLUGIN_DIR)
        hu = m.config_schema["properties"]["hot_update"]["properties"]
        assert hu["canary_ratio"]["default"] == 0.1
        assert hu["canary_ratio"]["minimum"] == 0
        assert hu["canary_ratio"]["maximum"] == 1
        assert hu["rollback_on_failure"]["default"] is True


# 入口点加载与 IPlugin 契约


@pytest.mark.unit
@pytest.mark.contracts
class TestDataFlywheelPluginLoad:
    """entrypoint 加载与 IPlugin 契约实现."""

    def test_entrypoint_loads_plugin_class(self):
        """entrypoint 字符串能通过 entrypoint_loader 加载到 Plugin 类."""
        cls = load_plugin_class(
            "plugins.data_flywheel.main:Plugin",
            fmt=EntryPointFormat.MODULE_CLASS,
        )
        assert isinstance(cls, type)
        assert cls.__name__ == "Plugin"

    def test_plugin_instance_implements_IPlugin(self, plugin_instance):
        """Plugin 实例实现 IPlugin 契约（manifest/on_load/on_unload/health_check）."""
        from app.contracts.plugin import IPlugin

        assert isinstance(plugin_instance, IPlugin)

    def test_plugin_manifest_method_returns_cached(self, plugin_instance):
        """manifest() 返回 PluginManifest 且带缓存（多次调用同一对象）."""
        m1 = plugin_instance.manifest()
        m2 = plugin_instance.manifest()
        assert isinstance(m1, PluginManifest)
        assert m1 is m2, "manifest() 应缓存结果"
        assert m1.id == "data_flywheel"


# 生命周期：on_load / on_unload / health_check


@pytest.mark.unit
@pytest.mark.contracts
class TestDataFlywheelPluginLifecycle:
    """插件生命周期测试."""

    def test_health_check_before_load_unhealthy(self, plugin_instance):
        """未 on_load 时 health_check 应返回 unhealthy."""
        result = plugin_instance.health_check()
        assert result["healthy"] is False
        assert result["checks"]["manifest_loaded"] is False
        assert result["checks"]["context_injected"] is False

    def test_on_load_registers_three_extension_points(self, plugin_instance, fresh_registry: ExtensionRegistry):
        """on_load 后注册 UI_WORKSPACE_PANEL + WORKFLOW_TEMPLATE + TASK_HANDLER 三个扩展点."""
        ctx = _make_context()
        _run(plugin_instance.on_load(ctx))

        # UI_WORKSPACE_PANEL 应有 1 个贡献
        panel_contribs = fresh_registry.list(BUILTIN_EXTENSION_POINTS.UI_WORKSPACE_PANEL)
        assert len(panel_contribs) == 1
        assert panel_contribs[0]["plugin_id"] == "data_flywheel"

        # WORKFLOW_TEMPLATE 应有 1 个贡献
        tmpl_contribs = fresh_registry.list(BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE)
        assert len(tmpl_contribs) == 1
        assert tmpl_contribs[0]["plugin_id"] == "data_flywheel"

        # TASK_HANDLER 应有 2 个贡献（p4-2 反馈提交 + p4-5 热更新，均来自 data_flywheel）
        handler_contribs = fresh_registry.list(BUILTIN_EXTENSION_POINTS.TASK_HANDLER)
        assert len(handler_contribs) == 2
        assert {c["plugin_id"] for c in handler_contribs} == {"data_flywheel"}
        task_types = {c["metadata"]["task_type"] for c in handler_contribs}
        assert task_types == {"submit_feedback", "hot_update_manager"}

    def test_on_load_workspace_panel_metadata(self, plugin_instance, fresh_registry: ExtensionRegistry):
        """UI_WORKSPACE_PANEL 贡献携带 title/icon/component_url 元信息."""
        ctx = _make_context()
        _run(plugin_instance.on_load(ctx))

        contribs = fresh_registry.list(BUILTIN_EXTENSION_POINTS.UI_WORKSPACE_PANEL)
        meta = contribs[0]["metadata"]
        assert meta["component_url"] == "FlywheelDashboard.vue"
        assert meta["title"] == "数据飞轮"
        assert meta["icon"] == "flywheel"
        assert meta["props"]["layout"] == "tabs"
        # 4 个 tab（概览/反馈采集/模型迭代/飞轮指标）
        tab_ids = [t["id"] for t in meta["props"]["tabs"]]
        assert tab_ids == ["overview", "feedback", "models", "metrics"]

    def test_on_load_workflow_template_metadata(self, plugin_instance, fresh_registry: ExtensionRegistry):
        """WORKFLOW_TEMPLATE 贡献携带 template_name/description/version."""
        ctx = _make_context()
        _run(plugin_instance.on_load(ctx))

        contribs = fresh_registry.list(BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE)
        meta = contribs[0]["metadata"]
        assert meta["template_name"] == "model_iteration_pipeline"
        assert "训练" in meta["description"]
        assert meta["version"] == "0.1.0"

    def test_health_check_after_load_healthy(self, plugin_instance, fresh_registry: ExtensionRegistry):
        """on_load 后（context 完整）health_check 应返回 healthy."""
        ctx = _make_context()
        _run(plugin_instance.on_load(ctx))

        result = plugin_instance.health_check()
        assert result["healthy"] is True
        assert result["checks"]["manifest_loaded"] is True
        assert result["checks"]["context_injected"] is True
        assert result["checks"]["dataset_store_available"] is True
        assert result["checks"]["task_registry_available"] is True
        assert result["checks"]["observability_available"] is True

    def test_health_check_partial_context(self, plugin_instance, fresh_registry: ExtensionRegistry):
        """context 部分依赖为 None 时 health_check 应返回 unhealthy."""
        ctx = PluginContext(
            plugin_id="data_flywheel",
            config={},
            task_registry=object(),
            dataset_store=None,  # 缺失
            observability=object(),
            logger=logging.getLogger("test"),
            data_dir=str(_PLUGIN_DIR),
        )
        _run(plugin_instance.on_load(ctx))

        result = plugin_instance.health_check()
        assert result["healthy"] is False
        assert result["checks"]["dataset_store_available"] is False

    def test_on_unload_clears_extensions(self, plugin_instance, fresh_registry: ExtensionRegistry):
        """on_unload 后所有扩展点贡献被清理."""
        ctx = _make_context()
        _run(plugin_instance.on_load(ctx))

        # 加载后应有 4 个贡献（UI_WORKSPACE_PANEL + WORKFLOW_TEMPLATE + 2×TASK_HANDLER）
        assert fresh_registry.count() == 4

        _run(plugin_instance.on_unload())

        # 卸载后应为 0
        assert fresh_registry.count() == 0
        assert fresh_registry.count(BUILTIN_EXTENSION_POINTS.UI_WORKSPACE_PANEL) == 0
        assert fresh_registry.count(BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE) == 0
        assert fresh_registry.count(BUILTIN_EXTENSION_POINTS.TASK_HANDLER) == 0

    def test_on_unload_idempotent(self, plugin_instance, fresh_registry):
        """on_unload 幂等（重复调用不报错）."""
        ctx = _make_context()
        _run(plugin_instance.on_load(ctx))
        _run(plugin_instance.on_unload())
        # 再次卸载不应抛错
        _run(plugin_instance.on_unload())
        assert fresh_registry.count() == 0


# 扩展点调用：WORKFLOW_TEMPLATE 返回 ready 状态（p4-3 已填充完整 WorkflowSpec）


@pytest.mark.unit
@pytest.mark.contracts
class TestDataFlywheelWorkflowTemplateHandler:
    """工作流模板扩展点处理器测试."""

    def test_invoke_workflow_template_returns_ready(self, plugin_instance, fresh_registry: ExtensionRegistry):
        """invoke WORKFLOW_TEMPLATE 返回 status=ready 的模板描述（p4-3 已填充完整 spec）."""
        ctx = _make_context()
        _run(plugin_instance.on_load(ctx))

        results = _run(
            fresh_registry.invoke(
                BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE,
                {"template_name": "model_iteration_pipeline"},
            )
        )
        assert len(results) == 1
        result = results[0]
        assert result["template_name"] == "model_iteration_pipeline"
        assert result["status"] == "ready"
        assert result["spec"] is not None  # p4-3 已填充完整 WorkflowSpec
        assert "训练" in result["description"]

    def test_invoke_workflow_template_via_invoke_first(self, plugin_instance, fresh_registry: ExtensionRegistry):
        """invoke_first 只调用第一个 handler，返回非 None."""
        ctx = _make_context()
        _run(plugin_instance.on_load(ctx))

        result = _run(
            fresh_registry.invoke_first(
                BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE,
                {},
                default={"fallback": True},
            )
        )
        assert result is not None
        assert result["template_name"] == "model_iteration_pipeline"
        assert "fallback" not in result

    def test_invoke_first_without_registration_returns_default(self, fresh_registry: ExtensionRegistry):
        """无注册时 invoke_first 返回 default."""
        result = _run(
            fresh_registry.invoke_first(
                BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE,
                {},
                default={"default": True},
            )
        )
        assert result == {"default": True}


# 加载-卸载循环：重复加载一致性


@pytest.mark.unit
@pytest.mark.contracts
class TestDataFlywheelReloadCycle:
    """插件加载-卸载-再加载循环一致性."""

    def test_reload_after_unload_registers_again(self, plugin_instance, fresh_registry: ExtensionRegistry):
        """卸载后重新 on_load 仍能正确注册扩展点."""
        ctx = _make_context()

        # 第一次加载
        _run(plugin_instance.on_load(ctx))
        assert fresh_registry.count() == 4

        # 卸载
        _run(plugin_instance.on_unload())
        assert fresh_registry.count() == 0

        # 重新加载
        _run(plugin_instance.on_load(ctx))
        assert fresh_registry.count() == 4
        # 重新加载后 invoke 仍可正常工作
        result = _run(fresh_registry.invoke_first(BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE, {}))
        assert result is not None
        assert result["status"] == "ready"
