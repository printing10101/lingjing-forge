"""SHARP 终止条件判定（M3.3）。

ReAct 循环的多重终止条件：
1. **步数上限**：step_idx >= max_steps
2. **置信度达标**：current_confidence >= confidence_threshold
3. **证据收敛**：连续 N 步置信度变化 < 0.05
4. **LLM 主动终止**：解析到 finish action
5. **错误熔断**：连续 3 次工具调用失败
6. **工具耗尽**：策略工具序列已全部调用

设计原则
--------
- **可解释**：每次终止返回 reason，便于复盘
- **优先级**：finish > 错误熔断 > 置信度 > 收敛 > 步数 > 工具耗尽
- **配置驱动**：所有阈值来自 VerificationStrategy
"""

from __future__ import annotations

from dataclasses import dataclass


from app.sharp.react.trajectory_recorder import TrajectoryRecorder
from app.sharp.schema.strategic_planner import VerificationStrategy


# 终止决策


@dataclass
class StoppingDecision:
    """终止决策结果。

    Attributes
    ----------
    should_stop : bool
        是否应终止
    reason : str
        终止原因（用于日志与证据链）
    trigger : str
        触发器类型：step_limit / confidence_reached / evidence_converged /
        llm_finish / error_circuit / tools_exhausted
    """

    should_stop: bool
    reason: str = ""
    trigger: str = ""

    def to_dict(self) -> dict:
        return {
            "should_stop": self.should_stop,
            "reason": self.reason,
            "trigger": self.trigger,
        }


# 终止条件判定器


class StoppingCriteria:
    """ReAct 循环终止条件判定器。

    使用方式：

        criteria = StoppingCriteria()
        decision = criteria.check(
            step_idx=3,
            strategy=strategy,
            recorder=recorder,
            llm_action=last_action,
            consecutive_errors=0,
        )
        if decision.should_stop:
            break
    """

    def __init__(
        self,
        convergence_threshold: float = 0.05,
        max_consecutive_errors: int = 3,
    ) -> None:
        """Args:
        convergence_threshold: 收敛阈值，连续 N 步置信度变化 < 该值视为收敛
        max_consecutive_errors: 连续错误熔断阈值
        """
        self.convergence_threshold = convergence_threshold
        self.max_consecutive_errors = max_consecutive_errors

    def check(
        self,
        step_idx: int,
        strategy: VerificationStrategy,
        recorder: TrajectoryRecorder,
        llm_action: dict | None = None,
        consecutive_errors: int = 0,
        max_steps_override: int | None = None,
    ) -> StoppingDecision:
        """综合检查所有终止条件。

        Args:
            step_idx: 当前步数（从 1 开始）
            strategy: 验证策略
            recorder: 轨迹记录器
            llm_action: 上一步 LLM 解析出的 action（含 type 字段）
            consecutive_errors: 连续错误次数
            max_steps_override: 单次验证的 max_steps 覆盖值（可选）。
                由 ``ReActLoop.verify`` 传入，用于让 step_limit 判定尊重
                调用方显式指定的上限，而非 strategy.max_steps（planner 默认 8）。

        Returns:
            StoppingDecision
        """
        # 1. LLM 主动终止（最高优先级）
        if llm_action and llm_action.get("type") == "finish":
            return StoppingDecision(
                should_stop=True,
                reason=f"LLM 主动终止：verdict={llm_action.get('verdict')}, confidence={llm_action.get('confidence')}",
                trigger="llm_finish",
            )

        # 2. 错误熔断
        if consecutive_errors >= self.max_consecutive_errors:
            return StoppingDecision(
                should_stop=True,
                reason=f"连续 {consecutive_errors} 次工具调用失败，触发错误熔断",
                trigger="error_circuit",
            )

        # 3. 置信度达标
        current_conf = recorder.current_confidence
        if current_conf >= strategy.confidence_threshold:
            return StoppingDecision(
                should_stop=True,
                reason=f"置信度 {current_conf:.4f} >= 阈值 {strategy.confidence_threshold}",
                trigger="confidence_reached",
            )

        # 4. 证据收敛
        if self._is_evidence_converged(recorder, strategy.evidence_convergence_window):
            return StoppingDecision(
                should_stop=True,
                reason=f"连续 {strategy.evidence_convergence_window} 步置信度变化 "
                f"< {self.convergence_threshold}，证据已收敛",
                trigger="evidence_converged",
            )

        # 5. 步数上限（尊重 max_steps_override，用于压测场景显式控制）
        effective_max_steps = max_steps_override if max_steps_override is not None else strategy.max_steps
        if step_idx >= effective_max_steps:
            return StoppingDecision(
                should_stop=True,
                reason=f"达到最大步数 {effective_max_steps}",
                trigger="step_limit",
            )

        # 6. 工具耗尽（策略工具序列已全部调用）
        if self._are_tools_exhausted(recorder, strategy):
            return StoppingDecision(
                should_stop=True,
                reason="策略工具序列已全部调用完成",
                trigger="tools_exhausted",
            )

        return StoppingDecision(should_stop=False)

    # 内部判定

    def _is_evidence_converged(
        self,
        recorder: TrajectoryRecorder,
        window: int,
    ) -> bool:
        """检测证据是否收敛（连续 window 步置信度变化 < threshold）。"""
        if window <= 0 or recorder.step_count < window:
            return False
        recent_steps = recorder.steps[-window:]
        deltas = [abs(s.confidence_delta) for s in recent_steps]
        return all(d < self.convergence_threshold for d in deltas)

    def _are_tools_exhausted(
        self,
        recorder: TrajectoryRecorder,
        strategy: VerificationStrategy,
    ) -> bool:
        """检测策略工具序列是否已全部调用。"""
        if not strategy.tool_sequence:
            return False
        called_tools = {s.tool_name for s in recorder.steps if s.tool_name != "finish"}
        # 至少调用过策略序列中的所有工具
        return set(strategy.tool_sequence).issubset(called_tools)


__all__ = ["StoppingCriteria", "StoppingDecision"]
