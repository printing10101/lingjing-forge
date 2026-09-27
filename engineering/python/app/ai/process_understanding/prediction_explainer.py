"""
模型预测结果解释模块

将LNN/JEPA模型的技术预测结果转化为通俗易懂的语言，
向操作员提供明确的指导信息。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# 刀具磨损阈值（单位：mm）
WEAR_REPLACEMENT_THRESHOLD: float = 0.3  # 达到此值建议更换刀具
WEAR_WARNING_THRESHOLD: float = 0.2  # 达到此值发出警告


@dataclass
class PredictionData:
    """模型预测数据"""

    force_pred: float = 0.0  # 切削力预测值 (N)
    force_conf: float = 0.0  # 切削力置信度 (%)
    wear_pred: float = 0.0  # 刀具磨损预测值 (mm)
    wear_conf: float = 0.0  # 刀具磨损置信度 (%)
    visual_status: str = ""  # 工件状态描述
    anomaly_prob: float = 0.0  # 异常概率 (%)


@dataclass
class ExplanationSection:
    """解释结果的一个章节"""

    title: str
    content: str
    priority: str = "normal"  # high / normal / low


@dataclass
class PredictionExplanation:
    """预测结果解释"""

    summary: str = ""
    sections: list[ExplanationSection] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    attention_points: list[str] = field(default_factory=list)
    risk_level: str = "normal"  # critical / high / medium / low / normal
    generation_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "sections": [{"title": s.title, "content": s.content, "priority": s.priority} for s in self.sections],
            "recommended_actions": self.recommended_actions,
            "attention_points": self.attention_points,
            "risk_level": self.risk_level,
        }


# 解释生成 Prompt

EXPLANATION_PROMPT = """你是一个制造业AI助手，负责将传感器和AI模型的预测结果转化为操作员易于理解的指导信息。

## LNN模型预测结果
- 切削力预测：{force_pred} N（置信度：{force_conf}%）
- 刀具磨损预测：{wear_pred} mm（置信度：{wear_conf}%）

## JEPA视觉分析结果
- 工件状态：{visual_status}
- 异常概率：{anomaly_prob}%

## 任务要求
请用通俗易懂的语言向操作员解释以下内容，严格按JSON格式输出：

```json
{{
  "summary": "一句话总结当前状态",
  "sections": [
    {{
      "title": "章节标题",
      "content": "章节内容",
      "priority": "high/normal/low"
    }}
  ],
  "recommended_actions": ["具体操作建议1", "具体操作建议2"],
  "attention_points": ["需要特别注意的事项1", "需要注意的事项2"],
  "risk_level": "critical/high/medium/low/normal"
}}
```

