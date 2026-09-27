"""工艺理解引擎输出数据类与工具（从 engine 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai.process_understanding.task_classifier import TaskType


@dataclass
class ProcessUnderstandingOutput:
    """工艺理解模块统一输出格式"""

    task_type: str = ""
    intent: str = ""
    entities: dict[str, str] = field(default_factory=dict)
    response: str = ""
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "intent": self.intent,
            "entities": self.entities,
            "response": self.response,
            "confidence": self.confidence,
            "sources": self.sources,
            "actions": self.actions,
            "details": self.details,
        }


# 实体提取 Prompt


def task_type_to_code(task_type: TaskType) -> str:
    return task_type.value
