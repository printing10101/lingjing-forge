"""
LLM工艺理解主引擎

整合任务分类、知识检索、方案生成和预测结果解释四大模块，
提供统一的工艺理解与知识问答入口。

处理流程：
1. 用户输入 -> 任务分类
2. 知识检索（根据任务类型调整策略）
3. 根据任务类型路由到对应处理模块
4. 格式化输出（JSON统一格式）

本模块为门面：实现已拆分至 _output / _prompts / _handlers_mixin / _engine_holder。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.ai.process_understanding._engine_holder import (  # noqa: F401
    get_process_understanding_engine,
)
from app.ai.process_understanding._handlers_mixin import _HandlersMixin
from app.ai.process_understanding._output import (  # noqa: F401
    ProcessUnderstandingOutput,
    task_type_to_code,
)
from app.ai.process_understanding.knowledge_retriever import KnowledgeRetriever
from app.ai.process_understanding.task_classifier import TaskClassifier
from app.ai.process_understanding.solution_generator import SolutionGenerator
from app.ai.process_understanding.prediction_explainer import PredictionExplainer

logger = logging.getLogger(__name__)


class ProcessUnderstandingEngine(_HandlersMixin):
    """LLM工艺理解主引擎。

    整合所有子模块，提供统一的工艺理解与知识问答接口。
    支持：
    - 自然语言理解与意图分类
    - 混合知识检索
    - 工艺方案生成
    - 模型预测结果解释
    - 通用工艺知识问答
    """

    def __init__(self):
        self._classifier: TaskClassifier | None = None
        self._retriever: KnowledgeRetriever | None = None
        self._solution_generator: SolutionGenerator | None = None
        self._explainer: PredictionExplainer | None = None
        self._llm_client: Any = None
        self._total_requests = 0
        self._total_latency_ms = 0.0

    @property
    def classifier(self) -> TaskClassifier:
        if self._classifier is None:
            self._classifier = TaskClassifier()
        return self._classifier

    @property
    def retriever(self) -> KnowledgeRetriever:
        if self._retriever is None:
            self._retriever = KnowledgeRetriever()
        return self._retriever

    @property
    def solution_generator(self) -> SolutionGenerator:
        if self._solution_generator is None:
            self._solution_generator = SolutionGenerator()
        return self._solution_generator

    @property
    def explainer(self) -> PredictionExplainer:
        if self._explainer is None:
            self._explainer = PredictionExplainer()
        return self._explainer

    async def _get_llm_client(self) -> Any:
        # 修复断点 A：通过 get_llm_client() 工厂函数接入 Provider 网关，
        # 优先使用用户在系统设置中激活的 Provider（本地 Ollama/LM Studio/llama.cpp/vLLM 或云端 API），
        # 无激活 Provider 时回退到 config.ai 配置（向后兼容）。
        if self._llm_client is None:
            from app.ai.llm_client import get_llm_client

            self._llm_client = await get_llm_client()
        return self._llm_client

    async def process(self, user_input: str) -> ProcessUnderstandingOutput:
        """处理用户输入，返回结构化的工艺理解结果。

        这是模块的主入口。

        Args:
            user_input: 用户自然语言输入

        Returns:
            ProcessUnderstandingOutput 包含分类、意图、回复等完整信息
        """
        start_time = time.perf_counter()
        self._total_requests += 1

        # 1. 任务分类 + 实体提取 并行执行（两者相互独立）
        # 检索依赖 task_type，需等分类完成后再启动
        classification, entities = await asyncio.gather(
            self.classifier.classify(user_input),
            self._extract_entities(user_input),
        )
        task_type = classification.task_type

        # 2. 知识检索（依赖 task_type）
        retrieval = await self.retriever.retrieve(query=user_input, task_type=task_type)

        # 3. 根据任务类型路由处理
        output = await self._route_by_task_type(
            user_input=user_input,
            task_type=task_type,
            classification=classification,
            retrieval=retrieval,
            entities=entities,
        )

        elapsed = (time.perf_counter() - start_time) * 1000
        output.latency_ms = elapsed
        self._total_latency_ms += elapsed

        logger.info(
            "处理完成: type=%s, intent=%s, confidence=%.2f, %.1fms",
            output.task_type,
            output.intent,
            output.confidence,
            elapsed,
        )

        return output

    def get_stats(self) -> dict[str, Any]:
        """获取引擎整体性能统计。"""
        return {
            "total_requests": self._total_requests,
            "avg_latency_ms": (self._total_latency_ms / self._total_requests if self._total_requests > 0 else 0.0),
            "classifier": self._classifier.get_stats() if self._classifier else {},
            "retriever": self._retriever.get_stats() if self._retriever else {},
            "solution_generator": (self._solution_generator.get_stats() if self._solution_generator else {}),
            "explainer": self._explainer.get_stats() if self._explainer else {},
        }
