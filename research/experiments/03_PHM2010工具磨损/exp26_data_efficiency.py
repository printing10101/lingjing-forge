"""
实验二十六：数据量效率实验（Learning Curve）
测试不同训练样本比例（10%/20%/50%/80%/100%）下的性能，验证DL-LNN在小样本场景下的优势
"""

import torch
import numpy as np
import json
import os
import time
from typing import Dict, List, Tuple
from torch.utils.data import Dataset, DataLoader, Subset

from models import DLLNNModel
from data_generator import PHM2010Dataset, Industrial6061T6Dataset, create_dataloaders
from metrics import ChatterMetrics


def train_model(
    model: torch.nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    num_epochs: int = 50,
    device: str = "cpu"
) -> Tuple[List[float], List[float]]:
    """
    训练模型
    
    Args:
        model: 要训练的模型
        train_loader: 训练数据加载器
        val_loader: 验证数据加载器
        num_epochs: 训练轮数
        device: 设备
    
    Returns:
        训练损失列表, 验证损失列表
    """
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)
    criterion = torch.nn.MSELoss()
    
    train_losses = []
    val_losses = []
    
    for epoch in range(num_epochs):
        # 训练阶段
        model.train()
        train_loss = 0.0
        for features, labels, _ in train_loader:
            features = features.to(device)
            labels = labels.to(device)
            
            optimizer.zero_grad()
            outputs = model(features)
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            train_loss += loss.item()
        
        train_loss /= len(train_loader)
        train_losses.append(train_loss)
        
        # 验证阶段
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for features, labels, _ in val_loader:
                features = features.to(device)
                labels = labels.to(device)
                
                outputs = model(features)
                if isinstance(outputs, tuple):
                    outputs = outputs[0]
                
                loss = criterion(outputs, labels)
                val_loss += loss.item()
        
        val_loss /= len(val_loader)
        val_losses.append(val_loss)
    
    return train_losses, val_losses


def evaluate_model(
    model: torch.nn.Module,
    test_loader: DataLoader,
    device: str = "cpu"
) -> Dict[str, float]:
    """
    评估模型
    
    Args:
        model: 要评估的模型
        test_loader: 测试数据加载器
        device: 设备
    
    Returns:
        评估指标字典
    """
    model = model.to(device)
    model.eval()
    
    all_preds = []
    all_labels = []
    
    with torch.no_grad():
        for features, labels, _ in test_loader:
            features = features.to(device)
            outputs = model(features)
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            
            # 处理 LSTM/GRU 的输出（需要投影到输出维度）
            if hasattr(model, '__class__') and model.__class__.__name__ in ['LSTM', 'GRU']:
                # 取最后一个时间步的隐藏状态
                if outputs.dim() == 3:  # [batch, seq, hidden]
                    outputs = outputs[:, -1, :]
                # 添加线性投影层（在模型初始化时应该添加）
                if outputs.shape[-1] != 1:
                    # 临时投影到输出维度
                    outputs = outputs.mean(dim=-1, keepdim=True)
            
            all_preds.append(outputs.cpu().numpy())
            all_labels.append(labels.numpy())
    
    all_preds = np.concatenate(all_preds, axis=0)
    all_labels = np.concatenate(all_labels, axis=0)
    
    # 计算指标
    metrics = ChatterMetrics()
    results = metrics.compute_all(all_preds, all_labels)
    
    return results


