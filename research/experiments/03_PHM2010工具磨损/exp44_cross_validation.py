"""
实验44：交叉验证实验
使用K折交叉验证评估模型的稳定性和泛化能力
"""

import numpy as np
import torch
import torch.nn as nn
from datetime import datetime
import json
import os
from sklearn.model_selection import KFold

RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')


def generate_milling_data(num_samples=1000, seq_len=20, input_dim=6):
    """生成铣削振动数据"""
    np.random.seed(42)
    
    X = np.random.randn(num_samples, seq_len, input_dim).astype(np.float32)
    
    # 生成与输入相关的目标值
    y = np.zeros(num_samples, dtype=np.float32)
    for i in range(num_samples):
        # 基于输入特征的加权和加上非线性变换
        y[i] = (
            0.5 * np.mean(X[i, :, 0]) +
            0.3 * np.std(X[i, :, 1]) +
            0.2 * np.max(X[i, :, 2]) -
            0.1 * np.min(X[i, :, 3]) +
            0.15 * np.sum(X[i, :, 4]) / seq_len +
            0.05 * np.random.randn()
        )
    
    return X, y


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


class LSTMModel(nn.Module):
    """LSTM模型"""
    def __init__(self, input_dim=6, hidden_dim=64, num_layers=3, output_dim=1):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)
    
    def forward(self, x):
        out, _ = self.lstm(x)
        out = self.fc(out[:, -1, :])
        return out.squeeze(-1)


class GRUModel(nn.Module):
    """GRU模型"""
    def __init__(self, input_dim=6, hidden_dim=64, num_layers=3, output_dim=1):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)
    
    def forward(self, x):
        out, _ = self.gru(x)
        out = self.fc(out[:, -1, :])
        return out.squeeze(-1)


def train_model(model, X_train, y_train, epochs=50, lr=0.001):
    """训练模型"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = model.to(device)
    
    X_train_t = torch.FloatTensor(X_train).to(device)
    y_train_t = torch.FloatTensor(y_train).to(device)
    
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()
    
    model.train()
    for epoch in range(epochs):
        optimizer.zero_grad()
        pred = model(X_train_t)
        loss = criterion(pred, y_train_t)
        loss.backward()
        optimizer.step()
    
    return model


def evaluate_model(model, X_test, y_test):
    """评估模型"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = model.to(device)
    
    X_test_t = torch.FloatTensor(X_test).to(device)
    
    model.eval()
    with torch.no_grad():
        y_pred = model(X_test_t).cpu().numpy()
    
    mae = np.mean(np.abs(y_test - y_pred))
    rmse = np.sqrt(np.mean((y_test - y_pred) ** 2))
    r2 = 1 - np.sum((y_test - y_pred) ** 2) / (np.sum((y_test - y_test.mean()) ** 2) + 1e-8)
    pcc = np.corrcoef(y_test, y_pred)[0, 1]
    
    return {
        'mae': float(mae),
        'rmse': float(rmse),
        'r2': float(r2),
        'pcc': float(pcc),
        'predictions': y_pred.tolist(),
        'true_values': y_test.tolist()
    }


def k_fold_cross_validation(X, y, model_class, model_params, k=5, epochs=50, lr=0.001):
    """执行K折交叉验证"""
    kf = KFold(n_splits=k, shuffle=True, random_state=42)
    
    fold_results = []
    
    for fold_idx, (train_idx, test_idx) in enumerate(kf.split(X)):
        print(f"    Fold {fold_idx + 1}/{k}...")
        
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
        
        # 创建并训练模型
        model = model_class(**model_params)
        model = train_model(model, X_train, y_train, epochs=epochs, lr=lr)
        
        # 评估
        metrics = evaluate_model(model, X_test, y_test)
        metrics['fold'] = fold_idx + 1
        metrics['train_size'] = len(train_idx)
        metrics['test_size'] = len(test_idx)
        
        fold_results.append(metrics)
        
        print(f"      MAE={metrics['mae']:.4f}, RMSE={metrics['rmse']:.4f}, R²={metrics['r2']:.4f}, PCC={metrics['pcc']:.4f}")
    
    return fold_results


def compute_statistics(fold_results):
    """计算统计指标"""
    metrics = ['mae', 'rmse', 'r2', 'pcc']
    stats = {}
    
    for metric in metrics:
        values = [r[metric] for r in fold_results]
        stats[metric] = {
            'mean': float(np.mean(values)),
            'std': float(np.std(values)),
            'min': float(np.min(values)),
            'max': float(np.max(values)),
            'median': float(np.median(values)),
            'cv': float(np.std(values) / (np.abs(np.mean(values)) + 1e-8))  # 变异系数
        }
    
    return stats


