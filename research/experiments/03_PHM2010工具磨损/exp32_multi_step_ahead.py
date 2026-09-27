"""
实验三十二：多步ahead预测实验
评估模型对未来多时间步颤振趋势的预测能力
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


class MultiStepDataset(Dataset):
    """
    多步预测数据集
    生成时间序列数据，支持多步ahead预测
    """
    
    def __init__(
        self,
        num_samples: int = 5000,
        seq_length: int = 20,
        ahead_steps: int = 5,
        seed: int = 42
    ):
        super().__init__()
        self.num_samples = num_samples
        self.seq_length = seq_length
        self.ahead_steps = ahead_steps
        
        np.random.seed(seed)
        self.data = self._generate_data()
    
    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成时间序列数据"""
        total_length = self.num_samples + self.seq_length + self.ahead_steps
        
        # 生成主轴转速和切深的时间序列
        spindle_speed = np.random.uniform(3000, 9000, total_length)
        axial_depth = np.random.uniform(0.5, 8.0, total_length)
        
        # 使用Tlusty模型计算极限切深
        tlusty_model = TlustyAnalyticalModel()
        a_lim = tlusty_model.compute_limiting_depth(spindle_speed)
        
        # 添加时序相关性（模拟真实加工过程）
        a_lim_smooth = np.convolve(a_lim, np.ones(5)/5, mode='same')
        
        return {
            'spindle_speed': spindle_speed.astype(np.float32),
            'axial_depth': axial_depth.astype(np.float32),
            'a_lim': a_lim_smooth.astype(np.float32)
        }
    
    def __len__(self) -> int:
        return self.num_samples
    
    def __getitem__(self, idx: int):
        # 输入序列
        start_idx = idx
        end_idx = idx + self.seq_length
        
        features = np.column_stack([
            self.data['spindle_speed'][start_idx:end_idx] / 10000,
            self.data['axial_depth'][start_idx:end_idx] / 10
        ]).astype(np.float32)
        
        # 目标序列（未来ahead_steps步）
        target_start = end_idx
        target_end = target_start + self.ahead_steps
        targets = self.data['a_lim'][target_start:target_end].astype(np.float32)
        
        return torch.from_numpy(features), torch.from_numpy(targets)


class MultiStepPredictor(torch.nn.Module):
    """多步预测模型"""
    
    def __init__(self, input_dim: int = 7, hidden_dim: int = 64, ahead_steps: int = 5):
        super().__init__()
        self.ltc1 = torch.nn.Linear(input_dim, hidden_dim)
        self.ltc2 = torch.nn.Linear(hidden_dim, hidden_dim)
        self.output_layer = torch.nn.Linear(hidden_dim, ahead_steps)
        
        # 时间常数
        self.tau1 = torch.nn.Parameter(torch.ones(hidden_dim) * 0.1)
        self.tau2 = torch.nn.Parameter(torch.ones(hidden_dim) * 0.1)
    
    def forward(self, x):
        # x: [batch, seq_len, input_dim]
        batch_size, seq_len, _ = x.shape
        
        # 处理序列
        h1 = torch.tanh(self.ltc1(x[:, -1, :]))  # 取最后一个时间步
        h2 = torch.tanh(self.ltc2(h1))
        output = self.output_layer(h2)
        
        return output


def train_multi_step(
    model: torch.nn.Module,
    train_loader: DataLoader,
    num_epochs: int = 80,
    device: str = "cpu"
) -> List[float]:
    """训练多步预测模型"""
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)
    criterion = torch.nn.MSELoss()
    
    losses = []
    model.train()
    
    for epoch in range(num_epochs):
        epoch_loss = 0.0
        for features, targets in train_loader:
            features = features.to(device)
            targets = targets.to(device)
            
            optimizer.zero_grad()
            outputs = model(features)
            loss = criterion(outputs, targets)
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item()
        
        losses.append(epoch_loss / len(train_loader))
    
    return losses


def evaluate_multi_step(
    model: torch.nn.Module,
    test_loader: DataLoader,
    ahead_steps: int,
    device: str = "cpu"
) -> Dict[str, float]:
    """评估多步预测性能"""
    model.eval()
    
    all_preds = []
    all_targets = []
    
    with torch.no_grad():
        for features, targets in test_loader:
            features = features.to(device)
            outputs = model(features)
            all_preds.append(outputs.cpu().numpy())
            all_targets.append(targets.numpy())
    
    all_preds = np.concatenate(all_preds, axis=0)
    all_targets = np.concatenate(all_targets, axis=0)
    
    # 计算每个ahead step的指标
    step_metrics = {}
    for step in range(ahead_steps):
        pred_step = all_preds[:, step]
        target_step = all_targets[:, step]
        
        mae = float(np.mean(np.abs(pred_step - target_step)))
        rmse = float(np.sqrt(np.mean((pred_step - target_step) ** 2)))
        
        # R²
        ss_res = np.sum((target_step - pred_step) ** 2)
        ss_tot = np.sum((target_step - np.mean(target_step)) ** 2)
        r2 = float(1 - ss_res / (ss_tot + 1e-8))
        
        # PCC
        pcc = float(np.corrcoef(pred_step, target_step)[0, 1])
        
        step_metrics[f"step_{step + 1}"] = {
            "mae": round(mae, 4),
            "rmse": round(rmse, 4),
            "r2": round(r2, 4),
            "pcc": round(pcc, 4)
        }
    
    # 平均指标
    avg_mae = float(np.mean([step_metrics[f"step_{s+1}"]["mae"] for s in range(ahead_steps)]))
    avg_pcc = float(np.mean([step_metrics[f"step_{s+1}"]["pcc"] for s in range(ahead_steps)]))
    
    step_metrics["average"] = {
        "mae": round(avg_mae, 4),
        "pcc": round(avg_pcc, 4)
    }
    
    return step_metrics


def main():
    print("=" * 60)
    print("实验三十二：多步ahead预测实验")
    print("=" * 60)
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")
    
    # 实验配置
    ahead_steps_list = [1, 3, 5, 10, 20]
    seq_length = 20
    
    all_results = {}
    
    for ahead_steps in ahead_steps_list:
        print(f"\n预测步数: {ahead_steps}")
        print("-" * 40)
        
        # 创建数据集
        dataset = MultiStepDataset(
            num_samples=5000,
            seq_length=seq_length,
            ahead_steps=ahead_steps,
            seed=42
        )
        
        # 划分训练集和测试集
        train_size = int(0.8 * len(dataset))
        test_size = len(dataset) - train_size
        train_dataset, test_dataset = torch.utils.data.random_split(dataset, [train_size, test_size])
        
        train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
        test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)
        
        # 训练和评估
        model = MultiStepPredictor(input_dim=7, hidden_dim=64, ahead_steps=ahead_steps)
        losses = train_multi_step(model, train_loader, num_epochs=80, device=device)
        
        # 评估
        step_metrics = evaluate_multi_step(model, test_loader, ahead_steps, device)
        
        all_results[f"ahead_{ahead_steps}"] = step_metrics
        
        print(f"  平均MAE: {step_metrics['average']['mae']:.4f}")
        print(f"  平均PCC: {step_metrics['average']['pcc']:.4f}")
    
    # 保存结果
    output_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, 'multi_step_ahead_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "多步ahead预测实验",
            "sequence_length": seq_length,
            "ahead_steps_list": ahead_steps_list,
            "results": all_results
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存至: {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
