"""
实验二十七：多传感器对比实验
对比仅用加速度、仅用力信号、仅用电流信号与多信号融合的性能差异，验证传感器融合的增益
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


class MultiSensorDataset(Dataset):
    """
    多传感器数据集
    模拟加速度、力信号、电流信号以及融合信号
    """
    
    def __init__(
        self,
        num_samples: int = 3000,
        sensor_config: str = "all",
        noise_level: float = 0.05,
        seed: int = 42
    ):
        """
        Args:
            num_samples: 样本数
            sensor_config: 传感器配置 ("accel", "force", "current", "all")
            noise_level: 噪声水平
            seed: 随机种子
        """
        super().__init__()
        self.num_samples = num_samples
        self.sensor_config = sensor_config
        self.noise_level = noise_level
        
        np.random.seed(seed)
        self.data = self._generate_data()
    
    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成多传感器数据"""
        # 基础切削参数
        spindle_speed = np.random.uniform(3000, 9000, self.num_samples)
        axial_depth = np.random.uniform(0.5, 8.0, self.num_samples)
        
        # 使用Tlusty模型生成基础标签
        from data_generator import TlustyAnalyticalModel
        tlusty_model = TlustyAnalyticalModel(
            stiffness=1.2e6,
            modal_mass=120.0,
            damping_ratio=0.06
        )
        
        a_lim_clean = tlusty_model.compute_limiting_depth(spindle_speed)
        a_lim = a_lim_clean * (1 + np.random.randn(self.num_samples) * self.noise_level)
        a_lim = np.maximum(a_lim, 0.01)
        
        # 生成不同传感器的特征
        # 加速度传感器特征：与振动相关，对高频敏感
        accel_features = np.column_stack([
            spindle_speed / 10000,
            axial_depth / 10,
            np.abs(np.sin(spindle_speed / 1000)) * 0.3,  # 振动幅值
            np.random.randn(self.num_samples) * 0.05  # 噪声
        ]).astype(np.float32)
        
        # 力传感器特征：与切削力相关
        force_features = np.column_stack([
            spindle_speed / 10000,
            axial_depth / 10,
            axial_depth * spindle_speed / 100000,  # 切削力估计
            np.random.randn(self.num_samples) * 0.05
        ]).astype(np.float32)
        
        # 电流传感器特征：与主轴负载相关
        current_features = np.column_stack([
            spindle_speed / 10000,
            axial_depth / 10,
            axial_depth * 0.5 + np.random.randn(self.num_samples) * 0.1,  # 电流估计
            np.random.randn(self.num_samples) * 0.05
        ]).astype(np.float32)
        
        # 融合特征：所有传感器信息
        fused_features = np.column_stack([
            spindle_speed / 10000,
            axial_depth / 10,
            np.abs(np.sin(spindle_speed / 1000)) * 0.3,
            axial_depth * spindle_speed / 100000,
            axial_depth * 0.5,
            np.random.randn(self.num_samples) * 0.03
        ]).astype(np.float32)
        
        # 根据配置选择特征
        sensor_map = {
            "accel": accel_features,
            "force": force_features,
            "current": current_features,
            "all": fused_features
        }
        
        features = sensor_map[self.sensor_config]
        
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
    model: torch.nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    test_loader: DataLoader,
    num_epochs: int = 80,
    device: str = "cpu"
) -> Dict[str, float]:
    """训练并评估模型"""
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)
    criterion = torch.nn.MSELoss()
    
    best_val_loss = float('inf')
    best_model_state = None
    
    for epoch in range(num_epochs):
        # 训练
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
        
        # 验证
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
        
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_model_state = {k: v.clone() for k, v in model.state_dict().items()}
    
    # 加载最佳模型并测试
    if best_model_state is not None:
        model.load_state_dict(best_model_state)
    
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
            if hasattr(model, '__class__') and model.__class__.__name__ in ['LSTM', 'GRU', 'Sequential']:
                # 取最后一个时间步的隐藏状态
                if outputs.dim() == 3:  # [batch, seq, hidden]
                    outputs = outputs[:, -1, :]
                # 临时投影到输出维度
                if outputs.shape[-1] != 1:
                    outputs = outputs.mean(dim=-1, keepdim=True)
            
            all_preds.append(outputs.cpu().numpy())
            all_labels.append(labels.numpy())
    
    all_preds = np.concatenate(all_preds, axis=0)
    all_labels = np.concatenate(all_labels, axis=0)
    
    metrics = ChatterMetrics()
    results = metrics.compute_all(all_preds, all_labels)
    
    return results


