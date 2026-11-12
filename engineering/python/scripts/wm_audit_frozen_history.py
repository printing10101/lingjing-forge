"""冻结权重的历史消融复算。现有验证集复用不构成独立最终测试。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

import numpy as np

PYTHON_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PYTHON_ROOT.parent.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

DIM_NAMES = ["chatter_p", "wear_mm", "Ra_um", "vib_rms", "temp/100", "force_norm", "mrr_norm", "confidence"]
ARRAY_NAMES = ("geometry", "dynamics", "actions", "target", "state_history")
MODES = ("full", "last_only", "repeat_last", "shuffle_past")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_arrays(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        missing = set(ARRAY_NAMES) - set(archive.files)
        if missing:
            raise ValueError(f"数据集缺少字段: {sorted(missing)}")
        arrays = {name: np.asarray(archive[name], dtype=np.float32) for name in ARRAY_NAMES}
    if any(value.ndim != 3 or not np.isfinite(value).all() for value in arrays.values()):
        raise ValueError("所有评估数组须为有限三维数值数组")
    geo, dyn, act, target, state = (arrays[name] for name in ARRAY_NAMES)
    n, history, geo_dim = geo.shape
    horizon = target.shape[1]
    if n <= 0 or history <= 0 or horizon <= 0 or geo_dim < 6:
        raise ValueError("样本、历史、预测长度及几何维度必须有效")
    expected = {"dynamics": (n, history, 6), "actions": (n, history + horizon, 4),
                "target": (n, horizon, 8), "state_history": (n, history, 8)}
    if any(arrays[name].shape != shape for name, shape in expected.items()):
        raise ValueError(f"评估数组形状不匹配，期望: {expected}")
    # 避免持久性基线误用错位或不同口径的末时刻状态。
    if not (np.allclose(dyn[:, :, 3], state[:, :, 1]) and np.allclose(dyn[:, :, 4], state[:, :, 3])
            and np.allclose(dyn[:, :, 5] / 100, state[:, :, 4])):
        raise ValueError("state_history与动力学的磨损、振动或温度通道不一致")
    return arrays


def window_hashes(arrays: dict[str, np.ndarray]) -> list[str]:
    return [hashlib.sha256(b"".join(np.ascontiguousarray(arrays[name][index]).tobytes()
                                  for name in ARRAY_NAMES)).hexdigest()
            for index in range(arrays["target"].shape[0])]


def split_metadata(path: Path) -> dict:
    candidates = [path.with_suffix(".meta.json")]
    for suffix in ("_train", "_val", "_test"):
        if path.stem.endswith(suffix):
            candidates.append(path.with_name(path.stem[:-len(suffix)] + ".meta.json"))
    for candidate in candidates:
        if candidate.is_file():
            value = json.loads(candidate.read_text(encoding="utf-8"))
            return {"path": str(candidate.resolve()), "sha256": file_hash(candidate), "content": value}
    return {"path": None, "content": {}, "sha256": None}


def ablation_inputs(arrays: dict[str, np.ndarray], mode: str, seed: int) -> tuple[np.ndarray, ...]:
    geo, dyn, act = (arrays[name].copy() for name in ("geometry", "dynamics", "actions"))
    history = dyn.shape[1]
    if mode == "last_only":
        return geo[:, -1:], dyn[:, -1:], np.concatenate([act[:, history - 1:history], act[:, history:]], axis=1)
    if mode == "repeat_last":
        # 保持时长、几何和动作，只移除更早动力学信息。
        dyn[:] = dyn[:, -1:, :]
    elif mode == "shuffle_past":
        rng = np.random.default_rng(seed)
        for index in range(dyn.shape[0]):
            order = np.r_[rng.permutation(history - 1), history - 1]
            geo[index] = geo[index, order]
            dyn[index] = dyn[index, order]
            act[index, :history] = act[index, order]
    elif mode != "full":
        raise ValueError(f"未知消融: {mode}")
    return geo, dyn, act


def predict_modes(model, arrays: dict[str, np.ndarray], *, feedback: bool, batch_size: int, seed: int) -> dict[str, np.ndarray]:
    import torch

    if batch_size <= 0:
        raise ValueError("batch_size必须为正数")
    model.cpu().eval()
    predictions = {}
    horizon = arrays["target"].shape[1]
    for mode in MODES:
        geo, dyn, act = ablation_inputs(arrays, mode, seed)
        batches = []
        with torch.inference_mode():
            for start in range(0, len(geo), batch_size):
                region = slice(start, start + batch_size)
                output = model(states=None, actions=torch.from_numpy(act[region]), horizon=horizon,
                    unified_states=(torch.from_numpy(geo[region]), torch.from_numpy(dyn[region])),
                    initial_state=torch.from_numpy(arrays["state_history"][region, -1]) if feedback else None)
                batches.append(output["predicted_trajectory"].cpu().numpy())
        prediction = np.concatenate(batches)
        if prediction.shape != arrays["target"].shape or not np.isfinite(prediction).all():
            raise ValueError(f"{mode}预测形状或有限性不合格")
        predictions[mode] = prediction
    return predictions


def summarize_predictions(predictions: dict[str, np.ndarray], target: np.ndarray) -> dict:
    summary = {}
    persistence_mae = np.abs(predictions["persistence"].astype(np.float64) - target).mean(axis=(0, 1))
    for mode, prediction in predictions.items():
        error = np.abs(prediction.astype(np.float64) - target)
        mae = error.mean(axis=(0, 1))
        summary[mode] = {
            "mae_by_dim": dict(zip(DIM_NAMES, mae.tolist())),
            "mae_by_horizon_and_dim": error.mean(axis=0).tolist(),
            "window_mae_by_dim": error.mean(axis=1).tolist(),
            "prediction_std_by_dim": dict(zip(DIM_NAMES, prediction.astype(np.float64).std(axis=(0, 1)).tolist())),
            "prediction_std_across_windows_by_horizon_and_dim": prediction.astype(np.float64).std(axis=0).tolist(),
            "gain_vs_persistence_pct": {name: float((baseline - value) / baseline * 100) if baseline > 1e-12 else None
                                         for name, baseline, value in zip(DIM_NAMES, persistence_mae, mae)},
        }
        if mode != "full":
            summary[mode]["max_abs_change_vs_full_by_dim"] = dict(zip(DIM_NAMES,
                np.abs(prediction.astype(np.float64) - predictions["full"]).max(axis=(0, 1)).tolist()))
    return summary


def history_gradient_probe(model, arrays: dict[str, np.ndarray], *, feedback: bool) -> dict:
    """固定末条反馈，检查首8个窗口的力输出能否向历史动力学反传。"""
    import torch

    count = min(8, len(arrays["target"]))
    dynamics = torch.tensor(arrays["dynamics"][:count], requires_grad=True)
    model.cpu().eval()
    encoder_inputs = []
    encoder = getattr(model, "encoder", None)
    hook = encoder.register_forward_pre_hook(lambda _, args: encoder_inputs.append(args[0].detach())) if isinstance(encoder, torch.nn.LSTM) else None
    try:
        output = model(states=None, actions=torch.from_numpy(arrays["actions"][:count]),
            horizon=arrays["target"].shape[1], unified_states=(torch.from_numpy(arrays["geometry"][:count]), dynamics),
            initial_state=torch.from_numpy(arrays["state_history"][:count, -1]) if feedback else None)
    finally:
        if hook is not None:
            hook.remove()
    gradient = torch.autograd.grad(output["predicted_trajectory"][:, :, 5].sum(), dynamics)[0].detach().numpy()
    if not np.isfinite(gradient).all():
        raise ValueError("历史梯度探针出现非有限值")
    scale_probe = None
    if encoder_inputs:
        # LSTM首步初始h/c为零；这里仅记录输入仿射量级，不改输入尺度。
        first_gate_affine = torch.nn.functional.linear(encoder_inputs[0][:, 0], encoder.weight_ih_l0.detach(),
                                                       encoder.bias_ih_l0.detach()) + encoder.bias_hh_l0.detach()
        scale_probe = {"encoder_input_max_abs": float(encoder_inputs[0].abs().max()),
                       "first_step_gate_affine_max_abs": float(first_gate_affine.abs().max()),
                       "first_step_gate_affine_abs_gt20_fraction": float((first_gate_affine.abs() > 20).float().mean()),
                       "scope": "first LSTM layer, first time step, zero initial recurrent state; descriptive scale probe"}
    return {"windows": count, "scope": "first windows, normalized force summed over forecast steps; feedback fixed",
            "max_abs_gradient_by_dynamic": np.abs(gradient).max(axis=(0, 1)).tolist(),
            "nonzero_fraction": float(np.count_nonzero(gradient) / gradient.size),
            "scale_probe": scale_probe,
            "interpretation": "local float32 sensitivity only; zero gradient does not establish global causation"}


def evaluate(dataset: Path, weights: Path, train_dataset: Path, *, batch_size: int = 64, seed: int = 42) -> dict:
    import torch
    from app.plugins.world_model.net import WorldModelConfig, WorldModelNet

    arrays, train = load_arrays(dataset), load_arrays(train_dataset)
    if train["target"].shape[1:] != arrays["target"].shape[1:]:
        raise ValueError("训练与评估的预测目标形状不匹配")
    hashes, train_hashes = window_hashes(arrays), window_hashes(train)
    if set(hashes) & set(train_hashes):
        raise ValueError("训练与评估存在完全相同的窗口，拒绝复算")
    metadata = split_metadata(dataset)
    declared_split = metadata["content"].get("split", {})
    if set(declared_split.get("train", [])) & set(declared_split.get("val", [])):
        raise ValueError("元数据声明的训练与验证实验组重叠")
    checkpoint = torch.load(weights, map_location="cpu", weights_only=True)
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("model_config"), dict):
        raise ValueError("评估须使用含明确model_config的训练checkpoint，不能使用随机网络")
    schema = checkpoint.get("state_schema", "machining_v1" if checkpoint.get("trainer_type") == "FusionWorldModelTrainer" else None)
    if schema != "machining_v1":
        raise ValueError("评估需要明确的machining_v1输出口径")
    config = WorldModelConfig(**checkpoint["model_config"])
    config.validate()
    if not config.use_fusion or config.feature_dim + 5 != arrays["geometry"].shape[2] or config.state_dim != 8 or config.action_dim != 4:
        raise ValueError("checkpoint输入输出口径与评估数组不匹配")
    model = WorldModelNet(config)
    model.load_state_dict(checkpoint["model_state_dict"])
    feedback = bool(checkpoint.get("state_feedback", False))
    predictions = predict_modes(model, arrays, feedback=feedback, batch_size=batch_size, seed=seed)
    target = arrays["target"]
    predictions["persistence"] = np.repeat(arrays["state_history"][:, -1:], target.shape[1], axis=1)
    predictions["train_mean"] = np.broadcast_to(train["target"].astype(np.float64).mean(axis=(0, 1)), target.shape).copy()
    drift = (train["target"].astype(np.float64) - train["state_history"][:, -1:, :]).mean(axis=0)
    predictions["train_drift"] = arrays["state_history"][:, -1:, :] + drift[None, :, :]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "evaluation_kind": "exploratory_validation_reanalysis",
        "evaluator_sha256": file_hash(Path(__file__)),
        "independent_final_test_established": False, "weights_source": "checkpoint", "device": "cpu",
        "checkpoint": {"path": str(weights.resolve()), "sha256": file_hash(weights), "config": config.to_dict(),
                       "state_feedback": feedback, "model_selection": checkpoint.get("model_selection"),
                       "input_contract": checkpoint.get("input_contract"),
                       "loss_normalization": checkpoint.get("loss_normalization"),
                       "selection_loss_normalization": checkpoint.get("selection_loss_normalization")},
        "dataset": {"path": str(dataset.resolve()), "sha256": file_hash(dataset),
                    "n_windows": len(target), "history_steps": arrays["dynamics"].shape[1], "horizon": target.shape[1],
                    "duplicate_windows": len(hashes) - len(set(hashes)), "metadata": metadata,
                    "window_sha256": hashes},
        "train_dataset": {"path": str(train_dataset.resolve()), "sha256": file_hash(train_dataset),
                          "n_windows": len(train["target"]), "exact_window_overlap": 0,
                          "mean_baseline_fit_scope": "train_targets_only", "metadata": split_metadata(train_dataset)},
        "protocol": {"shuffle_seed": seed, "batch_size": batch_size, "torch": torch.__version__,
                     "independent_units": "experiment groups; windows and horizon steps are repeated measurements",
                     "feedback_control": "latest state fixed across all model ablations",
                     "last_only_limit": "changes history length; out-of-distribution diagnostic, not retrained baseline",
                     "train_mean_information": "fit using only training targets; no validation-target fitting",
                     "train_drift_information": "per-horizon mean target-minus-last-state fitted only on training windows",
                     "cross_channel_aggregate": "not reported because dimensions have different units",
                     "endpoint_scope": "wear, vibration and normalized force; other channels include proxies/constants"},
        "results": summarize_predictions(predictions, target),
        "history_gradient_probe": history_gradient_probe(model, arrays, feedback=feedback),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--train-dataset", required=True, type=Path)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise FileExistsError("输出已存在，请指定新文件，不覆盖历史结果")
        if args.out.resolve() in {path.resolve() for path in (args.dataset, args.train_dataset, args.weights)}:
            raise ValueError("结果路径不能指向输入资产")
        import torch
        torch.set_num_threads(1)
        report = evaluate(args.dataset, args.weights, args.train_dataset, batch_size=args.batch_size, seed=args.seed)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(f"冻结权重诊断已保存: {args.out}")
        return 0
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print(f"[评估失败] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
