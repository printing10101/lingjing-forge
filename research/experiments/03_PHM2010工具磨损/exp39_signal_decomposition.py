"""
实验39：信号分量解耦分析实验
分析振动信号中的不同频率分量，评估模型对不同频率分量的敏感度
"""

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from scipy import signal as scipy_signal
from scipy.fft import fft, ifft
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


def generate_milling_signal(num_samples=500, sampling_rate=1000):
    """
    生成包含多个分量的铣削振动信号
    包含：
    1. 低频切削力分量 (0-50 Hz)
    2. 中频颤振分量 (50-200 Hz)
    3. 高频噪声分量 (200-500 Hz)
    """
    np.random.seed(42)
    t = np.linspace(0, 10, num_samples)
    
    # 切削参数
    spindle_speed = np.random.uniform(3000, 12000, num_samples)
    feed_rate = np.random.uniform(0.05, 0.2, num_samples)
    depth_of_cut = np.random.uniform(0.5, 3.0, num_samples)
    
    # 1. 低频切削力分量 (与主轴转速相关)
    spindle_freq = spindle_speed / 60.0  # Hz
    cutting_force = np.zeros_like(t)
    for i in range(len(t)):
        # 基频及其谐波
        cutting_force[i] = (np.sin(2 * np.pi * spindle_freq[i] * t[i]) +
                           0.5 * np.sin(2 * np.pi * 2 * spindle_freq[i] * t[i]) +
                           0.3 * np.sin(2 * np.pi * 3 * spindle_freq[i] * t[i]))
    
    # 2. 中频颤振分量 (与系统固有频率相关)
    natural_freq = 120  # Hz, 系统固有频率
    damping_ratio = 0.05
    chatter_freq = natural_freq * np.sqrt(1 - 2 * damping_ratio**2)
    
    # 颤振幅值与切深相关
    chatter_amplitude = depth_of_cut / 3.0
    chatter_signal = np.zeros_like(t)
    for i in range(len(t)):
        # 颤振信号 (调制信号)
        modulation = 1 + 0.3 * np.sin(2 * np.pi * spindle_freq[i] * t[i])
        chatter_signal[i] = (chatter_amplitude[i] * modulation *
                            np.sin(2 * np.pi * chatter_freq * t[i]) *
                            np.exp(-damping_ratio * t[i]))
    
    # 3. 高频噪声分量
    noise = np.random.normal(0, 0.1, len(t))
    
    # 合成信号
    signal = cutting_force + chatter_signal + noise
    
    # 生成目标值 (极限切削深度)
    y = (depth_of_cut * 0.4 + 
         feed_rate * 2.0 + 
         spindle_speed / 10000 * 0.3 +
         np.random.normal(0, 0.1, num_samples))
    
    return signal, y, {
        'cutting_force': cutting_force,
        'chatter_signal': chatter_signal,
        'noise': noise,
        'spindle_speed': spindle_speed,
        'feed_rate': feed_rate,
        'depth_of_cut': depth_of_cut
    }


def bandpass_filter(signal_data, lowcut, highcut, fs, order=5):
    """带通滤波器"""
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = scipy_signal.butter(order, [low, high], btype='band')
    filtered = scipy_signal.filtfilt(b, a, signal_data)
    return filtered


def decompose_signal(signal_data, sampling_rate=1000):
    """
    信号分量解耦
    分离出：
    1. 低频切削力分量 (0-50 Hz)
    2. 中频颤振分量 (50-200 Hz)
    3. 高频噪声分量 (200-500 Hz)
    """
    # 低频切削力分量
    cutting_force = bandpass_filter(signal_data, 0, 50, sampling_rate, order=4)
    
    # 中频颤振分量
    chatter = bandpass_filter(signal_data, 50, 200, sampling_rate, order=4)
    
    # 高频噪声分量
    noise = bandpass_filter(signal_data, 200, 500, sampling_rate, order=4)
    
    return cutting_force, chatter, noise


