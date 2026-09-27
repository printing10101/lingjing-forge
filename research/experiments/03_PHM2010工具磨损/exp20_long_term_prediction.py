"""
长时域预测稳定性实验
测试模型在长时间序列上的预测稳定性，评估是否会发散

实验设计：
1. 生成长序列（1000-5000步）的铣削信号
2. 使用模型进行递推预测（autoregressive prediction）
3. 分析预测误差随时间的变化趋势
4. 对比DL-LNN与LSTM、Transformer的长时域稳定性
5. 评估指标：误差增长率、发散时间、长期R²
"""

import sys
import json
import torch
import torch.nn as nn
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from config import ModelConfig
from models import DLLNNWithPhysics, BaselineLSTM, BaselineTransformer
from data_generator import Industrial6061T6Dataset, create_dataloaders
from metrics import ChatterMetrics


def generate_long_sequence(
    length: int = 2000,
    stable: bool = True,
    seed: int = 42
) -> Tuple[np.ndarray, np.ndarray]:
    """
    生成长序列铣削信号
    
    Args:
        length: 序列长度
        stable: 是否为稳定切削
        seed: 随机种子
    
    Returns:
        features: 特征序列 (length, input_dim)
        targets: 目标序列 (length,)
    """
    np.random.seed(seed)
    
    # 生成时间序列
    t = np.linspace(0, length * 0.001, length)  # 采样频率1kHz
    
    # 主轴转速和切深（缓慢变化）
    spindle_speed = 5000 + 500 * np.sin(2 * np.pi * t / 10)  # rpm
    axial_depth = 2.5 + 0.5 * np.sin(2 * np.pi * t / 20)  # mm
    
    # 生成切削力信号
    num_teeth = 4
    tooth_freq = spindle_speed * num_teeth / 60  # Hz
    
    force_signal = np.zeros(length)
    for i in range(length):
        # 刀齿通过频率
        force_signal[i] = 10 * np.sin(2 * np.pi * tooth_freq[i] * t[i])
        
        if not stable:
            # 添加颤振成分
            chatter_freq = 850  # Hz
            force_signal[i] += 5 * np.sin(2 * np.pi * chatter_freq * t[i])
        
        # 添加噪声
        force_signal[i] += 0.5 * np.random.randn()
    
    # 特征：主轴转速、切深（2维，与训练数据一致）
    features = np.column_stack([
        spindle_speed / 10000,  # 归一化
        axial_depth / 10
    ])
    
    # 目标：极限切深（简化模型）
    if stable:
        targets = 3.0 + 0.5 * np.sin(2 * np.pi * t / 50)
    else:
        targets = 2.0 + 0.3 * np.sin(2 * np.pi * t / 30)
    
    return features, targets


def autoregressive_prediction(
    model: nn.Module,
    initial_features: np.ndarray,
    initial_targets: np.ndarray,
    prediction_length: int,
    device: torch.device
) -> np.ndarray:
    """
    递推预测（autoregressive prediction）
    
    Args:
        model: 预测模型
        initial_features: 初始特征序列
        initial_targets: 初始目标序列
        prediction_length: 预测步数
        device: 计算设备
    
    Returns:
        predictions: 预测结果 (prediction_length,)
    """
    model.eval()
    predictions = []
    
    current_features = initial_features.copy()
    current_targets = initial_targets.copy()
    
    with torch.no_grad():
        for _ in range(prediction_length):
            # 准备输入：取最后一个时间步的特征，保持 2D [1, input_dim] 与训练一致
            x = torch.FloatTensor(current_features[-1:]).to(device)
            
            # 预测
            output = model(x)
            pred = output[0] if isinstance(output, tuple) else output
            pred_value = pred.cpu().numpy().flatten()[-1]
            predictions.append(pred_value)
            
            # 更新序列（简化：使用预测值作为下一步输入）
            new_feature = current_features[-1].copy()
            new_feature[1] = pred_value / 10  # 更新切深特征
            current_features = np.vstack([current_features[1:], new_feature])
    
    return np.array(predictions)


def compute_stability_metrics(
    predictions: np.ndarray,
    targets: np.ndarray,
    window_size: int = 100
) -> Dict:
    """
    计算稳定性指标
    
    Args:
        predictions: 预测序列
        targets: 真实序列
        window_size: 滑动窗口大小
    
    Returns:
        稳定性指标字典
    """
    errors = np.abs(predictions - targets)
    
    # 分段计算误差
    num_segments = len(errors) // window_size
    segment_errors = []
    segment_r2 = []
    
    for i in range(num_segments):
        start = i * window_size
        end = start + window_size
        
        seg_pred = predictions[start:end]
        seg_target = targets[start:end]
        seg_error = errors[start:end]
        
        segment_errors.append(float(np.mean(seg_error)))
        
        # 计算R²
        ss_res = np.sum((seg_target - seg_pred) ** 2)
        ss_tot = np.sum((seg_target - np.mean(seg_target)) ** 2)
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
        segment_r2.append(float(r2))
    
    # 误差增长率
    if len(segment_errors) > 1:
        error_growth_rate = (segment_errors[-1] - segment_errors[0]) / len(segment_errors)
    else:
        error_growth_rate = 0
    
    # 发散时间（误差超过阈值的时间点）
    threshold = np.mean(targets) * 0.2  # 20%阈值
    divergence_time = None
    for i, err in enumerate(errors):
        if err > threshold:
            divergence_time = i
            break
    
    return {
        "segment_errors": segment_errors,
        "segment_r2": segment_r2,
        "error_growth_rate": float(error_growth_rate),
        "divergence_time": divergence_time,
        "final_error": float(segment_errors[-1]) if segment_errors else 0,
        "avg_error": float(np.mean(errors))
    }


