"""
实验二十五：早期颤振检测时效性实验
评估模型在颤振发生前多少个时间步能准确预警
"""

import torch
import numpy as np
import json
import os
import time
from typing import Dict, List, Tuple
from torch.utils.data import Dataset, DataLoader

from models import DLLNNModel
from data_generator import PHM2010Dataset, create_dataloaders
from metrics import ChatterMetrics


class EarlyDetectionDataset(Dataset):
    """
    早期检测数据集
    生成带有时间序列标签的数据，用于评估早期预警能力
    """
    
    def __init__(
        self,
        num_samples: int = 2000,
        sequence_length: int = 50,
        early_warning_steps: int = 10,
        seed: int = 42
    ):
        super().__init__()
        self.num_samples = num_samples
        self.sequence_length = sequence_length
        self.early_warning_steps = early_warning_steps
        
        np.random.seed(seed)
        self.data = self._generate_data()
    
    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成带有时间序列标签的数据"""
        # 生成主轴转速和切深序列
        spindle_speed = np.random.uniform(3000, 9000, (self.num_samples, self.sequence_length))
        axial_depth = np.random.uniform(0.5, 8.0, (self.num_samples, self.sequence_length))
        
        # 生成稳定性标签（考虑时间演化）
        stability = np.zeros((self.num_samples, self.sequence_length), dtype=np.int64)
        chatter_onset_time = np.random.randint(
            self.sequence_length // 2, 
            self.sequence_length - self.early_warning_steps,
            self.num_samples
        )
        
        for i in range(self.num_samples):
            onset = chatter_onset_time[i]
            # 在颤振发生前，切深逐渐增加
            for t in range(self.sequence_length):
                if t < onset:
                    # 稳定阶段
                    stability[i, t] = 0
                else:
                    # 颤振阶段
                    stability[i, t] = 1
        
        # 构造特征
        features = np.stack([
            spindle_speed / 10000,
            axial_depth / 10
        ], axis=-1).astype(np.float32)
        
        return {
            'features': features,
            'stability': stability,
            'chatter_onset_time': chatter_onset_time
        }
    
    def __len__(self) -> int:
        return self.num_samples
    
    def __getitem__(self, idx: int):
        features = torch.from_numpy(self.data['features'][idx])
        stability = torch.from_numpy(self.data['stability'][idx])
        onset_time = torch.tensor(self.data['chatter_onset_time'][idx], dtype=torch.long)
        
        return features, stability, onset_time


def evaluate_early_detection(
    model: torch.nn.Module,
    test_loader: DataLoader,
    early_warning_steps: int = 10,
    device: str = "cpu"
) -> Dict:
    """
    评估早期检测能力
    
    Args:
        model: 预测模型
        test_loader: 测试数据加载器
        early_warning_steps: 提前预警步数
        device: 设备
    
    Returns:
        评估结果字典
    """
    model = model.to(device)
    model.eval()
    
    # 统计指标
    true_positives = 0
    false_positives = 0
    false_negatives = 0
    true_negatives = 0
    
    early_detection_correct = 0
    early_detection_total = 0
    
    detection_lead_times = []
    
    with torch.no_grad():
        for features, stability, onset_times in test_loader:
            features = features.to(device)
            batch_size, seq_len, input_dim = features.shape
            
            # 模型预测 - 处理序列输入
            if hasattr(model, 'ltc_cells'):  # DL-LNN
                outputs_list = []
                for t in range(seq_len):
                    x_t = features[:, t, :]
                    out = model(x_t)
                    if isinstance(out, tuple):
                        out = out[0]
                    outputs_list.append(out)
                outputs = torch.stack(outputs_list, dim=1)
            else:  # LSTM/GRU
                outputs = model(features)[0]
                outputs = torch.nn.functional.linear(outputs, torch.nn.Parameter(torch.randn(1, 64, device=device)))
            
            # 转换为稳定性预测（阈值0.5）
            predictions = (outputs.squeeze(-1) > 0.5).long()
            
            # 对每个样本进行评估
            for i in range(batch_size):
                pred_seq = predictions[i].cpu().numpy()
                true_seq = stability[i].numpy()
                onset = onset_times[i].item()
                
                # 计算提前检测能力
                early_warning_point = max(0, onset - early_warning_steps)
                
                # 检查是否在颤振发生前正确预警
                if early_warning_point < len(pred_seq):
                    early_pred = pred_seq[early_warning_point:onset]
                    early_true = true_seq[early_warning_point:onset]
                    
                    # 如果在预警窗口内有预测为颤振，认为成功预警
                    if np.sum(early_pred > 0) > 0 and np.sum(early_true > 0) > 0:
                        early_detection_correct += 1
                    early_detection_total += 1
                
                # 计算整体检测指标
                for t in range(len(pred_seq)):
                    if true_seq[t] == 1 and pred_seq[t] == 1:
                        true_positives += 1
                    elif true_seq[t] == 0 and pred_seq[t] == 1:
                        false_positives += 1
                    elif true_seq[t] == 1 and pred_seq[t] == 0:
                        false_negatives += 1
                    else:
                        true_negatives += 1
                
                # 计算检测提前时间
                first_detection = np.argmax(pred_seq > 0)
                if first_detection < onset:
                    lead_time = onset - first_detection
                    detection_lead_times.append(lead_time)
    
    # 计算指标
    precision = true_positives / (true_positives + false_positives + 1e-8)
    recall = true_positives / (true_positives + false_negatives + 1e-8)
    f1_score = 2 * precision * recall / (precision + recall + 1e-8)
    accuracy = (true_positives + true_negatives) / (true_positives + true_negatives + false_positives + false_negatives + 1e-8)
    
    early_detection_rate = early_detection_correct / (early_detection_total + 1e-8)
    
    avg_lead_time = np.mean(detection_lead_times) if detection_lead_times else 0
    max_lead_time = np.max(detection_lead_times) if detection_lead_times else 0
    
    return {
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1_score": round(float(f1_score), 4),
        "accuracy": round(float(accuracy), 4),
        "early_detection_rate": round(float(early_detection_rate), 4),
        "avg_lead_time_steps": round(float(avg_lead_time), 2),
        "max_lead_time_steps": int(max_lead_time),
        "total_samples": early_detection_total,
        "early_detection_correct": early_detection_correct
    }


def run_experiment():
    """运行早期检测实验"""
    print("=" * 60)
    print("实验二十五：早期颤振检测时效性实验")
    print("=" * 60)
    
    # 配置
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")
    
    # 创建数据集
    print("\n创建数据集...")
    train_dataset = EarlyDetectionDataset(num_samples=1600, sequence_length=50, early_warning_steps=10, seed=42)
    test_dataset = EarlyDetectionDataset(num_samples=400, sequence_length=50, early_warning_steps=10, seed=43)
    
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)
    
    print(f"训练集样本数: {len(train_dataset)}")
    print(f"测试集样本数: {len(test_dataset)}")
    
    # 创建模型
    print("\n创建模型...")
    models = {
        "DL-LNN": DLLNNModel(
            input_dim=7,
            hidden_dim=64,
            num_layers=3,
            output_dim=1,
            dt=0.1,
            dropout=0.2
        ),
        "LSTM": torch.nn.LSTM(
            input_size=7,
            hidden_size=64,
            num_layers=2,
            batch_first=True
        ),
        "GRU": torch.nn.GRU(
            input_size=7,
            hidden_size=64,
            num_layers=2,
            batch_first=True
        )
    }
    
    # 训练模型
    print("\n训练模型...")
    criterion = torch.nn.BCEWithLogitsLoss()
    
    for model_name, model in models.items():
        print(f"\n训练 {model_name}...")
        model = model.to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
        
        model.train()
        for epoch in range(20):
            total_loss = 0
            for features, stability, _ in train_loader:
                features = features.to(device)
                stability = stability.to(device).float()
                
                optimizer.zero_grad()
                
                # 处理序列输入
                batch_size, seq_len, input_dim = features.shape
                
                if model_name == "DL-LNN":
                    # DL-LNN 需要逐时间步处理
                    outputs_list = []
                    for t in range(seq_len):
                        x_t = features[:, t, :]  # [batch, input_dim]
                        out = model(x_t)
                        if isinstance(out, tuple):
                            out = out[0]
                        outputs_list.append(out)
                    outputs = torch.stack(outputs_list, dim=1)  # [batch, seq_len, 1]
                else:
                    # LSTM/GRU 可以直接处理序列
                    outputs = model(features)[0]  # [batch, seq_len, hidden]
                    # 添加输出层
                    outputs = torch.nn.functional.linear(outputs, torch.nn.Parameter(torch.randn(1, 64, device=device)))
                
                loss = criterion(outputs.squeeze(-1), stability)
                loss.backward()
                optimizer.step()
                total_loss += loss.item()
            
            if (epoch + 1) % 5 == 0:
                avg_loss = total_loss / len(train_loader)
                print(f"  Epoch [{epoch+1}/20], Loss: {avg_loss:.4f}")
    
    # 评估早期检测能力
    print("\n评估早期检测能力...")
    results = {}
    
    for early_warning_steps in [5, 10, 15, 20]:
        print(f"\n提前预警步数: {early_warning_steps}")
        
        # 重新创建测试集
        test_dataset = EarlyDetectionDataset(
            num_samples=400, 
            sequence_length=50, 
            early_warning_steps=early_warning_steps, 
            seed=43
        )
        test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)
        
        model_results = {}
        for model_name, model in models.items():
            print(f"  评估 {model_name}...")
            metrics = evaluate_early_detection(
                model, test_loader, 
                early_warning_steps=early_warning_steps,
                device=device
            )
            model_results[model_name] = metrics
            print(f"    早期检测率: {metrics['early_detection_rate']:.4f}")
            print(f"    平均提前时间: {metrics['avg_lead_time_steps']:.2f} 步")
        
        results[f"warning_steps_{early_warning_steps}"] = model_results
    
    # 保存结果
    output_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, 'early_detection_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "早期颤振检测时效性实验",
            "sequence_length": 50,
            "results": results
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存至: {output_file}")
    print("=" * 60)
    
    return results


if __name__ == "__main__":
    run_experiment()
