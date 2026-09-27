"""
噪声鲁棒性实验
测试模型在不同信噪比(SNR)下的性能表现

实验设计：
1. 在测试数据中添加不同水平的高斯噪声（SNR = 0, 5, 10, 15, 20, 25, 30 dB）
2. 对比DL-LNN与LSTM、Transformer、PINN、BPNN在噪声环境下的性能
3. 评估指标：MAE, RMSE, R², PCC
4. 分析各模型对噪声的敏感性和鲁棒性
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
from models import DLLNNWithPhysics, BaselineLSTM, BaselineTransformer, BaselinePINN, BaselineBPNN
from data_generator import Industrial6061T6Dataset, create_dataloaders
from metrics import ChatterMetrics


def add_gaussian_noise(data: np.ndarray, snr_db: float) -> np.ndarray:
    """
    向数据中添加高斯噪声

    根据信噪比(SNR)计算噪声功率，然后生成对应强度的高斯噪声叠加到原始数据上。
    SNR(dB) = 10 * log10(P_signal / P_noise)
    因此 P_noise = P_signal / 10^(SNR/10)

    Args:
        data: 原始干净数据，形状为 (N,) 或 (N, D)
        snr_db: 信噪比，单位 dB。值越小噪声越大，0 dB 表示信号功率等于噪声功率

    Returns:
        添加噪声后的数据，形状与输入相同
    """
    # 计算信号功率（均方值）
    signal_power = np.mean(data**2)

    # 根据 SNR 计算噪声功率
    # SNR(dB) = 10 * log10(P_signal / P_noise)
    # => P_noise = P_signal / 10^(SNR/10)
    snr_linear = 10 ** (snr_db / 10)
    noise_power = signal_power / snr_linear

    # 生成高斯噪声（均值为0，方差等于噪声功率）
    noise = np.random.normal(0, np.sqrt(noise_power), data.shape)

    return data + noise


def create_noisy_test_loader(
    test_loader: torch.utils.data.DataLoader, snr_db: float, seed: int = 42
) -> torch.utils.data.DataLoader:
    """
    创建添加了噪声的测试数据加载器

    从原始 test_loader 中提取所有数据，对特征添加指定 SNR 的高斯噪声，
    然后重新封装为 DataLoader。

    Args:
        test_loader: 原始测试数据加载器
        snr_db: 信噪比 (dB)
        seed: 随机种子，确保同一 SNR 下噪声一致

    Returns:
        添加了噪声的新 DataLoader
    """
    np.random.seed(seed)

    # 收集所有测试数据
    all_features = []
    all_targets = []
    all_physics = []

    for batch in test_loader:
        x, y, y_phys = batch
        all_features.append(x.numpy())
        all_targets.append(y.numpy())
        all_physics.append(y_phys.numpy())

    all_features = np.concatenate(all_features, axis=0)
    all_targets = np.concatenate(all_targets, axis=0)
    all_physics = np.concatenate(all_physics, axis=0)

    # 对特征添加高斯噪声
    noisy_features = add_gaussian_noise(all_features, snr_db)

    # 构建新的 TensorDataset
    noisy_dataset = torch.utils.data.TensorDataset(
        torch.from_numpy(noisy_features.astype(np.float32)),
        torch.from_numpy(all_targets.astype(np.float32)),
        torch.from_numpy(all_physics.astype(np.float32)),
    )

    # 创建 DataLoader（保持与原始相同的 batch_size）
    noisy_loader = torch.utils.data.DataLoader(
        noisy_dataset, batch_size=test_loader.batch_size, shuffle=False, num_workers=0, pin_memory=False
    )

    return noisy_loader


def train_model(
    model: torch.nn.Module,
    train_loader: torch.utils.data.DataLoader,
    val_loader: torch.utils.data.DataLoader,
    config: ModelConfig,
    device: torch.device,
    num_epochs: int = 80,
) -> torch.nn.Module:
    """
    训练模型

    使用 Adam 优化器和余弦退火学习率调度器进行训练，
    根据验证集损失选择最优模型权重。

    Args:
        model: 待训练的模型
        train_loader: 训练数据加载器
        val_loader: 验证数据加载器
        config: 模型配置
        device: 计算设备
        num_epochs: 训练轮数

    Returns:
        训练完成的最优模型
    """
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
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
            # DLLNNWithPhysics 返回元组 (final_pred, ltc_pred)，其他模型返回单个张量
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
    """
    在测试集上评估模型性能

    Args:
        model: 待评估的模型
        test_loader: 测试数据加载器（可能已添加噪声）
        device: 计算设备

    Returns:
        包含 MAE, RMSE, R2, PCC 四个指标的字典
    """
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


def create_model_by_name(model_name: str, config: ModelConfig, device: torch.device) -> torch.nn.Module:
    """
    根据模型名称创建对应的模型实例

    Args:
        model_name: 模型名称（DL-LNN / LSTM / Transformer / PINN / BPNN）
        config: 模型配置
        device: 计算设备

    Returns:
        初始化后的模型
    """
    if model_name == "DL-LNN":
        model = DLLNNWithPhysics(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
            dt=config.ltc_dt,
            dropout=config.dropout,
        )
    elif model_name == "LSTM":
        model = BaselineLSTM(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    elif model_name == "Transformer":
        model = BaselineTransformer(
            input_dim=config.input_dim,
            d_model=config.hidden_dim,
            nhead=4,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    elif model_name == "PINN":
        model = BaselinePINN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    elif model_name == "BPNN":
        model = BaselineBPNN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    else:
        raise ValueError(f"未知模型: {model_name}")

    return model.to(device)


def run_noise_robustness_experiment():
    """
    运行噪声鲁棒性实验

    实验流程：
    1. 加载工业 6061-T6 数据集，创建训练/验证/测试 DataLoader
    2. 定义 SNR 水平列表 [0, 5, 10, 15, 20, 25, 30] dB
    3. 对每个 SNR 水平：
       a. 在测试数据上添加对应强度的高斯噪声
       b. 训练所有对比模型（DL-LNN, LSTM, Transformer, PINN, BPNN）
       c. 在噪声测试集上评估各模型性能
       d. 记录 MAE, RMSE, R2, PCC 指标
    4. 将结果保存为 JSON 文件
    """

    print("=" * 80)
    print("噪声鲁棒性实验 (Noise Robustness Experiment)")
    print("=" * 80)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n使用设备: {device}")

    config = ModelConfig()

    # 加载工业数据集
    print("\n[步骤 1/4] 加载工业 6061-T6 数据集...")
    train_loader, val_loader, test_loader = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={"num_samples": 500, "noise_level": 0.08, "seed": 46},
        batch_size=config.batch_size,
        train_ratio=0.7,
        val_ratio=0.15,
    )
    print(f"  训练集样本数: {len(train_loader.dataset)}")
    print(f"  验证集样本数: {len(val_loader.dataset)}")
    print(f"  测试集样本数: {len(test_loader.dataset)}")

    # 定义 SNR 水平和模型列表
    snr_levels = [0, 5, 10, 15, 20, 25, 30]  # 单位: dB
    model_names = ["DL-LNN", "LSTM", "Transformer", "PINN", "BPNN"]

    print(f"\n[步骤 2/4] 实验配置:")
    print(f"  SNR 水平 (dB): {snr_levels}")
    print(f"  对比模型: {model_names}")

    # 初始化结果存储
    results = {
        "timestamp": datetime.now().isoformat(),
        "snr_levels": snr_levels,
        "results": {name: [] for name in model_names},
    }

    # 对每个 SNR 水平进行实验
    print(f"\n[步骤 3/4] 开始噪声鲁棒性测试...")

    for snr_db in snr_levels:
        print(f"\n{'─' * 70}")
        print(f"  SNR = {snr_db} dB")
        print(f"{'─' * 70}")

        # 3a. 创建添加了噪声的测试数据加载器
        noisy_test_loader = create_noisy_test_loader(test_loader, snr_db, seed=42)

        # 统计噪声信息
        orig_features = []
        noisy_features = []
        for orig_batch, noisy_batch in zip(test_loader, noisy_test_loader):
            orig_features.append(orig_batch[0].numpy())
            noisy_features.append(noisy_batch[0].numpy())
        orig_features = np.concatenate(orig_features, axis=0)
        noisy_features = np.concatenate(noisy_features, axis=0)
        actual_noise = noisy_features - orig_features
        actual_snr = 10 * np.log10(np.mean(orig_features**2) / max(np.mean(actual_noise**2), 1e-12))
        print(f"  实际 SNR: {actual_snr:.1f} dB (目标: {snr_db} dB)")

        # 3b & 3c. 训练并评估每个模型
        for model_name in model_names:
            print(f"\n  训练 {model_name}...", end=" ")

            # 设置随机种子以保证可重复性
            torch.manual_seed(42)
            np.random.seed(42)

            # 创建新模型
            model = create_model_by_name(model_name, config, device)

            # 训练模型
            model = train_model(
                model=model,
                train_loader=train_loader,
                val_loader=val_loader,
                config=config,
                device=device,
                num_epochs=80,
            )

            # 在噪声测试集上评估
            metrics = evaluate_model(model, noisy_test_loader, device)

            # 记录结果
            results["results"][model_name].append(
                {
                    "snr": snr_db,
                    "MAE": round(metrics["MAE"], 6),
                    "RMSE": round(metrics["RMSE"], 6),
                    "R2": round(metrics["R2"], 6),
                    "PCC": round(metrics["PCC"], 6),
                }
            )

            print(
                f"MAE={metrics['MAE']:.4f}, RMSE={metrics['RMSE']:.4f}, "
                f"R²={metrics['R2']:.4f}, PCC={metrics['PCC']:.4f}"
            )

    # 保存结果
    print(f"\n[步骤 4/4] 保存实验结果...")

    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / "noise_robustness_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"  结果已保存到: {output_file}")

    # 打印汇总表格
    print(f"\n{'=' * 80}")
    print("噪声鲁棒性实验结果汇总")
    print(f"{'=' * 80}")

    # 按模型打印各 SNR 下的 R² 和 MAE
    for model_name in model_names:
        print(f"\n  {model_name}:")
        print(f"  {'SNR(dB)':<10} {'MAE':<12} {'RMSE':<12} {'R²':<12} {'PCC':<12}")
        print(f"  {'-' * 58}")
        for entry in results["results"][model_name]:
            print(
                f"  {entry['snr']:<10} {entry['MAE']:<12.4f} {entry['RMSE']:<12.4f} "
                f"{entry['R2']:<12.4f} {entry['PCC']:<12.4f}"
            )

    # 计算鲁棒性指标：各模型在 SNR=0 到 SNR=30 的性能退化幅度
    print(f"\n{'=' * 80}")
    print("鲁棒性分析 (性能退化幅度: SNR=0dB vs SNR=30dB)")
    print(f"{'=' * 80}")
    print(f"  {'模型':<15} {'MAE退化':<15} {'R²退化':<15} {'PCC退化':<15}")
    print(f"  {'-' * 60}")

    for model_name in model_names:
        snr0 = results["results"][model_name][0]  # SNR=0
        snr30 = results["results"][model_name][-1]  # SNR=30

        mae_degradation = snr0["MAE"] - snr30["MAE"]  # 正值表示 SNR=0 时更差
        r2_degradation = snr30["R2"] - snr0["R2"]  # 正值表示 SNR=30 时更好
        pcc_degradation = snr30["PCC"] - snr0["PCC"]

        print(f"  {model_name:<15} {mae_degradation:<+15.4f} {r2_degradation:<+15.4f} {pcc_degradation:<+15.4f}")

    print(f"\n{'=' * 80}")
    print("实验完成！")
    print(f"{'=' * 80}")

    return results


if __name__ == "__main__":
    results = run_noise_robustness_experiment()
