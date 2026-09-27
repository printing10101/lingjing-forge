"""
实验17: 模型可解释性分析 - 使用SHAP值分析特征重要性

本实验旨在通过SHAP (SHapley Additive exPlanations) 方法分析DL-LNN模型的输入特征重要性，
理解主轴转速和切深等参数对预测结果的贡献程度，并分析不同工况下的特征贡献差异。
"""

import sys
import json
import torch
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
import matplotlib.pyplot as plt
import seaborn as sns

# 添加项目根目录到路径（experiments 的父目录即 python 根目录）
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from training.reproducibility import set_global_seed

# 按照任务要求从顶层模块导入
from config import ModelConfig
from models import DLLNNWithPhysics
from data_generator import Industrial6061T6Dataset, create_dataloaders


class SHAPAnalyzer:
    """SHAP值分析器 - 用于计算和可视化特征重要性"""
    
    def __init__(self, model: DLLNNWithPhysics, device: torch.device):
        """
        初始化SHAP分析器
        
        Args:
            model: 训练好的DL-LNN模型
            device: 计算设备 (CPU/GPU)
        """
        self.model = model
        self.device = device
        self.model.eval()  # 设置为评估模式
        
    def compute_shap_values_gradient(
        self, 
        input_features: torch.Tensor, 
        num_samples: int = 100
    ) -> np.ndarray:
        """
        使用梯度法近似计算SHAP值
        
        Args:
            input_features: 输入特征张量 [batch_size, num_features]
            num_samples: 采样次数
            
        Returns:
            SHAP值数组 [batch_size, num_features]
        """
        batch_size, num_features = input_features.shape
        shap_values = np.zeros((batch_size, num_features))
        
        # 对每个特征维度计算梯度作为重要性近似
        for feat_idx in range(num_features):
            # 创建需要梯度的输入副本
            input_grad = input_features.clone().detach().requires_grad_(True)
            
            # 前向传播
            output = self.model(input_grad)
            
            # 计算输出对输入的梯度
            if isinstance(output, tuple):
                output = output[0]  # 取第一个输出
            
            # 对输出求和以便反向传播
            output_sum = output.sum()
            output_sum.backward()
            
            # 提取当前特征的梯度
            gradients = input_grad.grad[:, feat_idx]
            
            # 使用梯度的绝对值作为SHAP值的近似
            # SHAP值表示特征对输出的贡献，梯度可以近似表示这种贡献
            shap_values[:, feat_idx] = gradients.abs().cpu().numpy()
        
        return shap_values
    
    def compute_shap_values_perturbation(
        self,
        input_features: torch.Tensor,
        baseline: torch.Tensor = None,
        num_samples: int = 50
    ) -> np.ndarray:
        """
        使用扰动法计算SHAP值（更精确但更慢）
        
        Args:
            input_features: 输入特征张量 [batch_size, num_features]
            baseline: 基线值（通常为均值或零）
            num_samples: 采样次数
            
        Returns:
            SHAP值数组 [batch_size, num_features]
        """
        batch_size, num_features = input_features.shape
        
        if baseline is None:
            baseline = torch.zeros_like(input_features)
        
        shap_values = np.zeros((batch_size, num_features))
        
        # 对每个特征进行扰动分析
        for feat_idx in range(num_features):
            feature_contributions = []
            
            for _ in range(num_samples):
                # 随机选择特征子集
                mask = torch.rand(batch_size, num_features) > 0.5
                mask = mask.to(self.device)
                
                # 创建两个输入：一个包含当前特征，一个不包含
                input_with = input_features.clone()
                input_without = input_features.clone()
                input_without[:, feat_idx] = baseline[:, feat_idx]
                
                # 应用掩码
                input_with = input_with * mask + baseline * (~mask)
                input_without = input_without * mask + baseline * (~mask)
                
                # 确保当前特征在两个输入中不同
                input_with[:, feat_idx] = input_features[:, feat_idx]
                
                # 前向传播
                with torch.no_grad():
                    output_with = self.model(input_with)
                    output_without = self.model(input_without)
                    
                    if isinstance(output_with, tuple):
                        output_with = output_with[0]
                        output_without = output_without[0]
                
                # 计算差异
                diff = (output_with - output_without).abs().mean()
                feature_contributions.append(diff.item())
            
            # 平均贡献作为SHAP值
            shap_values[:, feat_idx] = np.mean(feature_contributions)
        
        return shap_values


