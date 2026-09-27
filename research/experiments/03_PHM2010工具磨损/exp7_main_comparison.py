"""
主对比实验：5个数据集 × 10个模型
生成论文表2所需的完整数据
自包含训练循环，避免训练器接口不匹配问题
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

from config import ModelConfig, ExperimentConfig
from models import (
    DLLNNModel, DLLNNWithPhysics,
    BaselineLSTM, BaselineTransformer, BaselinePINN, BaselineBPNN,
    BaselineCNN, BaselineGRU, BaselinegPINN, BaselinePeRCNN
)
from data_generator import (
    PHM2010Dataset, NUAADataset, NISTDataset,
    Benchmark1Dataset, Industrial6061T6Dataset,
    create_dataloaders
)
from metrics import ChatterMetrics
from losses import PCC_Loss


def create_model_by_name(name: str, config: ModelConfig, device: torch.device) -> torch.nn.Module:
    """根据名称创建模型"""
    kwargs = dict(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        output_dim=config.output_dim,
    )

    if name == 'DL-LNN':
        model = DLLNNWithPhysics(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
            dt=config.ltc_dt,
            dropout=config.dropout
        )
    elif name == 'LSTM':
        model = BaselineLSTM(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim
        )
    elif name == 'GRU':
        model = BaselineGRU(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim
        )
    elif name == 'Transformer':
        model = BaselineTransformer(
            input_dim=config.input_dim,
            d_model=config.hidden_dim,
            nhead=4,
            num_layers=config.num_layers,
            output_dim=config.output_dim
        )
    elif name == 'CNN':
        model = BaselineCNN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            output_dim=config.output_dim
        )
    elif name == 'PINN':
        model = BaselinePINN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim
        )
    elif name == 'gPINN':
        model = BaselinegPINN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim
        )
    elif name == 'PeRCNN':
        model = BaselinePeRCNN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            output_dim=config.output_dim
        )
    elif name == 'BPNN':
        model = BaselineBPNN(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim
        )
    else:
        raise ValueError(f"Unknown model: {name}")

    return model.to(device)


def train_model(
    model: torch.nn.Module,
    model_name: str,
    train_loader,
    val_loader,
    config: ModelConfig,
    device: torch.device,
    num_epochs: int = 80
) -> torch.nn.Module:
    """训练模型，返回最佳模型"""

    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-5)

    best_val_loss = float('inf')
    best_state = None

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

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        if (epoch + 1) % 20 == 0 or epoch == 0:
            print(f"    Epoch [{epoch+1}/{num_epochs}] Train: {train_loss:.4f} Val: {val_loss:.4f}")

    if best_state is not None:
        model.load_state_dict(best_state)

    return model


def evaluate_model(
    model: torch.nn.Module,
    test_loader,
    device: torch.device
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
        'MAE': metrics_calc.mae(all_preds, all_targets),
        'RMSE': metrics_calc.rmse(all_preds, all_targets),
        'R2': metrics_calc.r2_score(all_preds, all_targets),
        'MAPE': metrics_calc.mape(all_preds, all_targets),
        'PCC': metrics_calc.physics_consistency_coefficient(all_preds, all_phys)
    }

    return metrics


def get_all_datasets() -> Dict[str, object]:
    """获取所有5个数据集

    学术诚信说明：
        - PHM2010: 加载真实 PHM2010 公开数据集（通过 UniwearDataLoader）
        - NUAA/NIST/Benchmark-1: 合成数据，基于 TlustyAnalyticalModel 生成
        - 自采6061-T6: 合成数据占位实现，**不可在论文中声称对应真实自采数据**
          （详见 Industrial6061T6Dataset 的 docstring）
    """
    return {
        'PHM2010': PHM2010Dataset(num_samples=2000, noise_level=0.05, seed=42),
        'NUAA': NUAADataset(num_samples=1800, noise_level=0.04, seed=43),
        'NIST': NISTDataset(num_samples=1500, noise_level=0.06, seed=44),
        'Benchmark-1': Benchmark1Dataset(num_samples=2200, noise_level=0.045, seed=45),
        '自采6061-T6': Industrial6061T6Dataset(num_samples=500, noise_level=0.08, seed=46),
    }


def get_dataset_data_source(dataset_name: str) -> str:
    """获取数据集的 data_source 标签（用于结果追溯）。

    Args:
        dataset_name: 数据集名称（与 get_all_datasets() 的 key 一致）

    Returns:
        数据来源标签：
        - 'real_PHM2010'           : 真实 PHM2010 公开数据集
        - 'synthetic_Tlusty'       : 基于 TlustyAnalyticalModel 的合成数据
        - 'synthetic_6061T6_placeholder': 合成数据占位（不可声称真实自采）
    """
    mapping = {
        'PHM2010': 'real_PHM2010',
        'NUAA': 'synthetic_Tlusty',
        'NIST': 'synthetic_Tlusty',
        'Benchmark-1': 'synthetic_Tlusty',
        '自采6061-T6': 'synthetic_6061T6_placeholder',
    }
    return mapping.get(dataset_name, 'unknown')


MODEL_NAMES = ['DL-LNN', 'LSTM', 'GRU', 'Transformer', 'CNN', 'PINN', 'gPINN', 'PeRCNN', 'BPNN']


def run_main_comparison_experiment():
    """运行主对比实验"""

    print("=" * 80)
    print("主对比实验：5个数据集 × 9个模型")
    print("=" * 80)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"\n使用设备: {device}")

    config = ModelConfig()

    # 加载数据集
    print("\n[1/2] 加载数据集...")
    all_datasets = get_all_datasets()
    for name, ds in all_datasets.items():
        print(f"  - {name}: {len(ds)} 样本")

    # 运行实验
    print("\n[2/2] 运行实验...")
    results = {}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 学术诚信：记录每个数据集的 data_source 标签
    dataset_data_sources = {}

    for ds_idx, (dataset_name, dataset) in enumerate(all_datasets.items(), 1):
        print(f"\n{'='*80}")
        print(f"[{ds_idx}/5] 数据集: {dataset_name}")
        print(f"{'='*80}")

        # 记录数据来源标签（用于结果追溯）
        data_source = get_dataset_data_source(dataset_name)
        dataset_data_sources[dataset_name] = data_source
        print(f"  data_source: {data_source}")

        # 创建数据加载器
        train_loader, val_loader, test_loader = create_dataloaders(
            dataset_class=type(dataset),
            dataset_params={
                'num_samples': len(dataset),
                'noise_level': dataset.noise_level,
                'seed': 42
            },
            batch_size=config.batch_size,
            train_ratio=0.7,
            val_ratio=0.15
        )

        results[dataset_name] = {}

        for model_name in MODEL_NAMES:
            print(f"\n  [{model_name}]")

            try:
                # 创建新模型
                model = create_model_by_name(model_name, config, device)

                # 训练
                model = train_model(
                    model=model,
                    model_name=model_name,
                    train_loader=train_loader,
                    val_loader=val_loader,
                    config=config,
                    device=device,
                    num_epochs=80
                )

                # 评估
                test_metrics = evaluate_model(model, test_loader, device)

                results[dataset_name][model_name] = test_metrics

                print(f"    MAE: {test_metrics['MAE']:.4f}, RMSE: {test_metrics['RMSE']:.4f}, "
                      f"R2: {test_metrics['R2']:.4f}, PCC: {test_metrics['PCC']:.4f}")

            except Exception as e:
                print(f"    错误: {str(e)}")
                import traceback
                traceback.print_exc()
                results[dataset_name][model_name] = {
                    'MAE': float('nan'),
                    'RMSE': float('nan'),
                    'R2': float('nan'),
                    'MAPE': float('nan'),
                    'PCC': float('nan')
                }

    # 保存结果（含 data_source 元数据，用于学术诚信追溯）
    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)

    # 采用扁平结构 + _metadata key，保持向后兼容性：
    # - 原有脚本访问 results['PHM2010'] 仍然有效
    # - 新脚本可访问 results['_metadata'] 获取数据来源信息
    # - 遍历数据集时需跳过 '_metadata' key
    output_payload = dict(results)  # 复制原有扁平结构
    output_payload['_metadata'] = {
        'description': '主对比实验结果：5 个数据集 × 9 个模型',
        'generated_at': timestamp,
        'data_sources': dataset_data_sources,
        'data_source_legend': {
            'real_PHM2010': '真实 PHM2010 公开数据集（通过 UniwearDataLoader 加载）',
            'synthetic_Tlusty': '基于 TlustyAnalyticalModel 生成的合成数据',
            'synthetic_6061T6_placeholder': '合成数据占位实现（不可声称真实自采数据）',
        },
        'academic_integrity_note': (
            '本结果文件中 PHM2010 数据集的指标基于真实公开数据，'
            '其余数据集（NUAA/NIST/Benchmark-1/自采6061-T6）为合成数据。'
            '在论文中引用本文件的指标时，必须根据 data_sources 字段如实标注数据来源。'
        ),
    }

    output_file = output_dir / "main_comparison_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*80}")
    print(f"实验完成！结果已保存到: {output_file}")
    print(f"{'='*80}")

    # 打印汇总表格
    print("\n汇总表格 (MAE):")
    print("-" * 130)
    header = f"{'Dataset':<15}" + "".join([f"{name:<13}" for name in MODEL_NAMES])
    print(header)
    print("-" * 130)

    for dataset_name, dataset_results in results.items():
        row = f"{dataset_name:<15}"
        for model_name in MODEL_NAMES:
            if model_name in dataset_results:
                mae = dataset_results[model_name]['MAE']
                if np.isnan(mae):
                    row += f"{'N/A':<13}"
                else:
                    row += f"{mae:<13.4f}"
            else:
                row += f"{'N/A':<13}"
        print(row)

    print("-" * 130)

    # R2 汇总
    print("\n汇总表格 (R2):")
    print("-" * 130)
    print(header)
    print("-" * 130)

    for dataset_name, dataset_results in results.items():
        row = f"{dataset_name:<15}"
        for model_name in MODEL_NAMES:
            if model_name in dataset_results:
                r2 = dataset_results[model_name]['R2']
                if np.isnan(r2):
                    row += f"{'N/A':<13}"
                else:
                    row += f"{r2:<13.4f}"
            else:
                row += f"{'N/A':<13}"
        print(row)

    print("-" * 130)

    return results


if __name__ == "__main__":
    set_global_seed(42)
    with start_run(experiment_name="exp7_main_comparison"):
        results = run_main_comparison_experiment()
