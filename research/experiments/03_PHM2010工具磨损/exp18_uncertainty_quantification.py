"""
实验18：不确定性量化实验
使用MC Dropout估计预测不确定性

实验设计：
1. 训练DL-LNN模型（启用Dropout）
2. 使用MC Dropout进行多次前向传播（100次）
3. 计算预测的均值和标准差（不确定性）
4. 分析不确定性与预测误差的关系
5. 识别高不确定性样本
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
# 添加项目根目录（python/）到 path，用于导入 app 模块
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from training.reproducibility import set_global_seed
from training.experiment_tracker import start_run, is_enabled

from config import ModelConfig
from models import DLLNNWithPhysics
from data_generator import Industrial6061T6Dataset, create_dataloaders
from metrics import ChatterMetrics


def train_model_with_dropout(
    model: torch.nn.Module, train_loader, val_loader, config: ModelConfig, device: torch.device, num_epochs: int = 100
) -> torch.nn.Module:
    """
    训练模型（保持Dropout启用）

    Args:
        model: 模型实例
        train_loader: 训练数据加载器
        val_loader: 验证数据加载器
        config: 模型配置
        device: 计算设备
        num_epochs: 训练轮数

    Returns:
        训练完成的模型
    """
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-5)

    best_val_loss = float("inf")
    best_state = None

    print("    开始训练...")
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

            # 前向传播
            output = model(x)
            if isinstance(output, tuple):
                y_pred = output[0]
            else:
                y_pred = output

            # 确保形状匹配
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

        # 保存最佳模型
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        # 每20轮打印一次进度
        if (epoch + 1) % 20 == 0:
            print(f"      Epoch {epoch + 1}/{num_epochs}, Train Loss: {train_loss:.6f}, Val Loss: {val_loss:.6f}")

    # 加载最佳模型状态
    if best_state is not None:
        model.load_state_dict(best_state)

    print(f"    训练完成，最佳验证损失: {best_val_loss:.6f}")
    return model


def mc_dropout_inference(
    model: torch.nn.Module, data_loader, device: torch.device, num_runs: int = 100
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    使用MC Dropout进行推理，收集多次前向传播的结果

    Args:
        model: 训练好的模型
        data_loader: 数据加载器
        device: 计算设备
        num_runs: MC Dropout运行次数

    Returns:
        all_predictions: 所有运行的预测值 [num_runs, num_samples]
        all_targets: 真实值 [num_samples]
        mean_predictions: 预测均值 [num_samples]
        std_predictions: 预测标准差（不确定性）[num_samples]
    """
    print(f"    进行MC Dropout推理（{num_runs}次前向传播）...")

    # 收集所有样本的真实值
    all_targets_list = []
    for batch in data_loader:
        _, y_true, _ = batch
        all_targets_list.append(y_true.numpy())

    all_targets = np.concatenate(all_targets_list, axis=0).flatten()
    num_samples = len(all_targets)

    # 存储所有运行的预测结果
    all_predictions = np.zeros((num_runs, num_samples))

    # 关键：保持Dropout层开启（train模式）
    model.train()

    for run in range(num_runs):
        run_predictions = []

        with torch.no_grad():  # 不计算梯度，但保持Dropout激活
            for batch in data_loader:
                x, _, _ = batch
                x = x.to(device)

                # 前向传播（Dropout保持激活状态）
                output = model(x)
                if isinstance(output, tuple):
                    y_pred = output[0]
                else:
                    y_pred = output

                if y_pred.shape[1] != 1:
                    y_pred = y_pred.view(-1)

                run_predictions.append(y_pred.cpu().numpy())

        # 合并当前运行的所有预测
        run_predictions = np.concatenate(run_predictions, axis=0).flatten()
        all_predictions[run, :] = run_predictions

        # 每20次打印一次进度
        if (run + 1) % 20 == 0:
            print(f"      MC Dropout运行 {run + 1}/{num_runs}")

    # 计算每个样本的均值和标准差
    mean_predictions = np.mean(all_predictions, axis=0)
    std_predictions = np.std(all_predictions, axis=0)

    print(f"    MC Dropout推理完成")
    print(f"      预测均值范围: [{mean_predictions.min():.4f}, {mean_predictions.max():.4f}]")
    print(f"      不确定性范围: [{std_predictions.min():.4f}, {std_predictions.max():.4f}]")

    return all_predictions, all_targets, mean_predictions, std_predictions


