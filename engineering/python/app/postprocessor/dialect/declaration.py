"""方言声明模型与 YAML 加载。

对应 docs/development/postprocessor-方言声明化设计.md §3.3：
方言 = 声明（dialect.yaml）+ 模板（Jinja2）+ 可选代码钩子。

本模块只定义声明数据结构与加载校验，不包含编译/渲染逻辑（见 compiler.py）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


class DialectDeclarationError(ValueError):
    """方言声明加载/校验失败。"""


# 声明 schema 版本（契约稳定性：Stable v1，只允许向后兼容扩展）
DIALECT_SCHEMA_VERSION = "v1"

# 允许声明的模板方法白名单（必须与 BasePostProcessor 可覆盖方法对齐）
ALLOWED_TEMPLATE_METHODS = frozenset(
    {
        "format_header",
        "format_tool_change",
        "format_arc",
        "format_coolant",
        "format_tool_compensation",
        "format_cycle_drill",
        "format_cycle_tapping",
        "format_cycle_boring",
        "format_cycle_threading",
        "format_cycle_groove",
        "format_cycle_thread_turning",
        "format_subprogram_call",
        "format_subprogram_end",
        "format_footer",
        "format_high_precision_mode",
        "format_five_axis_mode",
        "format_probe_cycle",
        "format_surface_normal_compensation",
        "format_rtcp_on",
        "format_rtcp_off",
        "format_twp_on",
        "format_twp_off",
        "format_rotary_axis_config",
        "format_workspace_check",
    }
)

# 支持的内置基类方言（extends 可引用；与 PostProcessorRegistry 内置注册对齐）
BUILTIN_BASE_DIALECTS = frozenset(
    {
        "fanuc_0i",
        "siemens_840d",
        "heidenhain_tnc",
        "gsk_980_25i",
        "hnc_848_22",
        "knd_1000_2000_3000",
        "mitsubishi_m70_m80",
        "fagor_8055",
        "xmachine_xm100",
    }
)


@dataclass
class DialectDeclaration:
    """方言声明（dialect.yaml 的 Python 投影）。

    Attributes:
        id: 控制器标识（如 ``knd_1000_2000_3000``），须全局唯一
        name: 可读名称
        version: 声明版本（semver）
        extends: 继承的基类方言 id（None 表示不继承，需提供完整模板）
        target_controller: 目标控制器标识（用于与 ConfigLoader 控制器段对齐）
        templates: 模板覆盖 {方法名: 相对 dialect.yaml 的模板路径}；
            未声明的模板方法继承 extends 基类实现
        params: 参数覆盖（与 postprocessor_config.yaml 的 controllers.<name> 语义一致）
        hooks: 可选代码钩子 entrypoint，支持两种写法（默认无）：
            - 单入口字符串 "module.path:ClassName"
            - 多入口列表（Phase E hooks 模式）：
              ``[{module: pkg.hooks, class: MyHooks}, ...]``
              加载时归一化为 ["pkg.hooks:MyHooks", ...]
        author / description: 元信息
    """

    id: str
    name: str
    version: str
    extends: str | None = None
    target_controller: str | None = None
    templates: dict[str, Path] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    hooks: str | list[str] | None = None
    author: str = ""
    description: str = ""

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "DialectDeclaration":
        """从 dialect.yaml 加载并校验声明。

        Args:
            yaml_path: dialect.yaml 的绝对路径

        Returns:
            校验通过的声明

        Raises:
            DialectDeclarationError: 文件缺失 / YAML 非法 / 字段校验失败
        """
        if not yaml_path.exists():
            raise DialectDeclarationError(f"方言声明文件不存在: {yaml_path}")
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise DialectDeclarationError(f"方言声明 YAML 解析失败: {yaml_path}: {e}") from e
        if not isinstance(raw, dict):
            raise DialectDeclarationError(f"方言声明顶层必须是映射: {yaml_path}")

        base_dir = yaml_path.parent

        # 必填字段
        for key in ("id", "name", "version"):
            value = raw.get(key)
            if not value or not isinstance(value, str):
                raise DialectDeclarationError(f"方言声明缺少必填字符串字段 '{key}': {yaml_path}")

        # extends：可选，但若声明必须在白名单内
        extends = raw.get("extends")
        if extends is not None:
            if not isinstance(extends, str) or extends not in BUILTIN_BASE_DIALECTS:
                raise DialectDeclarationError(
                    f"方言 '{raw['id']}' 的 extends='{extends}' 不是受支持的内置方言"
                    f"（可选值: {sorted(BUILTIN_BASE_DIALECTS)}）"
                )

        # templates：方法白名单 + 模板文件存在性
        templates: dict[str, Path] = {}
        raw_templates = raw.get("templates") or {}
        if not isinstance(raw_templates, dict):
            raise DialectDeclarationError(f"方言 '{raw['id']}' 的 templates 必须是映射: {yaml_path}")
        for method, rel_path in raw_templates.items():
            if method not in ALLOWED_TEMPLATE_METHODS:
                raise DialectDeclarationError(
                    f"方言 '{raw['id']}' 模板方法 '{method}' 不在白名单内（可选值: {sorted(ALLOWED_TEMPLATE_METHODS)}）"
                )
            if not isinstance(rel_path, str) or not rel_path.endswith(".j2"):
                raise DialectDeclarationError(f"方言 '{raw['id']}' 模板路径必须是 .j2 文件: {rel_path}")
            template_path = base_dir / rel_path
            if not template_path.exists():
                raise DialectDeclarationError(f"方言 '{raw['id']}' 模板文件不存在: {template_path}")
            templates[method] = template_path

        # hooks：可选，支持两种写法（归一化为 entrypoint 列表语义，str 保持兼容）
        hooks = raw.get("hooks")
        if hooks is not None:
            if isinstance(hooks, str):
                if ":" not in hooks:
                    raise DialectDeclarationError(
                        f"方言 '{raw['id']}' 的 hooks 格式错误（应为 module.path:ClassName）: {hooks}"
                    )
            elif isinstance(hooks, list):
                normalized: list[str] = []
                for entry in hooks:
                    if not isinstance(entry, dict) or not entry.get("module") or not entry.get("class"):
                        raise DialectDeclarationError(
                            f"方言 '{raw['id']}' 的 hooks 列表项格式错误（应为 {{module: 路径, class: 类名}}）: {entry}"
                        )
                    normalized.append(f"{entry['module']}:{entry['class']}")
                hooks = normalized
            else:
                raise DialectDeclarationError(
                    f"方言 '{raw['id']}' 的 hooks 格式错误（应为字符串 entrypoint 或 "
                    f"[{{module, class}}] 列表）: {hooks}"
                )

        # params：可选，必须是映射
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise DialectDeclarationError(f"方言 '{raw['id']}' 的 params 必须是映射: {yaml_path}")

        return cls(
            id=raw["id"],
            name=raw["name"],
            version=raw["version"],
            extends=extends,
            target_controller=raw.get("target_controller"),
            templates=templates,
            params=params,
            hooks=hooks,
            author=raw.get("author", ""),
            description=raw.get("description", ""),
        )


__all__ = [
    "ALLOWED_TEMPLATE_METHODS",
    "BUILTIN_BASE_DIALECTS",
    "DIALECT_SCHEMA_VERSION",
    "DialectDeclaration",
    "DialectDeclarationError",
]
