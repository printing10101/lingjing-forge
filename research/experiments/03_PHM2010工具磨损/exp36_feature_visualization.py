"""
实验36：特征空间可视化实验（t-SNE/PCA）
使用t-SNE和PCA降维技术可视化模型学到的特征表示，对比不同模型的特征聚类质量
"""

import json
import os
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.manifold import TSNE
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
import sys

sys.path.insert(0, os.path.dirname(__file__))
from models import DLLNNModel

RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')
os.makedirs(RESULTS_DIR, exist_ok=True)


def generate_synthetic_data_with_labels(num_samples=1000, seq_len=20, input_dim=6):
    """生成带标签的模拟数据，用于特征可视化"""
    np.random.seed(42)
    X = np.random.randn(num_samples, seq_len, input_dim).astype(np.float32)
    # 生成二分类标签：基于信号特征区分稳定/颤振状态
    y = (np.sum(X[:, -1, :3], axis=1) > 0).astype(np.int64)
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
    
    def extract_features(self, x):
        """提取特征表示"""
        out, _ = self.lstm(x)
        return out[:, -1, :]


class GRUModel(nn.Module):
    def __init__(self, input_dim, hidden_dim, num_layers, output_dim):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)
    
    def forward(self, x):
        out, _ = self.gru(x)
        out = self.fc(out[:, -1, :])
        return out
    
    def extract_features(self, x):
        """提取特征表示"""
        out, _ = self.gru(x)
        return out[:, -1, :]


def extract_features_from_model(model, data_loader, device):
    """从模型中提取特征表示"""
    model.eval()
    features = []
    labels = []
    
    with torch.no_grad():
        for batch_X, batch_y in data_loader:
            batch_X = batch_X.to(device)
            # 提取倒数第二层的特征
            feat = model.extract_features(batch_X)
            features.append(feat.cpu().numpy())
            labels.append(batch_y.numpy())
    
    features = np.vstack(features)
    labels = np.concatenate(labels)
    return features, labels


def analyze_feature_quality(features, labels):
    """分析特征质量指标"""
    # 计算轮廓系数（聚类质量）
    if len(np.unique(labels)) > 1:
        silhouette = silhouette_score(features, labels)
    else:
        silhouette = 0.0
    
    # 计算类内距离和类间距离
    class_centers = []
    intra_class_distances = []
    
    for label in np.unique(labels):
        class_features = features[labels == label]
        class_center = np.mean(class_features, axis=0)
        class_centers.append(class_center)
        
        # 类内距离：每个点到类中心的平均距离
        distances = np.linalg.norm(class_features - class_center, axis=1)
        intra_class_distances.append(np.mean(distances))
    
    # 类间距离：类中心之间的距离
    inter_class_distances = []
    for i in range(len(class_centers)):
        for j in range(i+1, len(class_centers)):
            dist = np.linalg.norm(class_centers[i] - class_centers[j])
            inter_class_distances.append(dist)
    
    # 可分性指标：类间距离/类内距离
    avg_intra = np.mean(intra_class_distances)
    avg_inter = np.mean(inter_class_distances) if inter_class_distances else 0.0
    separability = avg_inter / (avg_intra + 1e-8)
    
    return {
        'silhouette_score': float(silhouette),
        'avg_intra_class_distance': float(avg_intra),
        'avg_inter_class_distance': float(avg_inter),
        'separability_score': float(separability),
        'num_classes': int(len(np.unique(labels)))
    }


def perform_tsne(features, perplexity=30, random_state=42):
    """执行t-SNE降维"""
    tsne = TSNE(n_components=2, perplexity=perplexity, random_state=random_state)
    embedded = tsne.fit_transform(features)
    return embedded


def perform_pca(features, n_components=2):
    """执行PCA降维"""
    pca = PCA(n_components=n_components, random_state=42)
    embedded = pca.fit_transform(features)
    explained_variance = pca.explained_variance_ratio_
    return embedded, explained_variance


def main():
    print("=" * 60)
    print("实验36：特征空间可视化实验（t-SNE/PCA）")
    print("=" * 60)
    
    # 生成数据
    print("\n[1/5] 生成带标签的模拟数据...")
    X, y = generate_synthetic_data_with_labels(num_samples=1000, seq_len=20, input_dim=6)
    
    dataset = TensorDataset(torch.from_numpy(X), torch.from_numpy(y))
    data_loader = DataLoader(dataset, batch_size=32, shuffle=False)
    
    print(f"  样本数: {len(X)}")
    print(f"  类别分布: {np.bincount(y)}")
    
    # 定义模型
    input_dim = 6
    hidden_dim = 64
    num_layers = 2
    output_dim = 2  # 二分类
    
    models_config = {
        'DL-LNN': DLLNNModel(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim, dt=0.01),
        'LSTM': LSTMModel(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim),
        'GRU': GRUModel(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim),
    }
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    
    # 提取各模型的特征表示
    print("\n[2/5] 提取各模型的特征表示...")
    feature_results = {}
    
    for model_name, model in models_config.items():
        print(f"\n  处理 {model_name}...")
        model = model.to(device)
        
        # 提取特征
        features, labels = extract_features_from_model(model, data_loader, device)
        print(f"    特征维度: {features.shape}")
        
        # 分析特征质量
        quality_metrics = analyze_feature_quality(features, labels)
        print(f"    轮廓系数: {quality_metrics['silhouette_score']:.4f}")
        print(f"    可分性得分: {quality_metrics['separability_score']:.4f}")
        
        # t-SNE降维
        print("    执行t-SNE降维...")
        tsne_embedded = perform_tsne(features)
        
        # PCA降维
        print("    执行PCA降维...")
        pca_embedded, pca_variance = perform_pca(features)
        
        feature_results[model_name] = {
            'quality_metrics': quality_metrics,
            'tsne_coordinates': tsne_embedded.tolist(),
            'pca_coordinates': pca_embedded.tolist(),
            'pca_explained_variance': pca_variance.tolist(),
            'labels': labels.tolist()
        }
    
    # 计算原始数据的特征质量作为基线
    print("\n[3/5] 计算原始数据基线...")
    original_features = X[:, -1, :]  # 使用最后一个时间步的原始特征
    original_quality = analyze_feature_quality(original_features, y)
    print(f"  原始数据轮廓系数: {original_quality['silhouette_score']:.4f}")
    print(f"  原始数据可分性得分: {original_quality['separability_score']:.4f}")
    
    # 对比分析
    print("\n[4/5] 对比分析特征质量...")
    comparison = {
        'original_data': original_quality,
        'models': {}
    }
    
    for model_name, results in feature_results.items():
        comparison['models'][model_name] = results['quality_metrics']
        print(f"\n  {model_name}:")
        print(f"    轮廓系数: {results['quality_metrics']['silhouette_score']:.4f}")
        print(f"    类内距离: {results['quality_metrics']['avg_intra_class_distance']:.4f}")
        print(f"    类间距离: {results['quality_metrics']['avg_inter_class_distance']:.4f}")
        print(f"    可分性: {results['quality_metrics']['separability_score']:.4f}")
    
    # 保存结果
    print("\n[5/5] 保存结果...")
    output = {
        'timestamp': str(np.datetime64('now')),
        'experiment': '特征空间可视化实验',
        'num_samples': len(X),
        'feature_dim': hidden_dim,
        'comparison': comparison,
        'detailed_results': feature_results
    }
    
    output_path = os.path.join(RESULTS_DIR, 'feature_visualization_results.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存至: {output_path}")
    print("=" * 60)
    print("实验36完成！")
    print("=" * 60)


if __name__ == '__main__':
    main()