def train_ct_ltc_model(
    train_loader, 
    val_loader, 
    config: ModelConfig,
    device: torch.device,
    epochs: int = 10
) -> DLLNNWithPhysics:
    """
    训练DL-LNN模型
    
    Args:
        train_loader: 训练数据加载器
        val_loader: 验证数据加载器
        config: 模型配置
        device: 计算设备
        epochs: 训练轮数
        
    Returns:
        训练好的模型
    """
    print("=" * 60)
    print("开始训练DL-LNN模型")
    print("=" * 60)
    
    # 创建模型
    model = DLLNNWithPhysics(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
        dt=config.ltc_dt,
        dropout=config.dropout
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
    criterion = torch.nn.MSELoss()
    
    best_val_loss = float('inf')
    
    for epoch in range(epochs):
        # 训练阶段
        model.train()
        train_loss = 0.0
        train_batches = 0
        
        for batch in train_loader:
            inputs = batch[0].to(device)  # features
            targets = batch[1].to(device)  # a_lim
            
            optimizer.zero_grad()
            outputs = model(inputs)
            
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            
            loss = criterion(outputs, targets)
            loss.backward()
            optimizer.step()
            
            train_loss += loss.item()
            train_batches += 1
        
        avg_train_loss = train_loss / max(train_batches, 1)
        
        # 验证阶段
        model.eval()
        val_loss = 0.0
        val_batches = 0
        
        with torch.no_grad():
            for batch in val_loader:
                inputs = batch[0].to(device)  # features
                targets = batch[1].to(device)  # a_lim
                
                outputs = model(inputs)
                if isinstance(outputs, tuple):
                    outputs = outputs[0]
                
                loss = criterion(outputs, targets)
                val_loss += loss.item()
                val_batches += 1
        
        avg_val_loss = val_loss / max(val_batches, 1)
        
        print(f"Epoch [{epoch+1}/{epochs}] - "
              f"训练损失: {avg_train_loss:.6f}, "
              f"验证损失: {avg_val_loss:.6f}")
        
        # 保存最佳模型
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            best_model_state = model.state_dict().copy()
    
    # 加载最佳模型
    model.load_state_dict(best_model_state)
    print(f"\n最佳验证损失: {best_val_loss:.6f}")
    print("=" * 60)
    
    return model


def analyze_feature_importance(
    model: DLLNNWithPhysics,
    test_loader,
    device: torch.device,
    method: str = "gradient"
) -> Dict:
    """
    分析特征重要性
    
    Args:
        model: 训练好的模型
        test_loader: 测试数据加载器
        device: 计算设备
        method: 计算方法 ("gradient" 或 "perturbation")
        
    Returns:
        特征重要性字典
    """
    print("\n" + "=" * 60)
    print(f"使用{method}法计算SHAP值")
    print("=" * 60)
    
    analyzer = SHAPAnalyzer(model, device)
    
    # 收集所有测试数据
    all_shap_values = []
    all_features = []
    
    for batch in test_loader:
        input_features = batch[0].to(device)  # features
        
        # 计算SHAP值
        if method == "gradient":
            shap_values = analyzer.compute_shap_values_gradient(input_features)
        else:
            shap_values = analyzer.compute_shap_values_perturbation(input_features)
        
        all_shap_values.append(shap_values)
        all_features.append(input_features.cpu().numpy())
    
    # 合并所有批次
    all_shap_values = np.concatenate(all_shap_values, axis=0)
    all_features = np.concatenate(all_features, axis=0)
    
    # 计算平均SHAP值（跨批次）
    mean_shap = all_shap_values.mean(axis=0)
    std_shap = all_shap_values.std(axis=0)
    
    # 特征名称映射
    feature_names = ['spindle_speed', 'axial_depth']
    
    # 构建特征重要性字典
    feature_importance = {}
    for idx, name in enumerate(feature_names):
        feature_importance[name] = {
            'shap_mean': float(mean_shap[idx]),
            'shap_std': float(std_shap[idx]),
            'rank': 0  # 稍后填充
        }
    
    # 计算排名
    sorted_features = sorted(
        feature_importance.items(),
        key=lambda x: x[1]['shap_mean'],
        reverse=True
    )
    for rank, (name, _) in enumerate(sorted_features, 1):
        feature_importance[name]['rank'] = rank
    
    # 打印结果
    print("\n特征重要性排序:")
    for name, info in sorted_features:
        print(f"  {name}: SHAP均值={info['shap_mean']:.6f} ± {info['shap_std']:.6f} (排名 #{info['rank']})")
    
    return feature_importance


def analyze_depth_range_contribution(
    model: DLLNNWithPhysics,
    test_loader,
    device: torch.device
) -> Dict:
    """
    分析不同切深范围下的特征贡献
    
    Args:
        model: 训练好的模型
        test_loader: 测试数据加载器
        device: 计算设备
        
    Returns:
        不同切深范围的特征贡献字典
    """
    print("\n" + "=" * 60)
    print("分析不同切深范围下的特征贡献")
    print("=" * 60)
    
    analyzer = SHAPAnalyzer(model, device)
    
    # 定义切深范围
    depth_ranges = {
        'low_depth_0_2mm': (0.0, 2.0),
        'medium_depth_2_4mm': (2.0, 4.0),
        'high_depth_4_5mm': (4.0, 5.0)
    }
    
    depth_range_analysis = {}
    
    for range_name, (min_depth, max_depth) in depth_ranges.items():
        print(f"\n分析 {range_name} (切深 {min_depth}-{max_depth}mm)...")
        
        range_shap_values = []
        range_features = []
        
        for batch in test_loader:
            input_features = batch[0].to(device)  # features [batch, num_features]
            
            # 提取切深特征（第二个特征是切深）
            # 注意：实际特征索引需要根据数据集定义调整
            depth_values = input_features[:, 1]  # 直接取第二个特征
            
            # 筛选当前范围的数据
            mask = (depth_values >= min_depth) & (depth_values < max_depth)
            
            if mask.sum() > 0:
                filtered_features = input_features[mask]
                
                # 计算SHAP值
                shap_values = analyzer.compute_shap_values_gradient(filtered_features)
                range_shap_values.append(shap_values)
                range_features.append(filtered_features.cpu().numpy())
        
        if len(range_shap_values) > 0:
            # 合并并计算平均值
            range_shap_values = np.concatenate(range_shap_values, axis=0)
            mean_shap = range_shap_values.mean(axis=0)
            
            depth_range_analysis[range_name] = {
                'spindle_speed_shap': float(mean_shap[0]),
                'axial_depth_shap': float(mean_shap[1]),
                'sample_count': int(len(range_shap_values))
            }
            
            print(f"  样本数: {depth_range_analysis[range_name]['sample_count']}")
            print(f"  主轴转速SHAP: {depth_range_analysis[range_name]['spindle_speed_shap']:.6f}")
            print(f"  切深SHAP: {depth_range_analysis[range_name]['axial_depth_shap']:.6f}")
        else:
            print(f"  警告: 该范围内没有样本")
            depth_range_analysis[range_name] = {
                'spindle_speed_shap': 0.0,
                'axial_depth_shap': 0.0,
                'sample_count': 0
            }
    
    return depth_range_analysis


def visualize_feature_importance(feature_importance: Dict, save_path: Path):
    """
    可视化特征重要性
    
    Args:
        feature_importance: 特征重要性字典
        save_path: 保存路径
    """
    print("\n" + "=" * 60)
    print("生成特征重要性可视化")
    print("=" * 60)
    
    # 准备数据
    names = list(feature_importance.keys())
    values = [feature_importance[name]['shap_mean'] for name in names]
    stds = [feature_importance[name]['shap_std'] for name in names]
    
    # 按重要性排序
    sorted_indices = np.argsort(values)[::-1]
    names = [names[i] for i in sorted_indices]
    values = [values[i] for i in sorted_indices]
    stds = [stds[i] for i in sorted_indices]
    
    # 创建图表
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # 绘制条形图
    colors = sns.color_palette("husl", len(names))
    bars = ax.barh(names, values, xerr=stds, color=colors, capsize=5)
    
    # 添加数值标签
    for i, (bar, val) in enumerate(zip(bars, values)):
        ax.text(val + 0.001, bar.get_y() + bar.get_height()/2, 
                f'{val:.4f}', va='center', fontsize=10)
    
    # 设置标签和标题
    ax.set_xlabel('SHAP Value (Mean |SHAP|)', fontsize=12)
    ax.set_ylabel('Feature', fontsize=12)
    ax.set_title('Feature Importance Analysis (SHAP Values)', fontsize=14, fontweight='bold')
    ax.grid(axis='x', alpha=0.3)
    
    plt.tight_layout()
    
    # 保存图表
    save_path.mkdir(parents=True, exist_ok=True)
    fig_path = save_path / 'feature_importance.png'
    plt.savefig(fig_path, dpi=300, bbox_inches='tight')
    print(f"特征重要性图表已保存: {fig_path}")
    
    plt.close()


def main():
    """主函数 - 执行完整的可解释性分析实验"""
    print("\n" + "=" * 60)
    print("实验17: 模型可解释性分析 - SHAP特征重要性")
    print("=" * 60)
    
    # 设置设备
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"\n使用设备: {device}")
    
    # 加载配置
    config = ModelConfig()
    
    # 创建数据加载器
    print("\n加载数据集...")
    train_loader, val_loader, test_loader = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={'num_samples': 500, 'noise_level': 0.08, 'seed': 46},
        batch_size=config.batch_size,
        train_ratio=0.7,
        val_ratio=0.15
    )
    
    # 训练模型
    model = train_ct_ltc_model(
        train_loader=train_loader,
        val_loader=val_loader,
        config=config,
        device=device,
        epochs=10
    )
    
    # 分析特征重要性
    feature_importance = analyze_feature_importance(
        model=model,
        test_loader=test_loader,
        device=device,
        method="gradient"  # 使用梯度法（更快）
    )
    
    # 分析不同切深范围的特征贡献
    depth_range_analysis = analyze_depth_range_contribution(
        model=model,
        test_loader=test_loader,
        device=device
    )
    
    # 可视化特征重要性
    results_dir = Path(__file__).parent / 'results'
    visualize_feature_importance(feature_importance, results_dir)
    
    # 构建结果字典
    results = {
        'timestamp': datetime.now().isoformat(),
        'feature_importance': feature_importance,
        'depth_range_analysis': depth_range_analysis
    }
    
    # 保存结果到JSON
    results_path = results_dir / 'interpretability_results.json'
    with open(results_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print("\n" + "=" * 60)
    print(f"实验结果已保存: {results_path}")
    print("=" * 60)
    
    return results


if __name__ == "__main__":
    set_global_seed(42)
    results = main()
