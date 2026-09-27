"""SHARP 相似轨迹检索（M4.2）。

对应论文 §4.5 "Memory-Augmented Mechanism" 中的相似度检索模块。

核心思想
--------
传统向量检索需要预训练 embedding 模型，与 SHARP "training-free" 原则相悖。
本模块采用**规则驱动的多维匹配**，对历史 `StoredTrajectory` 与当前 `Triple`
计算相似度，返回 top-k 相似案例。

匹配维度
--------
1. **关系类型匹配**（权重 0.4）：关系类型完全相同 → +0.4
2. **实体 ID 匹配**（权重 0.3）：
   - head_id 相同 → +0.15
   - tail_id 相同 → +0.15
3. **实体类型组合匹配**（权重 0.2）：
   - (head_type, tail_type) 完全相同 → +0.2
4. **关系属性相似度**（权重 0.1）：
   - 同一关系下的属性 key 重合度 → 0~0.1

最高分 1.0，最低分 0.0。默认 top_k=3，最低阈值 0.3（低于此值的案例不返回）。

设计原则
--------
- **training-free**：纯规则匹配，无向量索引
- **可解释**：每条相似案例返回 `SimilarityScore`，包含各维度得分与命中原因
- **容错**：单条记录异常不影响整体检索
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.sharp.memory.trajectory_store import StoredTrajectory, TrajectoryStore
from app.sharp.schema.domain_schema import Triple

logger = logging.getLogger(__name__)


# 相似度分数


@dataclass
class SimilarityScore:
    """相似度分数结构。

    Attributes
    ----------
    total_score : float
        总分（0-1）
    relation_match : float
        关系类型匹配得分（0 或 0.4）
    entity_match : float
        实体 ID 匹配得分（0-0.3）
    type_match : float
        实体类型组合匹配得分（0 或 0.2）
    property_match : float
        关系属性匹配得分（0-0.1）
    reasons : list[str]
        命中原因（用于可观测性）
    """

    total_score: float = 0.0
    relation_match: float = 0.0
    entity_match: float = 0.0
    type_match: float = 0.0
    property_match: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_score": round(self.total_score, 4),
            "relation_match": round(self.relation_match, 4),
            "entity_match": round(self.entity_match, 4),
            "type_match": round(self.type_match, 4),
            "property_match": round(self.property_match, 4),
            "reasons": self.reasons,
        }


# 权重常量

WEIGHT_RELATION: float = 0.4
WEIGHT_ENTITY_HEAD: float = 0.15
WEIGHT_ENTITY_TAIL: float = 0.15
WEIGHT_TYPE_COMBO: float = 0.2
WEIGHT_PROPERTY: float = 0.1

DEFAULT_MIN_SCORE: float = 0.3
DEFAULT_TOP_K: int = 3


# 相似度检索器


class SimilarityRetriever:
    """基于规则的三元组相似度检索器。

    Usage::

        retriever = SimilarityRetriever()
        results = retriever.retrieve(triple, store, top_k=3)
        for record, score in results:
            print(record.verification_id, score.total_score)
    """

    def __init__(
        self,
        min_score: float = DEFAULT_MIN_SCORE,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        """初始化检索器。

        Args:
            min_score: 最低相似度阈值，低于此值的案例不返回
            top_k: 返回的最相似案例数
        """
        self.min_score = max(0.0, min(1.0, min_score))
        self.top_k = max(1, top_k)

    # 公共接口

    def retrieve(
        self,
        triple: Triple,
        store: TrajectoryStore,
        top_k: int | None = None,
    ) -> list[tuple[StoredTrajectory, SimilarityScore]]:
        """从轨迹存储中检索与给定三元组相似的案例。

        Args:
            triple: 待验证的三元组
            store: 轨迹存储
            top_k: 返回的最相似案例数，None 时使用默认值

        Returns:
            按 total_score 降序排列的 (record, score) 列表，长度 <= top_k
        """
        k = top_k or self.top_k
        records = store.list_all()
        if not records:
            return []

        scored: list[tuple[StoredTrajectory, SimilarityScore]] = []
        for record in records:
            try:
                score = self._compute_similarity(triple, record)
            except Exception as e:
                logger.debug(
                    "Skip malformed trajectory %s: %s",
                    record.verification_id,
                    e,
                )
                continue

            if score.total_score >= self.min_score:
                scored.append((record, score))

        # 按总分降序排序
        scored.sort(key=lambda x: x[1].total_score, reverse=True)
        return scored[:k]

    # 相似度计算

    def _compute_similarity(self, triple: Triple, record: StoredTrajectory) -> SimilarityScore:
        """计算单个历史轨迹与当前三元组的相似度。"""
        score = SimilarityScore()
        reasons: list[str] = []

        record_triple = record.triple or {}
        if not record_triple:
            return score

        # 1. 关系类型匹配
        cur_relation = triple.relation.value if hasattr(triple.relation, "value") else str(triple.relation)
        rec_relation = str(record_triple.get("relation", ""))
        if cur_relation and rec_relation and cur_relation == rec_relation:
            score.relation_match = WEIGHT_RELATION
            reasons.append(f"关系类型相同 ({cur_relation})")

        # 2. 实体 ID 匹配
        if triple.head_id and triple.head_id == record_triple.get("head_id"):
            score.entity_match += WEIGHT_ENTITY_HEAD
            reasons.append(f"head_id 相同 ({triple.head_id})")
        if triple.tail_id and triple.tail_id == record_triple.get("tail_id"):
            score.entity_match += WEIGHT_ENTITY_TAIL
            reasons.append(f"tail_id 相同 ({triple.tail_id})")

        # 3. 实体类型组合匹配
        cur_head_type = triple.head_type.value if hasattr(triple.head_type, "value") else str(triple.head_type)
        cur_tail_type = triple.tail_type.value if hasattr(triple.tail_type, "value") else str(triple.tail_type)
        rec_head_type = str(record_triple.get("head_type", ""))
        rec_tail_type = str(record_triple.get("tail_type", ""))
        if cur_head_type and cur_tail_type and cur_head_type == rec_head_type and cur_tail_type == rec_tail_type:
            score.type_match = WEIGHT_TYPE_COMBO
            reasons.append(f"类型组合相同 ({cur_head_type},{cur_tail_type})")

        # 4. 关系属性匹配
        cur_props = triple.relation_properties or {}
        rec_props = (record.triple or {}).get("relation_properties") or {}
        # 注意：StoredTrajectory.triple 仅保存 head/relation/tail 的核心字段，
        # 关系属性不一定持久化，所以这里的属性匹配是可选的容错匹配
        if cur_props and rec_props:
            cur_keys = set(cur_props.keys())
            rec_keys = set(rec_props.keys())
            overlap = cur_keys & rec_keys
            if overlap:
                # 属性 key 重合度归一化到 0-0.1
                union = cur_keys | rec_keys
                ratio = len(overlap) / len(union) if union else 0.0
                score.property_match = WEIGHT_PROPERTY * ratio
                reasons.append(f"关系属性重合 {len(overlap)}/{len(union)}")

        # 总分
        score.total_score = score.relation_match + score.entity_match + score.type_match + score.property_match
        score.reasons = reasons
        return score


__all__ = ["SimilarityRetriever", "SimilarityScore"]
