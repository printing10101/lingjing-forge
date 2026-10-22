"""世界模型融合路径训练脚本（T1-2）.

流程
----
``trajectories npz`` → ``FusionTrajectoryDataset`` → DataLoader
（``fusion_collate_fn``）→ ``WorldModelNet(use_fusion=True)`` →
``FusionWorldModelTrainer.train()`` → ``save_checkpoint()``

产出
----
- checkpoint：``<WORLD_MODEL_MODELS_DIR>/world_model/<version>.pt``
  （默认 ``<repo>/data/models/world_model/<version>.pt``）；
- 训练报告：``<repo>/reports/wm_training_report_<timestamp>.json``；
- MLflow run（若环境已安装 mlflow，tracking 目录默认
  ``<repo>/data/mlruns``）。

用法（工作目录 ``engineering/python``）::

    python scripts/train_world_model.py \
        --dataset data/world_model/trajectories_v1.npz \
        --version 1.0.0 --epochs 40

退出码
------
0 — 训练完成且 checkpoint 已落盘
1 — 前置检查失败（数据/环境）
2 — 训练或保存异常
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PYTHON_ROOT.parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

import numpy as np  # noqa: E402

# MLflow tracking 统一放仓库 data/ 下。
# 项目 pin `mlflow==2.16.0`（research/requirements.txt），该版本原生支持文件后端；
# MLflow 3.x 才弃用文件后端，且 2.16 + 新版 sqlalchemy 的 sqlite 后端会因
# FallbackAsyncAdaptedQueuePool 缺失而报错，故这里用文件后端。
os.environ.setdefault(
    "MLFLOW_TRACKING_URI",
    f"file:///{(_REPO_ROOT / 'data' / 'mlruns').as_posix()}",
)

from app.plugins.world_model.net import WorldModelConfig, WorldModelNet  # noqa: E402
from app.plugins.world_model.training.fusion_dataset import (  # noqa: E402
    FusionTrajectoryDataset,
    fusion_collate_fn,
)
from app.plugins.world_model.training.fusion_trainer import (  # noqa: E402
    FusionWorldModelTrainer,
)

MODEL_URI_TEMPLATE = "model://world_model/{version}"


def load_dataset(npz_path: Path) -> tuple[list[dict], dict]:
    """加载 npz 并转成 ``FusionTrajectoryDataset`` 需要的样本 dict 列表."""
    if not npz_path.exists():
        raise FileNotFoundError(f"数据集不存在: {npz_path}")
    data = np.load(npz_path)
    required = {"geometry", "dynamics", "actions", "target"}
    missing = required - set(data.files)
    if missing:
        raise ValueError(f"数据集缺少字段: {sorted(missing)}")

    geo = data["geometry"]
    dyn = data["dynamics"]
    act = data["actions"]
    tgt = data["target"]
    # ADR-023：npz 含 initial_state 时透传（状态反馈训练）
    init = data["initial_state"] if "initial_state" in data.files else None

    samples = []
    for i in range(geo.shape[0]):
        sample = {
            "geometry_seq": geo[i],
            "dynamics_seq": dyn[i],
            "actions": act[i],
            "target_trajectory": tgt[i],
        }
        if init is not None:
            sample["initial_state"] = init[i]
        samples.append(sample)
    meta = {
        "samples": int(geo.shape[0]),
        "history": int(geo.shape[1]),
        "horizon": int(tgt.shape[1]),
        "geo_dim": int(geo.shape[2]),
        "dyn_dim": int(dyn.shape[2]),
        "action_dim": int(act.shape[2]),
        "state_dim": int(tgt.shape[2]),
        "has_initial_state": init is not None,  # ADR-023
    }
    meta_path = npz_path.with_suffix(".meta.json")
    if meta_path.exists():
        meta["provenance"] = json.loads(meta_path.read_text(encoding="utf-8")).get("provenance", "unknown")
    else:
        meta["provenance"] = "unknown"
    return samples, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="训练融合路径世界模型")
    parser.add_argument("--dataset", type=str, default="data/world_model/trajectories_v1.npz")
    parser.add_argument(
        "--val-dataset",
        type=str,
        default=None,
        help="独立验证集 npz（给定则不再随机切分，用于按实验组切分的真实数据）",
    )
    parser.add_argument(
        "--init-checkpoint",
        type=str,
        default=None,
        help="从已有 checkpoint 初始化权重（微调模式）",
    )
    parser.add_argument(
        "--freeze-geometry",
        action="store_true",
        help="冻结 GeometryEncoder（真实数据几何为常量占位时避免无约束漂移）",
    )
    parser.add_argument(
        "--delta-prediction",
        action="store_true",
        help="启用 Δ 状态预测（解码器预测相对上一状态的增量，改善慢漂移量）",
    )
    parser.add_argument("--version", type=str, default="1.0.0")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--models-dir", type=str, default=None)
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--no-amp", action="store_true", help="禁用混合精度（CPU 自动禁用）")
    parser.add_argument(
        "--loss-weights",
        type=str,
        default=None,
        help=(
            "按维损失权重：'auto' = 逆方差加权（常量维权重置 0），"
            "或逗号分隔的手动权重（如 1,20,5,1,1,1,1,1）"
        ),
    )
    args = parser.parse_args()

    try:
        import torch
        from torch.utils.data import DataLoader
    except ImportError:
        print("[train] torch 不可用，无法训练", file=sys.stderr)
        return 1

    device_desc = (
        f"{torch.__version__} cuda={torch.cuda.is_available()}"
        + (f" gpu={torch.cuda.get_device_name(0)}" if torch.cuda.is_available() else "")
    )
    print(f"[train] torch: {device_desc}", file=sys.stderr)

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = _REPO_ROOT / dataset_path
    try:
        samples, meta = load_dataset(dataset_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[train] 数据集加载失败: {exc}", file=sys.stderr)
        return 1

    if len(samples) < 50:
        print(f"[train] 样本过少（{len(samples)}），至少需要 50 条", file=sys.stderr)
        return 1

    horizon = meta["horizon"]
    geo_dim, dyn_dim = meta["geo_dim"], meta["dyn_dim"]
    action_dim, state_dim = meta["action_dim"], meta["state_dim"]
    feature_dim = geo_dim - 3 - 1 - 1
    print(
        f"[train] 数据: samples={len(samples)} T={meta['history']} H={horizon} "
        f"geo={geo_dim} dyn={dyn_dim} act={action_dim} state={state_dim} "
        f"provenance={meta.get('provenance')}",
        file=sys.stderr,
    )

    # 训练/验证切分：给了独立验证集就按数据源切分（真实数据按实验组，避免窗口泄漏），
    # 否则按固定种子随机切分（合成数据）
    if args.val_dataset:
        val_path = Path(args.val_dataset)
        if not val_path.is_absolute():
            val_path = _REPO_ROOT / val_path
        try:
            val_samples, val_meta = load_dataset(val_path)
        except (FileNotFoundError, ValueError) as exc:
            print(f"[train] 验证集加载失败: {exc}", file=sys.stderr)
            return 1
        train_samples = samples
        print(
            f"[train] 独立验证集: {val_path} samples={len(val_samples)} provenance={val_meta.get('provenance')}",
            file=sys.stderr,
        )
    else:
        rng = np.random.default_rng(args.seed)
        indices = rng.permutation(len(samples))
        val_size = max(1, int(round(len(samples) * args.val_ratio)))
        val_idx = set(indices[:val_size].tolist())
        train_samples = [s for i, s in enumerate(samples) if i not in val_idx]
        val_samples = [s for i, s in enumerate(samples) if i in val_idx]

    train_ds = FusionTrajectoryDataset(
        train_samples,
        geo_input_dim=geo_dim,
        dynamics_input_dim=dyn_dim,
        action_dim=action_dim,
        state_dim=state_dim,
    )
    val_ds = FusionTrajectoryDataset(
        val_samples,
        geo_input_dim=geo_dim,
        dynamics_input_dim=dyn_dim,
        action_dim=action_dim,
        state_dim=state_dim,
    )

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=fusion_collate_fn,
        num_workers=0,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=fusion_collate_fn,
        num_workers=0,
    )

    config = WorldModelConfig(
        state_dim=state_dim,
        action_dim=action_dim,
        use_fusion=True,
        feature_dim=feature_dim,
        delta_prediction=args.delta_prediction,
    )
    print(f"[train] delta_prediction={args.delta_prediction}", file=sys.stderr)
    model = WorldModelNet(config)

    # 按维损失权重：真实数据各维量级差异巨大（Ra 2–7 µm vs 磨损 0.1 mm），
    # 未加权 MSE 会被大量级维度主导。'auto' 用训练集目标方差做逆方差加权，
    # 零方差（常量）维权重置 0。
    loss_weights: list[float] | None = None
    if args.loss_weights:
        if args.loss_weights.strip().lower() == "auto":
            arr = np.stack([s["target_trajectory"] for s in train_samples]).astype(np.float64)
            std = arr.std(axis=(0, 1))
            floor = max(float(std.max()) * 1e-3, 1e-6)
            raw = np.where(std > floor, 1.0 / (std**2), 0.0)
            nonzero = raw[raw > 0]
            if nonzero.size:
                raw = raw / nonzero.mean()
            loss_weights = [round(float(min(w, 1000.0)), 4) for w in raw]
            print(f"[train] 自动损失权重(逆方差): std={[round(float(s),5) for s in std]}", file=sys.stderr)
            print(f"[train] loss_weights={loss_weights}", file=sys.stderr)
        else:
            loss_weights = [float(x) for x in args.loss_weights.split(",") if x.strip()]
            print(f"[train] 手动 loss_weights={loss_weights}", file=sys.stderr)

    # 微调：从已有 checkpoint 初始化（同一 config 维度必须一致，否则直接报错）
    init_info: dict = {}
    if args.init_checkpoint:
        ckpt_path = Path(args.init_checkpoint)
        if not ckpt_path.is_absolute():
            ckpt_path = _REPO_ROOT / ckpt_path
        if not ckpt_path.is_file():
            print(f"[train] 初始化 checkpoint 不存在: {ckpt_path}", file=sys.stderr)
            return 1
        payload = torch.load(ckpt_path, map_location="cpu", weights_only=True)
        state_dict = payload.get("model_state_dict", payload) if isinstance(payload, dict) else payload
        try:
            model.load_state_dict(state_dict)
        except RuntimeError as exc:
            if not args.delta_prediction:
                raise
            # Δ 模式下新增 delta_head，旧 checkpoint 没有该键：按 strict=False 加载，
            # 新头保持随机初始化（后续训练会学到）。其余键仍按名字匹配。
            result = model.load_state_dict(state_dict, strict=False)
            print(
                f"[train] 旧 checkpoint 无 delta_head，按 strict=False 加载；"
                f"missing={list(result.missing_keys)} unexpected={list(result.unexpected_keys)}",
                file=sys.stderr,
            )
        init_info = {"path": str(ckpt_path), "epoch": payload.get("epoch") if isinstance(payload, dict) else None}
        print(f"[train] 从 checkpoint 初始化: {ckpt_path}", file=sys.stderr)

    frozen_params = 0
    if args.freeze_geometry:
        geo_encoder = getattr(model, "geometry_encoder", None)
        if geo_encoder is None:
            print("[train] --freeze-geometry 但模型无 geometry_encoder（use_fusion=False？）", file=sys.stderr)
            return 1
        for param in geo_encoder.parameters():
            param.requires_grad = False
            frozen_params += param.numel()
        print(f"[train] 已冻结 GeometryEncoder: {frozen_params} 参数", file=sys.stderr)

    trainer = FusionWorldModelTrainer(
        model=model,
        model_uri=MODEL_URI_TEMPLATE.format(version=args.version),
        learning_rate=args.lr,
        epochs=args.epochs,
        early_stopping_patience=args.patience,
        device=args.device,
        use_amp=not args.no_amp,
        seed=args.seed,
        track_experiment=True,
        models_dir=args.models_dir,
        loss_weights=loss_weights,
    )

    started = time.perf_counter()
    try:
        history = trainer.train(train_loader, val_loader, horizon=horizon)
        elapsed = time.perf_counter() - started
        ckpt_path = trainer.save_checkpoint(
            version=args.version,
            metrics={
                "best_val_loss": float(trainer.best_val_loss),
                "epochs_run": float(trainer.current_epoch),
            },
        )
    except Exception as exc:  # noqa: BLE001 - 脚本入口需完整报错
        print(f"[train] 训练失败: {exc}", file=sys.stderr)
        return 2

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "path": str(dataset_path),
            "val_path": args.val_dataset,
            "samples": len(samples),
            "train": len(train_samples),
            "val": len(val_samples),
            "history": meta["history"],
            "horizon": horizon,
            "provenance": meta.get("provenance"),
        },
        "finetune": {
            "init_checkpoint": init_info or None,
            "frozen_geometry_params": frozen_params,
            "loss_weights": loss_weights,
        },
        "model": {
            "class": "WorldModelNet",
            "config": config.to_dict(),
            "params": int(sum(p.numel() for p in model.parameters())),
            "model_uri": MODEL_URI_TEMPLATE.format(version=args.version),
        },
        "training": {
            "device": str(trainer.device),
            "epochs_run": trainer.current_epoch,
            "best_val_loss": float(trainer.best_val_loss),
            "elapsed_seconds": round(elapsed, 2),
            "learning_rate": args.lr,
            "batch_size": args.batch_size,
            "amp": bool(trainer.scaler is not None),
            "history": {k: [float(x) for x in v] for k, v in history.items()},
        },
        "checkpoint": {
            "path": ckpt_path,
            "version": args.version,
        },
        "torch": device_desc,
    }

    report_dir = _REPO_ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"wm_training_report_{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[train] 完成: epochs={trainer.current_epoch} best_val_loss={trainer.best_val_loss:.6f} "
        f"time={elapsed:.1f}s ckpt={ckpt_path}",
        file=sys.stderr,
    )
    print(f"[train] 报告: {report_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
