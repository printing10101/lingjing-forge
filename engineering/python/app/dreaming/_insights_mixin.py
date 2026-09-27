"""反思洞察生成 mixin（从 reflector 拆出）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable

from app.dreaming._reflector_models import DeduplicationResult, InsightItem, UpdateResult
from app.dreaming.session_extractor import ProjectSession

logger = logging.getLogger(__name__)


class _InsightsMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_llm_router: Callable[..., Any]

    def _prepare_session_summaries(self, sessions: list[ProjectSession]) -> str:
        """将 Session 列表准备为 LLM 输入文本。"""
        lines = []
        for s in sessions:
            line = (
                f"[{s.source}] {s.session_id} @ {s.timestamp}\n"
                f"  material={s.material_type}, outcome={s.outcome}\n"
                f"  chatter_conf={s.chatter_confidence}, "
                f"cam_passed={s.cam_validation_passed}\n"
            )
            if s.failure_reason:
                line += f"  failure_reason={s.failure_reason}\n"
            lines.append(line)
        return "\n".join(lines)

    async def _llm_reflect(
        self,
        session_summaries: str,
        instructions: str | None,
    ) -> tuple[list[InsightItem], str] | None:
        """调用 LLM 进行反思。

        Returns:
            (insights, model_name) 或 None（LLM 不可用）
        """
        router = self._get_llm_router()
        if router is None:
            return None

        # 构造反思 prompt
        prompt = self._build_reflection_prompt(session_summaries, instructions)

        messages = [
            {
                "role": "system",
                "content": (
                    "你是'灵境制造'项目的离线反思助手。"
                    "请分析以下 Session 记录，发现潜在规律、异常和可执行的规则候选。"
                    '输出 JSON 格式：{"insights": [{"category": "...", "content": "...", "confidence": 0.0}]}。'
                    "category 可选：pattern / anomaly / rule_candidate / warning。"
                    "硬约束：CAM 二次验证始终必须，SUCCEEDED 任务不可删除，"
                    "HRC52 pending_calibration 必须降低置信度。"
                ),
            },
            {"role": "user", "content": prompt},
        ]

        try:
            response = await router.chat_completion(
                messages=messages,
                max_tokens=2048,
                temperature=0.3,  # 低温度保证稳定性
            )
            content = response.get("content", "")
            model = response.get("model", "unknown")

            # 解析 LLM 输出
            insights = self._parse_llm_insights(content)
            return insights, model

        except Exception as e:
            logger.warning("LLM 反思调用失败: %s", e)
            return None

    def _build_reflection_prompt(
        self,
        session_summaries: str,
        instructions: str | None,
    ) -> str:
        """构造反思 prompt。"""
        prompt = "请分析以下项目 Session 记录：\n\n"
        prompt += session_summaries
        prompt += "\n\n"
        if instructions:
            prompt += f"特别关注：{instructions}\n\n"
        prompt += (
            "请从以下维度反思：\n"
            "1. pattern：跨 Session 的重复模式（如某材料总是失败）\n"
            "2. anomaly：异常值或离群点\n"
            "3. rule_candidate：可转化为规则候选的规律\n"
            "4. warning：需要人工介入的警告\n"
            '输出 JSON：{"insights": [...]}'
        )
        return prompt

    def _parse_llm_insights(self, content: str) -> list[InsightItem]:
        """解析 LLM 输出为 InsightItem 列表。"""
        # 尝试提取 JSON
        try:
            # 处理可能的 markdown 代码块包裹
            if "```json" in content:
                start = content.index("```json") + 7
                end = content.index("```", start)
                content = content[start:end]
            elif "```" in content:
                start = content.index("```") + 3
                end = content.index("```", start)
                content = content[start:end]

            data = json.loads(content)
            insights_data = data.get("insights", [])
            return [
                InsightItem(
                    category=i.get("category", "pattern"),
                    content=i.get("content", ""),
                    confidence=float(i.get("confidence", 0.5)),
                )
                for i in insights_data
            ]
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("LLM 输出解析失败: %s", e)
            return []

    def _rule_based_insights(self, sessions: list[ProjectSession]) -> list[InsightItem]:
        """规则统计降级：无 LLM 时的洞察生成。"""
        insights: list[InsightItem] = []

        # 规则 1：按材料统计失败率
        material_failures: dict[str, list[str]] = {}
        for s in sessions:
            if s.outcome == "failure" and s.material_type:
                material_failures.setdefault(s.material_type, []).append(s.session_id)

        for material, fail_sessions in material_failures.items():
            if len(fail_sessions) >= 2:
                insights.append(
                    InsightItem(
                        category="pattern",
                        content=f"材料 {material} 出现 {len(fail_sessions)} 次失败，建议检查切削参数推荐逻辑",
                        confidence=0.7,
                        supporting_sessions=fail_sessions,
                    )
                )

        # 规则 2：CAM 验证失败聚集
        cam_failures = [s for s in sessions if s.cam_validation_passed is False]
        if len(cam_failures) >= 2:
            insights.append(
                InsightItem(
                    category="warning",
                    content=f"CAM 验证失败 {len(cam_failures)} 次，"
                    f"常见原因：{cam_failures[0].cam_validation_failure_reason}",
                    confidence=0.6,
                    supporting_sessions=[s.session_id for s in cam_failures],
                )
            )

        # 规则 3：SUCCEEDED 锁定提醒
        succeeded = [s for s in sessions if s.outcome == "success"]
        if succeeded:
            insights.append(
                InsightItem(
                    category="rule_candidate",
                    content=f"{len(succeeded)} 个 Session 成功，对应的 memory 条目应提升 validation_count",
                    confidence=0.5,
                    supporting_sessions=[s.session_id for s in succeeded],
                )
            )

        return insights

    # 摘要生成

    def _generate_summary(
        self,
        sessions: list[ProjectSession],
        dedup: DeduplicationResult,
        update: UpdateResult,
        insights: list[InsightItem],
    ) -> str:
        """生成人类可读的反思摘要。"""
        success_count = sum(1 for s in sessions if s.outcome == "success")
        failure_count = sum(1 for s in sessions if s.outcome == "failure")

        summary = (
            f"Dreaming 反思报告 @ {datetime.now(timezone.utc).isoformat()}\n"
            f"输入 Session 数：{len(sessions)} "
            f"（成功 {success_count}，失败 {failure_count}）\n"
            f"去重：合并 {dedup.merged_count} 条，"
            f"移除 {len(dedup.removed_node_ids)} 条\n"
            f"过时更新：失效 {len(update.invalidated_node_ids)} 条，"
            f"标记 {len(update.updated_node_ids)} 条需重新验证\n"
            f"洞察浮现：{len(insights)} 条"
        )
        if insights:
            summary += "（"
            for i in insights[:3]:
                summary += f"[{i.category}] {i.content[:50]}...; "
            if len(insights) > 3:
                summary += f"等 {len(insights)} 条"
            summary += "）"
        return summary
