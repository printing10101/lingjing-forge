"""
实验24: 实时推理延迟与吞吐量分析
针对工业实时监控系统的关键性能指标：
- 单样本推理延迟（latency）：均值、标准差、P50、P95、P99
- 批量推理吞吐量（throughput）：samples/second
- 连续推理流处理能力：模拟实时数据流的持续推理
- 不同批量大小下的性能表现
- 推理延迟的稳定性分析
"""

import sys
import json
import torch
import time
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
import statistics

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from config import ExperimentConfig
from models import create_model


def measure_latency_statistics(
    model: torch.nn.Module,
    input_tensor: torch.Tensor,
    num_runs: int = 500,
    device: str = "cpu"
) -> Dict[str, float]:
    """
    测量单样本推理延迟的统计指标
    
    Args:
        model: PyTorch模型实例
        input_tensor: 输入张量 [batch_size, input_dim]
        num_runs: 测量次数
        device: 计算设备
    
    Returns:
        包含均值、标准差、P50、P95、P99的字典（单位：毫秒）
    """
    model = model.to(device)
    model.eval()
    input_tensor = input_tensor.to(device)
    
    # Warmup：排除初始化开销
    with torch.no_grad():
        for _ in range(20):
            _ = model(input_tensor)
    
    # 正式计时
    latencies = []
    with torch.no_grad():
        for _ in range(num_runs):
            if device == "cuda":
                torch.cuda.synchronize()
            
            start = time.perf_counter()
            _ = model(input_tensor)
            
            if device == "cuda":
                torch.cuda.synchronize()
            
            end = time.perf_counter()
            latencies.append((end - start) * 1000)  # 转换为毫秒
    
    # 计算统计指标
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)
    
    return {
        "mean_ms": round(statistics.mean(latencies), 4),
        "std_ms": round(statistics.stdev(latencies), 4),
        "min_ms": round(min(latencies), 4),
        "max_ms": round(max(latencies), 4),
        "p50_ms": round(latencies_sorted[int(n * 0.50)], 4),
        "p95_ms": round(latencies_sorted[int(n * 0.95)], 4),
        "p99_ms": round(latencies_sorted[int(n * 0.99)], 4),
        "num_runs": num_runs
    }


def measure_throughput(
    model: torch.nn.Module,
    batch_sizes: List[int],
    input_dim: int,
    num_batches: int = 100,
    device: str = "cpu"
) -> Dict[int, Dict[str, float]]:
    """
    测量不同批量大小下的推理吞吐量
    
    Args:
        model: PyTorch模型实例
        batch_sizes: 要测试的批量大小列表
        input_dim: 输入特征维度
        num_batches: 每个批量大小的测试批次数
        device: 计算设备
    
    Returns:
        字典，键为批量大小，值为吞吐量指标
    """
    model = model.to(device)
    model.eval()
    
    results = {}
    
    for batch_size in batch_sizes:
        # 生成随机输入
        input_tensor = torch.randn(batch_size, input_dim, device=device)
        
        # Warmup
        with torch.no_grad():
            for _ in range(10):
                _ = model(input_tensor)
        
        # 正式计时
        total_samples = 0
        start_time = time.perf_counter()
        
        with torch.no_grad():
            for _ in range(num_batches):
                _ = model(input_tensor)
                total_samples += batch_size
        
        if device == "cuda":
            torch.cuda.synchronize()
        
        end_time = time.perf_counter()
        elapsed_time = end_time - start_time
        
        throughput = total_samples / elapsed_time  # samples/second
        
        results[batch_size] = {
            "throughput_samples_per_sec": round(throughput, 2),
            "avg_latency_per_sample_ms": round((elapsed_time * 1000) / total_samples, 4),
            "total_time_sec": round(elapsed_time, 4),
            "num_batches": num_batches
        }
    
    return results


def measure_continuous_inference(
    model: torch.nn.Module,
    input_dim: int,
    duration_sec: float = 10.0,
    batch_size: int = 1,
    device: str = "cpu"
) -> Dict[str, float]:
    """
    模拟连续推理流，测试模型在持续负载下的性能
    
    Args:
        model: PyTorch模型实例
        input_dim: 输入特征维度
        duration_sec: 测试持续时间（秒）
        batch_size: 批量大小
        device: 计算设备
    
    Returns:
        连续推理性能指标
    """
    model = model.to(device)
    model.eval()
    
    # 生成随机输入
    input_tensor = torch.randn(batch_size, input_dim, device=device)
    
    # Warmup
    with torch.no_grad():
        for _ in range(10):
            _ = model(input_tensor)
    
    # 持续推理
    total_inferences = 0
    start_time = time.perf_counter()
    
    with torch.no_grad():
        while True:
            _ = model(input_tensor)
            total_inferences += 1
            
            # 检查是否达到目标时长
            elapsed = time.perf_counter() - start_time
            if elapsed >= duration_sec:
                break
    
    end_time = time.perf_counter()
    actual_duration = end_time - start_time
    
    inferences_per_second = total_inferences / actual_duration
    
    return {
        "total_inferences": total_inferences,
        "actual_duration_sec": round(actual_duration, 4),
        "inferences_per_second": round(inferences_per_second, 2),
        "avg_interval_ms": round((actual_duration * 1000) / total_inferences, 4),
        "batch_size": batch_size,
        "target_duration_sec": duration_sec
    }