def extract_features(signal_data, sampling_rate=1000):
    """提取信号特征"""
    features = []
    
    # 时域特征
    features.append(np.mean(signal_data))
    features.append(np.std(signal_data))
    features.append(np.max(signal_data))
    features.append(np.min(signal_data))
    features.append(np.sqrt(np.mean(signal_data**2)))  # RMS
    features.append(np.mean(np.abs(signal_data)))
    
    # 频域特征
    fft_vals = fft(signal_data)
    freqs = np.fft.fftfreq(len(signal_data), 1/sampling_rate)
    
    # 主频率
    positive_freq_idx = freqs > 0
    magnitude = np.abs(fft_vals[positive_freq_idx])
    dominant_freq = freqs[positive_freq_idx][np.argmax(magnitude)]
    features.append(dominant_freq)
    
    # 频谱熵
    power_spectrum = magnitude**2
    power_spectrum = power_spectrum / (np.sum(power_spectrum) + 1e-8)
    spectral_entropy = -np.sum(power_spectrum * np.log(power_spectrum + 1e-8))
    features.append(spectral_entropy)
    
    # 频谱质心
    spectral_centroid = np.sum(freqs[positive_freq_idx] * magnitude) / (np.sum(magnitude) + 1e-8)
    features.append(spectral_centroid)
    
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


