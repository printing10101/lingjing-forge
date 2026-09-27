"""
时间常数分析实验
分析DL-LNN网络学习到的时间常数τ分布
生成论文表6所需数据
"""

import sys
import json
import torch
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
from training.experiment_tracker import start_run, is_enabled

from config import ModelConfig
from models import DLLNNWithPhysics
from data_generator import Industrial6061T6Dataset, create_dataloaders


def analyze_time_constants():
    """分析时间常数分布"""
    
    print("=" * 80)
    print("时间常数分析实验")
    print("=" * 80)
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"\n使用设备: {device}")
    
    config = ModelConfig()
    
    # 加载工业数据集
    print("\n[1/3] 加载工业数据集...")
    dataset = Industrial6061T6Dataset(num_samples=500, noise_level=0.08, seed=46)
    
    train_loader, val_loader, test_loader = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={'num_samples': 500, 'noise_level': 0.08, 'seed': 46},
        batch_size=config.batch_size,
        train_ratio=0.7,
        val_ratio=0.15
    )
    
    # 创建并训练模型
    print("\n[2/3] 训练DL-LNN模型...")
    model = DLLNNWithPhysics(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
        dt=config.ltc_dt,
        dropout=config.dropout
    ).to(device)
    
    # 简单训练
    import torch.nn as nn
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    
    best_loss = float('inf')
    best_state = None
    
    for epoch in range(50):
        model.train()
        train_loss = 0.0
        
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
        
        train_loss /= len(train_loader)
        
        # 验证
        model.eval()
        val_loss = 0.0
        
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
        
        val_loss /= len(val_loader)
        
        if val_loss < best_loss:
            best_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        
        if (epoch + 1) % 10 == 0:
            print(f"  Epoch [{epoch+1}/50] Train: {train_loss:.4f} Val: {val_loss:.4f}")
    
    # 加载最佳模型
    if best_state is not None:
        model.load_state_dict(best_state)
    
    # 分析时间常数
    print("\n[3/3] 分析时间常数分布...")
    
    results = {
        'timestamp': datetime.now().isoformat(),
        'layers': []
    }
    
    # 提取每层的tau值
    for layer_idx, ltc_cell in enumerate(model.ltc_branch.ltc_cells):
        tau_values = ltc_cell.tau.detach().cpu().numpy()
        
        layer_stats = {
            'layer': layer_idx + 1,
            'tau_mean': float(np.mean(tau_values)),
            'tau_std': float(np.std(tau_values)),
            'tau_min': float(np.min(tau_values)),
            'tau_max': float(np.max(tau_values)),
            'tau_median': float(np.median(tau_values)),
            'tau_values': tau_values.tolist()
        }
        
        results['layers'].append(layer_stats)
        
        print(f"\n  Layer {layer_idx + 1}:")
        print(f"    τ mean: {layer_stats['tau_mean']:.4f}")
        print(f"    τ std:  {layer_stats['tau_std']:.4f}")
        print(f"    τ range: [{layer_stats['tau_min']:.4f}, {layer_stats['tau_max']:.4f}]")
        print(f"    τ median: {layer_stats['tau_median']:.4f}")
    
    # 计算全局统计
    all_taus = []
    for layer in results['layers']:
        all_taus.extend(layer['tau_values'])
    
    results['global'] = {
        'tau_mean': float(np.mean(all_taus)),
        'tau_std': float(np.std(all_taus)),
        'tau_min': float(np.min(all_taus)),
        'tau_max': float(np.max(all_taus)),
        'tau_median': float(np.median(all_taus))
    }
    
    print(f"\n  全局统计:")
    print(f"    τ mean: {results['global']['tau_mean']:.4f}")
    print(f"    τ std:  {results['global']['tau_std']:.4f}")
    print(f"    τ range: [{results['global']['tau_min']:.4f}, {results['global']['tau_max']:.4f}]")
    
    # 分析时间常数的物理意义
    print("\n  时间常数物理意义分析:")
    print(f"    - 快速响应单元 (τ < 0.05): {sum(1 for t in all_taus if t < 0.05)} 个")
    print(f"    - 中速响应单元 (0.05 ≤ τ < 0.15): {sum(1 for t in all_taus if 0.05 <= t < 0.15)} 个")
    print(f"    - 慢速响应单元 (τ ≥ 0.15): {sum(1 for t in all_taus if t >= 0.15)} 个")
    
    # 保存结果
    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)
    
    output_file = output_dir / "time_constant_analysis.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*80}")
    print(f"实验完成！结果已保存到: {output_file}")
    print(f"{'='*80}")
    
    return results


if __name__ == "__main__":
    set_global_seed(42)
    with start_run(experiment_name="exp11_time_constant"):
        results = analyze_time_constants()
