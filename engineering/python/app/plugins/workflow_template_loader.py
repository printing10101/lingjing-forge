"""工作流模板加载器：template.yaml → WorkflowTemplateManifest.

对应 ADR-010 阶段 6 p6-1。

定义 template.yaml 的官方 schema，提供加载器与校验器。
template.yaml 描述一个可分享/复用的工作流模板，由 manifest 元信息 + 一个
WorkflowSpec（nodes/edges/inputs/outputs/metadata）组成。

== template.yaml schema（YAML）==

    id: ltc_chatter_pipeline           # 必填，模板唯一 ID（小写 + 下划线）
    name: LTC 颤振预测流水线            # 必填，显示名
    version: 1.0.0                      # 必填，semver
    description: 端到端颤振预测工作流    # 必填
    author: 灵境制造团队                 # 必填
    license: MIT                        # 必填
    category: training                  # 可选，默认 general（见 TEMPLATE_CATEGORIES）
    tags: [chatter, ltc, pipeline]      # 可选
    plugin_id: ltc_chatter              # 可选，贡献此模板的插件 id
    homepage: https://example.com/tpl   # 可选
    required_contracts:                 # 可选，依赖的契约及版本约束
      - task@>=1.0
      - dataset@>=1.0
    required_capabilities:              # 可选，运行此模板必须授权的能力
      - task:submit
      - dataset:read
    inputs_schema:                      # 可选，输入参数 JSON Schema
      type: object
      properties:
        dataset_uri:
          type: string
      required: [dataset_uri]
    parameters:                         # 可选，默认参数（被 inputs_schema 覆盖）
      window_size: 1024

    spec:                               # 必填，WorkflowSpec dict
      name: ltc_chatter_pipeline
      version: 1.0.0
      nodes:
        - node_id: preprocess
          task_type: signal_preprocess
          params: {window_size: 1024}
        - node_id: train
          task_type: ltc_train
          params: {epochs: 50}
      edges:
        - from_node: preprocess
          to_node: train
          from_output: dataset
          to_input: train_data
      inputs: {}
      outputs:
        model: train.model
      metadata:
        template_category: training

== 加载流程 ==

    1. 读取 template.yaml 文件
    2. PyYAML 解析为 dict
    3. schema 校验（必填字段、格式约束、spec 内部一致性）
    4. 构造 WorkflowTemplateManifest dataclass（含 spec 校验）
    5. 返回

== 与 plugin.yaml 的关系 ==

插件通过 plugin.yaml 的 workflow_templates 字段声明贡献的模板路径，
加载器在插件 on_load 时调用 load_template_from_yaml 逐个加载，再由
IExtensionRegistry.register 注册到 core.workflow_template 扩展点。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.contracts.workflow_template import (
    TEMPLATE_CATEGORIES,
    WorkflowTemplateManifest,
)

logger = logging.getLogger(__name__)


# Schema 常量


REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "version",
    "description",
    "author",
    "license",
    "spec",
)

OPTIONAL_FIELDS: tuple[str, ...] = (
    "category",
    "tags",
    "plugin_id",
    "homepage",
    "required_contracts",
    "required_capabilities",
    "inputs_schema",
    "parameters",
)

# ID 必须小写字母/数字/下划线，开头非数字（与 plugin id 规则一致）
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# semver 简化模式（复用 manifest_loader 的模式）
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

# required_contracts 条目：name@version_constraint
_CONTRACT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*@[^\s]+$")


# 校验异常


class TemplateValidationError(ValueError):
    """工作流模板校验失败异常."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


# 校验器


