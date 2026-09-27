"""
实验43：特征交互作用分析实验
分析不同输入特征之间的交互作用及其对预测性能的影响
"""

import numpy as np
import torch
import torch.nn as nn
from datetime import datetime
import json
import os
from itertools import combinations

RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')


def generate_milling_data_with_features(num_samples=1500, seq_len=20):
    """生成多特征铣削数据"""
    np.random.seed(42)
    
    # 切削参数
    spindle_speed = np.random.uniform(5000, 15000, num_samples)  # rpm
    feed_rate = np.random.uniform(0.05, 0.2, num_samples)  # mm/tooth
    axial_depth = np.random.uniform(0.5, 5.0, num_samples)  # mm
    radial_depth = np.random.uniform(0.5, 10.0, num_samples)  # mm
    
    # 物理特征
    tooth_freq = spindle_speed / 60.0 * 4  # 4齿铣刀
    sampling_rate = 1000  # Hz
    
    X = np.zeros((num_samples, seq_len, 8), dtype=np.float32)
    y = np.zeros(num_samples, dtype=np.float32)
    
    feature_names = [
        'vibration', 'cutting_force', 'velocity', 'acceleration',
        'energy', 'envelope', 'spindle_current', 'temperature'
    ]
    
    for i in range(num_samples):
        t = np.arange(seq_len) / sampling_rate
        
        # 基础信号
        cutting_force = axial_depth[i] * feed_rate[i] * 100 * np.sin(2 * np.pi * tooth_freq[i] * t)
        chatter_freq = tooth_freq[i] * 2.5
        chatter_amp = axial_depth[i] * 0.3
        chatter = chatter_amp * np.sin(2 * np.pi * chatter_freq * t)
        noise = 0.1 * np.random.randn(seq_len)
        
        signal = cutting_force + chatter + noise
        
        # 多通道特征
        X[i, :, 0] = signal  # 振动信号
        X[i, :, 1] = cutting_force  # 切削力
        X[i, :, 2] = np.gradient(signal)  # 速度
        X[i, :, 3] = np.gradient(np.gradient(signal))  # 加速度
        X[i, :, 4] = signal ** 2  # 能量
        X[i, :, 5] = np.abs(signal)  # 包络
        X[i, :, 6] = spindle_speed[i] / 10000 * np.ones(seq_len)  # 主轴电流（与转速相关）
        X[i, :, 7] = 0.01 * radial_depth[i] * np.ones(seq_len)  # 温度（与切深相关）
        
        # 极限切削深度（综合多个参数）
        stability_limit = (
            axial_depth[i] * (1 - 0.3 * chatter_amp / axial_depth[i]) +
            0.1 * radial_depth[i] -
            0.05 * feed_rate[i] * 100
        )
        y[i] = stability_limit
    
    return X, y, feature_names, {
        'spindle_speed': spindle_speed,
        'feed_rate': feed_rate,
        'axial_depth': axial_depth,
        'radial_depth': radial_depth
    }


class DLLNNModel(nn.Module):
    """DL-LNN模型简化版"""
    def __init__(self, input_dim=8, hidden_dim=64, num_layers=3, output_dim=1):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        
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