def analyze_uncertainty(predictions: np.ndarray, targets: np.ndarray, uncertainties: np.ndarray) -> Dict:
    """
    分析不确定性与预测误差的关系

    Args:
        predictions: 预测均值
        targets: 真实值
        uncertainties: 不确定性（标准差）

    Returns:
        分析结果字典
    """
    print("\n    分析不确定性与误差的关系...")

    # 计算每个样本的绝对误差
    absolute_errors = np.abs(predictions - targets)

    # 计算不确定性与误差的相关系数
    correlation = np.corrcoef(uncertainties, absolute_errors)[0, 1]

    print(f"      不确定性-误差相关系数: {correlation:.4f}")

    # 将样本按不确定性分为三组：低、中、高
    # 使用百分位数划分：33%和67%
    low_threshold = np.percentile(uncertainties, 33)
    high_threshold = np.percentile(uncertainties, 67)

    low_mask = uncertainties <= low_threshold
    medium_mask = (uncertainties > low_threshold) & (uncertainties <= high_threshold)
    high_mask = uncertainties > high_threshold

    # 统计各组的信息
    uncertainty_bins = []

    # 低不确定性组
    if np.sum(low_mask) > 0:
        low_bin = {
            "bin": "low",
            "avg_uncertainty": float(np.mean(uncertainties[low_mask])),
            "avg_error": float(np.mean(absolute_errors[low_mask])),
            "count": int(np.sum(low_mask)),
        }
        uncertainty_bins.append(low_bin)
        print(f"      低不确定性组: {low_bin['count']}个样本, 平均误差: {low_bin['avg_error']:.4f}")

    # 中不确定性组
    if np.sum(medium_mask) > 0:
        medium_bin = {
            "bin": "medium",
            "avg_uncertainty": float(np.mean(uncertainties[medium_mask])),
            "avg_error": float(np.mean(absolute_errors[medium_mask])),
            "count": int(np.sum(medium_mask)),
        }
        uncertainty_bins.append(medium_bin)
        print(f"      中不确定性组: {medium_bin['count']}个样本, 平均误差: {medium_bin['avg_error']:.4f}")

    # 高不确定性组
    if np.sum(high_mask) > 0:
        high_bin = {
            "bin": "high",
            "avg_uncertainty": float(np.mean(uncertainties[high_mask])),
            "avg_error": float(np.mean(absolute_errors[high_mask])),
            "count": int(np.sum(high_mask)),
        }
        uncertainty_bins.append(high_bin)
        print(f"      高不确定性组: {high_bin['count']}个样本, 平均误差: {high_bin['avg_error']:.4f}")

    # 识别高不确定性样本（不确定性 > 75百分位）
    high_unc_threshold = np.percentile(uncertainties, 75)
    high_uncertainty_samples = int(np.sum(uncertainties > high_unc_threshold))

    # 识别低不确定性样本（不确定性 < 25百分位）
    low_unc_threshold = np.percentile(uncertainties, 25)
    low_uncertainty_samples = int(np.sum(uncertainties < low_unc_threshold))

    print(f"      高不确定性样本数（>75%）: {high_uncertainty_samples}")
    print(f"      低不确定性样本数（<25%）: {low_uncertainty_samples}")

    analysis_results = {
        "mean_uncertainty": float(np.mean(uncertainties)),
        "median_uncertainty": float(np.median(uncertainties)),
        "uncertainty_error_correlation": float(correlation),
        "high_uncertainty_samples": high_uncertainty_samples,
        "low_uncertainty_samples": low_uncertainty_samples,
        "uncertainty_bins": uncertainty_bins,
    }

    return analysis_results


