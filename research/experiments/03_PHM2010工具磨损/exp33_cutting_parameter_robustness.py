"""
实验三十三：不同切削参数组合鲁棒性实验
测试模型在极端和边界切削参数组合下的预测性能
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


class ExtremeCuttingDataset(Dataset):
    """
    极端切削参数数据集
    测试模型在边界条件下的性能
    """
    
    def __init__(
        self,
        num_samples: int = 2000,
        condition: str = "normal",
        seed: int = 42
    ):
        """
        Args:
            num_samples: 样本数
            condition: 工况条件
                - "normal": 正常工况
                - "high_speed": 高速切削
                - "low_speed": 低速切削
                - "high_depth": 大切深
                - "low_depth": 小切深
                - "combined_extreme": 组合极端
            seed: 随机种子
        """
        super().__init__()
        self.num_samples = num_samples
        self.condition = condition
        
        np.random.seed(seed)
        self.data = self._generate_data()
    
    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成数据"""
        # 根据工况设置参数范围
        param_ranges = {
            "normal": ((3000, 9000), (0.5, 8.0)),
            "high_speed": ((8000, 12000), (0.5, 8.0)),
            "low_speed": ((500, 3000), (0.5, 8.0)),
            "high_depth": ((3000, 9000), (6.0, 15.0)),
            "low_depth": ((3000, 9000), (0.05, 1.0)),
            "combined_extreme": ((8000, 12000), (6.0, 15.0))
        }
        
        speed_range, depth_range = param_ranges.get(self.condition, ((3000, 9000), (0.5, 8.0)))
        
        spindle_speed = np.random.uniform(speed_range[0], speed_range[1], self.num_samples)
        axial_depth = np.random.uniform(depth_range[0], depth_range[1], self.num_samples)
        
        # 使用Tlusty模型
        tlusty_model = TlustyAnalyticalModel()
        a_lim_clean = tlusty_model.compute_limiting_depth(spindle_speed)
        
        # 添加噪声
        noise_level = 0.05
        a_lim = a_lim_clean * (1 + np.random.randn(self.num_samples) * noise_level)
        a_lim = np.maximum(a_lim, 0.01)
        
        features = np.column_stack([
            spindle_speed / 10000,
            axial_depth / 10
        ]).astype(np.float32)
        
        return {
            'features': features,
            'a_lim': a_lim.astype(np.float32),
            'a_lim_clean': a_lim_clean.astype(np.float32),
            'spindle_speed': spindle_speed.astype(np.float32),
            'axial_depth': axial_depth.astype(np.float32)
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
    train_dataset: ExtremeCuttingDataset,
    test_dataset: ExtremeCuttingDataset,
    num_epochs: int = 80,
    device: str = "cpu"
) -> Dict[str, float]:
    """训练并评估模型"""
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)
    
    # 初始化模型
    if model_name == "DL-LNN":
        model = DLLNNModel(input_dim=7, hidden_dim=64)
    elif model_name == "LSTM":
        model = torch.nn.LSTM(input_size=7, hidden_size=64, num_layers=2, batch_first=True)
    else:  # GRU
        model = torch.nn.GRU(input_size=7, hidden_size=64, num_layers=2, batch_first=True)
    
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
    print("实验三十三：不同切削参数组合鲁棒性实验")
    print("=" * 60)
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")
    
    # 定义工况条件
    conditions = {
        "normal": "正常工况",
        "high_speed": "高速切削",
        "low_speed": "低速切削",
        "high_depth": "大切深",
        "low_depth": "小切深",
        "combined_extreme": "组合极端"
    }
    
    models = ["DL-LNN", "LSTM", "GRU"]
    
    all_results = {}
    
    # 实验1：用正常工况训练，测试各种极端工况
    print("\n实验1：正常工况训练，极端工况测试")
    print("-" * 40)
    
    train_dataset = ExtremeCuttingDataset(num_samples=5000, condition="normal", seed=42)
    
    condition_results = {}
    for test_condition, condition_name in conditions.items():
        print(f"  测试工况: {condition_name}")
        
        test_dataset = ExtremeCuttingDataset(num_samples=2000, condition=test_condition, seed=43)
        
        condition_results[test_condition] = {}
        for model_name in models:
            results = train_and_evaluate(
                model_name, train_dataset, test_dataset,
                num_epochs=80, device=device
            )
            condition_results[test_condition][model_name] = results
            print(f"    {model_name}: MAE={results['mae']:.4f}, PCC={results['pcc']:.4f}")
    
    all_results["normal_to_extreme"] = condition_results
    
    # 实验2：各工况独立训练测试
    print("\n实验2：各工况独立训练测试")
    print("-" * 40)
    
    independent_results = {}
    for condition, condition_name in conditions.items():
        print(f"  工况: {condition_name}")
        
        dataset = ExtremeCuttingDataset(num_samples=3000, condition=condition, seed=42)
        
        # 划分训练集和测试集
        train_size = int(0.8 * len(dataset))
        test_size = len(dataset) - train_size
        train_subset, test_subset = torch.utils.data.random_split(
            dataset, [train_size, test_size]
        )
        
        independent_results[condition] = {}
        for model_name in models:
            train_ds = torch.utils.data.Subset(dataset, list(range(train_size)))
            test_ds = torch.utils.data.Subset(dataset, list(range(train_size, len(dataset))))
            
            results = train_and_evaluate(
                model_name, train_ds, test_ds,
                num_epochs=80, device=device
            )
            independent_results[condition][model_name] = results
            print(f"    {model_name}: MAE={results['mae']:.4f}, PCC={results['pcc']:.4f}")
    
    all_results["independent_training"] = independent_results
    
    # 实验3：参数边界敏感性分析
    print("\n实验3：参数边界敏感性分析")
    print("-" * 40)
    
    sensitivity_results = {}
    train_dataset = ExtremeCuttingDataset(num_samples=5000, condition="normal", seed=42)
    
    # 测试不同速度边界
    speed_boundaries = [
        (1000, 5000, "低速边界"),
        (5000, 8000, "中速边界"),
        (8000, 12000, "高速边界")
    ]
    
    for low, high, name in speed_boundaries:
        test_dataset = ExtremeCuttingDataset(num_samples=1000, condition="normal", seed=44)
        # 修改速度范围
        test_dataset.data['spindle_speed'] = np.random.uniform(low, high, len(test_dataset))
        test_dataset.data['features'][:, 0] = test_dataset.data['spindle_speed'] / 10000
        
        sensitivity_results[name] = {}
        for model_name in models:
            results = train_and_evaluate(
                model_name, train_dataset, test_dataset,
                num_epochs=80, device=device
            )
            sensitivity_results[name][model_name] = results
    
    all_results["boundary_sensitivity"] = sensitivity_results
    
    # 保存结果
    output_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, 'cutting_parameter_robustness_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "不同切削参数组合鲁棒性实验",
            "conditions": conditions,
            "models": models,
            "results": all_results
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存至: {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
