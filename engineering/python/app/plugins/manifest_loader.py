"""插件 manifest 加载器：plugin.yaml → PluginManifest.

对应 ADR-005 第 5 章 + core-contracts-design.md 阶段 3 p3-3。

定义 plugin.yaml 的官方 schema，提供加载器与校验器。
plugin.yaml 是插件根目录的清单文件，描述插件的元信息、入口点、契约依赖、能力请求。

== plugin.yaml schema（YAML）==

    id: ltc_chatter                      # 必填，插件唯一 ID（小写 + 下划线）
    name: LTC 颤振预测                    # 必填，显示名
    version: 1.0.0                       # 必填，semver
    description: 基于 LTC 网络的颤振预测  # 必填
    author: 灵境制造团队                  # 必填
    license: MIT                         # 必填
    entrypoint: ltc_chatter.main:Plugin  # 必填，module.path:ClassName
    homepage: https://example.com/ltc    # 可选
    tags: [chatter, ltc, prediction]     # 可选

    required_contracts:                  # 可选，依赖的契约及版本约束
      - task@>=1.0
      - dataset@>=1.0
    required_capabilities:               # 可选，必须授权的能力
      - task:submit
      - dataset:read
    optional_capabilities:               # 可选，期望但非必需的能力
      - compute:gpu
    dependencies:                        # 可选，其他插件 id
      - signal_processor

    config_schema:                       # 可选，JSON Schema 形式的配置声明
      type: object
      properties:
        window_size:
          type: integer
          default: 1024
      required: [window_size]

== 加载流程 ==

    1. 读取 plugin.yaml 文件
    2. PyYAML 解析为 dict
    3. schema 校验（必填字段、格式约束）
    4. 构造 PluginManifest dataclass
    5. 返回

== 与 legacy plugin.json 的关系 ==

legacy plugin.json 继续支持（PluginDiscovery 兼容），
plugin.yaml 是契约层推荐的官方格式。两者通过 adapt_metadata_to_manifest
统一适配到 PluginManifest 契约。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.contracts.plugin import (
    PluginManifest,
    validate_capability_request,
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
    "entrypoint",
)

OPTIONAL_FIELDS: tuple[str, ...] = (
    "homepage",
    "tags",
    "required_contracts",
    "required_capabilities",
    "optional_capabilities",
    "dependencies",
    "config_schema",
    "workflow_templates",  # ADR-010: 插件贡献的工作流模板 YAML 路径列表
)

# ID 必须小写字母/数字/下划线，开头非数字
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# semver 简化模式：MAJOR.MINOR.PATCH（可选 -prerelease + +build）
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

# required_contracts 条目：name@version_constraint
_CONTRACT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*@[^\s]+$")

# entrypoint：module.path:ClassName（ClassName 大写开头）
_ENTRYPOINT_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*:[A-Z][a-zA-Z0-9_]*$")


# 校验异常


class ManifestValidationError(ValueError):
    """manifest 校验失败异常."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


# 校验器


def validate_manifest_dict(data: dict[str, Any]) -> list[str]:
    """校验 manifest dict，返回错误列表（空列表表示通过）.

    校验项：
        1. 必填字段齐全
        2. id 格式（小写 + 下划线）
        3. version 是 semver
        4. entrypoint 格式（module:Class）
        5. required_contracts 条目格式（name@version）
        6. required/optional_capabilities 在内置清单中
        7. dependencies 是字符串列表
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

    # entrypoint 格式
    entrypoint = str(data["entrypoint"])
    if not _ENTRYPOINT_PATTERN.match(entrypoint):
        errors.append(f"entrypoint 格式错误（应为 module.path:ClassName）: {entrypoint}")

    # required_contracts 格式
    for req in data.get("required_contracts", []) or []:
        if not _CONTRACT_PATTERN.match(str(req)):
            errors.append(f"required_contracts 条目格式错误（应为 name@version）: {req}")

    # capabilities 校验
    for cap in data.get("required_capabilities", []) or []:
        if not validate_capability_request(str(cap)):
            errors.append(f"required_capabilities 引用未知能力: {cap}")

    for cap in data.get("optional_capabilities", []) or []:
        if not validate_capability_request(str(cap)):
            errors.append(f"optional_capabilities 引用未知能力: {cap}")

    # dependencies 是字符串列表
    deps = data.get("dependencies", []) or []
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        errors.append("dependencies 必须是字符串列表")

    # tags 是字符串列表
    tags = data.get("tags", []) or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        errors.append("tags 必须是字符串列表")

    # config_schema 是 dict
    cs = data.get("config_schema")
    if cs is not None and not isinstance(cs, dict):
        errors.append("config_schema 必须是 dict（JSON Schema）")

    # workflow_templates 是字符串列表（ADR-010）
    wts = data.get("workflow_templates", []) or []
    if not isinstance(wts, list) or not all(isinstance(t, str) for t in wts):
        errors.append("workflow_templates 必须是字符串列表（YAML 文件相对路径）")

    return errors


# 加载器


def load_manifest_from_dict(data: dict[str, Any]) -> PluginManifest:
    """从 dict 构造 PluginManifest（带校验）.

    Raises:
        ManifestValidationError: 校验失败
    """
    errors = validate_manifest_dict(data)
    if errors:
        raise ManifestValidationError(errors)

    return PluginManifest(
        id=str(data["id"]),
        name=str(data["name"]),
        version=str(data["version"]),
        description=str(data["description"]),
        author=str(data["author"]),
        license=str(data["license"]),
        entrypoint=str(data["entrypoint"]),
        required_contracts=list(data.get("required_contracts", []) or []),
        required_capabilities=list(data.get("required_capabilities", []) or []),
        optional_capabilities=list(data.get("optional_capabilities", []) or []),
        dependencies=list(data.get("dependencies", []) or []),
        config_schema=dict(data.get("config_schema", {}) or {}),
        homepage=str(data.get("homepage", "") or ""),
        tags=list(data.get("tags", []) or []),
        workflow_templates=list(data.get("workflow_templates", []) or []),
    )


def load_manifest_from_yaml(path: str | Path) -> PluginManifest:
    """从 plugin.yaml 文件加载 manifest.

    Args:
        path: plugin.yaml 文件路径

    Raises:
        FileNotFoundError: 文件不存在
        ManifestValidationError: 校验失败
        yaml.YAMLError: YAML 解析失败
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest file not found: {path}")

    # 延迟导入 yaml，避免未安装时影响模块加载
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        raise ImportError("PyYAML is required to load plugin.yaml manifests. Install it via: pip install pyyaml") from e

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        raise ManifestValidationError(["plugin.yaml 文件为空"])
    if not isinstance(data, dict):
        raise ManifestValidationError([f"plugin.yaml 顶层应为 dict，实际: {type(data).__name__}"])

    return load_manifest_from_dict(data)