## 编写指南
1. 使用通俗易懂的语言，避免堆砌专业术语
2. 解释要准确反映模型预测结果的数值含义
3. 操作建议需具体、可执行（如"立即停机检查刀具"而非"注意刀具状态"）
4. 风险提示需突出重点，明确优先级
5. 当异常概率 > 50% 时，风险等级应设为 high 或 critical
6. 当刀具磨损预测 > 0.3mm 时，应建议更换刀具
7. 当切削力预测 > 500N 时，应提示检查切削参数"""


class PredictionExplainer:
    """模型预测结果解释器。

    将LNN/JEPA技术预测结果转化为操作员可理解的指导信息。
    支持结构化输出，包含总结、分项解释、操作建议和注意事项。
    """

    def __init__(self):
        self._llm_client: Any = None
        self._total_explanations = 0
        self._total_latency_ms = 0.0

    async def _get_llm_client(self) -> Any:
        """获取 LLM 客户端。

        统一使用 ``get_llm_client()`` 工厂函数，优先复用 ProviderRegistry 中
        已激活的 Provider，回退到 config.ai 配置。避免在此处直接实例化
        ``CloudLLMClient``，以保证客户端生命周期与连接池的统一管理。
        """
        if self._llm_client is None:
            from app.ai.llm_client import get_llm_client

            self._llm_client = await get_llm_client()
        return self._llm_client

    async def explain(
        self,
        prediction: PredictionData,
        additional_context: str | None = None,
    ) -> PredictionExplanation:
        """将模型预测结果转化为操作员可理解的解释。

        Args:
            prediction: LNN/JEPA模型预测数据
            additional_context: 补充上下文信息

        Returns:
            PredictionExplanation 包含通俗解释和操作建议
        """
        start_time = time.perf_counter()
        self._total_explanations += 1

        prompt = EXPLANATION_PROMPT.format(
            force_pred=f"{prediction.force_pred:.1f}",
            force_conf=f"{prediction.force_conf:.1f}",
            wear_pred=f"{prediction.wear_pred:.3f}",
            wear_conf=f"{prediction.wear_conf:.1f}",
            visual_status=prediction.visual_status or "正常",
            anomaly_prob=f"{prediction.anomaly_prob:.1f}",
        )

        if additional_context:
            prompt = f"{prompt}\n\n## 补充信息\n{additional_context}"

        client = await self._get_llm_client()
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请根据上述预测结果，向操作员解释当前状态并提供操作建议。"},
        ]

        try:
            response = await client.chat_completion(
                messages=messages,
                max_tokens=2048,
                temperature=0.3,
            )
            content = response.get("content", "").strip()
        except (RuntimeError, OSError, ValueError, TypeError, ImportError, AttributeError, KeyError) as e:
            logger.error("LLM解释生成失败: %s", e, exc_info=True)
            return self._create_fallback_explanation(prediction)

        explanation = self._parse_explanation(content)

        elapsed = (time.perf_counter() - start_time) * 1000
        explanation.generation_time_ms = elapsed
        self._total_latency_ms += elapsed

        logger.info(
            "解释生成完成: risk=%s, actions=%d, %.1fms",
            explanation.risk_level,
            len(explanation.recommended_actions),
            elapsed,
        )

        return explanation

    @staticmethod
    def _parse_explanation(raw_content: str) -> PredictionExplanation:
        """解析LLM生成的解释结果JSON。"""
        from app.utils.utils import extract_json_from_markdown

        try:
            data = extract_json_from_markdown(raw_content)
            if not data:
                raise ValueError("JSON解析结果为空")

            sections = []
            for sec in data.get("sections", []):
                sections.append(
                    ExplanationSection(
                        title=sec.get("title", ""),
                        content=sec.get("content", ""),
                        priority=sec.get("priority", "normal"),
                    )
                )

            return PredictionExplanation(
                summary=data.get("summary", ""),
                sections=sections,
                recommended_actions=data.get("recommended_actions", []),
                attention_points=data.get("attention_points", []),
                risk_level=data.get("risk_level", "normal"),
            )
        except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as e:
            logger.warning("解释解析失败: %s", e, exc_info=True)
            return PredictionExplanation(
                summary="模型预测结果解析中，请查看原始数据。",
                sections=[
                    ExplanationSection(
                        "提示",
                        "自动解释生成遇到问题，请联系技术人员查看原始预测数据。",
                        "normal",
                    )
                ],
                recommended_actions=["查看原始预测数据", "联系技术人员"],
                risk_level="normal",
            )

    @staticmethod
    def _create_fallback_explanation(prediction: PredictionData) -> PredictionExplanation:
        """创建降级解释（基于规则）。"""
        sections = []
        actions = []
        attention = []
        risk_level = "normal"

        # 切削力分析
        if prediction.force_pred > 500:
            sections.append(
                ExplanationSection(
                    "切削力偏高",
                    f"当前预测切削力为 {prediction.force_pred:.1f} N，超出正常范围。"
                    f"高切削力可能导致刀具磨损加速和工件变形。",
                    "high",
                )
            )
            actions.append("检查切削参数（切削速度、进给量、切深）是否合理")
            actions.append("检查刀具是否磨损，必要时更换刀具")
            risk_level = "high"
        elif prediction.force_pred > 300:
            sections.append(
                ExplanationSection(
                    "切削力正常偏高",
                    f"预测切削力为 {prediction.force_pred:.1f} N，处于正常偏高范围，注意监控。",
                    "normal",
                )
            )
        else:
            sections.append(
                ExplanationSection(
                    "切削力正常",
                    f"预测切削力为 {prediction.force_pred:.1f} N，处于正常范围。",
                    "low",
                )
            )

        # 刀具磨损分析
        if prediction.wear_pred > WEAR_REPLACEMENT_THRESHOLD:
            sections.append(
                ExplanationSection(
                    "刀具磨损严重 - 需要立即处理",
                    f"预测刀具磨损量为 {prediction.wear_pred:.3f} mm，已达到或超过磨损极限。"
                    f"继续使用可能导致加工质量下降和安全隐患。",
                    "high",
                )
            )
            actions.append("立即停机更换刀具")
            attention.append("更换刀具后需重新对刀并验证首件尺寸")
            if risk_level != "high":
                risk_level = "high"
            else:
                risk_level = "critical"
        elif prediction.wear_pred > WEAR_WARNING_THRESHOLD:
            sections.append(
                ExplanationSection(
                    "刀具磨损需关注",
                    f"预测刀具磨损量为 {prediction.wear_pred:.3f} mm，接近磨损极限，建议提前准备备用刀具。",
                    "normal",
                )
            )
            actions.append("准备备用刀具")
            attention.append("密切关注加工表面质量和尺寸变化")
        else:
            sections.append(
                ExplanationSection(
                    "刀具磨损正常",
                    f"预测刀具磨损量为 {prediction.wear_pred:.3f} mm，处于正常范围。",
                    "low",
                )
            )

        # 异常概率分析
        if prediction.anomaly_prob > 50:
            sections.append(
                ExplanationSection(
                    "异常风险较高",
                    f"JEPA视觉分析显示异常概率为 {prediction.anomaly_prob:.1f}%，建议仔细检查工件状态和加工过程。",
                    "high",
                )
            )
            actions.append("检查工件装夹是否牢固")
            actions.append("检查冷却液供应是否正常")
            attention.append("增加巡检频次，关注加工声音和振动变化")
            if risk_level == "normal":
                risk_level = "high"
        elif prediction.anomaly_prob > 30:
            sections.append(
                ExplanationSection(
                    "存在轻微异常迹象",
                    f"异常概率为 {prediction.anomaly_prob:.1f}%，建议保持关注。",
                    "normal",
                )
            )

        return PredictionExplanation(
            summary=f"风险等级: {risk_level}。{sections[0].content[:50] if sections else '系统运行正常'}...",
            sections=sections,
            recommended_actions=actions or ["继续正常加工，按计划巡检"],
            attention_points=attention or ["按标准操作规程执行"],
            risk_level=risk_level,
        )

    def get_stats(self) -> dict[str, Any]:
        """获取解释器性能统计。"""
        return {
            "total_explanations": self._total_explanations,
            "avg_latency_ms": (
                self._total_latency_ms / self._total_explanations if self._total_explanations > 0 else 0.0
            ),
        }
