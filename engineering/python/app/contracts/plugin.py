"""插件契约：定义插件、扩展点、生命周期的统一接口.

对应 ADR-005 第 5 章。本文件只定义接口与数据结构，不包含实现。
现有 app/plugins/plugin_system.py 通过 contract_adapter 适配此契约。

契约稳定性：Stable（v1.0.0），向后兼容扩展。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable
import builtins


@dataclass
class PluginManifest:
    """插件清单契约（plugin.yaml 的 Python 投影）.

    required_contracts 格式：["task@>=1.0", "dataset@>=1.0"]
        <contract_name>@<semver_constraint>
    """

    id: str
    name: str
    version: str
    description: str
    author: str
    license: str
    entrypoint: str  # Python 模块路径，如 "plugins.ltc_chatter.main:Plugin"
    required_contracts: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    optional_capabilities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)  # 其他插件 id
    config_schema: dict[str, Any] = field(default_factory=dict)  # JSON Schema
    homepage: str = ""
    tags: list[str] = field(default_factory=list)
    # ADR-010: 插件可声明贡献的工作流模板（相对插件根目录的 YAML 文件路径列表）
    # 加载流程：插件 on_load 时读取这些 YAML，通过 IExtensionRegistry.register
    # 注册到 BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE 扩展点
    workflow_templates: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("PluginManifest.id 不能为空")
        if not self.name:
            raise ValueError("PluginManifest.name 不能为空")
        if not self.version:
            raise ValueError("PluginManifest.version 不能为空")
        if not self.entrypoint:
            raise ValueError("PluginManifest.entrypoint 不能为空")
        if ":" not in self.entrypoint:
            raise ValueError(f"PluginManifest.entrypoint 格式错误（应为 module.path:ClassName）: {self.entrypoint}")
        # 校验 required_contracts 格式
        for req in self.required_contracts:
            if "@" not in req:
                raise ValueError(f"required_contracts 条目格式错误（应为 name@version）: {req}")
        # workflow_templates 必须是字符串列表
        if not isinstance(self.workflow_templates, list) or not all(
            isinstance(t, str) for t in self.workflow_templates
        ):
            raise ValueError("PluginManifest.workflow_templates 必须是字符串列表（YAML 文件相对路径）")


class IPlugin(ABC):
    """插件主类契约.

    插件通过实现此抽象类被加载。entrypoint 指向的类必须继承 IPlugin。
    生命周期：install → enable → load → register → unload → disable → uninstall
    """

    @abstractmethod
    def manifest(self) -> PluginManifest:
        """返回插件清单。"""

    @abstractmethod
    async def on_load(self, context: "PluginContext") -> None:
        """插件加载时调用.

        插件应在此处：
            - 注册任务处理器（context.task_registry.register）
            - 注册扩展点贡献（context.extension_registry.register）
            - 初始化资源（连接池/模型加载等）
        """

    @abstractmethod
    async def on_unload(self) -> None:
        """插件卸载时调用.

        插件应在此处：
            - 释放资源（关闭连接/文件句柄）
            - 取消注册（由核心层自动处理，无需手动）
        """

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """健康检查.

        返回格式：
            {
                "healthy": bool,
                "checks": {"db": True, "model_loaded": True, ...},
                "message": str (可选)
            }
        """


@dataclass
class PluginContext:
    """插件运行时上下文（核心层在 on_load 时注入）.

    所有字段都是核心层提供的接口实例，插件通过此 context 与核心交互。
    """

    plugin_id: str
    config: dict[str, Any]
    task_registry: Any  # ITaskRegistry
    dataset_store: Any  # IDatasetStore
    observability: Any  # IObservabilitySink
    logger: Any  # logging.Logger
    data_dir: str  # 插件私有数据目录

    def __post_init__(self) -> None:
        if not self.plugin_id:
            raise ValueError("PluginContext.plugin_id 不能为空")
        if not self.data_dir:
            raise ValueError("PluginContext.data_dir 不能为空")


class IExtensionPoint(ABC):
    """扩展点契约.

    扩展点是核心定义的"插槽"，插件通过实现此接口向核心注入能力。
    与 IExtensionRegistry 的区别：扩展点是核心定义的"插槽"，
    扩展注册表是"插槽管理器"。
    """

    @abstractmethod
    def name(self) -> str:
        """扩展点名称（如 core.task_handler）。"""

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """扩展点接受的输入 schema（JSON Schema）。"""

    @abstractmethod
    async def invoke(self, payload: dict[str, Any]) -> Any:
        """调用扩展点。"""


@dataclass
class ExtensionPointContribution:
    """扩展点贡献契约（插件向扩展点注册的内容）."""

    extension_point: str  # 扩展点名称
    plugin_id: str  # 贡献此内容的插件 id
    handler: Callable[[dict[str, Any]], Any] | None = None  # 后端调用处理器
    # 前端扩展点用 component_url 加载远程组件（当前阶段：仅本地插件）
    component_url: str | None = None
    props: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.extension_point:
            raise ValueError("ExtensionPointContribution.extension_point 不能为空")
        if not self.plugin_id:
            raise ValueError("ExtensionPointContribution.plugin_id 不能为空")
        if self.handler is None and self.component_url is None:
            raise ValueError("ExtensionPointContribution 必须提供 handler 或 component_url 之一")


class IExtensionRegistry(ABC):
    """扩展点注册表契约.

    实现见 app/plugins/extension_registry.py（阶段 3 交付）。
    """

    @abstractmethod
    def register(
        self,
        extension_point: str,
        plugin_id: str,
        handler: Callable[[dict[str, Any]], Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """注册扩展点贡献."""

    @abstractmethod
    def unregister(self, plugin_id: str, extension_point: str | None = None) -> int:
        """取消注册. extension_point=None 时取消该插件所有贡献。返回取消数量。"""

    @abstractmethod
    def list(self, extension_point: str) -> builtins.list[dict[str, Any]]:
        """列出某扩展点的所有贡献元信息."""

    @abstractmethod
    async def invoke(self, extension_point: str, payload: dict[str, Any]) -> builtins.list[Any]:
        """调用某扩展点的所有贡献，返回结果列表（按注册顺序）."""


class BUILTIN_EXTENSION_POINTS:
    """内置扩展点常量.

    业务模块通过这些常量引用扩展点，避免硬编码字符串。
    """

    TASK_HANDLER = "core.task_handler"  # 注册任务类型
    DATASET_READER = "core.dataset_reader"  # 自定义数据格式读取
    MODEL_REGISTRY = "core.model_registry"  # 注册可推理模型
    WORKFLOW_TEMPLATE = "core.workflow_template"  # 贡献工作流模板
    UI_WORKSPACE_PANEL = "core.ui.workspace_panel"  # 前端工作区面板
    UI_SETTINGS_TAB = "core.ui.settings_tab"  # 前端设置页 tab
    CHAT_COMMAND = "core.chat_command"  # 自然语言命令扩展

    @classmethod
    def all(cls) -> list[str]:
        """返回所有内置扩展点名称."""
        return [
            cls.TASK_HANDLER,
            cls.DATASET_READER,
            cls.MODEL_REGISTRY,
            cls.WORKFLOW_TEMPLATE,
            cls.UI_WORKSPACE_PANEL,
            cls.UI_SETTINGS_TAB,
            cls.CHAT_COMMAND,
        ]


# 插件能力授权矩阵（与 ADR-005 第 2.2 节对齐）


@dataclass(frozen=True)
class Capability:
    """插件能力定义."""

    name: str
    description: str
    default_grant: bool  # 默认是否授权（False 表示需要用户/管理员确认）


# 内置能力清单（核心层维护，插件只能请求这些能力）
BUILTIN_CAPABILITIES: dict[str, Capability] = {
    "task:submit": Capability("task:submit", "提交任务", True),
    "task:workflow:run": Capability("task:workflow:run", "运行 DAG 工作流", True),
    "dataset:read": Capability("dataset:read", "读取数据集", True),
    "dataset:write": Capability("dataset:write", "写入数据集", False),
    "dataset:version:create": Capability("dataset:version:create", "创建数据集版本", False),
    "config:sweep": Capability("config:sweep", "启动超参搜索", False),
    "observability:snapshot:create": Capability("observability:snapshot:create", "创建实验快照", True),
    "observability:trace:export": Capability("observability:trace:export", "导出 trace", True),
    "plugin:install": Capability("plugin:install", "安装其他插件", False),
    "compute:gpu": Capability("compute:gpu", "使用 GPU", True),
    "network:egress": Capability("network:egress", "外网访问", False),
}


def validate_capability_request(capability: str) -> bool:
    """校验插件请求的能力是否在内置清单中."""
    return capability in BUILTIN_CAPABILITIES
