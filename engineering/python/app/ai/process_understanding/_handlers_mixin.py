"""工艺理解引擎路由/处理 mixin（从 engine 拆出）。"""

from __future__ import annotations

import logging
import re
from typing import Any
from collections.abc import Callable

from app.ai.process_understanding._output import ProcessUnderstandingOutput, task_type_to_code
from app.ai.process_understanding._prompts import ENTITY_EXTRACTION_PROMPT, FAULT_DIAGNOSIS_PROMPT, GENERAL_QA_PROMPT
from app.ai.process_understanding.knowledge_retriever import HybridRetrievalResult
from app.ai.process_understanding.prediction_explainer import PredictionData, PredictionExplanation
from app.ai.process_understanding.solution_generator import ProcessSolution
from app.ai.process_understanding.task_classifier import ClassificationResult, TaskType

logger = logging.getLogger(__name__)


class _HandlersMixin:
    async def _route_by_task_type(
        self,
        user_input: str,
        task_type: TaskType,
        classification: ClassificationResult,
        retrieval: HybridRetrievalResult,
        entities: dict[str, str],
    ) -> ProcessUnderstandingOutput:
        """根据任务类型路由到不同的处理逻辑。"""
        if task_type == TaskType.SOLUTION_GENERATION:
            return await self._handle_solution_generation(user_input, entities, retrieval, classification)
        elif task_type == TaskType.FAULT_DIAGNOSIS:
            return await self._handle_fault_diagnosis(user_input, retrieval, entities, classification)
        elif task_type == TaskType.CHITCHAT:
            return self._handle_chitchat(classification)
        elif task_type == TaskType.PROCESS_CONSULT:
            return await self._handle_process_consult(user_input, retrieval, entities, classification)
        else:  # KNOWLEDGE_QUERY
            return await self._handle_knowledge_query(user_input, retrieval, entities, classification)

    async def _handle_solution_generation(
        self,
        user_input: str,
        entities: dict[str, str],
        retrieval: HybridRetrievalResult,
        classification: ClassificationResult,
    ) -> ProcessUnderstandingOutput:
        """处理方案生成请求。"""
        material = entities.get("材料", "45钢")
        precision = entities.get("精度", "IT8")
        batch = entities.get("批量", "单件")
        machine = entities.get("设备", "CNC加工中心")

        solution = await self.solution_generator.generate(
            material=material,
            precision_level=precision,
            batch_size=batch,
            machine_type=machine,
        )

        knowledge_text = "\n".join(d.content[:200] for d in retrieval.documents[:3])

        return ProcessUnderstandingOutput(
            task_type=task_type_to_code(TaskType.SOLUTION_GENERATION),
            intent=f"为{precision}精度的{material}工件生成{machine}加工方案",
            entities=entities,
            response=self._format_solution_response(solution),
            confidence=solution.confidence_score / 10.0,
            sources=[d.source for d in retrieval.documents[:5]],
            actions=[f"步骤{i + 1}: {s.operation}" for i, s in enumerate(solution.process_route[:5])],
            details={
                "solution": solution.to_dict(),
                "knowledge_summary": knowledge_text[:500],
                "classification_confidence": classification.confidence,
            },
        )

    async def _handle_fault_diagnosis(
        self,
        user_input: str,
        retrieval: HybridRetrievalResult,
        entities: dict[str, str],
        classification: ClassificationResult,
    ) -> ProcessUnderstandingOutput:
        """处理故障诊断请求。"""
        knowledge_text = "\n\n".join(d.content[:500] for d in retrieval.documents[:5])

        prompt = FAULT_DIAGNOSIS_PROMPT.format(
            knowledge_context=knowledge_text or "暂无相关参考知识",
            user_input=user_input,
        )

        client = await self._get_llm_client()
        try:
            response = await client.chat_completion(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_input},
                ],
                max_tokens=2048,
                temperature=0.3,
            )
            content = response.get("content", "")
        except (RuntimeError, OSError, ValueError, TypeError, ImportError, AttributeError, KeyError) as e:
            logger.error("故障诊断LLM调用失败: %s", e, exc_info=True)
            content = "故障诊断服务暂时不可用，请联系技术人员。建议检查：1)刀具状态 2)切削参数 3)设备运行状态。"

        # 提取操作建议
        actions = self._extract_actions_from_text(content)

        return ProcessUnderstandingOutput(
            task_type=task_type_to_code(TaskType.FAULT_DIAGNOSIS),
            intent="加工故障诊断",
            entities=entities,
            response=content,
            confidence=classification.confidence,
            sources=[d.source for d in retrieval.documents[:5]],
            actions=actions,
            details={
                "knowledge_count": len(retrieval.documents),
                "classification_confidence": classification.confidence,
            },
        )

    async def _handle_process_consult(
        self,
        user_input: str,
        retrieval: HybridRetrievalResult,
        entities: dict[str, str],
        classification: ClassificationResult,
    ) -> ProcessUnderstandingOutput:
        """处理工艺咨询请求。"""
        knowledge_text = "\n\n".join(d.content[:500] for d in retrieval.documents[:5])

        prompt = GENERAL_QA_PROMPT.format(
            knowledge_context=knowledge_text or "暂无相关参考知识",
            user_input=user_input,
        )

        client = await self._get_llm_client()
        try:
            response = await client.chat_completion(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_input},
                ],
                max_tokens=2048,
                temperature=0.3,
            )
            content = response.get("content", "")
        except (RuntimeError, OSError, ValueError, TypeError, ImportError, AttributeError, KeyError) as e:
            logger.error("工艺咨询LLM调用失败: %s", e, exc_info=True)
            content = "工艺咨询服务暂时不可用，请稍后重试。建议参考相关工艺手册或联系工艺工程师。"

        actions = self._extract_actions_from_text(content)

        return ProcessUnderstandingOutput(
            task_type=task_type_to_code(TaskType.PROCESS_CONSULT),
            intent="工艺技术咨询",
            entities=entities,
            response=content,
            confidence=classification.confidence,
            sources=[d.source for d in retrieval.documents[:5]],
            actions=actions,
            details={
                "knowledge_count": len(retrieval.documents),
            },
        )

    async def _handle_knowledge_query(
        self,
        user_input: str,
        retrieval: HybridRetrievalResult,
        entities: dict[str, str],
        classification: ClassificationResult,
    ) -> ProcessUnderstandingOutput:
        """处理知识查询请求。"""
        knowledge_text = "\n\n".join(d.content[:500] for d in retrieval.documents[:5])

        prompt = GENERAL_QA_PROMPT.format(
            knowledge_context=knowledge_text or "暂无相关参考知识",
            user_input=user_input,
        )

        client = await self._get_llm_client()
        try:
            response = await client.chat_completion(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_input},
                ],
                max_tokens=2048,
                temperature=0.3,
            )
            content = response.get("content", "")
        except (RuntimeError, OSError, ValueError, TypeError, ImportError, AttributeError, KeyError) as e:
            logger.error("知识查询LLM调用失败: %s", e, exc_info=True)
            content = "知识查询服务暂时不可用，请稍后重试。"

        return ProcessUnderstandingOutput(
            task_type=task_type_to_code(TaskType.KNOWLEDGE_QUERY),
            intent="知识查询",
            entities=entities,
            response=content,
            confidence=classification.confidence,
            sources=[d.source for d in retrieval.documents[:5]],
            actions=[],
            details={
                "knowledge_count": len(retrieval.documents),
                "top_knowledge": knowledge_text[:200] if knowledge_text else "",
            },
        )

    # 宿主契约：由主类 / 兄弟 mixin 提供
    solution_generator: Any
    explainer: Any
    _get_llm_client: Callable[..., Any]

    def _handle_chitchat(self, classification: ClassificationResult) -> ProcessUnderstandingOutput:
        """处理闲聊请求。"""
        return ProcessUnderstandingOutput(
            task_type=task_type_to_code(TaskType.CHITCHAT),
            intent="问候/闲聊",
            entities={},
            response=(
                "您好！我是灵境制造AI助手，专注于制造业工艺知识服务。"
                "我可以帮您：\n"
                "1. 咨询加工工艺和切削参数\n"
                "2. 诊断加工故障和异常\n"
                "3. 生成完整的加工工艺方案\n"
                "4. 查询标准、规范和最佳实践\n"
                "5. 解释模型预测结果\n\n"
                "请告诉我您需要什么帮助？"
            ),
            confidence=classification.confidence,
            sources=[],
            actions=[],
        )

    async def explain_prediction(
        self,
        prediction: PredictionData,
    ) -> ProcessUnderstandingOutput:
        """解释LNN/JEPA模型预测结果。

        Args:
            prediction: 模型预测数据

        Returns:
            ProcessUnderstandingOutput 包含解释和操作建议
        """
        explanation = await self.explainer.explain(prediction)

        return ProcessUnderstandingOutput(
            task_type="PREDICTION_EXPLAIN",
            intent="模型预测结果解释",
            entities={
                "切削力": f"{prediction.force_pred:.1f}N",
                "刀具磨损": f"{prediction.wear_pred:.3f}mm",
            },
            response=self._format_explanation_response(explanation),
            confidence=min(prediction.force_conf, prediction.wear_conf) / 100.0,
            sources=["LNN模型", "JEPA视觉分析"],
            actions=explanation.recommended_actions,
            details={
                "explanation": explanation.to_dict(),
                "risk_level": explanation.risk_level,
                "prediction": {
                    "force_pred": prediction.force_pred,
                    "force_conf": prediction.force_conf,
                    "wear_pred": prediction.wear_pred,
                    "wear_conf": prediction.wear_conf,
                    "anomaly_prob": prediction.anomaly_prob,
                },
            },
        )

    async def _extract_entities(self, user_input: str) -> dict[str, str]:
        """从用户输入中提取工艺实体。"""
        client = await self._get_llm_client()
        prompt = ENTITY_EXTRACTION_PROMPT.format(user_input=user_input)

        try:
            response = await client.chat_completion(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_input},
                ],
                max_tokens=512,
                temperature=0.1,
            )
            content = response.get("content", "").strip()
            return self._parse_entity_json(content)
        except (RuntimeError, OSError, ValueError, TypeError, ImportError, AttributeError, KeyError) as e:
            logger.warning("实体提取失败: %s", e, exc_info=True)
            return {}

    @staticmethod
    def _parse_entity_json(content: str) -> dict[str, str]:
        """解析实体提取的JSON结果。"""
        from app.utils.utils import extract_json_from_markdown

        try:
            return extract_json_from_markdown(content)
        except (ValueError, KeyError, TypeError) as e:
            # JSON解析失败时回退到正则提取，记录警告以便调试
            logger.warning("Entity JSON parsing failed, falling back to regex: %s", e)
            # 基于正则的简化提取
            entities = {}
            fields = ["材料", "精度", "批量", "设备", "刀具", "特征"]
            for field_name in fields:
                pattern = rf'"{field_name}"\s*:\s*"([^"]*)"'
                match = re.search(pattern, content)
                if match:
                    entities[field_name] = match.group(1)
            return entities

    @staticmethod
    def _format_solution_response(solution: ProcessSolution) -> str:
        """格式化方案生成为可读文本。"""
        lines = [
            f"## {solution.material} 加工工艺方案",
            "",
            f"**精度要求**: {solution.precision_level}",
            f"**批量大小**: {solution.batch_size}",
            f"**设备类型**: {solution.machine_type}",
            "",
            "### 加工路线",
        ]
        for step in solution.process_route:
            lines.append(f"{step.step_number}. **{step.operation}** ({step.machine}) - {step.description}")

        lines.append("")
        lines.append("### 切削参数")
        lines.append("| 工序 | 刀具 | 转速 | 进给 | 切深 |")
        lines.append("|------|------|------|------|------|")
        for param in solution.cutting_parameters:
            lines.append(
                f"| {param.operation} | {param.tool} | "
                f"{param.spindle_speed} | {param.feed_rate} | "
                f"{param.depth_of_cut} |"
            )

        if solution.risk_warnings:
            lines.append("")
            lines.append("### 风险提示")
            for risk in solution.risk_warnings:
                icon = {"high": "[高]", "medium": "[中]", "low": "[低]"}.get(risk.severity, "[?]")
                lines.append(f"- {icon} **{risk.risk}**")
                if risk.mitigation:
                    lines.append(f"  - 应对措施: {risk.mitigation}")

        lines.append("")
        lines.append("### 置信度评估")
        lines.append(f"- 综合置信度: {solution.confidence_score}/10")
        if solution.uncertainty:
            lines.append(f"- 主要不确定性: {solution.uncertainty}")

        return "\n".join(lines)

    @staticmethod
    def _format_explanation_response(explanation: PredictionExplanation) -> str:
        """格式化预测解释为可读文本。"""
        lines = [
            "## 加工状态分析",
            "",
            f"**风险等级**: {explanation.risk_level.upper()}",
            "",
            "### 概述",
            f"{explanation.summary}",
        ]

        for section in explanation.sections:
            icon = {"high": "[高]", "normal": "[中]", "low": "[低]"}.get(section.priority, "[?]")
            lines.append("")
            lines.append(f"### {icon} {section.title}")
            lines.append(section.content)

        if explanation.recommended_actions:
            lines.append("")
            lines.append("### 建议操作")
            for i, action in enumerate(explanation.recommended_actions, 1):
                lines.append(f"{i}. {action}")

        if explanation.attention_points:
            lines.append("")
            lines.append("### 注意事项")
            for point in explanation.attention_points:
                lines.append(f"- {point}")

        return "\n".join(lines)

    @staticmethod
    def _extract_actions_from_text(text: str) -> list[str]:
        """从文本中提取操作建议列表。"""
        actions = []
        # 查找数字编号的建议
        numbered = re.findall(r"(?:^|\n)\s*(?:\d+[.、)）]|[-•])\s*(.+?)(?:\n|$)", text)
        if numbered:
            actions = [a.strip() for a in numbered[:5] if len(a.strip()) > 5]
        # 查找"建议"、"应该"等关键词
        if not actions:
            suggestion_patterns = re.findall(r"(?:建议|应当|应该|需要|请|务必|注意)[^。\n]{5,50}", text)
            actions = [s.strip() for s in suggestion_patterns[:5]]
        return actions
