"""SHARP 轨迹记录器（M3.2）。

记录 ReAct 循环每一步的 thought / action / observation，用于：
1. 后续步骤的 prompt 上下文
2. 最终证据链的可观测性
3. M4 Memory-Augmented 的相似轨迹检索源

设计原则
--------
- **结构化**：每步保存为 `TrajectoryStep` dataclass，可序列化为 dict/JSON
- **可截断**：长 observation 自动截断，避免 prompt 爆炸
- **可追溯**：记录工具名、参数、耗时、成功状态，便于复盘
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.sharp.tools.base import ToolCall, ToolResult


# 轨迹步结构


@dataclass
class TrajectoryStep:
    """单步推理轨迹。

    Attributes
    ----------
    step_idx : int
        步骤序号（从 1 开始）
    thought : str
        LLM 生成的思考
    tool_name : str
        调用的工具名（"finish" 表示终止）
    tool_args : dict
        工具参数
    observation : Any
        工具返回结果（已截断）
    success : bool
        工具调用是否成功
    elapsed_ms : float
        该步总耗时（含 LLM 推理 + 工具执行）
    timestamp : float
        时间戳
    confidence_delta : float
        该步引起的置信度变化（用于收敛检测）
    """

    step_idx: int
    thought: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    observation: Any = None
    success: bool = True
    elapsed_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    confidence_delta: float = 0.0
    finish_action: dict[str, Any] | None = None  # LLM Finish 时的完整动作

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（observation 自动转为可序列化形式）。"""
        return {
            "step_idx": self.step_idx,
            "thought": self.thought,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "observation": self._serialize_observation(),
            "success": self.success,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "timestamp": self.timestamp,
            "confidence_delta": round(self.confidence_delta, 4),
            "finish_action": self.finish_action,
        }

    def _serialize_observation(self) -> Any:
        """将 observation 序列化为可 JSON 化的形式。"""
        if self.observation is None:
            return None
        if isinstance(self.observation, (str, int, float, bool)):
            return self.observation
        if isinstance(self.observation, dict):
            # 截断过长的 content 字段
            # 值类型混合（str / list / 原样值），显式 Any 注解
            result: dict[str, Any] = {}
            for k, v in self.observation.items():
                if isinstance(v, str) and len(v) > 500:
                    result[k] = v[:500] + "...（截断）"
                elif isinstance(v, list) and len(v) > 20:
                    result[k] = v[:20] + ["...（截断）"]
                else:
                    result[k] = v
            return result
        if isinstance(self.observation, list):
            return self.observation[:20] if len(self.observation) > 20 else self.observation
        return str(self.observation)[:500]


# 轨迹记录器


