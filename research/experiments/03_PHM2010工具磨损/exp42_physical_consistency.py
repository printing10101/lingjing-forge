"""
实验42：物理一致性验证实验
验证模型预测结果是否满足物理约束条件（如能量守恒、频率响应特性等）
"""

import numpy as np
import torch
import torch.nn as nn
from datetime import datetime
import json
import os

RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')


def generate_physics_constrained_data(num_samples=1000, seq_len=20):
    """生成具有明确物理约束的铣削数据"""
    np.random.seed(42)
    
    # 切削参数
    spindle_speed = np.random.uniform(5000, 15000, num_samples)  # rpm
    feed_rate = np.random.uniform(0.05, 0.2, num_samples)  # mm/tooth
    axial_depth = np.random.uniform(0.5, 5.0, num_samples)  # mm
    
    # 物理特征
    tooth_freq = spindle_speed / 60.0 * 4  # 4齿铣刀，单位Hz
    sampling_rate = 1000  # Hz
    
    X = np.zeros((num_samples, seq_len, 6), dtype=np.float32)
    y = np.zeros(num_samples, dtype=np.float32)
    
    for i in range(num_samples):
        t = np.arange(seq_len) / sampling_rate
        
        # 切削力分量（低频）
        cutting_force = axial_depth[i] * feed_rate[i] * 100 * np.sin(2 * np.pi * tooth_freq[i] * t)
        
        # 颤振分量（中频，与稳定性相关）
        chatter_freq = tooth_freq[i] * 2.5  # 颤振频率约为齿频的2.5倍
        chatter_amp = axial_depth[i] * 0.3
        chatter = chatter_amp * np.sin(2 * np.pi * chatter_freq * t)
        
        # 高频噪声
        noise = 0.1 * np.random.randn(seq_len)
        
        # 合成信号
        signal = cutting_force + chatter + noise
        
        # 多通道特征
        X[i, :, 0] = signal  # 振动信号
        X[i, :, 1] = cutting_force  # 切削力
        X[i, :, 2] = np.gradient(signal)  # 速度
        X[i, :, 3] = np.gradient(np.gradient(signal))  # 加速度
        X[i, :, 4] = signal ** 2  # 能量
        X[i, :, 5] = np.abs(signal)  # 包络
        
        # 极限切削深度（与物理参数相关）
        stability_limit = axial_depth[i] * (1 - 0.3 * chatter_amp / axial_depth[i])
        y[i] = stability_limit
    
    return X, y, {
        'spindle_speed': spindle_speed,
        'feed_rate': feed_rate,
        'axial_depth': axial_depth,
        'tooth_freq': tooth_freq,
        'chatter_freq': tooth_freq * 2.5
    }


class DLLNNModel(nn.Module):
    """DL-LNN模型简化版"""
    def __init__(self, input_dim=6, hidden_dim=64, num_layers=3, output_dim=1, dt=0.01):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.dt = dt
        
        self.ltc_layers = nn.ModuleList()
        for i in range(num_layers):
            in_dim = input_dim if i == 0 else hidden_dim
            self.ltc_layers.append(nn.LSTMCell(in_dim, hidden_dim))
        
        self.fc = nn.Linear(hidden_dim, output_dim)
    
    def forward(self, x):
        batch_size, seq_len, _ = x.shape
        
        h = [torch.zeros(batch_size, self.hidden_dim, device=x.device) for _ in range(self.num_layers)]
        c = [torch.zeros(batch_size, self.hidden_dim, device=x.device) for _ in range(self.num_layers)]
        
        for t in range(seq_len):
            x_t = x[:, t, :]
            for layer_idx, ltc_cell in enumerate(self.ltc_layers):
                if layer_idx == 0:
                    h[layer_idx], c[layer_idx] = ltc_cell(x_t, (h[layer_idx], c[layer_idx]))
                else:
                    h[layer_idx], c[layer_idx] = ltc_cell(h[layer_idx - 1], (h[layer_idx], c[layer_idx]))
        
        out = self.fc(h[-1])
        return out.squeeze(-1)


