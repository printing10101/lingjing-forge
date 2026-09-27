"""可解释性纯函数算法集.

从原 ``explainability_service.py`` 拆分。本模块只包含纯函数（无 IO / 无 DB /
无状态），便于单元测试与并行化。

函数分类
--------
- **采集**（``collect_*``）：调用 predictor 捕获 intermediates
- **构建**（``build_*``）：从 intermediates 构造 Explanation 对象
- **分析**（``compute_*`` / ``scan_*``）：纯 numpy 数值计算
- **差异**（``compute_diff``）：两个 payload 的差异计算
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.contracts.explainability import (
    ExplanationValidationError,
    HiddenStateExplanation,
    ProjectionError,
    SamplingError,
)

logger = logging.getLogger(__name__)


# ── 隐状态 ──────────────────────────────────────────────────────────


def collect_hidden_state_intermediates(predictor: Any, *, max_frames: int) -> tuple[dict[str, Any], np.ndarray]:
    """调用 predictor 捕获隐状态序列并下采样.

    Returns
    -------
    tuple[dict, np.ndarray]
        (intermediates, hidden_array) — intermediates 字典与下采样后的
        ``[N, hidden_dim]`` 隐状态数组。
    """
    # 使用零输入触发模型前向（v1：隐状态来自模型初始状态 + 前向）
    # 真实场景应从 source_snapshot_id 加载历史输入，v1 简化为零向量探测
    try:
        # 构造探测输入：从模型 config 推断 input_dim
        model_config = getattr(predictor.model, "config", None)
        input_dim = getattr(model_config, "input_size", 8) if model_config else 8
        probe_input = np.zeros((1, input_dim), dtype=np.float32)
        result = predictor.predict_with_intermediates(probe_input, capture_hidden=True, capture_gates=False)
    except (ValueError, TypeError, RuntimeError) as exc:
        raise ProjectionError(f"predict_with_intermediates 调用失败: {exc}") from exc

    intermediates = result.model_info.get("intermediates", {}) or {}
    hidden_states_raw = intermediates.get("hidden_states", [])
    if not hidden_states_raw:
        raise ProjectionError(
            "模型未捕获到隐状态，无法生成隐状态投影解释"
            f"（capture_mode={intermediates.get('capture_mode', 'disabled')}）"
        )

    hidden_array = np.asarray(hidden_states_raw, dtype=np.float32)
    # 下采样到 max_frames
    if hidden_array.shape[0] > max_frames:
        indices = np.linspace(0, hidden_array.shape[0] - 1, max_frames, dtype=int)
        hidden_array = hidden_array[indices]
    return intermediates, hidden_array


def build_hidden_state_explanation(
    hidden_array: np.ndarray,
    projections: np.ndarray,
    *,
    projection_method: str,
    projection_dim: int,
    model_uri: str,
) -> HiddenStateExplanation:
    """从隐状态数组与投影坐标构造 HiddenStateExplanation.

    计算能量（L2 范数平方均值）与关键帧标记（v1 全部为 True）。
    """
    # 计算能量（L2 范数平方均值）
    energies = np.mean(hidden_array**2, axis=1).astype(float).tolist() if hidden_array.size > 0 else []
    # v1：所有帧标记为关键帧（不从 StreamingPredictor 获取关键帧标记）
    keyframe_flags = [True] * len(hidden_array)
    frame_ids = list(range(len(hidden_array)))

    return HiddenStateExplanation(
        frame_ids=frame_ids,
        projections=projections.astype(float).tolist(),
        energies=energies,
        keyframe_flags=keyframe_flags,
        projection_method=projection_method,
        projection_dim=projection_dim,
        hidden_dim=int(hidden_array.shape[1]),
        sample_count=len(hidden_array),
        model_uri=model_uri,
    )


# ── 门控动力学 ──────────────────────────────────────────────────────


def collect_gate_intermediates(predictor: Any) -> tuple[dict[str, Any], list, list]:
    """调用 predictor 捕获门控值与时间常数.

    Returns
    -------
    tuple[dict, list, list]
        (intermediates, gate_values_raw, time_constants_raw)。
    """
    try:
        model_config = getattr(predictor.model, "config", None)
        input_dim = getattr(model_config, "input_size", 8) if model_config else 8
        probe_input = np.zeros((1, input_dim), dtype=np.float32)
        result = predictor.predict_with_intermediates(probe_input, capture_hidden=True, capture_gates=True)
    except (ValueError, TypeError, RuntimeError) as exc:
        raise ProjectionError(f"predict_with_intermediates 调用失败: {exc}") from exc

    intermediates = result.model_info.get("intermediates", {}) or {}
    gate_values_raw = intermediates.get("gate_values", [])
    time_constants_raw = intermediates.get("time_constants", [])

    if not gate_values_raw:
        raise ProjectionError(
            "模型未捕获到门控值，无法生成门控动力学解释"
            f"（capture_mode={intermediates.get('capture_mode', 'disabled')}）"
        )

    return intermediates, gate_values_raw, time_constants_raw


def compute_gate_anomalies(gate_array: np.ndarray, anomaly_sigma: float) -> tuple[list[float], list[int]]:
    """计算每个特征的全局平均门控值与异常帧索引.

    异常帧定义：门控值偏离均值超过 ``anomaly_sigma * std``。
    """
    # 每个特征的全局平均门控值
    mean_gate_per_feature = np.mean(gate_array, axis=0).astype(float).tolist() if gate_array.size > 0 else []

    # 异常帧检测：门控值超过 mean ± sigma*std
    anomaly_frames: list[int] = []
    if gate_array.ndim == 2 and gate_array.shape[0] > 1:
        mean = np.mean(gate_array, axis=0)
        std = np.std(gate_array, axis=0)
        for frame_idx in range(gate_array.shape[0]):
            deviations = np.abs(gate_array[frame_idx] - mean)
            if np.any(deviations > anomaly_sigma * (std + 1e-8)):
                anomaly_frames.append(frame_idx)
    return mean_gate_per_feature, anomaly_frames


# ── 反事实 ──────────────────────────────────────────────────────────


def build_perturbation_range(
    perturbation_range: list[float] | None,
    perturbation_step: float,
) -> list[float]:
    """生成扰动序列（相对基准值的比例）.

    若未提供 perturbation_range，默认 ±20%、步长 perturbation_step。
    """
    if perturbation_range is None:
        # 默认 ±20%，步长 perturbation_step
        steps = int(0.2 / perturbation_step)
        perturbation_range = [round(-0.2 + i * perturbation_step, 4) for i in range(-steps, steps + 1)]
    if not perturbation_range:
        raise ExplanationValidationError("perturbation_range 不能为空")
    return perturbation_range


def scan_counterfactual_outputs(
    predictor: Any,
    base_input: dict[str, float],
    perturbed_feature: str,
    base_value: float,
    perturbation_range: list[float],
) -> list[float]:
    """逐点扰动推理，返回每个扰动点对应的标量输出.

    推理失败时该点输出记为 0.0 并告警。
    """
    outputs: list[float] = []
    for perturbation in perturbation_range:
        perturbed_input = dict(base_input)
        perturbed_input[perturbed_feature] = base_value * (1.0 + perturbation)
        try:
            # 构造输入向量（按 base_input 的值顺序）
            input_vector = np.array(list(perturbed_input.values()), dtype=np.float32).reshape(1, -1)
            result = predictor.predict(input_vector)
            output_value = result if not isinstance(result, dict) else (result.get("value", 0.0))
            # 标量化
            if hasattr(output_value, "item"):
                output_value = float(output_value.item())
            elif hasattr(output_value, "__iter__"):
                output_value = float(np.mean(output_value))
            else:
                output_value = float(output_value)
            outputs.append(output_value)
        except (ValueError, TypeError, RuntimeError) as exc:
            logger.warning(
                "反事实推理失败 perturbation=%.4f: %s",
                perturbation,
                exc,
            )
            outputs.append(0.0)
    return outputs


def compute_counterfactual_metrics(
    outputs: list[float],
    perturbation_range: list[float],
) -> tuple[float, list[dict[str, Any]]]:
    """计算一阶敏感度系数与临界点（差分突变）.

    Returns
    -------
    tuple[float, list[dict]]
        (sensitivity, critical_points) — sensitivity 为差分均值的绝对值，
        critical_points 为突变点列表（含 perturbation / output / delta）。
    """
    # 计算敏感度（一阶导数均值）
    outputs_array = np.asarray(outputs, dtype=np.float32)
    if len(outputs) >= 2:
        diffs = np.diff(outputs_array) / (np.diff(perturbation_range) + 1e-8)
        sensitivity = float(np.mean(np.abs(diffs)))
    else:
        sensitivity = 0.0

    # 识别临界点（差分突变）
    critical_points: list[dict[str, Any]] = []
    if len(outputs) >= 3:
        deltas = np.abs(np.diff(outputs_array))
        mean_delta = float(np.mean(deltas)) if deltas.size > 0 else 0.0
        threshold = mean_delta * 2.0 if mean_delta > 0 else 0.0
        for i in range(1, len(outputs) - 1):
            delta = float(abs(outputs[i] - outputs[i - 1]))
            if delta > threshold and threshold > 0:
                critical_points.append(
                    {
                        "perturbation": perturbation_range[i],
                        "output": outputs[i],
                        "delta": delta,
                    }
                )
    return sensitivity, critical_points


# ── 置信度（MC dropout） ────────────────────────────────────────────


def collect_mc_dropout_samples(
    predictor: Any,
    input_vector: np.ndarray,
    sample_count: int,
) -> tuple[float, float]:
    """调用 MC dropout 采样并返回 (mean, std).

    Raises
    ------
    SamplingError
        MC dropout 采样失败。
    """
    try:
        mc_result = predictor.predict_mc_dropout(input_vector, n_samples=sample_count)
    except (ValueError, TypeError, RuntimeError) as exc:
        raise SamplingError(f"MC dropout 采样失败: {exc}") from exc

    mc_info = mc_result.model_info or {}
    return float(mc_info.get("mc_mean", 0.0)), float(mc_info.get("mc_std", 0.0))


def build_confidence_distribution(
    mc_mean: float,
    mc_std: float,
    sample_count: int,
) -> tuple[dict[str, float], dict[str, Any]]:
    """基于正态假设计算分位数与直方图.

    v1：单点采样的均值/方差，分位数与直方图为简化估计；
    真实分布需要 predict_mc_dropout 返回所有样本，v1 保守估计。

    Returns
    -------
    tuple[dict, dict]
        (percentiles, histogram) — 分位数字典与直方图字典。
    """
    percentiles = {
        "p5": mc_mean - 1.645 * mc_std if mc_std > 0 else mc_mean,
        "p25": mc_mean - 0.674 * mc_std if mc_std > 0 else mc_mean,
        "p50": mc_mean,
        "p75": mc_mean + 0.674 * mc_std if mc_std > 0 else mc_mean,
        "p95": mc_mean + 1.645 * mc_std if mc_std > 0 else mc_mean,
    }

    # 直方图（基于正态假设生成 20 个 bin 的计数）
    if mc_std > 0:
        bins = np.linspace(mc_mean - 3 * mc_std, mc_mean + 3 * mc_std, 21).tolist()
        # 简化：使用正态分布 CDF 差分估算计数
        from math import erf, sqrt

        counts = []
        for i in range(len(bins) - 1):
            cdf_low = 0.5 * (1 + erf((bins[i] - mc_mean) / (mc_std * sqrt(2))))
            cdf_high = 0.5 * (1 + erf((bins[i + 1] - mc_mean) / (mc_std * sqrt(2))))
            counts.append(int((cdf_high - cdf_low) * sample_count))
        histogram = {"bins": bins, "counts": counts}
    else:
        histogram = {
            "bins": [mc_mean, mc_mean],
            "counts": [sample_count],
        }
    return percentiles, histogram


# ── 差异计算 ────────────────────────────────────────────────────────


def compute_diff(
    base: dict[str, Any],
    compared: dict[str, Any],
    explanation_type: str,
) -> dict[str, Any]:
    """计算两个解释 payload 的差异.

    根据 explanation_type 选择差异计算策略：
    - hidden_state：投影坐标的 L2 距离 + 能量差
    - gate_dynamics：门控值的逐帧差分 + 异常帧差异
    - counterfactual：输出曲线的逐点差分 + 敏感度差
    - confidence：均值/标准差/异常分数差
    """
    from app.contracts.explainability import ExplanationType

    diff: dict[str, Any] = {
        "explanation_type": explanation_type,
        "base_summary": {},
        "compared_summary": {},
        "differences": {},
    }

    if explanation_type == ExplanationType.HIDDEN_STATE:
        base_proj = np.asarray(base.get("projections", []), dtype=float)
        comp_proj = np.asarray(compared.get("projections", []), dtype=float)
        base_energy = np.asarray(base.get("energies", []), dtype=float)
        comp_energy = np.asarray(compared.get("energies", []), dtype=float)

        diff["base_summary"] = {
            "sample_count": base.get("sample_count", 0),
            "mean_energy": float(np.mean(base_energy)) if base_energy.size else 0.0,
        }
        diff["compared_summary"] = {
            "sample_count": compared.get("sample_count", 0),
            "mean_energy": float(np.mean(comp_energy)) if comp_energy.size else 0.0,
        }
        # 对齐长度后计算距离
        min_len = min(len(base_proj), len(comp_proj))
        if min_len > 0:
            distances = np.linalg.norm(base_proj[:min_len] - comp_proj[:min_len], axis=1)
            diff["differences"] = {
                "mean_distance": float(np.mean(distances)),
                "max_distance": float(np.max(distances)),
                "energy_diff": float(np.mean(comp_energy[:min_len] - base_energy[:min_len])),
            }

    elif explanation_type == ExplanationType.GATE_DYNAMICS:
        base_gates = np.asarray(base.get("gate_values", []), dtype=float)
        comp_gates = np.asarray(compared.get("gate_values", []), dtype=float)
        diff["base_summary"] = {
            "frame_count": len(base.get("frame_ids", [])),
            "anomaly_frame_count": len(base.get("anomaly_frames", [])),
        }
        diff["compared_summary"] = {
            "frame_count": len(compared.get("frame_ids", [])),
            "anomaly_frame_count": len(compared.get("anomaly_frames", [])),
        }
        min_len = min(len(base_gates), len(comp_gates))
        if min_len > 0:
            diffs = np.abs(base_gates[:min_len] - comp_gates[:min_len])
            diff["differences"] = {
                "mean_gate_diff": float(np.mean(diffs)),
                "max_gate_diff": float(np.max(diffs)),
            }

    elif explanation_type == ExplanationType.COUNTERFACTUAL:
        base_outputs = np.asarray(base.get("outputs", []), dtype=float)
        comp_outputs = np.asarray(compared.get("outputs", []), dtype=float)
        diff["base_summary"] = {
            "sensitivity": base.get("sensitivity", 0.0),
            "critical_point_count": len(base.get("critical_points", [])),
        }
        diff["compared_summary"] = {
            "sensitivity": compared.get("sensitivity", 0.0),
            "critical_point_count": len(compared.get("critical_points", [])),
        }
        min_len = min(len(base_outputs), len(comp_outputs))
        if min_len > 0:
            output_diffs = base_outputs[:min_len] - comp_outputs[:min_len]
            diff["differences"] = {
                "mean_output_diff": float(np.mean(output_diffs)),
                "max_output_diff": float(np.max(np.abs(output_diffs))),
                "sensitivity_diff": float(base.get("sensitivity", 0.0) - compared.get("sensitivity", 0.0)),
            }

    elif explanation_type == ExplanationType.CONFIDENCE:
        diff["base_summary"] = {
            "mean": base.get("mean", 0.0),
            "std": base.get("std", 0.0),
            "anomaly_score": base.get("anomaly_score", 0.0),
        }
        diff["compared_summary"] = {
            "mean": compared.get("mean", 0.0),
            "std": compared.get("std", 0.0),
            "anomaly_score": compared.get("anomaly_score", 0.0),
        }
        diff["differences"] = {
            "mean_diff": float(base.get("mean", 0.0) - compared.get("mean", 0.0)),
            "std_diff": float(base.get("std", 0.0) - compared.get("std", 0.0)),
            "anomaly_score_diff": float(base.get("anomaly_score", 0.0) - compared.get("anomaly_score", 0.0)),
        }

    return diff


__all__ = [
    # 隐状态
    "collect_hidden_state_intermediates",
    "build_hidden_state_explanation",
    # 门控动力学
    "collect_gate_intermediates",
    "compute_gate_anomalies",
    # 反事实
    "build_perturbation_range",
    "scan_counterfactual_outputs",
    "compute_counterfactual_metrics",
    # 置信度
    "collect_mc_dropout_samples",
    "build_confidence_distribution",
    # 差异
    "compute_diff",
]
