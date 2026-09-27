"""
实验40：误差分布分析实验
分析模型在不同预测值区间的误差分布，识别模型的优势和劣势区间
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


def generate_milling_data(num_samples=1000):
    """生成铣削数据"""
    np.random.seed(42)
    
    # 切削参数
    spindle_speed = np.random.uniform(3000, 12000, num_samples)
    feed_rate = np.random.uniform(0.05, 0.2, num_samples)
    depth_of_cut = np.random.uniform(0.5, 3.0, num_samples)
    
    # 振动特征
    vibration_amp = depth_of_cut * 0.3 + feed_rate * 2.0
    vibration_freq = spindle_speed / 60.0
    
    # 生成多维特征
    X = np.column_stack([
        spindle_speed / 12000,
        feed_rate / 0.2,
        depth_of_cut / 3.0,
        vibration_amp,
        vibration_freq / 200,
        np.sin(vibration_freq * 0.01),
        np.cos(vibration_freq * 0.01),
        vibration_amp * vibration_freq / 1000,
        depth_of_cut * feed_rate,
        spindle_speed * depth_of_cut / 10000
    ]).astype(np.float32)
    
    # 目标值（极限切削深度，包含非线性关系）
    y = (depth_of_cut * 0.4 + 
         feed_rate * 2.0 + 
         spindle_speed / 10000 * 0.3 +
         0.1 * np.sin(spindle_speed / 1000) +
         np.random.normal(0, 0.1, num_samples))
    y = y.astype(np.float32)
    
    return X, y


def train_model(model, X_train, y_train, epochs=80, lr=0.001):
    """训练模型"""
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
    
    return model


def predict(model, X):
    """预测"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model.eval()
    with torch.no_grad():
        X_tensor = torch.FloatTensor(X).to(device)
        pred = model(X_tensor).squeeze().cpu().numpy()
    return pred


def analyze_error_distribution(y_true, y_pred, model_name, num_bins=10):
    """分析误差分布"""
    errors = y_true - y_pred
    abs_errors = np.abs(errors)
    rel_errors = abs_errors / (np.abs(y_true) + 1e-8)
    
    # 按预测值区间分析
    y_min, y_max = y_true.min(), y_true.max()
    bin_edges = np.linspace(y_min, y_max, num_bins + 1)
    
    bin_analysis = []
    for i in range(num_bins):
        mask = (y_true >= bin_edges[i]) & (y_true < bin_edges[i+1])
        if np.sum(mask) > 0:
            bin_errors = errors[mask]
            bin_abs_errors = abs_errors[mask]
            bin_rel_errors = rel_errors[mask]
            
            bin_analysis.append({
                'bin_index': i,
                'range': [float(bin_edges[i]), float(bin_edges[i+1])],
                'num_samples': int(np.sum(mask)),
                'mean_error': float(np.mean(bin_errors)),
                'std_error': float(np.std(bin_errors)),
                'mae': float(np.mean(bin_abs_errors)),
                'rmse': float(np.sqrt(np.mean(bin_errors**2))),
                'mean_relative_error': float(np.mean(bin_rel_errors)),
                'max_error': float(np.max(bin_abs_errors)),
                'median_error': float(np.median(bin_abs_errors))
            })
    
    # 误差统计
    error_stats = {
        'mean_error': float(np.mean(errors)),
        'std_error': float(np.std(errors)),
        'mae': float(np.mean(abs_errors)),
        'rmse': float(np.sqrt(np.mean(errors**2))),
        'median_error': float(np.median(abs_errors)),
        'max_error': float(np.max(abs_errors)),
        'min_error': float(np.min(abs_errors)),
        'mean_relative_error': float(np.mean(rel_errors)),
        'skewness': float((np.mean((errors - np.mean(errors))**3) / (np.std(errors)**3 + 1e-8))),
        'kurtosis': float((np.mean((errors - np.mean(errors))**4) / (np.std(errors)**4 + 1e-8)))
    }
    
    # 误差分位数
    quantiles = [0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]
    error_quantiles = {f'q{int(q*100)}': float(np.quantile(abs_errors, q)) for q in quantiles}
    
    return {
        'bin_analysis': bin_analysis,
        'error_stats': error_stats,
        'error_quantiles': error_quantiles,
        'predictions': y_pred.tolist(),
        'true_values': y_true.tolist(),
        'errors': errors.tolist()
    }


