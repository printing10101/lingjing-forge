"""将有来源声明的等间隔历史记录转换为融合模型输入，不插值或补零。"""

from dataclasses import dataclass
import math
from typing import Any

import numpy as np

from app.contracts.world_model import ActionField, InvalidStateError
from app.plugins.world_model.dynamics_state_bridge import REQUIRED_FIELDS

MAX_HISTORY_STEPS = 256


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidStateError(f"历史记录 {field} 必须为有限数。")
    return float(value)


@dataclass
class PreparedFusionHistory:
    states: list[dict[str, Any]]
    actions: np.ndarray
    initial_state: np.ndarray | None
    summary: dict[str, Any]


def prepare_fusion_history(
    payload: dict[str, Any], geometry: dict[str, Any], *,
    training_steps: list[int], feedback_fields: list[str],
) -> PreparedFusionHistory:
    """时间单位为秒；记录由旧到新，每条动作对应同条状态的训练输入。"""
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise InvalidStateError("历史文件必须使用 schema_version=1。")
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip() or len(source) > 512:
        raise InvalidStateError("历史文件须声明非空来源 source（最多512字）。")
    period = _number(payload.get("sample_period_seconds"), "sample_period_seconds")
    if period <= 0:
        raise InvalidStateError("历史采样间隔必须大于零。")
    samples = payload.get("samples")
    if not isinstance(samples, list) or not 2 <= len(samples) <= MAX_HISTORY_STEPS:
        raise InvalidStateError(f"历史记录必须含2至{MAX_HISTORY_STEPS}个时刻。")
    if training_steps and len(samples) not in training_steps:
        raise InvalidStateError(f"历史长度{len(samples)}与权重记录的训练长度{training_steps}不匹配；不自动截断或填充。")
    states, actions = [], []
    times = []
    latest_state: dict[str, Any] = {}
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise InvalidStateError(f"历史第{index + 1}条必须是对象。")
        timestamp = _number(sample.get("time_seconds"), f"samples[{index}].time_seconds")
        if times and (timestamp <= times[-1] or not math.isclose(timestamp - times[-1], period, rel_tol=1e-5, abs_tol=period * 1e-6)):
            raise InvalidStateError("历史时间须由旧到新严格递增，且间隔须与声明值一致；不自动重采样。")
        times.append(timestamp)
        state, action = sample.get("current_state"), sample.get("action")
        if not isinstance(state, dict) or not isinstance(action, dict):
            raise InvalidStateError(f"历史第{index + 1}条须含 current_state 与 action。")
        dynamics = {name: _number(state.get(name), f"samples[{index}].{name}") for name in REQUIRED_FIELDS}
        if any(value < 0 for name, value in dynamics.items() if name != "temperature"):
            raise InvalidStateError("历史转速、进给、切深、磨损和振动必须非负。")
        actions.append([_number(action.get(name), f"samples[{index}].{name}") for name in ActionField.all()])
        states.append({"geometry": geometry, "dynamics": dynamics})
        latest_state = dict(state)
    initial_state = None
    if feedback_fields:
        # 温度归一化口径与手动状态一致，从实际摄氏温度派生。
        latest_state["temperature_over_100c"] = latest_state["temperature"] / 100
        feedback = [_number(latest_state.get(name), f"末条反馈.{name}") for name in feedback_fields]
        if any(value < 0 for name, value in zip(feedback_fields, feedback) if name != "temperature_over_100c"):
            raise InvalidStateError("历史末条状态反馈除温度外不能为负数。")
        if any(not 0 <= latest_state[name] <= 1 for name in ("confidence", "chatter_probability")):
            raise InvalidStateError("历史末条概率和可信度必须在0到1之间。")
        initial_state = np.asarray(feedback, dtype=np.float32)
    action_array = np.asarray(actions, dtype=np.float32)
    if not np.isfinite(action_array).all() or (initial_state is not None and not np.isfinite(initial_state).all()):
        raise InvalidStateError("历史数值超出模型浮点范围。")
    return PreparedFusionHistory(states, action_array, initial_state, {
        "source": source.strip(), "source_verification": "user_declared",
        "history_steps": len(samples), "sample_period_seconds": period,
        "time_start_seconds": times[0], "time_end_seconds": times[-1],
        "training_window_match": "matched" if training_steps else "unknown",
        "training_sample_period_match": "unknown",
    })
