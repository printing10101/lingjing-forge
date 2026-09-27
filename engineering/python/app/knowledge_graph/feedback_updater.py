"""知识图谱反馈更新器

实现基于实测数据的知识图谱更新逻辑，包括：
- Process节点属性更新（加工参数、可信度等）
- Tool-Material关系可信度更新
- Process-Feature关系可信度更新
- 基于实测结果的置信度调整

设计原则：
- 只负责数据搬运和图谱更新，不包含数据分析或决策逻辑
- 基于record_id实现幂等处理，防止重复更新
- 支持批量更新操作
- 更新操作应保证原子性
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from app.knowledge_graph.graph_store import GraphStore

logger = logging.getLogger(__name__)

# 与 graph_store._NODE_ID_PATTERN 对齐的本地副本（避免依赖私有符号）
_NODE_ID_SAFE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.\-]{0,127}$")


class FeedbackUpdater:
    """知识图谱反馈更新器

    负责将加工实测数据反馈到知识图谱，更新相关节点和关系的属性与可信度。

    更新策略：
    1. Process节点：更新加工参数统计信息（平均值、标准差、样本数等）
    2. Tool-Material关系：根据加工结果调整SUITABLE_FOR关系的可信度
    3. Process-Feature关系：根据加工结果调整APPLIED_TO关系的可信度
    4. 可信度调整：首次合格提升置信度，不合格降低置信度
    """

    def __init__(self, graph_store: GraphStore | None = None):
        """初始化反馈更新器

        Args:
            graph_store: 知识图谱存储实例，为None时自动创建
        """
        self.graph_store = graph_store if graph_store is not None else GraphStore(auto_load=False)
        logger.info("FeedbackUpdater initialized")

    def update_from_machining_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """从加工记录更新知识图谱

        Args:
            record: 加工记录字典，包含以下字段：
                - record_id: 记录ID
                - machine_id: 机床ID
                - tool_id: 刀具ID
                - workpiece_material: 工件材料
                - process_plan: 工艺计划
                - first_pass_acceptance: 首次合格率
                - actual_dimensions: 实测尺寸
                - surface_roughness: 表面粗糙度

        Returns:
            更新统计信息字典，包含：
            - process_nodes_updated: 更新的Process节点数
            - tool_material_edges_updated: 更新的Tool-Material关系数
            - process_feature_edges_updated: 更新的Process-Feature关系数
            - confidence_adjustments: 可信度调整次数
        """
        record_id = record.get("record_id")
        if not record_id:
            raise ValueError("record must contain 'record_id'")

        stats = {
            "process_nodes_updated": 0,
            "tool_material_edges_updated": 0,
            "process_feature_edges_updated": 0,
            "confidence_adjustments": 0,
        }

        # 1. 更新Process节点
        process_plan = record.get("process_plan", {})
        if process_plan:
            stats["process_nodes_updated"] = self._update_process_nodes(record, process_plan)

        # 2. 更新Tool-Material关系
        tool_id = record.get("tool_id")
        material = record.get("workpiece_material")
        if tool_id and material:
            stats["tool_material_edges_updated"] = self._update_tool_material_relationship(tool_id, material, record)

        # 3. 更新Process-Feature关系
        if process_plan:
            stats["process_feature_edges_updated"] = self._update_process_feature_relationships(record, process_plan)

        # 4. 调整可信度
        first_pass = record.get("first_pass_acceptance", False)
        stats["confidence_adjustments"] = self._adjust_confidence_based_on_result(record, first_pass)

        logger.info(f"Knowledge graph updated for record {record_id}: {stats}")

        return stats

    def _update_process_nodes(self, record: dict[str, Any], process_plan: dict[str, Any]) -> int:
        """更新Process节点属性

        根据加工记录更新Process节点的统计信息，包括：
        - 加工参数平均值
        - 样本数量
        - 合格率统计

        Args:
            record: 加工记录
            process_plan: 工艺计划

        Returns:
            更新的Process节点数量
        """
        updated_count = 0

        # 提取工艺步骤
        steps = process_plan.get("steps", [])
        if not steps:
            return updated_count

        for step in steps:
            process_id = step.get("process_id")
            if not process_id:
                continue

            # 确保Process节点存在
            if not self.graph_store.has_node(process_id):
                self.graph_store.add_node(
                    node_type="process",
                    node_id=process_id,
                    properties={
                        "name": step.get("name", process_id),
                        "sample_count": 0,
                        "success_count": 0,
                        "avg_surface_roughness": 0.0,
                    },
                )

            # 更新节点属性
            current_node = self.graph_store.get_node(process_id)
            if not current_node:
                continue

            props = current_node.get("properties", {})
            sample_count = props.get("sample_count", 0)
            success_count = props.get("success_count", 0)
            avg_roughness = props.get("avg_surface_roughness", 0.0)

            # 更新统计信息
            new_sample_count = sample_count + 1
            first_pass = record.get("first_pass_acceptance", False)
            new_success_count = success_count + (1 if first_pass else 0)

            # 计算新的平均粗糙度
            current_roughness = record.get("surface_roughness", 0.0)
            if sample_count == 0:
                new_avg_roughness = current_roughness
            else:
                new_avg_roughness = (avg_roughness * sample_count + current_roughness) / new_sample_count

            # 更新节点属性
            self.graph_store.update_node_properties(
                process_id,
                {
                    "sample_count": new_sample_count,
                    "success_count": new_success_count,
                    "success_rate": new_success_count / new_sample_count if new_sample_count > 0 else 0.0,
                    "avg_surface_roughness": new_avg_roughness,
                    "last_updated": record.get("record_id"),
                },
            )

            updated_count += 1
            # P2-批次2 修复：改用 %s 懒求值。批量节点更新循环内，
            # debug 级别关闭时避免字符串插值开销。
            logger.debug("Updated Process node %s: sample_count=%s", process_id, new_sample_count)

        return updated_count

    # material 可读名 → 合法 node_id 的稳定映射缓存（进程级，避免重复散列）
    _material_id_cache: dict[str, str] = {}

    @classmethod
    def _material_node_id(cls, material: str) -> str:
        """把材料可读名归一为图存储允许的 node_id（幂等）。

        graph_store 的 node_id 模式为 ``^[a-zA-Z_][a-zA-Z0-9_.\\-]{0,127}$``，
        而加工记录的 workpiece_material 通常是可读名（如 "45号钢"），
        直接入图会被校验拒绝（导致整条回灌链路中断）。
        归一规则：
        - 已符合模式的输入原样返回（兼容既有节点 id）；
        - 否则取 ``material-<ASCII清洗>-<md5前8位>``，同一材料名恒定映射。
        """
        if cls._material_id_cache.get(material):
            return cls._material_id_cache[material]

        node_id: str
        if _NODE_ID_SAFE_PATTERN.match(material):
            node_id = material
        else:
            sanitized = re.sub(r"[^a-zA-Z0-9_.\-]+", "_", material).strip("_") or "mat"
            digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]
            node_id = f"material-{sanitized[:80]}-{digest}"

        cls._material_id_cache[material] = node_id
        return node_id

    def _update_tool_material_relationship(self, tool_id: str, material: str, record: dict[str, Any]) -> int:
        """更新Tool-Material关系

        根据加工结果调整刀具-材料适配关系的可信度。

        Args:
            tool_id: 刀具ID
            material: 材料ID（可读名，如 "45号钢"；入图前经 _material_node_id 归一）
            record: 加工记录

        Returns:
            更新的关系数量（0或1）
        """
        material_node_id = self._material_node_id(material)

        # 确保节点存在
        if not self.graph_store.has_node(tool_id):
            self.graph_store.add_node(node_type="tool", node_id=tool_id, properties={"name": tool_id})

        if not self.graph_store.has_node(material_node_id):
            self.graph_store.add_node(
                node_type="material",
                node_id=material_node_id,
                properties={"name": material},
            )

        # 检查关系是否存在
        edge_type = "SUITABLE_FOR"
        if not self.graph_store.has_edge(tool_id, material_node_id, edge_type):
            # 创建新关系
            initial_confidence = 0.5
            self.graph_store.add_edge(
                source_id=tool_id,
                target_id=material_node_id,
                edge_type=edge_type,
                properties={
                    "confidence": initial_confidence,
                    "sample_count": 1,
                    "success_count": 1 if record.get("first_pass_acceptance", False) else 0,
                },
            )
            logger.debug("Created new %s relationship: %s -> %s", edge_type, tool_id, material_node_id)
            return 1

        # 更新现有关系
        edge = self.graph_store.get_edge(tool_id, material_node_id, edge_type)
        if not edge:
            return 0

        props = edge.get("properties", {})
        sample_count = props.get("sample_count", 0)
        success_count = props.get("success_count", 0)

        # 更新统计
        new_sample_count = sample_count + 1
        first_pass = record.get("first_pass_acceptance", False)
        new_success_count = success_count + (1 if first_pass else 0)

        # 根据成功率调整可信度
        success_rate = new_success_count / new_sample_count if new_sample_count > 0 else 0.0
        # 可信度 = 基础可信度 * 成功率 + 0.2（保证最低可信度）
        new_confidence = min(0.5 * success_rate + 0.2, 1.0)

        # 更新关系属性
        self.graph_store.update_edge_properties(
            source_id=tool_id,
            target_id=material_node_id,
            edge_type=edge_type,
            properties={
                "confidence": new_confidence,
                "sample_count": new_sample_count,
                "success_count": new_success_count,
                "success_rate": success_rate,
                "last_updated": record.get("record_id"),
            },
        )

        logger.debug(
            f"Updated {edge_type} relationship {tool_id} -> {material}: "
            f"confidence={new_confidence:.3f}, sample_count={new_sample_count}"
        )

        return 1

    def _update_process_feature_relationships(self, record: dict[str, Any], process_plan: dict[str, Any]) -> int:
        """更新Process-Feature关系

        根据加工结果更新工艺-特征的适配关系。

        Args:
            record: 加工记录
            process_plan: 工艺计划

        Returns:
            更新的关系数量
        """
        updated_count = 0

        steps = process_plan.get("steps", [])
        for step in steps:
            process_id = step.get("process_id")
            feature_id = step.get("feature_id")

            if not process_id or not feature_id:
                continue

            # 确保节点存在
            if not self.graph_store.has_node(process_id):
                self.graph_store.add_node(
                    node_type="process", node_id=process_id, properties={"name": step.get("name", process_id)}
                )

            if not self.graph_store.has_node(feature_id):
                self.graph_store.add_node(node_type="feature", node_id=feature_id, properties={"name": feature_id})

            # 更新关系
            edge_type = "APPLIED_TO"
            if not self.graph_store.has_edge(process_id, feature_id, edge_type):
                # 创建新关系
                self.graph_store.add_edge(
                    source_id=process_id,
                    target_id=feature_id,
                    edge_type=edge_type,
                    properties={
                        "confidence": 0.5,
                        "sample_count": 1,
                        "success_count": 1 if record.get("first_pass_acceptance", False) else 0,
                    },
                )
                updated_count += 1
                continue

            # 更新现有关系
            edge = self.graph_store.get_edge(process_id, feature_id, edge_type)
            if not edge:
                continue

            props = edge.get("properties", {})
            sample_count = props.get("sample_count", 0)
            success_count = props.get("success_count", 0)

            new_sample_count = sample_count + 1
            first_pass = record.get("first_pass_acceptance", False)
            new_success_count = success_count + (1 if first_pass else 0)

            success_rate = new_success_count / new_sample_count if new_sample_count > 0 else 0.0
            new_confidence = min(0.5 * success_rate + 0.2, 1.0)

            self.graph_store.update_edge_properties(
                source_id=process_id,
                target_id=feature_id,
                edge_type=edge_type,
                properties={
                    "confidence": new_confidence,
                    "sample_count": new_sample_count,
                    "success_count": new_success_count,
                    "success_rate": success_rate,
                    "last_updated": record.get("record_id"),
                },
            )

            updated_count += 1

        return updated_count

    def _adjust_confidence_based_on_result(self, record: dict[str, Any], first_pass_acceptance: bool) -> int:
        """根据加工结果调整可信度

        根据首次合格率调整相关关系的可信度：
        - 合格：提升可信度
        - 不合格：降低可信度

        Args:
            record: 加工记录
            first_pass_acceptance: 首次是否合格

        Returns:
            调整的关系数量
        """
        adjustment_count = 0

        tool_id = record.get("tool_id")
        material = record.get("workpiece_material")

        if not tool_id or not material:
            return adjustment_count

        # 调整Tool-Material关系可信度
        edge_type = "SUITABLE_FOR"
        if self.graph_store.has_edge(tool_id, material, edge_type):
            edge = self.graph_store.get_edge(tool_id, material, edge_type)
            if edge:
                props = edge.get("properties", {})
                current_confidence = props.get("confidence", 0.5)

                # 根据结果调整可信度
                if first_pass_acceptance:
                    # 合格：提升可信度（最多提升0.1）
                    new_confidence = min(current_confidence + 0.05, 1.0)
                else:
                    # 不合格：降低可信度（最多降低0.1）
                    new_confidence = max(current_confidence - 0.1, 0.0)

                if abs(new_confidence - current_confidence) > 0.001:
                    self.graph_store.update_edge_properties(
                        source_id=tool_id,
                        target_id=material,
                        edge_type=edge_type,
                        properties={"confidence": new_confidence},
                    )
                    adjustment_count += 1
                    logger.debug(
                        f"Adjusted confidence for {tool_id} -> {material}: "
                        f"{current_confidence:.3f} -> {new_confidence:.3f}"
                    )

        return adjustment_count

    def flush_to_repository(self) -> dict[str, int]:
        """将内存图谱刷新到持久化存储

        Returns:
            刷新统计信息
        """
        try:
            result = self.graph_store.flush_to_repository()
            logger.info("Graph flushed to repository: %s", result)
            return result
        except (OSError, RuntimeError) as e:
            logger.error("Failed to flush graph to repository: %s", e)
            raise


__all__ = ["FeedbackUpdater"]
