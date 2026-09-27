"""插件契约单元测试.

对应 ADR-005 第 5 章 / app/contracts/plugin.py.

覆盖：
- PluginManifest（entrypoint 格式、required_contracts 格式、必填字段）
- PluginContext（plugin_id / data_dir 非空）
- ExtensionPointContribution（handler 或 component_url 必须提供其一）
- BUILTIN_EXTENSION_POINTS.all()
- BUILTIN_CAPABILITIES（11 个内置能力、默认授权策略）
- validate_capability_request
- IPlugin / IExtensionRegistry 抽象接口
"""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts.plugin import (
    BUILTIN_CAPABILITIES,
    BUILTIN_EXTENSION_POINTS,
    Capability,
    ExtensionPointContribution,
    IExtensionPoint,
    IExtensionRegistry,
    IPlugin,
    PluginContext,
    PluginManifest,
    validate_capability_request,
)


@pytest.mark.unit
@pytest.mark.contracts
class TestPluginManifest:
    """PluginManifest dataclass 构造校验."""

    def _make_manifest(self, **overrides) -> PluginManifest:
        defaults = dict(
            id="ltc-chatter",
            name="LTC 颤振预测插件",
            version="1.0.0",
            description="基于 LTC 网络的颤振预测",
            author="灵境制造团队",
            license="MIT",
            entrypoint="plugins.ltc_chatter.main:Plugin",
            required_contracts=["task@>=1.0", "dataset@>=1.0"],
        )
        defaults.update(overrides)
        return PluginManifest(**defaults)

    def test_valid_manifest(self):
        """合法 manifest 构造成功."""
        m = self._make_manifest()
        assert m.id == "ltc-chatter"
        assert m.entrypoint == "plugins.ltc_chatter.main:Plugin"

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="id"):
            self._make_manifest(id="")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            self._make_manifest(name="")

    def test_empty_version_rejected(self):
        with pytest.raises(ValueError, match="version"):
            self._make_manifest(version="")

    def test_empty_entrypoint_rejected(self):
        with pytest.raises(ValueError, match="entrypoint"):
            self._make_manifest(entrypoint="")

    def test_entrypoint_missing_colon_rejected(self):
        """entrypoint 必须是 module.path:ClassName 格式."""
        with pytest.raises(ValueError, match="entrypoint 格式"):
            self._make_manifest(entrypoint="plugins.ltc_chatter.main")  # 缺 :ClassName

    def test_required_contracts_missing_at_rejected(self):
        """required_contracts 条目必须含 @."""
        with pytest.raises(ValueError, match="required_contracts"):
            self._make_manifest(required_contracts=["task-1.0"])  # 缺 @

    def test_empty_required_contracts_allowed(self):
        """required_contracts 默认空列表."""
        m = self._make_manifest(required_contracts=[])
        assert m.required_contracts == []

    def test_optional_fields_default(self):
        """可选字段默认值."""
        m = self._make_manifest()
        assert m.required_capabilities == []
        assert m.optional_capabilities == []
        assert m.dependencies == []
        assert m.config_schema == {}
        assert m.homepage == ""
        assert m.tags == []


@pytest.mark.unit
@pytest.mark.contracts
class TestPluginContext:
    """PluginContext dataclass 构造校验."""

    def _make_context(self, **overrides) -> PluginContext:
        defaults = dict(
            plugin_id="ltc-chatter",
            config={"lr": 0.001},
            task_registry=None,
            dataset_store=None,
            observability=None,
            logger=None,
            data_dir="/tmp/plugins/ltc-chatter",
        )
        defaults.update(overrides)
        return PluginContext(**defaults)

    def test_valid_context(self):
        ctx = self._make_context()
        assert ctx.plugin_id == "ltc-chatter"

    def test_empty_plugin_id_rejected(self):
        with pytest.raises(ValueError, match="plugin_id"):
            self._make_context(plugin_id="")

    def test_empty_data_dir_rejected(self):
        with pytest.raises(ValueError, match="data_dir"):
            self._make_context(data_dir="")


@pytest.mark.unit
@pytest.mark.contracts
class TestExtensionPointContribution:
    """ExtensionPointContribution dataclass 构造校验."""

    def test_valid_with_handler(self):
        """提供 handler 的贡献合法."""

        def handler(payload: dict[str, Any]) -> Any:
            return payload

        contrib = ExtensionPointContribution(
            extension_point="core.task_handler",
            plugin_id="ltc-chatter",
            handler=handler,
        )
        assert contrib.handler is handler

    def test_valid_with_component_url(self):
        """提供 component_url 的前端贡献合法."""
        contrib = ExtensionPointContribution(
            extension_point="core.ui.workspace_panel",
            plugin_id="ui-plugin",
            component_url="http://localhost:3000/panel.js",
        )
        assert contrib.component_url is not None

    def test_both_handler_and_component_url_allowed(self):
        """同时提供 handler 和 component_url 是允许的."""
        contrib = ExtensionPointContribution(
            extension_point="core.task_handler",
            plugin_id="hybrid-plugin",
            handler=lambda p: p,
            component_url="http://localhost:3000/panel.js",
        )
        assert contrib.handler is not None
        assert contrib.component_url is not None

    def test_neither_handler_nor_component_url_rejected(self):
        """handler 和 component_url 必须提供其一."""
        with pytest.raises(ValueError, match="必须提供 handler 或 component_url"):
            ExtensionPointContribution(
                extension_point="core.task_handler",
                plugin_id="bad-plugin",
            )

    def test_empty_extension_point_rejected(self):
        with pytest.raises(ValueError, match="extension_point"):
            ExtensionPointContribution(
                extension_point="",
                plugin_id="p1",
                handler=lambda p: p,
            )

    def test_empty_plugin_id_rejected(self):
        with pytest.raises(ValueError, match="plugin_id"):
            ExtensionPointContribution(
                extension_point="core.task_handler",
                plugin_id="",
                handler=lambda p: p,
            )

    def test_default_props_and_metadata(self):
        contrib = ExtensionPointContribution(
            extension_point="core.task_handler",
            plugin_id="p1",
            handler=lambda p: p,
        )
        assert contrib.props == {}
        assert contrib.metadata == {}


