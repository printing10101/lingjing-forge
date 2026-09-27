"""
灵境制造 - LLM工艺理解与知识问答模块

提供自然语言理解、工艺知识检索、方案生成及模型预测结果解释能力，
辅助制造业工艺决策与问题解决。

核心组件:
- TaskClassifier: 任务分类模块
- KnowledgeRetriever: 知识检索模块（混合检索 + 重排序）
- SolutionGenerator: 工艺方案生成模块
- PredictionExplainer: 模型预测结果解释模块
- ProcessUnderstandingEngine: 主引擎（整合所有子模块）
"""

from app.ai.process_understanding.engine import ProcessUnderstandingEngine
from app.ai.process_understanding.task_classifier import TaskClassifier, TaskType
from app.ai.process_understanding.knowledge_retriever import (
    KnowledgeRetriever,
    HybridRetrievalResult,
)
from app.ai.process_understanding.solution_generator import (
    SolutionGenerator,
    ProcessSolution,
)
from app.ai.process_understanding.prediction_explainer import (
    PredictionExplainer,
    PredictionExplanation,
)

__all__ = [
    "ProcessUnderstandingEngine",
    "TaskClassifier",
    "TaskType",
    "KnowledgeRetriever",
    "HybridRetrievalResult",
    "SolutionGenerator",
    "ProcessSolution",
    "PredictionExplainer",
    "PredictionExplanation",
]