def run_long_term_prediction_experiment():
    """运行长时域预测稳定性实验"""
    print("=" * 60)
    print("长时域预测稳定性实验")
    print("=" * 60)
    
    # 配置
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"使用设备: {device}")
    
    config = ModelConfig()
    config.input_dim = 7  # 与 config.py 默认值及论文第3节声明的 7 维物理参数特征一致
    config.hidden_dim = 64
    config.num_layers = 3
    config.output_dim = 1
    config.ltc_dt = 0.01
    
    # 实验参数
    sequence_lengths = [1000, 2000, 3000, 4000, 5000]
    model_names = ["DL-LNN", "LSTM", "Transformer"]
    
    results = {
        "timestamp": datetime.now().isoformat(),
        "sequence_lengths": sequence_lengths,
        "models": model_names,
        "results": {}
    }
    
    # 加载数据（用于初始化）
    print("\n加载数据...")
    dataset = Industrial6061T6Dataset(num_samples=500, noise_level=0.08, seed=46)
    train_loader, val_loader, test_loader = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={'num_samples': 500, 'noise_level': 0.08, 'seed': 46},
        batch_size=32,
        train_ratio=0.7,
        val_ratio=0.15
    )
    
    # 初始化模型
    print("初始化模型...")
    models = {}
    
    # DL-LNN
    models["DL-LNN"] = DLLNNWithPhysics(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
        dt=config.ltc_dt,
        dropout=0.1
    ).to(device)
    
    # LSTM
    models["LSTM"] = BaselineLSTM(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim
    ).to(device)
    
    # Transformer
    models["Transformer"] = BaselineTransformer(
        input_dim=config.input_dim,
        d_model=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim
    ).to(device)
    
    # 简单训练（仅用于演示）
    print("训练模型（简化版本）...")
    optimizer = torch.optim.Adam(
        list(models["DL-LNN"].parameters()) + 
        list(models["LSTM"].parameters()) + 
        list(models["Transformer"].parameters()),
        lr=0.001
    )
    criterion = nn.MSELoss()
    
    for epoch in range(5):  # 简化训练
        for model_name, model in models.items():
            model.train()
            for batch in train_loader:
                x, y = batch[0].to(device), batch[1].to(device)
                output = model(x)
                # DLLNNWithPhysics 返回元组，其他模型返回单个张量
                pred = output[0] if isinstance(output, tuple) else output
                if pred.shape != y.shape:
                    pred = pred.view_as(y)
                loss = criterion(pred, y)
                
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
    
    # 运行实验
    print("\n开始长时域预测实验...")
    for length in sequence_lengths:
        print(f"\n序列长度: {length}")
        results["results"][length] = {}
        
        # 生成稳定切削信号
        features, targets = generate_long_sequence(
            length=length,
            stable=True,
            seed=42
        )
        
        # 初始序列（前100步）
        initial_length = 100
        initial_features = features[:initial_length]
        initial_targets = targets[:initial_length]
        
        # 预测剩余部分
        prediction_length = length - initial_length
        
        for model_name in model_names:
            print(f"  模型: {model_name}")
            model = models[model_name]
            
            # 递推预测
            predictions = autoregressive_prediction(
                model=model,
                initial_features=initial_features,
                initial_targets=initial_targets,
                prediction_length=prediction_length,
                device=device
            )
            
            # 真实值
            true_targets = targets[initial_length:]
            
            # 计算稳定性指标
            stability_metrics = compute_stability_metrics(predictions, true_targets)
            
            results["results"][length][model_name] = {
                "avg_error": stability_metrics["avg_error"],
                "final_error": stability_metrics["final_error"],
                "error_growth_rate": stability_metrics["error_growth_rate"],
                "divergence_time": stability_metrics["divergence_time"],
                "segment_errors": stability_metrics["segment_errors"][:5],  # 只保存前5段
                "segment_r2": stability_metrics["segment_r2"][:5]
            }
            
            print(f"    平均误差: {stability_metrics['avg_error']:.4f}")
            print(f"    最终误差: {stability_metrics['final_error']:.4f}")
            print(f"    误差增长率: {stability_metrics['error_growth_rate']:.6f}")
    
    # 保存结果
    output_path = Path(__file__).parent / 'results' / 'long_term_prediction_results.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n实验结果已保存至: {output_path}")
    print("=" * 60)
    
    return results


if __name__ == '__main__':
    run_long_term_prediction_experiment()
