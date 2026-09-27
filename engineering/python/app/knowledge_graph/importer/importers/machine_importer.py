"""``machines.json`` 导入器（M1.3 重构 P1-4）。

职责
----
- 读取 ``machines.json``，按 ``id`` 去重，写入 ``machine`` 节点。
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
    NODE_TYPE_MACHINE,
    _MachineDeduper,
    _load_json,
    _resolve_default_path,
    _retry_with_backoff,
)

import logging

logger = logging.getLogger(__name__)


def import_machines(
    graph: GraphStore,
    *,
    source_path: Path | None = None,
    retries: int = 3,
) -> ImportStats:
    """导入 ``machines.json`` → 若干 ``machine`` 节点。

    实体映射规则：
        - ``id``            → 节点 ``node_id``（基于 id 规整化）；
        - ``name``          → 节点 ``properties.name``；
        - ``type``          → 节点 ``properties.type``；
        - 主轴 / 行程 / 刀具容量 / 冷却液压力等参数 → 节点 ``properties`` 平铺。

    去重策略：按 ``id`` 完全匹配。
    """
    default_path = _resolve_default_path("MACHINES_JSON")
    stats = ImportStats(
        source_file=str(source_path or default_path),
        node_type=NODE_TYPE_MACHINE,
    )
    started = time.perf_counter()
    deduper = _MachineDeduper()

    def _do_import() -> None:
        data = _load_json(source_path or _resolve_default_path("MACHINES_JSON"))
        local_seen: set[str] = set()
        for raw in data:
            try:
                nid, is_dup = deduper.resolve(raw)
            except (ValueError, TypeError, KeyError) as exc:
                stats.failed += 1
                stats.error_messages.append(f"machine dedup error: {exc}")
                logger.warning("machine dedup error for raw=%s: %s", raw.get("name", "?"), exc)
                continue
            if not nid:
                stats.failed += 1
                stats.error_messages.append(f"machine skipped: missing id ({raw.get('name', '?')})")
                continue
            if is_dup or nid in local_seen or graph.has_node(nid):
                stats.duplicate += 1
                continue

            properties: dict[str, Any] = {
                "raw_id": nid.split("machine-", 1)[-1] if nid.startswith("machine-") else nid,
                "name": str(raw.get("name", "")).strip(),
                "type": str(raw.get("type", "")).strip(),
                "description": str(raw.get("description", "")).strip()
                or f"{raw.get('name', '')} - {raw.get('type', '')}",
            }
            for k in (
                "spindle_power_kw",
                "spindle_speed_rpm",
                "feed_rapid_mmmin",
                "feed_cutting_max_mmmin",
                "max_cutting_force_n",
                "table_size_mm",
                "travel_xyz_mm",
                "tool_changer_capacity",
                "coolant_pressure_mpa",
                "positioning_accuracy_mm",
                "max_turning_diameter_mm",
                "max_turning_length_mm",
            ):
                if k in raw and raw[k] is not None:
                    properties[k] = raw[k]
            graph.add_node(NODE_TYPE_MACHINE, nid, properties)
            local_seen.add(nid)
            stats.success += 1
            stats.node_type_breakdown[NODE_TYPE_MACHINE] = stats.node_type_breakdown.get(NODE_TYPE_MACHINE, 0) + 1

    try:
        _retry_with_backoff(_do_import, retries=retries, label="import_machines")
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        stats.failed += 1
        stats.error_messages.append(f"machines import aborted: {exc}")
        logger.error("import_machines failed: %s", traceback.format_exc())

    stats.elapsed_ms = (time.perf_counter() - started) * 1000.0
    return stats
