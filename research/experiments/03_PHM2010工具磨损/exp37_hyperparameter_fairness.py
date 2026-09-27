"""
实验37：对比模型最优超参数公平性验证实验
验证所有对比模型都在最优超参数下运行，确保公平对比
"""

import json
import os
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from itertools import product
import sys

sys.path.insert(0, os.path.dirname(__file__))
from models import DLLNNModel

RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')
os.makedirs(RESULTS_DIR, exist_ok=True)


def generate_synthetic_data(num_samples=800, seq_len=20, input_dim=6):
    """生成模拟数据"""
    np.random.seed(42)
    X = np.random.randn(num_samples, seq_len, input_dim).astype(np.float32)
    y = np.sum(X[:, -1, :], axis=1, keepdims=True).astype(np.float32)
    return X, y


class LSTMModel(nn.Module):
    def __init__(self, input_dim, hidden_dim, num_layers, output_dim):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)
    
    def forward(self, x):
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :])


class GRUModel(nn.Module):
    def __init__(self, input_dim, hidden_dim, num_layers, output_dim):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)
    
    def forward(self, x):
        out, _ = self.gru(x)
        return self.fc(out[:, -1, :])


class TransformerModel(nn.Module):
    def __init__(self, input_dim, d_model=64, nhead=4, num_layers=2, output_dim=1):
        super().__init__()
        self.encoder = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(d_model=d_model, nhead=nhead, batch_first=True),
            num_layers=num_layers
        )
        self.fc = nn.Linear(d_model, output_dim)
    
    def forward(self, x):
        out = self.encoder(x)
        return self.fc(out[:, -1, :])


