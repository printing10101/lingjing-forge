"""
实验三十：刀具磨损全生命周期实验
评估模型在刀具从新刀到报废整个生命周期中的预测稳定性
"""

import torch
import numpy as np
import json
import os
import time
from typing import Dict, List
from torch.utils.data import Dataset, DataLoader

from models import DLLNNModel
from metrics import ChatterMetrics
from data_generator import TlustyAnalyticalModel


class ToolWearDataset(Dataset):
    """
    刀具磨损数据集
    模拟刀具从新刀到严重磨损的不同阶段
    """
    
    def __init__(
        self,
        num_samples: int = 4000,
        wear_stage: str = "new",
        seed: int = 42
    ):
        """
        Args:
            num_samples: 样本数
            wear_stage: 磨损阶段 ("new", "initial", "normal", "severe", "worn_out")
            seed: 随机种子
        """
        super().__init__()
        self.num_samples = num_samples
        self.wear_stage = wear_stage
        
        np.random.seed(seed)
        self.data = self._generate_data()
    
    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成刀具磨损数据"""
        # 基础切削参数
        spindle_speed = np.random.uniform(3000, 9000, self.num_samples)
        axial_depth = np.random.uniform(0.5, 8.0, self.num_samples)
        
        # 根据磨损阶段调整物理参数
        wear_factors = {
            "new": 1.0,
            "initial": 0.95,
            "normal": 0.85,
            "severe": 0.70,
            "worn_out": 0.55
        }
        
        wear_factor = wear_factors.get(self.wear_stage, 1.0)
        
        # 使用Tlusty模型生成基础标签
        base_stiffness = 1.2e6
        tlusty_model = TlustyAnalyticalModel(
            stiffness=base_stiffness * wear_factor,
            modal_mass=120.0,
            damping_ratio=0.06
        )
        
        a_lim_clean = tlusty_model.compute_limiting_depth(spindle_speed)
        
        # 添加噪声（磨损阶段噪声更大）
        noise_level = 0.05 + (1.0 - wear_factor) * 0.1
        a_lim = a_lim_clean * (1 + np.random.randn(self.num_samples) * noise_level)
        a_lim = np.maximum(a_lim, 0.01)
        
        # 生成特征（包含磨损相关信息）
        wear_indicator = np.full(self.num_samples, 1.0 - wear_factor, dtype=np.float32)
        features = np.column_stack([
            spindle_speed / 10000,
            axial_depth / 10,
            wear_indicator,
            np.random.randn(self.num_samples) * 0.05
        ]).astype(np.float32)
        
        return {
            'features': features,
            'a_lim': a_lim.astype(np.float32),
            'a_lim_clean': a_lim_clean.astype(np.float32),
            'wear_factor': np.full(self.num_samples, wear_factor, dtype=np.float32)
        }
    
    def __len__(self) -> int:
        return self.num_samples
    
    def __getitem__(self, idx: int):
        features = torch.from_numpy(self.data['features'][idx])
        a_lim = torch.from_numpy(np.array([self.data['a_lim'][idx]]))
        a_lim_physics = torch.from_numpy(np.array([self.data['a_lim_clean'][idx]]))
        
        return features, a_lim, a_lim_physics


def train_and_evaluate(
    model_name: str,
    train_datasets: List[ToolWearDataset],
    test_dataset: ToolWearDataset,
    num_epochs: int = 60,
    device: str = "cpu"
) -> Dict[str, float]:
    """训练并评估模型"""
    # 合并训练数据
    train_indices = []
    for ds in train_datasets:
        train_indices.extend(range(len(ds)))
    
    combined_train = torch.utils.data.ConcatDataset(train_datasets)
    train_loader = DataLoader(combined_train, batch_size=32, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)
    
    # 初始化模型
    if model_name == "DL-LNN":
        model = DLLNNModel(input_dim=4, hidden_dim=64)
    elif model_name == "LSTM":
        model = torch.nn.LSTM(input_size=4, hidden_size=64, num_layers=2, batch_first=True)
    else:  # GRU
        model = torch.nn.GRU(input_size=4, hidden_size=64, num_layers=2, batch_first=True)
    
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)
    criterion = torch.nn.MSELoss()
    
    # 训练
    model.train()
    for epoch in range(num_epochs):
        epoch_loss = 0.0
        for features, labels, _ in train_loader:
            features = features.to(device)
            labels = labels.to(device)
            
            optimizer.zero_grad()
            outputs = model(features)
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            
            # 处理LSTM/GRU输出
            if model_name in ["LSTM", "GRU"]:
                if outputs.dim() == 3:
                    outputs = outputs[:, -1, :]
                if outputs.shape[-1] != 1:
                    outputs = outputs.mean(dim=-1, keepdim=True)
            
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item()
    
    # 评估
    model.eval()
    all_preds = []
    all_labels = []
    
    with torch.no_grad():
        for features, labels, _ in test_loader:
            features = features.to(device)
            outputs = model(features)
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            
            if model_name in ["LSTM", "GRU"]:
                if outputs.dim() == 3:
                    outputs = outputs[:, -1, :]
                if outputs.shape[-1] != 1:
                    outputs = outputs.mean(dim=-1, keepdim=True)
            
            all_preds.append(outputs.cpu().numpy())
            all_labels.append(labels.numpy())
    
    all_preds = np.concatenate(all_preds)
    all_labels = np.concatenate(all_labels)
    
    metrics = ChatterMetrics()
    results = metrics.compute_all(all_labels, all_preds)
    
    return {
        "mae": round(float(results['mae']), 4),
        "rmse": round(float(results['rmse']), 4),
        "r2": round(float(results.get('r2', 0)), 4),
        "pcc": round(float(results.get('pcc', 0)), 4)
    }


def main():
    print("=" * 60)
    print("实验三十：刀具磨损全生命周期实验")
    print("=" * 60)
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")
    
    # 定义刀具磨损阶段
    wear_stages = ["new", "initial", "normal", "severe", "worn_out"]
    stage_names = {
        "new": "新刀",
        "initial": "初期磨损",
        "normal": "正常磨损",
        "severe": "严重磨损",
        "worn_out": "报废"
    }
    
    models = ["DL-LNN", "LSTM", "GRU"]
    
    all_results = {}
    
    # 实验1：每个磨损阶段独立测试
    print("\n实验1：各磨损阶段独立性能")
    print("-" * 40)
    
    stage_results = {}
    for test_stage in wear_stages:
        print(f"  测试阶段: {stage_names[test_stage]}")
        
        test_dataset = ToolWearDataset(num_samples=800, wear_stage=test_stage, seed=42)
        
        stage_results[test_stage] = {}
        for model_name in models:
            # 使用所有阶段数据训练
            train_datasets = [
                ToolWearDataset(num_samples=800, wear_stage=stage, seed=42)
                for stage in wear_stages
            ]
            
            results = train_and_evaluate(
                model_name, train_datasets, test_dataset,
                num_epochs=60, device=device
            )
            stage_results[test_stage][model_name] = results
            print(f"    {model_name}: MAE={results['mae']:.4f}, PCC={results['pcc']:.4f}")
    
    all_results["stage_independent"] = stage_results
    
    # 实验2：跨磨损阶段泛化
    print("\n实验2：跨磨损阶段泛化能力")
    print("-" * 40)
    
    cross_stage_results = {}
    for train_stage in wear_stages:
        print(f"  训练阶段: {stage_names[train_stage]}")
        
        train_dataset = ToolWearDataset(num_samples=2000, wear_stage=train_stage, seed=42)
        
        cross_stage_results[train_stage] = {}
        for test_stage in wear_stages:
            test_dataset = ToolWearDataset(num_samples=800, wear_stage=test_stage, seed=43)
            
            cross_stage_results[train_stage][test_stage] = {}
            for model_name in models:
                results = train_and_evaluate(
                    model_name, [train_dataset], test_dataset,
                    num_epochs=60, device=device
                )
                cross_stage_results[train_stage][test_stage][model_name] = results
    
    all_results["cross_stage_generalization"] = cross_stage_results
    
    # 实验3：渐进磨损性能退化
    print("\n实验3：渐进磨损性能退化分析")
    print("-" * 40)
    
    degradation_results = {}
    for model_name in models:
        print(f"  模型: {model_name}")
        
        # 用新刀数据训练
        train_dataset = ToolWearDataset(num_samples=2000, wear_stage="new", seed=42)
        
        degradation_results[model_name] = []
        for test_stage in wear_stages:
            test_dataset = ToolWearDataset(num_samples=800, wear_stage=test_stage, seed=42)
            results = train_and_evaluate(
                model_name, [train_dataset], test_dataset,
                num_epochs=60, device=device
            )
            degradation_results[model_name].append({
                "stage": test_stage,
                "stage_name": stage_names[test_stage],
                "mae": results['mae'],
                "rmse": results['rmse'],
                "r2": results['r2'],
                "pcc": results['pcc']
            })
            print(f"    {stage_names[test_stage]}: MAE={results['mae']:.4f}, PCC={results['pcc']:.4f}")
    
    all_results["degradation_analysis"] = degradation_results
    
    # 保存结果
    output_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, 'tool_wear_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "刀具磨损全生命周期实验",
            "wear_stages": wear_stages,
            "stage_names": stage_names,
            "models": models,
            "results": all_results
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存至: {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
