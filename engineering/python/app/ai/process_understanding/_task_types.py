"""任务类型枚举与分类结果（从 task_classifier 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TaskType(Enum):
    """任务类型枚举"""

    PROCESS_CONSULT = "A"  # 工艺咨询
    FAULT_DIAGNOSIS = "B"  # 故障诊断
    SOLUTION_GENERATION = "C"  # 方案生成
    KNOWLEDGE_QUERY = "D"  # 知识查询
    CHITCHAT = "E"  # 闲聊

    @classmethod
    def from_code(cls, code: str) -> "TaskType":
        code = code.strip().upper()
        for t in cls:
            if t.value == code:
                return t
        return cls.CHITCHAT

    @property
    def label(self) -> str:
        labels = {
            TaskType.PROCESS_CONSULT: "工艺咨询",
            TaskType.FAULT_DIAGNOSIS: "故障诊断",
            TaskType.SOLUTION_GENERATION: "方案生成",
            TaskType.KNOWLEDGE_QUERY: "知识查询",
            TaskType.CHITCHAT: "闲聊",
        }
        return labels.get(self, "未知")


@dataclass
class ClassificationResult:
    """分类结果"""

    task_type: TaskType
    confidence: float
    keywords_matched: list[str] = field(default_factory=list)
    raw_response: str = ""
    latency_ms: float = 0.0