def cross_validation_experiment():
    """执行交叉验证实验"""
    print("=" * 60)
    print("实验44：交叉验证实验")
    print("=" * 60)
    
    # 生成数据
    print("\n[1] 生成铣削振动数据...")
    X, y = generate_milling_data(num_samples=1000, seq_len=20, input_dim=6)
    print(f"  数据形状: X={X.shape}, y={y.shape}")
    
    # 定义模型
    models = {
        'DL-LNN': {
            'class': DLLNNModel,
            'params': {'input_dim': 6, 'hidden_dim': 64, 'num_layers': 3, 'output_dim': 1, 'dt': 0.01}
        },
        'LSTM': {
            'class': LSTMModel,
            'params': {'input_dim': 6, 'hidden_dim': 64, 'num_layers': 3, 'output_dim': 1}
        },
        'GRU': {
            'class': GRUModel,
            'params': {'input_dim': 6, 'hidden_dim': 64, 'num_layers': 3, 'output_dim': 1}
        }
    }
    
    # K折交叉验证
    k = 5
    print(f"\n[2] 执行{k}折交叉验证...")
    
    results = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'experiment': '交叉验证实验',
        'num_samples': len(X),
        'sequence_length': 20,
        'input_dim': 6,
        'k_folds': k,
        'epochs': 50,
        'learning_rate': 0.001,
        'models': {}
    }
    
    for model_name, model_info in models.items():
        print(f"\n  [{model_name}]")
        
        # 执行K折交叉验证
        fold_results = k_fold_cross_validation(
            X, y,
            model_info['class'],
            model_info['params'],
            k=k,
            epochs=50,
            lr=0.001
        )
        
        # 计算统计指标
        stats = compute_statistics(fold_results)
        
        # 保存结果（移除大列表以节省空间）
        fold_results_summary = []
        for r in fold_results:
            fold_results_summary.append({
                'fold': r['fold'],
                'train_size': r['train_size'],
                'test_size': r['test_size'],
                'mae': r['mae'],
                'rmse': r['rmse'],
                'r2': r['r2'],
                'pcc': r['pcc']
            })
        
        results['models'][model_name] = {
            'fold_results': fold_results_summary,
            'statistics': stats
        }
        
        print(f"  统计结果:")
        print(f"    MAE:  {stats['mae']['mean']:.4f} ± {stats['mae']['std']:.4f}")
        print(f"    RMSE: {stats['rmse']['mean']:.4f} ± {stats['rmse']['std']:.4f}")
        print(f"    R²:   {stats['r2']['mean']:.4f} ± {stats['r2']['std']:.4f}")
        print(f"    PCC:  {stats['pcc']['mean']:.4f} ± {stats['pcc']['std']:.4f}")
        print(f"    变异系数(CV): MAE={stats['mae']['cv']:.4f}, PCC={stats['pcc']['cv']:.4f}")
    
    # 模型对比分析
    print("\n[3] 模型稳定性对比分析...")
    
    comparison = {}
    for model_name in models.keys():
        stats = results['models'][model_name]['statistics']
        comparison[model_name] = {
            'mae_mean': stats['mae']['mean'],
            'mae_std': stats['mae']['std'],
            'mae_cv': stats['mae']['cv'],
            'pcc_mean': stats['pcc']['mean'],
            'pcc_std': stats['pcc']['std'],
            'pcc_cv': stats['pcc']['cv'],
            'stability_score': 1.0 / (stats['mae']['cv'] + 1e-8)  # 稳定性得分（CV越小越稳定）
        }
    
    results['comparison'] = comparison
    
    # 找出最稳定的模型
    most_stable = max(comparison.items(), key=lambda x: x[1]['stability_score'])
    best_avg_performance = max(comparison.items(), key=lambda x: x[1]['pcc_mean'])
    
    print(f"  最稳定模型: {most_stable[0]} (稳定性得分={most_stable[1]['stability_score']:.4f})")
    print(f"  最佳平均性能: {best_avg_performance[0]} (PCC={best_avg_performance[1]['pcc_mean']:.4f})")
    
    # 保存结果
    output_file = os.path.join(RESULTS_DIR, 'cross_validation_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n[4] 实验结果已保存至: {output_file}")
    print("=" * 60)
    
    return results


if __name__ == '__main__':
    cross_validation_experiment()