def run_uncertainty_quantification_experiment():
    """
    运行不确定性量化实验
    """
    print("=" * 80)
    print("实验18：不确定性量化（MC Dropout）")
    print("=" * 80)

    # 设置设备
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n使用设备: {device}")

    # 配置参数
    config = ModelConfig()
    mc_dropout_runs = 100  # MC Dropout运行次数

    # 准备数据
    print("\n[1/4] 加载工业数据集...")

    # 创建数据加载器
    train_loader, val_loader, test_loader = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={"num_samples": 500, "noise_level": 0.08, "seed": 46},
        batch_size=config.batch_size,
        train_ratio=0.7,
        val_ratio=0.15,
    )

    print(f"    训练集: {len(train_loader.dataset)} 样本")
    print(f"    验证集: {len(val_loader.dataset)} 样本")
    print(f"    测试集: {len(test_loader.dataset)} 样本")

    # 训练模型
    print("\n[2/4] 训练DL-LNN模型（dropout=0.2）...")

    # 创建模型（确保dropout启用）
    model = DLLNNWithPhysics(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
        dt=config.ltc_dt,
        dropout=config.dropout,  # dropout=0.2
    ).to(device)

    print(f"    模型参数量: {sum(p.numel() for p in model.parameters()):,}")

    # 训练模型
    model = train_model_with_dropout(
        model=model, train_loader=train_loader, val_loader=val_loader, config=config, device=device, num_epochs=100
    )

    # MC Dropout推理
    print("\n[3/4] MC Dropout推理...")

    # 进行MC Dropout推理
    all_predictions, all_targets, mean_predictions, std_predictions = mc_dropout_inference(
        model=model, data_loader=test_loader, device=device, num_runs=mc_dropout_runs
    )

    # 分析不确定性
    print("\n[4/4] 分析不确定性...")

    # 分析不确定性与误差的关系
    analysis_results = analyze_uncertainty(
        predictions=mean_predictions, targets=all_targets, uncertainties=std_predictions
    )

    # 计算整体评估指标
    metrics_calc = ChatterMetrics()
    overall_metrics = {
        "MAE": metrics_calc.mae(mean_predictions, all_targets),
        "RMSE": metrics_calc.rmse(mean_predictions, all_targets),
        "R2": metrics_calc.r2_score(mean_predictions, all_targets),
    }

    print(f"\n    整体预测性能:")
    print(f"      MAE: {overall_metrics['MAE']:.4f}")
    print(f"      RMSE: {overall_metrics['RMSE']:.4f}")
    print(f"      R²: {overall_metrics['R2']:.4f}")

    # 保存结果
    results = {
        "timestamp": datetime.now().isoformat(),
        "mc_dropout_runs": mc_dropout_runs,
        "overall_metrics": overall_metrics,
        "uncertainty_analysis": {
            "mean_uncertainty": analysis_results["mean_uncertainty"],
            "median_uncertainty": analysis_results["median_uncertainty"],
            "uncertainty_error_correlation": analysis_results["uncertainty_error_correlation"],
            "high_uncertainty_samples": analysis_results["high_uncertainty_samples"],
            "low_uncertainty_samples": analysis_results["low_uncertainty_samples"],
        },
        "calibration": {"uncertainty_bins": analysis_results["uncertainty_bins"]},
    }

    # 创建输出目录
    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)

    # 保存JSON结果
    output_file = output_dir / "uncertainty_quantification_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 80}")
    print(f"实验完成！结果已保存到: {output_file}")
    print(f"{'=' * 80}")

    # 打印汇总
    print("\n" + "=" * 80)
    print("实验结果汇总")
    print("=" * 80)
    print(f"\nMC Dropout运行次数: {mc_dropout_runs}")
    print(f"\n整体预测性能:")
    print(f"  MAE: {overall_metrics['MAE']:.4f}")
    print(f"  RMSE: {overall_metrics['RMSE']:.4f}")
    print(f"  R²: {overall_metrics['R2']:.4f}")
    print(f"\n不确定性分析:")
    print(f"  平均不确定性: {analysis_results['mean_uncertainty']:.4f}")
    print(f"  中位数不确定性: {analysis_results['median_uncertainty']:.4f}")
    print(f"  不确定性-误差相关系数: {analysis_results['uncertainty_error_correlation']:.4f}")
    print(f"  高不确定性样本数: {analysis_results['high_uncertainty_samples']}")
    print(f"  低不确定性样本数: {analysis_results['low_uncertainty_samples']}")
    print(f"\n不确定性校准:")
    for bin_info in analysis_results["uncertainty_bins"]:
        print(
            f"  {bin_info['bin'].upper():8s}组: "
            f"平均不确定性={bin_info['avg_uncertainty']:.4f}, "
            f"平均误差={bin_info['avg_error']:.4f}, "
            f"样本数={bin_info['count']}"
        )
    print("=" * 80)

    return results


if __name__ == "__main__":
    set_global_seed(42)
    with start_run(experiment_name="exp18_uncertainty_quantification"):
        results = run_uncertainty_quantification_experiment()
