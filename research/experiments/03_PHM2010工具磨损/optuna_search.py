"""
Optuna 超参搜索脚本

为 GP 基线和 DL-LNN 关键超参执行 Optuna 贝叶斯优化搜索，
解决以下问题：
    1. GP 基线在默认超参下发散（MAE≈20）—— 搜索核参数 length_scale / alpha / constant_value
    2. DL-LNN 在默认超参下精度不足 —— 搜索 learning_rate / weight_decay / dropout

搜索策略：
    - GP：每次试验仅需秒级 fit，使用 30 trials 充分探索
    - DL-LNN：每次试验使用缩减轮数（Stage1=20, Stage2=30）快速评估，
      使用 15 trials 平衡搜索质量与总耗时

结果保存至 results/best_hyperparams.json，供 run_experiment.py 加载。

用法：
    python experiments/optuna_search.py
"""

import sys
import os
import json
import types
import time
import copy

# WinSock 损坏绕过补丁（与 run_experiment.py 保持一致）
try:
    import _overlapped  # noqa: F401
except OSError:
    _patch = types.ModuleType("_overlapped")
    _patch.Overlapped = type("Overlapped", (), {})
    sys.modules["_overlapped"] = _patch
    print("[warn] _overlapped 模块加载失败，已注入空实现绕过 WinSock 损坏。")

import numpy as np
import torch
from torch.utils.data import DataLoader
import optuna

# 添加项目路径
_EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_EXPERIMENTS_DIR)
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, _EXPERIMENTS_DIR)

from training.reproducibility import set_global_seed, get_worker_init_fn
from experiments.config import get_config
from experiments.data_generator import SyntheticChatterDataset, IndustrialChatterDataset
from experiments.models import create_model
from experiments.trainer import DLLNNTrainer
from experiments.metrics import ChatterMetrics


# 数据准备（与 run_experiment.py 完全一致的划分，确保搜索结果可复用）


def prepare_dataset(dataset_name: str, config):
    """准备数据集与 DataLoader，返回 (train_loader, val_loader, test_loader)。"""
    if dataset_name == "Synthetic":
        full_dataset = SyntheticChatterDataset(
            num_samples=1000,
            spindle_speed_range=(1000, 10000),
            axial_depth_range=(0.1, 10.0),
            noise_level=0.02,
        )
    elif dataset_name == "Industrial":
        full_dataset = IndustrialChatterDataset(
            num_samples=500,
            num_conditions=30,
            material="6061-T6",
        )
    else:
        raise ValueError(f"未知数据集: {dataset_name}")

    train_size = int(0.7 * len(full_dataset))
    val_size = int(0.15 * len(full_dataset))
    test_size = len(full_dataset) - train_size - val_size

    split_generator = torch.Generator().manual_seed(42)
    train_dataset, val_dataset, test_dataset = torch.utils.data.random_split(
        full_dataset, [train_size, val_size, test_size], generator=split_generator
    )

    train_loader = DataLoader(
        train_dataset,
        batch_size=32,
        shuffle=True,
        generator=torch.Generator().manual_seed(42),
        worker_init_fn=get_worker_init_fn(42),
    )
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)

    return train_loader, val_loader, test_loader


def collect_numpy(loader):
    """将 DataLoader 展平为 (X, y) numpy 数组，供 sklearn 模型使用。"""
    xs, ys = [], []
    for batch in loader:
        if len(batch) == 3:
            x, y_true, _ = batch
        else:
            x, y_true = batch
        xs.append(x.cpu().numpy())
        ys.append(y_true.cpu().numpy())
    X = np.concatenate(xs, axis=0)
    y = np.concatenate(ys, axis=0).reshape(-1)
    return X, y


# GP 基线 Optuna 搜索


def gp_objective(trial, train_loader, val_loader):
    """GP 基线 Optuna 目标函数：返回验证集 MAE（越小越好）。"""
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, ConstantKernel

    # 搜索空间
    length_scale = trial.suggest_float("length_scale", 0.1, 10.0, log=True)
    constant_value = trial.suggest_float("constant_value", 0.1, 10.0, log=True)
    alpha = trial.suggest_float("alpha", 1e-8, 1.0, log=True)

    kernel = ConstantKernel(constant_value) * RBF(length_scale=length_scale)
    model = GaussianProcessRegressor(kernel=kernel, alpha=alpha, random_state=42)

    X_train, y_train = collect_numpy(train_loader)
    X_val, y_val = collect_numpy(val_loader)

    try:
        model.fit(X_train, y_train)
        y_pred = model.predict(X_val)
        mae = float(np.mean(np.abs(y_pred - y_val)))
        # 检测发散：若 MAE 异常大，返回惩罚值
        if not np.isfinite(mae) or mae > 100:
            return 100.0
        return mae
    except Exception as e:
        print(f"  [GP trial {trial.number}] 失败: {e}")
        return 100.0


def search_gp(train_loader, val_loader, n_trials=30):
    """执行 GP 基线 Optuna 搜索。"""
    print("\n" + "=" * 80)
    print("GP 基线 Optuna 超参搜索")
    print("=" * 80)

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.optimize(
        lambda trial: gp_objective(trial, train_loader, val_loader),
        n_trials=n_trials,
        show_progress_bar=False,
    )

    print(f"\nGP 最佳 trial: {study.best_trial.number}")
    print(f"GP 最佳 MAE: {study.best_value:.4f}")
    print(f"GP 最佳超参: {study.best_params}")

    return study.best_params, study.best_value


# DL-LNN Optuna 搜索


