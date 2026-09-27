"""反思过时更新/洞察 mixin（从 reflector 拆出）。"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable

from app.dreaming._reflector_models import InsightItem, UpdateResult
from app.dreaming.session_extractor import ProjectSession

logger = logging.getLogger(__name__)


class _UpdateMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _llm_reflect: Callable[..., Any]
    _prepare_session_summaries: Callable[..., Any]
    _rule_based_insights: Callable[..., Any]
    enable_llm: Any
    store: Any

    def _update_stale_entries(self, sessions: list[ProjectSession]) -> UpdateResult:
        """用新 Session 数据修正过时的 memory 条目。

        策略：
            - 遍历失败 Session，找到对应 entity 的 memory
            - 如果 memory 的 confidence 高但实际失败，降低 confidence
            - HRC52 pending_calibration 强制降低置信度（硬约束）
        """
        result = UpdateResult()

        for session in sessions:
            # 只处理失败/警告类 Session
            if session.outcome not in ("failure", "warning"):
                continue

            entity = session.material_type or "unknown"
            related = self.store.read_by_entity(f"material-{entity}")

            for entry in related:
                node_id = entry["node_id"]
                props = entry["properties"]
                confidence = props.get("confidence", 0.5)

                # 如果 memory 置信度高但实际失败，降低置信度
                if confidence > 0.7 and session.outcome == "failure":
                    new_confidence = max(0.2, confidence - 0.3)
                    self.store.update_observation(
                        node_id=node_id,
                        confidence=new_confidence,
                    )
                    result.invalidated_node_ids.append(node_id)
                    result.details.append(
                        {
                            "node_id": node_id,
                            "old_confidence": confidence,
                            "new_confidence": new_confidence,
                            "reason": f"session_failure: {session.failure_reason}",
                            "session_id": session.session_id,
                        }
                    )

                # HRC52 pending_calibration 硬约束：强制降低置信度
                if entity.upper() in ("HRC52", "HRC_52"):
                    if props.get("metadata", {}).get("calibration_status") == "pending_calibration":
                        new_confidence = min(confidence, 0.3)
                        if new_confidence != confidence:
                            self.store.update_observation(
                                node_id=node_id,
                                confidence=new_confidence,
                            )
                            result.invalidated_node_ids.append(node_id)
                            result.details.append(
                                {
                                    "node_id": node_id,
                                    "old_confidence": confidence,
                                    "new_confidence": new_confidence,
                                    "reason": "HRC52_pending_calibration_hard_constraint",
                                }
                            )

                # CAM 验证失败：标记 memory 需要重新验证
                if session.cam_validation_passed is False:
                    self.store.graph.update_node_properties(
                        node_id,
                        {"requires_revalidation": True},
                    )
                    result.updated_node_ids.append(node_id)

        logger.info(
            "过时更新完成：invalidated=%d, updated=%d",
            len(result.invalidated_node_ids),
            len(result.updated_node_ids),
        )
        return result

    async def _surface_insights(
        self,
        sessions: list[ProjectSession],
        instructions: str | None,
    ) -> tuple[list[InsightItem], bool, str | None]:
        """跨 Session 发现潜在规律。

        优先使用 LLM 反思；LLM 不可用时降级为规则统计。
        """
        # 准备 Session 摘要文本
        session_summaries = self._prepare_session_summaries(sessions)

        # 尝试 LLM 反思
        if self.enable_llm:
            llm_result = await self._llm_reflect(session_summaries, instructions)
            if llm_result is not None:
                insights, model = llm_result
                return insights, True, model

        # 降级：规则统计
        logger.info("LLM 不可用，降级为规则统计洞察")
        insights = self._rule_based_insights(sessions)
        return insights, False, None
