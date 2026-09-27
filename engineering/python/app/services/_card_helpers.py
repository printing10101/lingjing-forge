"""资源卡片服务辅助函数（从 resource_card_service 拆分，D5）。

模块级纯函数：JSON 序列化 / ORM 转换 / 时间解析 / 图谱层构建。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

from app.contracts.resource_card import DatasetReadme, ModelArtifact
from app.database.models.resource_card import (
    ModelArtifact as ModelArtifactORM,
    DatasetReadme as DatasetReadmeORM,
)


def _json_dumps(value: Any) -> str:
    """安全 JSON 序列化."""
    if value is None:
        return "[]"
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    """安全 JSON 反序列化."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _orm_to_model_artifact(orm: ModelArtifactORM) -> ModelArtifact:
    """ORM → dataclass."""
    return ModelArtifact(
        model_id=str(orm.id),
        model_uri=str(orm.model_uri),
        name=str(orm.name),
        model_type=str(orm.model_type),
        version=str(orm.version),
        framework=str(orm.framework),
        storage_uri=str(orm.storage_uri),
        metrics=orm.metrics,
        metrics_history=orm.metrics_history,
        readme_md=str(orm.readme_md),
        tags=orm.tags,
        owner_id=str(orm.owner_id),
        status=str(orm.status),
        created_at=cast(datetime, orm.created_at) if orm.created_at else None,
        updated_at=cast(datetime, orm.updated_at) if orm.updated_at else None,
    )


def _orm_to_dataset_readme(orm: DatasetReadmeORM) -> DatasetReadme:
    """ORM → dataclass."""
    return DatasetReadme(
        readme_id=str(orm.id),
        dataset_id=str(orm.dataset_id),
        readme_md=str(orm.readme_md),
        updated_by=str(orm.updated_by),
        version=str(orm.version) if orm.version else None,
        updated_at=cast(datetime, orm.updated_at) if orm.updated_at else None,
    )


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """解析 ISO 字符串为 datetime（失败返回 None）."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _build_layers(
    target_uri: str,
    records: list,
    max_depth: int,
    max_nodes_per_layer: int,
    *,
    direction: str,
) -> list[list[str]]:
    """BFS 按"层"分组 lineage records，返回每层的 URI 列表.

    upstream 方向：record.target 是当前层节点，record.inputs 是上一层
    downstream 方向：record.target 是上一层节点，record.outputs 是当前层

    Args:
        target_uri: 起点 URI
        records: LineageRecord 列表（BFS 顺序，已由 store 保证）
        max_depth: 最大深度
        max_nodes_per_layer: 每层保留的最大节点数
        direction: "upstream" 或 "downstream"

    Returns:
        [[layer1_uris], [layer2_uris], ...]，每层最多 max_nodes_per_layer 个 URI
    """
    if not records:
        return []

    # 构造邻接表
    # upstream: target 的 inputs 是它的上游节点
    # downstream: target 的 outputs 是它的下游节点
    adjacency: dict[str, list[str]] = {}
    for rec in records:
        if direction == "upstream":
            # rec.target 的上游是 rec.inputs
            for input_uri in rec.inputs:
                adjacency.setdefault(rec.target, []).append(input_uri)
        else:
            # downstream: rec.target 的下游是 rec.outputs
            for output_uri in rec.outputs:
                adjacency.setdefault(rec.target, []).append(output_uri)

    # BFS 分层
    layers: list[list[str]] = []
    visited: set[str] = {target_uri}
    current_layer: list[str] = [target_uri]

    for _ in range(max_depth):
        next_layer_uris: list[str] = []
        next_layer_set: set[str] = set()
        for node in current_layer:
            for neighbor in adjacency.get(node, []):
                if neighbor not in visited and neighbor not in next_layer_set:
                    next_layer_set.add(neighbor)
                    next_layer_uris.append(neighbor)
        if not next_layer_uris:
            break
        # 限制每层节点数
        if len(next_layer_uris) > max_nodes_per_layer:
            next_layer_uris = next_layer_uris[:max_nodes_per_layer]
        layers.append(next_layer_uris)
        visited.update(next_layer_uris)
        current_layer = next_layer_uris

    return layers


def _collect_unique_nodes(records: list, target_uri: str) -> set[str]:
    """从 LineageRecord 列表收集所有唯一节点 URI（不含 target_uri 自身）."""
    nodes: set[str] = set()
    for rec in records:
        nodes.add(rec.target)
        for uri in rec.inputs:
            if uri != target_uri:
                nodes.add(uri)
        for uri in rec.outputs:
            if uri != target_uri:
                nodes.add(uri)
    # target_uri 自身可能出现在 records 的 target 中（作为下游的"上游"）
    nodes.discard(target_uri)
    return nodes


def _extract_key_path(target_uri: str, upstream_records: list) -> list[str]:
    """提取 target → 根节点的最短路径（用于卡片侧栏展示）.

    算法：BFS 找到第一个没有上游的"根"节点，回溯路径。
    若存在多个根，取 BFS 顺序的第一个。

    Returns:
        [target_uri, intermediate_uri_1, ..., root_uri]，若无可达根返回 [target_uri]
    """
    if not upstream_records:
        return [target_uri]

    # 构造上游邻接表：target inputs
    adjacency: dict[str, list[str]] = {}
    for rec in upstream_records:
        for input_uri in rec.inputs:
            adjacency.setdefault(rec.target, []).append(input_uri)

    # BFS 找最短路径到第一个根节点（无上游的节点）
    from collections import deque

    queue: deque = deque([(target_uri, [target_uri])])
    visited: set[str] = {target_uri}

    while queue:
        current, path = queue.popleft()
        upstreams = adjacency.get(current, [])
        if not upstreams:
            # 当前节点无上游，是根节点
            return path
        for upstream in upstreams:
            if upstream not in visited:
                visited.add(upstream)
                queue.append((upstream, path + [upstream]))

    # 未找到根节点（可能存在环），返回当前最长路径
    return [target_uri]
