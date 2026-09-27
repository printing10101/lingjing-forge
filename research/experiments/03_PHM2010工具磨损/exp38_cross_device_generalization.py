"""
实验38：跨设备泛化实验
评估模型在不同机床设备间的迁移能力
"""

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import json
import os
from datetime import datetime


class DLLNNModel(nn.Module):
    """DL-LNN模型简化版本"""
    def __init__(self, input_dim=10, hidden_dim=64, num_layers=3, output_dim=1, dt=0.01):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.dt = dt
        
        self.input_proj = nn.Linear(input_dim, hidden_dim)
        self.ltc_cells = nn.ModuleList([
            self._create_ltc_layer(hidden_dim) for _ in range(num_layers)
        ])
        self.output_proj = nn.Linear(hidden_dim, output_dim)
        
    def _create_ltc_layer(self, dim):
        return nn.LSTMCell(dim, dim)
    
    def forward(self, x):
        if x.dim() == 3:
            batch_size, seq_len, _ = x.shape
            x = x[:, -1, :]
        
        x = self.input_proj(x)
        
        for cell in self.ltc_cells:
            h = torch.zeros(x.size(0), self.hidden_dim, device=x.device)
            c = torch.zeros(x.size(0), self.hidden_dim, device=x.device)
            h, c = cell(x, (h, c))
            x = h
        
        return self.output_proj(x)


class LSTMModel(nn.Module):
    def __init__(self, input_dim=10, hidden_dim=64, num_layers=2, output_dim=1, dropout=0.1):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, 
                           batch_first=True, dropout=dropout)
        self.fc = nn.Linear(hidden_dim, output_dim)
    
    def forward(self, x):
        if x.dim() == 2:
            x = x.unsqueeze(1)
        out, _ = self.lstm(x)
        out = out[:, -1, :]
        return self.fc(out)


class GRUModel(nn.Module):
    def __init__(self, input_dim=10, hidden_dim=64, num_layers=2, output_dim=1, dropout=0.1):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers,
                         batch_first=True, dropout=dropout)
        self.fc = nn.Linear(hidden_dim, output_dim)
    
    def forward(self, x):
        if x.dim() == 2:
            x = x.unsqueeze(1)
        out, _ = self.gru(x)
        out = out[:, -1, :]
        return self.fc(out)


def generate_device_data(device_id, num_samples=200, noise_level=0.05):
    """生成模拟不同设备的铣削数据"""
    np.random.seed(device_id * 100)
    
    # 不同设备有不同的系统特性
    device_characteristics = {
        1: {'spindle_freq': 50.0, 'damping': 0.05, 'stiffness': 1.0},
        2: {'spindle_freq': 55.0, 'damping': 0.06, 'stiffness': 0.95},
        3: {'spindle_freq': 45.0, 'damping': 0.04, 'stiffness': 1.05},
        4: {'spindle_freq': 52.0, 'damping': 0.055, 'stiffness': 0.98},
    }
    
    char = device_characteristics.get(device_id, device_characteristics[1])
    
    # 生成切削参数
    spindle_speed = np.random.uniform(3000, 12000, num_samples)
    feed_rate = np.random.uniform(0.05, 0.2, num_samples)
    depth_of_cut = np.random.uniform(0.5, 3.0, num_samples)
    
    # 基于设备特性生成振动信号
    t = np.linspace(0, 1, 100)
    signals = []
    
    for i in range(num_samples):
        # 主轴频率及其谐波
        freq = char['spindle_freq'] * (spindle_speed[i] / 6000)
        signal = np.sin(2 * np.pi * freq * t)
        
        # 添加阻尼效应
        signal *= np.exp(-char['damping'] * t)
        
        # 添加切削力影响
        force_effect = depth_of_cut[i] * 0.1 * np.sin(2 * np.pi * 2 * freq * t)
        signal += force_effect
        
        # 添加噪声
        noise = np.random.normal(0, noise_level, len(t))
        signal += noise
        
        # 特征提取
        features = [
            np.mean(signal),
            np.std(signal),
            np.max(signal),
            np.min(signal),
            np.sqrt(np.mean(signal**2)),
            np.mean(np.abs(signal)),
            spindle_speed[i] / 12000,
            feed_rate[i] / 0.2,
            depth_of_cut[i] / 3.0,
            char['stiffness']
        ]
        signals.append(features)
    
    X = np.array(signals, dtype=np.float32)
    
    # 生成目标值（极限切削深度）
    y = (depth_of_cut * 0.4 + 
         feed_rate * 2.0 + 
         spindle_speed / 10000 * 0.3 +
         np.random.normal(0, 0.1, num_samples))
    y = y.astype(np.float32)
    
    return X, y


