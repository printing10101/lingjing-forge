"""输入标准化固定、零增量初始化固定：损失尺度×增量单位2×2诊断。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import numpy as np

from train_world_model import load_dataset
from wm_audit_frozen_history import evaluate, file_hash, load_arrays, window_hashes
from app.plugins.world_model.input_normalization import fit_fusion_normalization
from app.plugins.world_model.state_loss_normalization import fit_state_scale
from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
from app.plugins.world_model.training.fusion_dataset import FusionTrajectoryDataset, fusion_collate_fn
from app.plugins.world_model.training.fusion_trainer import FusionWorldModelTrainer

REPO_ROOT = Path(__file__).resolve().parents[3]


def run_pilot(data_dir: Path, output_dir: Path, *, groups: list[str], epochs: int, seed: int, budget_seconds: float) -> dict:
    import torch
    from torch.utils.data import DataLoader

    if output_dir.exists():
        raise FileExistsError("输出实验目录已存在，请使用新目录")
    if not groups or len(set(groups)) != len(groups) or any(group not in ("c1", "c4", "c6") for group in groups):
        raise ValueError("组须为c1/c4/c6的无重复子集")
    if epochs <= 0 or not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("轮数和时间预算必须为正有限数")
    torch.set_num_threads(1)
    for group in groups:
        training = load_arrays(data_dir / f"cv_{group}_train.npz")
        validation = load_arrays(data_dir / f"cv_{group}_val.npz")
        if set(window_hashes(training)) & set(window_hashes(validation)):
            raise ValueError("训练与验证存在相同窗口")
        for split, arrays in (("train", training), ("val", validation)):
            with np.load(data_dir / f"cv_{group}_{split}.npz", allow_pickle=False) as archive:
                if "initial_state" not in archive.files or not np.array_equal(archive["initial_state"], arrays["state_history"][:, -1]):
                    raise ValueError("反馈必须是窗口实际末条状态")
    output_dir.mkdir(parents=True)
    ledger = {"kind": "exploratory_state_scale_factorial", "independent_final_test_established": False,
        "seed": seed, "groups": groups, "epochs_per_run": epochs, "batch_size": 32, "learning_rate": 0.001,
        "device": "cpu", "zero_initial_delta_head": True, "common_selection": "train_persistence_rmse",
        "budget_seconds": budget_seconds, "runs": []}
    started = time.perf_counter()
    try:
        for group in groups:
            train_path, val_path = (data_dir / f"cv_{group}_{split}.npz" for split in ("train", "val"))
            training = load_arrays(train_path)
            samples, meta = load_dataset(train_path)
            val_samples, _ = load_dataset(val_path)
            state_scale = fit_state_scale(training["target"], training["state_history"][:, -1])
            normalization = fit_fusion_normalization(training["geometry"], training["dynamics"])
            for head_mode, loss_mode in (("physical", "physical"), ("physical", "relative"),
                                         ("relative", "physical"), ("relative", "relative")):
                if time.perf_counter() - started > budget_seconds:
                    raise TimeoutError("达到预算，保留已完成项，不自动重试")
                version = f"statescale-{group}-head-{head_mode}-loss-{loss_mode}-s{seed}"
                config = WorldModelConfig(feature_dim=meta["geo_dim"] - 5, seed=seed, delta_prediction=True,
                    input_normalization=normalization, delta_output_normalization=state_scale if head_mode == "relative" else None)
                model = WorldModelNet(config)
                torch.nn.init.zeros_(model.delta_head.weight)
                torch.nn.init.zeros_(model.delta_head.bias)
                identity = hashlib.sha256(b"".join(parameter.detach().numpy().tobytes() for parameter in model.parameters())).hexdigest()
                model.eval()
                with torch.inference_mode():
                    count = min(8, len(samples))
                    initial = torch.from_numpy(training["state_history"][:count, -1])
                    prediction = model(states=None, actions=torch.from_numpy(training["actions"][:count]), horizon=meta["horizon"],
                        unified_states=(torch.from_numpy(training["geometry"][:count]), torch.from_numpy(training["dynamics"][:count])),
                        initial_state=initial)["predicted_trajectory"]
                    initial_error = float((prediction - initial[:, None]).abs().max())
                if initial_error != 0:
                    raise ValueError("零增量初始化未达到精确持久性预测")
                trainer = FusionWorldModelTrainer(model, model_uri=f"model://world_model/{version}", epochs=epochs,
                    early_stopping_patience=epochs, seed=seed, device="cpu", use_amp=False, track_experiment=False,
                    models_dir=str(output_dir / "models"), loss_normalization=state_scale if loss_mode == "relative" else None,
                    selection_loss_normalization=state_scale)
                train_loader = DataLoader(FusionTrajectoryDataset(samples, geo_input_dim=meta["geo_dim"]), batch_size=32,
                    shuffle=True, collate_fn=fusion_collate_fn, generator=torch.Generator().manual_seed(seed))
                val_loader = DataLoader(FusionTrajectoryDataset(val_samples, geo_input_dim=meta["geo_dim"]), batch_size=32,
                    shuffle=False, collate_fn=fusion_collate_fn)
                run_started = time.perf_counter()
                trainer.train(train_loader, val_loader, horizon=meta["horizon"])
                checkpoint = Path(trainer.save_checkpoint(version))
                audit = evaluate(val_path, checkpoint, train_path, seed=seed)
                audit_path = output_dir / f"{version}.audit.json"
                with audit_path.open("x", encoding="utf-8") as stream:
                    json.dump(audit, stream, ensure_ascii=False, indent=2, allow_nan=False)
                ledger["runs"].append({"group": group, "head_mode": head_mode, "loss_mode": loss_mode,
                    "version": version, "initial_parameters_sha256": identity, "initial_persistence_max_error": initial_error,
                    "trainable_parameters": sum(parameter.numel() for parameter in model.parameters()),
                    "checkpoint": str(checkpoint), "checkpoint_sha256": file_hash(checkpoint),
                    "training_dataset_sha256": file_hash(train_path), "validation_dataset_sha256": file_hash(val_path),
                    "audit_path": str(audit_path), "audit_sha256": file_hash(audit_path),
                    "selection": trainer.get_training_summary(), "elapsed_seconds": time.perf_counter() - run_started})
                print(f"[pilot] {version} epoch={trainer.selected_epoch} completed", flush=True)
        ledger["status"] = "completed"
    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
        ledger["status"], ledger["error"] = "failed", str(exc)
        raise
    finally:
        ledger["elapsed_seconds"] = time.perf_counter() - started
        (output_dir / "ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data/world_model")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--groups", default="c1,c4,c6")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--budget-seconds", type=float, default=300)
    args = parser.parse_args()
    try:
        run_pilot(args.data_dir, args.out_dir, groups=args.groups.split(","), epochs=args.epochs,
                  seed=args.seed, budget_seconds=args.budget_seconds)
        return 0
    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
        print(f"[pilot failed] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
