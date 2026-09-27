"""
实验三十一：与传统稳定性叶瓣图对比实验
将DL-LNN预测结果与Tlusty理论模型的稳定性叶瓣图进行对比，验证模型的物理一致性
"""

import torch
import numpy as np
import json
import os
import time
from typing import Dict, List, Tuple

from models import DLLNNModel
from data_generator import TlustyAnalyticalModel


def generate_stability_lobe_data(
    spindle_speed_range: Tuple[float, float] = (1000, 10000),
    num_speed_points: int = 50,
    num_depth_points: int = 30,
    seed: int = 42
) -> Dict[str, np.ndarray]:
    """
    生成稳定性叶瓣图数据
    
    Returns:
        包含主轴转速、轴向切深、稳定性标签的网格数据
    """
    np.random.seed(seed)
    
    # 生成网格
    spindle_speeds = np.linspace(spindle_speed_range[0], spindle_speed_range[1], num_speed_points)
    axial_depths = np.linspace(0.1, 10.0, num_depth_points)
    
    speed_grid, depth_grid = np.meshgrid(spindle_speeds, axial_depths)
    speed_flat = speed_grid.flatten()
    depth_flat = depth_grid.flatten()
    
    # 使用Tlusty模型计算理论极限切深
    tlusty_model = TlustyAnalyticalModel()
    a_lim_theory = tlusty_model.compute_limiting_depth(speed_flat)
    
    # 稳定性标签（理论）
    stability_theory = (depth_flat > a_lim_theory).astype(int)
    
    return {
        'spindle_speeds': spindle_speeds,
        'axial_depths': axial_depths,
        'speed_grid': speed_grid,
        'depth_grid': depth_grid,
        'a_lim_theory': a_lim_theory.reshape(speed_grid.shape),
        'stability_theory': stability_theory.reshape(speed_grid.shape)
    }


def predict_with_model(
    model: torch.nn.Module,
    speed_grid: np.ndarray,
    depth_grid: np.ndarray,
    device: str = "cpu"
) -> np.ndarray:
    """
    使用模型预测极限切深
    
    Returns:
        预测的极限切深网格
    """
    model.eval()
    
    # 归一化输入
    speed_norm = speed_grid / 10000
    depth_norm = depth_grid / 10
    
    # 展平网格为 [N, 2] 格式
    speed_flat = speed_norm.flatten()
    depth_flat = depth_norm.flatten()
    features = np.stack([speed_flat, depth_flat], axis=-1).astype(np.float32)
    features_tensor = torch.from_numpy(features).to(device)
    
    with torch.no_grad():
        outputs = model(features_tensor)
        if isinstance(outputs, tuple):
            outputs = outputs[0]
        
        # 处理输出维度
        if outputs.dim() > 1 and outputs.shape[-1] != 1:
            outputs = outputs.mean(dim=-1, keepdim=True)
        
        predictions = outputs.cpu().numpy().flatten()
    
    # 恢复为网格形状
    return predictions.reshape(speed_grid.shape)


