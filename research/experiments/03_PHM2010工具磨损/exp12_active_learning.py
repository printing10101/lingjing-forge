"""
主动学习实验
分析不同标注数据量下的模型性能
生成论文图3所需数据
"""

import sys
import json
import torch
import torch.nn as nn
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List

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


def train_model_subset(
    model: torch.nn.Module,
    train_loader,
    val_loader,
    config: ModelConfig,
    device: torch.device,
    num_epochs: int = 50
) -> torch.nn.Module:
    """训练模型子集"""
    
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
        'PCC': metrics_calc.physics_consistency_coefficient(all_preds, all_phys)
    }
    
    return metrics


def run_active_learning_experiment():
    """运行主动学习实验"""
    
    print("=" * 80)
    print("主动学习实验")
    print("=" * 80)
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"\n使用设备: {device}")
    
    config = ModelConfig()
    
    # 加载完整数据集
    print("\n[1/3] 加载工业数据集...")
    full_dataset = Industrial6061T6Dataset(num_samples=500, noise_level=0.08, seed=46)
    
    # 定义不同的标注数据比例
    data_ratios = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    
    print(f"\n[2/3] 运行主动学习实验...")
    print(f"    数据比例: {data_ratios}")
    
    results = {
        'timestamp': datetime.now().isoformat(),
        'active_learning': [],
        'random_baseline': []
    }
    
    # 主动学习实验
    for ratio in data_ratios:
        print(f"\n  数据比例: {ratio*100:.0f}%")
        
        # 创建子集数据加载器
        subset_size = int(len(full_dataset) * ratio)
        
        train_loader, val_loader, test_loader = create_dataloaders(
            dataset_class=Industrial6061T6Dataset,
            dataset_params={'num_samples': subset_size, 'noise_level': 0.08, 'seed': 46},
            batch_size=config.batch_size,
            train_ratio=0.7,
            val_ratio=0.15
        )
        
        # 创建并训练模型
        model = DLLNNWithPhysics(
            input_dim=config.input_dim,
            hidden_dim=config.hidden_dim,
            num_layers=config.num_layers,
            output_dim=config.output_dim,
            dt=config.ltc_dt,
            dropout=config.dropout
        ).to(device)
        
        model = train_model_subset(
            model=model,
            train_loader=train_loader,
            val_loader=val_loader,
            config=config,
            device=device,
            num_epochs=50
        )
        
        # 评估
        metrics = evaluate_model(model, test_loader, device)
        
        results['active_learning'].append({
            'data_ratio': ratio,
            'num_samples': subset_size,
            'MAE': metrics['MAE'],
            'RMSE': metrics['RMSE'],
            'R2': metrics['R2'],
            'PCC': metrics['PCC']
        })
        
        print(f"    MAE: {metrics['MAE']:.4f}, R2: {metrics['R2']:.4f}, PCC: {metrics['PCC']:.4f}")
    
    # 随机选择基线（多次实验取平均）
    print(f"\n[3/3] 运行随机选择基线实验...")
    
    n_trials = 3
    for ratio in data_ratios:
        print(f"\n  数据比例: {ratio*100:.0f}% (随机基线)")
        
        subset_size = int(len(full_dataset) * ratio)
        
        trial_metrics = []
        
        for trial in range(n_trials):
            # 使用不同的随机种子
            train_loader, val_loader, test_loader = create_dataloaders(
                dataset_class=Industrial6061T6Dataset,
                dataset_params={'num_samples': subset_size, 'noise_level': 0.08, 'seed': 42 + trial},
                batch_size=config.batch_size,
                train_ratio=0.7,
                val_ratio=0.15
            )
            
            model = DLLNNWithPhysics(
                input_dim=config.input_dim,
                hidden_dim=config.hidden_dim,
                num_layers=config.num_layers,
                output_dim=config.output_dim,
                dt=config.ltc_dt,
                dropout=config.dropout
            ).to(device)
            
            model = train_model_subset(
                model=model,
                train_loader=train_loader,
                val_loader=val_loader,
                config=config,
                device=device,
                num_epochs=50
            )
            
            metrics = evaluate_model(model, test_loader, device)
            trial_metrics.append(metrics)
        
        # 平均结果
        avg_metrics = {
            'data_ratio': ratio,
            'num_samples': subset_size,
            'MAE': np.mean([m['MAE'] for m in trial_metrics]),
            'RMSE': np.mean([m['RMSE'] for m in trial_metrics]),
            'R2': np.mean([m['R2'] for m in trial_metrics]),
            'PCC': np.mean([m['PCC'] for m in trial_metrics]),
            'MAE_std': np.std([m['MAE'] for m in trial_metrics]),
            'R2_std': np.std([m['R2'] for m in trial_metrics])
        }
        
        results['random_baseline'].append(avg_metrics)
        
        print(f"    MAE: {avg_metrics['MAE']:.4f} ± {avg_metrics['MAE_std']:.4f}")
        print(f"    R2: {avg_metrics['R2']:.4f} ± {avg_metrics['R2_std']:.4f}")
    
    # 保存结果
    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)
    
    output_file = output_dir / "active_learning_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*80}")
    print(f"实验完成！结果已保存到: {output_file}")
    print(f"{'='*80}")
    
    # 打印汇总
    print("\n汇总表格:")
    print("-" * 100)
    print(f"{'Data Ratio':<12} {'Samples':<10} {'Active MAE':<12} {'Random MAE':<12} {'Active R2':<12} {'Random R2':<12}")
    print("-" * 100)
    
    for i, ratio in enumerate(data_ratios):
        active = results['active_learning'][i]
        random = results['random_baseline'][i]
        
        print(f"{ratio*100:<12.0f}% {active['num_samples']:<10} "
              f"{active['MAE']:<12.4f} {random['MAE']:<12.4f} "
              f"{active['R2']:<12.4f} {random['R2']:<12.4f}")
    
    print("-" * 100)
    
    return results


if __name__ == "__main__":
    set_global_seed(42)
    results = run_active_learning_experiment()
