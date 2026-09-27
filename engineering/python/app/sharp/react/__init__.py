"""SHARP ReAct 增强循环（M3）。

对应论文 §4.5 "ReAct Loop with Schema-Aware Enhancement"。

模块结构
--------
- `prompt_templates`    ReAct prompt 模板与解析器
- `trajectory_recorder` 推理轨迹记录器（thought-action-observation）
- `stopping_criteria`   终止条件判定
- `react_loop`          ReAct 主循环

设计原则
--------
- **可观测**：每步 thought/action/observation 完整记录，便于证据链追溯
- **可中断**：支持最大步数、置信度阈值、证据收敛三重终止条件
- **容错**：LLM 输出解析失败时不崩溃，回退到默认工具序列
- **消融支持**：`no_react` 模式下退化为单步工具调用
"""

from __future__ import annotations

from app.sharp.react.prompt_templates import (
    SYSTEM_PROMPT,
    build_user_prompt,
    parse_action,
    format_trajectory_for_prompt,
)
from app.sharp.react.trajectory_recorder import (
    TrajectoryRecorder,
    TrajectoryStep,
)
from app.sharp.react.stopping_criteria import (
    StoppingCriteria,
    StoppingDecision,
)
from app.sharp.react.react_loop import (
    ReActLoop,
    VerificationResult,
)

__all__ = [
    # prompt
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "parse_action",
    "format_trajectory_for_prompt",
    # trajectory
    "TrajectoryRecorder",
    "TrajectoryStep",
    # stopping
    "StoppingCriteria",
    "StoppingDecision",
    # loop
    "ReActLoop",
    "VerificationResult",
]
