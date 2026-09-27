"""DAG 工作流校验器.

复用契约层 WorkflowSpec.validate() 的无环检测、节点引用合法性检查，
并额外校验：
    1. 工作流至少有一个起始节点（入度为 0）
    2. 工作流至少有一个终止节点（出度为 0）
    3. 节点 task_type 在 ITaskRegistry 中已注册（可选，由 runner 在运行时检查）
"""

from __future__ import annotations


from app.contracts import ITaskRegistry, WorkflowSpec


class WorkflowValidationError(Exception):
    """工作流校验失败异常.

    Attributes:
        errors: 校验错误信息列表（每条对应一个具体问题）。
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


def validate_workflow_spec(
    spec: WorkflowSpec,
    *,
    registry: ITaskRegistry | None = None,
) -> list[str]:
    """校验工作流规格.

    Args:
        spec: 工作流规格.
        registry: 可选的任务注册表，提供时额外校验 task_type 是否已注册.

    Returns:
        错误信息列表，空列表表示校验通过.
    """
    # 1. 契约层基础校验（节点唯一性、边引用、DAG 无环、artifact 引用）
    errors = list(spec.validate())

    if errors:
        return errors

    # 2. 起始/终止节点存在性
    node_ids = {n.node_id for n in spec.nodes}
    upstream_set = {e.upstream for e in spec.edges}
    downstream_set = {e.downstream for e in spec.edges}

    start_nodes = node_ids - downstream_set
    end_nodes = node_ids - upstream_set

    if not start_nodes:
        errors.append("工作流没有起始节点（所有节点都有上游依赖，存在环或配置错误）")
    if not end_nodes:
        errors.append("工作流没有终止节点（所有节点都有下游依赖，存在环或配置错误）")

    # 3. task_type 注册检查（可选）
    if registry is not None:
        registered_types: set[str] = set()
        try:
            for info in registry.list():
                # info 字典中应有 name 字段（task_type）
                name = info.get("name") or info.get("task_type")
                if name:
                    registered_types.add(name)
        except Exception:
            # 注册表查询失败时不阻断校验，由 runner 在执行时抛出
            registered_types = set()

        if registered_types:
            for node in spec.nodes:
                if node.task_type not in registered_types:
                    errors.append(f"节点 {node.node_id} 的 task_type '{node.task_type}' 未在注册表中注册")

    return errors


def validate_or_raise(
    spec: WorkflowSpec,
    *,
    registry: ITaskRegistry | None = None,
) -> None:
    """校验工作流规格，失败时抛出 WorkflowValidationError."""
    errors = validate_workflow_spec(spec, registry=registry)
    if errors:
        raise WorkflowValidationError(errors)
