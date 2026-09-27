"""SHARP ReAct Prompt 模板与解析器（M3.1）。

定义 ReAct 循环的 prompt 结构：

1. System Prompt：定义 KG 验证智能体角色与 ReAct 范式
2. User Prompt：注入三元组、策略、工具集、历史轨迹
3. 解析器：从 LLM 输出提取 thought / action_name / action_args

ReAct 范式
----------
每步 LLM 输出格式：

    Thought: <对当前观察的思考>
    Action: <工具名>
    Action Input: <JSON 参数>

或终止时：

    Thought: <最终思考>
    Finish: <最终结论 JSON>

解析器容错
----------
- 支持中英文标签（Thought/思考，Action/动作，Finish/终止）
- Action Input 支持 JSON 或简单键值对
- 缺失字段时返回 None，调用方决定回退策略
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.sharp.schema.domain_schema import Triple
from app.sharp.schema.strategic_planner import VerificationStrategy

logger = logging.getLogger(__name__)


# System Prompt

SYSTEM_PROMPT = """你是知识图谱三元组验证智能体（SHARP），采用 ReAct 范式逐步验证三元组。

每一步你需要输出：
1. Thought: 对当前观察的思考（分析已有证据、决定下一步）
2. Action: 选择一个工具调用（从可用工具列表中选）
3. Action Input: 工具参数（JSON 格式）

当证据充分时，输出：
1. Thought: 最终思考
2. Finish: {"verdict": "supported|refuted|uncertain", "confidence": 0.0-1.0, "reasoning": "..."}