@pytest.mark.unit
@pytest.mark.contracts
class TestBuiltinExtensionPoints:
    """BUILTIN_EXTENSION_POINTS 常量."""

    def test_all_extension_points_listed(self):
        """all() 返回全部 7 个扩展点."""
        points = BUILTIN_EXTENSION_POINTS.all()
        assert len(points) == 7

    @pytest.mark.parametrize(
        "attr,expected",
        [
            ("TASK_HANDLER", "core.task_handler"),
            ("DATASET_READER", "core.dataset_reader"),
            ("MODEL_REGISTRY", "core.model_registry"),
            ("WORKFLOW_TEMPLATE", "core.workflow_template"),
            ("UI_WORKSPACE_PANEL", "core.ui.workspace_panel"),
            ("UI_SETTINGS_TAB", "core.ui.settings_tab"),
            ("CHAT_COMMAND", "core.chat_command"),
        ],
    )
    def test_extension_point_value(self, attr, expected):
        assert getattr(BUILTIN_EXTENSION_POINTS, attr) == expected

    def test_all_returns_unique_values(self):
        """all() 返回值无重复."""
        points = BUILTIN_EXTENSION_POINTS.all()
        assert len(points) == len(set(points))


@pytest.mark.unit
@pytest.mark.contracts
class TestBuiltinCapabilities:
    """BUILTIN_CAPABILITIES 能力授权矩阵."""

    def test_capability_count(self):
        """共 11 个内置能力."""
        assert len(BUILTIN_CAPABILITIES) == 11

    @pytest.mark.parametrize(
        "cap_name,expected_grant",
        [
            ("task:submit", True),
            ("task:workflow:run", True),
            ("dataset:read", True),
            ("dataset:write", False),  # 写入需确认
            ("dataset:version:create", False),
            ("config:sweep", False),
            ("observability:snapshot:create", True),
            ("observability:trace:export", True),
            ("plugin:install", False),  # 安装其他插件需确认
            ("compute:gpu", True),
            ("network:egress", False),  # 外网访问需确认
        ],
    )
    def test_default_grant_policy(self, cap_name, expected_grant):
        """默认授权策略与 ADR-005 第 2.2 节对齐."""
        cap = BUILTIN_CAPABILITIES[cap_name]
        assert cap.default_grant is expected_grant

    def test_capability_is_frozen(self):
        """Capability 是 frozen dataclass."""
        cap = BUILTIN_CAPABILITIES["task:submit"]
        with pytest.raises(AttributeError):
            cap.default_grant = False  # type: ignore[misc]

    def test_capability_has_name_and_description(self):
        for cap in BUILTIN_CAPABILITIES.values():
            assert isinstance(cap.name, str) and cap.name
            assert isinstance(cap.description, str) and cap.description


@pytest.mark.unit
@pytest.mark.contracts
class TestValidateCapabilityRequest:
    """validate_capability_request 函数."""

    @pytest.mark.parametrize(
        "cap_name,expected",
        [
            ("task:submit", True),
            ("dataset:read", True),
            ("compute:gpu", True),
            ("network:egress", True),
            ("hack:admin", False),  # 未注册能力
            ("", False),
            ("task", False),  # 不完整
        ],
    )
    def test_capability_validation(self, cap_name, expected):
        assert validate_capability_request(cap_name) is expected


@pytest.mark.unit
@pytest.mark.contracts
class TestAbstractInterfaces:
    """IPlugin / IExtensionRegistry / IExtensionPoint 抽象接口."""

    def test_plugin_abstract(self):
        with pytest.raises(TypeError):
            IPlugin()  # type: ignore[abstract]

    def test_extension_registry_abstract(self):
        with pytest.raises(TypeError):
            IExtensionRegistry()  # type: ignore[abstract]

    def test_extension_point_abstract(self):
        with pytest.raises(TypeError):
            IExtensionPoint()  # type: ignore[abstract]

    def test_plugin_can_be_subclassed(self):
        """IPlugin 可被具体实现子类化."""

        class DummyPlugin(IPlugin):
            def manifest(self):
                return PluginManifest(
                    id="dummy",
                    name="Dummy",
                    version="1.0.0",
                    description="",
                    author="",
                    license="MIT",
                    entrypoint="dummy:Plugin",
                )

            async def on_load(self, context):
                return None

            async def on_unload(self):
                return None

            def health_check(self):
                return {"healthy": True, "checks": {}}

        plugin = DummyPlugin()
        assert plugin.health_check()["healthy"] is True

    def test_extension_registry_can_be_subclassed(self):
        """IExtensionRegistry 可被具体实现子类化."""

        class DummyRegistry(IExtensionRegistry):
            def register(self, extension_point, plugin_id, handler, *, metadata=None):
                return None

            def unregister(self, plugin_id, extension_point=None):
                return 0

            def list(self, extension_point):
                return []

            async def invoke(self, extension_point, payload):
                return []

        reg = DummyRegistry()
        assert reg is not None
