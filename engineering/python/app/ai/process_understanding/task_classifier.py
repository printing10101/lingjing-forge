"""
任务分类模块

对用户输入进行精准分类，确定任务类型。
支持 A-工艺咨询 / B-故障诊断 / C-方案生成 / D-知识查询 / E-闲聊 五类。

本模块为门面：实现已拆分至 _task_types / _keywords / _rule_classifier。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.ai.process_understanding._rule_classifier import RuleBasedClassifier  # noqa: F401
from app.ai.process_understanding._task_types import (  # noqa: F401
    ClassificationResult,
    TaskType,
)

logger = logging.getLogger(__name__)


CLASSIFICATION_SYSTEM_PROMPT = """你是一个制造业AI助手。请判断用户输入属于哪个任务类别：

类别：
A. 工艺咨询 - 询问材料加工方法、刀具选择、参数推荐等工艺技术问题
B. 故障诊断 - 报告加工异常、刀具问题、设备故障、加工缺陷
C. 方案生成 - 需要生成完整的加工工艺方案或工艺路线
D. 知识查询 - 查询标准、规范、公差、最佳实践等知识性问题
E. 闲聊 - 问候、感谢或其他与制造业无关的内容

请严格只输出一个JSON对象：
{"task_type": "A/B/C/D/E", "confidence": 0.0-1.0, "reason": "简短理由"}

不要输出其他内容。"""


class TaskClassifier:
    """LLM增强的任务分类器。

    采用两级分类策略：
    1. 规则匹配（<10ms）：覆盖约80%的常见输入
    2. LLM分类（<500ms）：处理模糊或复杂输入
    """

    # LLM 置信度参数
    LLM_DEFAULT_CONFIDENCE: float = 0.8  # LLM 解析成功的默认置信度
    LLM_FALLBACK_CONFIDENCE: float = 0.5  # LLM 解析失败时的回退置信度
    LLM_ERROR_CONFIDENCE: float = 0.3  # LLM 异常时的最低置信度
    LLM_PARSE_DEFAULT_CONFIDENCE: float = 0.5  # LLM 响应中 confidence 字段缺失时的默认值

    def __init__(self):
        self._rule_classifier = RuleBasedClassifier()
        self._llm_client: Any = None
        self._total_classifications = 0
        self._rule_hits = 0
        self._llm_hits = 0
        self._total_latency_ms = 0.0

    async def _get_llm_client(self) -> Any:
        # 修复断点 A：通过 get_llm_client() 工厂函数接入 Provider 网关，
        # 优先使用用户在系统设置中激活的 Provider（本地 Ollama/LM Studio/llama.cpp/vLLM 或云端 API），
        # 无激活 Provider 时回退到 config.ai 配置（向后兼容）。
        if self._llm_client is None:
            from app.ai.llm_client import get_llm_client

            self._llm_client = await get_llm_client()
        return self._llm_client

    async def classify(self, user_input: str) -> ClassificationResult:
        """对用户输入进行分类。

        Args:
            user_input: 用户输入文本

        Returns:
            ClassificationResult 包含任务类型、置信度等信息
        """
        start_time = time.perf_counter()
        self._total_classifications += 1

        # 第一级：规则匹配
        rule_result = self._rule_classifier.classify(user_input)
        if rule_result is not None:
            elapsed = (time.perf_counter() - start_time) * 1000
            rule_result.latency_ms = elapsed
            self._rule_hits += 1
            self._total_latency_ms += elapsed
            logger.debug(
                "规则分类命中: %s (置信度=%.2f, 耗时=%.1fms)",
                rule_result.task_type.label,
                rule_result.confidence,
                elapsed,
            )
            return rule_result

        # 第二级：LLM分类
        llm_result = await self._classify_via_llm(user_input)
        elapsed = (time.perf_counter() - start_time) * 1000
        llm_result.latency_ms = elapsed
        self._llm_hits += 1
        self._total_latency_ms += elapsed
        logger.info(
            "LLM分类: %s (置信度=%.2f, 耗时=%.1fms)",
            llm_result.task_type.label,
            llm_result.confidence,
            elapsed,
        )
        return llm_result

    async def _classify_via_llm(self, user_input: str) -> ClassificationResult:
        """通过LLM进行任务分类。"""
        client = await self._get_llm_client()
        messages = [
            {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_input},
        ]

        try:
            response = await client.chat_completion(
                messages=messages,
                max_tokens=256,
                temperature=0.1,
            )
            content = response.get("content", "").strip()
            return self._parse_llm_response(content)
        except (RuntimeError, OSError, ValueError, TypeError, ImportError, AttributeError, KeyError) as e:
            logger.warning("LLM分类失败，降级为通用查询: %s", e, exc_info=True)
            return ClassificationResult(
                task_type=TaskType.KNOWLEDGE_QUERY,
                confidence=self.LLM_ERROR_CONFIDENCE,
                raw_response="llm_classification_failed",
            )

    @staticmethod
    def _parse_llm_response(content: str) -> ClassificationResult:
        """解析LLM返回的分类结果。"""
        # 尝试提取JSON
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                task_code = data.get("task_type", "").strip().upper()
                if task_code in ("A", "B", "C", "D", "E"):
                    return ClassificationResult(
                        task_type=TaskType.from_code(task_code),
                        confidence=float(data.get("confidence", TaskClassifier.LLM_PARSE_DEFAULT_CONFIDENCE)),
                        raw_response=content,
                    )
            except (json.JSONDecodeError, ValueError) as parse_err:
                # LLM 输出非 JSON 时回退到原始文本解析，记录失败原因
                logger.debug(
                    "Failed to parse LLM classification JSON, fallback to text scan: %s",
                    parse_err,
                    exc_info=True,
                )

        # 降级：从原始文本中提取单个字母
        for ch in content:
            if ch in ("A", "B", "C", "D", "E"):
                return ClassificationResult(
                    task_type=TaskType.from_code(ch),
                    confidence=TaskClassifier.LLM_FALLBACK_CONFIDENCE,
                    raw_response=content,
                )

        logger.warning("无法解析LLM分类结果，降级为通用查询: %s", content[:200])
        return ClassificationResult(
            task_type=TaskType.KNOWLEDGE_QUERY,
            confidence=TaskClassifier.LLM_ERROR_CONFIDENCE,
            raw_response=content,
        )

    def get_stats(self) -> dict[str, Any]:
        """获取分类器性能统计。"""
        return {
            "total_classifications": self._total_classifications,
            "rule_hits": self._rule_hits,
            "llm_hits": self._llm_hits,
            "rule_hit_rate": (
                self._rule_hits / self._total_classifications if self._total_classifications > 0 else 0.0
            ),
            "avg_latency_ms": (
                self._total_latency_ms / self._total_classifications if self._total_classifications > 0 else 0.0
            ),
        }