规则：
- 优先调用 KG 工具查询已有关系，再调用文本工具收集外部证据
- 每步只调用一个工具
- Action Input 必须是合法 JSON
- 当 confidence >= 阈值 或 证据收敛时输出 Finish
- 严格遵守工具的参数 schema"""


# User Prompt 构造


def build_user_prompt(
    triple: Triple,
    strategy: VerificationStrategy,
    tool_prompt: str,
    trajectory_text: str = "",
    memory_context: str = "",
) -> str:
    """构造 user prompt。

    Args:
        triple: 待验证三元组
        strategy: 验证策略
        tool_prompt: 工具集描述文本（来自 ToolRegistry.to_prompt_text()）
        trajectory_text: 已有轨迹文本（来自 format_trajectory_for_prompt()）
        memory_context: M4 Memory 增强注入的历史相似案例文本（可选）

    Returns:
        完整的 user prompt 字符串
    """
    parts = [
        f"# 待验证三元组\n{triple.short_repr()}",
        f"\n# 验证策略\n{strategy.rationale}",
        f"- 最大步数: {strategy.max_steps}",
        f"- 置信度阈值: {strategy.confidence_threshold}",
        f"- 关注维度: {', '.join(strategy.focus_dimensions) if strategy.focus_dimensions else '默认'}",
        f"- 推荐工具序列: {' → '.join(strategy.tool_sequence) if strategy.tool_sequence else '由你决定'}",
        f"- 需外部证据: {'是' if strategy.require_external_evidence else '否'}",
        f"- 需交叉验证: {'是' if strategy.require_cross_validation else '否'}",
        f"\n# 可用工具\n{tool_prompt}",
    ]
    if memory_context:
        parts.append(f"\n# 历史相似案例（Memory-Augmented）\n{memory_context}")
    if trajectory_text:
        parts.append(f"\n# 已执行步骤\n{trajectory_text}")
    parts.append("\n# 当前任务\n请输出下一步 Thought / Action / Action Input，或 Thought / Finish。")
    return "\n".join(parts)


# LLM 输出解析

# 标签正则（支持中英文）
_THOUGHT_PATTERN = re.compile(
    r"(?:Thought|思考)\s*[:：]\s*(.*?)(?=(?:Action|动作|Finish|终止|$))",
    re.DOTALL | re.IGNORECASE,
)
_ACTION_PATTERN = re.compile(
    r"(?:Action|动作)\s*[:：]\s*(\S+)",
    re.IGNORECASE,
)
_ACTION_INPUT_PATTERN = re.compile(
    r"(?:Action Input|动作输入|ActionInput)\s*[:：]\s*(.*?)(?=(?:Thought|思考|Action|动作|Finish|终止|$))",
    re.DOTALL | re.IGNORECASE,
)
_FINISH_PATTERN = re.compile(
    r"(?:Finish|终止)\s*[:：]\s*(\{.*?\})\s*$",
    re.DOTALL | re.IGNORECASE,
)


def parse_action(
    response_text: str,
) -> dict[str, Any] | None:
    """解析 LLM 输出为结构化 action。

    Returns
    -------
    dict | None
        解析成功返回：
        - {"type": "action", "thought": str, "action": str, "action_input": dict}
        - {"type": "finish", "thought": str, "verdict": str, "confidence": float, "reasoning": str}
        解析失败返回 None
    """
    if not response_text:
        return None

    text = response_text.strip()

    # 1. 尝试解析 Finish
    finish_match = _FINISH_PATTERN.search(text)
    if finish_match:
        thought = _extract_thought(text)
        finish_json = _safe_json_loads(finish_match.group(1))
        if finish_json is None:
            return None
        return {
            "type": "finish",
            "thought": thought,
            "verdict": finish_json.get("verdict", "uncertain"),
            "confidence": float(finish_json.get("confidence", 0.0)),
            "reasoning": finish_json.get("reasoning", ""),
        }

    # 2. 解析 Action
    action_match = _ACTION_PATTERN.search(text)
    if not action_match:
        return None

    action_name = action_match.group(1).strip().rstrip(",.;")
    thought = _extract_thought(text)

    # 解析 Action Input
    action_input: dict[str, Any] = {}
    input_match = _ACTION_INPUT_PATTERN.search(text)
    if input_match:
        raw_input = input_match.group(1).strip()
        # 尝试 JSON 解析
        action_input = _safe_json_loads(raw_input) or {}
        if not action_input and raw_input:
            # 回退：尝试简单键值对解析
            action_input = _parse_key_value(raw_input)

    return {
        "type": "action",
        "thought": thought,
        "action": action_name,
        "action_input": action_input,
    }


def _extract_thought(text: str) -> str:
    """提取 Thought 字段内容。"""
    match = _THOUGHT_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return ""


def _safe_json_loads(text: str) -> dict | None:
    """容错 JSON 解析。"""
    if not text:
        return None
    text = text.strip()
    # 去除 markdown 代码块
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        # 尝试提取第一个 {...} 块
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                result = json.loads(match.group(0))
                return result if isinstance(result, dict) else None
            except json.JSONDecodeError as parse_err:
                # 两次解析均失败，记录便于排查 LLM 输出格式问题
                logger.debug("JSON block extraction also failed: %s", parse_err)
    return None


def _parse_key_value(text: str) -> dict[str, Any]:
    """简单键值对解析（回退方案）。

    支持：
        key1: value1
        key2: value2
    或
        key1=value1, key2=value2
    """
    result: dict[str, Any] = {}
    # 尝试逗号分隔的 key=value
    if "=" in text and ":" not in text:
        for pair in text.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k.strip()] = v.strip().strip("\"'")
    else:
        # 行分隔的 key: value
        for line in text.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                result[k.strip()] = v.strip().strip("\"'")
    return result


# 轨迹格式化


def format_trajectory_for_prompt(steps: list) -> str:
    """将历史轨迹格式化为 prompt 文本。

    Args:
        steps: `TrajectoryStep` 列表
    """
    if not steps:
        return ""
    lines = []
    for i, step in enumerate(steps, 1):
        lines.append(f"## 步骤 {i}")
        lines.append(f"Thought: {step.thought}")
        lines.append(f"Action: {step.tool_name}")
        lines.append(f"Action Input: {json.dumps(step.tool_args, ensure_ascii=False)}")
        # observation 截断避免 prompt 爆炸
        obs = step.observation
        if isinstance(observation_str := str(obs), str) and len(observation_str) > 500:
            observation_str = observation_str[:500] + "...（截断）"
        lines.append(f"Observation: {observation_str}")
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "parse_action",
    "format_trajectory_for_prompt",
]