def signal_decomposition_experiment():
    """执行信号分量解耦分析实验"""
    print("=" * 60)
    print("实验39：信号分量解耦分析实验")
    print("=" * 60)
    
    # 生成原始信号
    print("\n[1] 生成铣削振动信号...")
    raw_signal, y, components = generate_milling_signal(num_samples=500, sampling_rate=1000)
    print(f"  原始信号长度: {len(raw_signal)}")
    
    # 信号解耦
    print("\n[2] 信号分量解耦...")
    cutting_force, chatter, noise = decompose_signal(raw_signal, sampling_rate=1000)
    print(f"  切削力分量范围: [{cutting_force.min():.3f}, {cutting_force.max():.3f}]")
    print(f"  颤振分量范围: [{chatter.min():.3f}, {chatter.max():.3f}]")
    print(f"  噪声分量范围: [{noise.min():.3f}, {noise.max():.3f}]")
    
    # 特征提取
    print("\n[3] 特征提取...")
    # 原始信号特征
    X_original = []
    window_size = 50
    for i in range(len(raw_signal) - window_size + 1):
        window = raw_signal[i:i+window_size]
        features = extract_features(window, sampling_rate=1000)
        X_original.append(features)
    
    X_original = np.array(X_original, dtype=np.float32)
    y_aligned = y[window_size-1:]
    
    # 各分量特征
    X_cutting_force = []
    X_chatter = []
    X_noise = []
    
    for i in range(len(raw_signal) - window_size + 1):
        cf_window = cutting_force[i:i+window_size]
        ch_window = chatter[i:i+window_size]
        n_window = noise[i:i+window_size]
        
        X_cutting_force.append(extract_features(cf_window, sampling_rate=1000))
        X_chatter.append(extract_features(ch_window, sampling_rate=1000))
        X_noise.append(extract_features(n_window, sampling_rate=1000))
    
    X_cutting_force = np.array(X_cutting_force, dtype=np.float32)
    X_chatter = np.array(X_chatter, dtype=np.float32)
    X_noise = np.array(X_noise, dtype=np.float32)
    
    print(f"  特征矩阵形状: {X_original.shape}")
    
    # 数据划分
    n = len(X_original)
    train_idx = int(n * 0.7)
    val_idx = int(n * 0.85)
    
    X_train_orig = X_original[:train_idx]
    y_train = y_aligned[:train_idx]
    X_val_orig = X_original[train_idx:val_idx]
    y_val = y_aligned[train_idx:val_idx]
    X_test_orig = X_original[val_idx:]
    y_test = y_aligned[val_idx:]
    
    # 各分量的训练/测试集
    X_train_cf = X_cutting_force[:train_idx]
    X_train_ch = X_chatter[:train_idx]
    X_train_n = X_noise[:train_idx]
    
    X_test_cf = X_cutting_force[val_idx:]
    X_test_ch = X_chatter[val_idx:]
    X_test_n = X_noise[val_idx:]
    
    results = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'experiment': '信号分量解耦分析实验',
        'signal_info': {
            'sampling_rate': 1000,
            'window_size': window_size,
            'num_samples': n,
            'feature_dim': X_original.shape[1]
        },
        'signal_components': {
            'cutting_force': {
                'frequency_range': '0-50 Hz',
                'amplitude_range': [float(cutting_force.min()), float(cutting_force.max())],
                'rms': float(np.sqrt(np.mean(cutting_force**2)))
            },
            'chatter': {
                'frequency_range': '50-200 Hz',
                'amplitude_range': [float(chatter.min()), float(chatter.max())],
                'rms': float(np.sqrt(np.mean(chatter**2)))
            },
            'noise': {
                'frequency_range': '200-500 Hz',
                'amplitude_range': [float(noise.min()), float(noise.max())],
                'rms': float(np.sqrt(np.mean(noise**2)))
            }
        },
        'results': {
            'original_signal': {},
            'cutting_force_only': {},
            'chatter_only': {},
            'noise_only': {},
            'combined_analysis': {}
        }
    }
    
    # 实验1：原始信号预测
    print("\n[实验1] 原始信号预测...")
    original_results = {}
    
    # DL-LNN
    model = DLLNNModel(input_dim=X_original.shape[1], hidden_dim=64, num_layers=3)
    metrics = train_and_evaluate(model, X_train_orig, y_train, X_test_orig, y_test)
    original_results['DL-LNN'] = metrics
    print(f"  DL-LNN: MAE={metrics['mae']:.4f}, PCC={metrics['pcc']:.4f}")
    
    # LSTM
    model = LSTMModel(input_dim=X_original.shape[1], hidden_dim=64, num_layers=2)
    metrics = train_and_evaluate(model, X_train_orig, y_train, X_test_orig, y_test)
    original_results['LSTM'] = metrics
    print(f"  LSTM: MAE={metrics['mae']:.4f}, PCC={metrics['pcc']:.4f}")
    
    # GRU
    model = GRUModel(input_dim=X_original.shape[1], hidden_dim=64, num_layers=2)
    metrics = train_and_evaluate(model, X_train_orig, y_train, X_test_orig, y_test)
    original_results['GRU'] = metrics
    print(f"  GRU: MAE={metrics['mae']:.4f}, PCC={metrics['pcc']:.4f}")
    
    results['results']['original_signal'] = original_results
    
    # 实验2：单一分量预测
    print("\n[实验2] 单一分量预测...")
    
    # 切削力分量
    cf_results = {}
    model = DLLNNModel(input_dim=X_cutting_force.shape[1], hidden_dim=64, num_layers=3)
    metrics = train_and_evaluate(model, X_train_cf, y_train, X_test_cf, y_test)
    cf_results['DL-LNN'] = metrics
    print(f"  切削力 - DL-LNN: MAE={metrics['mae']:.4f}")
    
    model = LSTMModel(input_dim=X_cutting_force.shape[1], hidden_dim=64, num_layers=2)
    metrics = train_and_evaluate(model, X_train_cf, y_train, X_test_cf, y_test)
    cf_results['LSTM'] = metrics
    
    model = GRUModel(input_dim=X_cutting_force.shape[1], hidden_dim=64, num_layers=2)
    metrics = train_and_evaluate(model, X_train_cf, y_train, X_test_cf, y_test)
    cf_results['GRU'] = metrics
    
    results['results']['cutting_force_only'] = cf_results
    
    # 颤振分量
    ch_results = {}
    model = DLLNNModel(input_dim=X_chatter.shape[1], hidden_dim=64, num_layers=3)
    metrics = train_and_evaluate(model, X_train_ch, y_train, X_test_ch, y_test)
    ch_results['DL-LNN'] = metrics
    print(f"  颤振 - DL-LNN: MAE={metrics['mae']:.4f}")
    
    model = LSTMModel(input_dim=X_chatter.shape[1], hidden_dim=64, num_layers=2)
    metrics = train_and_evaluate(model, X_train_ch, y_train, X_test_ch, y_test)
    ch_results['LSTM'] = metrics
    
    model = GRUModel(input_dim=X_chatter.shape[1], hidden_dim=64, num_layers=2)
    metrics = train_and_evaluate(model, X_train_ch, y_train, X_test_ch, y_test)
    ch_results['GRU'] = metrics
    
    results['results']['chatter_only'] = ch_results
    
    # 噪声分量
    n_results = {}
    model = DLLNNModel(input_dim=X_noise.shape[1], hidden_dim=64, num_layers=3)
    metrics = train_and_evaluate(model, X_train_n, y_train, X_test_n, y_test)
    n_results['DL-LNN'] = metrics
    print(f"  噪声 - DL-LNN: MAE={metrics['mae']:.4f}")
    
    model = LSTMModel(input_dim=X_noise.shape[1], hidden_dim=64, num_layers=2)
    metrics = train_and_evaluate(model, X_train_n, y_train, X_test_n, y_test)
    n_results['LSTM'] = metrics
    
    model = GRUModel(input_dim=X_noise.shape[1], hidden_dim=64, num_layers=2)
    metrics = train_and_evaluate(model, X_train_n, y_train, X_test_n, y_test)
    n_results['GRU'] = metrics
    
    results['results']['noise_only'] = n_results
    
    # 实验3：分量组合分析
    print("\n[实验3] 分量组合分析...")
    # 切削力 + 颤振
    X_cf_ch = np.concatenate([X_cutting_force, X_chatter], axis=1)
    X_train_cf_ch = X_cf_ch[:train_idx]
    X_test_cf_ch = X_cf_ch[val_idx:]
    
    combined_results = {}
    model = DLLNNModel(input_dim=X_cf_ch.shape[1], hidden_dim=64, num_layers=3)
    metrics = train_and_evaluate(model, X_train_cf_ch, y_train, X_test_cf_ch, y_test)
    combined_results['cutting_force+chatter'] = {'DL-LNN': metrics}
    print(f"  切削力+颤振 - DL-LNN: MAE={metrics['mae']:.4f}")
    
    # 切削力 + 噪声
    X_cf_n = np.concatenate([X_cutting_force, X_noise], axis=1)
    X_train_cf_n = X_cf_n[:train_idx]
    X_test_cf_n = X_cf_n[val_idx:]
    
    model = DLLNNModel(input_dim=X_cf_n.shape[1], hidden_dim=64, num_layers=3)
    metrics = train_and_evaluate(model, X_train_cf_n, y_train, X_test_cf_n, y_test)
    combined_results['cutting_force+noise'] = {'DL-LNN': metrics}
    print(f"  切削力+噪声 - DL-LNN: MAE={metrics['mae']:.4f}")
    
    # 颤振 + 噪声
    X_ch_n = np.concatenate([X_chatter, X_noise], axis=1)
    X_train_ch_n = X_ch_n[:train_idx]
    X_test_ch_n = X_ch_n[val_idx:]
    
    model = DLLNNModel(input_dim=X_ch_n.shape[1], hidden_dim=64, num_layers=3)
    metrics = train_and_evaluate(model, X_train_ch_n, y_train, X_test_ch_n, y_test)
    combined_results['chatter+noise'] = {'DL-LNN': metrics}
    print(f"  颤振+噪声 - DL-LNN: MAE={metrics['mae']:.4f}")
    
    results['results']['combined_analysis'] = combined_results
    
    # 计算各分量的贡献度
    print("\n[4] 计算分量贡献度...")
    baseline_mae = original_results['DL-LNN']['mae']
    
    contribution_analysis = {
        'cutting_force_contribution': (baseline_mae - cf_results['DL-LNN']['mae']) / baseline_mae * 100,
        'chatter_contribution': (baseline_mae - ch_results['DL-LNN']['mae']) / baseline_mae * 100,
        'noise_contribution': (baseline_mae - n_results['DL-LNN']['mae']) / baseline_mae * 100,
        'synergy_cf_ch': (baseline_mae - combined_results['cutting_force+chatter']['DL-LNN']['mae']) / baseline_mae * 100
    }
    
    results['contribution_analysis'] = contribution_analysis
    
    print(f"  切削力贡献度: {contribution_analysis['cutting_force_contribution']:.2f}%")
    print(f"  颤振贡献度: {contribution_analysis['chatter_contribution']:.2f}%")
    print(f"  噪声贡献度: {contribution_analysis['noise_contribution']:.2f}%")
    print(f"  切削力+颤振协同效应: {contribution_analysis['synergy_cf_ch']:.2f}%")
    
    # 保存结果
    output_path = os.path.join(os.path.dirname(__file__), 'results',
                              'signal_decomposition_results.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存至: {output_path}")
    print("=" * 60)
    
    return results


if __name__ == '__main__':
    signal_decomposition_experiment()
