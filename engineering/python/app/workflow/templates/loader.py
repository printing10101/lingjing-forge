"""YAML 工作流模板加载器.

职责：
    1. 加载内置模板（``builtin/`` 目录下的 YAML）
    2. 加载用户自定义模板（任意路径）
    3. 将模板 dict 转换为 :class:`WorkflowSpec`
    4. 列举可用模板（供 API 暴露）

模板格式见 ``__init__.py`` 模块文档字符串。

设计要点：
    - YAML 解析使用 PyYAML（已在 requirements.txt 中）
    - 模板字段与 :class:`WorkflowSpec` 字段一一对应，转换零损耗
    - 加载时只做"结构校验"（字段齐全/类型对），DAG 一致性校验交给
      :meth:`WorkflowSpec.validate` 在 run 时执行
    - 内置模板以 ``.yaml`` 后缀放在 ``builtin/`` 目录，按文件名（去后缀）
      作为 template_id
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

from app.contracts.task import (
    Artifact,
    WorkflowEdge,
    WorkflowNode,
    WorkflowSpec,
)
from app.workflow.validator import WorkflowValidationError

logger = logging.getLogger(__name__)


_BUILTIN_DIR: Path = Path(__file__).parent / "builtin"


class TemplateNotFoundError(FileNotFoundError):
    """模板不存在。"""


@dataclass
class WorkflowTemplate:
    """加载后的模板元信息 + 原始 spec dict.

    `spec_dict` 与 :func:`_spec_to_dict` 输出格式一致，
    可直接作为 ``/api/v1/workflows/run`` 的 ``spec`` 字段提交。
    """

    template_id: str  # 文件名去后缀
    name: str
    version: str
    description: str = ""
    spec_dict: dict[str, Any] = field(default_factory=dict)


def _ensure_yaml() -> None:
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML 未安装，无法加载 YAML 模板。请在 requirements.txt 中追加 'PyYAML'。")


def _coerce_artifact(name: str, raw: dict[str, Any]) -> Artifact:
    """从 dict 构造 Artifact，校验字段合法性。"""
    if not isinstance(raw, dict):
        raise ValueError(f"artifact '{name}' 必须是 dict，实际类型: {type(raw).__name__}")
    try:
        return Artifact(
            name=name,
            type=raw.get("type", "file"),
            uri=raw.get("uri", ""),
            metadata=dict(raw.get("metadata") or {}),
        )
    except ValueError as e:
        raise ValueError(f"artifact '{name}' 构造失败: {e}") from e


def _template_dict_to_spec(template_dict: dict[str, Any]) -> WorkflowSpec:
    """模板 dict → WorkflowSpec（结构校验，不做 DAG 一致性校验）。"""
    if not isinstance(template_dict, dict):
        raise ValueError(f"模板必须是 dict，实际类型: {type(template_dict).__name__}")

    name = template_dict.get("name")
    if not name:
        raise ValueError("模板缺少必填字段 'name'")

    version = template_dict.get("version", "1.0.0")
    raw_nodes = template_dict.get("nodes") or []
    if not raw_nodes:
        raise ValueError("模板 'nodes' 不能为空")

    nodes: list[WorkflowNode] = []
    for i, n in enumerate(raw_nodes):
        if not isinstance(n, dict):
            raise ValueError(f"nodes[{i}] 必须是 dict")
        node_id = n.get("node_id")
        task_type = n.get("task_type")
        if not node_id:
            raise ValueError(f"nodes[{i}] 缺少 'node_id'")
        if not task_type:
            raise ValueError(f"nodes[{i}] 缺少 'task_type'")
        try:
            nodes.append(
                WorkflowNode(
                    node_id=node_id,
                    task_type=task_type,
                    params=dict(n.get("params") or {}),
                    inputs=dict(n.get("inputs") or {}),
                    retry=int(n.get("retry", 0)),
                    timeout_seconds=int(n.get("timeout_seconds", 3600)),
                )
            )
        except ValueError as e:
            raise ValueError(f"nodes[{i}] ({node_id}) 构造失败: {e}") from e

    raw_edges = template_dict.get("edges") or []
    edges: list[WorkflowEdge] = []
    for i, edge in enumerate(raw_edges):
        if not isinstance(edge, dict):
            raise ValueError(f"edges[{i}] 必须是 dict")
        upstream = edge.get("upstream")
        downstream = edge.get("downstream")
        if not upstream or not downstream:
            raise ValueError(f"edges[{i}] 缺少 'upstream' 或 'downstream'")
        try:
            edges.append(WorkflowEdge(upstream=upstream, downstream=downstream))
        except ValueError as ve:
            raise ValueError(f"edges[{i}] 构造失败: {ve}") from ve

    raw_inputs = template_dict.get("inputs") or {}
    inputs: dict[str, Artifact] = {}
    for k, v in raw_inputs.items():
        inputs[k] = _coerce_artifact(k, v)

    outputs = dict(template_dict.get("outputs") or {})
    metadata = dict(template_dict.get("metadata") or {})

    try:
        return WorkflowSpec(
            name=name,
            version=version,
            nodes=nodes,
            edges=edges,
            inputs=inputs,
            outputs=outputs,
            metadata=metadata,
        )
    except ValueError as e:
        raise ValueError(f"WorkflowSpec 构造失败: {e}") from e


def template_to_spec(template: WorkflowTemplate) -> WorkflowSpec:
    """WorkflowTemplate → WorkflowSpec（执行完整 DAG 校验）。"""
    spec = _template_dict_to_spec(template.spec_dict)
    errors = spec.validate()
    if errors:
        # WorkflowValidationError 接收 list[str]，每条对应一个具体校验问题
        raise WorkflowValidationError([f"模板 '{template.template_id}' DAG 校验失败: {err}" for err in errors])
    return spec


def _load_template_dict(path: Path) -> dict[str, Any]:
    _ensure_yaml()
    if not path.exists():
        raise TemplateNotFoundError(f"模板文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"模板 {path} 顶层必须是 dict，实际类型: {type(data).__name__}")
    return data


def _dict_to_template(template_id: str, raw: dict[str, Any]) -> WorkflowTemplate:
    """dict → WorkflowTemplate（结构校验失败抛 ValueError）。"""
    spec_dict = dict(raw)  # 保留原始 dict 供 API 直接回显
    # 同时做一次结构校验，提前发现错误
    _template_dict_to_spec(raw)
    return WorkflowTemplate(
        template_id=template_id,
        name=raw.get("name", template_id),
        version=raw.get("version", "1.0.0"),
        description=raw.get("description", ""),
        spec_dict=spec_dict,
    )


def load_builtin_template(template_id: str) -> WorkflowTemplate:
    """按 template_id 加载内置模板。

    Args:
        template_id: 内置模板 ID（``builtin/`` 下的文件名，去 ``.yaml`` 后缀）。

    Raises:
        TemplateNotFoundError: 模板不存在。
        ValueError: 模板结构非法。
    """
    # 防路径穿越：仅允许字母数字下划线短横线
    if not template_id or any(c in template_id for c in "/\\:"):
        raise TemplateNotFoundError(f"非法 template_id: {template_id!r}")
    path = _BUILTIN_DIR / f"{template_id}.yaml"
    raw = _load_template_dict(path)
    return _dict_to_template(template_id, raw)


def load_template_from_file(path: str | Path) -> WorkflowTemplate:
    """从任意 YAML 文件加载模板（用户自定义模板）。"""
    p = Path(path)
    template_id = p.stem
    raw = _load_template_dict(p)
    return _dict_to_template(template_id, raw)


def list_builtin_templates() -> list[dict[str, Any]]:
    """列举所有内置模板的元信息（不含 spec_dict，避免响应过大）。

    Returns:
        [{template_id, name, version, description, node_count, edge_count}, ...]
    """
    if not _BUILTIN_DIR.exists():
        return []

    items: list[dict[str, Any]] = []
    for path in sorted(_BUILTIN_DIR.glob("*.yaml")):
        try:
            tpl = load_builtin_template(path.stem)
            items.append(
                {
                    "template_id": tpl.template_id,
                    "name": tpl.name,
                    "version": tpl.version,
                    "description": tpl.description,
                    "node_count": len(tpl.spec_dict.get("nodes") or []),
                    "edge_count": len(tpl.spec_dict.get("edges") or []),
                }
            )
        except (ValueError, TemplateNotFoundError) as e:
            logger.warning("跳过无效内置模板 %s: %s", path, e)
    return items
