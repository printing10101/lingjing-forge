"""
实验41：不同采样率下的性能实验
评估模型在不同数据采集频率下的表现，指导实际部署
"""

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from scipy import signal as scipy_signal
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
        self.lstm_cell = nn.LSTMCell(hidden_dim, hidden_dim)
        self.output_proj = nn.Linear(hidden_dim, output_dim)
        
    def forward(self, x):
        if x.dim() == 3:
            x = x[:, -1, :]
        
        x = self.input_proj(x)
        h = torch.zeros(x.size(0), self.hidden_dim, device=x.device)
        c = torch.zeros(x.size(0), self.hidden_dim, device=x.device)
        h, c = self.lstm_cell(x, (h, c))
        
        return self.output_proj(h)


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


def generate_high_freq_milling_data(num_samples=2000, original_fs=10000):
    """
    生成高频铣削振动信号
    original_fs: 原始采样率 (Hz)
    """
    np.random.seed(42)
    duration = num_samples / original_fs
    t = np.linspace(0, duration, num_samples)
    
    # 切削参数
    spindle_speed = np.random.uniform(3000, 12000, num_samples)
    feed_rate = np.random.uniform(0.05, 0.2, num_samples)
    depth_of_cut = np.random.uniform(0.5, 3.0, num_samples)
    
    # 振动信号（包含多个频率分量）
    spindle_freq = spindle_speed / 60.0  # Hz
    
    # 生成多分量振动信号
    vibration = np.zeros_like(t)
    for i in range(len(t)):
        # 基频及其谐波
        vibration[i] = (np.sin(2 * np.pi * spindle_freq[i] * t[i]) +
                       0.5 * np.sin(2 * np.pi * 2 * spindle_freq[i] * t[i]) +
                       0.3 * np.sin(2 * np.pi * 3 * spindle_freq[i] * t[i]) +
                       0.2 * np.sin(2 * np.pi * 120 * t[i]))  # 颤振分量
    
    # 添加噪声
    noise = np.random.normal(0, 0.1, len(t))
    vibration += noise
    
    # 目标值
    y = (depth_of_cut * 0.4 + 
         feed_rate * 2.0 + 
         spindle_speed / 10000 * 0.3 +
         np.random.normal(0, 0.1, num_samples))
    
    return vibration, y, {
        'spindle_speed': spindle_speed,
        'feed_rate': feed_rate,
        'depth_of_cut': depth_of_cut,
        'original_fs': original_fs
    }


def downsample_signal(signal_data, original_fs, target_fs):
    """
    降采样信号
    """
    if target_fs >= original_fs:
        return signal_data, original_fs
    
    # 计算降采样比例
    ratio = original_fs / target_fs
    
    # 使用scipy进行抗混叠降采样
    downsampled = scipy_signal.resample(signal_data, int(len(signal_data) / ratio))
    
    return downsampled, target_fs


def extract_features_from_signal(signal_data, window_size=100):
    """
    从信号中提取特征
    """
    num_windows = len(signal_data) // window_size
    features = []
    
    for i in range(num_windows):
        window = signal_data[i*window_size:(i+1)*window_size]
        
        # 时域特征
        feat = [
            np.mean(window),
            np.std(window),
            np.max(window),
            np.min(window),
            np.sqrt(np.mean(window**2)),  # RMS
            np.mean(np.abs(window)),
            np.max(np.abs(window)),
            np.std(window) / (np.mean(np.abs(window)) + 1e-8),  # 变异系数
        ]
        features.append(feat)
    
    # 补充特征到10维
    while len(features[0]) < 10:
        for i in range(len(features)):
            features[i].append(np.mean(np.abs(np.diff(features[i][:3]))))
    
    return np.array(features, dtype=np.float32)


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


