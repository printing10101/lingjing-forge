"""
实验15：统计显著性检验
验证DL-LNN模型性能差异的统计显著性

实验设计：
1. 对每个模型进行5次独立重复实验（不同随机种子）
2. 记录每次实验的MAE, RMSE, R², PCC
3. 计算各指标的均值和标准差
4. 进行独立样本t检验（DL-LNN vs 其他模型）
5. 计算p值和置信区间（95%）
6. 计算效应量（Cohen's d）
"""

import sys
import json
import torch
import torch.nn as nn
import numpy as np
from scipy import stats
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from config import ModelConfig
from models import (
    DLLNNModel,
    DLLNNWithPhysics,
    BaselineLSTM,
    BaselineTransformer,
    BaselinePINN,
    BaselineBPNN,
    BaselineCNN,
    BaselineGRU,
    BaselinegPINN,
    BaselinePeRCNN,
)
from data_generator import Industrial6061T6Dataset, create_dataloaders
from metrics import ChatterMetrics


# 实验参数

# 5个独立实验的随机种子
SEEDS = [42, 43, 44, 45, 46]
NUM_TRIALS = len(SEEDS)

# 参与对比的模型列表
MODEL_NAMES = ["DL-LNN", "LSTM", "GRU", "Transformer", "CNN", "PINN", "gPINN", "PeRCNN", "BPNN"]

# 需要统计检验的指标
METRIC_NAMES = ["MAE", "RMSE", "R2", "PCC"]


# 工具函数


def create_model_by_name(name: str, config: ModelConfig, device: torch.device) -> torch.nn.Module:
    """
    根据名称创建模型实例

    Args:
        name: 模型名称
        config: 模型配置
        device: 计算设备

    Returns:
        模型实例
    """
    kwargs = dict(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        output_dim=config.output_dim,
    )

    if name == "DL-LNN":
        model = DLLNNWithPhysics(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
            dt=config.ltc_dt,
            dropout=config.dropout,
        )
    elif name == "LSTM":
        model = BaselineLSTM(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    elif name == "GRU":
        model = BaselineGRU(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    elif name == "Transformer":
        model = BaselineTransformer(
            input_dim=config.input_dim,
            d_model=config.hidden_dim,
            nhead=4,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    elif name == "CNN":
        model = BaselineCNN(input_dim=config.input_dim, hidden_dim=config.hidden_dim, output_dim=config.output_dim)
    elif name == "PINN":
        model = BaselinePINN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    elif name == "gPINN":
        model = BaselinegPINN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    elif name == "PeRCNN":
        model = BaselinePeRCNN(input_dim=config.input_dim, hidden_dim=config.hidden_dim, output_dim=config.output_dim)
    elif name == "BPNN":
        model = BaselineBPNN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
        )
    else:
        raise ValueError(f"未知模型: {name}")

    return model.to(device)


def train_model(
    model: torch.nn.Module,
    model_name: str,
    train_loader,
    val_loader,
    config: ModelConfig,
    device: torch.device,
    num_epochs: int = 80,
) -> torch.nn.Module:
    """
    训练模型，返回最佳模型

    Args:
        model: 模型实例
        model_name: 模型名称
        train_loader: 训练数据加载器
        val_loader: 验证数据加载器
        config: 模型配置
        device: 计算设备
        num_epochs: 训练轮数

    Returns:
        训练后的模型（加载了最佳验证损失权重）
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

        # 保存最佳模型权重
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        if (epoch + 1) % 20 == 0 or epoch == 0:
            print(f"    Epoch [{epoch + 1}/{num_epochs}] Train: {train_loss:.4f} Val: {val_loss:.4f}")

    # 加载最佳权重
    if best_state is not None:
        model.load_state_dict(best_state)

    return model


def evaluate_model(model: torch.nn.Module, test_loader, device: torch.device) -> Dict[str, float]:
    """
    评估模型，计算MAE, RMSE, R², PCC等指标

    Args:
        model: 训练好的模型
        test_loader: 测试数据加载器
        device: 计算设备

    Returns:
        指标字典 {'MAE': ..., 'RMSE': ..., 'R2': ..., 'PCC': ...}
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


def cohens_d(group1: np.ndarray, group2: np.ndarray) -> float:
    """
    计算Cohen's d效应量
    衡量两组样本均值差异的标准化度量

    Args:
        group1: 第一组样本（DL-LNN的指标值）
        group2: 第二组样本（对比模型的指标值）

    Returns:
        Cohen's d值
        |d| < 0.2: 小效应
        0.2 <= |d| < 0.5: 小-中效应
        0.5 <= |d| < 0.8: 中效应
        |d| >= 0.8: 大效应
    """
    n1, n2 = len(group1), len(group2)
    mean1, mean2 = np.mean(group1), np.mean(group2)
    var1, var2 = np.var(group1, ddof=1), np.var(group2, ddof=1)

    # 合并标准差（pooled standard deviation）
    pooled_std = np.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))

    if pooled_std < 1e-10:
        return 0.0

    d = (mean1 - mean2) / pooled_std
    return float(d)


