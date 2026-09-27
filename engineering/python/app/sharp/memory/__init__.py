"""SHARP Memory-Augmented 机制（M4）。

对应论文 §4.5 "Memory-Augmented Mechanism"。

核心思想
--------
传统 ReAct 循环每次验证都是"从零开始"，无法利用历史相似验证的经验。
SHARP 提出 Memory-Augmented 机制：

1. **轨迹存储**：每次验证完成后，将 `VerificationResult` 持久化到本地
2. **相似度检索**：新验证开始前，从历史轨迹中检索与当前三元组相似的案例
3. **Prompt 注入**：将相似案例的 verdict / reasoning / 关键证据注入到 prompt，
   为 LLM 提供先验知识，加速收敛并提升准确率

模块
----
- `TrajectoryStore`        轨迹存储（JSONL 文件 + 内存索引）
- `SimilarityRetriever`    三元组相似度检索（关系/实体/类型多维匹配）
- `MemoryAugmentor`        统一封装，提供 `retrieve_similar` / `format_memory_context` / `store`

设计原则
--------
- **training-free**：相似度计算基于规则（关系类型 + 实体 ID + 类型组合），无向量索引
- **可持久化**：默认 JSONL 文件存储（`~/.lingjing/sharp/trajectories.jsonl`）
- **可关闭**：通过配置 `enable_memory_augment=False` 或 `ablation_mode="no_memory"` 禁用
- **可观测**：每次检索返回相似度分数与命中原因，便于调试
"""

from __future__ import annotations

from app.sharp.memory.trajectory_store import TrajectoryStore, StoredTrajectory
from app.sharp.memory.similarity_retriever import (
    SimilarityRetriever,
    SimilarityScore,
)
from app.sharp.memory.memory_augmentor import MemoryAugmentor

__all__ = [
    "TrajectoryStore",
    "StoredTrajectory",
    "SimilarityRetriever",
    "SimilarityScore",
    "MemoryAugmentor",
]
