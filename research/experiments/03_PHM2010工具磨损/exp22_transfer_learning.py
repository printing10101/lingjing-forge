"""
跨数据集迁移学习实验
验证DL-LNN模型在不同数据集间的迁移能力

实验设计：
1. 在源数据集（PHM2010）上预训练模型
2. 在目标数据集（6061-T6）上进行微调
3. 对比直接训练 vs 迁移学习的性能差异
4. 分析不同微调策略（全参数微调 vs 部分冻结）的效果
"""

import sys
import json
import torch
import torch.nn as nn
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from config import ModelConfig
from models import DLLNNWithPhysics, BaselineLSTM, BaselineTransformer
from data_generator import PHM2010Dataset, Industrial6061T6Dataset, create_dataloaders
from metrics import ChatterMetrics


def train_model(
    model: torch.nn.Module,
    train_loader: torch.utils.data.DataLoader,
    val_loader: torch.utils.data.DataLoader,
    config: ModelConfig,
    device: torch.device,
    num_epochs: int = 100,
    freeze_layers: List[str] = None,
) -> torch.nn.Module:
    """训练模型，支持部分层冻结"""
    criterion = nn.MSELoss()

    # 如果需要冻结层
    if freeze_layers:
        for name, param in model.named_parameters():
            if any(freeze in name for freeze in freeze_layers):
                param.requires_grad = False

    # 只优化未冻结的参数
    optimizer = torch.optim.Adam(
        filter(lambda p: p.requires_grad, model.parameters()), lr=config.learning_rate, weight_decay=config.weight_decay
    )
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