def run_realtime_inference_experiment():
    """
    运行实时推理延迟与吞吐量实验的主函数
    """
    print("=" * 70)
    print("实验24: 实时推理延迟与吞吐量分析")
    print("=" * 70)
    
    # 确定计算设备
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n计算设备: {device}")
    if device == "cuda":
        print(f"GPU型号: {torch.cuda.get_device_name(0)}")
    
    # 获取配置
    config = ExperimentConfig()
    model_config = config.model
    
    # 定义要测试的模型
    model_names = ["DL-LNN", "LSTM", "Transformer", "PINN", "BPNN"]
    
    # 定义要测试的批量大小
    batch_sizes = [1, 8, 16, 32, 64, 128]
    
    # 存储所有结果
    results = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "device": device,
        "input_dim": model_config.input_dim,
        "experiments": {}
    }
    
    print(f"\n[1/4] 测试单样本推理延迟统计...")
    print("-" * 70)
    
    latency_results = {}
    for model_name in model_names:
        print(f"\n>>> 模型: {model_name}")
        try:
            model = create_model(model_name, config)
            
            # 单样本输入
            single_input = torch.randn(1, model_config.input_dim, device=device)
            
            latency_stats = measure_latency_statistics(
                model, single_input, num_runs=500, device=device
            )
            
            latency_results[model_name] = latency_stats
            
            print(f"  平均延迟: {latency_stats['mean_ms']:.4f} ms")
            print(f"  标准差: {latency_stats['std_ms']:.4f} ms")
            print(f"  P50: {latency_stats['p50_ms']:.4f} ms")
            print(f"  P95: {latency_stats['p95_ms']:.4f} ms")
            print(f"  P99: {latency_stats['p99_ms']:.4f} ms")
            
        except Exception as e:
            print(f"  [错误] {model_name}: {str(e)}")
            latency_results[model_name] = {"error": str(e)}
    
    results["experiments"]["latency_statistics"] = latency_results
    
    print(f"\n[2/4] 测试批量推理吞吐量...")
    print("-" * 70)
    
    throughput_results = {}
    for model_name in model_names:
        print(f"\n>>> 模型: {model_name}")
        try:
            model = create_model(model_name, config)
            
            throughput_stats = measure_throughput(
                model, batch_sizes, model_config.input_dim, 
                num_batches=100, device=device
            )
            
            throughput_results[model_name] = throughput_stats
            
            # 打印关键批量
            for bs in [1, 32, 128]:
                if bs in throughput_stats:
                    print(f"  Batch={bs}: {throughput_stats[bs]['throughput_samples_per_sec']:.2f} samples/sec")
            
        except Exception as e:
            print(f"  [错误] {model_name}: {str(e)}")
            throughput_results[model_name] = {"error": str(e)}
    
    results["experiments"]["batch_throughput"] = throughput_results
    
    print(f"\n[3/4] 测试连续推理流性能...")
    print("-" * 70)
    
    continuous_results = {}
    for model_name in model_names:
        print(f"\n>>> 模型: {model_name}")
        try:
            model = create_model(model_name, config)
            
            continuous_stats = measure_continuous_inference(
                model, model_config.input_dim, 
                duration_sec=10.0, batch_size=1, device=device
            )
            
            continuous_results[model_name] = continuous_stats
            
            print(f"  总推理次数: {continuous_stats['total_inferences']}")
            print(f"  推理速率: {continuous_stats['inferences_per_second']:.2f} inferences/sec")
            print(f"  平均间隔: {continuous_stats['avg_interval_ms']:.4f} ms")
            
        except Exception as e:
            print(f"  [错误] {model_name}: {str(e)}")
            continuous_results[model_name] = {"error": str(e)}
    
    results["experiments"]["continuous_inference"] = continuous_results
    
    # 计算实时性评分
    print(f"\n[4/4] 计算实时性评分...")
    print("-" * 70)
    
    realtime_scores = {}
    for model_name in model_names:
        if model_name in latency_results and "error" not in latency_results[model_name]:
            # 实时性评分 = 1000 / (P99延迟 * 标准差)
            # 延迟越低、稳定性越好，评分越高
            p99 = latency_results[model_name]["p99_ms"]
            std = latency_results[model_name]["std_ms"]
            
            # 避免除以零
            score = 1000 / (p99 * max(std, 0.001))
            realtime_scores[model_name] = round(score, 2)
            
            print(f"  {model_name}: {score:.2f}")
    
    results["experiments"]["realtime_score"] = realtime_scores
    
    # 保存结果
    print("\n" + "=" * 70)
    print("保存实验结果...")
    
    results_dir = project_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    output_path = results_dir / "realtime_inference_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"结果已保存到: {output_path}")
    
    # 打印汇总表
    print("\n" + "=" * 70)
    print("实时推理性能汇总表")
    print("=" * 70)
    
    header = f"{'模型':<14} {'平均延迟(ms)':>14} {'P95(ms)':>10} {'P99(ms)':>10} {'吞吐量(1/s)':>14} {'实时评分':>10}"
    print(header)
    print("-" * len(header))
    
    for model_name in model_names:
        if model_name in latency_results and "error" not in latency_results[model_name]:
            lat = latency_results[model_name]
            thr = throughput_results.get(model_name, {}).get(1, {})
            score = realtime_scores.get(model_name, 0)
            
            print(
                f"{model_name:<14} "
                f"{lat['mean_ms']:>14.4f} "
                f"{lat['p95_ms']:>10.4f} "
                f"{lat['p99_ms']:>10.4f} "
                f"{thr.get('throughput_samples_per_sec', 0):>14.2f} "
                f"{score:>10.2f}"
            )
    
    print("=" * 70)
    print("实验完成！")
    
    return results


if __name__ == "__main__":
    run_realtime_inference_experiment()
