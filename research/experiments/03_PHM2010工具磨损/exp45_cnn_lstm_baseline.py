"""
实验 45：CNN+LSTM 基线对比实验

目的：
    验证 LTC（液态时间常数网络）相对于 CNN+LSTM 这一经典深度时序混合基线
    的优势。CNN+LSTM 是机械信号建模领域的标准对照模型：CNN 提取局部短时
    窗内的力/振动模式，LSTM 捕捉长程时序依赖。

实验设计：
    - 模型集合：DL-LNN（含物理分支）、LTC（纯数据驱动）、CNN-LSTM、LSTM、CNN
    - 数据集：5 个（PHM2010 真实数据 + 4 个合成数据集，沿用 exp7 配置）
    - 评价指标：MAE、RMSE、R²、MAPE、PCC（物理一致性系数）
    - 训练协议：80 epoch，Adam + CosineAnnealingLR，与 exp7 完全一致

学术诚信：
    本实验沿用 exp7_main_comparison.py 的数据来源标注，PHM2010 为真实公开数据，
    其余为合成数据。结果文件中 _metadata.data_sources 字段如实标注每个数据集
    的来源，论文引用时必须按此字段注明。

输出：
    results/cnn_lstm_baseline_results.json — 详细指标
    控制台汇总表 — MAE / R² 对比
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict

import numpy as np
import torch
import torch.nn as nn

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))
# 添加项目根目录（python/）到 path，用于导入 app 模块
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from training.reproducibility import set_global_seed

from config import ModelConfig
from data_generator import (
    Benchmark1Dataset,
    Industrial6061T6Dataset,
    NISTDataset,
    NUAADataset,
    PHM2010Dataset,
    create_dataloaders,
)
from metrics import ChatterMetrics
from models import (
    BaselineCNN,
    BaselineCNNLSTM,
    BaselineLSTM,
    DLLNNModel,
    DLLNNWithPhysics,
)


# 配置

# 参与对比的模型列表（与 exp7 主对比保持差异：聚焦于"传统深度时序基线"对照组）
MODEL_NAMES = ["DL-LNN", "LTC", "CNN-LSTM", "LSTM", "CNN"]

# ── Overfitting 检测阈值 ──────────────────────────────────────────────
# gap = val_loss - train_loss，正值表示训练集损失低于验证集（过拟合信号）
# 阈值 0.05：典型 MSE 训练中，5% 的 gap 已能反映泛化能力下降
# patience 5：连续 5 个 epoch 触发才标记为 overfitting，规避偶发波动
OVERFITTING_GAP_THRESHOLD = 0.05
OVERFITTING_PATIENCE = 5


def create_model_by_name(name: str, config: ModelConfig, device: torch.device) -> nn.Module:
    """根据名称创建模型（仅支持本实验涉及的 5 个模型）。"""
    if name == "DL-LNN":
        model = DLLNNWithPhysics(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
            dt=config.ltc_dt,
            dropout=config.dropout,
        )
    elif name == "LTC":
        model = DLLNNModel(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
            dt=config.ltc_dt,
            dropout=config.dropout,
        )
    elif name == "CNN-LSTM":
        model = BaselineCNNLSTM(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
            dropout=config.dropout,
        )
    elif name == "LSTM":
        model = BaselineLSTM(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    elif name == "CNN":
        model = BaselineCNN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            output_dim=config.output_dim,
        )
    else:
        raise ValueError(f"未知模型: {name}")

    return model.to(device)


def train_model(
    model: nn.Module,
    train_loader,
    val_loader,
    config: ModelConfig,
    device: torch.device,
    num_epochs: int = 80,
) -> tuple[nn.Module, list[dict], dict]:
    """训练模型，返回最佳模型、训练历史与 overfitting 诊断。

    训练协议与 exp7_main_comparison.py 完全一致：
        - 损失：MSE
        - 优化器：Adam(lr=config.learning_rate, weight_decay=config.weight_decay)
        - 调度器：CosineAnnealingLR(T_max=num_epochs, eta_min=1e-5)
        - 早停：基于验证集 loss 选择最佳权重

    Overfitting 检测（不改训练协议，仅诊断标注）：
        - gap = val_loss - train_loss（正值表示训练优于验证）
        - 检测条件：gap > OVERFITTING_GAP_THRESHOLD 且持续 OVERFITTING_PATIENCE 个 epoch
        - 输出 ``overfitting_diag`` 字典：max_gap / final_gap / detected / detected_epoch
        - 论文中可据此判断各模型在小样本数据集（如自采6061-T6）上的过拟合倾向
    """
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-5)

    best_val_loss = float("inf")
    best_state = None
    history: list[dict] = []

    # Overfitting 检测状态
    overfitting_consecutive = 0
    overfitting_detected = False
    overfitting_detected_epoch: int | None = None
    max_gap = 0.0

    for epoch in range(num_epochs):
        # 训练
        model.train()
        train_loss = 0.0
        n_batches = 0
        for batch in train_loader:
            x, y_true, _ = batch
            x = x.to(device)
            y_true = y_true.to(device)

            optimizer.zero_grad()
            output = model(x)
            y_pred = output[0] if isinstance(output, tuple) else output
            if y_pred.shape != y_true.shape:
                y_pred = y_pred.view_as(y_true)

            loss = criterion(y_pred, y_true)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            n_batches += 1

        train_loss /= max(n_batches, 1)

        # 验证
        model.eval()
        val_loss = 0.0
        n_val = 0
        with torch.no_grad():
            for batch in val_loader:
                x, y_true, _ = batch
                x = x.to(device)
                y_true = y_true.to(device)

                output = model(x)
                y_pred = output[0] if isinstance(output, tuple) else output
                if y_pred.shape != y_true.shape:
                    y_pred = y_pred.view_as(y_true)

                loss = criterion(y_pred, y_true)
                val_loss += loss.item()
                n_val += 1

        val_loss /= max(n_val, 1)
        scheduler.step()

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        # Overfitting gap 诊断（不改训练协议）
        gap = val_loss - train_loss
        max_gap = max(max_gap, gap)
        if gap > OVERFITTING_GAP_THRESHOLD:
            overfitting_consecutive += 1
            if not overfitting_detected and overfitting_consecutive >= OVERFITTING_PATIENCE:
                overfitting_detected = True
                overfitting_detected_epoch = epoch + 1
        else:
            overfitting_consecutive = 0

        history.append(
            {
                "epoch": epoch + 1,
                "train_loss": train_loss,
                "val_loss": val_loss,
                "overfitting_gap": gap,
            }
        )

        if (epoch + 1) % 20 == 0 or epoch == 0:
            of_flag = " [OVERFIT]" if gap > OVERFITTING_GAP_THRESHOLD else ""
            print(
                f"    Epoch [{epoch + 1}/{num_epochs}] "
                f"Train: {train_loss:.4f} Val: {val_loss:.4f}"
                f" Gap: {gap:.4f}{of_flag}"
            )

    if best_state is not None:
        model.load_state_dict(best_state)

    overfitting_diag = {
        "max_gap": round(max_gap, 6),
        "final_gap": round(history[-1]["overfitting_gap"], 6) if history else 0.0,
        "detected": overfitting_detected,
        "detected_epoch": overfitting_detected_epoch,
        "threshold": OVERFITTING_GAP_THRESHOLD,
        "patience": OVERFITTING_PATIENCE,
    }

    if overfitting_detected:
        print(
            f"    ⚠ Overfitting 检测：gap > {OVERFITTING_GAP_THRESHOLD} 持续 "
            f"{OVERFITTING_PATIENCE} epoch，首次触发 @ epoch {overfitting_detected_epoch}"
        )

    return model, history, overfitting_diag


def evaluate_model(model: nn.Module, test_loader, device: torch.device) -> Dict[str, float]:
    """评估模型，返回 MAE/RMSE/R²/MAPE/PCC 指标。"""
    model.eval()
    all_preds = []
    all_targets = []
    all_phys = []

    with torch.no_grad():
        for batch in test_loader:
            x, y_true, y_physics = batch
            x = x.to(device)

            output = model(x)
            y_pred = output[0] if isinstance(output, tuple) else output
            if y_pred.shape != y_true.shape:
                y_pred = y_pred.view_as(y_true)

            all_preds.append(y_pred.cpu().numpy())
            all_targets.append(y_true.numpy())
            all_phys.append(y_physics.numpy())

    all_preds = np.concatenate(all_preds, axis=0).flatten()
    all_targets = np.concatenate(all_targets, axis=0).flatten()
    all_phys = np.concatenate(all_phys, axis=0).flatten()

    metrics_calc = ChatterMetrics()
    return {
        "MAE": metrics_calc.mae(all_preds, all_targets),
        "RMSE": metrics_calc.rmse(all_preds, all_targets),
        "R2": metrics_calc.r2_score(all_preds, all_targets),
        "MAPE": metrics_calc.mape(all_preds, all_targets),
        "PCC": metrics_calc.physics_consistency_coefficient(all_preds, all_phys),
    }


def get_all_datasets() -> Dict[str, object]:
    """获取所有 5 个数据集（与 exp7 保持一致）。"""
    return {
        "PHM2010": PHM2010Dataset(num_samples=2000, noise_level=0.05, seed=42),
        "NUAA": NUAADataset(num_samples=1800, noise_level=0.04, seed=43),
        "NIST": NISTDataset(num_samples=1500, noise_level=0.06, seed=44),
        "Benchmark-1": Benchmark1Dataset(num_samples=2200, noise_level=0.045, seed=45),
        "自采6061-T6": Industrial6061T6Dataset(num_samples=500, noise_level=0.08, seed=46),
    }


def get_dataset_data_source(dataset_name: str) -> str:
    """数据来源标签（用于学术诚信追溯，与 exp7 一致）。"""
    mapping = {
        "PHM2010": "real_PHM2010",
        "NUAA": "synthetic_Tlusty",
        "NIST": "synthetic_Tlusty",
        "Benchmark-1": "synthetic_Tlusty",
        "自采6061-T6": "synthetic_6061T6_placeholder",
    }
    return mapping.get(dataset_name, "unknown")


def run_cnn_lstm_baseline_experiment():
    """运行 CNN+LSTM 基线对比实验。"""
    print("=" * 80)
    print("实验 45：CNN+LSTM 基线对比（聚焦传统深度时序模型对照组）")
    print("=" * 80)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n使用设备: {device}")

    config = ModelConfig()

    # 加载数据集
    print("\n[1/2] 加载数据集...")
    all_datasets = get_all_datasets()
    for name, ds in all_datasets.items():
        print(f"  - {name}: {len(ds)} 样本")

    # 运行实验
    print("\n[2/2] 运行实验...")
    results: Dict[str, dict] = {}
    dataset_data_sources: Dict[str, str] = {}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for ds_idx, (dataset_name, dataset) in enumerate(all_datasets.items(), 1):
        print(f"\n{'=' * 80}")
        print(f"[{ds_idx}/5] 数据集: {dataset_name}")
        print(f"{'=' * 80}")

        data_source = get_dataset_data_source(dataset_name)
        dataset_data_sources[dataset_name] = data_source
        print(f"  data_source: {data_source}")

        train_loader, val_loader, test_loader = create_dataloaders(
            dataset_class=type(dataset),
            dataset_params={
                "num_samples": len(dataset),
                "noise_level": dataset.noise_level,
                "seed": 42,
            },
            batch_size=config.batch_size,
            train_ratio=0.7,
            val_ratio=0.15,
        )

        results[dataset_name] = {}

        for model_name in MODEL_NAMES:
            print(f"\n  [{model_name}]")
            try:
                model = create_model_by_name(model_name, config, device)
                model, history, overfitting_diag = train_model(
                    model=model,
                    train_loader=train_loader,
                    val_loader=val_loader,
                    config=config,
                    device=device,
                    num_epochs=80,
                )
                test_metrics = evaluate_model(model, test_loader, device)
                # 记录训练历史末态（用于后续分析）
                test_metrics["_final_train_loss"] = history[-1]["train_loss"]
                test_metrics["_final_val_loss"] = history[-1]["val_loss"]
                test_metrics["_best_val_loss"] = min(h["val_loss"] for h in history)
                # Overfitting 诊断字段（论文中可用于分析各模型泛化能力）
                test_metrics["_overfitting"] = overfitting_diag

                results[dataset_name][model_name] = test_metrics
                print(
                    f"    MAE: {test_metrics['MAE']:.4f}, "
                    f"RMSE: {test_metrics['RMSE']:.4f}, "
                    f"R2: {test_metrics['R2']:.4f}, "
                    f"PCC: {test_metrics['PCC']:.4f}"
                )
            except Exception as e:
                print(f"    错误: {e}")
                traceback.print_exc()
                results[dataset_name][model_name] = {
                    "MAE": float("nan"),
                    "RMSE": float("nan"),
                    "R2": float("nan"),
                    "MAPE": float("nan"),
                    "PCC": float("nan"),
                    "_overfitting": {
                        "max_gap": float("nan"),
                        "final_gap": float("nan"),
                        "detected": False,
                        "detected_epoch": None,
                        "error": str(e),
                    },
                }

    # 保存结果（含 _metadata 元数据，沿用 exp7 的学术诚信标注结构）
    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)

    output_payload = dict(results)
    output_payload["_metadata"] = {
        "description": (
            "CNN+LSTM 基线对比实验：5 个数据集 × 5 个模型（DL-LNN/LTC/CNN-LSTM/LSTM/CNN），聚焦传统深度时序模型对照组"
        ),
        "experiment_id": "exp45_cnn_lstm_baseline",
        "generated_at": timestamp,
        "training_protocol": {
            "epochs": 80,
            "optimizer": "Adam",
            "scheduler": "CosineAnnealingLR(eta_min=1e-5)",
            "loss": "MSE",
            "batch_size": config.batch_size,
            "learning_rate": config.learning_rate,
            "weight_decay": config.weight_decay,
        },
        "overfitting_detection": {
            "method": "val_loss - train_loss gap 监控",
            "gap_threshold": OVERFITTING_GAP_THRESHOLD,
            "patience_epochs": OVERFITTING_PATIENCE,
            "fields_in_results": (
                "_overfitting.max_gap: 训练过程中最大 gap 值"
                "_overfitting.final_gap: 最后一个 epoch 的 gap 值"
                "_overfitting.detected: 是否检测到 overfitting"
                "_overfitting.detected_epoch: 首次触发的 epoch"
            ),
            "note": (
                "本检测不改训练协议（仍是固定 80 epoch），仅作为诊断标注。"
                "论文中可据此分析各模型在小样本数据集（如自采6061-T6）上的过拟合倾向。"
            ),
        },
        "models": MODEL_NAMES,
        "data_sources": dataset_data_sources,
        "data_source_legend": {
            "real_PHM2010": "真实 PHM2010 公开数据集（通过 UniwearDataLoader 加载）",
            "synthetic_Tlusty": "基于 TlustyAnalyticalModel 生成的合成数据",
            "synthetic_6061T6_placeholder": "合成数据占位实现（不可声称真实自采数据）",
        },
        "academic_integrity_note": (
            "本结果文件中 PHM2010 数据集的指标基于真实公开数据，"
            "其余数据集（NUAA/NIST/Benchmark-1/自采6061-T6）为合成数据。"
            "在论文中引用本文件的指标时，必须根据 data_sources 字段如实标注数据来源。"
        ),
        "purpose": (
            "验证 LTC 相对于 CNN+LSTM 经典混合基线的优势。"
            "CNN+LSTM 是机械信号建模领域的标准对照模型："
            "CNN 提取局部短时窗内的力/振动模式，LSTM 捕捉长程时序依赖。"
        ),
    }

    output_file = output_dir / "cnn_lstm_baseline_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 80}")
    print(f"实验完成！结果已保存到: {output_file}")
    print(f"{'=' * 80}")

    # 打印汇总表格
    _print_summary_table(results, "MAE")
    _print_summary_table(results, "R2")
    _print_summary_table(results, "PCC")

    return results


def _print_summary_table(results: Dict[str, dict], metric: str) -> None:
    """打印指定指标的汇总表格。"""
    print(f"\n汇总表格 ({metric}):")
    print("-" * 100)
    header = f"{'Dataset':<18}" + "".join([f"{name:<13}" for name in MODEL_NAMES])
    print(header)
    print("-" * 100)

    for dataset_name, dataset_results in results.items():
        row = f"{dataset_name:<18}"
        for model_name in MODEL_NAMES:
            if model_name in dataset_results:
                val = dataset_results[model_name].get(metric, float("nan"))
                if isinstance(val, float) and np.isnan(val):
                    row += f"{'N/A':<13}"
                else:
                    row += f"{val:<13.4f}"
            else:
                row += f"{'N/A':<13}"
        print(row)

    print("-" * 100)


if __name__ == "__main__":
    set_global_seed(42)
    run_cnn_lstm_baseline_experiment()
