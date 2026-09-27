"""反思结果数据类（从 reflector 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class InsightItem:
    """单条浮现的洞察。"""

    category: str  # "pattern" | "anomaly" | "rule_candidate" | "warning"
    content: str  # 洞察文本
    confidence: float = 0.5  # 置信度 [0, 1]
    supporting_sessions: list[str] = field(default_factory=list)  # 支撑该洞察的 session_id
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "content": self.content,
            "confidence": self.confidence,
            "supporting_sessions": self.supporting_sessions,
            "metadata": self.metadata,
        }


@dataclass
class DeduplicationResult:
    """去重操作结果。"""

    merged_count: int = 0  # 合并的条目数
    removed_node_ids: list[str] = field(default_factory=list)  # 被移除的节点
    kept_node_ids: list[str] = field(default_factory=list)  # 保留的节点


@dataclass
class UpdateResult:
    """过时更新操作结果。"""

    updated_node_ids: list[str] = field(default_factory=list)
    invalidated_node_ids: list[str] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReflectionResult:
    """完整反思结果。"""

    deduplicated: DeduplicationResult
    updated: UpdateResult
    insights: list[InsightItem]
    new_memory_version: str | None = None  # Git commit hash
    summary: str = ""  # 人类可读的反思摘要
    llm_used: bool = False  # 是否成功调用了 LLM
    llm_model: str | None = None  # 实际使用的 LLM 模型
    reflected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "deduplicated": {
                "merged_count": self.deduplicated.merged_count,
                "removed_node_ids": self.deduplicated.removed_node_ids,
                "kept_node_ids": self.deduplicated.kept_node_ids,
            },
            "updated": {
                "updated_node_ids": self.updated.updated_node_ids,
                "invalidated_node_ids": self.updated.invalidated_node_ids,
                "details": self.updated.details,
            },
            "insights": [i.to_dict() for i in self.insights],
            "new_memory_version": self.new_memory_version,
            "summary": self.summary,
            "llm_used": self.llm_used,
            "llm_model": self.llm_model,
            "reflected_at": self.reflected_at,
        }


# DreamReflector
