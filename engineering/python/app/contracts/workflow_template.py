"""工作流模板市场契约：定义 WorkflowTemplateManifest 与市场流转接口.

对应 ADR-010（工作流模板市场）。本文件只定义数据结构与接口契约，
实现见 app/plugins/workflow_template_loader.py（加载器）、
app/services/workflow_template_service.py（服务层）、
app/api/v1/workflow_templates.py（路由层）。

契约稳定性：Stable（v1.0.0），向后兼容扩展。

设计要点：
    1. 复用现有 WorkflowSpec（app.contracts.task.WorkflowSpec），不修改其定义
    2. 模板 = WorkflowSpec + 市场元数据（category/tags/inputs_schema/parameters）
    3. 模板多版本管理（semver），每个版本对应一个不可变的 WorkflowSpec 快照
    4. 市场统计字段（downloads/avg_rating/rating_count）由服务层维护，不在 manifest 中
    5. 通过 BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE 扩展点接入插件系统
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkflowTemplateManifest:
    """工作流模板清单契约（workflow_template.yaml 的 Python 投影）.

    一个模板 = 模板元数据 + 一个 WorkflowSpec（不可变）。
    模板的多个版本通过 (template_id, version) 唯一标识，每版本对应独立的 manifest。

    与 PluginManifest 的关系：
        - 插件可通过 plugin.yaml 的 `workflow_templates` 字段声明贡献的模板
        - 模板也可独立存在（无 plugin_id），用户手写 YAML 发布到市场
        - 模板通过 BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE 扩展点注册

    属性:
        id: 模板 ID（小写字母/数字/下划线，开头非数字，与 plugin id 同规范）
        name: 显示名
        version: semver 版本号
        description: 描述
        author: 作者
        license: 许可证
        spec: WorkflowSpec dict（含 name/version/nodes/edges/inputs/outputs/metadata）
        category: 模板分类，如 ``training`` / ``evaluation`` / ``iteration`` /
            ``preprocess`` / ``inference``。用于市场浏览过滤
        tags: 自由标签列表
        inputs_schema: 输入参数 JSON Schema dict，覆盖 spec.inputs 的默认值。
            实例化时用户必须提供满足此 schema 的 inputs
        parameters: 可调参数声明 dict（参数名 → JSON Schema 片段）。
            实例化时用户可覆盖这些参数，注入到 spec.nodes[].params
        required_contracts: 依赖的契约及版本约束（格式同 PluginManifest），
            如 ``["task@>=1.0", "dataset@>=1.0"]``
        required_capabilities: 必须授权的能力（与 PluginManifest 同规范）
        plugin_id: 贡献此模板的插件 id。空字符串表示独立模板（非插件贡献）
        homepage: 主页 URL（可选）
    """

    id: str
    name: str
    version: str
    description: str
    author: str
    license: str
    spec: dict[str, Any]
    category: str = "general"
    tags: tuple[str, ...] = field(default_factory=tuple)
    inputs_schema: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    required_contracts: tuple[str, ...] = field(default_factory=tuple)
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    plugin_id: str = ""
    homepage: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("WorkflowTemplateManifest.id 不能为空")
        if not self.name:
            raise ValueError("WorkflowTemplateManifest.name 不能为空")
        if not self.version:
            raise ValueError("WorkflowTemplateManifest.version 不能为空")
        if not self.spec:
            raise ValueError("WorkflowTemplateManifest.spec 不能为空")
        if not isinstance(self.spec, dict):
            raise TypeError(f"WorkflowTemplateManifest.spec 必须是 dict，实际: {type(self.spec).__name__}")
        # spec 必须包含 nodes（与 WorkflowSpec 契约对齐）
        if not self.spec.get("nodes"):
            raise ValueError("WorkflowTemplateManifest.spec.nodes 不能为空")
        # required_contracts 格式校验
        for req in self.required_contracts:
            if "@" not in req:
                raise ValueError(f"required_contracts 条目格式错误（应为 name@version）: {req}")


# 模板分类常量（推荐值，不强制枚举）


class TEMPLATE_CATEGORIES:
    """推荐的工作流模板分类常量.

    分类不强制枚举，用户可自定义，但市场 UI 按这些推荐分类过滤。
    """

    GENERAL = "general"  # 通用
    TRAINING = "training"  # 训练
    EVALUATION = "evaluation"  # 评估
    ITERATION = "iteration"  # 迭代（数据飞轮）
    PREPROCESS = "preprocess"  # 预处理
    INFERENCE = "inference"  # 推理
    ANALYSIS = "analysis"  # 分析（如颤振分析）

    @classmethod
    def all(cls) -> list[str]:
        """返回所有推荐分类."""
        return [
            cls.GENERAL,
            cls.TRAINING,
            cls.EVALUATION,
            cls.ITERATION,
            cls.PREPROCESS,
            cls.INFERENCE,
            cls.ANALYSIS,
        ]


# 市场统计快照（运行时聚合，不在 manifest 中持久化）


@dataclass(frozen=True)
class TemplateMarketStats:
    """模板市场统计快照（由服务层聚合，不持久化在 manifest 中）.

    与 SkillMarketplace.MarketListing 对齐：downloads + avg_rating + rating_count
    三维度驱动优质内容浮现。

    属性:
        template_id: 模板 ID
        version: 对应版本（统计按版本聚合）
        downloads: 累计实例化次数
        avg_rating: 平均评分（0-5，保留 2 位小数）
        rating_count: 评分人数
        published_at: 发布时间（ISO8601 字符串）
    """

    template_id: str
    version: str
    downloads: int = 0
    avg_rating: float = 0.0
    rating_count: int = 0
    published_at: str = ""


__all__ = [
    "TEMPLATE_CATEGORIES",
    "TemplateMarketStats",
    "WorkflowTemplateManifest",
]
