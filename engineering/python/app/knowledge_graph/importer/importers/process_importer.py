"""``process_rules.json`` 导入器（M1.3 重构 P1-4）。

职责
----
- 读取 ``process_rules.json``，借助 :class:`RuleParser` 解析 IF-THEN 规则。
- 写入 ``process`` 节点，并生成 ``Process APPLIED_TO Feature``、
  ``Process USED Tool`` 关系。
- 通过 :func:`_retry_with_backoff` 提供指数退避重试。
- 产出 :class:`ImportStats` 统计。
"""

from __future__ import annotations

import re
import time
import traceback
from pathlib import Path
from typing import Any

from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.importer.importers._common import (
    EDGE_APPLIED_TO,
    EDGE_USED,
    ImportStats,
    NODE_TYPE_FEATURE,
    NODE_TYPE_PROCESS,
    NODE_TYPE_TOOL,
    _FEATURE_TO_REPRESENTATIVE_TOOLS,
    _load_json,
    _resolve_default_path,
    _retry_with_backoff,
    _slugify_id,
)
from app.knowledge_graph.importer.rule_parser import RuleParser

import logging

logger = logging.getLogger(__name__)


def import_process_rules(
    graph: GraphStore,
    *,
    source_path: Path | None = None,
    retries: int = 3,
    rule_parser: RuleParser | None = None,
) -> ImportStats:
    """导入 ``process_rules.json`` → Process + Feature 节点 + 关系。

    实体映射规则：
        - Process ``id``   → 节点 ``node_id``（``process-<id>``）；
        - ``name``         → 节点 ``properties.name``；
        - ``category``     → 节点 ``properties.category``；
        - ``description``  → 节点 ``properties.description``；
        - ``details``      → 节点 ``properties.details``。

    关系生成（基于关键词匹配 + 启发式）：
        - Process ``APPLIED_TO`` Feature（IF 部分涉及到的几何特征）；
        - Process ``USED`` Tool（基于涉及的 feature 推断代表工具）。

    去重策略：Process 按 ``id`` 完全匹配。
    """
    default_path = _resolve_default_path("PROCESS_RULES_JSON")
    stats = ImportStats(
        source_file=str(source_path or default_path),
        node_type=NODE_TYPE_PROCESS,
    )
    started = time.perf_counter()
    parser = rule_parser or RuleParser()

    def _do_import() -> None:
        data = _load_json(source_path or _resolve_default_path("PROCESS_RULES_JSON"))
        local_seen: set[str] = set()
        for raw in data:
            process_id = str(raw.get("id", "")).strip()
            if not process_id:
                stats.failed += 1
                stats.error_messages.append("process rule skipped: missing id")
                continue
            if process_id in local_seen:
                stats.duplicate += 1
                continue
            local_seen.add(process_id)

            try:
                parsed = parser.parse_single_rule(raw)
            except (ValueError, TypeError, KeyError) as exc:
                stats.failed += 1
                stats.error_messages.append(f"process rule parse error ({process_id}): {exc}")
                logger.warning("process rule parse error for id=%s: %s", process_id, exc)
                continue

            node_id = f"process-{_slugify_id(process_id, prefix='').lstrip('-')}"
            # 简化：直接用 process_id 拼成 node_id
            slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", process_id).strip("-").lower()
            if not slug:
                slug = "rule"
            node_id = f"process-{slug[:80]}"

            if node_id in local_seen or graph.has_node(node_id):
                stats.duplicate += 1
                continue
            local_seen.add(node_id)

            properties: dict[str, Any] = {
                "raw_id": process_id,
                "name": parsed.process_name or process_id,
                "category": parsed.process_category,
                "description": parsed.process_description,
                "details": parsed.process_details or {},
            }
            graph.add_node(NODE_TYPE_PROCESS, node_id, properties)
            stats.success += 1
            stats.node_type_breakdown[NODE_TYPE_PROCESS] = stats.node_type_breakdown.get(NODE_TYPE_PROCESS, 0) + 1

            # 关系 1：Process APPLIED_TO Feature（IF 条件部分）
            tools_used_set: set[str] = set()
            for feature in parsed.features:
                fid = feature.feature_id
                if not graph.has_node(fid):
                    graph.add_node(
                        NODE_TYPE_FEATURE,
                        fid,
                        {
                            "name": feature.name,
                            "feature_type": feature.feature_type,
                        },
                    )
                    stats.node_type_breakdown[NODE_TYPE_FEATURE] = (
                        stats.node_type_breakdown.get(NODE_TYPE_FEATURE, 0) + 1
                    )
                try:
                    graph.add_edge(
                        node_id,
                        fid,
                        EDGE_APPLIED_TO,
                        {
                            "confidence": parsed.confidence,
                            "source": parsed.source,
                            "evidence": ("process_rules.json keyword match: " + (parsed.process_name or process_id)),
                        },
                    )
                    stats.edges_added += 1
                    stats.edge_type_breakdown[EDGE_APPLIED_TO] = stats.edge_type_breakdown.get(EDGE_APPLIED_TO, 0) + 1
                except ValueError as exc:
                    stats.error_messages.append(f"process->feature edge error: {exc}")
                # 收集可能使用的工具
                for tool_id in _FEATURE_TO_REPRESENTATIVE_TOOLS.get(fid, []):
                    tools_used_set.add(tool_id)

            # 关系 2：Process USED Tool（THEN 动作部分启发式）
            # 如果规则没有抽到任何 feature，使用通用刀具
            if not parsed.features:
                tools_used_set.update(["tool-endmill_6", "tool-twist_drill_5", "tool-face_mill_50"])
            for tool_id in sorted(tools_used_set):
                if not graph.has_node(tool_id):
                    # 工具可能尚未导入（取决于调用顺序），先建占位节点
                    graph.add_node(
                        NODE_TYPE_TOOL,
                        tool_id,
                        {
                            "name": tool_id,
                            "series": "",
                            "application": "",
                            "description": "auto-created from rule parser",
                        },
                    )
                    stats.node_type_breakdown[NODE_TYPE_TOOL] = stats.node_type_breakdown.get(NODE_TYPE_TOOL, 0) + 1
                try:
                    graph.add_edge(
                        node_id,
                        tool_id,
                        EDGE_USED,
                        {
                            "confidence": parsed.confidence * 0.9,
                            "source": parsed.source,
                            "evidence": ("process_rules.json inferred USED tools"),
                        },
                    )
                    stats.edges_added += 1
                    stats.edge_type_breakdown[EDGE_USED] = stats.edge_type_breakdown.get(EDGE_USED, 0) + 1
                except ValueError as exc:
                    stats.error_messages.append(f"process->tool edge error: {exc}")

    try:
        _retry_with_backoff(_do_import, retries=retries, label="import_process_rules")
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        stats.failed += 1
        stats.error_messages.append(f"process_rules import aborted: {exc}")
        logger.error("import_process_rules failed: %s", traceback.format_exc())

    stats.elapsed_ms = (time.perf_counter() - started) * 1000.0
    return stats