def train_and_evaluate(model, X_train, y_train, X_val, y_val, epochs=30, lr=0.001):
    """训练模型并返回验证集MAE"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = model.to(device)
    
    train_dataset = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    val_dataset = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
    
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.L1Loss()
    
    best_val_mae = float('inf')
    
    for epoch in range(epochs):
        model.train()
        for batch_X, batch_y in train_loader:
            batch_X, batch_y = batch_X.to(device), batch_y.to(device)
            optimizer.zero_grad()
            pred = model(batch_X)
            loss = criterion(pred, batch_y)
            loss.backward()
            optimizer.step()
        
        # 验证
        model.eval()
        val_mae = 0.0
        with torch.no_grad():
            for batch_X, batch_y in val_loader:
                batch_X, batch_y = batch_X.to(device), batch_y.to(device)
                pred = model(batch_X)
                val_mae += criterion(pred, batch_y).item()
        val_mae /= len(val_loader)
        best_val_mae = min(best_val_mae, val_mae)
    
    return best_val_mae


def hyperparameter_search(model_name, model_fn, X_train, y_train, X_val, y_val, param_grid):
    """执行超参数搜索"""
    results = []
    best_mae = float('inf')
    best_params = None
    
    param_combinations = list(product(*param_grid.values()))
    print(f"    搜索空间大小: {len(param_combinations)} 种组合")
    
    for i, params in enumerate(param_combinations):
        param_dict = dict(zip(param_grid.keys(), params))
        
        # 创建模型
        model = model_fn(**param_dict)
        
        # 训练并评估
        val_mae = train_and_evaluate(model, X_train, y_train, X_val, y_val, epochs=30, lr=param_dict.get('lr', 0.001))
        
        results.append({
            'params': param_dict,
            'val_mae': float(val_mae)
        })
        
        if val_mae < best_mae:
            best_mae = val_mae
            best_params = param_dict.copy()
        
        if (i + 1) % 10 == 0:
            print(f"      已搜索 {i+1}/{len(param_combinations)} 种组合")
    
    return results, best_params, float(best_mae)


def main():
    print("=" * 60)
    print("实验37：对比模型最优超参数公平性验证实验")
    print("=" * 60)
    
    # 生成数据
    print("\n[1/5] 生成数据...")
    X, y = generate_synthetic_data(num_samples=800)
    
    n_train = int(0.7 * len(X))
    n_val = int(0.15 * len(X))
    X_train, y_train = X[:n_train], y[:n_train]
    X_val, y_val = X[n_train:n_train+n_val], y[n_train:n_train+n_val]
    
    print(f"  训练集: {len(X_train)} 样本")
    print(f"  验证集: {len(X_val)} 样本")
    
    input_dim = 6
    output_dim = 1
    
    # 定义各模型的超参数搜索空间
    print("\n[2/5] 定义超参数搜索空间...")
    
    # DL-LNN 搜索空间
    ctl_tc_grid = {
        'hidden_dim': [32, 64, 128],
        'num_layers': [1, 2, 3],
        'dt': [0.01, 0.05, 0.1],
        'lr': [0.0005, 0.001, 0.005]
    }
    
    # LSTM 搜索空间
    lstm_grid = {
        'hidden_dim': [32, 64, 128],
        'num_layers': [1, 2, 3],
        'lr': [0.0005, 0.001, 0.005]
    }
    
    # GRU 搜索空间
    gru_grid = {
        'hidden_dim': [32, 64, 128],
        'num_layers': [1, 2, 3],
        'lr': [0.0005, 0.001, 0.005]
    }
    
    # Transformer 搜索空间
    transformer_grid = {
        'd_model': [32, 64, 128],
        'nhead': [4, 8],
        'num_layers': [1, 2, 3],
        'lr': [0.0005, 0.001, 0.005]
    }
    
    # 执行超参数搜索
    print("\n[3/5] 执行超参数搜索...")
    
    search_results = {}
    
    # DL-LNN
    print("\n  搜索 DL-LNN 最优超参数...")
    def ctl_tc_fn(hidden_dim, num_layers, dt, lr):
        return DLLNNModel(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim, dt=dt)
    
    ctl_tc_all, ctl_tc_best, ctl_tc_best_mae = hyperparameter_search(
        'DL-LNN', ctl_tc_fn, X_train, y_train, X_val, y_val, ctl_tc_grid
    )
    search_results['DL-LNN'] = {
        'search_space': {k: list(v) for k, v in ctl_tc_grid.items()},
        'all_results': ctl_tc_all,
        'best_params': ctl_tc_best,
        'best_val_mae': ctl_tc_best_mae
    }
    print(f"    最优参数: {ctl_tc_best}")
    print(f"    最优验证MAE: {ctl_tc_best_mae:.4f}")
    
    # LSTM
    print("\n  搜索 LSTM 最优超参数...")
    def lstm_fn(hidden_dim, num_layers, lr):
        return LSTMModel(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim)
    
    lstm_all, lstm_best, lstm_best_mae = hyperparameter_search(
        'LSTM', lstm_fn, X_train, y_train, X_val, y_val, lstm_grid
    )
    search_results['LSTM'] = {
        'search_space': {k: list(v) for k, v in lstm_grid.items()},
        'all_results': lstm_all,
        'best_params': lstm_best,
        'best_val_mae': lstm_best_mae
    }
    print(f"    最优参数: {lstm_best}")
    print(f"    最优验证MAE: {lstm_best_mae:.4f}")
    
    # GRU
    print("\n  搜索 GRU 最优超参数...")
    def gru_fn(hidden_dim, num_layers, lr):
        return GRUModel(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim)
    
    gru_all, gru_best, gru_best_mae = hyperparameter_search(
        'GRU', gru_fn, X_train, y_train, X_val, y_val, gru_grid
    )
    search_results['GRU'] = {
        'search_space': {k: list(v) for k, v in gru_grid.items()},
        'all_results': gru_all,
        'best_params': gru_best,
        'best_val_mae': gru_best_mae
    }
    print(f"    最优参数: {gru_best}")
    print(f"    最优验证MAE: {gru_best_mae:.4f}")
    
    # Transformer
    print("\n  搜索 Transformer 最优超参数...")
    def transformer_fn(d_model, nhead, num_layers, lr):
        return TransformerModel(input_dim=input_dim, d_model=d_model, nhead=nhead, num_layers=num_layers, output_dim=output_dim)
    
    transformer_all, transformer_best, transformer_best_mae = hyperparameter_search(
        'Transformer', transformer_fn, X_train, y_train, X_val, y_val, transformer_grid
    )
    search_results['Transformer'] = {
        'search_space': {k: list(v) for k, v in transformer_grid.items()},
        'all_results': transformer_all,
        'best_params': transformer_best,
        'best_val_mae': transformer_best_mae
    }
    print(f"    最优参数: {transformer_best}")
    print(f"    最优验证MAE: {transformer_best_mae:.4f}")
    
    # 公平性分析
    print("\n[4/5] 公平性分析...")
    fairness_summary = {
        'DL-LNN': {
            'best_val_mae': ctl_tc_best_mae,
            'best_params': ctl_tc_best,
            'num_configs_tested': len(ctl_tc_all),
            'performance_range': [min(r['val_mae'] for r in ctl_tc_all), max(r['val_mae'] for r in ctl_tc_all)]
        },
        'LSTM': {
            'best_val_mae': lstm_best_mae,
            'best_params': lstm_best,
            'num_configs_tested': len(lstm_all),
            'performance_range': [min(r['val_mae'] for r in lstm_all), max(r['val_mae'] for r in lstm_all)]
        },
        'GRU': {
            'best_val_mae': gru_best_mae,
            'best_params': gru_best,
            'num_configs_tested': len(gru_all),
            'performance_range': [min(r['val_mae'] for r in gru_all), max(r['val_mae'] for r in gru_all)]
        },
        'Transformer': {
            'best_val_mae': transformer_best_mae,
            'best_params': transformer_best,
            'num_configs_tested': len(transformer_all),
            'performance_range': [min(r['val_mae'] for r in transformer_all), max(r['val_mae'] for r in transformer_all)]
        }
    }
    
    for model_name, summary in fairness_summary.items():
        print(f"\n  {model_name}:")
        print(f"    测试配置数: {summary['num_configs_tested']}")
        print(f"    最优验证MAE: {summary['best_val_mae']:.4f}")
        print(f"    性能范围: [{summary['performance_range'][0]:.4f}, {summary['performance_range'][1]:.4f}]")
    
    # 保存结果
    print("\n[5/5] 保存结果...")
    output = {
        'timestamp': str(np.datetime64('now')),
        'experiment': '对比模型最优超参数公平性验证实验',
        'search_results': search_results,
        'fairness_summary': fairness_summary
    }
    
    output_path = os.path.join(RESULTS_DIR, 'hyperparameter_fairness_results.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存至: {output_path}")
    print("=" * 60)
    print("实验37完成！")
    print("=" * 60)


if __name__ == '__main__':
    main()
