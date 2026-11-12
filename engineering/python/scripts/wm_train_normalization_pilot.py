"""匹配原量纲/标准化重训，不覆盖旧权重，不自动切换生产版本。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np

from train_world_model import load_dataset
from wm_audit_frozen_history import evaluate, file_hash, load_arrays
from app.plugins.world_model.input_normalization import fit_fusion_normalization
from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
from app.plugins.world_model.training.fusion_dataset import FusionTrajectoryDataset, fusion_collate_fn
from app.plugins.world_model.training.fusion_trainer import FusionWorldModelTrainer

REPO_ROOT = Path(__file__).resolve().parents[3]


def run_pilot(data_dir: Path, output_dir: Path, *, groups: list[str], epochs: int, seed: int, budget_seconds: float) -> dict:
    import torch
    from torch.utils.data import DataLoader

    if output_dir.exists():
        raise FileExistsError("实验目录已存在，请用新目录，不能覆盖历史模型")
    if not groups or len(set(groups)) != len(groups) or any(tag not in ("c1", "c4", "c6") for tag in groups):
        raise ValueError("实验组须为c1/c4/c6的无重复子集")
    if epochs <= 0 or budget_seconds <= 0:
        raise ValueError("训练轮数和预算必须为正数")
    torch.set_num_threads(1)
    # 前置检查不创建输出目录，输入失败不会留下看似完成的实验。
    for tag in groups:
        for split in ("train", "val"):
            load_arrays(data_dir / f"cv_{tag}_{split}.npz")
    output_dir.mkdir(parents=True)
    ledger = {"kind": "exploratory_normalization_pilot", "independent_final_test_established": False,
              "seed": seed, "epochs_per_run": epochs, "batch_size": 32, "learning_rate": 0.001,
              "device": "cpu", "groups": groups, "budget_seconds": budget_seconds, "runs": []}
    started = time.perf_counter()
    try:
        for tag in groups:
            training_path = data_dir / f"cv_{tag}_train.npz"
            validation_path = data_dir / f"cv_{tag}_val.npz"
            training, validation = load_arrays(training_path), load_arrays(validation_path)
            samples, meta = load_dataset(training_path)
            val_samples, _ = load_dataset(validation_path)
            if any("initial_state" not in sample for sample in samples + val_samples):
                raise ValueError("匹配反馈实验要求每个样本含实际末条initial_state")
            with np.load(training_path, allow_pickle=False) as archive:
                if not np.array_equal(archive["initial_state"], training["state_history"][:, -1]):
                    raise ValueError("训练反馈与末条历史状态不一致")
            with np.load(validation_path, allow_pickle=False) as archive:
                if not np.array_equal(archive["initial_state"], validation["state_history"][:, -1]):
                    raise ValueError("验证反馈与末条历史状态不一致")
            for mode in ("raw", "zscore"):
                if time.perf_counter() - started > budget_seconds:
                    raise TimeoutError("实验达到预定时间预算，保留已完成项")
                normalization = fit_fusion_normalization(training["geometry"], training["dynamics"]) if mode == "zscore" else None
                config = WorldModelConfig(feature_dim=meta["geo_dim"] - 5, seed=seed, delta_prediction=True,
                                          input_normalization=normalization)
                model = WorldModelNet(config)
                initial_identity = hashlib.sha256(b"".join(tensor.detach().numpy().tobytes()
                    for _, tensor in model.named_parameters())).hexdigest()
                version = f"normpilot-{tag}-{mode}-s{seed}"
                trainer = FusionWorldModelTrainer(model, model_uri=f"model://world_model/{version}", epochs=epochs,
                    early_stopping_patience=epochs, seed=seed, device="cpu", use_amp=False,
                    track_experiment=False, models_dir=str(output_dir / "models"))
                train_loader = DataLoader(FusionTrajectoryDataset(samples, geo_input_dim=meta["geo_dim"]), batch_size=32,
                    shuffle=True, collate_fn=fusion_collate_fn, generator=torch.Generator().manual_seed(seed))
                val_loader = DataLoader(FusionTrajectoryDataset(val_samples, geo_input_dim=meta["geo_dim"]), batch_size=32,
                    shuffle=False, collate_fn=fusion_collate_fn)
                run_started = time.perf_counter()
                trainer.train(train_loader, val_loader, horizon=meta["horizon"])
                path = Path(trainer.save_checkpoint(version))
                audit = evaluate(validation_path, path, training_path, seed=seed)
                audit_path = output_dir / f"{version}.audit.json"
                with audit_path.open("x", encoding="utf-8") as stream:
                    json.dump(audit, stream, ensure_ascii=False, indent=2, allow_nan=False)
                ledger["runs"].append({"group": tag, "mode": mode, "version": version,
                    "initial_parameters_sha256": initial_identity, "trainable_parameters": sum(p.numel() for p in model.parameters()),
                    "checkpoint": str(path), "checkpoint_sha256": file_hash(path),
                    "training_dataset_sha256": file_hash(training_path), "validation_dataset_sha256": file_hash(validation_path),
                    "audit_path": str(audit_path), "audit_sha256": file_hash(audit_path),
                    "elapsed_seconds": time.perf_counter() - run_started, "selection": trainer.get_training_summary()})
                print(f"[pilot] {version} completed; epoch={trainer.selected_epoch}; elapsed={time.perf_counter() - run_started:.1f}s", flush=True)
        ledger["status"] = "completed"
    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
        ledger["status"] = "failed"
        ledger["error"] = str(exc)
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