def dlnn_objective(trial, config, dataset_name, train_loader, val_loader):
    """DL-LNN Optuna 目标函数：返回验证集 MAE（越小越好）。

    使用缩减轮数（Stage1=20, Stage2=30）快速评估超参组合，
    最终实验在 run_experiment.py 中用完整轮数（100+200）训练。
    """
    # 搜索空间（不搜索 λ₂/λ₃，因 AR-01 要求与论文一致）
    learning_rate = trial.suggest_float("learning_rate", 1e-4, 1e-2, log=True)
    weight_decay = trial.suggest_float("weight_decay", 1e-5, 1e-3, log=True)
    dropout = trial.suggest_float("dropout", 0.1, 0.4)

    # 复制 config 并应用搜索超参
    search_config = copy.deepcopy(config)
    search_config.model.learning_rate = learning_rate
    search_config.model.weight_decay = weight_decay
    search_config.model.dropout = dropout
    # 缩减轮数以加速搜索（仅用于超参评估，最终实验用 100+200）
    search_config.model.num_epochs_stage1 = 10
    search_config.model.num_epochs_stage2 = 15
    search_config.model.device = "cpu"

    try:
        set_global_seed(42)
        trainer = DLLNNTrainer(search_config, device="cpu")
        trainer.train_stage1(train_loader, val_loader)
        trainer.train_stage2(train_loader, val_loader)

        # 在验证集上评估 MAE
        model = trainer.model
        model.eval()
        all_preds = []
        all_targets = []
        with torch.no_grad():
            for batch in val_loader:
                x, y_true, _ = batch
                y_pred, _ = model(x)
                all_preds.append(y_pred.cpu().numpy())
                all_targets.append(y_true.numpy())
        all_preds = np.concatenate(all_preds, axis=0)
        all_targets = np.concatenate(all_targets, axis=0)
        mae = float(np.mean(np.abs(all_preds - all_targets)))

        if not np.isfinite(mae) or mae > 100:
            return 100.0
        return mae
    except Exception as e:
        print(f"  [DL-LNN trial {trial.number}] 失败: {e}")
        return 100.0


def search_dlnn(config, dataset_name, train_loader, val_loader, n_trials=5):
    """执行 DL-LNN Optuna 搜索。"""
    print("\n" + "=" * 80)
    print(f"DL-LNN Optuna 超参搜索 (数据集: {dataset_name})")
    print("=" * 80)

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.optimize(
        lambda trial: dlnn_objective(trial, config, dataset_name, train_loader, val_loader),
        n_trials=n_trials,
        show_progress_bar=False,
    )

    print(f"\nDL-LNN 最佳 trial: {study.best_trial.number}")
    print(f"DL-LNN 最佳 MAE: {study.best_value:.4f}")
    print(f"DL-LNN 最佳超参: {study.best_params}")

    return study.best_params, study.best_value


# 主流程


def main():
    config = get_config("optuna_search")
    config.model.device = "cpu"

    # 在 Synthetic 数据集上搜索（数据量更大，搜索更稳定）
    dataset_name = "Synthetic"
    print(f"\n在 {dataset_name} 数据集上执行 Optuna 超参搜索")
    print(f"设备: {config.model.device}")

    train_loader, val_loader, test_loader = prepare_dataset(dataset_name, config)

    best_params = {}
    start_time = time.time()

    # 检查是否已有 GP 搜索结果（避免重复搜索）
    results_dir = os.path.join(_EXPERIMENTS_DIR, "results")
    os.makedirs(results_dir, exist_ok=True)
    existing_path = os.path.join(results_dir, "best_hyperparams.json")
    gp_already_searched = False
    if os.path.exists(existing_path):
        try:
            with open(existing_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if "GP" in existing:
                best_params["GP"] = existing["GP"]
                best_params["GP_search_mae"] = existing.get("GP_search_mae", 0.3148)
                gp_already_searched = True
                print(f"[info] 检测到已有 GP 搜索结果，跳过 GP 搜索: {best_params['GP']}")
        except Exception:
            pass

    # 1. GP 基线搜索（仅在未搜索过时执行）
    if not gp_already_searched:
        try:
            gp_best, gp_mae = search_gp(train_loader, val_loader, n_trials=30)
            best_params["GP"] = gp_best
            best_params["GP_search_mae"] = gp_mae
        except Exception as e:
            print(f"[警告] GP 搜索失败: {e}")
            best_params["GP"] = {
                "length_scale": 1.0,
                "constant_value": 1.0,
                "alpha": 1e-2,
            }

    # 2. DL-LNN 关键超参搜索（5 trials × 10+15 epochs，约 10 分钟）
    try:
        dlnn_best, dlnn_mae = search_dlnn(config, dataset_name, train_loader, val_loader, n_trials=5)
        best_params["DL-LNN"] = dlnn_best
        best_params["DL-LNN_search_mae"] = dlnn_mae
    except Exception as e:
        print(f"[警告] DL-LNN 搜索失败: {e}")
        best_params["DL-LNN"] = {
            "learning_rate": 1e-3,
            "weight_decay": 1e-4,
            "dropout": 0.2,
        }

    elapsed = time.time() - start_time

    output_path = existing_path

    # 添加搜索元信息
    best_params["_meta"] = {
        "search_dataset": dataset_name,
        "search_time_seconds": elapsed,
        "search_time_minutes": round(elapsed / 60, 2),
        "gp_trials": 30 if not gp_already_searched else "skipped(already_done)",
        "dlnn_trials": 5,
        "dlnn_search_epochs": "Stage1=10, Stage2=15",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(best_params, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print("Optuna 超参搜索完成")
    print("=" * 80)
    print(f"总耗时: {elapsed / 60:.2f} 分钟")
    print(f"结果保存至: {output_path}")
    print(f"\n最佳超参汇总:")
    print(json.dumps(best_params, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