def train_and_evaluate(X_train, y_train, X_test, y_test, feature_indices=None, epochs=30):
    """训练并评估模型"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    
    # 选择特征
    if feature_indices is not None:
        X_train = X_train[:, :, feature_indices]
        X_test = X_test[:, :, feature_indices]
    
    X_train_t = torch.FloatTensor(X_train).to(device)
    y_train_t = torch.FloatTensor(y_train).to(device)
    X_test_t = torch.FloatTensor(X_test).to(device)
    y_test_t = torch.FloatTensor(y_test).to(device)
    
    model = DLLNNModel(input_dim=X_train.shape[2]).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
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
    
    mae = np.mean(np.abs(y_test - y_pred))
    rmse = np.sqrt(np.mean((y_test - y_pred) ** 2))
    r2 = 1 - np.sum((y_test - y_pred) ** 2) / (np.sum((y_test - y_test.mean()) ** 2) + 1e-8)
    pcc = np.corrcoef(y_test, y_pred)[0, 1]
    
    return {
        'mae': float(mae),
        'rmse': float(rmse),
        'r2': float(r2),
        'pcc': float(pcc)
    }


def single_feature_analysis(X_train, y_train, X_test, y_test, feature_names):
    """单特征重要性分析"""
    print("  [1] 单特征重要性分析...")
    
    results = {}
    for i, fname in enumerate(feature_names):
        metrics = train_and_evaluate(X_train, y_train, X_test, y_test, feature_indices=[i])
        results[fname] = metrics
        print(f"    {fname}: MAE={metrics['mae']:.4f}, PCC={metrics['pcc']:.4f}")
    
    return results


def pairwise_interaction_analysis(X_train, y_train, X_test, y_test, feature_names):
    """成对特征交互分析"""
    print("  [2] 成对特征交互分析...")
    
    results = {}
    num_features = len(feature_names)
    
    for i, j in combinations(range(num_features), 2):
        pair_name = f"{feature_names[i]}+{feature_names[j]}"
        metrics = train_and_evaluate(X_train, y_train, X_test, y_test, feature_indices=[i, j])
        results[pair_name] = metrics
        print(f"    {pair_name}: MAE={metrics['mae']:.4f}, PCC={metrics['pcc']:.4f}")
    
    return results


def feature_synergy_analysis(X_train, y_train, X_test, y_test, feature_names, single_results, pair_results):
    """特征协同效应分析"""
    print("  [3] 特征协同效应分析...")
    
    synergy_results = {}
    num_features = len(feature_names)
    
    for i, j in combinations(range(num_features), 2):
        pair_name = f"{feature_names[i]}+{feature_names[j]}"
        
        # 计算协同效应：成对性能 - 单特征性能之和
        single_mae_sum = single_results[feature_names[i]]['mae'] + single_results[feature_names[j]]['mae']
        pair_mae = pair_results[pair_name]['mae']
        
        # 协同效应 = 单特征误差和 - 成对误差（正值表示协同，负值表示冗余）
        synergy = single_mae_sum - pair_mae
        
        # 归一化协同效应
        normalized_synergy = synergy / (single_mae_sum + 1e-8)
        
        synergy_results[pair_name] = {
            'synergy_score': float(synergy),
            'normalized_synergy': float(normalized_synergy),
            'single_mae_sum': float(single_mae_sum),
            'pair_mae': float(pair_mae),
            'interpretation': '协同' if synergy > 0 else '冗余'
        }
    
    # 排序找出最强协同效应
    sorted_synergies = sorted(
        synergy_results.items(),
        key=lambda x: x[1]['normalized_synergy'],
        reverse=True
    )
    
    print("    最强协同效应Top 5:")
    for pair_name, data in sorted_synergies[:5]:
        print(f"      {pair_name}: 协同得分={data['synergy_score']:.4f}, 归一化={data['normalized_synergy']:.4f}")
    
    return synergy_results


def feature_redundancy_analysis(X_train, y_train, X_test, y_test, feature_names):
    """特征冗余度分析"""
    print("  [4] 特征冗余度分析...")
    
    num_features = len(feature_names)
    redundancy_matrix = np.zeros((num_features, num_features))
    
    # 计算特征间的相关性
    for i in range(num_features):
        for j in range(num_features):
            if i == j:
                redundancy_matrix[i, j] = 1.0
            else:
                # 使用特征的第一个时间步计算相关性
                feat_i = X_train[:, 0, i]
                feat_j = X_train[:, 0, j]
                corr = np.corrcoef(feat_i, feat_j)[0, 1]
                redundancy_matrix[i, j] = abs(corr)
    
    # 识别高冗余特征对（相关性>0.8）
    high_redundancy_pairs = []
    for i, j in combinations(range(num_features), 2):
        if redundancy_matrix[i, j] > 0.8:
            high_redundancy_pairs.append({
                'feature_i': feature_names[i],
                'feature_j': feature_names[j],
                'correlation': float(redundancy_matrix[i, j])
            })
    
    print(f"    高冗余特征对数量: {len(high_redundancy_pairs)}")
    for pair in high_redundancy_pairs:
        print(f"      {pair['feature_i']} <-> {pair['feature_j']}: 相关性={pair['correlation']:.4f}")
    
    return {
        'redundancy_matrix': redundancy_matrix.tolist(),
        'high_redundancy_pairs': high_redundancy_pairs,
        'feature_names': feature_names
    }


def optimal_feature_subset_selection(X_train, y_train, X_test, y_test, feature_names, top_k=5):
    """最优特征子集选择"""
    print(f"  [5] 最优特征子集选择（Top {top_k}）...")
    
    num_features = len(feature_names)
    
    # 计算每个特征的重要性（基于单特征性能）
    feature_importance = {}
    for i, fname in enumerate(feature_names):
        metrics = train_and_evaluate(X_train, y_train, X_test, y_test, feature_indices=[i], epochs=20)
        # 使用PCC的绝对值作为重要性
        feature_importance[fname] = abs(metrics['pcc'])
    
    # 按重要性排序
    sorted_features = sorted(feature_importance.items(), key=lambda x: x[1], reverse=True)
    
    print("    特征重要性排序:")
    for fname, importance in sorted_features:
        print(f"      {fname}: {importance:.4f}")
    
    # 选择Top K特征
    top_features = [f[0] for f in sorted_features[:top_k]]
    top_indices = [feature_names.index(f) for f in top_features]
    
    # 评估Top K特征组合
    metrics_top_k = train_and_evaluate(X_train, y_train, X_test, y_test, feature_indices=top_indices)
    
    # 评估全部特征
    metrics_all = train_and_evaluate(X_train, y_train, X_test, y_test, feature_indices=None)
    
    print(f"\n    Top {top_k}特征组合: {top_features}")
    print(f"    Top {top_k}性能: MAE={metrics_top_k['mae']:.4f}, PCC={metrics_top_k['pcc']:.4f}")
    print(f"    全部特征性能: MAE={metrics_all['mae']:.4f}, PCC={metrics_all['pcc']:.4f}")
    
    return {
        'feature_importance': {k: float(v) for k, v in sorted_features},
        'top_features': top_features,
        'top_k_metrics': metrics_top_k,
        'all_features_metrics': metrics_all,
        'performance_ratio': float(metrics_top_k['mae'] / (metrics_all['mae'] + 1e-8))
    }


def feature_interaction_experiment():
    """执行特征交互作用分析实验"""
    print("=" * 60)
    print("实验43：特征交互作用分析实验")
    print("=" * 60)
    
    # 生成数据
    print("\n[1] 生成多特征铣削数据...")
    X, y, feature_names, params = generate_milling_data_with_features(num_samples=1500, seq_len=20)
    print(f"  数据形状: X={X.shape}, y={y.shape}")
    print(f"  特征名称: {feature_names}")
    
    # 划分数据集
    split_idx = int(0.8 * len(X))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]
    
    print(f"  训练集: {X_train.shape[0]} 样本")
    print(f"  测试集: {X_test.shape[0]} 样本")
    
    # 1. 单特征分析
    print("\n[2] 单特征重要性分析...")
    single_results = single_feature_analysis(X_train, y_train, X_test, y_test, feature_names)
    
    # 2. 成对交互分析
    print("\n[3] 成对特征交互分析...")
    pair_results = pairwise_interaction_analysis(X_train, y_train, X_test, y_test, feature_names)
    
    # 3. 协同效应分析
    print("\n[4] 特征协同效应分析...")
    synergy_results = feature_synergy_analysis(
        X_train, y_train, X_test, y_test, feature_names,
        single_results, pair_results
    )
    
    # 4. 冗余度分析
    print("\n[5] 特征冗余度分析...")
    redundancy_results = feature_redundancy_analysis(X_train, y_train, X_test, y_test, feature_names)
    
    # 5. 最优特征子集选择
    print("\n[6] 最优特征子集选择...")
    subset_results = optimal_feature_subset_selection(X_train, y_train, X_test, y_test, feature_names, top_k=5)
    
    # 保存结果
    results = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'experiment': '特征交互作用分析实验',
        'num_samples': len(X),
        'sequence_length': 20,
        'feature_names': feature_names,
        'single_feature_analysis': single_results,
        'pairwise_interaction_analysis': pair_results,
        'synergy_analysis': synergy_results,
        'redundancy_analysis': redundancy_results,
        'optimal_subset_selection': subset_results
    }
    
    output_file = os.path.join(RESULTS_DIR, 'feature_interaction_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n[7] 实验结果已保存至: {output_file}")
    print("=" * 60)
    
    return results


if __name__ == '__main__':
    feature_interaction_experiment()
