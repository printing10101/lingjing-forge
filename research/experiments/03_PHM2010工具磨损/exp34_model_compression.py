"""
实验三十四：模型压缩与边缘部署实验
评估模型量化、剪枝后的性能损失，验证边缘设备部署可行性
"""

import torch
import torch.nn.utils.prune as prune
import numpy as np
import json
import os
import time
import copy
from typing import Dict, List
from torch.utils.data import DataLoader

from models import DLLNNModel
from metrics import ChatterMetrics
from data_generator import SyntheticChatterDataset


def quantize_model(model: torch.nn.Module, bits: int = 8) -> torch.nn.Module:
    """
    模型量化
    将浮点参数转换为低精度表示
    """
    # 创建模型副本
    quantized_model = DLLNNModel(
        input_dim=model.input_dim,
        hidden_dim=model.hidden_dim,
        num_layers=model.num_layers,
        output_dim=model.output_dim,
        dt=model.dt
    )
    quantized_model.load_state_dict(model.state_dict())
    
    for name, module in quantized_model.named_modules():
        if isinstance(module, (torch.nn.Linear, torch.nn.Conv2d)):
            # 动态量化
            weight = module.weight.data
            scale = (weight.max() - weight.min()) / (2**bits - 1)
            zero_point = 2**(bits-1) - weight.max() / scale
            weight_int = torch.clamp(torch.round(weight / scale + zero_point), 0, 2**bits - 1)
            weight_dequant = (weight_int - zero_point) * scale
            module.weight.data = weight_dequant
    
    return quantized_model


def prune_model(model: torch.nn.Module, amount: float = 0.3) -> torch.nn.Module:
    """
    模型剪枝
    移除不重要的权重连接
    """
    # 创建模型副本
    pruned_model = DLLNNModel(
        input_dim=model.input_dim,
        hidden_dim=model.hidden_dim,
        num_layers=model.num_layers,
        output_dim=model.output_dim,
        dt=model.dt
    )
    pruned_model.load_state_dict(model.state_dict())
    
    for name, module in pruned_model.named_modules():
        if isinstance(module, (torch.nn.Linear, torch.nn.Conv2d)):
            # L1未结构化剪枝
            prune.l1_unstructured(module, name='weight', amount=amount)
            # 移除剪枝掩码，永久化剪枝
            prune.remove(module, 'weight')
    
    return pruned_model


def evaluate_model(
    model: torch.nn.Module,
    test_loader: DataLoader,
    device: str = "cpu"
) -> Dict[str, float]:
    """评估模型性能"""
    model.eval()
    all_preds = []
    all_labels = []
    
    with torch.no_grad():
        for features, labels, _ in test_loader:
            features = features.to(device)
            outputs = model(features)
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            
            all_preds.append(outputs.cpu().numpy())
            all_labels.append(labels.numpy())
    
    all_preds = np.concatenate(all_preds)
    all_labels = np.concatenate(all_labels)
    
    metrics = ChatterMetrics()
    results = metrics.compute_all(all_labels, all_preds)
    
    return {
        "mae": round(float(results['mae']), 4),
        "rmse": round(float(results['rmse']), 4),
        "r2": round(float(results.get('r2', 0)), 4),
        "pcc": round(float(results.get('pcc', 0)), 4)
    }


def count_parameters(model: torch.nn.Module) -> int:
    """计算模型参数量"""
    return sum(p.numel() for p in model.parameters())


def estimate_model_size(model: torch.nn.Module) -> float:
    """估算模型大小（MB）"""
    param_size = sum(p.nelement() * p.element_size() for p in model.parameters())
    buffer_size = sum(b.nelement() * b.element_size() for b in model.buffers())
    return (param_size + buffer_size) / 1024 / 1024