def run_data_efficiency_experiment(
    dataset_class,
    dataset_params: Dict,
    ratios: List[float],
    model_name: str,
    device: str = "cpu"
) -> Dict[str, Dict]:
    """
    运行数据量效率实验
    
    Args:
        dataset_class: 数据集类
        dataset_params: 数据集参数
        ratios: 训练数据比例列表
        model_name: 模型名称
        device: 设备
    
    Returns:
        实验结果字典
    """
    results = {}
    
    for ratio in ratios:
        print(f"  训练数据比例: {ratio*100:.0f}%")
        
        # 创建完整数据集
        full_dataset = dataset_class(**dataset_params)
        total_size = len(full_dataset)
        
        # 计算当前比例下的样本数
        current_size = int(total_size * ratio)
        
        # 随机选择样本
        torch.manual_seed(42)
        indices = torch.randperm(total_size)[:current_size].tolist()
        subset_dataset = Subset(full_dataset, indices)
        
        # 创建数据加载器
        train_size = int(len(subset_dataset) * 0.7)
        val_size = int(len(subset_dataset) * 0.15)
        test_size = len(subset_dataset) - train_size - val_size
        
        train_subset, val_subset, test_subset = torch.utils.data.random_split(
            subset_dataset, [train_size, val_size, test_size]
        )
        
        train_loader = DataLoader(train_subset, batch_size=32, shuffle=True)
        val_loader = DataLoader(val_subset, batch_size=32, shuffle=False)
        test_loader = DataLoader(test_subset, batch_size=32, shuffle=False)
        
        # 创建模型
        if model_name == "DL-LNN":
            model = DLLNNModel(
                input_dim=7,
                hidden_dim=64,
                num_layers=3,
                output_dim=1,
                dt=0.1,
                dropout=0.2
            )
        elif model_name == "LSTM":
            model = torch.nn.LSTM(
                input_size=7,
                hidden_size=64,
                num_layers=2,
                batch_first=True
            )
        elif model_name == "GRU":
            model = torch.nn.GRU(
                input_size=7,
                hidden_size=64,
                num_layers=2,
                batch_first=True
            )
        else:
            raise ValueError(f"Unknown model: {model_name}")
        
        # 训练模型
        train_losses, val_losses = train_model(
            model, train_loader, val_loader, num_epochs=50, device=device
        )
        
        # 评估模型
        eval_results = evaluate_model(model, test_loader, device)
        
        results[f"{ratio*100:.0f}%"] = {
            "num_samples": current_size,
            "train_loss": train_losses[-1],
            "val_loss": val_losses[-1],
            "MAE": eval_results['mae'],
            "RMSE": eval_results['rmse'],
            "R2": eval_results['r2'],
            "PCC": eval_results.get('pcc', 0.0)
        }
        
        print(f"    样本数: {current_size}, MAE: {eval_results['mae']:.4f}, R2: {eval_results['r2']:.4f}")
    
    return results


def main():
    """主函数"""
    print("=" * 60)
    print("实验二十六：数据量效率实验（Learning Curve）")
    print("=" * 60)
    
    # 配置
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")
    
    # 实验参数
    ratios = [0.1, 0.2, 0.5, 0.8, 1.0]
    models = ["DL-LNN", "LSTM", "GRU"]
    
    # 数据集配置
    dataset_configs = {
        "PHM2010": {
            "class": PHM2010Dataset,
            "params": {"num_samples": 2000, "noise_level": 0.05, "seed": 42}
        },
        "6061-T6": {
            "class": Industrial6061T6Dataset,
            "params": {"num_samples": 500, "noise_level": 0.08, "seed": 46}
        }
    }
    
    all_results = {}
    
    for dataset_name, config in dataset_configs.items():
        print(f"\n数据集: {dataset_name}")
        print("-" * 60)
        
        dataset_results = {}
        
        for model_name in models:
            print(f"\n模型: {model_name}")
            model_results = run_data_efficiency_experiment(
                config["class"],
                config["params"],
                ratios,
                model_name,
                device
            )
            dataset_results[model_name] = model_results
        
        all_results[dataset_name] = dataset_results
    
    # 保存结果
    output_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, 'data_efficiency_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "数据量效率实验",
            "ratios": ratios,
            "models": models,
            "results": all_results
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存至: {output_file}")
    print("=" * 60)
    
    return all_results


if __name__ == "__main__":
    main()