def train_model(X_train, y_train, X_test, y_test, epochs=50, lr=0.001):
    """训练模型"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    
    X_train_t = torch.FloatTensor(X_train).to(device)
    y_train_t = torch.FloatTensor(y_train).to(device)
    X_test_t = torch.FloatTensor(X_test).to(device)
    y_test_t = torch.FloatTensor(y_test).to(device)
    
    model = DLLNNModel(input_dim=X_train.shape[2]).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()
    
    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        pred = model(X_train_t)
        loss = criterion(pred, y_train_t)
        loss.backward()
        optimizer.step()
    
    model.eval()
    with torch.no_grad():
        y_pred = model(X_test_t).cpu().numpy()
    
    return model, y_pred


def check_energy_consistency(signal, prediction, window_size=5):
    """
    检查能量一致性：预测的稳定性极限应与信号能量正相关
    物理原理：切削深度越大，振动能量越大，稳定性极限应越低
    """
    # 计算信号能量（RMS）
    energy = np.sqrt(np.mean(signal ** 2, axis=1))
    
    # 按能量排序
    sorted_indices = np.argsort(energy)
    sorted_predictions = prediction[sorted_indices]
    
    # 计算能量-预测相关性
    correlation = np.corrcoef(energy, prediction)[0, 1]
    
    # 分窗口统计
    num_windows = len(energy) // window_size
    window_stats = []
    
    for i in range(num_windows):
        start_idx = i * window_size
        end_idx = start_idx + window_size
        window_energy = energy[start_idx:end_idx]
        window_pred = prediction[start_idx:end_idx]
        
        window_stats.append({
            'window_index': i,
            'mean_energy': float(np.mean(window_energy)),
            'std_energy': float(np.std(window_energy)),
            'mean_prediction': float(np.mean(window_pred)),
            'std_prediction': float(np.std(window_pred))
        })
    
    return {
        'energy_prediction_correlation': float(correlation),
        'window_stats': window_stats,
        'energy_range': [float(energy.min()), float(energy.max())],
        'prediction_range': [float(prediction.min()), float(prediction.max())]
    }


def check_frequency_response(X_test, y_pred, sampling_rate=1000):
    """
    检查频率响应特性：模型预测应保留信号的频率特征
    """
    # 对每个样本进行FFT
    freq_responses = []
    
    for i in range(min(100, len(X_test))):  # 取前100个样本
        signal = X_test[i, :, 0]  # 振动信号通道
        
        # FFT
        fft_signal = np.fft.fft(signal)
        freqs = np.fft.fftfreq(len(signal), 1.0 / sampling_rate)
        
        # 主频率
        magnitude = np.abs(fft_signal)
        positive_mask = freqs > 0
        main_freq_idx = np.argmax(magnitude[positive_mask])
        main_freq = freqs[positive_mask][main_freq_idx]
        
        # 频谱熵
        power_spectrum = magnitude ** 2
        power_spectrum = power_spectrum / (power_spectrum.sum() + 1e-8)
        spectral_entropy = -np.sum(power_spectrum * np.log(power_spectrum + 1e-8))
        
        freq_responses.append({
            'sample_index': i,
            'main_frequency': float(main_freq),
            'spectral_entropy': float(spectral_entropy),
            'prediction': float(y_pred[i])
        })
    
    # 统计主频率分布
    main_freqs = [fr['main_frequency'] for fr in freq_responses]
    freq_hist, freq_bins = np.histogram(main_freqs, bins=20)
    
    return {
        'frequency_responses': freq_responses,
        'main_frequency_distribution': {
            'hist': freq_hist.tolist(),
            'bins': freq_bins.tolist()
        },
        'mean_main_frequency': float(np.mean(main_freqs)),
        'std_main_frequency': float(np.std(main_freqs))
    }


def check_monotonicity(X_test, y_pred, feature_idx=0):
    """
    检查单调性：某些物理特征与预测值应保持单调关系
    例如：切削深度增加，稳定性极限应降低
    """
    feature = X_test[:, 0, feature_idx]  # 取第一个时间步的特征
    
    # 按特征值排序
    sorted_indices = np.argsort(feature)
    sorted_feature = feature[sorted_indices]
    sorted_pred = y_pred[sorted_indices]
    
    # 计算单调性指标
    # 1. 相邻样本的单调性
    monotonic_count = 0
    total_count = len(sorted_pred) - 1
    
    for i in range(total_count):
        if sorted_pred[i + 1] <= sorted_pred[i]:
            monotonic_count += 1
    
    monotonicity_ratio = monotonic_count / total_count if total_count > 0 else 0
    
    # 2. Spearman秩相关系数
    from scipy import stats
    spearman_corr, spearman_p = stats.spearmanr(feature, y_pred)
    
    # 3. 分段单调性
    num_segments = 10
    segment_size = len(sorted_feature) // num_segments
    segment_monotonicity = []
    
    for i in range(num_segments):
        start = i * segment_size
        end = start + segment_size if i < num_segments - 1 else len(sorted_feature)
        seg_feature = sorted_feature[start:end]
        seg_pred = sorted_pred[start:end]
        
        if len(seg_feature) > 1:
            seg_corr, _ = stats.spearmanr(seg_feature, seg_pred)
            segment_monotonicity.append({
                'segment': i,
                'feature_range': [float(seg_feature.min()), float(seg_feature.max())],
                'spearman_corr': float(seg_corr)
            })
    
    return {
        'overall_monotonicity_ratio': float(monotonicity_ratio),
        'spearman_correlation': float(spearman_corr),
        'spearman_p_value': float(spearman_p),
        'segment_monotonicity': segment_monotonicity
    }


def check_physical_bounds(y_pred, y_true, physics_params):
    """
    检查物理边界：预测值应在物理合理范围内
    """
    # 1. 预测值范围检查
    pred_min, pred_max = y_pred.min(), y_pred.max()
    true_min, true_max = y_true.min(), y_true.max()
    
    # 2. 负值检查（切削深度不应为负）
    negative_predictions = np.sum(y_pred < 0)
    negative_ratio = negative_predictions / len(y_pred)
    
    # 3. 超出物理范围检查
    axial_depth = physics_params['axial_depth']
    max_possible_depth = axial_depth.max()
    
    exceed_upper = np.sum(y_pred > max_possible_depth)
    exceed_ratio = exceed_upper / len(y_pred)
    
    # 4. 与物理参数的关系
    # 稳定性极限应小于实际切削深度
    violation_count = np.sum(y_pred > axial_depth[:len(y_pred)])
    violation_ratio = violation_count / len(y_pred)
    
    return {
        'prediction_range': [float(pred_min), float(pred_max)],
        'true_range': [float(true_min), float(true_max)],
        'negative_predictions': int(negative_predictions),
        'negative_ratio': float(negative_ratio),
        'exceed_upper_bound': int(exceed_upper),
        'exceed_ratio': float(exceed_ratio),
        'physical_violations': int(violation_count),
        'violation_ratio': float(violation_ratio),
        'max_possible_depth': float(max_possible_depth)
    }


def physical_consistency_experiment():
    """执行物理一致性验证实验"""
    print("=" * 60)
    print("实验42：物理一致性验证实验")
    print("=" * 60)
    
    # 生成数据
    print("\n[1] 生成物理约束数据...")
    X, y, physics_params = generate_physics_constrained_data(num_samples=2000, seq_len=20)
    print(f"  数据形状: X={X.shape}, y={y.shape}")
    
    # 划分数据集
    split_idx = int(0.8 * len(X))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]
    
    print(f"  训练集: {X_train.shape[0]} 样本")
    print(f"  测试集: {X_test.shape[0]} 样本")
    
    # 训练模型
    print("\n[2] 训练DL-LNN模型...")
    model, y_pred = train_model(X_train, y_train, X_test, y_test, epochs=50)
    
    # 计算基础指标
    mae = np.mean(np.abs(y_test - y_pred))
    rmse = np.sqrt(np.mean((y_test - y_pred) ** 2))
    r2 = 1 - np.sum((y_test - y_pred) ** 2) / (np.sum((y_test - y_test.mean()) ** 2) + 1e-8)
    pcc = np.corrcoef(y_test, y_pred)[0, 1]
    
    print(f"  基础指标: MAE={mae:.4f}, RMSE={rmse:.4f}, R²={r2:.4f}, PCC={pcc:.4f}")
    
    # 物理一致性检查
    print("\n[3] 执行物理一致性检查...")
    
    # 3.1 能量一致性
    print("  [3.1] 能量一致性检查...")
    energy_consistency = check_energy_consistency(X_test[:, :, 0], y_pred)
    print(f"    能量-预测相关性: {energy_consistency['energy_prediction_correlation']:.4f}")
    
    # 3.2 频率响应
    print("  [3.2] 频率响应检查...")
    frequency_response = check_frequency_response(X_test, y_pred)
    print(f"    主频率均值: {frequency_response['mean_main_frequency']:.2f} Hz")
    print(f"    主频率标准差: {frequency_response['std_main_frequency']:.2f} Hz")
    
    # 3.3 单调性检查
    print("  [3.3] 单调性检查...")
    monotonicity = check_monotonicity(X_test, y_pred, feature_idx=0)
    print(f"    整体单调性比率: {monotonicity['overall_monotonicity_ratio']:.4f}")
    print(f"    Spearman相关系数: {monotonicity['spearman_correlation']:.4f}")
    
    # 3.4 物理边界检查
    print("  [3.4] 物理边界检查...")
    physical_bounds = check_physical_bounds(y_pred, y_test, physics_params)
    print(f"    负值预测数: {physical_bounds['negative_predictions']}")
    print(f"    物理违规数: {physical_bounds['physical_violations']}")
    
    # 保存结果
    results = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'experiment': '物理一致性验证实验',
        'num_samples': len(X),
        'sequence_length': 20,
        'basic_metrics': {
            'mae': float(mae),
            'rmse': float(rmse),
            'r2': float(r2),
            'pcc': float(pcc)
        },
        'energy_consistency': energy_consistency,
        'frequency_response': frequency_response,
        'monotonicity': monotonicity,
        'physical_bounds': physical_bounds
    }
    
    output_file = os.path.join(RESULTS_DIR, 'physical_consistency_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n[4] 实验结果已保存至: {output_file}")
    print("=" * 60)
    
    return results


if __name__ == '__main__':
    physical_consistency_experiment()