def main():
    print("=" * 60)
    print("实验三十四：模型压缩与边缘部署实验")
    print("=" * 60)
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")
    
    # 准备数据集
    print("\n准备数据集...")
    dataset = SyntheticChatterDataset(num_samples=5000, seed=42)
    
    train_size = int(0.8 * len(dataset))
    test_size = len(dataset) - train_size
    train_dataset, test_dataset = torch.utils.data.random_split(dataset, [train_size, test_size])
    
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)
    
    all_results = {}
    
    # 实验1：原始模型基线
    print("\n实验1：原始模型基线")
    print("-" * 40)
    
    original_model = DLLNNModel(input_dim=7, hidden_dim=64)
    original_model = original_model.to(device)
    
    # 训练原始模型
    optimizer = torch.optim.Adam(original_model.parameters(), lr=0.001, weight_decay=1e-4)
    criterion = torch.nn.MSELoss()
    
    original_model.train()
    for epoch in range(80):
        epoch_loss = 0.0
        for features, labels, _ in train_loader:
            features = features.to(device)
            labels = labels.to(device)
            
            optimizer.zero_grad()
            outputs = original_model(features)
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item()
    
    # 评估原始模型
    original_results = evaluate_model(original_model, test_loader, device)
    original_params = count_parameters(original_model)
    original_size = estimate_model_size(original_model)
    
    all_results["original"] = {
        "metrics": original_results,
        "parameters": original_params,
        "size_mb": round(original_size, 2)
    }
    
    print(f"  参数量: {original_params:,}")
    print(f"  模型大小: {original_size:.2f} MB")
    print(f"  MAE: {original_results['mae']:.4f}, PCC: {original_results['pcc']:.4f}")
    
    # 实验2：模型量化
    print("\n实验2：模型量化")
    print("-" * 40)
    
    quantization_bits = [32, 16, 8, 4]
    quantization_results = {}
    
    for bits in quantization_bits:
        print(f"  量化位数: {bits}-bit")
        
        if bits == 32:
            # 32位就是原始模型
            quantized_model = original_model
        else:
            quantized_model = quantize_model(original_model, bits)
            quantized_model = quantized_model.to(device)
        
        # 评估
        quant_results = evaluate_model(quantized_model, test_loader, device)
        quant_params = count_parameters(quantized_model)
        quant_size = estimate_model_size(quantized_model)
        
        # 计算性能损失
        mae_degradation = (quant_results['mae'] - original_results['mae']) / (original_results['mae'] + 1e-8) * 100
        pcc_degradation = (original_results['pcc'] - quant_results['pcc']) / (original_results['pcc'] + 1e-8) * 100
        
        quantization_results[f"{bits}bit"] = {
            "metrics": quant_results,
            "parameters": quant_params,
            "size_mb": round(quant_size, 2),
            "mae_degradation_pct": round(mae_degradation, 2),
            "pcc_degradation_pct": round(pcc_degradation, 2)
        }
        
        print(f"    模型大小: {quant_size:.2f} MB")
        print(f"    MAE: {quant_results['mae']:.4f} (退化 {mae_degradation:.2f}%)")
        print(f"    PCC: {quant_results['pcc']:.4f} (退化 {pcc_degradation:.2f}%)")
    
    all_results["quantization"] = quantization_results
    
    # 实验3：模型剪枝
    print("\n实验3：模型剪枝")
    print("-" * 40)
    
    prune_ratios = [0.0, 0.1, 0.3, 0.5, 0.7]
    pruning_results = {}
    
    for ratio in prune_ratios:
        print(f"  剪枝比例: {ratio*100:.0f}%")
        
        if ratio == 0.0:
            pruned_model = original_model
        else:
            pruned_model = prune_model(original_model, ratio)
            pruned_model = pruned_model.to(device)
        
        # 评估
        prune_results = evaluate_model(pruned_model, test_loader, device)
        prune_params = count_parameters(pruned_model)
        prune_size = estimate_model_size(pruned_model)
        
        # 计算非零参数比例
        non_zero_params = sum(torch.count_nonzero(p).item() for p in pruned_model.parameters())
        sparsity = 1.0 - non_zero_params / prune_params
        
        # 计算性能损失
        mae_degradation = (prune_results['mae'] - original_results['mae']) / (original_results['mae'] + 1e-8) * 100
        pcc_degradation = (original_results['pcc'] - prune_results['pcc']) / (original_results['pcc'] + 1e-8) * 100
        
        pruning_results[f"prune_{int(ratio*100)}"] = {
            "metrics": prune_results,
            "parameters": prune_params,
            "non_zero_params": non_zero_params,
            "sparsity": round(sparsity, 4),
            "size_mb": round(prune_size, 2),
            "mae_degradation_pct": round(mae_degradation, 2),
            "pcc_degradation_pct": round(pcc_degradation, 2)
        }
        
        print(f"    稀疏度: {sparsity:.2%}")
        print(f"    MAE: {prune_results['mae']:.4f} (退化 {mae_degradation:.2f}%)")
        print(f"    PCC: {prune_results['pcc']:.4f} (退化 {pcc_degradation:.2f}%)")
    
    all_results["pruning"] = pruning_results
    
    # 实验4：边缘设备推理延迟测试
    print("\n实验4：边缘设备推理延迟测试")
    print("-" * 40)
    
    # 模拟不同模型配置
    model_configs = [
        ("original", original_model),
        ("quantized_8bit", quantize_model(original_model, 8)),
        ("pruned_30", prune_model(original_model, 0.3))
    ]
    
    inference_results = {}
    
    for config_name, model in model_configs:
        model = model.to(device)
        model.eval()
        
        # 准备测试输入
        test_input = torch.randn(1, 2).to(device)
        
        # 预热
        with torch.no_grad():
            for _ in range(10):
                _ = model(test_input)
        
        # 测量推理延迟
        latencies = []
        num_runs = 100
        
        for _ in range(num_runs):
            start_time = time.perf_counter()
            with torch.no_grad():
                _ = model(test_input)
            end_time = time.perf_counter()
            latencies.append((end_time - start_time) * 1000)  # 转换为ms
        
        avg_latency = np.mean(latencies)
        std_latency = np.std(latencies)
        p99_latency = np.percentile(latencies, 99)
        
        # 计算吞吐量
        throughput = 1000 / avg_latency  # samples per second
        
        inference_results[config_name] = {
            "avg_latency_ms": round(avg_latency, 3),
            "std_latency_ms": round(std_latency, 3),
            "p99_latency_ms": round(p99_latency, 3),
            "throughput_sps": round(throughput, 2)
        }
        
        print(f"  {config_name}:")
        print(f"    平均延迟: {avg_latency:.3f} ms")
        print(f"    P99延迟: {p99_latency:.3f} ms")
        print(f"    吞吐量: {throughput:.2f} samples/s")
    
    all_results["inference_latency"] = inference_results
    
    # 保存结果
    output_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, 'model_compression_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "模型压缩与边缘部署实验",
            "results": all_results
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存至: {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
