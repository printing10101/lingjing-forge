"""
实验19：失败案例与边界分析
分析DL-LNN模型预测失败的情况，识别模型失效模式
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

from config import ModelConfig
from models import DLLNNWithPhysics
from data_generator import Industrial6061T6Dataset, create_dataloaders
from metrics import ChatterMetrics


def train_model(
    model: torch.nn.Module, train_loader, val_loader, config: ModelConfig, device: torch.device, num_epochs: int = 80
) -> torch.nn.Module:
    """
    训练DL-LNN模型

    Args:
        model: 待训练模型
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

    for epoch in range(num_epochs):
        # 训练阶段
        model.train()
        train_loss = 0.0
        n_batches = 0

        for batch in train_loader:
            x, y_true, y_physics = batch
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

            # 数据损失
            loss_data = criterion(y_pred, y_true)

            # 物理一致性损失（预测值与物理模型预测的差异）
            y_physics_dev = y_physics.to(device)
            loss_phys = criterion(y_pred, y_physics_dev)

            # 综合损失
            loss = config.lambda_data * loss_data + config.lambda_phys * loss_phys

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
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

        # 保存最优模型
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    return model


def predict_on_testset(
    model: torch.nn.Module, test_loader, device: torch.device
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    在测试集上进行预测，收集所有样本的预测值、真实值和输入参数

    Args:
        model: 训练好的模型
        test_loader: 测试数据加载器
        device: 计算设备

    Returns:
        predictions: 模型预测值
        targets: 真实标签
        physics_preds: 物理模型预测值
        spindle_speeds: 主轴转速
        axial_depths: 轴向切深
    """
    model.eval()
    all_preds = []
    all_targets = []
    all_phys = []
    all_speeds = []
    all_depths = []

    with torch.no_grad():
        for batch in test_loader:
            x, y_true, y_physics = batch
            x = x.to(device)

            # 从归一化特征还原原始参数
            # features[:, 0] = spindle_speed / 10000
            # features[:, 1] = axial_depth / 10
            spindle_speeds = x[:, 0].cpu().numpy() * 10000.0
            axial_depths = x[:, 1].cpu().numpy() * 10.0

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
            all_speeds.append(spindle_speeds)
            all_depths.append(axial_depths)

    predictions = np.concatenate(all_preds, axis=0).flatten()
    targets = np.concatenate(all_targets, axis=0).flatten()
    physics_preds = np.concatenate(all_phys, axis=0).flatten()
    spindle_speeds = np.concatenate(all_speeds, axis=0).flatten()
    axial_depths = np.concatenate(all_depths, axis=0).flatten()

    return predictions, targets, physics_preds, spindle_speeds, axial_depths


def analyze_failure_cases(
    predictions: np.ndarray,
    targets: np.ndarray,
    physics_preds: np.ndarray,
    spindle_speeds: np.ndarray,
    axial_depths: np.ndarray,
    failure_ratio: float = 0.1,
) -> Dict:
    """
    分析失败案例的特征

    Args:
        predictions: 模型预测值
        targets: 真实标签
        physics_preds: 物理模型预测值
        spindle_speeds: 主轴转速
        axial_depths: 轴向切深
        failure_ratio: 失败案例比例（默认Top 10%）

    Returns:
        失败案例分析结果字典
    """
    # 计算每个样本的绝对误差
    per_sample_errors = np.abs(predictions - targets)
    total_samples = len(per_sample_errors)

    # 识别失败案例（误差最大的Top 10%）
    num_failures = max(1, int(total_samples * failure_ratio))
    failure_indices = np.argsort(per_sample_errors)[-num_failures:]

    # 提取失败案例的数据
    failure_errors = per_sample_errors[failure_indices]
    failure_speeds = spindle_speeds[failure_indices]
    failure_depths = axial_depths[failure_indices]
    failure_preds = predictions[failure_indices]
    failure_targets = targets[failure_indices]
    failure_phys = physics_preds[failure_indices]

    # 计算失败案例的物理一致性系数（PCC）
    epsilon = 1e-8
    relative_errors = np.abs(failure_preds - failure_phys) / (np.abs(failure_phys) + epsilon)
    failure_pcc = float(1.0 - np.mean(relative_errors))

    # 成功样本的统计
    success_indices = np.argsort(per_sample_errors)[: total_samples - num_failures]
    success_speeds = spindle_speeds[success_indices]
    success_depths = axial_depths[success_indices]

    result = {
        "num_failures": int(num_failures),
        "total_samples": int(total_samples),
        "failure_rate": float(failure_ratio),
        "avg_error": float(np.mean(failure_errors)),
        "std_error": float(np.std(failure_errors)),
        "max_error": float(np.max(failure_errors)),
        "min_error": float(np.min(failure_errors)),
        "input_distribution": {
            "spindle_speed_mean": float(np.mean(failure_speeds)),
            "spindle_speed_std": float(np.std(failure_speeds)),
            "spindle_speed_min": float(np.min(failure_speeds)),
            "spindle_speed_max": float(np.max(failure_speeds)),
            "axial_depth_mean": float(np.mean(failure_depths)),
            "axial_depth_std": float(np.std(failure_depths)),
            "axial_depth_min": float(np.min(failure_depths)),
            "axial_depth_max": float(np.max(failure_depths)),
        },
        "comparison_with_success": {
            "failure_speed_mean": float(np.mean(failure_speeds)),
            "success_speed_mean": float(np.mean(success_speeds)),
            "failure_depth_mean": float(np.mean(failure_depths)),
            "success_depth_mean": float(np.mean(success_depths)),
            "speed_diff": float(np.mean(failure_speeds) - np.mean(success_speeds)),
            "depth_diff": float(np.mean(failure_depths) - np.mean(success_depths)),
        },
        "physics_consistency": {
            "failure_pcc": failure_pcc,
            "failure_pred_mean": float(np.mean(failure_preds)),
            "failure_target_mean": float(np.mean(failure_targets)),
            "failure_phys_mean": float(np.mean(failure_phys)),
        },
    }

    return result


def analyze_boundary_cases(
    predictions: np.ndarray,
    targets: np.ndarray,
    physics_preds: np.ndarray,
    spindle_speeds: np.ndarray,
    axial_depths: np.ndarray,
) -> Dict:
    """
    分析边界情况下模型的性能

    Args:
        predictions: 模型预测值
        targets: 真实标签
        physics_preds: 物理模型预测值
        spindle_speeds: 主轴转速
        axial_depths: 轴向切深

    Returns:
        边界分析结果字典
    """
    per_sample_errors = np.abs(predictions - targets)

    # 按转速分箱分析
    speed_bins = []
    # 定义转速区间（根据数据范围自适应）
    speed_min = float(np.min(spindle_speeds))
    speed_max = float(np.max(spindle_speeds))
    speed_range = speed_max - speed_min
    n_speed_bins = 5
    speed_bin_width = speed_range / n_speed_bins

    for i in range(n_speed_bins):
        low = speed_min + i * speed_bin_width
        high = low + speed_bin_width
        if i == n_speed_bins - 1:
            # 最后一个区间包含右边界
            mask = (spindle_speeds >= low) & (spindle_speeds <= high)
        else:
            mask = (spindle_speeds >= low) & (spindle_speeds < high)

        count = int(np.sum(mask))
        if count > 0:
            avg_err = float(np.mean(per_sample_errors[mask]))
            std_err = float(np.std(per_sample_errors[mask]))
        else:
            avg_err = 0.0
            std_err = 0.0

        speed_bins.append(
            {
                "range": f"{low:.0f}-{high:.0f}",
                "avg_error": round(avg_err, 4),
                "std_error": round(std_err, 4),
                "count": count,
            }
        )

    # 按切深分箱分析
    depth_bins = []
    depth_min = float(np.min(axial_depths))
    depth_max = float(np.max(axial_depths))
    depth_range = depth_max - depth_min
    n_depth_bins = 5
    depth_bin_width = depth_range / n_depth_bins

    for i in range(n_depth_bins):
        low = depth_min + i * depth_bin_width
        high = low + depth_bin_width
        if i == n_depth_bins - 1:
            mask = (axial_depths >= low) & (axial_depths <= high)
        else:
            mask = (axial_depths >= low) & (axial_depths < high)

        count = int(np.sum(mask))
        if count > 0:
            avg_err = float(np.mean(per_sample_errors[mask]))
            std_err = float(np.std(per_sample_errors[mask]))
        else:
            avg_err = 0.0
            std_err = 0.0

        depth_bins.append(
            {
                "range": f"{low:.2f}-{high:.2f}",
                "avg_error": round(avg_err, 4),
                "std_error": round(std_err, 4),
                "count": count,
            }
        )

    # 稳定性边界附近分析
    # 使用物理模型预测值作为稳定性边界的参考
    # 当样本的切深接近极限切深时，认为在稳定性边界附近
    epsilon_boundary = 0.15  # 相对阈值：切深在极限切深的85%-115%范围内视为边界附近
    boundary_mask = (axial_depths >= physics_preds * (1.0 - epsilon_boundary)) & (
        axial_depths <= physics_preds * (1.0 + epsilon_boundary)
    )
    boundary_count = int(np.sum(boundary_mask))
    if boundary_count > 0:
        boundary_avg_error = float(np.mean(per_sample_errors[boundary_mask]))
        boundary_std_error = float(np.std(per_sample_errors[boundary_mask]))
    else:
        boundary_avg_error = 0.0
        boundary_std_error = 0.0

    result = {
        "speed_bins": speed_bins,
        "depth_bins": depth_bins,
        "stability_boundary": {
            "threshold_ratio": epsilon_boundary,
            "count": boundary_count,
            "avg_error": round(boundary_avg_error, 4),
            "std_error": round(boundary_std_error, 4),
            "description": "切深在极限切深的85%-115%范围内",
        },
    }

    return result


def analyze_failure_patterns(
    predictions: np.ndarray, targets: np.ndarray, spindle_speeds: np.ndarray, axial_depths: np.ndarray
) -> Dict:
    """
    总结模型失效模式
    分析不同参数组合区域的误差特征

    Args:
        predictions: 模型预测值
        targets: 真实标签
        spindle_speeds: 主轴转速
        axial_depths: 轴向切深

    Returns:
        失效模式分析结果字典
    """
    per_sample_errors = np.abs(predictions - targets)

    # 计算转速和切深的分位数，用于划分高/低区域
    speed_median = float(np.median(spindle_speeds))
    depth_median = float(np.median(axial_depths))

    # 高速 + 低切深区域
    mask_high_speed_low_depth = (spindle_speeds >= speed_median) & (axial_depths < depth_median)
    count_hsl = int(np.sum(mask_high_speed_low_depth))
    if count_hsl > 0:
        avg_err_hsl = float(np.mean(per_sample_errors[mask_high_speed_low_depth]))
    else:
        avg_err_hsl = 0.0

    # 低速 + 高切深区域
    mask_low_speed_high_depth = (spindle_speeds < speed_median) & (axial_depths >= depth_median)
    count_lsh = int(np.sum(mask_low_speed_high_depth))
    if count_lsh > 0:
        avg_err_lsh = float(np.mean(per_sample_errors[mask_low_speed_high_depth]))
    else:
        avg_err_lsh = 0.0

    # 高速 + 高切深区域
    mask_high_speed_high_depth = (spindle_speeds >= speed_median) & (axial_depths >= depth_median)
    count_hsh = int(np.sum(mask_high_speed_high_depth))
    if count_hsh > 0:
        avg_err_hsh = float(np.mean(per_sample_errors[mask_high_speed_high_depth]))
    else:
        avg_err_hsh = 0.0

    # 低速 + 低切深区域
    mask_low_speed_low_depth = (spindle_speeds < speed_median) & (axial_depths < depth_median)
    count_lsl = int(np.sum(mask_low_speed_low_depth))
    if count_lsl > 0:
        avg_err_lsl = float(np.mean(per_sample_errors[mask_low_speed_low_depth]))
    else:
        avg_err_lsl = 0.0

    # 极高转速区域（Top 10%）
    speed_p90 = float(np.percentile(spindle_speeds, 90))
    mask_extreme_speed = spindle_speeds >= speed_p90
    count_es = int(np.sum(mask_extreme_speed))
    if count_es > 0:
        avg_err_es = float(np.mean(per_sample_errors[mask_extreme_speed]))
    else:
        avg_err_es = 0.0

    # 极大切深区域（Top 10%）
    depth_p90 = float(np.percentile(axial_depths, 90))
    mask_extreme_depth = axial_depths >= depth_p90
    count_ed = int(np.sum(mask_extreme_depth))
    if count_ed > 0:
        avg_err_ed = float(np.mean(per_sample_errors[mask_extreme_depth]))
    else:
        avg_err_ed = 0.0

    result = {
        "high_speed_low_depth": {
            "count": count_hsl,
            "avg_error": round(avg_err_hsl, 4),
            "speed_threshold": f">={speed_median:.0f} rpm",
            "depth_threshold": f"<{depth_median:.2f} mm",
        },
        "low_speed_high_depth": {
            "count": count_lsh,
            "avg_error": round(avg_err_lsh, 4),
            "speed_threshold": f"<{speed_median:.0f} rpm",
            "depth_threshold": f">={depth_median:.2f} mm",
        },
        "high_speed_high_depth": {
            "count": count_hsh,
            "avg_error": round(avg_err_hsh, 4),
            "speed_threshold": f">={speed_median:.0f} rpm",
            "depth_threshold": f">={depth_median:.2f} mm",
        },
        "low_speed_low_depth": {
            "count": count_lsl,
            "avg_error": round(avg_err_lsl, 4),
            "speed_threshold": f"<{speed_median:.0f} rpm",
            "depth_threshold": f"<{depth_median:.2f} mm",
        },
        "extreme_high_speed": {
            "count": count_es,
            "avg_error": round(avg_err_es, 4),
            "speed_threshold": f">={speed_p90:.0f} rpm (P90)",
        },
        "extreme_high_depth": {
            "count": count_ed,
            "avg_error": round(avg_err_ed, 4),
            "depth_threshold": f">={depth_p90:.2f} mm (P90)",
        },
        "summary": {
            "speed_median": round(speed_median, 2),
            "depth_median": round(depth_median, 2),
            "worst_region": "",
            "analysis": "",
        },
    }

    # 自动识别最差区域
    region_errors = {
        "high_speed_low_depth": avg_err_hsl,
        "low_speed_high_depth": avg_err_lsh,
        "high_speed_high_depth": avg_err_hsh,
        "low_speed_low_depth": avg_err_lsl,
    }
    worst_region = max(region_errors, key=region_errors.get)
    result["summary"]["worst_region"] = worst_region
    result["summary"]["analysis"] = (
        f"模型在{worst_region}区域误差最大（{region_errors[worst_region]:.4f}），表明模型在该参数组合下的泛化能力较弱。"
    )

    return result


def run_failure_analysis_experiment():
    """
    运行失败案例分析实验

    实验步骤：
    1. 训练DL-LNN模型
    2. 在测试集上进行预测
    3. 计算每个样本的误差
    4. 识别失败案例（误差最大的10%）
    5. 分析失败案例的输入参数分布、物理一致性
    6. 分析边界情况（极高/极低转速、极大/极小切深、稳定性边界）
    7. 总结模型失效模式
    8. 保存结果到JSON文件
    """
    print("=" * 80)
    print("实验19：失败案例与边界分析")
    print("=" * 80)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n使用设备: {device}")

    config = ModelConfig()

    # 创建数据集并训练模型
    print("\n[步骤1/6] 加载工业数据集并创建数据加载器...")
    train_loader, val_loader, test_loader = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={"num_samples": 500, "noise_level": 0.08, "seed": 46},
        batch_size=config.batch_size,
        train_ratio=0.7,
        val_ratio=0.15,
    )
    print(f"  训练集批次: {len(train_loader)}")
    print(f"  验证集批次: {len(val_loader)}")
    print(f"  测试集批次: {len(test_loader)}")

    # 创建并训练DL-LNN模型
    print("\n[步骤2/6] 训练DL-LNN模型...")
    model = DLLNNWithPhysics(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
        dt=config.ltc_dt,
        dropout=config.dropout,
    ).to(device)

    model = train_model(
        model=model, train_loader=train_loader, val_loader=val_loader, config=config, device=device, num_epochs=80
    )
    print("  模型训练完成。")

    # 在测试集上进行预测
    print("\n[步骤3/6] 在测试集上进行预测...")
    predictions, targets, physics_preds, spindle_speeds, axial_depths = predict_on_testset(model, test_loader, device)

    total_samples = len(predictions)
    print(f"  测试集样本数: {total_samples}")

    # 计算整体评价指标
    metrics_calc = ChatterMetrics()
    overall_metrics = metrics_calc.compute_all(predictions, targets, physics_preds)
    print(f"  整体 MAE: {overall_metrics['mae']:.4f}")
    print(f"  整体 RMSE: {overall_metrics['rmse']:.4f}")
    print(f"  整体 R2: {overall_metrics['r2']:.4f}")
    print(f"  整体 PCC: {overall_metrics['pcc']:.4f}")

    # 步骤3-4：分析失败案例
    print("\n[步骤4/6] 分析失败案例（误差最大的Top 10%）...")
    failure_results = analyze_failure_cases(
        predictions=predictions,
        targets=targets,
        physics_preds=physics_preds,
        spindle_speeds=spindle_speeds,
        axial_depths=axial_depths,
        failure_ratio=0.1,
    )
    print(f"  失败案例数: {failure_results['num_failures']} / {failure_results['total_samples']}")
    print(f"  平均误差: {failure_results['avg_error']:.4f}")
    print(f"  失败案例主轴转速均值: {failure_results['input_distribution']['spindle_speed_mean']:.1f} rpm")
    print(f"  失败案例切深均值: {failure_results['input_distribution']['axial_depth_mean']:.2f} mm")
    print(f"  失败案例PCC: {failure_results['physics_consistency']['failure_pcc']:.4f}")

    # 分析边界情况
    print("\n[步骤5/6] 分析边界情况...")
    boundary_results = analyze_boundary_cases(
        predictions=predictions,
        targets=targets,
        physics_preds=physics_preds,
        spindle_speeds=spindle_speeds,
        axial_depths=axial_depths,
    )

    print("  转速分箱分析:")
    for bin_info in boundary_results["speed_bins"]:
        print(f"    {bin_info['range']} rpm: avg_error={bin_info['avg_error']:.4f}, count={bin_info['count']}")

    print("  切深分箱分析:")
    for bin_info in boundary_results["depth_bins"]:
        print(f"    {bin_info['range']} mm: avg_error={bin_info['avg_error']:.4f}, count={bin_info['count']}")

    print(
        f"  稳定性边界附近: "
        f"avg_error={boundary_results['stability_boundary']['avg_error']:.4f}, "
        f"count={boundary_results['stability_boundary']['count']}"
    )

    # 总结失效模式
    print("\n[步骤6/6] 总结模型失效模式...")
    pattern_results = analyze_failure_patterns(
        predictions=predictions, targets=targets, spindle_speeds=spindle_speeds, axial_depths=axial_depths
    )

    print(f"  最差区域: {pattern_results['summary']['worst_region']}")
    print(f"  分析: {pattern_results['summary']['analysis']}")

    # 保存结果
    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)

    results = {
        "timestamp": datetime.now().isoformat(),
        "overall_metrics": {
            "MAE": round(overall_metrics["mae"], 4),
            "RMSE": round(overall_metrics["rmse"], 4),
            "R2": round(overall_metrics["r2"], 4),
            "PCC": round(overall_metrics["pcc"], 4),
            "MAPE": round(overall_metrics["mape"], 4),
        },
        "failure_cases": failure_results,
        "boundary_analysis": boundary_results,
        "failure_patterns": pattern_results,
    }

    output_file = output_dir / "failure_analysis_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 80}")
    print(f"实验完成！结果已保存到: {output_file}")
    print(f"{'=' * 80}")

    # 打印汇总表格
    print("\n失效模式汇总:")
    print("-" * 70)
    print(f"{'参数区域':<25} {'样本数':<10} {'平均误差':<12}")
    print("-" * 70)
    for region_name in ["high_speed_low_depth", "low_speed_high_depth", "high_speed_high_depth", "low_speed_low_depth"]:
        region = pattern_results[region_name]
        print(f"{region_name:<25} {region['count']:<10} {region['avg_error']:<12.4f}")
    print("-" * 70)

    return results


if __name__ == "__main__":
    set_global_seed(42)
    results = run_failure_analysis_experiment()
