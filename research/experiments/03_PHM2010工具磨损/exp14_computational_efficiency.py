"""
实验14: 计算效率分析
对比各模型的计算资源消耗，包括：
- 模型参数量
- FLOPs（浮点运算次数）估算
- 训练时间（每个epoch平均时间）
- 推理时间（单样本预测时间）
- 内存占用（GPU/CPU）
"""

import sys
import json
import torch
import time
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
# 添加项目根目录（python/）到 path，用于导入 app 模块
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from training.reproducibility import set_global_seed

from config import ModelConfig, ExperimentConfig
from models import (
    DLLNNModel,
    DLLNNWithPhysics,
    BaselineLSTM,
    BaselineTransformer,
    BaselinePINN,
    BaselineBPNN,
    BaselineCNN,
    BaselineGRU,
    BaselinegPINN,
    BaselinePeRCNN,
    create_model,
)
from data_generator import Industrial6061T6Dataset, create_dataloaders


def count_parameters(model: torch.nn.Module) -> int:
    """
    统计模型可训练参数总量

    Args:
        model: PyTorch模型实例

    Returns:
        参数总数（int）
    """
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def estimate_flops(model: torch.nn.Module, input_tensor: torch.Tensor) -> int:
    """
    通过注册前向传播钩子估算FLOPs（浮点运算次数）

    原理：对每个 nn.Module 的前向传播注册钩子，统计其中乘法运算的次数，
    然后乘以2（一次乘法对应一次加法）得到总FLOPs。

    Args:
        model: PyTorch模型实例
        input_tensor: 输入张量

    Returns:
        估算的FLOPs数量
    """
    total_flops = [0]

    def hook_fn(module, input, output):
        """钩子函数：统计当前层的乘法运算次数"""
        flops = 0
        if isinstance(module, torch.nn.Linear):
            # Linear层: output = input @ weight.T + bias
            # 乘法次数 = batch_size * in_features * out_features
            flops += input[0].numel() * module.out_features
        elif isinstance(module, torch.nn.Conv1d):
            # Conv1d: 乘法次数 = batch * out_channels * output_length * in_channels * kernel_size
            if isinstance(output, torch.Tensor):
                flops += output.numel() * module.in_channels * module.kernel_size[0] // module.groups
        elif isinstance(module, (torch.nn.LSTM, torch.nn.GRU)):
            # RNN层：粗略估算，每个时间步有若干矩阵乘法
            if isinstance(output, tuple):
                hidden = output[0]
                # 简化估算：hidden_size * hidden_size * 4 (门控) * seq_len * batch
                if isinstance(module, torch.nn.LSTM):
                    flops += hidden.numel() * module.hidden_size * 4
                else:
                    flops += hidden.numel() * module.hidden_size * 3
        elif isinstance(module, torch.nn.TransformerEncoderLayer):
            # Transformer层：自注意力 + FFN
            if isinstance(output, torch.Tensor):
                d_model = output.shape[-1]
                # 自注意力 QKV投影 + 注意力计算 + 输出投影 + FFN
                flops += d_model * d_model * 3 + d_model * d_model + d_model * d_model * 4 * 2
        elif isinstance(module, (torch.nn.ReLU, torch.nn.Tanh, torch.nn.Sigmoid)):
            # 激活函数：每个元素一次运算
            if isinstance(output, torch.Tensor):
                flops += output.numel()

        total_flops[0] += flops

    # 注册钩子
    handles = []
    for module in model.modules():
        handles.append(module.register_forward_hook(hook_fn))

    # 执行前向传播
    model.eval()
    with torch.no_grad():
        _ = model(input_tensor)

    # 移除钩子
    for handle in handles:
        handle.remove()

    return total_flops[0]


