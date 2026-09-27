"""注册表模型训练 + 权重导出脚本（接线方案 WP3）。

训练一个与工程侧 NumPy 模型 2D 批量前向**逐层同构**的 MLP（ReLU 隐层 +
线性输出），把特征/目标标准化折叠进首末层权重后导出 v2 npz——服务运行时
零改动加载，训练-服务架构严格一致（不存在「训练一个函数、服务另一个」）。

LTC/CFC 的 research torch 变体（LTC 细胞/CFC 门控）保留用于科研实验；
注册表模型的服务行为由工程侧 NumPy 实现定义，训练必须对齐它。

产物（--out-dir，缺省 models/lnn/）：
    <name>.npz            v2 权重（is_trained=True，registry 可直接加载）
    <name>.manifest.json  数据血缘/标准化参数/parity/回归指标/训练环境

用法（PY314，含 torch CPU 即可）：
    python scripts/train_registry_model.py cutting_force \
        --dataset-dir data/training_sets/cutting_force
    python scripts/train_registry_model.py wear_prediction \
        --csv ../research/datasets/uniwear/uniwear/uniwear.csv \
        --features force_z,vibration_x,vibration_y --target tool_wear --time-col timestamp
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_PYTHON_DIR = Path(__file__).resolve().parent.parent
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))

from app.ai.lnn.models.cfc_model import CFCModel  # noqa: E402
from app.ai.lnn.models.ltc_model import LTCModel  # noqa: E402
from app.training.weight_bridge import (  # noqa: E402
    fit_standardization,
    fold_standardization,
    numpy_mirror_forward,
    regression_metrics,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("train-registry-model")

#: 模型名 → NumPy 模型类（2D 批量前向均为 ReLU 隐层 + 线性输出的同构 MLP）
MODEL_CLASS_MAP = {
    "cutting_force": CFCModel,
    "wear_prediction": LTCModel,
}

#: 训练超参（小网络小数据，CPU 秒级）
DEFAULTS = {
    "hidden_dim": 64,
    "num_layers": 2,
    "epochs": 500,
    "batch_size": 64,
    "learning_rate": 1e-3,
    "patience": 30,
    "seed": 42,
    "val_ratio": 0.15,
    "test_ratio": 0.15,
    "parity_tolerance": 1e-3,
}


def _load_dataset(args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray, dict]:
    """返回 (X, y, source_info)。"""
    if args.dataset_dir:
        d = Path(args.dataset_dir)
        x = np.load(d / "X.npy")
        y = np.load(d / "y.npy").reshape(-1)
        manifest = json.loads((d / "feature_manifest.json").read_text(encoding="utf-8"))
        return x, y, {"kind": "dataset_store_export", **manifest.get("source", {})}

    if not (args.csv and args.features and args.target):
        raise SystemExit("数据源二选一：--dataset-dir 或 (--csv + --features + --target)")
    csv_path = Path(args.csv)
    feature_cols = [c.strip() for c in args.features.split(",")]

    # csv.DictReader 而非 genfromtxt：公开数据集常带未命名索引列/BOM，
    # 列数严格对齐的 ndarray 解析会在这类文件上整批失败
    import csv

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = [(name or "").strip().lstrip("\ufeff") for name in (reader.fieldnames or [])]
        rows = list(reader)
    cols = fieldnames

    def _col(name: str) -> np.ndarray:
        if name not in cols:
            raise SystemExit(f"列不存在: {name}（可用列 {cols}）")
        key_idx = cols.index(name)
        raw_key = (reader.fieldnames or [])[key_idx]
        return np.asarray([float(row[raw_key]) for row in rows], dtype=np.float64)

    xs = [_col(c) for c in feature_cols]
    if args.time_col:
        xs.append(_col(args.time_col))
        feature_cols = feature_cols + ["time_s"]
    x = np.column_stack(xs)
    y = _col(args.target).reshape(-1)
    keep = ~(np.isnan(x).any(axis=1) | np.isnan(y))
    dropped = int((~keep).sum())
    x, y = x[keep], y[keep]
    source = {
        "kind": "csv",
        "path": str(csv_path),
        "sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest()[:16],
        "feature_columns": feature_cols,
        "target_column": args.target,
        "rows_dropped_nan": dropped,
    }
    return x, y, source


def _split(x: np.ndarray, y: np.ndarray, seed: int, val_ratio: float, test_ratio: float):
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(x))
    n_test = max(1, int(len(x) * test_ratio))
    n_val = max(1, int(len(x) * val_ratio))
    test_idx, val_idx, train_idx = idx[:n_test], idx[n_test : n_test + n_val], idx[n_test + n_val :]
    return (x[train_idx], y[train_idx]), (x[val_idx], y[val_idx]), (x[test_idx], y[test_idx])


def _train_torch_mlp(
    layer_dims: list[int],
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    hp: dict,
):
    """标准化空间内训练镜像 MLP，返回 (state_dict, layer_dims, history)。"""
    import torch
    from torch import nn

    torch.manual_seed(hp["seed"])
    np.random.seed(hp["seed"])

    layers: list[nn.Module] = []
    for i in range(len(layer_dims) - 1):
        layers.append(nn.Linear(layer_dims[i], layer_dims[i + 1]))
        if i < len(layer_dims) - 2:
            layers.append(nn.ReLU())
    model = nn.Sequential(*layers)

    xt = torch.tensor(x_train, dtype=torch.float32)
    yt = torch.tensor(y_train.reshape(-1, 1), dtype=torch.float32)
    xv = torch.tensor(x_val, dtype=torch.float32)
    yv = torch.tensor(y_val.reshape(-1, 1), dtype=torch.float32)

    optimizer = torch.optim.AdamW(model.parameters(), lr=hp["learning_rate"], weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=hp["epochs"], eta_min=1e-6)
    criterion = nn.MSELoss()

    history = {"train_loss": [], "val_loss": []}
    best_val, best_state, patience = float("inf"), None, 0
    generator = torch.Generator().manual_seed(hp["seed"])
    for epoch in range(hp["epochs"]):
        model.train()
        perm = torch.randperm(len(xt), generator=generator)
        epoch_loss = 0.0
        for start in range(0, len(xt), hp["batch_size"]):
            batch_idx = perm[start : start + hp["batch_size"]]
            optimizer.zero_grad()
            loss = criterion(model(xt[batch_idx]), yt[batch_idx])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += float(loss.item()) * len(batch_idx)
        epoch_loss /= len(xt)
        scheduler.step()

        model.eval()
        with torch.no_grad():
            val_loss = float(criterion(model(xv), yv).item())
        history["train_loss"].append(epoch_loss)
        history["val_loss"].append(val_loss)
        if val_loss < best_val - 1e-12:
            best_val, patience = val_loss, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            patience += 1
            if patience >= hp["patience"]:
                logger.info("早停于 epoch %d（best val_loss=%.6g）", epoch, best_val)
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, history


def _torch_to_numpy_arrays(model, num_layers: int) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """torch Sequential(Linear,ReLU,...) → NumPy 权重（(in,out) 排布）。"""
    linears = [m for m in model if hasattr(m, "weight")]
    assert len(linears) == num_layers, f"Linear 层数不符: {len(linears)} != {num_layers}"
    weights = [m.weight.detach().cpu().numpy().astype(np.float64).T for m in linears]
    biases = [m.bias.detach().cpu().numpy().astype(np.float64) for m in linears]
    return weights, biases


def _assign_to_registry_model(np_model, weights, biases):
    """把折叠后的权重赋给注册表 NumPy 模型并置 trained。"""
    np_model.build()
    np_model.weights = [np.asarray(w, dtype=np.float64) for w in weights]
    np_model.biases = [np.asarray(b, dtype=np.float64) for b in biases]
    if isinstance(np_model, LTCModel):
        # memory_weights 仅服务时序路径（本批次回归任务不经过），
        # 保留种子化随机初始化作为结构占位——manifest 中显式声明
        np.random.seed(DEFAULTS["seed"] + 1)
        if not np_model.memory_weights:
            np_model.memory_weights = [
                np.random.randn(np_model.hidden_dim, np_model.memory_size) * 0.1,
                np.random.randn(np_model.memory_size, np_model.hidden_dim) * 0.1,
            ]
            np_model.memory_state = np.zeros((1, np_model.memory_size))
    np_model.is_trained = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model_name", choices=sorted(MODEL_CLASS_MAP))
    parser.add_argument("--dataset-dir", default=None, help="WP2 导出目录（X.npy/y.npy/feature_manifest.json）")
    parser.add_argument("--csv", default=None, help="CSV 数据源（uniwear 等）")
    parser.add_argument("--features", default=None, help="逗号分隔特征列（--csv 模式）")
    parser.add_argument("--target", default=None, help="目标列（--csv 模式）")
    parser.add_argument("--time-col", default=None, help="时间戳列名（追加为 time_s 特征）")
    parser.add_argument(
        "--out-dir",
        default=str(_PYTHON_DIR / "models" / "lnn"),
        help="产物目录（缺省 engineering/python/models/lnn）",
    )
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--hidden-dim", type=int, default=DEFAULTS["hidden_dim"])
    parser.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    args = parser.parse_args()

    from app.ai.lnn.inference._lnn_registry import LNNModelRegistry

    info = LNNModelRegistry.PREDEFINED_MODELS[args.model_name]
    feature_order = list(info.input_features)
    input_dim = len(feature_order)
    layer_dims = [input_dim] + [args.hidden_dim] * DEFAULTS["num_layers"] + [1]

    x, y, source = _load_dataset(args)
    if x.shape[1] != input_dim:
        raise SystemExit(f"数据特征维度 {x.shape[1]} 与注册表契约 {input_dim} 不符（{feature_order}）")
    logger.info("数据: X%s y%s source=%s", x.shape, y.shape, source.get("kind"))

    (x_tr, y_tr), (x_val, y_val), (x_te, y_te) = _split(
        x, y, args.seed, DEFAULTS["val_ratio"], DEFAULTS["test_ratio"]
    )
    x_mean, x_std, y_mean, y_std = fit_standardization(x_tr, y_tr)
    logger.info(
        "标准化(train): x_mean=%s x_std=%s y_mean=%.4g y_std=%.4g",
        np.round(x_mean, 3).tolist(),
        np.round(x_std, 3).tolist(),
        y_mean,
        y_std,
    )

    try:
        import torch  # noqa: F401

        torch_version = torch.__version__
    except ImportError as e:
        raise SystemExit(f"训练需要 PyTorch（当前环境缺失: {e}）。运行时推理不需要。") from e

    model, history = _train_torch_mlp(
        layer_dims, (x_tr - x_mean) / x_std, (y_tr - y_mean) / y_std, (x_val - x_mean) / x_std, (y_val - y_mean) / y_std,
        {**DEFAULTS, "seed": args.seed, "epochs": args.epochs},
    )

    weights, biases = _torch_to_numpy_arrays(model, len(layer_dims) - 1)

    # ── parity 1：torch 前向 vs NumPy 镜像前向（标准化空间，架构同构证明）──
    probe = ((x_te[:64] - x_mean) / x_std).astype(np.float32)
    with __import__("torch").no_grad():
        y_torch = model(__import__("torch").tensor(probe)).numpy().astype(np.float64)
    y_mirror = numpy_mirror_forward(probe.astype(np.float64), weights, biases)
    parity_max = float(np.max(np.abs(y_torch - y_mirror)))
    logger.info("parity(torch vs numpy mirror): max_abs=%.3g（阈值 %g）", parity_max, DEFAULTS["parity_tolerance"])
    if parity_max > DEFAULTS["parity_tolerance"]:
        raise SystemExit(f"parity 超限: {parity_max:.3g} > {DEFAULTS['parity_tolerance']}")

    # ── 折叠标准化 → 原始输入空间的同构模型 ──
    fw, fb = fold_standardization(weights, biases, x_mean, x_std, y_mean, y_std)

    np_model = MODEL_CLASS_MAP[args.model_name](
        model_name=args.model_name, input_dim=input_dim, output_dim=1, **{"hidden_dim": args.hidden_dim}
    )
    _assign_to_registry_model(np_model, fw, fb)

    # ── 指标：原始输入空间、NumPy 服务路径（与服务行为一致）──
    y_pred = np_model.predict(x_te).reshape(-1)
    metrics = regression_metrics(y_te, y_pred)
    metrics_val = regression_metrics(y_val, np_model.predict(x_val).reshape(-1))
    logger.info("test(raw, numpy path): %s", metrics)
    logger.info("val(raw, numpy path): r2=%.4f mae=%.4g", metrics_val["r2"], metrics_val["mae"])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    npz_path = out_dir / f"{args.model_name}.npz"
    np_model.config["feature_order"] = feature_order
    np_model.config["folded_standardization"] = True
    np_model.save(str(npz_path))

    manifest = {
        "model_name": args.model_name,
        "registry_version": info.version,
        "model_type": info.model_type,
        "feature_order": feature_order,
        "output_features": list(info.output_features),
        "npz_format_version": 2,
        "source": source,
        "data": {
            "rows_total": int(len(x)),
            "rows_train": int(len(x_tr)),
            "rows_val": int(len(x_val)),
            "rows_test": int(len(x_te)),
            "standardization": {
                "x_mean": x_mean.tolist(),
                "x_std": x_std.tolist(),
                "y_mean": y_mean,
                "y_std": y_std,
                "folded_into_weights": True,
            },
        },
        "training": {
            "framework": "torch",
            "torch_version": torch_version,
            "architecture": f"MirrorMLP {layer_dims}（ReLU 隐层+线性输出，与 NumPy 服务前向同构）",
            "seed": args.seed,
            "epochs_requested": args.epochs,
            "epochs_completed": len(history["train_loss"]),
            "learning_rate": DEFAULTS["learning_rate"],
            "batch_size": DEFAULTS["batch_size"],
            "patience": DEFAULTS["patience"],
            "final_train_loss": history["train_loss"][-1],
            "best_val_loss": min(history["val_loss"]),
        },
        "parity": {
            "torch_vs_numpy_mirror_max_abs": parity_max,
            "tolerance": DEFAULTS["parity_tolerance"],
        },
        "metrics_numpy_path": {"test": metrics, "val": metrics_val},
        "notes": (
            ("LTC memory_weights 为结构占位（未参与本批量回归任务训练）；" if args.model_name == "wear_prediction" else "")
            + "权重为标准化折叠后产物，服务端无需任何预处理；"
            + "ONNX 工件因环境缺 onnx 包暂缓导出（onnxruntime 消费路径见 WP1 适配器）。"
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = out_dir / f"{args.model_name}.manifest.json"
    manifest["artifacts"] = {
        "npz": {"path": npz_path.name, "sha256": hashlib.sha256(npz_path.read_bytes()).hexdigest()},
        "manifest": {"path": manifest_path.name},
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 终验：注册表以生产默认参数加载 npz → weights_source 必须 trained ──
    registry = LNNModelRegistry(model_dir=str(out_dir))
    verdict = registry.validate_model(args.model_name)
    logger.info("registry validate: %s", verdict)
    if verdict.get("weights_source") != "trained":
        raise SystemExit(f"终验失败：weights_source={verdict.get('weights_source')}（应为 trained）")

    print(json.dumps({"metrics_test": metrics, "parity_max_abs": parity_max, "npz": str(npz_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