def main():
    print("=" * 60)
    print("实验三十一：与传统稳定性叶瓣图对比实验")
    print("=" * 60)
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")
    
    # 生成稳定性叶瓣图数据
    print("\n生成稳定性叶瓣图数据...")
    lobe_data = generate_stability_lobe_data(
        spindle_speed_range=(1000, 10000),
        num_speed_points=50,
        num_depth_points=30
    )
    
    speed_grid = lobe_data['speed_grid']
    depth_grid = lobe_data['depth_grid']
    a_lim_theory = lobe_data['a_lim_theory']
    stability_theory = lobe_data['stability_theory']
    
    # 训练DL-LNN模型
    print("训练DL-LNN模型...")
    from data_generator import SyntheticChatterDataset
    from torch.utils.data import DataLoader
    
    train_dataset = SyntheticChatterDataset(num_samples=5000, seed=42)
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    
    model = DLLNNModel(input_dim=7, hidden_dim=64)
    model = model.to(device)
    
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)
    criterion = torch.nn.MSELoss()
    
    model.train()
    for epoch in range(80):
        epoch_loss = 0.0
        for features, labels, _ in train_loader:
            features = features.to(device)
            labels = labels.to(device)
            
            optimizer.zero_grad()
            outputs = model(features)
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item()
    
    print("模型训练完成")
    
    # 使用模型预测
    print("生成模型预测的稳定性叶瓣图...")
    a_lim_predicted = predict_with_model(model, speed_grid, depth_grid, device)
    
    # 计算稳定性标签（基于预测值）
    stability_predicted = (depth_grid > a_lim_predicted).astype(int)
    
    # 计算对比指标
    print("计算对比指标...")
    
    # 1. 极限切深误差
    a_lim_error = np.abs(a_lim_predicted - a_lim_theory)
    mae_a_lim = float(np.mean(a_lim_error))
    rmse_a_lim = float(np.sqrt(np.mean(a_lim_error ** 2)))
    
    # 2. 稳定性分类准确率
    accuracy = float(np.mean(stability_predicted == stability_theory))
    
    # 3. 混淆矩阵
    tp = int(np.sum((stability_predicted == 1) & (stability_theory == 1)))
    tn = int(np.sum((stability_predicted == 0) & (stability_theory == 0)))
    fp = int(np.sum((stability_predicted == 1) & (stability_theory == 0)))
    fn = int(np.sum((stability_predicted == 0) & (stability_theory == 1)))
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1_score = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    # 4. 边界区域分析（稳定性边界附近的预测）
    boundary_mask = np.abs(depth_grid - a_lim_theory) < 0.5  # 边界附近0.5mm范围内
    boundary_accuracy = float(np.mean(stability_predicted[boundary_mask] == stability_theory[boundary_mask]))
    
    # 5. 不同转速区间的性能
    speed_intervals = [
        (1000, 3000, "低速区"),
        (3000, 6000, "中速区"),
        (6000, 10000, "高速区")
    ]
    
    interval_results = {}
    for low, high, name in speed_intervals:
        mask = (speed_grid >= low) & (speed_grid < high)
        if np.sum(mask) > 0:
            interval_mae = float(np.mean(a_lim_error[mask]))
            interval_acc = float(np.mean(stability_predicted[mask] == stability_theory[mask]))
            interval_results[name] = {
                "mae": round(interval_mae, 4),
                "accuracy": round(interval_acc, 4),
                "num_points": int(np.sum(mask))
            }
    
    # 保存结果
    output_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, 'stability_lobes_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "与传统稳定性叶瓣图对比实验",
            "grid_size": {
                "num_speed_points": int(speed_grid.shape[1]),
                "num_depth_points": int(speed_grid.shape[0])
            },
            "metrics": {
                "mae_a_lim": round(mae_a_lim, 4),
                "rmse_a_lim": round(rmse_a_lim, 4),
                "stability_accuracy": round(accuracy, 4),
                "boundary_accuracy": round(boundary_accuracy, 4),
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1_score": round(f1_score, 4)
            },
            "confusion_matrix": {
                "tp": tp,
                "tn": tn,
                "fp": fp,
                "fn": fn
            },
            "interval_analysis": interval_results,
            "lobe_data_summary": {
                "speed_range": [float(speed_grid.min()), float(speed_grid.max())],
                "depth_range": [float(depth_grid.min()), float(depth_grid.max())],
                "a_lim_theory_range": [float(a_lim_theory.min()), float(a_lim_theory.max())],
                "a_lim_predicted_range": [float(a_lim_predicted.min()), float(a_lim_predicted.max())]
            }
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存至: {output_file}")
    print(f"\n对比结果:")
    print(f"  极限切深MAE: {mae_a_lim:.4f} mm")
    print(f"  稳定性分类准确率: {accuracy:.4f}")
    print(f"  边界区域准确率: {boundary_accuracy:.4f}")
    print(f"  F1分数: {f1_score:.4f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