def run_transfer_learning_experiment():
    """运行迁移学习实验"""
    print("=" * 80)
    print("跨数据集迁移学习实验 (Transfer Learning Experiment)")
    print("=" * 80)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n使用设备: {device}")

    config = ModelConfig()

    # 加载源数据集（PHM2010）
    print("\n[步骤 1/6] 加载源数据集 PHM2010...")
    source_train, source_val, source_test = create_dataloaders(
        dataset_class=PHM2010Dataset,
        dataset_params={"num_samples": 315, "noise_level": 0.05, "seed": 42},
        batch_size=config.batch_size,
        train_ratio=0.7,
        val_ratio=0.15,
    )
    print(f"  源数据集训练样本数: {len(source_train.dataset)}")

    # 加载目标数据集（6061-T6）
    print("\n[步骤 2/6] 加载目标数据集 6061-T6...")
    target_train, target_val, target_test = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={"num_samples": 500, "noise_level": 0.08, "seed": 46},
        batch_size=config.batch_size,
        train_ratio=0.7,
        val_ratio=0.15,
    )
    print(f"  目标数据集训练样本数: {len(target_train.dataset)}")

    results = {
        "timestamp": datetime.now().isoformat(),
        "source_dataset": "PHM2010",
        "target_dataset": "6061-T6",
        "experiments": [],
    }

    # 基线 - 直接在目标数据集上训练
    print("\n[步骤 3/6] 基线实验：直接在目标数据集上训练...")

    torch.manual_seed(42)
    np.random.seed(42)

    baseline_model = DLLNNWithPhysics(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
        dt=config.ltc_dt,
        dropout=config.dropout,
    ).to(device)

    baseline_model = train_model(
        model=baseline_model,
        train_loader=target_train,
        val_loader=target_val,
        config=config,
        device=device,
        num_epochs=100,
    )

    baseline_metrics = evaluate_model(baseline_model, target_test, device)

    results["experiments"].append(
        {
            "method": "Direct Training (Baseline)",
            "description": "直接在目标数据集上训练",
            "MAE": round(baseline_metrics["MAE"], 6),
            "RMSE": round(baseline_metrics["RMSE"], 6),
            "R2": round(baseline_metrics["R2"], 6),
            "PCC": round(baseline_metrics["PCC"], 6),
        }
    )

    print(
        f"  MAE={baseline_metrics['MAE']:.4f}, RMSE={baseline_metrics['RMSE']:.4f}, "
        f"R²={baseline_metrics['R2']:.4f}, PCC={baseline_metrics['PCC']:.4f}"
    )

    # 迁移学习 - 全参数微调
    print("\n[步骤 4/6] 迁移学习实验：全参数微调...")

    # 4a. 在源数据集上预训练
    print("  4a. 在源数据集上预训练...")
    torch.manual_seed(42)
    np.random.seed(42)

    pretrained_model = DLLNNWithPhysics(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
        dt=config.ltc_dt,
        dropout=config.dropout,
    ).to(device)

    pretrained_model = train_model(
        model=pretrained_model,
        train_loader=source_train,
        val_loader=source_val,
        config=config,
        device=device,
        num_epochs=100,
    )

    # 评估预训练模型在源数据集上的性能
    source_metrics = evaluate_model(pretrained_model, source_test, device)
    print(f"    源数据集性能: MAE={source_metrics['MAE']:.4f}, PCC={source_metrics['PCC']:.4f}")

    # 4b. 在目标数据集上全参数微调
    print("  4b. 在目标数据集上全参数微调...")
    finetune_model = train_model(
        model=pretrained_model,
        train_loader=target_train,
        val_loader=target_val,
        config=config,
        device=device,
        num_epochs=50,  # 微调阶段使用较少epoch
    )

    finetune_metrics = evaluate_model(finetune_model, target_test, device)

    results["experiments"].append(
        {
            "method": "Transfer Learning (Full Fine-tuning)",
            "description": "预训练 + 全参数微调",
            "MAE": round(finetune_metrics["MAE"], 6),
            "RMSE": round(finetune_metrics["RMSE"], 6),
            "R2": round(finetune_metrics["R2"], 6),
            "PCC": round(finetune_metrics["PCC"], 6),
            "improvement_over_baseline": {
                "MAE": round((baseline_metrics["MAE"] - finetune_metrics["MAE"]) / baseline_metrics["MAE"] * 100, 2),
                "PCC": round((finetune_metrics["PCC"] - baseline_metrics["PCC"]) / baseline_metrics["PCC"] * 100, 2),
            },
        }
    )

    print(
        f"  MAE={finetune_metrics['MAE']:.4f}, RMSE={finetune_metrics['RMSE']:.4f}, "
        f"R²={finetune_metrics['R2']:.4f}, PCC={finetune_metrics['PCC']:.4f}"
    )

    # 迁移学习 - 部分冻结
    print("\n[步骤 5/6] 迁移学习实验：部分冻结（冻结LTC层）...")

    torch.manual_seed(42)
    np.random.seed(42)

    # 重新预训练
    pretrained_model2 = DLLNNWithPhysics(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
        dt=config.ltc_dt,
        dropout=config.dropout,
    ).to(device)

    pretrained_model2 = train_model(
        model=pretrained_model2,
        train_loader=source_train,
        val_loader=source_val,
        config=config,
        device=device,
        num_epochs=100,
    )

    # 冻结LTC层，只微调其他层
    print("  冻结LTC层，微调其他层...")
    freeze_layers = ["ltc_layers"]
    partial_finetune_model = train_model(
        model=pretrained_model2,
        train_loader=target_train,
        val_loader=target_val,
        config=config,
        device=device,
        num_epochs=50,
        freeze_layers=freeze_layers,
    )

    partial_metrics = evaluate_model(partial_finetune_model, target_test, device)

    results["experiments"].append(
        {
            "method": "Transfer Learning (Partial Fine-tuning)",
            "description": "预训练 + 冻结LTC层，微调其他层",
            "MAE": round(partial_metrics["MAE"], 6),
            "RMSE": round(partial_metrics["RMSE"], 6),
            "R2": round(partial_metrics["R2"], 6),
            "PCC": round(partial_metrics["PCC"], 6),
            "improvement_over_baseline": {
                "MAE": round((baseline_metrics["MAE"] - partial_metrics["MAE"]) / baseline_metrics["MAE"] * 100, 2),
                "PCC": round((partial_metrics["PCC"] - baseline_metrics["PCC"]) / baseline_metrics["PCC"] * 100, 2),
            },
        }
    )

    print(
        f"  MAE={partial_metrics['MAE']:.4f}, RMSE={partial_metrics['RMSE']:.4f}, "
        f"R²={partial_metrics['R2']:.4f}, PCC={partial_metrics['PCC']:.4f}"
    )

    # 对比实验 - LSTM迁移学习
    print("\n[步骤 6/6] 对比实验：LSTM迁移学习...")

    # LSTM基线
    torch.manual_seed(42)
    np.random.seed(42)

    lstm_baseline = BaselineLSTM(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
    ).to(device)

    lstm_baseline = train_model(
        model=lstm_baseline,
        train_loader=target_train,
        val_loader=target_val,
        config=config,
        device=device,
        num_epochs=100,
    )

    lstm_baseline_metrics = evaluate_model(lstm_baseline, target_test, device)

    # LSTM迁移学习
    torch.manual_seed(42)
    np.random.seed(42)

    lstm_pretrained = BaselineLSTM(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
    ).to(device)

    lstm_pretrained = train_model(
        model=lstm_pretrained,
        train_loader=source_train,
        val_loader=source_val,
        config=config,
        device=device,
        num_epochs=100,
    )

    lstm_finetune = train_model(
        model=lstm_pretrained,
        train_loader=target_train,
        val_loader=target_val,
        config=config,
        device=device,
        num_epochs=50,
    )

    lstm_transfer_metrics = evaluate_model(lstm_finetune, target_test, device)

    results["experiments"].append(
        {
            "method": "LSTM Transfer Learning",
            "description": "LSTM预训练 + 微调",
            "MAE": round(lstm_transfer_metrics["MAE"], 6),
            "RMSE": round(lstm_transfer_metrics["RMSE"], 6),
            "R2": round(lstm_transfer_metrics["R2"], 6),
            "PCC": round(lstm_transfer_metrics["PCC"], 6),
            "improvement_over_baseline": {
                "MAE": round(
                    (lstm_baseline_metrics["MAE"] - lstm_transfer_metrics["MAE"]) / lstm_baseline_metrics["MAE"] * 100,
                    2,
                ),
                "PCC": round(
                    (lstm_transfer_metrics["PCC"] - lstm_baseline_metrics["PCC"]) / lstm_baseline_metrics["PCC"] * 100,
                    2,
                ),
            },
        }
    )

    print(f"  LSTM基线: MAE={lstm_baseline_metrics['MAE']:.4f}, PCC={lstm_baseline_metrics['PCC']:.4f}")
    print(f"  LSTM迁移: MAE={lstm_transfer_metrics['MAE']:.4f}, PCC={lstm_transfer_metrics['PCC']:.4f}")

    # 保存结果
    print(f"\n保存实验结果...")

    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / "transfer_learning_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"  结果已保存到: {output_file}")
    print("\n" + "=" * 80)
    print("跨数据集迁移学习实验完成!")
    print("=" * 80)

    return results


if __name__ == "__main__":
    run_transfer_learning_experiment()