def load_manifest_from_dir(plugin_dir: str | Path) -> PluginManifest:
    """从插件目录加载 manifest（查找 plugin.yaml）.

    Args:
        plugin_dir: 插件根目录

    Raises:
        FileNotFoundError: 目录中无 plugin.yaml
        ManifestValidationError: 校验失败
    """
    plugin_dir = Path(plugin_dir)
    yaml_path = plugin_dir / "plugin.yaml"

    if not yaml_path.exists():
        # 兼容 .yml 扩展名
        yml_path = plugin_dir / "plugin.yml"
        if yml_path.exists():
            yaml_path = yml_path
        else:
            raise FileNotFoundError(f"No plugin.yaml found in plugin directory: {plugin_dir}")

    return load_manifest_from_yaml(yaml_path)


def manifest_to_dict(manifest: PluginManifest) -> dict[str, Any]:
    """把 PluginManifest 序列化为 dict（可写回 YAML）."""
    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "author": manifest.author,
        "license": manifest.license,
        "entrypoint": manifest.entrypoint,
        "homepage": manifest.homepage,
        "tags": list(manifest.tags),
        "required_contracts": list(manifest.required_contracts),
        "required_capabilities": list(manifest.required_capabilities),
        "optional_capabilities": list(manifest.optional_capabilities),
        "dependencies": list(manifest.dependencies),
        "config_schema": dict(manifest.config_schema),
        # ADR-010: 插件贡献的工作流模板 YAML 路径列表
        "workflow_templates": list(manifest.workflow_templates),
    }


def manifest_to_yaml(manifest: PluginManifest) -> str:
    """把 PluginManifest 序列化为 YAML 字符串."""
    try:
        import yaml
    except ImportError as e:
        raise ImportError("PyYAML is required to serialize plugin.yaml manifests.") from e

    return yaml.safe_dump(
        manifest_to_dict(manifest),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


# 示例 manifest 生成


def create_example_manifest_dict(
    plugin_id: str = "example_plugin",
    plugin_name: str = "示例插件",
) -> dict[str, Any]:
    """生成示例 manifest dict（供插件脚手架使用）."""
    return {
        "id": plugin_id,
        "name": plugin_name,
        "version": "0.1.0",
        "description": "示例插件，演示 plugin.yaml 格式",
        "author": "灵境制造团队",
        "license": "MIT",
        "entrypoint": f"{plugin_id}.main:Plugin",
        "homepage": "",
        "tags": ["example"],
        "required_contracts": ["task@>=1.0"],
        "required_capabilities": ["task:submit"],
        "optional_capabilities": [],
        "dependencies": [],
        "config_schema": {
            "type": "object",
            "properties": {},
        },
    }


__all__ = [
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
    "ManifestValidationError",
    "validate_manifest_dict",
    "load_manifest_from_dict",
    "load_manifest_from_yaml",
    "load_manifest_from_dir",
    "manifest_to_dict",
    "manifest_to_yaml",
    "create_example_manifest_dict",
]