def train_and_evaluate(model, X_train, y_train, X_test, y_test, epochs=50, lr=0.001):
    """训练并评估模型"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = model.to(device)
    
    train_dataset = TensorDataset(
        torch.FloatTensor(X_train),
        torch.FloatTensor(y_train)
    )
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()
    
    model.train()
    for epoch in range(epochs):
        for batch_x, batch_y in train_loader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            optimizer.zero_grad()
            pred = model(batch_x).squeeze()
            loss = criterion(pred, batch_y)
            loss.backward()
            optimizer.step()
    
    # 评估
    model.eval()
    with torch.no_grad():
        test_x = torch.FloatTensor(X_test).to(device)
        test_y = torch.FloatTensor(y_test).to(device)
        pred = model(test_x).squeeze()
        
        pred_np = pred.cpu().numpy()
        true_np = test_y.cpu().numpy()
        
        mae = np.mean(np.abs(pred_np - true_np))
        rmse = np.sqrt(np.mean((pred_np - true_np) ** 2))
        r2 = 1 - np.sum((true_np - pred_np) ** 2) / (np.sum((true_np - np.mean(true_np)) ** 2) + 1e-8)
        
        if np.std(pred_np) > 1e-8 and np.std(true_np) > 1e-8:
            pcc = np.corrcoef(pred_np, true_np)[0, 1]
        else:
            pcc = 0.0
    
    return {
        'mae': float(mae),
        'rmse': float(rmse),
        'r2': float(r2),
        'pcc': float(pcc)
    }


def cross_device_experiment():
    """执行跨设备泛化实验"""
    print("=" * 60)
    print("实验38：跨设备泛化实验")
    print("=" * 60)
    
    # 生成4个不同设备的数据
    devices = {}
    for device_id in range(1, 5):
        X, y = generate_device_data(device_id, num_samples=200)
        devices[f'Device_{device_id}'] = (X, y)
        print(f"设备 {device_id} 数据生成完成: X.shape={X.shape}")
    
    results = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'experiment': '跨设备泛化实验',
        'devices': list(devices.keys()),
        'models': ['DL-LNN', 'LSTM', 'GRU'],
        'results': {
            'single_device_training': {},
            'cross_device_generalization': {},
            'multi_device_training': {}
        }
    }
    
    # 实验1：单设备训练，同设备测试
    print("\n[实验1] 单设备训练与测试")
    for device_name, (X, y) in devices.items():
        print(f"  训练设备: {device_name}")
        
        # 划分数据
        n = len(X)
        train_idx = int(n * 0.7)
        val_idx = int(n * 0.85)
        
        X_train, y_train = X[:train_idx], y[:train_idx]
        X_val, y_val = X[train_idx:val_idx], y[train_idx:val_idx]
        X_test, y_test = X[val_idx:], y[val_idx:]
        
        device_results = {}
        
        # DL-LNN
        model = DLLNNModel(input_dim=X.shape[1], hidden_dim=64, num_layers=3)
        metrics = train_and_evaluate(model, X_train, y_train, X_test, y_test)
        device_results['DL-LNN'] = metrics
        
        # LSTM
        model = LSTMModel(input_dim=X.shape[1], hidden_dim=64, num_layers=2)
        metrics = train_and_evaluate(model, X_train, y_train, X_test, y_test)
        device_results['LSTM'] = metrics
        
        # GRU
        model = GRUModel(input_dim=X.shape[1], hidden_dim=64, num_layers=2)
        metrics = train_and_evaluate(model, X_train, y_train, X_test, y_test)
        device_results['GRU'] = metrics
        
        results['results']['single_device_training'][device_name] = device_results
    
    # 实验2：跨设备泛化
    print("\n[实验2] 跨设备泛化")
    for source_device, (X_src, y_src) in devices.items():
        print(f"  源设备: {source_device}")
        
        n = len(X_src)
        X_train_src = X_src[:int(n * 0.7)]
        y_train_src = y_src[:int(n * 0.7)]
        
        cross_results = {}
        
        for target_device, (X_tgt, y_tgt) in devices.items():
            if source_device == target_device:
                continue
            
            # 使用源设备数据训练，目标设备数据测试
            n_tgt = len(X_tgt)
            X_test_tgt = X_tgt[int(n_tgt * 0.7):]
            y_test_tgt = y_tgt[int(n_tgt * 0.7):]
            
            target_results = {}
            
            # DL-LNN
            model = DLLNNModel(input_dim=X_src.shape[1], hidden_dim=64, num_layers=3)
            metrics = train_and_evaluate(model, X_train_src, y_train_src, X_test_tgt, y_test_tgt)
            target_results['DL-LNN'] = metrics
            
            # LSTM
            model = LSTMModel(input_dim=X_src.shape[1], hidden_dim=64, num_layers=2)
            metrics = train_and_evaluate(model, X_train_src, y_train_src, X_test_tgt, y_test_tgt)
            target_results['LSTM'] = metrics
            
            # GRU
            model = GRUModel(input_dim=X_src.shape[1], hidden_dim=64, num_layers=2)
            metrics = train_and_evaluate(model, X_train_src, y_train_src, X_test_tgt, y_test_tgt)
            target_results['GRU'] = metrics
            
            cross_results[target_device] = target_results
        
        results['results']['cross_device_generalization'][source_device] = cross_results
    
    # 实验3：多设备联合训练
    print("\n[实验3] 多设备联合训练")
    # 合并所有设备数据
    X_all = []
    y_all = []
    for device_name, (X, y) in devices.items():
        X_all.append(X)
        y_all.append(y)
    
    X_all = np.concatenate(X_all, axis=0)
    y_all = np.concatenate(y_all, axis=0)
    
    # 随机划分
    indices = np.random.permutation(len(X_all))
    train_idx = int(len(X_all) * 0.7)
    val_idx = int(len(X_all) * 0.85)
    
    X_train = X_all[indices[:train_idx]]
    y_train = y_all[indices[:train_idx]]
    X_val = X_all[indices[train_idx:val_idx]]
    y_val = y_all[indices[train_idx:val_idx]]
    X_test = X_all[indices[val_idx:]]
    y_test = y_all[indices[val_idx:]]
    
    multi_device_results = {}
    
    # DL-LNN
    model = DLLNNModel(input_dim=X_all.shape[1], hidden_dim=64, num_layers=3)
    metrics = train_and_evaluate(model, X_train, y_train, X_test, y_test)
    multi_device_results['DL-LNN'] = metrics
    
    # LSTM
    model = LSTMModel(input_dim=X_all.shape[1], hidden_dim=64, num_layers=2)
    metrics = train_and_evaluate(model, X_train, y_train, X_test, y_test)
    multi_device_results['LSTM'] = metrics
    
    # GRU
    model = GRUModel(input_dim=X_all.shape[1], hidden_dim=64, num_layers=2)
    metrics = train_and_evaluate(model, X_train, y_train, X_test, y_test)
    multi_device_results['GRU'] = metrics
    
    results['results']['multi_device_training'] = multi_device_results
    
    # 保存结果
    output_path = os.path.join(os.path.dirname(__file__), 'results', 
                               'cross_device_generalization_results.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存至: {output_path}")
    print("=" * 60)
    
    return results


if __name__ == '__main__':
    cross_device_experiment()