def independent_t_test(group1: np.ndarray, group2: np.ndarray) -> Dict[str, float]:
    """
    独立样本t检验（双尾）
    检验两组样本的均值是否存在显著差异

    Args:
        group1: 第一组样本（DL-LNN的指标值）
        group2: 第二组样本（对比模型的指标值）

    Returns:
        包含t统计量、p值、是否显著、Cohen's d的字典
    """
    t_stat, p_value = stats.ttest_ind(group1, group2, equal_var=False)  # Welch's t-test

    # 计算95%置信区间（均值差的置信区间）
    mean_diff = np.mean(group1) - np.mean(group2)
    se = np.sqrt(np.var(group1, ddof=1) / len(group1) + np.var(group2, ddof=1) / len(group2))
    df = len(group1) + len(group2) - 2  # 近似自由度
    t_critical = stats.t.ppf(0.975, df)  # 双尾95%
    ci_lower = mean_diff - t_critical * se
    ci_upper = mean_diff + t_critical * se

    # 计算效应量
    d = cohens_d(group1, group2)

    return {
        "t_stat": float(t_stat),
        "p_value": float(p_value),
        "significant": bool(p_value < 0.05),
        "cohens_d": d,
        "mean_diff": float(mean_diff),
        "ci_95_lower": float(ci_lower),
        "ci_95_upper": float(ci_upper),
    }


# 单次实验：在一个随机种子下训练并评估所有模型


def run_single_trial(seed: int, config: ModelConfig, device: torch.device) -> Dict[str, Dict[str, float]]:
    """
    在指定随机种子下，训练并评估所有模型

    Args:
        seed: 随机种子
        config: 模型配置
        device: 计算设备

    Returns:
        {模型名: {指标名: 值}} 的字典
    """
    print(f"\n  --- 种子 {seed} ---")

    # 设置全局随机种子，确保实验可复现
    torch.manual_seed(seed)
    np.random.seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    # 创建数据加载器（使用Industrial6061T6Dataset）
    train_loader, val_loader, test_loader = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={"num_samples": 500, "noise_level": 0.08, "seed": seed},
        batch_size=config.batch_size,
        train_ratio=0.7,
        val_ratio=0.15,
        seed=seed,
    )

    trial_results = {}

    for model_name in MODEL_NAMES:
        print(f"    [{model_name}] 训练中...")

        try:
            # 创建新模型实例
            model = create_model_by_name(model_name, config, device)

            # 训练
            model = train_model(
                model=model,
                model_name=model_name,
                train_loader=train_loader,
                val_loader=val_loader,
                config=config,
                device=device,
                num_epochs=80,
            )

            # 评估
            test_metrics = evaluate_model(model, test_loader, device)
            trial_results[model_name] = test_metrics

            print(
                f"      MAE: {test_metrics['MAE']:.4f}, RMSE: {test_metrics['RMSE']:.4f}, "
                f"R2: {test_metrics['R2']:.4f}, PCC: {test_metrics['PCC']:.4f}"
            )

        except Exception as e:
            print(f"      错误: {str(e)}")
            import traceback

            traceback.print_exc()
            # 记录NaN表示该次实验失败
            trial_results[model_name] = {
                "MAE": float("nan"),
                "RMSE": float("nan"),
                "R2": float("nan"),
                "PCC": float("nan"),
            }

    return trial_results