def sampling_rate_experiment():
    """执行不同采样率下的性能实验"""
    print("=" * 60)
    print("实验41：不同采样率下的性能实验")
    print("=" * 60)
    
    # 生成高频原始信号
    print("\n[1] 生成高频铣削振动信号...")
    original_fs = 10000  # 10 kHz
    vibration, y, params = generate_high_freq_milling_data(num_samples=20000, original_fs=original_fs)
    print(f"  原始采样率: {original_fs} Hz")
    print(f"  信号长度: {len(vibration)}")
    
    # 测试不同采样率
    sampling_rates = [10000, 5000, 2000, 1000, 500, 200, 100]
    
    results = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'experiment': '不同采样率下的性能实验',
        'original_sampling_rate': original_fs,
        'tested_sampling_rates': sampling_rates,
        'results': {}
    }
    
    print("\n[2] 测试不同采样率下的模型性能...")
    
    for target_fs in sampling_rates:
        print(f"\n  采样率: {target_fs} Hz")
        
        # 降采样
        if target_fs < original_fs:
            vibration_ds, _ = downsample_signal(vibration, original_fs, target_fs)
            # 同步降采样目标值
            y_ds = scipy_signal.resample(y, len(vibration_ds))
        else:
            vibration_ds = vibration
            y_ds = y
        
        print(f"    降采样后信号长度: {len(vibration_ds)}")
        
        # 提取特征
        window_size = max(50, int(target_fs / 100))  # 窗口大小随采样率调整
        X = extract_features_from_signal(vibration_ds, window_size=window_size)
        
        # 对齐目标值
        num_samples = min(len(X), len(y_ds))
        X = X[:num_samples]
        y_aligned = y_ds[:num_samples]
        
        # 数据划分
        n = len(X)
        train_idx = int(n * 0.7)
        val_idx = int(n * 0.85)
        
        X_train = X[:train_idx]
        y_train = y_aligned[:train_idx]
        X_val = X[train_idx:val_idx]
        y_val = y_aligned[train_idx:val_idx]
        X_test = X[val_idx:]
        y_test = y_aligned[val_idx:]
        
        print(f"    特征矩阵形状: {X.shape}")
        print(f"    训练集: {len(X_train)}, 测试集: {len(X_test)}")
        
        # 训练和评估各模型
        fs_results = {}
        
        # DL-LNN
        model = DLLNNModel(input_dim=X.shape[1], hidden_dim=64, num_layers=3)
        metrics = train_and_evaluate(model, X_train, y_train, X_test, y_test, epochs=50)
        fs_results['DL-LNN'] = metrics
        print(f"    DL-LNN: MAE={metrics['mae']:.4f}, PCC={metrics['pcc']:.4f}")
        
        # LSTM
        model = LSTMModel(input_dim=X.shape[1], hidden_dim=64, num_layers=2)
        metrics = train_and_evaluate(model, X_train, y_train, X_test, y_test, epochs=50)
        fs_results['LSTM'] = metrics
        print(f"    LSTM: MAE={metrics['mae']:.4f}, PCC={metrics['pcc']:.4f}")
        
        # GRU
        model = GRUModel(input_dim=X.shape[1], hidden_dim=64, num_layers=2)
        metrics = train_and_evaluate(model, X_train, y_train, X_test, y_test, epochs=50)
        fs_results['GRU'] = metrics
        print(f"    GRU: MAE={metrics['mae']:.4f}, PCC={metrics['pcc']:.4f}")
        
        results['results'][str(target_fs)] = {
            'sampling_rate': target_fs,
            'signal_length': len(vibration_ds),
            'num_features': X.shape[1],
            'num_samples': n,
            'model_results': fs_results
        }
    
    # 分析采样率对性能的影响
    print("\n[3] 分析采样率对性能的影响...")
    
    performance_trend = {}
    for model_name in ['DL-LNN', 'LSTM', 'GRU']:
        mae_values = []
        pcc_values = []
        
        for fs in sampling_rates:
            fs_key = str(fs)
            if fs_key in results['results']:
                mae = results['results'][fs_key]['model_results'][model_name]['mae']
                pcc = results['results'][fs_key]['model_results'][model_name]['pcc']
                mae_values.append(mae)
                pcc_values.append(pcc)
        
        # 计算性能退化率
        if len(mae_values) >= 2:
            mae_degradation = (mae_values[-1] - mae_values[0]) / (mae_values[0] + 1e-8) * 100
            pcc_degradation = (pcc_values[0] - pcc_values[-1]) / (pcc_values[0] + 1e-8) * 100
        else:
            mae_degradation = 0
            pcc_degradation = 0
        
        performance_trend[model_name] = {
            'mae_values': mae_values,
            'pcc_values': pcc_values,
            'mae_degradation_percent': float(mae_degradation),
            'pcc_degradation_percent': float(pcc_degradation)
        }
        
        print(f"  {model_name}:")
        print(f"    MAE退化率: {mae_degradation:.2f}%")
        print(f"    PCC退化率: {pcc_degradation:.2f}%")
    
    results['performance_trend'] = performance_trend
    
    # 确定最优采样率
    print("\n[4] 确定最优采样率...")
    
    optimal_sampling_rate = {}
    for model_name in ['DL-LNN', 'LSTM', 'GRU']:
        best_fs = None
        best_score = -1
        
        for fs in sampling_rates:
            fs_key = str(fs)
            if fs_key in results['results']:
                pcc = results['results'][fs_key]['model_results'][model_name]['pcc']
                # 综合考虑性能和计算成本
                score = pcc * (1 - 0.1 * (10000 - fs) / 10000)
                
                if score > best_score:
                    best_score = score
                    best_fs = fs
        
        optimal_sampling_rate[model_name] = {
            'optimal_fs': best_fs,
            'score': float(best_score)
        }
        print(f"  {model_name} 推荐采样率: {best_fs} Hz")
    
    results['optimal_sampling_rate'] = optimal_sampling_rate
    
    # 保存结果
    output_path = os.path.join(os.path.dirname(__file__), 'results',
                              'sampling_rate_performance_results.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存至: {output_path}")
    print("=" * 60)
    
    return results


if __name__ == '__main__':
    sampling_rate_experiment()