def validate_template_dict(data: dict[str, Any]) -> list[str]:
    """校验 template dict，返回错误列表（空列表表示通过）.

    校验项：
        1. 必填字段齐全
        2. id 格式（小写 + 下划线）
        3. version 是 semver
        4. category 在 TEMPLATE_CATEGORIES.all() 中
        5. spec 是 dict 且包含 nodes
        6. required_contracts 条目格式（name@version）
        7. tags 是字符串列表
        8. inputs_schema / parameters 是 dict
    """
    errors: list[str] = []

    # 必填字段
    for field_name in REQUIRED_FIELDS:
        val = data.get(field_name)
        if val is None or (isinstance(val, str) and not val.strip()):
            errors.append(f"必填字段缺失或为空: {field_name}")

    if errors:
        return errors  # 必填缺失时后续校验无意义

    # id 格式
    if not _ID_PATTERN.match(str(data["id"])):
        errors.append(f"id 格式错误（应为小写字母/数字/下划线，开头非数字）: {data['id']}")

    # version semver
    if not _SEMVER_PATTERN.match(str(data["version"])):
        errors.append(f"version 不是合法 semver: {data['version']}")

    # category 校验
    category = str(data.get("category", "general") or "general")
    if category not in TEMPLATE_CATEGORIES.all():
        errors.append(f"category 不在合法清单中（{TEMPLATE_CATEGORIES.all()}）: {category}")

    # spec 必须是 dict 且包含 nodes
    spec = data.get("spec")
    if not isinstance(spec, dict):
        errors.append(f"spec 必须是 dict，实际类型: {type(spec).__name__}")
    else:
        nodes = spec.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            errors.append("spec.nodes 必须是非空 list")
        # spec 内部一致性校验由 WorkflowSpec.validate() 在 dataclass 构造时执行，
        # 此处只做最浅层的结构校验，避免重复实现校验逻辑

    # required_contracts 格式
    for req in data.get("required_contracts", []) or []:
        if not _CONTRACT_PATTERN.match(str(req)):
            errors.append(f"required_contracts 条目格式错误（应为 name@version）: {req}")

    # tags 是字符串列表
    tags = data.get("tags", []) or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        errors.append("tags 必须是字符串列表")

    # inputs_schema 是 dict（JSON Schema）
    is_ = data.get("inputs_schema")
    if is_ is not None and not isinstance(is_, dict):
        errors.append("inputs_schema 必须是 dict（JSON Schema）")

    # parameters 是 dict
    params = data.get("parameters")
    if params is not None and not isinstance(params, dict):
        errors.append("parameters 必须是 dict")

    return errors


# 加载器


def load_template_from_dict(data: dict[str, Any]) -> WorkflowTemplateManifest:
    """从 dict 构造 WorkflowTemplateManifest（带校验）.

    Raises:
        TemplateValidationError: 校验失败
    """
    errors = validate_template_dict(data)
    if errors:
        raise TemplateValidationError(errors)

    try:
        return WorkflowTemplateManifest(
            id=str(data["id"]),
            name=str(data["name"]),
            version=str(data["version"]),
            description=str(data["description"]),
            author=str(data["author"]),
            license=str(data["license"]),
            spec=dict(data["spec"]),
            category=str(data.get("category", "general") or "general"),
            tags=tuple(data.get("tags", []) or []),
            inputs_schema=dict(data.get("inputs_schema", {}) or {}),
            parameters=dict(data.get("parameters", {}) or {}),
            required_contracts=tuple(data.get("required_contracts", []) or []),
            required_capabilities=tuple(data.get("required_capabilities", []) or []),
            plugin_id=str(data.get("plugin_id", "") or ""),
            homepage=str(data.get("homepage", "") or ""),
        )
    except (ValueError, TypeError) as e:
        # WorkflowTemplateManifest.__post_init__ 内部校验失败（如 spec.nodes 不合法）
        raise TemplateValidationError([f"WorkflowTemplateManifest 构造失败: {e}"]) from e


