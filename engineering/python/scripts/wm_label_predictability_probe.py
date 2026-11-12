"""短程标签可预测性探针：持久性/漂移/条件岭回归三档简单基线.

目的（承接 state_scale 报告的下一步）：
在不动神经网络的前提下，量化 cv_{c1,c4,c6} 短程（horizon=5）标签里
到底有多少可被简单条件模型利用的信息。若岭回归也无法超过持久性，
则"加轮数/调损失"追分的前提不成立，应先补标签信息或改预测目标。

口径
----
- 特征仅用窗口末条状态（8）+ 末条动力学（6）+ 未来动作均值（4），
  不使用未来状态；拟合只碰训练折，验证折只在评估时使用一次。
- 岭回归闭式解，特征按训练折统计 z-score；λ 网格用训练折内部
  确定性切分（末 20% 窗口）选择，选定后在全训练折重拟合。
- 预测目标是未来增量（target - initial_state），与相对增量头口径一致。
- 已知局限：窗口 stride=25 小于窗口长度，训练折内部存在窗口重叠，
  内部 λ 选择与绝对分数受此影响；跨组评估（val 为留出组）仍可支撑
  基线间的相对比较。探针结果为诊断，不构成预测优势声明。

用法（仓库根，使用新输出路径）::

    python engineering/python/desktop_runtime/runtime/python.exe 无需——纯 numpy。
    C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe ^
        engineering/python/scripts/wm_label_predictability_probe.py ^
        --output research/experiments/results/world_model_label_predictability_<date>.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from wm_audit_frozen_history import load_arrays  # noqa: E402

STATE_COLUMNS = ("chatter_probability", "tool_wear", "surface_roughness", "vibration_rms",
                 "temperature_over_100c", "cutting_force_norm", "mrr_norm", "confidence")
FEATURE_NAMES = tuple(f"init_{name}" for name in STATE_COLUMNS) \
    + ("dyn_spindle_speed", "dyn_feed_rate", "dyn_depth_of_cut",
       "dyn_tool_wear", "dyn_vibration_rms", "dyn_temperature") \
    + tuple(f"future_action_mean_{axis}" for axis in ("spindle_speed", "feed_rate", "depth_of_cut", "width_of_cut"))
LAMBDA_GRID = (1e-4, 1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0)


def build_features(arrays: dict[str, np.ndarray]) -> np.ndarray:
    """(n, 18) 特征：末条状态 + 末条动力学 + 未来动作均值。"""
    initial_state = arrays["state_history"][:, -1, :]
    last_dynamics = arrays["dynamics"][:, -1, :]
    future_actions = arrays["actions"][:, arrays["state_history"].shape[1]:, :].mean(axis=1)
    features = np.concatenate([initial_state, last_dynamics, future_actions], axis=1)
    if features.shape[1] != len(FEATURE_NAMES) or not np.isfinite(features).all():
        raise ValueError("特征构造失败或含非有限值")
    return features.astype(np.float64)


def ridge_fit(features: np.ndarray, targets: np.ndarray, lam: float) -> dict:
    """中心化闭式岭回归。targets 为 (n,) 或 (n, k)。"""
    if lam <= 0 or not math.isfinite(lam):
        raise ValueError("岭系数必须为正有限数")
    if features.ndim != 2 or targets.shape[0] != features.shape[0]:
        raise ValueError("特征与目标样本数不匹配")
    x_mean = features.mean(axis=0)
    x_scale = features.std(axis=0)
    x_scale = np.where(x_scale < 1e-8, 1.0, x_scale)
    x = (features - x_mean) / x_scale
    if targets.ndim == 1:
        y_mean = float(targets.mean())
        y = targets - y_mean
    else:
        y_mean = targets.mean(axis=0)
        y = targets - y_mean
    gram = x.T @ x + lam * np.eye(x.shape[1])
    weights = np.linalg.solve(gram, x.T @ y)
    return {"x_mean": x_mean, "x_scale": x_scale, "y_mean": np.atleast_1d(y_mean), "weights": weights}


def ridge_predict(model: dict, features: np.ndarray) -> np.ndarray:
    x = (features - model["x_mean"]) / model["x_scale"]
    return x @ model["weights"] + model["y_mean"]


def select_lambda(features: np.ndarray, targets: np.ndarray, lambdas: tuple[float, ...] = LAMBDA_GRID,
                  internal_fraction: float = 0.2) -> float:
    """训练折内部确定性切分选 λ：末 internal_fraction 窗口做内部验证。"""
    if not 0 < internal_fraction < 1:
        raise ValueError("内部验证比例须在 (0,1) 内")
    cut = max(1, int(round(features.shape[0] * (1 - internal_fraction))))
    fit_x, fit_y = features[:cut], targets[:cut]
    hold_x, hold_y = features[cut:], targets[cut:]
    best_lam, best_mse = lambdas[0], math.inf
    for lam in lambdas:
        model = ridge_fit(fit_x, fit_y, lam)
        mse = float(((ridge_predict(model, hold_x) - hold_y) ** 2).mean())
        if mse < best_mse - 1e-15:
            best_lam, best_mse = lam, mse
    return best_lam


def channel_deltas(arrays: dict[str, np.ndarray]) -> np.ndarray:
    """未来增量 (n, horizon, 8)：target - 末条状态。"""
    return arrays["target"] - arrays["state_history"][:, -1, :][:, None, :]


def evaluate_baselines(train: dict[str, np.ndarray], val: dict[str, np.ndarray]) -> dict:
    """逐通道逐步各基线的 MAE/MSE，与模型口径相同的原单位。"""
    horizon = train["target"].shape[1]
    train_delta, val_delta = channel_deltas(train), channel_deltas(val)
    drift = train_delta.mean(axis=0)  # (horizon, 8)，仅训练折统计
    persistence_mse_per_step = (val_delta ** 2).mean(axis=0)  # 持久性=零增量预测
    results: dict = {"per_channel": {}, "channels": list(STATE_COLUMNS), "horizon": horizon}
    for channel_index, channel in enumerate(STATE_COLUMNS):
        channel_result: dict = {"steps": []}
        ridge_mse_steps, persistence_mse_steps, drift_mse_steps = [], [], []
        ridge_mae_steps, persistence_mae_steps, drift_mae_steps = [], [], []
        for step in range(horizon):
            y_train = train_delta[:, step, channel_index]
            y_val = val_delta[:, step, channel_index]
            lam = select_lambda(build_features(train), y_train)
            model = ridge_fit(build_features(train), y_train, lam)
            ridge_pred = ridge_predict(model, build_features(val))
            ridge_mse = float(((ridge_pred - y_val) ** 2).mean())
            drift_mse = float(((drift[step, channel_index] - y_val) ** 2).mean())
            persistence_mse = float(persistence_mse_per_step[step, channel_index])
            ridge_mse_steps.append(ridge_mse)
            drift_mse_steps.append(drift_mse)
            persistence_mse_steps.append(persistence_mse)
            ridge_mae_steps.append(float(np.abs(ridge_pred - y_val).mean()))
            drift_mae_steps.append(float(np.abs(drift[step, channel_index] - y_val).mean()))
            persistence_mae_steps.append(float(np.abs(y_val).mean()))
            channel_result["steps"].append({"step": step + 1, "ridge_lambda": lam})
        ridge_mse_total = float(np.mean(ridge_mse_steps))
        persistence_mse_total = float(np.mean(persistence_mse_steps))
        channel_result["mae"] = {"ridge": float(np.mean(ridge_mae_steps)),
                                 "train_drift": float(np.mean(drift_mae_steps)),
                                 "persistence": float(np.mean(persistence_mae_steps))}
        channel_result["mse"] = {"ridge": ridge_mse_total, "train_drift": float(np.mean(drift_mse_steps)),
                                 "persistence": persistence_mse_total}
        channel_result["ridge_vs_persistence_r2"] = 1.0 - ridge_mse_total / persistence_mse_total \
            if persistence_mse_total > 0 else None
        channel_result["ridge_beats_persistence"] = bool(ridge_mse_total < persistence_mse_total)
        results["per_channel"][channel] = channel_result
    return results


def run_probe(data_dir: Path, output_path: Path, groups: list[str]) -> dict:
    if output_path.exists():
        raise FileExistsError("输出结果文件已存在，请使用全新路径")
    if not groups or len(set(groups)) != len(groups) or any(group not in ("c1", "c4", "c6") for group in groups):
        raise ValueError("组须为c1/c4/c6的无重复子集")
    report: dict = {"kind": "exploratory_label_predictability_probe", "independent_final_test_established": False,
                    "groups": {}, "feature_names": list(FEATURE_NAMES), "lambda_grid": list(LAMBDA_GRID),
                    "caveats": ["窗口stride小于窗口长度，训练折内部存在重叠，绝对分数受影响",
                                "验证组即 pilots 的选模验证组，本探针为诊断而非独立终测",
                                "岭回归只是线性条件基线，不能排除非线性可学信号"]}
    for group in groups:
        train = load_arrays(data_dir / f"cv_{group}_train.npz")
        val = load_arrays(data_dir / f"cv_{group}_val.npz")
        results = evaluate_baselines(train, val)
        results["train_windows"] = int(train["target"].shape[0])
        results["val_windows"] = int(val["target"].shape[0])
        results["datasets_sha256"] = {
            "train": hashlib.sha256((data_dir / f"cv_{group}_train.npz").read_bytes()).hexdigest(),
            "val": hashlib.sha256((data_dir / f"cv_{group}_val.npz").read_bytes()).hexdigest()}
        report["groups"][group] = results
    report["status"] = "completed"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[3] / "data/world_model")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--groups", default="c1,c4,c6")
    args = parser.parse_args()
    try:
        report = run_probe(args.data_dir, args.output, args.groups.split(","))
    except (ValueError, OSError) as exc:
        print(f"[probe failed] {exc}", file=sys.stderr)
        return 1
    for group, results in report["groups"].items():
        winners = [name for name, channel in results["per_channel"].items() if channel["ridge_beats_persistence"]]
        print(f"[probe] {group}: 岭回归超过持久性的通道: {winners or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