def error_distribution_experiment():
    """执行误差分布分析实验"""
    print("=" * 60)
    print("实验40：误差分布分析实验")
    print("=" * 60)
    
    # 生成数据
    print("\n[1] 生成铣削数据...")
    X, y = generate_milling_data(num_samples=1000)
    print(f"  数据形状: X={X.shape}, y={y.shape}")
    print(f"  目标值范围: [{y.min():.3f}, {y.max():.3f}]")
    
    # 数据划分
    n = len(X)
    train_idx = int(n * 0.7)
    val_idx = int(n * 0.85)
    
    X_train, y_train = X[:train_idx], y[:train_idx]
    X_val, y_val = X[train_idx:val_idx], y[train_idx:val_idx]
    X_test, y_test = X[val_idx:], y[val_idx:]
    
    print(f"  训练集: {len(X_train)}, 验证集: {len(X_val)}, 测试集: {len(X_test)}")
    
    results = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'experiment': '误差分布分析实验',
        'data_info': {
            'total_samples': n,
            'train_samples': len(X_train),
            'val_samples': len(X_val),
            'test_samples': len(X_test),
            'feature_dim': X.shape[1],
            'target_range': [float(y.min()), float(y.max())]
        },
        'results': {}
    }
    
    # 训练和评估各模型
    models = {
        'DL-LNN': lambda: DLLNNModel(input_dim=X.shape[1], hidden_dim=64, num_layers=3),
        'LSTM': lambda: LSTMModel(input_dim=X.shape[1], hidden_dim=64, num_layers=2),
        'GRU': lambda: GRUModel(input_dim=X.shape[1], hidden_dim=64, num_layers=2)
    }
    
    print("\n[2] 训练模型并分析误差分布...")
    
    for model_name, model_fn in models.items():
        print(f"\n  训练 {model_name}...")
        
        # 训练模型
        model = model_fn()
        model = train_model(model, X_train, y_train, epochs=80)
        
        # 预测
        y_pred = predict(model, X_test)
        
        # 分析误差分布
        print(f"  分析 {model_name} 误差分布...")
        error_analysis = analyze_error_distribution(y_test, y_pred, model_name, num_bins=10)
        
        results['results'][model_name] = error_analysis
        
        print(f"    MAE: {error_analysis['error_stats']['mae']:.4f}")
        print(f"    RMSE: {error_analysis['error_stats']['rmse']:.4f}")
        print(f"    误差中位数: {error_analysis['error_stats']['median_error']:.4f}")
        print(f"    最大误差: {error_analysis['error_stats']['max_error']:.4f}")
    
    # 分析各模型在不同区间的表现差异
    print("\n[3] 分析各模型在不同预测值区间的表现差异...")
    
    interval_comparison = []
    num_bins = 10
    
    for bin_idx in range(num_bins):
        bin_data = {'bin_index': bin_idx}
        
        for model_name in results['results']:
            bin_analysis = results['results'][model_name]['bin_analysis']
            if bin_idx < len(bin_analysis):
                bin_info = bin_analysis[bin_idx]
                bin_data[f'{model_name}_mae'] = bin_info['mae']
                bin_data[f'{model_name}_rmse'] = bin_info['rmse']
                bin_data['range'] = bin_info['range']
                bin_data['num_samples'] = bin_info['num_samples']
        
        interval_comparison.append(bin_data)
    
    results['interval_comparison'] = interval_comparison
    
    # 识别各模型的优势和劣势区间
    print("\n[4] 识别各模型的优势和劣势区间...")
    
    model_strengths = {}
    for model_name in results['results']:
        mae_values = []
        for bin_data in interval_comparison:
            if f'{model_name}_mae' in bin_data:
                mae_values.append((bin_data['bin_index'], bin_data[f'{model_name}_mae']))
        
        if mae_values:
            mae_values.sort(key=lambda x: x[1])
            best_bins = [v[0] for v in mae_values[:3]]  # 前3个最好的区间
            worst_bins = [v[0] for v in mae_values[-3:]]  # 后3个最差的区间
            
            model_strengths[model_name] = {
                'best_intervals': best_bins,
                'worst_intervals': worst_bins,
                'best_mae': mae_values[0][1],
                'worst_mae': mae_values[-1][1]
            }
            
            print(f"  {model_name}:")
            print(f"    最优区间: {best_bins}, MAE={mae_values[0][1]:.4f}")
            print(f"    最差区间: {worst_bins}, MAE={mae_values[-1][1]:.4f}")
    
    results['model_strengths'] = model_strengths
    
    # 保存结果
    output_path = os.path.join(os.path.dirname(__file__), 'results',
                              'error_distribution_results.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存至: {output_path}")
    print("=" * 60)
    
    return results


if __name__ == '__main__':
    error_distribution_experiment()
