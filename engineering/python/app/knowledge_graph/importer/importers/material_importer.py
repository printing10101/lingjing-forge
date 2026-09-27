"""``materials.json`` 导入器（M1.3 重构 P1-4）。

职责
----
- 读取 ``materials.json``，按 ``name`` 去重，写入 ``material`` 节点。
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
    ImportStats,
    NODE_TYPE_MATERIAL,
    _MaterialDeduper,
    _load_json,
    _resolve_default_path,
    _retry_with_backoff,
)

import logging

logger = logging.getLogger(__name__)


def import_materials(
    graph: GraphStore,
    *,
    source_path: Path | None = None,
    retries: int = 3,
) -> ImportStats:
    """导入 ``materials.json`` → 若干 ``material`` 节点。

    实体映射规则：
        - ``id``         → 节点 ``node_id``（基于 name 规整化）；
        - ``name``       → 节点 ``properties.name``；
        - ``category``   → 节点 ``properties.category``；
        - 物理属性（density / hardness / tensile / cutting_performance）→ 节点 ``properties``；
        - ``description`` → 节点 ``properties.description``。

    去重策略：按 ``name`` 字段完全匹配。

    Returns:
        :class:`ImportStats`
    """
    default_path = _resolve_default_path("MATERIALS_JSON")
    stats = ImportStats(
        source_file=str(source_path or default_path),
        node_type=NODE_TYPE_MATERIAL,
    )
    started = time.perf_counter()
    deduper = _MaterialDeduper()

    def _do_import() -> None:
        # 加载 JSON（可能瞬时 I/O 失败，重试覆盖）
        data = _load_json(source_path or _resolve_default_path("MATERIALS_JSON"))
        # 记录本次已新增的 node_id，避免同一文件内重复插入
        local_seen: set[str] = set()
        for raw in data:
            try:
                nid, is_dup = deduper.resolve(raw)
            except (ValueError, TypeError, KeyError) as exc:
                stats.failed += 1
                stats.error_messages.append(f"material dedup error: {exc}")
                logger.warning("material dedup error for raw=%s: %s", raw.get("id", "?"), exc)
                continue
            if not nid:
                stats.failed += 1
                stats.error_messages.append(f"material skipped: missing name ({raw.get('id', '?')})")
                continue
            if is_dup or nid in local_seen or graph.has_node(nid):
                stats.duplicate += 1
                continue
            # 构造属性
            properties: dict[str, Any] = {
                "name": str(raw.get("name", "")).strip(),
                "category": str(raw.get("category", "")).strip(),
                "raw_id": str(raw.get("id", "")).strip(),
                "description": str(raw.get("description", "")).strip(),
            }
            for k in (
                "density_gcm3",
                "hardness_hb",
                "tensile_strength_mpa",
                "cutting_performance",
            ):
                if k in raw and raw[k] is not None:
                    properties[k] = raw[k]
            # 添加节点
            graph.add_node(NODE_TYPE_MATERIAL, nid, properties)
            local_seen.add(nid)
            stats.success += 1
            stats.node_type_breakdown[NODE_TYPE_MATERIAL] = stats.node_type_breakdown.get(NODE_TYPE_MATERIAL, 0) + 1

    try:
        _retry_with_backoff(_do_import, retries=retries, label="import_materials")
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        stats.failed += 1
        stats.error_messages.append(f"materials import aborted: {exc}")
        logger.error("import_materials failed: %s", traceback.format_exc())

    stats.elapsed_ms = (time.perf_counter() - started) * 1000.0
    return stats