def measure_training_time(
    model: torch.nn.Module, train_loader, num_epochs: int = 10, device: str = "cpu"
) -> Dict[str, float]:
    """
    测量模型训练时间，记录每个epoch的耗时

    Args:
        model: 模型实例
        train_loader: 训练数据加载器
        num_epochs: 训练轮数
        device: 计算设备

    Returns:
        包含每个epoch时间和平均时间的字典
    """
    model = model.to(device)
    model.train()

    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = torch.nn.MSELoss()

    epoch_times = []

    for epoch in range(num_epochs):
        epoch_start = time.time()
        epoch_loss = 0.0
        num_batches = 0

        for batch_features, batch_labels, batch_physics in train_loader:
            batch_features = batch_features.to(device)
            batch_labels = batch_labels.to(device)

            optimizer.zero_grad()

            # 前向传播
            output = model(batch_features)
            # DLLNNWithPhysics返回元组，取第一个元素
            if isinstance(output, tuple):
                output = output[0]

            loss = criterion(output, batch_labels)
            loss.backward()
            optimizer.step()

            epoch_loss += loss.item()
            num_batches += 1

        epoch_time = time.time() - epoch_start
        epoch_times.append(epoch_time)

    avg_epoch_time = sum(epoch_times) / len(epoch_times)

    return {
        "epoch_times": epoch_times,
        "avg_epoch_time": round(avg_epoch_time, 4),
        "total_train_time": round(sum(epoch_times), 4),
    }


def measure_inference_time(
    model: torch.nn.Module, input_tensor: torch.Tensor, num_runs: int = 100, device: str = "cpu"
) -> float:
    """
    测量模型推理时间（单样本预测）

    执行num_runs次前向传播，取平均时间（毫秒）。
    先做10次warmup再正式计时，排除首次运行的初始化开销。

    Args:
        model: 模型实例
        input_tensor: 输入张量（单样本或批量）
        num_runs: 测量次数
        device: 计算设备

    Returns:
        平均推理时间（毫秒）
    """
    model = model.to(device)
    model.eval()
    input_tensor = input_tensor.to(device)

    # warmup：排除初始化开销
    with torch.no_grad():
        for _ in range(10):
            _ = model(input_tensor)

    # 正式计时
    times = []
    with torch.no_grad():
        for _ in range(num_runs):
            start = time.perf_counter()
            _ = model(input_tensor)
            end = time.perf_counter()
            times.append((end - start) * 1000)  # 转换为毫秒

    avg_time = sum(times) / len(times)
    return round(avg_time, 4)


def measure_memory_usage(model: torch.nn.Module, device: str = "cpu") -> float:
    """
    测量模型内存占用

    如果GPU可用，通过CUDA API获取显存占用；
    否则通过参数量估算CPU内存占用。

    Args:
        model: 模型实例
        device: 计算设备

    Returns:
        内存占用（MB）
    """
    model = model.to(device)

    if device == "cuda" and torch.cuda.is_available():
        # GPU显存：重置峰值统计，然后前向传播一次触发分配
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.empty_cache()

        # 创建一个输入并执行前向传播以触发显存分配
        dummy_input = torch.randn(1, 2, device=device)
        with torch.no_grad():
            _ = model(dummy_input)

        # 获取当前分配的显存
        memory_bytes = torch.cuda.memory_allocated()
        memory_mb = memory_bytes / (1024 * 1024)
    else:
        # CPU内存：通过参数量 * 4字节(float32) 估算
        param_count = sum(p.numel() for p in model.parameters())
        memory_bytes = param_count * 4  # float32 = 4 bytes
        # 加上优化器状态（Adam需要2倍额外内存用于一阶和二阶矩）
        memory_bytes *= 3  # 参数 + 一阶矩 + 二阶矩
        memory_mb = memory_bytes / (1024 * 1024)

    return round(memory_mb, 2)


