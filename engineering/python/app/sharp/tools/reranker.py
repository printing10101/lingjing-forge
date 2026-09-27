"""SHARP 证据重排序器（M2.5）。

对应论文 §4.4 "Hybrid Knowledge Toolset" 中的证据聚合与重排序模块。

核心职责
--------
ReAct 循环中工具会产出多源异构证据（KG 关系、文本片段、LLM 推理结论等），
需要统一的重排序器：

1. **来源加权**：不同来源的可信度不同（KG 实测 > 文献 > LLM 推理）
2. **相关性评分**：证据与三元组的语义相关度
3. **去重聚合**：相似证据合并，保留最具代表性的一条
4. **聚合置信度**：综合所有证据给出最终置信度

设计原则
--------
- **training-free**：所有权重与阈值硬编码，基于 ontology-v1.md 的领域经验
- **结构透明**：每条证据保留 source/tool/score 字段，便于证据链追溯
- **容错**：单条证据异常不影响整体聚合
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# 来源权重（基于 ontology-v1.md RelationSource 枚举与领域经验）

SOURCE_WEIGHTS: dict[str, float] = {
    # KG 来源（已持久化的关系）
    "kg": 0.95,
    "knowledge_graph": 0.95,
    # 实测数据
    "measured": 0.9,
    "experiment": 0.9,
    "bosch_cnc": 0.88,
    "uniwear-phm2010": 0.85,
    "uniwear-nuaa": 0.83,
    "uniwear": 0.82,
    # 文献
    "literature": 0.75,
    "paper": 0.75,
    "manual": 0.7,
    "rule": 0.7,
    # LLM 推理
    "llm": 0.6,
    "llm.reason": 0.6,
    "llm.extract": 0.55,
    # 默认
    "unknown": 0.5,
}

# 工具名 来源标签 映射
TOOL_TO_SOURCE: dict[str, str] = {
    "kg.query_entity": "kg",
    "kg.query_relation": "kg",
    "kg.query_neighbors": "kg",
    "kg.query_path": "kg",
    "text.retrieve": "text",
    "text.entity_lookup": "text",
    "llm.reason": "llm",
    "llm.extract": "llm",
}


# 证据结构


@dataclass
class Evidence:
    """单条证据。

    Attributes
    ----------
    source : str
        来源标签（kg / text / llm / 文档名等）
    tool : str
        产出该证据的工具名
    content : str
        证据文本内容（已截断）
    score : float
        原始相关性分数（0-1）
    weighted_score : float
        加权后的分数（source_weight * relevance）
    metadata : dict
        额外元数据（如 KG edge_type、文本 chunk_id 等）
    """

    source: str
    tool: str
    content: str
    score: float = 0.0
    weighted_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "tool": self.tool,
            "content": self.content,
            "score": round(self.score, 4),
            "weighted_score": round(self.weighted_score, 4),
            "metadata": self.metadata,
        }


# 重排序器


class EvidenceReranker:
    """多源证据重排序与聚合器。

    使用方式：

        reranker = EvidenceReranker()
        evidences = reranker.collect_from_tool_results(tool_results, triple)
        ranked = reranker.rerank(evidences, top_k=5)
        confidence = reranker.aggregate_confidence(ranked)

    聚合策略
    --------
    - **加权平均**：top-k 证据的 weighted_score 加权平均
    - **来源多样性奖励**：覆盖更多来源类型时置信度上浮
    - **冲突惩罚**：若存在 verdict 冲突（supported vs refuted），置信度下调
    """

    def __init__(
        self,
        source_weights: dict[str, float] | None = None,
        default_top_k: int = 5,
    ) -> None:
        self.source_weights = source_weights or SOURCE_WEIGHTS
        self.default_top_k = default_top_k

    # 证据收集

    def collect_from_tool_results(
        self,
        tool_results: list,
        triple: Any | None = None,
    ) -> list[Evidence]:
        """从 ToolResult 列表中提取证据。

        Args:
            tool_results: `ToolResult` 实例列表（仅取 success=True 的）
            triple: 可选的三元组（用于相关性过滤，当前实现暂不使用）

        Returns:
            Evidence 列表
        """
        evidences: list[Evidence] = []
        for tr in tool_results:
            if not getattr(tr, "success", False):
                continue
            tool_name = getattr(tr, "tool_name", "unknown")
            source = TOOL_TO_SOURCE.get(tool_name, "unknown")
            output = getattr(tr, "output", None)
            if output is None:
                continue
            evidences.extend(self._extract_evidences_from_output(tool_name, source, output))
        return evidences

    def _extract_evidences_from_output(self, tool_name: str, source: str, output: Any) -> list[Evidence]:
        """根据工具输出结构提取证据。"""
        evidences: list[Evidence] = []
        if not isinstance(output, dict):
            return evidences

        if tool_name.startswith("kg.query_relation"):
            edges = output.get("edges", [])
            for edge in edges:
                conf = (edge.get("properties") or {}).get("confidence", 0.5)
                evidences.append(
                    Evidence(
                        source=source,
                        tool=tool_name,
                        content=(
                            f"KG 关系存在：{edge.get('source_id')} -[{edge.get('edge_type')}]-> "
                            f"{edge.get('target_id')} (置信度 {conf})"
                        ),
                        score=float(conf),
                        metadata={"edge_type": edge.get("edge_type")},
                    )
                )
            # 关系不存在也是证据
            if output.get("exists") is False:
                evidences.append(
                    Evidence(
                        source=source,
                        tool=tool_name,
                        content="KG 中不存在该关系",
                        score=0.3,  # 不存在本身是弱证据
                        metadata={"exists": False},
                    )
                )

        elif tool_name.startswith("kg.query_entity"):
            node = output
            if isinstance(node, dict) and node.get("node_id"):
                props = node.get("properties", {})
                evidences.append(
                    Evidence(
                        source=source,
                        tool=tool_name,
                        content=f"KG 实体 {node.get('node_id')} 属性：{props}",
                        score=0.7,
                        metadata={"node_id": node.get("node_id")},
                    )
                )

        elif tool_name.startswith("kg.query_neighbors"):
            neighbors = output.get("neighbors", [])
            for nb in neighbors[:10]:  # 最多取 10 条
                evidences.append(
                    Evidence(
                        source=source,
                        tool=tool_name,
                        content=(
                            f"邻居：{nb.get('via_source')} -[{nb.get('via_edge')}]-> "
                            f"{nb.get('node_id')} (hop={nb.get('hop')})"
                        ),
                        score=0.5,
                        metadata={"hop": nb.get("hop")},
                    )
                )

        elif tool_name.startswith("kg.query_path"):
            paths = output.get("paths", [])
            for path in paths[:3]:
                path_str = " -> ".join(n.get("node_id", "?") for n in path)
                evidences.append(
                    Evidence(
                        source=source,
                        tool=tool_name,
                        content=f"KG 路径：{path_str}",
                        score=0.6,
                        metadata={"path_length": len(path)},
                    )
                )

        elif tool_name.startswith("text.retrieve"):
            results = output.get("results", [])
            for r in results:
                evidences.append(
                    Evidence(
                        source=r.get("source", "text") or "text",
                        tool=tool_name,
                        content=r.get("content", "")[:300],
                        score=float(r.get("score", 0.5)),
                        metadata=r.get("metadata", {}),
                    )
                )

        elif tool_name.startswith("text.entity_lookup"):
            entities = output.get("entities", [])
            if entities:
                evidences.append(
                    Evidence(
                        source=source,
                        tool=tool_name,
                        content=f"识别实体：{', '.join(entities)}",
                        score=0.65,
                        metadata={"entities": entities},
                    )
                )

        elif tool_name.startswith("llm.reason"):
            verdict = output.get("verdict", "uncertain")
            conf = float(output.get("confidence", 0.0))
            reasoning = output.get("reasoning", "")
            evidences.append(
                Evidence(
                    source=source,
                    tool=tool_name,
                    content=f"LLM 推理：verdict={verdict}, confidence={conf}, reasoning={reasoning}",
                    score=conf,
                    metadata={
                        "verdict": verdict,
                        "key_evidence": output.get("key_evidence", []),
                    },
                )
            )

        elif tool_name.startswith("llm.extract"):
            triples = output.get("triples", [])
            for t in triples:
                head = t.get("head", {})
                tail = t.get("tail", {})
                evidences.append(
                    Evidence(
                        source=source,
                        tool=tool_name,
                        content=(
                            f"LLM 抽取三元组：{head.get('name')} -[{t.get('relation')}]-> "
                            f"{tail.get('name')} (confidence={t.get('confidence')})"
                        ),
                        score=float(t.get("confidence", 0.5)),
                        metadata={"relation": t.get("relation")},
                    )
                )

        return evidences

    # 重排序

    def rerank(
        self,
        evidences: list[Evidence],
        top_k: int | None = None,
    ) -> list[Evidence]:
        """按加权分数排序并截断。

        步骤：
        1. 计算每条证据的 weighted_score = source_weight * relevance_score
        2. 按加权分数降序排序
        3. 去重（相同 content 保留分数最高的）
        4. 截断 top_k
        """
        if not evidences:
            return []

        top_k = top_k or self.default_top_k

        # 1. 计算加权分数
        for ev in evidences:
            weight = self._get_source_weight(ev.source)
            ev.weighted_score = weight * max(0.0, min(1.0, ev.score))

        # 2. 排序
        ranked = sorted(evidences, key=lambda e: e.weighted_score, reverse=True)

        # 3. 去重（按 content 前 100 字符去重）
        seen: set[str] = set()
        deduped: list[Evidence] = []
        for ev in ranked:
            key = ev.content[:100].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ev)

        return deduped[:top_k]

    def _get_source_weight(self, source: str) -> float:
        """获取来源权重，支持前缀匹配。"""
        if not source:
            return self.source_weights.get("unknown", 0.5)
        if source in self.source_weights:
            return self.source_weights[source]
        # 前缀匹配（如 "uniwear-phm2010-chunk-xxx" "uniwear-phm2010"）
        for prefix, weight in self.source_weights.items():
            if source.startswith(prefix):
                return weight
        return self.source_weights.get("unknown", 0.5)

    # 聚合置信度

    def aggregate_confidence(
        self,
        ranked_evidences: list[Evidence],
        require_external: bool = False,
    ) -> dict[str, Any]:
        """聚合 top-k 证据的最终置信度与结论。

        Args:
            ranked_evidences: 已重排序的证据列表
            require_external: 是否强制要求外部证据（KG 或文本）

        Returns:
            {
                "confidence": float,         # 0-1
                "verdict": str,              # supported / refuted / uncertain
                "evidence_count": int,
                "source_diversity": float,   # 来源类型多样性 0-1
                "has_external_evidence": bool,
                "conflict_detected": bool,
                "top_evidence": dict,        # 最强证据
            }
        """
        if not ranked_evidences:
            return {
                "confidence": 0.0,
                "verdict": "uncertain",
                "evidence_count": 0,
                "source_diversity": 0.0,
                "has_external_evidence": False,
                "conflict_detected": False,
                "top_evidence": None,
            }

        # 加权平均置信度
        scores = [ev.weighted_score for ev in ranked_evidences]
        avg_score = sum(scores) / len(scores)

        # 来源多样性
        source_types = set()
        for ev in ranked_evidences:
            if ev.source in ("kg", "knowledge_graph"):
                source_types.add("kg")
            elif ev.source in ("llm", "llm.reason", "llm.extract"):
                source_types.add("llm")
            elif ev.source in ("measured", "experiment", "bosch_cnc", "uniwear-phm2010", "uniwear-nuaa", "uniwear"):
                source_types.add("measured")
            elif ev.source in ("literature", "paper", "manual", "rule", "text"):
                source_types.add("text")
            else:
                source_types.add("other")
        diversity = len(source_types) / 4.0  # 归一化到 0-1（4 类来源）

        # 多样性奖励（最多 +0.1）
        diversity_bonus = min(0.1, diversity * 0.05)

        # 是否有外部证据（KG 或文本/实测）
        has_external = any(ev.source not in ("llm", "llm.reason", "llm.extract", "unknown") for ev in ranked_evidences)

        # 冲突检测（LLM verdict 冲突）
        verdicts = set()
        for ev in ranked_evidences:
            v = ev.metadata.get("verdict")
            if v:
                verdicts.add(v)
        conflict = "supported" in verdicts and "refuted" in verdicts
        conflict_penalty = 0.2 if conflict else 0.0

        # 最终置信度
        confidence = avg_score + diversity_bonus - conflict_penalty
        confidence = max(0.0, min(1.0, confidence))

        # 强制外部证据要求
        if require_external and not has_external:
            confidence *= 0.5  # 没有外部证据时置信度减半

        # verdict 判定
        if confidence >= 0.7:
            if "refuted" in verdicts and "supported" not in verdicts:
                verdict = "refuted"
            else:
                verdict = "supported"
        elif confidence <= 0.3:
            verdict = "refuted" if "refuted" in verdicts else "uncertain"
        else:
            verdict = "uncertain"

        return {
            "confidence": round(confidence, 4),
            "verdict": verdict,
            "evidence_count": len(ranked_evidences),
            "source_diversity": round(diversity, 4),
            "has_external_evidence": has_external,
            "conflict_detected": conflict,
            "top_evidence": ranked_evidences[0].to_dict() if ranked_evidences else None,
        }


__all__ = ["EvidenceReranker", "Evidence", "SOURCE_WEIGHTS"]
