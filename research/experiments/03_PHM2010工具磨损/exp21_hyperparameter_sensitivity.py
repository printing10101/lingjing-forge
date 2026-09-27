"""
超参数灵敏度分析实验
分析关键超参数对DL-LNN模型性能的影响

实验设计：
1. 隐藏层维度 (hidden_dim): [32, 64, 128, 256]
2. 学习率 (learning_rate): [1e-4, 5e-4, 1e-3, 5e-3]
3. 时间常数 dt: [0.01, 0.05, 0.1, 0.2]
4. Dropout率: [0.0, 0.1, 0.2, 0.3]
5. 分析各超参数对MAE、RMSE、R²、PCC的影响
"""

import sys
import json
import torch
import torch.nn as nn
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List
from itertools import product

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from config import ModelConfig
from models import DLLNNWithPhysics
from data_generator import Industrial6061T6Dataset, create_dataloaders
from metrics import ChatterMetrics


def train_model(
    model: torch.nn.Module,
    train_loader: torch.utils.data.DataLoader,
    val_loader: torch.utils.data.DataLoader,
    learning_rate: float,
    device: torch.device,
    num_epochs: int = 80,
) -> torch.nn.Module:
    """训练模型"""
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-5)

    best_val_loss = float("inf")
    best_state = None

    for epoch in range(num_epochs):
        # 训练阶段
        model.train()
        train_loss = 0.0
        n_batches = 0

        for batch in train_loader:
            x, y_true, _ = batch
            x = x.to(device)
            y_true = y_true.to(device)

            optimizer.zero_grad()
            output = model(x)
            if isinstance(output, tuple):
                y_pred = output[0]
            else:
                y_pred = output

            if y_pred.shape != y_true.shape:
                y_pred = y_pred.view_as(y_true)

            loss = criterion(y_pred, y_true)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            n_batches += 1

        train_loss /= max(n_batches, 1)

        # 验证阶段
        model.eval()
        val_loss = 0.0
        n_val = 0

        with torch.no_grad():
            for batch in val_loader:
                x, y_true, _ = batch
                x = x.to(device)
                y_true = y_true.to(device)

                output = model(x)
                if isinstance(output, tuple):
                    y_pred = output[0]
                else:
                    y_pred = output

                if y_pred.shape != y_true.shape:
                    y_pred = y_pred.view_as(y_true)

                loss = criterion(y_pred, y_true)
                val_loss += loss.item()
                n_val += 1

        val_loss /= max(n_val, 1)
        scheduler.step()

        # 保存最优权重
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    return model


def evaluate_model(
    model: torch.nn.Module, test_loader: torch.utils.data.DataLoader, device: torch.device
) -> Dict[str, float]:
    """评估模型"""
    model.eval()
    all_preds = []
    all_targets = []
    all_phys = []

    with torch.no_grad():
        for batch in test_loader:
            x, y_true, y_physics = batch
            x = x.to(device)

            output = model(x)
            if isinstance(output, tuple):
                y_pred = output[0]
            else:
                y_pred = output

            if y_pred.shape != y_true.shape:
                y_pred = y_pred.view_as(y_true)

            all_preds.append(y_pred.cpu().numpy())
            all_targets.append(y_true.numpy())
            all_phys.append(y_physics.numpy())

    all_preds = np.concatenate(all_preds, axis=0).flatten()
    all_targets = np.concatenate(all_targets, axis=0).flatten()
    all_phys = np.concatenate(all_phys, axis=0).flatten()

    metrics_calc = ChatterMetrics()
    metrics = {
        "MAE": metrics_calc.mae(all_preds, all_targets),
        "RMSE": metrics_calc.rmse(all_preds, all_targets),
        "R2": metrics_calc.r2_score(all_preds, all_targets),
        "PCC": metrics_calc.physics_consistency_coefficient(all_preds, all_phys),
    }

    return metrics


