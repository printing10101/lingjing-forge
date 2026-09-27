"""反思去重 mixin（从 reflector 拆出）。"""

from __future__ import annotations

import logging
from typing import Any

from app.dreaming._reflector_models import DeduplicationResult

logger = logging.getLogger(__name__)


class _DedupMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    store: Any

    def _deduplicate_memories(self) -> DeduplicationResult:
        """合并重复的 memory 条目。

        策略：
            - 按 entity 分组
            - 同一 entity 下 content 相似度高的合并（保留 validation_count 最高的）
            - 合并后累计 validation_count 和 confidence
        """
        all_entries = self.store.read_all()
        result = DeduplicationResult()

        # 按 entity 分组
        by_entity: dict[str, list[dict[str, Any]]] = {}
        for entry in all_entries:
            entity = entry["properties"].get("entity", "unknown")
            by_entity.setdefault(entity, []).append(entry)

        for entity, entries in by_entity.items():
            if len(entries) <= 1:
                # 无重复
                result.kept_node_ids.extend(e["node_id"] for e in entries)
                continue

            # 简单文本相似度：content 完全相同视为重复
            # （LLM 语义相似度在 _surface_insights 阶段处理）
            content_groups: dict[str, list[dict[str, Any]]] = {}
            for entry in entries:
                content = entry["properties"].get("content", "").strip()
                content_groups.setdefault(content, []).append(entry)

            for content, group in content_groups.items():
                if len(group) == 1:
                    result.kept_node_ids.append(group[0]["node_id"])
                    continue

                # 合并：保留 validation_count 最高的节点作为主节点
                group.sort(
                    key=lambda e: e["properties"].get("validation_count", 0),
                    reverse=True,
                )
                primary = group[0]
                merged_count = sum(e["properties"].get("validation_count", 0) for e in group)
                merged_confidence = max(e["properties"].get("confidence", 0.5) for e in group)

                # 更新主节点
                self.store.update_observation(
                    node_id=primary["node_id"],
                    confidence=merged_confidence,
                    increment_validation=False,
                )
                # 手动累加 validation_count（update_observation 只支持 +1）
                primary_props = dict(primary["properties"])
                primary_props["validation_count"] = merged_count
                primary_props["merged_from"] = [e["node_id"] for e in group[1:]]
                self.store.graph.update_node_properties(primary["node_id"], primary_props)

                result.kept_node_ids.append(primary["node_id"])
                # 移除重复节点（保留审计记录：不移除，标记 deprecated）
                for dup in group[1:]:
                    result.removed_node_ids.append(dup["node_id"])
                    self.store.update_observation(
                        node_id=dup["node_id"],
                        confidence=0.0,  # 降为 0 表示已合并
                    )
                    # 标记为 deprecated
                    self.store.graph.update_node_properties(
                        dup["node_id"],
                        {"deprecated": True, "merged_into": primary["node_id"]},
                    )

                result.merged_count += len(group) - 1

        logger.info(
            "去重完成：merged=%d, removed=%d, kept=%d",
            result.merged_count,
            len(result.removed_node_ids),
            len(result.kept_node_ids),
        )
        return result


# 阶段 2：过时更新
