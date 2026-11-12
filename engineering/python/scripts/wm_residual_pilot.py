"""残差建模匹配试跑：岭回归作基座，神经网络学残差.

承接 label_predictability 探针结论（切削力/磨损存在线性条件信号而 12 项
神经网络试跑未兑现）。两臂匹配对照，仅目标平移不同：

- control 臂：原始目标，相对增量头 + 物理损失（state_scale 最优臂配置）；
- residual 臂：目标平移为 ``target - ridge_delta``，岭回归逐通道逐步在
  训练折拟合（复用可预测性探针的闭式岭回归与内部 λ 选择）。评估时
  组合预测 = 网络预测 + ridge_delta，零初始化时组合预测精确等于
  "岭回归单独" 基线。

预登记比较（验证折只评估一次；诊断性质，不构成独立终测）：
- P1: residual 组合切削力 MSE < control 切削力 MSE；
- P2: residual 组合切削力 MSE < 岭回归单独切削力 MSE（网络是否学到岭外残差）；
- P3: 磨损 MAE 三档（control / residual / 岭单独 / 持久性）如实记录，不预设胜负。

用法（仓库根，全新输出目录与结果路径）::

    & '...Python314/python.exe' engineering/python/scripts/wm_residual_pilot.py `
        --out-dir reports/wm_residual_pilot_<date> `
        --summary research/experiments/results/world_model_residual_pilot_<date>.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np

from train_world_model import load_dataset
from wm_audit_frozen_history import file_hash, load_arrays, window_hashes
from wm_label_predictability_probe import (LAMBDA_GRID, STATE_COLUMNS, build_features,
                                           channel_deltas, ridge_fit, ridge_predict, select_lambda)
from app.plugins.world_model.input_normalization import fit_fusion_normalization
from app.plugins.world_model.state_loss_normalization import fit_state_scale
from app.plugins.world_model.net import WorldModelConfig, WorldModelNet

REPO_ROOT = Path(__file__).resolve().parents[3]
CHANNEL_INDEX = {name: index for index, name in enumerate(STATE_COLUMNS)}


def fit_ridge_models(train: dict[str, np.ndarray]) -> list[dict]:
    """逐(步,通道)在训练折拟合岭回归，返回模型表。"""
    features = build_features(train)
    deltas = channel_deltas(train)
    horizon = deltas.shape[1]
    models = []
    for step in range(horizon):
        for channel_index in range(deltas.shape[2]):
            y = deltas[:, step, channel_index]
            lam = select_lambda(features, y)
            models.append({"step": step, "channel": channel_index, "lambda": lam,
                           "model": ridge_fit(features, y, lam)})
    return models


def ridge_delta_predictions(models: list[dict], arrays: dict[str, np.ndarray]) -> np.ndarray:
    """(n, horizon, 8) 岭回归增量预测。"""
    features = build_features(arrays)
    horizon = max(entry["step"] for entry in models) + 1
    channels = max(entry["channel"] for entry in models) + 1
    prediction = np.zeros((features.shape[0], horizon, channels), dtype=np.float64)
    for entry in models:
        prediction[:, entry["step"], entry["channel"]] = ridge_predict(entry["model"], features)
    return prediction


def shift_sample_targets(samples: list[dict], ridge_delta_train: np.ndarray) -> None:
    """就地平移 target_trajectory 为残差目标；initial_state 与输入不动。"""
    if len(samples) != ridge_delta_train.shape[0]:
        raise ValueError("样本数与岭回归增量预测数不匹配")
    for index, sample in enumerate(samples):
        shifted = sample["target_trajectory"].astype(np.float64) - ridge_delta_train[index]
        if not np.isfinite(shifted).all():
            raise ValueError(f"窗口{index}残差目标含非有限值")
        sample["target_trajectory"] = shifted.astype(np.float32)


def predict_full(model, arrays: dict[str, np.ndarray], *, batch_size: int) -> np.ndarray:
    """反馈模式下全量前向，返回 (n, horizon, 8) 预测。"""
    import torch

    model.cpu().eval()
    horizon = arrays["target"].shape[1]
    batches = []
    with torch.inference_mode():
        for start in range(0, len(arrays["target"]), batch_size):
            region = slice(start, start + batch_size)
            output = model(states=None, actions=torch.from_numpy(arrays["actions"][region]), horizon=horizon,
                unified_states=(torch.from_numpy(arrays["geometry"][region]), torch.from_numpy(arrays["dynamics"][region])),
                initial_state=torch.from_numpy(arrays["state_history"][region, -1]))
            batches.append(output["predicted_trajectory"].cpu().numpy())
    prediction = np.concatenate(batches).astype(np.float64)
    if prediction.shape != arrays["target"].shape or not np.isfinite(prediction).all():
        raise ValueError("全量预测形状或有限性不合格")
    return prediction


def channel_metrics(prediction: np.ndarray, target: np.ndarray) -> dict[str, dict[str, float]]:
    """逐通道 MAE 与 MSE（原单位，float64）。"""
    error = prediction - target
    return {name: {"mae": float(np.abs(error[:, :, index]).mean()),
                   "mse": float((error[:, :, index] ** 2).mean())}
            for name, index in CHANNEL_INDEX.items()}


def r2_vs_persistence(metrics: dict[str, dict[str, float]], persistence_mse: dict[str, float]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for name in metrics:
        baseline = persistence_mse.get(name, 0.0)
        result[name] = (1.0 - metrics[name]["mse"] / baseline) if baseline > 0 else None
    return result


def train_arm(samples: list[dict], val_samples: list[dict], meta: dict,
              train_arrays: dict[str, np.ndarray], output_dir: Path, version: str, *,
              epochs: int, seed: int, ridge_delta_train: np.ndarray | None) -> dict:
    """训练单臂并返回 checkpoint 路径；residual 臂传 ridge_delta_train 平移目标。

    选模与 state_scale 协议一致：验证折做早停/选模（诊断性质，
    independent_final_test_established=False）。"""
    import torch
    from torch.utils.data import DataLoader

    from app.plugins.world_model.training.fusion_dataset import FusionTrajectoryDataset, fusion_collate_fn
    from app.plugins.world_model.training.fusion_trainer import FusionWorldModelTrainer

    torch.set_num_threads(1)
    targets = train_arrays["target"] if ridge_delta_train is None else train_arrays["target"] - ridge_delta_train
    state_scale = fit_state_scale(targets.astype(np.float32), train_arrays["state_history"][:, -1])
    normalization = fit_fusion_normalization(train_arrays["geometry"], train_arrays["dynamics"])
    config = WorldModelConfig(feature_dim=meta["geo_dim"] - 5, seed=seed, delta_prediction=True,
        input_normalization=normalization, delta_output_normalization=state_scale)
    model = WorldModelNet(config)
    torch.nn.init.zeros_(model.delta_head.weight)
    torch.nn.init.zeros_(model.delta_head.bias)
    identity = hashlib.sha256(b"".join(parameter.detach().numpy().tobytes() for parameter in model.parameters())).hexdigest()
    # 零增量初始化检查：控制臂网络输出=持久性；残差臂组合输出=持久性+岭增量。
    model.eval()
    with torch.inference_mode():
        count = min(8, len(samples))
        initial = torch.from_numpy(train_arrays["state_history"][:count, -1])
        probe = model(states=None, actions=torch.from_numpy(train_arrays["actions"][:count]), horizon=meta["horizon"],
            unified_states=(torch.from_numpy(train_arrays["geometry"][:count]), torch.from_numpy(train_arrays["dynamics"][:count])),
            initial_state=initial)["predicted_trajectory"].numpy().astype(np.float64)
    if ridge_delta_train is None:
        if float(np.abs(probe - initial.numpy()[:, None]).max()) != 0:
            raise ValueError("零增量初始化未达到精确持久性预测")
    else:
        combined_probe = probe + ridge_delta_train[:count]
        if float(np.abs(combined_probe - (initial.numpy()[:, None] + ridge_delta_train[:count])).max()) != 0:
            raise ValueError("残差臂零初始化组合预测未精确等于持久性加岭增量")
    trainer = FusionWorldModelTrainer(model, model_uri=f"model://world_model/{version}", epochs=epochs,
        early_stopping_patience=epochs, seed=seed, device="cpu", use_amp=False, track_experiment=False,
        models_dir=str(output_dir / "models"), loss_normalization=None,
        selection_loss_normalization=state_scale)
    loader = DataLoader(FusionTrajectoryDataset(samples, geo_input_dim=meta["geo_dim"]), batch_size=32,
        shuffle=True, collate_fn=fusion_collate_fn, generator=torch.Generator().manual_seed(seed))
    val_loader = DataLoader(FusionTrajectoryDataset(val_samples, geo_input_dim=meta["geo_dim"]), batch_size=32,
        shuffle=False, collate_fn=fusion_collate_fn)
    started = time.perf_counter()
    trainer.train(loader, val_loader, horizon=meta["horizon"])
    checkpoint = Path(trainer.save_checkpoint(version))
    return {"version": version, "checkpoint": str(checkpoint), "checkpoint_sha256": file_hash(checkpoint),
            "initial_parameters_sha256": identity, "selected_epoch": trainer.selected_epoch,
            "elapsed_seconds": time.perf_counter() - started}


def run_pilot(data_dir: Path, output_dir: Path, summary_path: Path, *, groups: list[str],
              epochs: int, seed: int) -> dict:
    if output_dir.exists():
        raise FileExistsError("输出实验目录已存在，请使用新目录")
    if summary_path.exists():
        raise FileExistsError("结果摘要已存在，请使用全新路径")
    if not groups or len(set(groups)) != len(groups) or any(group not in ("c1", "c4", "c6") for group in groups):
        raise ValueError("组须为c1/c4/c6的无重复子集")
    output_dir.mkdir(parents=True)
    ledger: dict = {"kind": "exploratory_residual_factorial", "independent_final_test_established": False,
        "seed": seed, "epochs_per_run": epochs, "groups": {}, "lambda_grid": list(LAMBDA_GRID), "arms": []}
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
            group_ledger: dict = {"train_windows": int(train_arrays["target"].shape[0]),
                "val_windows": int(val_arrays["target"].shape[0]),
                "datasets_sha256": {"train": file_hash(train_path), "val": file_hash(val_path)},
                "ridge_lambdas_by_channel": {}, "arms": {}}
            for channel_index, channel in enumerate(STATE_COLUMNS):
                group_ledger["ridge_lambdas_by_channel"][channel] = [
                    entry["lambda"] for entry in ridge_models if entry["channel"] == channel_index]
            for arm in ("control", "residual"):
                version = f"residual-{group}-{arm}-s{seed}"
                if arm == "residual":
                    arm_samples = [dict(sample) for sample in samples]
                    shift_sample_targets(arm_samples, ridge_train_delta)
                    arm_run = train_arm(arm_samples, val_samples, meta, train_arrays,
                        output_dir, version, epochs=epochs, seed=seed, ridge_delta_train=ridge_train_delta)
                else:
                    arm_run = train_arm(samples, val_samples, meta, train_arrays,
                        output_dir, version, epochs=epochs, seed=seed, ridge_delta_train=None)
                import torch
                checkpoint = torch.load(arm_run["checkpoint"], map_location="cpu", weights_only=True)
                config = WorldModelConfig(**checkpoint["model_config"])
                model = WorldModelNet(config)
                model.load_state_dict(checkpoint["model_state_dict"])
                net_prediction = predict_full(model, val_arrays, batch_size=64)
                combined = net_prediction + ridge_val_delta if arm == "residual" else net_prediction
                metrics = channel_metrics(combined, target_val)
                ridge_alone = channel_metrics(initial_val + ridge_val_delta, target_val)
                audit = {"arm": arm, "group": group, "version": version, "run": arm_run,
                    "metrics": metrics, "ridge_alone_metrics": ridge_alone,
                    "r2_vs_persistence": r2_vs_persistence(metrics, persistence_mse),
                    "ridge_alone_r2_vs_persistence": r2_vs_persistence(ridge_alone, persistence_mse),
                    "persistence_mse": persistence_mse,
                    "persistence_mae": {name: float(np.abs((target_val - initial_val)[:, :, CHANNEL_INDEX[name]]).mean())
                                        for name in STATE_COLUMNS}}
                audit_path = output_dir / f"{version}.audit.json"
                audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
                audit["audit_path"], audit["audit_sha256"] = str(audit_path), file_hash(audit_path)
                group_ledger["arms"][arm] = audit
                ledger["arms"].append({"group": group, "arm": arm, "version": version,
                    "checkpoint_sha256": arm_run["checkpoint_sha256"], "audit_sha256": file_hash(audit_path)})
                print(f"[pilot] {version} epoch={arm_run['selected_epoch']} completed", flush=True)
            ledger["groups"][group] = group_ledger
        ledger["status"] = "completed"
    except (ValueError, RuntimeError, OSError) as exc:
        ledger["status"], ledger["error"] = "failed", str(exc)
        raise
    finally:
        ledger["elapsed_seconds"] = time.perf_counter() - started
        (output_dir / "ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2, allow_nan=False),
                                                encoding="utf-8")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data/world_model")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--groups", default="c1,c4,c6")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        run_pilot(args.data_dir, args.out_dir, args.summary, groups=args.groups.split(","),
                  epochs=args.epochs, seed=args.seed)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"[pilot failed] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