def run_sensor_comparison(sensor_config: str, device: str = "cpu") -> Dict:
    """
    运行单一传感器配置的对比实验
    
    Args:
        sensor_config: 传感器配置
        device: 设备
    
    Returns:
        实验结果
    """
    print(f"\n传感器配置: {sensor_config}")
    
    # 创建数据集
    train_dataset = MultiSensorDataset(num_samples=2100, sensor_config=sensor_config, seed=42)
    val_dataset = MultiSensorDataset(num_samples=450, sensor_config=sensor_config, seed=43)
    test_dataset = MultiSensorDataset(num_samples=450, sensor_config=sensor_config, seed=44)
    
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)
    
    # 确定输入维度
    input_dims = {
        "accel": 4,
        "force": 4,
        "current": 4,
        "all": 6
    }
    input_dim = input_dims[sensor_config]
    
    # 创建并训练多个模型
    models = {
        "DL-LNN": DLLNNModel(
            input_dim=input_dim,
            hidden_dim=64,
            num_layers=3,
            output_dim=1,
            dt=0.1,
            dropout=0.2
        ),
        "LSTM": torch.nn.Sequential(
            torch.nn.LSTM(input_size=input_dim, hidden_size=64, num_layers=2, batch_first=True),
        ),
        "GRU": torch.nn.Sequential(
            torch.nn.GRU(input_size=input_dim, hidden_size=64, num_layers=2, batch_first=True),
        )
    }
    
    results = {}
    for model_name, model in models.items():
        print(f"  训练 {model_name}...")
        eval_results = train_and_evaluate(
            model, train_loader, val_loader, test_loader,
            num_epochs=80, device=device
        )
        results[model_name] = eval_results
        print(f"    MAE: {eval_results['mae']:.4f}, R2: {eval_results['r2']:.4f}, PCC: {eval_results.get('pcc', 0.0):.4f}")
    
    return results


def main():
    """主函数"""
    print("=" * 60)
    print("实验二十七：多传感器对比实验")
    print("=" * 60)
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")
    
    # 传感器配置
    sensor_configs = ["accel", "force", "current", "all"]
    sensor_names = {
        "accel": "加速度传感器",
        "force": "力传感器",
        "current": "电流传感器",
        "all": "多传感器融合"
    }
    
    all_results = {}
    
    for config in sensor_configs:
        print(f"\n{'='*40}")
        print(f"传感器: {sensor_names[config]}")
        print(f"{'='*40}")
        
        config_results = run_sensor_comparison(config, device)
        all_results[sensor_names[config]] = config_results
    
    # 计算融合增益
    print("\n\n计算传感器融合增益...")
    fusion_gains = {}
    
    for model_name in ["DL-LNN", "LSTM", "GRU"]:
        baseline_mae = None
        fused_mae = None
        
        gains = {}
        for config in sensor_configs:
            sensor_name = sensor_names[config]
            mae = all_results[sensor_name][model_name]["mae"]
            
            if config == "all":
                fused_mae = mae
            else:
                if baseline_mae is None or mae < baseline_mae:
                    baseline_mae = mae
                gains[sensor_names[config]] = {
                    "MAE": mae,
                    "improvement_vs_fused": round((mae - fused_mae) / mae * 100, 2) if fused_mae else 0
                }
        
        fusion_gains[model_name] = {
            "best_single_sensor_mae": baseline_mae,
            "fused_mae": fused_mae,
            "improvement_pct": round((baseline_mae - fused_mae) / baseline_mae * 100, 2) if baseline_mae and fused_mae else 0,
            "single_sensor_details": gains
        }
    
    # 保存结果
    output_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, 'multi_sensor_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "多传感器对比实验",
            "sensor_configs": sensor_configs,
            "sensor_names": sensor_names,
            "results": all_results,
            "fusion_gains": fusion_gains
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存至: {output_file}")
    print("=" * 60)
    
    return all_results


if __name__ == "__main__":
    main()