# 主实验流程


def run_statistical_significance_experiment():
    """
    运行统计显著性检验实验

    流程：
    1. 对每个种子运行一次完整实验（训练+评估所有模型）
    2. 汇总所有种子的指标，计算均值和标准差
    3. 对每个指标进行DL-LNN vs 其他模型的t检验
    4. 保存结果到JSON文件
    """
    print("=" * 80)
    print("实验15：统计显著性检验")
    print(f"实验次数: {NUM_TRIALS} (种子: {SEEDS})")
    print(f"对比模型: {', '.join(MODEL_NAMES)}")
    print("=" * 80)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n使用设备: {device}")

    config = ModelConfig()

    # 对每个种子运行实验，收集所有模型的指标
    print("\n[步骤1] 运行多次独立实验...")

    # all_trial_results[seed_idx][model_name][metric_name] = value
    all_trial_results: List[Dict[str, Dict[str, float]]] = []

    for trial_idx, seed in enumerate(SEEDS):
        print(f"\n{'=' * 60}")
        print(f"第 {trial_idx + 1}/{NUM_TRIALS} 次实验 (seed={seed})")
        print(f"{'=' * 60}")

        trial_result = run_single_trial(seed, config, device)
        all_trial_results.append(trial_result)

    # 汇总统计量（均值 ± 标准差）
    print(f"\n{'=' * 80}")
    print("[步骤2] 计算均值和标准差...")
    print(f"{'=' * 80}")

    # results[model_name][metric_name] = {"mean": ..., "std": ...}
    results: Dict[str, Dict[str, Dict[str, float]]] = {}

    for model_name in MODEL_NAMES:
        results[model_name] = {}
        for metric_name in METRIC_NAMES:
            # 收集该模型在所有种子下的指标值
            values = []
            for trial_result in all_trial_results:
                if model_name in trial_result and metric_name in trial_result[model_name]:
                    val = trial_result[model_name][metric_name]
                    if not np.isnan(val):
                        values.append(val)

            if len(values) > 0:
                results[model_name][metric_name] = {
                    "mean": float(np.mean(values)),
                    "std": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
                    "values": values,  # 保留原始值用于t检验
                }
            else:
                results[model_name][metric_name] = {"mean": float("nan"), "std": float("nan"), "values": []}

    # 打印汇总表
    print(f"\n{'模型':<15}", end="")
    for metric_name in METRIC_NAMES:
        print(f"{metric_name:<25}", end="")
    print()
    print("-" * (15 + 25 * len(METRIC_NAMES)))

    for model_name in MODEL_NAMES:
        print(f"{model_name:<15}", end="")
        for metric_name in METRIC_NAMES:
            mean = results[model_name][metric_name]["mean"]
            std = results[model_name][metric_name]["std"]
            if np.isnan(mean):
                print(f"{'N/A':<25}", end="")
            else:
                print(f"{mean:.4f}±{std:.4f}{'':<12}", end="")
        print()

    # t检验（DL-LNN vs 其他模型）
    print(f"\n{'=' * 80}")
    print("[步骤3] 独立样本t检验 (DL-LNN vs 其他模型)...")
    print(f"{'=' * 80}")

    # t_tests[comparison][metric_name] = {t_stat, p_value, significant, cohens_d, ...}
    t_tests: Dict[str, Dict[str, Dict]] = {}

    ct_ltc_name = "DL-LNN"

    for other_model in MODEL_NAMES:
        if other_model == ct_ltc_name:
            continue

        comparison_key = f"DL-LNN_vs_{other_model}"
        t_tests[comparison_key] = {}

        print(f"\n  {comparison_key}:")

        for metric_name in METRIC_NAMES:
            ct_values = np.array(results[ct_ltc_name][metric_name]["values"])
            other_values = np.array(results[other_model][metric_name]["values"])

            # 检查是否有足够的数据进行t检验
            if len(ct_values) < 2 or len(other_values) < 2:
                print(f"    {metric_name}: 数据不足，跳过t检验")
                t_tests[comparison_key][metric_name] = {
                    "t_stat": float("nan"),
                    "p_value": float("nan"),
                    "significant": False,
                    "cohens_d": float("nan"),
                    "mean_diff": float("nan"),
                    "ci_95_lower": float("nan"),
                    "ci_95_upper": float("nan"),
                }
                continue

            # 执行t检验
            test_result = independent_t_test(ct_values, other_values)
            t_tests[comparison_key][metric_name] = test_result

            # 打印结果
            sig_mark = (
                "***"
                if test_result["p_value"] < 0.001
                else ("**" if test_result["p_value"] < 0.01 else ("*" if test_result["p_value"] < 0.05 else "ns"))
            )
            print(
                f"    {metric_name}: t={test_result['t_stat']:.4f}, "
                f"p={test_result['p_value']:.6f} [{sig_mark}], "
                f"d={test_result['cohens_d']:.4f}, "
                f"diff=[{test_result['ci_95_lower']:.4f}, {test_result['ci_95_upper']:.4f}]"
            )

    # 保存结果到JSON
    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / "statistical_significance_results.json"

    # 构建输出JSON（移除原始values列表，只保留统计量）
    output_results = {}
    for model_name in MODEL_NAMES:
        output_results[model_name] = {}
        for metric_name in METRIC_NAMES:
            output_results[model_name][metric_name] = {
                "mean": results[model_name][metric_name]["mean"],
                "std": results[model_name][metric_name]["std"],
            }

    # 构建t检验输出（处理NaN的JSON序列化）
    output_t_tests = {}
    for comp_key, comp_results in t_tests.items():
        output_t_tests[comp_key] = {}
        for metric_name, test_result in comp_results.items():
            output_t_tests[comp_key][metric_name] = {}
            for k, v in test_result.items():
                if isinstance(v, float) and np.isnan(v):
                    output_t_tests[comp_key][metric_name][k] = None
                else:
                    output_t_tests[comp_key][metric_name][k] = v

    output_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "num_trials": NUM_TRIALS,
        "seeds": SEEDS,
        "dataset": "Industrial6061T6",
        "num_samples": 500,
        "results": output_results,
        "t_tests": output_t_tests,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 80}")
    print(f"实验完成！结果已保存到: {output_file}")
    print(f"{'=' * 80}")

    # 打印显著性汇总
    print("\n显著性汇总 (p < 0.05 标记为 *)：")
    print("-" * 100)
    header = f"{'对比':<25}" + "".join([f"{m:<18}" for m in METRIC_NAMES])
    print(header)
    print("-" * 100)

    for comp_key in t_tests:
        row = f"{comp_key:<25}"
        for metric_name in METRIC_NAMES:
            test_result = t_tests[comp_key][metric_name]
            if test_result["significant"]:
                d = test_result["cohens_d"]
                row += f"p={test_result['p_value']:.4f}*{'':<6}"
            else:
                p_val = test_result["p_value"]
                if np.isnan(p_val):
                    row += f"{'N/A':<18}"
                else:
                    row += f"p={p_val:.4f}{'':<10}"
        print(row)

    print("-" * 100)

    # 打印效应量汇总
    print("\n效应量汇总 (Cohen's d)：")
    print("-" * 100)
    print(header)
    print("-" * 100)

    for comp_key in t_tests:
        row = f"{comp_key:<25}"
        for metric_name in METRIC_NAMES:
            test_result = t_tests[comp_key][metric_name]
            d = test_result["cohens_d"]
            if np.isnan(d):
                row += f"{'N/A':<18}"
            else:
                # 标注效应量大小
                abs_d = abs(d)
                if abs_d >= 0.8:
                    size = "大"
                elif abs_d >= 0.5:
                    size = "中"
                elif abs_d >= 0.2:
                    size = "小"
                else:
                    size = "微"
                row += f"{d:>7.4f}({size}){'':<4}"
        print(row)

    print("-" * 100)

    return output_data


if __name__ == "__main__":
    results = run_statistical_significance_experiment()
