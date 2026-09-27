"""
实验二十九：在线自适应/增量学习实验
模拟工况漂移场景，测试模型的在线微调能力和持续学习性能
"""

import torch
import numpy as np
import json
import os
import time
from typing import Dict, List, Tuple
from torch.utils.data import Dataset, DataLoader

from models import DLLNNModel
from metrics import ChatterMetrics
from data_generator import TlustyAnalyticalModel


class ConceptDriftDataset(Dataset):
    """
    工况漂移数据集
    模拟不同时间段的工况变化（刀具磨损、材料批次变化等）
    """
    
    def __init__(
        self,
        num_samples: int = 5000,
        drift_type: str = "gradual",
        seed: int = 42
    ):
        """
        Args:
            num_samples: 总样本数
            drift_type: 漂移类型 ("gradual", "sudden", "incremental")
            seed: 随机种子
        """
        super().__init__()
        self.num_samples = num_samples
        self.drift_type = drift_type
        
        np.random.seed(seed)
        self.data = self._generate_data()
    
    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成带工况漂移的数据"""
        # 基础切削参数
        spindle_speed = np.random.uniform(3000, 9000, self.num_samples)
        axial_depth = np.random.uniform(0.5, 8.0, self.num_samples)
        
        # 模拟时间序列（样本按时间顺序排列）
        time_indices = np.arange(self.num_samples)
        
        # 根据漂移类型生成不同的物理参数变化
        if self.drift_type == "gradual":
            # 渐进漂移：刀具逐渐磨损，刚度逐渐下降
            stiffness_factor = 1.0 - 0.3 * (time_indices / self.num_samples)
        elif self.drift_type == "sudden":
            # 突变漂移：在某个时间点突然变化（如更换刀具）
            change_point = int(self.num_samples * 0.5)
            stiffness_factor = np.where(
                time_indices < change_point,
                1.0,
                0.75
            )
        else:  # incremental
            # 增量漂移：分阶段变化
            stage_size = self.num_samples // 4
            stiffness_factor = np.ones(self.num_samples)
            for i in range(4):
                start_idx = i * stage_size
                end_idx = (i + 1) * stage_size if i < 3 else self.num_samples
                stiffness_factor[start_idx:end_idx] = 1.0 - 0.1 * i
        
        # 使用变化的刚度计算极限切深
        base_stiffness = 1.2e6
        tlusty_model = TlustyAnalyticalModel(
            stiffness=base_stiffness,
            modal_mass=120.0,
            damping_ratio=0.06
        )
        
        a_lim_clean = tlusty_model.compute_limiting_depth(spindle_speed)
        # 应用刚度漂移
        a_lim_clean = a_lim_clean * stiffness_factor
        
        # 添加噪声
        noise_level = 0.05
        a_lim = a_lim_clean * (1 + np.random.randn(self.num_samples) * noise_level)
        a_lim = np.maximum(a_lim, 0.01)
        
        # 生成特征
        features = np.column_stack([
            spindle_speed / 10000,
            axial_depth / 10,
            stiffness_factor  # 隐式包含漂移信息
        ]).astype(np.float32)
        
        return {
            'features': features,
            'a_lim': a_lim.astype(np.float32),
            'a_lim_clean': a_lim_clean.astype(np.float32),
            'stiffness_factor': stiffness_factor.astype(np.float32),
            'time_indices': time_indices.astype(np.int64)
        }
    
    def __len__(self) -> int:
        return self.num_samples
    
    def __getitem__(self, idx: int):
        features = torch.from_numpy(self.data['features'][idx])
        a_lim = torch.from_numpy(np.array([self.data['a_lim'][idx]]))
        a_lim_physics = torch.from_numpy(np.array([self.data['a_lim_clean'][idx]]))
        
        return features, a_lim, a_lim_physics


def train_model(
    model: torch.nn.Module,
    train_loader: DataLoader,
    num_epochs: int = 50,
    device: str = "cpu",
    lr: float = 0.001
) -> List[float]:
    """训练模型"""
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    criterion = torch.nn.MSELoss()
    
    losses = []
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
            if hasattr(model, '__class__') and model.__class__.__name__ in ['LSTM', 'GRU']:
                if outputs.dim() == 3:
                    outputs = outputs[:, -1, :]
                if outputs.shape[-1] != 1:
                    outputs = outputs.mean(dim=-1, keepdim=True)
            
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item()
        
        avg_loss = epoch_loss / len(train_loader)
        losses.append(avg_loss)
    
    return losses


def evaluate_model(
    model: torch.nn.Module,
    test_loader: DataLoader,
    device: str = "cpu"
) -> Dict[str, float]:
    """评估模型"""
    model.eval()
    all_preds = []
    all_labels = []
    
    with torch.no_grad():
        for features, labels, _ in test_loader:
            features = features.to(device)
            outputs = model(features)
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            
            # 处理LSTM/GRU输出
            if hasattr(model, '__class__') and model.__class__.__name__ in ['LSTM', 'GRU']:
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
    
    return results


def main():
    print("=" * 60)
    print("实验二十九：在线自适应/增量学习实验")
    print("=" * 60)
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")
    
    # 实验配置
    drift_types = ["gradual", "sudden", "incremental"]
    adaptation_strategies = ["offline", "finetune", "online"]
    
    all_results = {}
    
    for drift_type in drift_types:
        print(f"\n漂移类型: {drift_type}")
        print("-" * 40)
        
        # 创建带漂移的数据集
        dataset = ConceptDriftDataset(num_samples=5000, drift_type=drift_type)
        
        # 将数据按时间分为4个阶段（模拟不同时间段）
        stage_size = len(dataset) // 4
        stages_data = []
        for i in range(4):
            start_idx = i * stage_size
            end_idx = (i + 1) * stage_size if i < 3 else len(dataset)
            stage_indices = list(range(start_idx, end_idx))
            stages_data.append(stage_indices)
        
        drift_results = {}
        
        for strategy in adaptation_strategies:
            print(f"  自适应策略: {strategy}")
            
            if strategy == "offline":
                # 离线训练：仅用第一阶段数据训练，后续阶段直接测试
                stage_indices = stages_data[0]
                train_subset = torch.utils.data.Subset(dataset, stage_indices)
                train_loader = DataLoader(train_subset, batch_size=32, shuffle=True)
                
                model = DLLNNModel(input_dim=3, hidden_dim=64)
                train_model(model, train_loader, num_epochs=50, device=device)
                
                # 在所有阶段测试
                stage_results = {}
                for stage_idx, stage_indices in enumerate(stages_data):
                    test_subset = torch.utils.data.Subset(dataset, stage_indices)
                    test_loader = DataLoader(test_subset, batch_size=32, shuffle=False)
                    eval_results = evaluate_model(model, test_loader, device)
                    stage_results[f"stage_{stage_idx + 1}"] = {
                        "mae": round(float(eval_results['mae']), 4),
                        "rmse": round(float(eval_results['rmse']), 4),
                        "r2": round(float(eval_results.get('r2', 0)), 4),
                        "pcc": round(float(eval_results.get('pcc', 0)), 4)
                    }
                
                drift_results[strategy] = stage_results
            
            elif strategy == "finetune":
                # 微调策略：在每个新阶段用少量学习率微调
                # 第一阶段训练
                stage_indices = stages_data[0]
                train_subset = torch.utils.data.Subset(dataset, stage_indices)
                train_loader = DataLoader(train_subset, batch_size=32, shuffle=True)
                
                model = DLLNNModel(input_dim=3, hidden_dim=64)
                train_model(model, train_loader, num_epochs=50, device=device, lr=0.001)
                
                stage_results = {}
                # 第一阶段测试
                test_subset = torch.utils.data.Subset(dataset, stages_data[0])
                test_loader = DataLoader(test_subset, batch_size=32, shuffle=False)
                eval_results = evaluate_model(model, test_loader, device)
                stage_results["stage_1"] = {
                    "mae": round(float(eval_results['mae']), 4),
                    "rmse": round(float(eval_results['rmse']), 4),
                    "r2": round(float(eval_results.get('r2', 0)), 4),
                    "pcc": round(float(eval_results.get('pcc', 0)), 4)
                }
                
                # 后续阶段微调
                for stage_idx in range(1, 4):
                    stage_indices = stages_data[stage_idx]
                    train_subset = torch.utils.data.Subset(dataset, stage_indices)
                    train_loader = DataLoader(train_subset, batch_size=32, shuffle=True)
                    
                    # 用小学习率微调
                    train_model(model, train_loader, num_epochs=20, device=device, lr=0.0001)
                    
                    # 测试
                    test_subset = torch.utils.data.Subset(dataset, stage_indices)
                    test_loader = DataLoader(test_subset, batch_size=32, shuffle=False)
                    eval_results = evaluate_model(model, test_loader, device)
                    stage_results[f"stage_{stage_idx + 1}"] = {
                        "mae": round(float(eval_results['mae']), 4),
                        "rmse": round(float(eval_results['rmse']), 4),
                        "r2": round(float(eval_results.get('r2', 0)), 4),
                        "pcc": round(float(eval_results.get('pcc', 0)), 4)
                    }
                
                drift_results[strategy] = stage_results
            
            else:  # online
                # 在线学习：每个阶段都用新数据重新训练部分参数
                stage_results = {}
                model = None
                
                for stage_idx in range(4):
                    stage_indices = stages_data[stage_idx]
                    train_subset = torch.utils.data.Subset(dataset, stage_indices)
                    train_loader = DataLoader(train_subset, batch_size=32, shuffle=True)
                    
                    if model is None or stage_idx == 0:
                        # 第一阶段或重新初始化
                        model = DLLNNModel(input_dim=3, hidden_dim=64)
                        train_model(model, train_loader, num_epochs=50, device=device, lr=0.001)
                    else:
                        # 后续阶段：在线更新
                        train_model(model, train_loader, num_epochs=30, device=device, lr=0.0005)
                    
                    # 测试
                    test_subset = torch.utils.data.Subset(dataset, stage_indices)
                    test_loader = DataLoader(test_subset, batch_size=32, shuffle=False)
                    eval_results = evaluate_model(model, test_loader, device)
                    stage_results[f"stage_{stage_idx + 1}"] = {
                        "mae": round(float(eval_results['mae']), 4),
                        "rmse": round(float(eval_results['rmse']), 4),
                        "r2": round(float(eval_results.get('r2', 0)), 4),
                        "pcc": round(float(eval_results.get('pcc', 0)), 4)
                    }
                
                drift_results[strategy] = stage_results
        
        all_results[drift_type] = drift_results
    
    # 保存结果
    output_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, 'online_adaptation_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "在线自适应/增量学习实验",
            "num_stages": 4,
            "adaptation_strategies": adaptation_strategies,
            "results": all_results
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存至: {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
