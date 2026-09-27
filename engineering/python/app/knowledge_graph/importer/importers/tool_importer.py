"""``tools.json`` 导入器（M1.3 重构 P1-4）。

职责
----
- 读取 ``tools.json``，按 ``(series, diameter_mm)`` 去重，写入 ``tool`` 节点。
- 生成 ``Tool SUITABLE_FOR Feature`` 与 ``Tool SUITABLE_FOR Material`` 关系。
- 通过 :func:`_retry_with_backoff` 提供指数退避重试。
- 产出 :class:`ImportStats` 统计。
"""

from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any

from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.importer.importers._common import (
    _ALL_MATERIAL_NAMES,
    EDGE_SUITABLE_FOR,
    ImportStats,
    NODE_TYPE_FEATURE,
    NODE_TYPE_MATERIAL,
    NODE_TYPE_TOOL,
    _SERIES_TO_FEATURES,
    _ToolDeduper,
    _load_json,
    _material_id_from_name,
    _resolve_default_path,
    _retry_with_backoff,
)

import logging

logger = logging.getLogger(__name__)


def import_tools(
    graph: GraphStore,
    *,
    source_path: Path | None = None,
    retries: int = 3,
) -> ImportStats:
    """导入 ``tools.json`` → 若干 ``tool`` 节点 + 工具→特征/材料关系。

    实体映射规则：
        - ``id``           → 节点 ``node_id``（基于 series+diameter 规整化）；
        - ``name``         → 节点 ``properties.name``；
        - ``series``       → 节点 ``properties.series``；
        - ``diameter_mm``  → 节点 ``properties.diameter_mm``；
        - ``material``     → 节点 ``properties.material``（刀具材料 HSS / carbide）；
        - ``application``  → 节点 ``properties.application``；
        - ``description``  → 节点 ``properties.description``。

    去重策略：按 ``(series, diameter_mm)`` 组合去重。

    关系生成：
        - 工具 → 适用 feature（基于 series 内置映射）；
        - 工具 → 适用 material（基于材料兼容性，所有材料均建立关系，confidence 因材料而异）。
    """
    default_path = _resolve_default_path("TOOLS_JSON")
    stats = ImportStats(
        source_file=str(source_path or default_path),
        node_type=NODE_TYPE_TOOL,
    )
    started = time.perf_counter()
    deduper = _ToolDeduper()

    def _do_import() -> None:
        data = _load_json(source_path or _resolve_default_path("TOOLS_JSON"))
        local_seen: set[str] = set()
        for raw in data:
            try:
                nid, is_dup = deduper.resolve(raw)
            except (ValueError, TypeError, KeyError) as exc:
                stats.failed += 1
                stats.error_messages.append(f"tool dedup error: {exc}")
                logger.warning("tool dedup error for raw=%s: %s", raw.get("id", "?"), exc)
                continue
            if not nid:
                stats.failed += 1
                stats.error_messages.append(f"tool skipped: missing series/diameter ({raw.get('id', '?')})")
                continue
            if is_dup or nid in local_seen or graph.has_node(nid):
                stats.duplicate += 1
                continue

            properties: dict[str, Any] = {
                "name": str(raw.get("name", "")).strip(),
                "series": str(raw.get("series", "")).strip(),
                "material": str(raw.get("material", "")).strip(),
                "application": str(raw.get("application", "")).strip(),
                "raw_id": str(raw.get("id", "")).strip(),
                "description": str(raw.get("description", "")).strip(),
            }
            if raw.get("diameter_mm") is not None:
                properties["diameter_mm"] = raw["diameter_mm"]
            graph.add_node(NODE_TYPE_TOOL, nid, properties)
            local_seen.add(nid)
            stats.success += 1
            stats.node_type_breakdown[NODE_TYPE_TOOL] = stats.node_type_breakdown.get(NODE_TYPE_TOOL, 0) + 1

            # 关系 1：Tool -> Feature (SUITABLE_FOR)
            series = properties["series"]
            for feature_id, feature_name, feature_type in _SERIES_TO_FEATURES.get(series, []):
                # 确保 feature 节点存在
                if not graph.has_node(feature_id):
                    graph.add_node(
                        NODE_TYPE_FEATURE,
                        feature_id,
                        {
                            "name": feature_name,
                            "feature_type": feature_type,
                        },
                    )
                    stats.node_type_breakdown[NODE_TYPE_FEATURE] = (
                        stats.node_type_breakdown.get(NODE_TYPE_FEATURE, 0) + 1
                    )
                try:
                    graph.add_edge(
                        nid,
                        feature_id,
                        EDGE_SUITABLE_FOR,
                        {
                            "confidence": 0.9,
                            "source": "manual",
                            "evidence": "tools.json series->feature mapping",
                        },
                    )
                    stats.edges_added += 1
                    stats.edge_type_breakdown[EDGE_SUITABLE_FOR] = (
                        stats.edge_type_breakdown.get(EDGE_SUITABLE_FOR, 0) + 1
                    )
                except ValueError as exc:
                    stats.error_messages.append(f"tool->feature edge error: {exc}")

            # 关系 2：Tool -> Material (SUITABLE_FOR)
            # 按材料 category 与刀具材料 (HSS / carbide) 设置 confidence
            tool_mat = properties["material"].lower()
            for material_name in _ALL_MATERIAL_NAMES:
                material_id = _material_id_from_name(material_name)
                if not graph.has_node(material_id):
                    # 若 material 节点尚未导入，先建占位（待 materials.json 真实导入会被覆盖）
                    graph.add_node(
                        NODE_TYPE_MATERIAL,
                        material_id,
                        {
                            "name": material_name,
                            "category": "",
                            "description": "",
                        },
                    )
                    stats.node_type_breakdown[NODE_TYPE_MATERIAL] = (
                        stats.node_type_breakdown.get(NODE_TYPE_MATERIAL, 0) + 1
                    )
                # 简单的 confidence 计算：HSS 适合软材料，硬质合金适合所有
                if "hss" in tool_mat:
                    if "铝" in material_name or "Al" in material_name:
                        conf = 0.9
                    else:
                        conf = 0.75
                elif "carbide" in tool_mat or "硬质合金" in tool_mat:
                    if "不锈钢" in material_name:
                        conf = 0.8
                    elif "40Cr" in material_name:
                        conf = 0.9
                    else:
                        conf = 0.95
                else:
                    conf = 0.7
                try:
                    graph.add_edge(
                        nid,
                        material_id,
                        EDGE_SUITABLE_FOR,
                        {
                            "confidence": conf,
                            "source": "manual",
                            "evidence": "tools.json series->material compatibility",
                        },
                    )
                    stats.edges_added += 1
                    stats.edge_type_breakdown[EDGE_SUITABLE_FOR] = (
                        stats.edge_type_breakdown.get(EDGE_SUITABLE_FOR, 0) + 1
                    )
                except ValueError as exc:
                    stats.error_messages.append(f"tool->material edge error: {exc}")

    try:
        _retry_with_backoff(_do_import, retries=retries, label="import_tools")
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        stats.failed += 1
        stats.error_messages.append(f"tools import aborted: {exc}")
        logger.error("import_tools failed: %s", traceback.format_exc())

    stats.elapsed_ms = (time.perf_counter() - started) * 1000.0
    return stats
