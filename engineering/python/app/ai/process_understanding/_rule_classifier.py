"""规则快速分类器（从 task_classifier 拆出）。"""

from __future__ import annotations

import re

from app.ai.process_understanding._keywords import KEYWORD_PATTERNS
from app.ai.process_understanding._task_types import ClassificationResult, TaskType


class RuleBasedClassifier:
    """基于规则和关键词匹配的快速分类器。

    在LLM分类之前先进行快速预分类，可覆盖约80%的常见输入。
    对于模糊输入则交由LLM分类器处理。
    """

    # 规则匹配的最低置信度
    MIN_RULE_CONFIDENCE = 0.5

    # 规则匹配置信度参数
    RULE_MATCH_BASE_CONFIDENCE: float = 0.5  # 基础置信度
    RULE_MATCH_STEP: float = 0.15  # 每匹配一个关键词的置信度增量
    RULE_MATCH_MAX_CONFIDENCE: float = 0.95  # 规则匹配置信度上限
    RULE_DEFAULT_CONFIDENCE: float = 0.9  # 规则匹配命中时的默认置信度

    # LLM 置信度参数
    LLM_DEFAULT_CONFIDENCE: float = 0.8  # LLM 解析成功的默认置信度
    LLM_FALLBACK_CONFIDENCE: float = 0.5  # LLM 解析失败时的回退置信度
    LLM_ERROR_CONFIDENCE: float = 0.3  # LLM 异常时的最低置信度
    LLM_PARSE_DEFAULT_CONFIDENCE: float = 0.5  # LLM 响应中 confidence 字段缺失时的默认值

    def classify(self, user_input: str) -> ClassificationResult | None:
        """基于规则快速分类，不确定时返回 None。

        Args:
            user_input: 用户输入文本

        Returns:
            ClassificationResult 或 None（无法确定时）
        """
        input_lower = user_input.lower().strip()
        if not input_lower:
            return ClassificationResult(
                task_type=TaskType.CHITCHAT,
                confidence=self.RULE_DEFAULT_CONFIDENCE,
                keywords_matched=[],
            )

        scores: dict[TaskType, tuple[float, list[str]]] = {}
        all_chitchat_matched: list[str] = []

        for task_type, patterns in KEYWORD_PATTERNS.items():
            matched: list[str] = []
            for pattern in patterns:
                try:
                    if re.search(pattern, input_lower):
                        matched.append(pattern)
                except re.error:
                    continue

            if task_type == TaskType.CHITCHAT:
                all_chitchat_matched = matched
                continue

            if matched:
                # 根据匹配数量计算置信度（每匹配一个关键词 +RULE_MATCH_STEP）
                confidence = min(
                    self.RULE_MATCH_MAX_CONFIDENCE,
                    self.RULE_MATCH_BASE_CONFIDENCE + len(matched) * self.RULE_MATCH_STEP,
                )
                scores[task_type] = (confidence, matched)

        if not scores:
            # 检查闲聊
            if all_chitchat_matched:
                return ClassificationResult(
                    task_type=TaskType.CHITCHAT,
                    confidence=self.LLM_DEFAULT_CONFIDENCE,
                    keywords_matched=all_chitchat_matched,
                )
            return None

        # 取最高分，同分时按优先级: C > B > A > D > E
        TIE_PRIORITY = {
            TaskType.SOLUTION_GENERATION: 5,
            TaskType.FAULT_DIAGNOSIS: 4,
            TaskType.PROCESS_CONSULT: 3,
            TaskType.KNOWLEDGE_QUERY: 2,
            TaskType.CHITCHAT: 1,
        }
        best_type = max(
            scores,
            key=lambda k: (scores[k][0], TIE_PRIORITY.get(k, 0)),
        )
        confidence, matched = scores[best_type]

        if confidence >= self.MIN_RULE_CONFIDENCE:
            return ClassificationResult(
                task_type=best_type,
                confidence=confidence,
                keywords_matched=matched,
            )
        return None