class TrajectoryRecorder:
    """ReAct 推理轨迹记录器。

    使用方式：

        recorder = TrajectoryRecorder()
        recorder.record_step(
            thought="需要先查询 KG 是否存在该关系",
            tool_call=ToolCall(tool_name="kg.query_relation", arguments={...}),
            tool_result=tool_result,
            elapsed_ms=120.5,
        )
        # 后续步骤读取历史
        steps = recorder.steps
    """

    def __init__(self, max_observation_length: int = 500) -> None:
        self._steps: list[TrajectoryStep] = []
        self._max_obs_len = max_observation_length
        self._current_confidence: float = 0.0
        # 待写入下一条 step 的置信度（在 record_step 之前通过 setter 设置）
        # 设计：修复 confidence_delta 计算时序缺陷——原实现中最后一步的 delta
        # 始终为 0（因为没有下一步来更新它），导致 evidence_converged 误触发。
        # 改为：setter 暂存 _pending_confidence，record_step 创建 step 时
        # 计算 delta = _pending_confidence - _current_confidence。
        self._pending_confidence: float | None = None

    @property
    def steps(self) -> list[TrajectoryStep]:
        return self._steps

    @property
    def step_count(self) -> int:
        return len(self._steps)

    @property
    def current_confidence(self) -> float:
        return self._current_confidence

    @current_confidence.setter
    def current_confidence(self, value: float) -> None:
        """更新当前置信度（暂存到 pending，由 record_step 计算 delta）。

        时序契约：
        - 必须在 record_step 之前调用：setter 暂存最新置信度到 _pending，
          record_step 创建 step 时计算 delta = pending - 历史累计置信度。
        - react_loop.py 中所有 setter 调用均满足此契约（在 record_step 之前）。
        - 若需修正已记录步骤的置信度，请直接修改 TrajectoryStep.confidence_delta。
        """
        new_conf = max(0.0, min(1.0, value))
        self._pending_confidence = new_conf
        self._current_confidence = new_conf

    # 记录

    def record_step(
        self,
        thought: str,
        tool_call: ToolCall | None,
        tool_result: ToolResult | None,
        elapsed_ms: float = 0.0,
        finish_action: dict[str, Any] | None = None,
    ) -> TrajectoryStep:
        """记录一步推理。

        Args:
            thought: LLM 生成的思考
            tool_call: 工具调用（None 表示 finish 或错误步骤）
            tool_result: 工具结果（None 表示无调用）
            elapsed_ms: 该步总耗时
            finish_action: LLM 主动 Finish 时的完整动作 dict

        Returns:
            创建的 TrajectoryStep
        """
        step_idx = len(self._steps) + 1
        tool_name = tool_call.tool_name if tool_call else "finish"
        tool_args = tool_call.arguments if tool_call else {}
        observation = self._truncate_observation(tool_result.output if tool_result else None)
        success = tool_result.success if tool_result else True

        # 计算本步的 confidence_delta（修复时序缺陷）
        # 设计：若 setter 在 record_step 之前调用，_pending_confidence 已暂存
        # 当前最新置信度。基准 = 历史所有 step 的 confidence_delta 之和
        # （即上一步结束时的累计置信度）。delta = pending - 基准。
        if self._pending_confidence is not None:
            prev_conf = sum(s.confidence_delta for s in self._steps)
            delta = self._pending_confidence - prev_conf
            self._pending_confidence = None  # 消费 pending 值
        else:
            delta = 0.0

        step = TrajectoryStep(
            step_idx=step_idx,
            thought=thought,
            tool_name=tool_name,
            tool_args=tool_args,
            observation=observation,
            success=success,
            elapsed_ms=elapsed_ms,
            confidence_delta=delta,
            finish_action=finish_action,
        )
        self._steps.append(step)
        return step

    def _truncate_observation(self, observation: Any) -> Any:
        """截断过长的 observation。"""
        if observation is None:
            return None
        if isinstance(observation, str):
            return observation[: self._max_obs_len] + ("...（截断）" if len(observation) > self._max_obs_len else "")
        return observation  # dict/list 在 to_dict 时再截断

    # 序列化

    def to_dict(self) -> dict[str, Any]:
        """完整轨迹序列化为 dict。"""
        return {
            "step_count": self.step_count,
            "final_confidence": self._current_confidence,
            "steps": [s.to_dict() for s in self._steps],
        }

    def to_prompt_text(self, last_n: int | None = None) -> str:
        """生成用于后续 prompt 的历史轨迹文本。

        Args:
            last_n: 仅取最后 N 步（None 表示全部）
        """
        from app.sharp.react.prompt_templates import format_trajectory_for_prompt

        steps = self._steps[-last_n:] if last_n else self._steps
        return format_trajectory_for_prompt(steps)

    # 统计

    def get_tool_usage_stats(self) -> dict[str, int]:
        """统计每个工具的调用次数。"""
        stats: dict[str, int] = {}
        for step in self._steps:
            stats[step.tool_name] = stats.get(step.tool_name, 0) + 1
        return stats

    def get_total_elapsed_ms(self) -> float:
        """总耗时（毫秒）。"""
        return sum(s.elapsed_ms for s in self._steps)


__all__ = ["TrajectoryRecorder", "TrajectoryStep"]