def run_computational_efficiency_experiment():
    """
    运行计算效率分析实验的主函数

    实验流程：
    1. 创建数据加载器
    2. 对每个模型分别测量参数量、FLOPs、训练时间、推理时间、内存占用
    3. 将结果保存为JSON文件
    """
    print("=" * 60)
    print("实验14: 计算效率分析")
    print("=" * 60)

    # 确定计算设备
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n计算设备: {device}")
    if device == "cuda":
        print(f"GPU型号: {torch.cuda.get_device_name(0)}")

    # 创建数据加载器（使用自采6061-T6工业数据集）
    print("\n[1/3] 创建数据加载器...")
    train_loader, val_loader, test_loader = create_dataloaders(
        Industrial6061T6Dataset,
        dataset_params={"num_samples": 500, "noise_level": 0.08, "seed": 46},
        batch_size=32,
        train_ratio=0.7,
        val_ratio=0.15,
        seed=42,
    )
    print(f"  训练集批次数: {len(train_loader)}")
    print(f"  验证集批次数: {len(val_loader)}")
    print(f"  测试集批次数: {len(test_loader)}")

    # 定义要对比的模型列表（仅包含create_model支持的模型）
    model_names = [
        "DL-LNN",  # DLLNNWithPhysics（带物理分支的完整模型）
        "LTC",  # DLLNNModel（纯LTC模型）
        "LSTM",  # 基线LSTM
        "Transformer",  # 基线Transformer
        "PINN",  # 基线PINN
        "BPNN",  # 基线BPNN
    ]

    # 获取模型配置
    config = ExperimentConfig()
    model_config = config.model

    # 创建用于FLOPs和推理时间测试的标准输入（放在正确设备上）
    sample_input = torch.randn(1, model_config.input_dim, device=device)

    # 存储所有模型的结果
    results = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "device": device,
        "dataset": "自采6061-T6工业数据集",
        "num_train_samples": 350,  # 500 * 0.7
        "batch_size": 32,
        "train_epochs": 10,
        "inference_runs": 100,
        "models": {},
    }

    print(f"\n[2/3] 开始对比 {len(model_names)} 个模型的计算效率...")
    print("-" * 60)

    for model_name in model_names:
        print(f"\n>>> 模型: {model_name}")

        try:
            # 创建模型实例
            model = create_model(model_name, config)
            model = model.to(device)

            # 1. 统计参数量
            param_count = count_parameters(model)
            print(f"  参数量: {param_count:,}")

            # 2. 估算FLOPs
            flops = estimate_flops(model, sample_input)
            print(f"  FLOPs: {flops:,}")

            # 3. 测量训练时间（10个epoch）
            # 每次训练前重新创建模型，避免状态累积
            fresh_model = create_model(model_name, config)
            print(f"  训练中（10个epoch）...", end="", flush=True)
            train_result = measure_training_time(fresh_model, train_loader, num_epochs=10, device=device)
            print(f" 完成")
            print(f"  平均每epoch时间: {train_result['avg_epoch_time']:.4f}s")

            # 4. 测量推理时间（100次前向传播取平均）
            test_model = create_model(model_name, config)
            inference_time = measure_inference_time(test_model, sample_input, num_runs=100, device=device)
            print(f"  推理时间: {inference_time:.4f}ms")

            # 5. 测量内存占用
            mem_model = create_model(model_name, config)
            memory_mb = measure_memory_usage(mem_model, device=device)
            print(f"  内存占用: {memory_mb:.2f}MB")

            # 保存该模型的结果
            results["models"][model_name] = {
                "parameters": param_count,
                "flops": flops,
                "train_time_per_epoch": train_result["avg_epoch_time"],
                "train_time_per_epoch_list": train_result["epoch_times"],
                "total_train_time": train_result["total_train_time"],
                "inference_time_ms": inference_time,
                "memory_mb": memory_mb,
            }

        except Exception as e:
            print(f"  [错误] {model_name} 实验失败: {str(e)}")
            results["models"][model_name] = {
                "error": str(e),
            }

    # 保存结果到JSON文件
    print("\n" + "=" * 60)
    print("[3/3] 保存实验结果...")

    results_dir = project_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    output_path = results_dir / "computational_efficiency_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"  结果已保存到: {output_path}")

    # 打印汇总表
    print("\n" + "=" * 60)
    print("计算效率对比汇总表")
    print("=" * 60)
    header = f"{'模型':<14} {'参数量':>10} {'FLOPs':>12} {'训练/epoch(s)':>14} {'推理(ms)':>10} {'内存(MB)':>10}"
    print(header)
    print("-" * len(header))

    for name, info in results["models"].items():
        if "error" in info:
            print(f"{name:<14} {'ERROR':>10}")
            continue
        print(
            f"{name:<14} "
            f"{info['parameters']:>10,} "
            f"{info['flops']:>12,} "
            f"{info['train_time_per_epoch']:>14.4f} "
            f"{info['inference_time_ms']:>10.4f} "
            f"{info['memory_mb']:>10.2f}"
        )

    print("=" * 60)
    print("实验完成！")

    return results


if __name__ == "__main__":
    set_global_seed(42)
    run_computational_efficiency_experiment()