def load_template_from_yaml(path: str | Path) -> WorkflowTemplateManifest:
    """从 template.yaml 文件加载工作流模板.

    Args:
        path: template.yaml 文件路径

    Raises:
        FileNotFoundError: 文件不存在
        TemplateValidationError: 校验失败
        yaml.YAMLError: YAML 解析失败
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Template file not found: {path}")

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        raise ImportError("PyYAML is required to load template.yaml files. Install it via: pip install pyyaml") from e

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        raise TemplateValidationError(["template.yaml 文件为空"])
    if not isinstance(data, dict):
        raise TemplateValidationError([f"template.yaml 顶层应为 dict，实际: {type(data).__name__}"])

    return load_template_from_dict(data)


def load_templates_from_dir(
    templates_dir: str | Path,
) -> list[WorkflowTemplateManifest]:
    """从目录批量加载所有 template.yaml / template.yml 文件.

    用于插件 on_load 时扫描插件根目录下的 templates/ 子目录。
    单个文件加载失败不会中断整体流程，会记录 warning 并跳过。

    Args:
        templates_dir: 包含 template.yaml 文件的目录

    Returns:
        成功加载的 WorkflowTemplateManifest 列表（可能为空）
    """
    templates_dir = Path(templates_dir)
    if not templates_dir.exists() or not templates_dir.is_dir():
        return []

    results: list[WorkflowTemplateManifest] = []
    # 优先 template.yaml，兼容 .yml；也支持任意 *.yaml 文件
    yaml_files = sorted(list(templates_dir.glob("*.yaml")) + list(templates_dir.glob("*.yml")))

    for yaml_path in yaml_files:
        try:
            manifest = load_template_from_yaml(yaml_path)
            results.append(manifest)
            logger.debug(
                "Loaded workflow template from %s: %s@%s",
                yaml_path,
                manifest.id,
                manifest.version,
            )
        except Exception as e:
            logger.warning("Failed to load workflow template from %s: %s", yaml_path, e)

    return results


def load_templates_from_plugin(
    plugin_dir: str | Path,
    relative_paths: list[str],
) -> list[WorkflowTemplateManifest]:
    """根据 plugin.yaml 中声明的 workflow_templates 路径列表加载模板.

    Args:
        plugin_dir: 插件根目录
        relative_paths: plugin.yaml workflow_templates 字段声明的相对路径列表

    Returns:
        成功加载的 WorkflowTemplateManifest 列表
    """
    plugin_dir = Path(plugin_dir)
    results: list[WorkflowTemplateManifest] = []

    for rel_path in relative_paths:
        yaml_path = plugin_dir / rel_path
        try:
            manifest = load_template_from_yaml(yaml_path)
            # 自动注入 plugin_id（若模板未显式声明）
            if not manifest.plugin_id:
                # frozen dataclass，需通过 dataclasses.replace 构造新实例
                from dataclasses import replace

                manifest = replace(manifest, plugin_id=_infer_plugin_id(plugin_dir))
            results.append(manifest)
        except Exception as e:
            # 保留 Exception 兜底：load_template_from_yaml 内部 yaml 在 try 块内导入，
            # 无法在 except 子句中引用 yaml.YAMLError。为确保单个模板加载失败不影响
            # 其他模板，保留 Exception 作为最外层兜底。
            logger.warning(
                "Failed to load workflow template %s from plugin %s: %s",
                rel_path,
                plugin_dir,
                e,
            )

    return results


def _infer_plugin_id(plugin_dir: Path) -> str:
    """从插件目录推断 plugin_id（读取同目录 plugin.yaml 的 id 字段）.

    若读取失败返回空字符串（plugin_id 为可选字段）。
    """
    yaml_path = plugin_dir / "plugin.yaml"
    if not yaml_path.exists():
        yaml_path = plugin_dir / "plugin.yml"
        if not yaml_path.exists():
            return ""

    try:
        import yaml

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return str(data.get("id", "") or "")
    except Exception:
        # 保留 Exception 兜底：yaml 在 try 块内导入，无法在 except 中引用 yaml.YAMLError。
        # plugin_id 为可选字段，任何失败都应返回空字符串而非阻塞插件加载。
        return ""


# 序列化器


def template_to_dict(manifest: WorkflowTemplateManifest) -> dict[str, Any]:
    """把 WorkflowTemplateManifest 序列化为 dict（可写回 YAML）."""
    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "author": manifest.author,
        "license": manifest.license,
        "category": manifest.category,
        "tags": list(manifest.tags),
        "plugin_id": manifest.plugin_id,
        "homepage": manifest.homepage,
        "required_contracts": list(manifest.required_contracts),
        "required_capabilities": list(manifest.required_capabilities),
        "inputs_schema": dict(manifest.inputs_schema),
        "parameters": dict(manifest.parameters),
        "spec": dict(manifest.spec),
    }


def template_to_yaml(manifest: WorkflowTemplateManifest) -> str:
    """把 WorkflowTemplateManifest 序列化为 YAML 字符串."""
    try:
        import yaml
    except ImportError as e:
        raise ImportError("PyYAML is required to serialize template.yaml files.") from e

    return yaml.safe_dump(
        template_to_dict(manifest),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


# 示例 template 生成


def create_example_template_dict(
    template_id: str = "example_pipeline",
    template_name: str = "示例工作流模板",
) -> dict[str, Any]:
    """生成示例 template dict（供模板脚手架使用）."""
    return {
        "id": template_id,
        "name": template_name,
        "version": "0.1.0",
        "description": "示例工作流模板，演示 template.yaml 格式",
        "author": "灵境制造团队",
        "license": "MIT",
        "category": "general",
        "tags": ["example"],
        "plugin_id": "",
        "homepage": "",
        "required_contracts": ["task@>=1.0"],
        "required_capabilities": ["task:submit"],
        "inputs_schema": {
            "type": "object",
            "properties": {},
        },
        "parameters": {},
        "spec": {
            "name": template_id,
            "version": "0.1.0",
            "nodes": [
                {
                    "node_id": "step1",
                    "task_type": "noop",
                    "params": {},
                }
            ],
            "edges": [],
            "inputs": {},
            "outputs": {},
            "metadata": {},
        },
    }


__all__ = [
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
    "TemplateValidationError",
    "validate_template_dict",
    "load_template_from_dict",
    "load_template_from_yaml",
    "load_templates_from_dir",
    "load_templates_from_plugin",
    "template_to_dict",
    "template_to_yaml",
    "create_example_template_dict",
]
