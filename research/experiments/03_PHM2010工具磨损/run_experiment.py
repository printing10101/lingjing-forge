"""
主实验脚本
运行所有数据集上的所有模型对比实验
"""

import sys
import os
import json
import types

# WinSock 损坏绕过补丁
# 本机 _overlapped 模块因系统级 WinSock 损坏无法导入（WinError 10038），
# 导致 torch -> asyncio -> _overlapped 导入链失败。
# 此处注入一个空实现的 _overlapped 模块以绕过导入阶段失败；
# 实验脚本仅使用同步张量运算，不依赖 asyncio ProactorEventLoop，
# 因此空实现不影响训练/评估逻辑。
# 根因修复：以管理员身份运行 `netsh winsock reset` 并重启系统。
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
from typing import Dict, List
import time

# 添加项目路径
_EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_EXPERIMENTS_DIR)
sys.path.insert(0, _PROJECT_ROOT)
# trainer.py / models.py / losses.py / config.py 等模块使用扁平导入（from models import ...），
# 需将 experiments/ 目录本身也加入 sys.path
sys.path.insert(0, _EXPERIMENTS_DIR)

from training.reproducibility import set_global_seed, get_worker_init_fn
from training.experiment_tracker import (
    start_run,
    log_params,
    log_metrics,
    log_model,
    is_enabled,
)
from experiments.config import get_config
from experiments.data_generator import (
    SyntheticChatterDataset,
    IndustrialChatterDataset,
    PHM2010Dataset,
)
from experiments.models import create_model
from experiments.trainer import DLLNNTrainer, BaselineTrainer, SklearnBaselineTrainer, SKLEARN_BASELINE_MODELS
from experiments.metrics import ChatterMetrics
from experiments.losses import PCC_Loss


def run_single_dataset_experiment(
    config, dataset_name: str, dataset_class, dataset_kwargs: dict
) -> Dict[str, Dict[str, float]]:
    """
    在单个数据集上运行所有模型实验

    Args:
        config: 实验配置
        dataset_name: 数据集名称
        dataset_class: 数据集类
        dataset_kwargs: 数据集参数

    Returns:
        实验结果字典
    """
    print(f"\n{'=' * 80}")
    print(f"数据集: {dataset_name}")
    print(f"{'=' * 80}\n")

    # 创建数据集
    full_dataset = dataset_class(**dataset_kwargs)

    # 划分训练集、验证集、测试集
    train_size = int(0.7 * len(full_dataset))
    val_size = int(0.15 * len(full_dataset))
    test_size = len(full_dataset) - train_size - val_size

    # 使用固定 generator 确保划分可复现
    split_generator = torch.Generator().manual_seed(42)
    train_dataset, val_dataset, test_dataset = torch.utils.data.random_split(
        full_dataset, [train_size, val_size, test_size], generator=split_generator
    )

    # 创建数据加载器（使用 generator 和 worker_init_fn 确保可复现）
    train_loader = DataLoader(
        train_dataset,
        batch_size=32,
        shuffle=True,
        generator=torch.Generator().manual_seed(42),
        worker_init_fn=get_worker_init_fn(42),
    )
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)

    # 定义所有模型
    model_names = [
        "DL-LNN",  # 本文方法
        "LSTM",
        "Transformer",
        "PINN",
        "BPNN",
        # AR-04: 论文第4节声明的传统 ML 基线
        "SVR",
        "RF",
        "XGBoost",
        "GP",
    ]

    results = {}

    # 训练和评估每个模型
    for model_name in model_names:
        print(f"\n训练模型: {model_name}")
        print("-" * 60)

        # AR-02: 每个模型-数据集组合单独开启一个 MLflow run，
        # 供审稿人验证报告指标。MLflow 未安装时为空操作。
        with start_run(
            run_name=f"{dataset_name}_{model_name}",
            experiment_name="AR02_retrain",
        ) as run:
            # 记录数据集与模型元信息
            log_params(
                {
                    "dataset_name": dataset_name,
                    "model_name": model_name,
                    "train_size": train_size,
                    "val_size": val_size,
                    "test_size": test_size,
                    "seed": 42,
                }
            )

            # 选择训练器
            if model_name == "DL-LNN":
                trainer = DLLNNTrainer(config, device=config.model.device)
                # 阶段一：预训练（使用 config 声明的 100 epochs，论文第4节）
                trainer.train_stage1(train_loader, val_loader)
                # 阶段二：微调（使用 config 声明的 200 epochs，论文第4节）
                trainer.train_stage2(train_loader, val_loader)
                model = trainer.model
            elif model_name in SKLEARN_BASELINE_MODELS:
                # AR-04: sklearn 基线走 fit/predict 路径，不走梯度下降
                trainer = SklearnBaselineTrainer(model_name, config, device="cpu")
                trainer.train(train_loader, val_loader, num_epochs=1)
                model = trainer.model
            else:
                trainer = BaselineTrainer(model_name, config, device=config.model.device)
                # 公平比较：非 sklearn 基线与 DL-LNN 总训练轮数对齐（300 epochs）
                trainer.train(train_loader, val_loader, num_epochs=300)
                model = trainer.model

            # 评估
            model.eval()
            metrics_calculator = ChatterMetrics()
            all_preds = []
            all_targets = []

            if model_name in SKLEARN_BASELINE_MODELS:
                # AR-04: sklearn 模型用 predict() 评估，不走 forward()
                with torch.no_grad():
                    for batch in test_loader:
                        if len(batch) == 3:
                            x, y_true, _ = batch
                        else:
                            x, y_true = batch
                        x_numpy = x.cpu().numpy()
                        y_pred = model.predict(x_numpy)
                        all_preds.append(y_pred)
                        all_targets.append(y_true.numpy())
            else:
                with torch.no_grad():
                    for batch in test_loader:
                        x, y_true, _ = batch
                        x = x.to(config.model.device)
                        y_pred, _ = model(x) if model_name == "DL-LNN" else (model(x), None)
                        all_preds.append(y_pred.cpu().numpy())
                        all_targets.append(y_true.numpy())

            all_preds = np.concatenate(all_preds, axis=0)
            all_targets = np.concatenate(all_targets, axis=0)

            # 神经网络模型（DL-LNN/LSTM/Transformer/PINN/BPNN）在归一化 target 空间训练，
            # y_pred 需反归一化到原始 a_lim 尺度 (mm) 才能与 y_true 计算 MAE/RMSE/R²
            # sklearn 基线（SVR/RF/XGBoost/GP）直接在原始尺度 fit/predict，无需反归一化
            if model_name not in SKLEARN_BASELINE_MODELS and hasattr(trainer, "denormalize"):
                all_preds = trainer.denormalize(all_preds)

            metrics = metrics_calculator.compute_all(all_preds, all_targets)
            results[model_name] = metrics

            # AR-02: 记录最终评估指标到 MLflow，供审稿人核对
            log_metrics({f"test_{k}": float(v) for k, v in metrics.items()})
            if is_enabled():
                try:
                    log_model(model, artifact_path=f"model_{model_name}")
                except Exception as e:
                    print(f"  [警告] MLflow log_model 失败: {e}")

            print(f"\n{model_name} 评估结果:")
            for metric_name, value in metrics.items():
                print(f"  {metric_name}: {value:.4f}")

    return results


