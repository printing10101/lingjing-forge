"""
实验35：训练收敛性分析实验
记录各模型训练/验证损失曲线，对比收敛速度和稳定性
"""

import json
import os
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import sys

sys.path.insert(0, os.path.dirname(__file__))
from models import DLLNNModel

RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')
os.makedirs(RESULTS_DIR, exist_ok=True)


def generate_synthetic_data(num_samples=1000, seq_len=20, input_dim=6, output_dim=1):
    """生成模拟铣削颤振数据"""
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
        out = self.fc(out[:, -1, :])
        return out


class GRUModel(nn.Module):
    def __init__(self, input_dim, hidden_dim, num_layers, output_dim):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)
    
    def forward(self, x):
        out, _ = self.gru(x)
        out = self.fc(out[:, -1, :])
        return out


def train_model(model, X_train, y_train, X_val, y_val, epochs=50, lr=0.001, model_name="Model"):
    """训练模型并记录每个epoch的训练/验证损失"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = model.to(device)
    
    train_dataset = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    val_dataset = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
    
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()
    
    train_losses = []
    val_losses = []
    
    for epoch in range(epochs):
        # 训练阶段
        model.train()
        epoch_train_loss = 0.0
        for batch_X, batch_y in train_loader:
            batch_X, batch_y = batch_X.to(device), batch_y.to(device)
            optimizer.zero_grad()
            pred = model(batch_X)
            loss = criterion(pred, batch_y)
            loss.backward()
            optimizer.step()
            epoch_train_loss += loss.item()
        
        train_losses.append(epoch_train_loss / len(train_loader))
        
        # 验证阶段
        model.eval()
        epoch_val_loss = 0.0
        with torch.no_grad():
            for batch_X, batch_y in val_loader:
                batch_X, batch_y = batch_X.to(device), batch_y.to(device)
                pred = model(batch_X)
                loss = criterion(pred, batch_y)
                epoch_val_loss += loss.item()
        
        val_losses.append(epoch_val_loss / len(val_loader))
    
    return train_losses, val_losses


def analyze_convergence(train_losses, val_losses):
    """分析收敛特性"""
    train_losses = np.array(train_losses)
    val_losses = np.array(val_losses)
    
    # 收敛速度：达到最终损失95%所需epoch数
    final_train_loss = train_losses[-1]
    threshold = final_train_loss * 1.05
    converge_epoch = len(train_losses)
    for i, loss in enumerate(train_losses):
        if loss <= threshold:
            converge_epoch = i + 1
            break
    
    # 收敛稳定性：最后10个epoch的损失标准差
    stability = np.std(val_losses[-10:])
    
    # 最终性能
    final_train = float(train_losses[-1])
    final_val = float(val_losses[-1])
    best_val = float(np.min(val_losses))
    best_val_epoch = int(np.argmin(val_losses)) + 1
    
    # 过拟合指标：最终训练损失与验证损失的比值
    overfit_ratio = final_val / (final_train + 1e-8)
    
    return {
        'converge_epoch': converge_epoch,
        'stability': float(stability),
        'final_train_loss': final_train,
        'final_val_loss': final_val,
        'best_val_loss': best_val,
        'best_val_epoch': best_val_epoch,
        'overfit_ratio': float(overfit_ratio),
        'train_losses': [float(x) for x in train_losses],
        'val_losses': [float(x) for x in val_losses]
    }


def main():
    print("=" * 60)
    print("实验35：训练收敛性分析实验")
    print("=" * 60)
    
    # 生成数据
    print("\n[1/4] 生成模拟数据...")
    X, y = generate_synthetic_data(num_samples=1000, seq_len=20, input_dim=6)
    
    # 划分训练/验证/测试集
    n_train = int(0.7 * len(X))
    n_val = int(0.15 * len(X))
    X_train, y_train = X[:n_train], y[:n_train]
    X_val, y_val = X[n_train:n_train+n_val], y[n_train:n_train+n_val]
    X_test, y_test = X[n_train+n_val:], y[n_train+n_val:]
    
    print(f"  训练集: {len(X_train)} 样本")
    print(f"  验证集: {len(X_val)} 样本")
    print(f"  测试集: {len(X_test)} 样本")
    
    # 定义模型配置
    input_dim = 6
    hidden_dim = 64
    num_layers = 2
    output_dim = 1
    epochs = 50
    
    models_config = {
        'DL-LNN': lambda: DLLNNModel(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim, dt=0.01),
        'LSTM': lambda: LSTMModel(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim),
        'GRU': lambda: GRUModel(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim),
    }
    
    # 训练各模型并记录收敛过程
    print("\n[2/4] 训练各模型并记录收敛曲线...")
    results = {}
    
    for model_name, model_fn in models_config.items():
        print(f"\n  训练 {model_name}...")
        model = model_fn()
        train_losses, val_losses = train_model(
            model, X_train, y_train, X_val, y_val, 
            epochs=epochs, lr=0.001, model_name=model_name
        )
        
        analysis = analyze_convergence(train_losses, val_losses)
        results[model_name] = analysis
        
        print(f"    收敛epoch: {analysis['converge_epoch']}")
        print(f"    最终训练损失: {analysis['final_train_loss']:.6f}")
        print(f"    最终验证损失: {analysis['final_val_loss']:.6f}")
        print(f"    最佳验证损失: {analysis['best_val_loss']:.6f} (epoch {analysis['best_val_epoch']})")
        print(f"    收敛稳定性(std): {analysis['stability']:.6f}")
        print(f"    过拟合比率: {analysis['overfit_ratio']:.4f}")
    
    # 生成收敛速度对比数据（不同学习率下的表现）
    print("\n[3/4] 分析不同学习率对DL-LNN收敛的影响...")
    lr_sweep_results = {}
    for lr in [0.0001, 0.0005, 0.001, 0.005, 0.01]:
        print(f"  学习率 = {lr}...")
        model = DLLNNModel(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim, dt=0.01)
        train_losses, val_losses = train_model(
            model, X_train, y_train, X_val, y_val, 
            epochs=epochs, lr=lr, model_name=f"DL-LNN_lr{lr}"
        )
        analysis = analyze_convergence(train_losses, val_losses)
        lr_sweep_results[str(lr)] = {
            'converge_epoch': analysis['converge_epoch'],
            'final_train_loss': analysis['final_train_loss'],
            'final_val_loss': analysis['final_val_loss'],
            'best_val_loss': analysis['best_val_loss'],
            'stability': analysis['stability']
        }
    
    # 保存结果
    print("\n[4/4] 保存结果...")
    output = {
        'timestamp': str(np.datetime64('now')),
        'experiment': '训练收敛性分析实验',
        'epochs': epochs,
        'models': list(models_config.keys()),
        'convergence_analysis': results,
        'learning_rate_sweep': lr_sweep_results
    }
    
    output_path = os.path.join(RESULTS_DIR, 'training_convergence_results.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存至: {output_path}")
    print("=" * 60)
    print("实验35完成！")
    print("=" * 60)


if __name__ == '__main__':
    main()