def run_hyperparameter_sensitivity_experiment():
    """运行超参数灵敏度分析实验"""
    print("=" * 80)
    print("超参数灵敏度分析实验 (Hyperparameter Sensitivity Analysis)")
    print("=" * 80)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n使用设备: {device}")

    # 加载数据
    print("\n[步骤 1/5] 加载工业 6061-T6 数据集...")
    train_loader, val_loader, test_loader = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={"num_samples": 500, "noise_level": 0.08, "seed": 46},
        batch_size=32,
        train_ratio=0.7,
        val_ratio=0.15,
    )
    print(f"  训练集样本数: {len(train_loader.dataset)}")
    print(f"  验证集样本数: {len(val_loader.dataset)}")
    print(f"  测试集样本数: {len(test_loader.dataset)}")

    # 定义超参数搜索空间
    print("\n[步骤 2/5] 定义超参数搜索空间...")
    hidden_dims = [32, 64, 128, 256]
    learning_rates = [1e-4, 5e-4, 1e-3, 5e-3]
    dts = [0.01, 0.05, 0.1, 0.2]
    dropouts = [0.0, 0.1, 0.2, 0.3]

    print(f"  隐藏层维度: {hidden_dims}")
    print(f"  学习率: {learning_rates}")
    print(f"  时间常数 dt: {dts}")
    print(f"  Dropout率: {dropouts}")

    results = {
        "timestamp": datetime.now().isoformat(),
        "hidden_dim_analysis": [],
        "learning_rate_analysis": [],
        "dt_analysis": [],
        "dropout_analysis": [],
        "best_config": None,
    }

    # 隐藏层维度分析
    print(f"\n[步骤 3/5] 隐藏层维度分析...")
    for hidden_dim in hidden_dims:
        print(f"\n  hidden_dim = {hidden_dim}")

        torch.manual_seed(42)
        np.random.seed(42)

        model = DLLNNWithPhysics(
            input_dim=7, hidden_dim=hidden_dim, num_layers=3, output_dim=1, dt=0.1, dropout=0.2
        ).to(device)

        model = train_model(
            model=model,
            train_loader=train_loader,
            val_loader=val_loader,
            learning_rate=1e-3,
            device=device,
            num_epochs=80,
        )

        metrics = evaluate_model(model, test_loader, device)

        results["hidden_dim_analysis"].append(
            {
                "hidden_dim": hidden_dim,
                "MAE": round(metrics["MAE"], 6),
                "RMSE": round(metrics["RMSE"], 6),
                "R2": round(metrics["R2"], 6),
                "PCC": round(metrics["PCC"], 6),
            }
        )

        print(
            f"    MAE={metrics['MAE']:.4f}, RMSE={metrics['RMSE']:.4f}, "
            f"R²={metrics['R2']:.4f}, PCC={metrics['PCC']:.4f}"
        )

    # 学习率分析
    print(f"\n[步骤 4/5] 学习率分析...")
    for lr in learning_rates:
        print(f"\n  learning_rate = {lr}")

        torch.manual_seed(42)
        np.random.seed(42)

        model = DLLNNWithPhysics(input_dim=7, hidden_dim=128, num_layers=3, output_dim=1, dt=0.1, dropout=0.2).to(
            device
        )

        model = train_model(
            model=model,
            train_loader=train_loader,
            val_loader=val_loader,
            learning_rate=lr,
            device=device,
            num_epochs=80,
        )

        metrics = evaluate_model(model, test_loader, device)

        results["learning_rate_analysis"].append(
            {
                "learning_rate": lr,
                "MAE": round(metrics["MAE"], 6),
                "RMSE": round(metrics["RMSE"], 6),
                "R2": round(metrics["R2"], 6),
                "PCC": round(metrics["PCC"], 6),
            }
        )

        print(
            f"    MAE={metrics['MAE']:.4f}, RMSE={metrics['RMSE']:.4f}, "
            f"R²={metrics['R2']:.4f}, PCC={metrics['PCC']:.4f}"
        )

    # 时间常数 dt 分析
    print(f"\n[步骤 5/5] 时间常数 dt 分析...")
    for dt in dts:
        print(f"\n  dt = {dt}")

        torch.manual_seed(42)
        np.random.seed(42)

        model = DLLNNWithPhysics(input_dim=7, hidden_dim=128, num_layers=3, output_dim=1, dt=dt, dropout=0.2).to(device)

        model = train_model(
            model=model,
            train_loader=train_loader,
            val_loader=val_loader,
            learning_rate=1e-3,
            device=device,
            num_epochs=80,
        )

        metrics = evaluate_model(model, test_loader, device)

        results["dt_analysis"].append(
            {
                "dt": dt,
                "MAE": round(metrics["MAE"], 6),
                "RMSE": round(metrics["RMSE"], 6),
                "R2": round(metrics["R2"], 6),
                "PCC": round(metrics["PCC"], 6),
            }
        )

        print(
            f"    MAE={metrics['MAE']:.4f}, RMSE={metrics['RMSE']:.4f}, "
            f"R²={metrics['R2']:.4f}, PCC={metrics['PCC']:.4f}"
        )

    # Dropout 分析
    print(f"\n[步骤 6/5] Dropout 分析...")
    for dropout in dropouts:
        print(f"\n  dropout = {dropout}")

        torch.manual_seed(42)
        np.random.seed(42)

        model = DLLNNWithPhysics(input_dim=7, hidden_dim=128, num_layers=3, output_dim=1, dt=0.1, dropout=dropout).to(
            device
        )

        model = train_model(
            model=model,
            train_loader=train_loader,
            val_loader=val_loader,
            learning_rate=1e-3,
            device=device,
            num_epochs=80,
        )

        metrics = evaluate_model(model, test_loader, device)

        results["dropout_analysis"].append(
            {
                "dropout": dropout,
                "MAE": round(metrics["MAE"], 6),
                "RMSE": round(metrics["RMSE"], 6),
                "R2": round(metrics["R2"], 6),
                "PCC": round(metrics["PCC"], 6),
            }
        )

        print(
            f"    MAE={metrics['MAE']:.4f}, RMSE={metrics['RMSE']:.4f}, "
            f"R²={metrics['R2']:.4f}, PCC={metrics['PCC']:.4f}"
        )

    # 找出最优配置
    print(f"\n[步骤 7/5] 分析最优配置...")

    # 找出每个维度的最优值
    best_hidden_dim = min(results["hidden_dim_analysis"], key=lambda x: x["MAE"])
    best_lr = min(results["learning_rate_analysis"], key=lambda x: x["MAE"])
    best_dt = min(results["dt_analysis"], key=lambda x: x["MAE"])
    best_dropout = min(results["dropout_analysis"], key=lambda x: x["MAE"])

    results["best_config"] = {
        "hidden_dim": best_hidden_dim["hidden_dim"],
        "learning_rate": best_lr["learning_rate"],
        "dt": best_dt["dt"],
        "dropout": best_dropout["dropout"],
        "best_MAE": best_hidden_dim["MAE"],
    }

    print(f"\n最优配置:")
    print(f"  hidden_dim = {results['best_config']['hidden_dim']}")
    print(f"  learning_rate = {results['best_config']['learning_rate']}")
    print(f"  dt = {results['best_config']['dt']}")
    print(f"  dropout = {results['best_config']['dropout']}")

    # 保存结果
    print(f"\n[步骤 8/5] 保存实验结果...")

    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / "hyperparameter_sensitivity_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"  结果已保存到: {output_file}")
    print("\n" + "=" * 80)
    print("超参数灵敏度分析实验完成!")
    print("=" * 80)

    return results


if __name__ == "__main__":
    run_hyperparameter_sensitivity_experiment()