def run_all_experiments(config) -> Dict[str, Dict[str, Dict[str, float]]]:
    """
    运行所有实验

    Args:
        config: 实验配置

    Returns:
        所有实验结果
    """
    all_results = {}

    # 实验1: 合成数据集
    print("\n" + "=" * 80)
    print("实验1: 合成数据集 (Synthetic)")
    print("=" * 80)

    synthetic_results = run_single_dataset_experiment(
        config,
        "Synthetic",
        SyntheticChatterDataset,
        {
            "num_samples": 1000,
            "spindle_speed_range": (1000, 10000),
            "axial_depth_range": (0.1, 10.0),
            "noise_level": 0.02,
        },
    )
    all_results["Synthetic"] = synthetic_results

    # 实验2: 工业数据集
    print("\n" + "=" * 80)
    print("实验2: 工业数据集 (Industrial)")
    print("=" * 80)

    industrial_results = run_single_dataset_experiment(
        config,
        "Industrial",
        IndustrialChatterDataset,
        {"num_samples": 500, "num_conditions": 30, "material": "6061-T6"},
    )
    all_results["Industrial"] = industrial_results

    # 实验3: PHM2010 公开 benchmark 数据集（真实信号 + 物理派生标签）
    # 学术诚信声明：PHM2010 原始任务为刀具磨损预测，不包含颤振标签。
    # 本实验输入特征来自真实 PHM2010 信号（force/vibration/ae）的统计量，
    # 颤振稳定性标签 a_lim 由 Tlusty 解析模型基于振动能量派生（代理标签）。
    # 论文需明确标注此派生关系，避免误导审稿人。
    print("\n" + "=" * 80)
    print("实验3: PHM2010 公开 benchmark 数据集 (PHM2010)")
    print("=" * 80)

    phm2010_results = run_single_dataset_experiment(
        config,
        "PHM2010",
        PHM2010Dataset,
        {
            "num_samples": 2000,
            "noise_level": 0.05,
            "window_size": 500,
        },
    )
    all_results["PHM2010"] = phm2010_results

    # 保存结果
    results_path = os.path.join(config.output_dir, "all_experiments_results.json")

    # 转换结果为可序列化格式
    serializable_results = {}
    for dataset_name, dataset_results in all_results.items():
        serializable_results[dataset_name] = {}
        for model_name, metrics in dataset_results.items():
            serializable_results[dataset_name][model_name] = {k: float(v) for k, v in metrics.items()}

    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(serializable_results, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 80}")
    print(f"所有实验完成！")
    print(f"结果保存至: {results_path}")
    print(f"{'=' * 80}\n")

    return all_results


