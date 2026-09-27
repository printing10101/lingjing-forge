"""SHARP Memory 增强器（M4.3）。

对应论文 §4.5 "Memory-Augmented Mechanism" 的统一封装层。

核心职责
--------
将 `TrajectoryStore`（持久化）与 `SimilarityRetriever`（检索）封装为一个
对外暴露的"记忆增强器"，供 `ReActLoop` 通过 `memory_augmentor` 参数注入。

被 ReActLoop 调用的接口
-----------------------
- `async retrieve_similar(triple) -> list[StoredTrajectory]`
    在 ReAct 主循环开始前调用，返回与当前三元组相似的历史轨迹
- `format_memory_context(records) -> str`
    将检索到的相似轨迹格式化为 prompt 文本，注入到 user prompt 中
- `store(result) -> StoredTrajectory`
    在 ReAct 主循环结束后调用，将本次验证结果存入轨迹库

设计原则
--------
- **async 友好**：`retrieve_similar` 为 async 方法，便于未来扩展为远程存储
- **容错**：存储/检索失败不影响主流程，仅记录日志
- **可关闭**：`enabled=False` 时所有方法返回空值/no-op
- **可观测**：检索结果包含相似度分数（通过 `last_retrieve_scores` 访问）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.sharp.memory.similarity_retriever import (
    SimilarityRetriever,
    SimilarityScore,
)
from app.sharp.memory.trajectory_store import (
    StoredTrajectory,
    TrajectoryStore,
)
from app.sharp.schema.domain_schema import Triple

logger = logging.getLogger(__name__)


class MemoryAugmentor:
    """Memory 增强器：统一封装轨迹存储 + 相似度检索。

    Usage::

        store = TrajectoryStore()
        retriever = SimilarityRetriever()
        augmentor = MemoryAugmentor(store, retriever, top_k=3)

        # 在 ReActLoop 主循环开始前
        similar = await augmentor.retrieve_similar(triple)
        context_text = augmentor.format_memory_context(similar)

        # 在 ReActLoop 主循环结束后
        augmentor.store(result)
    """

    def __init__(
        self,
        trajectory_store: TrajectoryStore,
        similarity_retriever: SimilarityRetriever,
        top_k: int = 3,
        enabled: bool = True,
    ) -> None:
        """初始化 Memory 增强器。

        Args:
            trajectory_store: 轨迹存储
            similarity_retriever: 相似度检索器
            top_k: 检索返回的最大案例数
            enabled: 是否启用（False 时所有方法返回空值/no-op）
        """
        self.trajectory_store = trajectory_store
        self.similarity_retriever = similarity_retriever
        self.top_k = max(1, top_k)
        self.enabled = bool(enabled)

        # 缓存最近一次检索的 (record, score) 列表，便于调试与可观测性
        self.last_retrieve_scores: list[tuple[StoredTrajectory, SimilarityScore]] = []

    # 公共接口

    async def retrieve_similar(self, triple: Triple) -> list[StoredTrajectory]:
        """检索与当前三元组相似的历史轨迹。

        Args:
            triple: 待验证的三元组

        Returns:
            相似轨迹列表（按相似度降序），最多 top_k 条。
            若 `enabled=False` 或检索失败，返回空列表。
        """
        if not self.enabled:
            return []

        # SimilarityRetriever.retrieve 是同步方法，包装为 async 以匹配 ReActLoop 接口
        try:
            scored = await asyncio.to_thread(
                self.similarity_retriever.retrieve,
                triple,
                self.trajectory_store,
                self.top_k,
            )
        except Exception as e:
            logger.warning("Memory retrieve_similar failed: %s", e)
            self.last_retrieve_scores = []
            return []

        self.last_retrieve_scores = scored
        return [record for record, _ in scored]

    def format_memory_context(
        self,
        records: list[StoredTrajectory],
    ) -> str:
        """将相似轨迹格式化为 prompt 文本。

        Args:
            records: 相似轨迹列表

        Returns:
            格式化的 prompt 文本。若列表为空，返回空字符串。
        """
        if not records:
            return ""

        # 取 last_retrieve_scores 中的分数信息（若对齐）
        scores_map: dict[str, SimilarityScore] = {r.verification_id: s for r, s in self.last_retrieve_scores}

        lines: list[str] = []
        lines.append(f"共检索到 {len(records)} 条历史相似案例：")
        lines.append("")

        for idx, record in enumerate(records, start=1):
            score = scores_map.get(record.verification_id)
            score_text = f"（相似度 {score.total_score:.2f}）" if score else ""

            # 三元组简短表示
            t = record.triple or {}
            triple_str = (
                f"({t.get('head_type', '?')}:{t.get('head_id', '?')})"
                f"-[{t.get('relation', '?')}]->"
                f"({t.get('tail_type', '?')}:{t.get('tail_id', '?')})"
            )

            lines.append(f"### 案例 {idx}{score_text}")
            lines.append(f"- 三元组: {triple_str}")
            lines.append(f"- 判定结果: {record.verdict} (置信度 {record.confidence:.3f})")
            lines.append(f"- 终止触发: {record.stopping_trigger}")
            lines.append(f"- 步数: {record.steps_taken}, 耗时: {record.elapsed_ms:.0f}ms")

            if record.reasoning:
                # 推理依据截断 200 字符
                reasoning = record.reasoning[:200].replace("\n", " ")
                lines.append(f"- 推理依据: {reasoning}")

            if record.key_evidence:
                # 仅展示第一条关键证据，截断 150 字符
                ev = record.key_evidence[0][:150].replace("\n", " ")
                lines.append(f"- 关键证据: {ev}")

            if score and score.reasons:
                lines.append(f"- 命中原因: {', '.join(score.reasons)}")

            lines.append("")

        return "\n".join(lines)

    def store(self, result: Any, timestamp: float | None = None) -> StoredTrajectory | None:
        """存储验证结果到轨迹库。

        Args:
            result: `VerificationResult` 实例
            timestamp: 时间戳，None 时使用 time.time()

        Returns:
            存储的 `StoredTrajectory`，若 `enabled=False` 或存储失败返回 None
        """
        if not self.enabled:
            return None

        try:
            return self.trajectory_store.store(result, timestamp)
        except Exception as e:
            logger.warning("Memory store failed: %s", e)
            return None

    # 辅助方法

    def is_enabled(self) -> bool:
        """是否启用 Memory 增强。"""
        return self.enabled

    def trajectory_count(self) -> int:
        """当前轨迹库中的记录数。"""
        return self.trajectory_store.count()


__all__ = ["MemoryAugmentor"]
