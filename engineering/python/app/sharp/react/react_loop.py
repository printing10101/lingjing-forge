"""SHARP ReAct 增强循环主逻辑（M3.5）。

对应论文 §4.3 "ReAct Loop with Schema-Aware Augmentation"。

核心流程
--------
1. `StrategicPlanner.plan(triple)` 生成 `VerificationStrategy`
2. 循环（最多 `max_steps` 步）：
   a. 构造 prompt（system + user + trajectory）
   b. 调用 `LLMRouter.chat_completion` 生成下一步动作
   c. `parse_action` 解析 LLM 输出为 `{"type":"action"|"finish", ...}`
   d. 若为 action：执行工具 → 记录轨迹 → 检查终止条件
   e. 若为 finish：直接终止并返回 LLM 给出的 verdict/confidence
3. 终止后：
   - `EvidenceReranker.collect_from_tool_results` 收集证据
   - `rerank` 加权排序
   - `aggregate_confidence` 计算最终置信度
4. 返回 `VerificationResult`

容错设计
--------
- LLM 调用失败：重试 1 次，仍失败则记录错误步骤并继续
- 工具调用失败：由 `BaseTool.execute` 捕获，记为失败步骤
- 解析失败：记为错误步骤，连续 3 次错误触发熔断（由 `StoppingCriteria` 处理）
- 资源耗尽：所有工具均不可用则提前终止
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.sharp.react.prompt_templates import (
    SYSTEM_PROMPT,
    build_user_prompt,
    parse_action,
)
from app.sharp.react.stopping_criteria import (
    StoppingCriteria,
    StoppingDecision,
)
from app.sharp.react.trajectory_recorder import TrajectoryRecorder
from app.sharp.schema.domain_schema import Triple
from app.sharp.schema.strategic_planner import (
    StrategicPlanner,
    VerificationStrategy,
)
from app.sharp.tools.base import ToolCall
from app.sharp.tools.reranker import EvidenceReranker
from app.sharp.tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


# 验证结果


@dataclass
class VerificationResult:
    """单次三元组验证结果。

    Attributes
    ----------
    triple : Triple
        被验证的三元组
    verdict : str
        判定结果："supported" / "refuted" / "uncertain"
    confidence : float
        最终聚合置信度 [0, 1]
    reasoning : str
        LLM 给出的推理依据（自然语言）
    trajectory : list[dict]
        完整 ReAct 轨迹（每步序列化）
    evidence_chain : list[dict]
        证据链（按加权分数降序）
    strategy : dict
        本次验证使用的策略
    stopping_decision : dict
        终止原因
    verification_id : str
        本次验证的唯一 ID（用于 M4 Memory 存储）
    elapsed_ms : float
        总耗时
    steps_taken : int
        实际执行步数
    """

    triple: Triple
    verdict: str = "uncertain"
    confidence: float = 0.0
    reasoning: str = ""
    trajectory: list[dict] = field(default_factory=list)
    evidence_chain: list[dict] = field(default_factory=list)
    strategy: dict = field(default_factory=dict)
    stopping_decision: dict = field(default_factory=dict)
    verification_id: str = ""
    elapsed_ms: float = 0.0
    steps_taken: int = 0

    def to_dict(self) -> dict[str, Any]:
        """完整序列化为 dict（可被 JSON 序列化）。"""
        return {
            "verification_id": self.verification_id,
            "triple": self.triple.short_repr(),
            "triple_detail": {
                "head_type": self.triple.head_type.value,
                "head_id": self.triple.head_id,
                "relation": self.triple.relation.value,
                "tail_type": self.triple.tail_type.value,
                "tail_id": self.triple.tail_id,
                "properties": self.triple.relation_properties,
            },
            "verdict": self.verdict,
            "confidence": round(self.confidence, 4),
            "reasoning": self.reasoning,
            "evidence_chain": self.evidence_chain,
            "strategy": self.strategy,
            "stopping_decision": self.stopping_decision,
            "steps_taken": self.steps_taken,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "trajectory": self.trajectory,
        }


# ReAct 主循环


class ReActLoop:
    """ReAct 增强循环主协调器。

    依赖注入
    --------
    - `llm_router` : `LLMRouter` 实例（必须支持 `chat_completion`）
    - `tool_registry` : `ToolRegistry` 实例（已注册可用工具）
    - `strategic_planner` : `StrategicPlanner` 实例
    - `evidence_reranker` : `EvidenceReranker` 实例
    - `stopping_criteria` : `StoppingCriteria` 实例（可选，使用默认）

    主入口
    -------
    `verify(triple) -> VerificationResult`
    """

    def __init__(
        self,
        llm_router,
        tool_registry: ToolRegistry,
        strategic_planner: StrategicPlanner,
        evidence_reranker: EvidenceReranker | None = None,
        stopping_criteria: StoppingCriteria | None = None,
        max_react_steps: int = 8,
        llm_max_tokens: int = 768,
        llm_temperature: float = 0.3,
        memory_augmentor: Any | None = None,
    ) -> None:
        """初始化 ReAct 循环。

        Args:
            llm_router: LLM 路由器实例
            tool_registry: 工具注册中心
            strategic_planner: 战略规划器
            evidence_reranker: 证据重排序器（None 时新建默认）
            stopping_criteria: 终止条件判定器（None 时新建默认）
            max_react_steps: 默认最大步数（被 strategy.max_steps 覆盖）
            llm_max_tokens: LLM 单次生成最大 token
            llm_temperature: LLM 生成温度
            memory_augmentor: M4 Memory 增强器（None 表示不使用）
        """
        self.llm_router = llm_router
        self.tool_registry = tool_registry
        self.planner = strategic_planner
        self.reranker = evidence_reranker or EvidenceReranker()
        self.stopper = stopping_criteria or StoppingCriteria()
        self.default_max_steps = max_react_steps
        self.llm_max_tokens = llm_max_tokens
        self.llm_temperature = llm_temperature
        self.memory_augmentor = memory_augmentor

    # 公共入口

    async def verify(
        self,
        triple: Triple,
        max_steps_override: int | None = None,
    ) -> VerificationResult:
        """验证单个三元组。

        Args:
            triple: 待验证的三元组
            max_steps_override: 单次验证的 max_steps 覆盖值（可选）。
                由 ``SharpService._run_verify_with_max_steps`` 传入，用于
                压测场景下显式控制循环上限。None 时使用
                ``min(strategy.max_steps, self.default_max_steps)``。

        Returns:
            VerificationResult
        """
        verification_id = f"ver_{uuid.uuid4().hex[:12]}"
        start_time = time.perf_counter()

        # 1. 生成验证策略
        strategy = self.planner.plan(triple)

        # 计算 max_steps：覆盖值优先，否则取 strategy 与 default 的较小值
        # 设计：max_steps_override 来自显式调用方（如压测），应完全决定循环上限，
        # 不被 strategy.max_steps（planner 默认 8）截断。
        if max_steps_override is not None:
            max_steps = int(max_steps_override)
        else:
            max_steps = min(strategy.max_steps, self.default_max_steps) or strategy.max_steps

        logger.info(
            "SHARP verify start | id=%s | triple=%s | max_steps=%d (strategy=%d, default=%d, override=%s) | tools=%s",
            verification_id,
            triple.short_repr(),
            max_steps,
            strategy.max_steps,
            self.default_max_steps,
            max_steps_override,
            strategy.tool_sequence,
        )

        # 2. 准备循环依赖
        recorder = TrajectoryRecorder(max_observation_length=500)
        tool_results: list = []  # 收集所有 ToolResult 用于证据聚合
        consecutive_errors = 0
        stopping_decision: StoppingDecision | None = None

        # 3. M4 Memory 增强：注入历史相似案例到 prompt
        memory_context = ""
        if self.memory_augmentor is not None:
            try:
                memory_context = await self._augment_with_memory(triple)
            except Exception as e:
                logger.warning("Memory augment failed: %s", e)
                memory_context = ""

        # 4. 构造工具提示文本
        tool_prompt = self.tool_registry.to_prompt_text(strategy.tool_sequence)

        # 5. 主循环（max_steps 已在第 1 步计算完成，支持 override）
        step_idx = 0
        while step_idx < max_steps:
            step_idx += 1

            # 5.1 构造 prompt
            trajectory_text = recorder.to_prompt_text(last_n=3)
            user_prompt = build_user_prompt(
                triple=triple,
                strategy=strategy,
                tool_prompt=tool_prompt,
                trajectory_text=trajectory_text,
                memory_context=memory_context,
            )

            # 5.2 调用 LLM
            llm_response = await self._call_llm_with_retry(user_prompt, verification_id, step_idx)

            if llm_response is None:
                # LLM 连续失败，记录错误步骤
                recorder.record_step(
                    thought=f"LLM 调用失败（step {step_idx}）",
                    tool_call=None,
                    tool_result=None,
                    elapsed_ms=0.0,
                )
                consecutive_errors += 1
                decision = self.stopper.check(
                    step_idx=step_idx,
                    strategy=strategy,
                    recorder=recorder,
                    llm_action=None,
                    consecutive_errors=consecutive_errors,
                    max_steps_override=max_steps_override,
                )
                if decision.should_stop:
                    stopping_decision = decision
                    break
                continue

            # 5.3 解析 LLM 输出
            action = parse_action(llm_response["content"])
            if action is None:
                consecutive_errors += 1
                recorder.record_step(
                    thought=f"LLM 输出解析失败（step {step_idx}）: {llm_response['content'][:200]}",
                    tool_call=None,
                    tool_result=None,
                    elapsed_ms=0.0,
                )
                decision = self.stopper.check(
                    step_idx=step_idx,
                    strategy=strategy,
                    recorder=recorder,
                    llm_action=None,
                    consecutive_errors=consecutive_errors,
                    max_steps_override=max_steps_override,
                )
                if decision.should_stop:
                    stopping_decision = decision
                    break
                continue

            # 解析成功，重置错误计数
            consecutive_errors = 0

            # 5.4 处理 Finish
            if action["type"] == "finish":
                recorder.current_confidence = float(action.get("confidence", 0.0))
                recorder.record_step(
                    thought=action.get("thought", ""),
                    tool_call=None,
                    tool_result=None,
                    elapsed_ms=0.0,
                    finish_action=action,
                )
                stopping_decision = StoppingDecision(
                    should_stop=True,
                    reason=f"LLM 主动 Finish: verdict={action.get('verdict')}",
                    trigger="llm_finish",
                )
                break

            # 5.5 处理 Action
            tool_name = action.get("action", "")
            action_input = action.get("action_input", {})
            thought = action.get("thought", "")

            tool_result = await self._execute_tool(tool_name, action_input)

            # 收集工具结果
            if tool_result is not None:
                tool_results.append(tool_result)
                # 更新置信度（来自 LLM reason 工具的结果）
                if tool_name == "llm.reason" and tool_result.success:
                    output = tool_result.output
                    if isinstance(output, dict) and "confidence" in output:
                        try:
                            recorder.current_confidence = float(output["confidence"])
                        except (TypeError, ValueError) as conf_err:
                            # confidence 字段类型不符（如非数值字符串）时跳过更新，
                            # 记录便于排查：置信度聚合可能因此缺失该步贡献
                            logger.debug("Invalid confidence value %r: %s", output.get("confidence"), conf_err)

                # 实时聚合证据更新置信度（修复：KG 工具也需要参与置信度计算，
                # 否则 confidence_delta 始终为 0，导致 evidence_converged 误触发）
                # 设计：每步工具调用后，基于已收集的所有工具结果计算实时聚合置信度，
                # 让收敛检测基于真实置信度变化，避免 LLM 未参与就提前终止。
                try:
                    evidences = self.reranker.collect_from_tool_results(tool_results, strategy.triple)
                    if evidences:
                        ranked = self.reranker.rerank(evidences, top_k=10)
                        aggregated = self.reranker.aggregate_confidence(
                            ranked,
                            require_external=strategy.require_external_evidence,
                        )
                        live_conf = float(aggregated.get("confidence", 0.0))
                        # 仅当 LLM 未明确给出 confidence 时用聚合值
                        # （LLM confidence 优先，避免被聚合值覆盖）
                        if tool_name != "llm.reason":
                            recorder.current_confidence = live_conf
                except Exception as e:
                    logger.debug("Live aggregation failed at step %d: %s", step_idx, e)

            recorder.record_step(
                thought=thought,
                tool_call=ToolCall(
                    tool_name=tool_name,
                    arguments=action_input if isinstance(action_input, dict) else {},
                    thought=thought,
                ),
                tool_result=tool_result,
                elapsed_ms=tool_result.elapsed_ms if tool_result else 0.0,
            )

            # 5.6 检查终止条件
            decision = self.stopper.check(
                step_idx=step_idx,
                strategy=strategy,
                recorder=recorder,
                llm_action=action,
                consecutive_errors=consecutive_errors,
                max_steps_override=max_steps_override,
            )
            if decision.should_stop:
                stopping_decision = decision
                break

        # 6. 循环结束，兜底终止原因
        if stopping_decision is None:
            stopping_decision = StoppingDecision(
                should_stop=True,
                reason=f"达到最大步数 {max_steps}",
                trigger="step_limit",
            )

        # 7. 证据聚合
        evidence_chain, aggregated_confidence = self._aggregate_evidence(tool_results, strategy, recorder)

        # 8. 推断最终 verdict
        verdict, reasoning = self._derive_final_verdict(stopping_decision, recorder, aggregated_confidence, strategy)

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        result = VerificationResult(
            triple=triple,
            verdict=verdict,
            confidence=round(aggregated_confidence, 4),
            reasoning=reasoning,
            trajectory=recorder.to_dict().get("steps", []),
            evidence_chain=evidence_chain,
            strategy=strategy.to_dict(),
            stopping_decision={
                "trigger": stopping_decision.trigger,
                "reason": stopping_decision.reason,
            },
            verification_id=verification_id,
            elapsed_ms=elapsed_ms,
            steps_taken=step_idx,
        )

        logger.info(
            "SHARP verify done | id=%s | verdict=%s | confidence=%.3f | steps=%d | elapsed=%.0fms | trigger=%s",
            verification_id,
            verdict,
            result.confidence,
            step_idx,
            elapsed_ms,
            stopping_decision.trigger,
        )

        return result

    # 内部方法

    async def _call_llm_with_retry(
        self, user_prompt: str, verification_id: str, step_idx: int
    ) -> dict[str, Any] | None:
        """调用 LLM，失败重试 1 次。"""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        for attempt in range(2):
            try:
                response = await self.llm_router.chat_completion(
                    messages=messages,
                    max_tokens=self.llm_max_tokens,
                    temperature=self.llm_temperature,
                )
                if isinstance(response, dict) and response.get("content"):
                    return response
                # 空响应视为失败
                logger.warning(
                    "LLM empty response | id=%s | step=%d | attempt=%d",
                    verification_id,
                    step_idx,
                    attempt + 1,
                )
            except Exception as e:
                logger.warning(
                    "LLM call failed | id=%s | step=%d | attempt=%d | err=%s",
                    verification_id,
                    step_idx,
                    attempt + 1,
                    e,
                )
                if attempt == 1:
                    return None
        return None

    async def _execute_tool(self, tool_name: str, action_input: Any) -> Any | None:
        """执行工具调用。

        Returns:
            ToolResult 或 None（工具不存在时）
        """
        if not tool_name:
            return None

        tool = self.tool_registry.get(tool_name)
        if tool is None:
            logger.warning("Tool not found: %s", tool_name)
            from app.sharp.tools.base import ToolResult

            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"工具不存在: {tool_name}",
                elapsed_ms=0.0,
            )

        # 规范化 arguments
        if not isinstance(action_input, dict):
            action_input = {"input": action_input} if action_input else {}

        call = ToolCall(tool_name=tool_name, arguments=action_input)
        return await tool.execute(call)

    async def _augment_with_memory(self, triple: Triple) -> str:
        """M4 Memory 增强：从历史验证中检索相似案例。"""
        if self.memory_augmentor is None:
            return ""
        try:
            result = await self.memory_augmentor.retrieve_similar(triple)
            if not result:
                return ""
            return self.memory_augmentor.format_memory_context(result)
        except Exception as e:
            logger.warning("Memory augment failed: %s", e)
            return ""

    def _aggregate_evidence(
        self,
        tool_results: list,
        strategy: VerificationStrategy,
        recorder: TrajectoryRecorder,
    ) -> tuple[list[dict], float]:
        """证据聚合：收集 → 重排 → 计算置信度。

        Returns:
            (evidence_chain, aggregated_confidence)
        """
        if not tool_results:
            return [], recorder.current_confidence

        # 收集证据
        evidences = self.reranker.collect_from_tool_results(tool_results, strategy.triple)
        if not evidences:
            return [], recorder.current_confidence

        # 重排序
        ranked = self.reranker.rerank(evidences, top_k=10)

        # 聚合置信度（返回 dict，提取 confidence 字段）
        aggregated = self.reranker.aggregate_confidence(ranked, require_external=strategy.require_external_evidence)
        confidence = float(aggregated.get("confidence", 0.0)) if isinstance(aggregated, dict) else float(aggregated)

        # 序列化证据链
        evidence_chain = [e.to_dict() for e in ranked]
        return evidence_chain, confidence

    def _derive_final_verdict(
        self,
        stopping_decision: StoppingDecision,
        recorder: TrajectoryRecorder,
        aggregated_confidence: float,
        strategy: VerificationStrategy,
    ) -> tuple[str, str]:
        """根据终止条件与置信度推断最终 verdict。

        Returns:
            (verdict, reasoning)
        """
        # 1. 若 LLM 主动 Finish，从最后一步提取 verdict
        if stopping_decision.trigger == "llm_finish":
            steps = recorder.to_dict().get("steps", [])
            for step in reversed(steps):
                finish_action = step.get("finish_action")
                if finish_action:
                    verdict = finish_action.get("verdict", "uncertain")
                    reasoning = finish_action.get("reasoning", "")
                    # 与聚合置信度取加权平均
                    llm_conf = float(finish_action.get("confidence", 0.0))
                    final_conf = 0.6 * aggregated_confidence + 0.4 * llm_conf
                    if final_conf >= strategy.confidence_threshold:
                        verdict = "supported" if verdict != "refuted" else "refuted"
                    return verdict, reasoning

        # 2. 基于聚合置信度判定
        if aggregated_confidence >= strategy.confidence_threshold:
            return "supported", f"聚合置信度 {aggregated_confidence:.3f} >= 阈值 {strategy.confidence_threshold}"
        elif aggregated_confidence < 0.3:
            return "refuted", f"聚合置信度 {aggregated_confidence:.3f} 过低，判定为反驳"
        else:
            return "uncertain", f"聚合置信度 {aggregated_confidence:.3f} 不足以判定，需进一步验证"


__all__ = ["ReActLoop", "VerificationResult"]