def print_final_summary(all_results: Dict[str, Dict[str, Dict[str, float]]]):
    """
    打印最终汇总结果
    """
    print("\n" + "=" * 100)
    print("最终实验结果汇总")
    print("=" * 100)

    for dataset_name, dataset_results in all_results.items():
        print(f"\n数据集: {dataset_name}")
        print("-" * 100)
        print(f"{'模型':<15} {'MAE':>10} {'RMSE':>10} {'R2':>10} {'MAPE':>10}")
        print("-" * 100)
        for model_name, metrics in dataset_results.items():
            print(
                f"{model_name:<15} {metrics.get('mae', 0):>10.4f} {metrics.get('rmse', 0):>10.4f} {metrics.get('r2', 0):>10.4f} {metrics.get('mape', 0):>10.4f}"
            )

        best_model = min(dataset_results.items(), key=lambda x: x[1].get("mae", float("inf")))
        print(f"\n最佳模型: {best_model[0]} (MAE: {best_model[1]['mae']:.4f})")

    print("\n" + "=" * 100)


def generate_paper_tables(all_results: Dict[str, Dict[str, Dict[str, float]]]):
    """
    生成论文表格数据
    """
    print("\n" + "=" * 100)
    print("论文表格数据")
    print("=" * 100)

    # 表2: 主实验结果（2个数据集 × 5种方法）
    print("\n表2: 不同方法在数据集上的MAE对比 (mm)")
    print("-" * 100)

    datasets = list(all_results.keys())
    models = list(all_results[datasets[0]].keys())

    # 打印表头
    header = "方法"
    for dataset in datasets:
        header += f" & {dataset}"
    header += " & 平均MAE"
    print(header)
    print("-" * 100)

    # 打印每个模型的结果
    for model in models:
        row = model
        mae_sum = 0

        for dataset in datasets:
            mae = all_results[dataset][model].get("mae", 0)
            row += f" & {mae:.3f}"
            mae_sum += mae

        avg_mae = mae_sum / len(datasets)
        row += f" & {avg_mae:.3f}"
        print(row)

    print("=" * 100)


if __name__ == "__main__":
    # 获取配置
    config = get_config("main_experiment")

    # 设置为CPU（如果有GPU可以改为"cuda"）
    config.model.device = "cpu"

    # 论文第4节声明的完整训练轮数：Stage1=100, Stage2=200
    # （config.py 已设置，此处不再覆盖）
    # Optuna 超参搜索使用缩减轮数，搜索结果保存至 best_hyperparams.json

    print("开始运行主实验...")
    print(f"设备: {config.model.device}")
    print(f"阶段一轮数: {config.model.num_epochs_stage1}")
    print(f"阶段二轮数: {config.model.num_epochs_stage2}")
    print(f"损失权重: λ₁={config.model.lambda_data}, λ₂={config.model.lambda_phys}, λ₃={config.model.lambda_pcc}")

    # 加载 Optuna 超参搜索结果（若存在），用于 GP 基线和 DL-LNN
    best_params_path = os.path.join(_EXPERIMENTS_DIR, "results", "best_hyperparams.json")
    if os.path.exists(best_params_path):
        with open(best_params_path, "r", encoding="utf-8") as f:
            best_params = json.load(f)
        print(f"\n[Optuna] 加载超参搜索结果: {best_params_path}")
        # 应用 GP 最佳超参（解决 GP 发散问题）
        # 修复说明：原先通过 monkey-patch experiments.models.BaselineGP 注入超参，
        # 但 trainer.py 使用 `from models import create_model`（扁平导入），
        # Python 将 models 与 experiments.models 视为两个不同的模块对象，
        # 导致 patch 不生效。现改为将超参挂载到 config.gp_best_params，
        # 由 create_model() 在构造 BaselineGP 时读取（models.py 已修改）。
        if "GP" in best_params:
            config.gp_best_params = best_params["GP"]
            print(f"  GP 最佳超参已挂载到 config.gp_best_params: {config.gp_best_params}")
        # 应用 DL-LNN 最佳超参
        if "DL-LNN" in best_params:
            dlnn_p = best_params["DL-LNN"]
            print(f"  DL-LNN 最佳超参: {dlnn_p}")
            if "learning_rate" in dlnn_p:
                config.model.learning_rate = dlnn_p["learning_rate"]
            if "weight_decay" in dlnn_p:
                config.model.weight_decay = dlnn_p["weight_decay"]
            if "dropout" in dlnn_p:
                config.model.dropout = dlnn_p["dropout"]
    else:
        print(f"\n[Optuna] 未找到超参搜索结果 ({best_params_path})，使用默认超参")
        print("  建议先运行: python experiments/optuna_search.py")

    # 运行所有实验
    start_time = time.time()
    all_results = run_all_experiments(config)
    end_time = time.time()

    print(f"\n总耗时: {(end_time - start_time) / 60:.2f} 分钟")

    # 打印汇总结果
    print_final_summary(all_results)

    # 生成论文表格
    generate_paper_tables(all_results)

    print("\n实验全部完成！")
