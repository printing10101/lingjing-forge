"""残差试跑的种子稳健性与岭回归特征消融.

两件事：
1. 种子扫描：control / residual 两臂 × c1/c4/c6 × 多种子（默认 42,43,44），
   检验 P1（residual 切削力 MSE 优于 control）与 P2（优于岭单独）是否
   依赖种子 42。岭回归本身确定（无种子），故岭基线不重复训练。
2. 特征消融：岭回归只用「末条状态」「+动力学」「+未来动作」三档特征，
   判断切削力条件信号主要来自哪里（是否为初始状态自相关）。

数据划分性质：cv_{g}_train 为另外两组的窗口、val 为留出组（留一实验组
交叉），岭回归与网络评估均为跨组泛化口径；但每折验证组都在其他折做过
训练与选模，本文件不宣称独立终测。

用法（仓库根，全新输出目录与结果路径）::

    & '...Python314/python.exe' engineering/python/scripts/wm_residual_robustness.py `
        --out-dir reports/wm_residual_seed_sweep_20261114 `
        --summary research/experiments/results/world_model_residual_seed_sweep_20261114.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

from train_world_model import load_dataset
from wm_audit_frozen_history import file_hash, load_arrays, window_hashes
from wm_label_predictability_probe import STATE_COLUMNS, build_features, channel_deltas, ridge_fit, ridge_predict, select_lambda
from wm_residual_pilot import (CHANNEL_INDEX, channel_metrics, fit_ridge_models, predict_full,
                               r2_vs_persistence, ridge_delta_predictions, shift_sample_targets, train_arm)
from app.plugins.world_model.net import WorldModelConfig, WorldModelNet

REPO_ROOT = Path(__file__).resolve().parents[3]
FEATURE_SLICES = {"state_only": slice(0, 8), "state_dynamics": slice(0, 14), "state_dynamics_actions": slice(0, 18)}


def ridge_deltas_for_slice(feature_slice: slice, train: dict[str, np.ndarray], val: dict[str, np.ndarray]) -> np.ndarray:
    """指定特征子集的岭回归增量预测（训练折拟合，验证折只预测）。"""
    train_features, val_features = build_features(train)[:, feature_slice], build_features(val)[:, feature_slice]
    deltas = channel_deltas(train)
    horizon, channels = deltas.shape[1], deltas.shape[2]
    prediction = np.zeros((val_features.shape[0], horizon, channels), dtype=np.float64)
    for step in range(horizon):
        for channel_index in range(channels):
            y = deltas[:, step, channel_index]
            lam = select_lambda(train_features, y)
            prediction[:, step, channel_index] = ridge_predict(ridge_fit(train_features, y, lam), val_features)
    return prediction


def feature_ablation(train: dict[str, np.ndarray], val: dict[str, np.ndarray]) -> dict:
    """三档特征集的验证折 R²（对持久性，MSE 口径）。"""
    target = val["target"].astype(np.float64)
    persistence = target - val["state_history"][:, -1, :][:, None, :].astype(np.float64)
    persistence_mse = {name: float((persistence[:, :, index] ** 2).mean()) for name, index in CHANNEL_INDEX.items()}
    ablation = {}
    for label, feature_slice in FEATURE_SLICES.items():
        deltas = ridge_deltas_for_slice(feature_slice, train, val)
        metrics = channel_metrics(val["state_history"][:, -1, :][:, None, :].astype(np.float64) + deltas, target)
        ablation[label] = {"r2_vs_persistence": r2_vs_persistence(metrics, persistence_mse),
                           "metrics": metrics}
    return ablation


def aggregate_arm(runs: list[dict], channel: str) -> dict:
    """单臂多种子的 R² 与 MAE 统计（均值/最小/最大）。"""
    r2_values = [run["r2_vs_persistence"][channel] for run in runs if run["r2_vs_persistence"][channel] is not None]
    mae_values = [run["metrics"][channel]["mae"] for run in runs]
    if not r2_values:
        raise ValueError("该通道在全部种子下无定义 R²")
    return {"seeds": len(runs), "r2_mean": float(np.mean(r2_values)), "r2_min": float(np.min(r2_values)),
            "r2_max": float(np.max(r2_values)), "mae_mean": float(np.mean(mae_values)),
            "mae_min": float(np.min(mae_values)), "mae_max": float(np.max(mae_values))}


def run_robustness(data_dir: Path, output_dir: Path, summary_path: Path, *, groups: list[str],
                   seeds: list[int], epochs: int) -> dict:
    import torch

    if output_dir.exists():
        raise FileExistsError("输出实验目录已存在，请使用新目录")
    if summary_path.exists():
        raise FileExistsError("结果摘要已存在，请使用全新路径")
    if not groups or len(set(groups)) != len(groups) or any(group not in ("c1", "c4", "c6") for group in groups):
        raise ValueError("组须为c1/c4/c6的无重复子集")
    if not seeds or len(set(seeds)) != len(seeds) or any(seed < 0 for seed in seeds):
        raise ValueError("种子须为非负且无重复")
    output_dir.mkdir(parents=True)
    ledger: dict = {"kind": "exploratory_residual_seed_sweep", "independent_final_test_established": False,
        "seeds": seeds, "epochs_per_run": epochs, "groups": {}, "runs": []}
    started = time.perf_counter()
    try:
        for group in groups:
            train_path, val_path = (data_dir / f"cv_{group}_{split}.npz" for split in ("train", "val"))
            train_arrays, val_arrays = load_arrays(train_path), load_arrays(val_path)
            if set(window_hashes(train_arrays)) & set(window_hashes(val_arrays)):
                raise ValueError("训练与验证存在相同窗口")
            samples, meta = load_dataset(train_path)
            val_samples, _ = load_dataset(val_path)
            ridge_models = fit_ridge_models(train_arrays)
            ridge_train_delta = ridge_delta_predictions(ridge_models, train_arrays)
            ridge_val_delta = ridge_delta_predictions(ridge_models, val_arrays)
            initial_val = val_arrays["state_history"][:, -1, :][:, None, :].astype(np.float64)
            target_val = val_arrays["target"].astype(np.float64)
            persistence_mse = {name: float(((target_val - initial_val)[:, :, CHANNEL_INDEX[name]] ** 2).mean())
                               for name in STATE_COLUMNS}
            ridge_alone = channel_metrics(initial_val + ridge_val_delta, target_val)
            group_ledger: dict = {"datasets_sha256": {"train": file_hash(train_path), "val": file_hash(val_path)},
                "action_channel": {"train_std_by_axis": train_arrays["actions"].std(axis=(0, 1)).tolist(),
                                   "val_std_by_axis": val_arrays["actions"].std(axis=(0, 1)).tolist(),
                                   "train_unique_by_axis": [int(np.unique(train_arrays["actions"][:, :, axis]).size)
                                                            for axis in range(train_arrays["actions"].shape[2])],
                                   "val_unique_by_axis": [int(np.unique(val_arrays["actions"][:, :, axis]).size)
                                                          for axis in range(val_arrays["actions"].shape[2])],
                                   "note": "std 全零时动作特征不携带信息，动作消融结果不可解释为模型能力"},
                "ridge_alone_r2_vs_persistence": r2_vs_persistence(ridge_alone, persistence_mse),
                "ridge_alone_metrics": ridge_alone, "seeds": {}, "p1_passes": [], "p2_passes": []}
            for seed in seeds:
                arm_runs: dict = {}
                for arm in ("control", "residual"):
                    version = f"seed-{group}-{arm}-s{seed}"
                    if arm == "residual":
                        arm_samples = [dict(sample) for sample in samples]
                        shift_sample_targets(arm_samples, ridge_train_delta)
                        run = train_arm(arm_samples, val_samples, meta, train_arrays, output_dir, version,
                            epochs=epochs, seed=seed, ridge_delta_train=ridge_train_delta)
                    else:
                        run = train_arm(samples, val_samples, meta, train_arrays, output_dir, version,
                            epochs=epochs, seed=seed, ridge_delta_train=None)
                    checkpoint = torch.load(run["checkpoint"], map_location="cpu", weights_only=True)
                    model = WorldModelNet(WorldModelConfig(**checkpoint["model_config"]))
                    model.load_state_dict(checkpoint["model_state_dict"])
                    net_prediction = predict_full(model, val_arrays, batch_size=64)
                    combined = net_prediction + ridge_val_delta if arm == "residual" else net_prediction
                    metrics = channel_metrics(combined, target_val)
                    arm_runs[arm] = {"metrics": metrics, "r2_vs_persistence": r2_vs_persistence(metrics, persistence_mse),
                                     "seed": seed, "selected_epoch": run["selected_epoch"],
                                     "checkpoint_sha256": run["checkpoint_sha256"]}
                    ledger["runs"].append({"group": group, "arm": arm, "seed": seed,
                                           "force_r2": arm_runs[arm]["r2_vs_persistence"]["cutting_force_norm"]})
                    print(f"[sweep] {version} epoch={run['selected_epoch']} "
                          f"force_r2={arm_runs[arm]['r2_vs_persistence']['cutting_force_norm']}", flush=True)
                control_r2 = arm_runs["control"]["r2_vs_persistence"]["cutting_force_norm"]
                residual_r2 = arm_runs["residual"]["r2_vs_persistence"]["cutting_force_norm"]
                ridge_r2 = group_ledger["ridge_alone_r2_vs_persistence"]["cutting_force_norm"]
                if None not in (control_r2, residual_r2):
                    group_ledger["p1_passes"].append(bool(residual_r2 > control_r2))
                if None not in (residual_r2, ridge_r2):
                    group_ledger["p2_passes"].append(bool(residual_r2 > ridge_r2))
                group_ledger["seeds"][str(seed)] = arm_runs
            group_ledger["aggregate"] = {
                arm: {channel: aggregate_arm([group_ledger["seeds"][str(seed)][arm] for seed in seeds], channel)
                      for channel in ("cutting_force_norm", "tool_wear", "vibration_rms")}
                for arm in ("control", "residual")}
            group_ledger["feature_ablation"] = feature_ablation(train_arrays, val_arrays)
            ledger["groups"][group] = group_ledger
        ledger["status"] = "completed"
    except (ValueError, RuntimeError, OSError) as exc:
        ledger["status"], ledger["error"] = "failed", str(exc)
        raise
    finally:
        ledger["elapsed_seconds"] = time.perf_counter() - started
        (output_dir / "ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2, allow_nan=False),
                                                encoding="utf-8")
    p1_all = [value for group in ledger["groups"].values() for value in group["p1_passes"]]
    p2_all = [value for group in ledger["groups"].values() for value in group["p2_passes"]]
    ledger["summary"] = {"p1_pass_count": sum(p1_all), "p1_total": len(p1_all),
                         "p2_pass_count": sum(p2_all), "p2_total": len(p2_all)}
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data/world_model")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--groups", default="c1,c4,c6")
    parser.add_argument("--seeds", default="42,43,44")
    parser.add_argument("--epochs", type=int, default=20)
    args = parser.parse_args()
    try:
        ledger = run_robustness(args.data_dir, args.out_dir, args.summary, groups=args.groups.split(","),
                                seeds=[int(seed) for seed in args.seeds.split(",")], epochs=args.epochs)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"[sweep failed] {exc}", file=sys.stderr)
        return 1
    print(f"[sweep] P1 {ledger['summary']['p1_pass_count']}/{ledger['summary']['p1_total']}，"
          f"P2 {ledger['summary']['p2_pass_count']}/{ledger['summary']['p2_total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
