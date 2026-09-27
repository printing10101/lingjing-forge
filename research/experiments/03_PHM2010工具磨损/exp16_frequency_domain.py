"""
频域分析实验
分析模型预测的频谱特性
生成论文表8所需数据
"""

import sys
import json
import torch
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
from scipy.fft import fft, fftfreq
from scipy.signal import welch, find_peaks
from scipy.stats import entropy

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))
# 添加项目根目录（python/）到 path，用于导入 app 模块
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from training.reproducibility import set_global_seed

from config import ModelConfig
from models import DLLNNWithPhysics
from data_generator import Industrial6061T6Dataset, create_dataloaders


def generate_milling_signal(
    duration: float = 1.0,
    fs: float = 10000,
    state: str = "stable",
    spindle_speed: float = 6000,
    num_teeth: int = 4
) -> Tuple[np.ndarray, np.ndarray]:
    """
    生成模拟铣削振动信号
    
    Args:
        duration: 信号时长 (秒)
        fs: 采样频率 (Hz)
        state: 状态 ("stable" 或 "chatter")
        spindle_speed: 主轴转速 (RPM)
        num_teeth: 刀具齿数
    
    Returns:
        t: 时间数组
        signal: 振动信号
    """
    t = np.arange(0, duration, 1/fs)
    
    # 计算基频 (刀齿通过频率)
    tooth_passing_freq = spindle_speed * num_teeth / 60  # Hz
    
    # 稳定状态：基频 + 谐波 + 噪声
    if state == "stable":
        signal = (
            0.5 * np.sin(2 * np.pi * tooth_passing_freq * t) +
            0.2 * np.sin(2 * np.pi * 2 * tooth_passing_freq * t) +
            0.1 * np.sin(2 * np.pi * 3 * tooth_passing_freq * t) +
            0.05 * np.random.randn(len(t))
        )
    # 颤振状态：基频 + 颤振频率 + 边带
    else:
        chatter_freq = 850  # 颤振频率 (Hz)
        signal = (
            0.3 * np.sin(2 * np.pi * tooth_passing_freq * t) +
            0.8 * np.sin(2 * np.pi * chatter_freq * t) +
            0.3 * np.sin(2 * np.pi * (chatter_freq + tooth_passing_freq) * t) +
            0.3 * np.sin(2 * np.pi * (chatter_freq - tooth_passing_freq) * t) +
            0.1 * np.random.randn(len(t))
        )
    
    return t, signal


def compute_spectrum(
    signal: np.ndarray,
    fs: float
) -> Tuple[np.ndarray, np.ndarray]:
    """
    计算信号频谱
    
    Args:
        signal: 时域信号
        fs: 采样频率
    
    Returns:
        freqs: 频率数组
        magnitudes: 幅值谱
    """
    n = len(signal)
    # FFT变换
    yf = fft(signal)
    # 频率数组
    xf = fftfreq(n, 1/fs)[:n//2]
    # 幅值谱
    magnitudes = 2.0/n * np.abs(yf[:n//2])
    
    return xf, magnitudes


def find_main_frequency(
    freqs: np.ndarray,
    magnitudes: np.ndarray,
    min_freq: float = 10
) -> Tuple[float, float]:
    """
    识别主频率
    
    Args:
        freqs: 频率数组
        magnitudes: 幅值谱
        min_freq: 最小频率阈值
    
    Returns:
        main_freq: 主频率
        main_amplitude: 主频率幅值
    """
    # 过滤低频噪声
    valid_idx = freqs > min_freq
    valid_freqs = freqs[valid_idx]
    valid_mags = magnitudes[valid_idx]
    
    # 找到最大幅值对应的频率
    peak_idx = np.argmax(valid_mags)
    main_freq = valid_freqs[peak_idx]
    main_amplitude = valid_mags[peak_idx]
    
    return main_freq, main_amplitude


def compute_spectral_entropy(
    magnitudes: np.ndarray,
    num_bins: int = 100
) -> float:
    """
    计算频谱熵
    
    Args:
        magnitudes: 幅值谱
        num_bins: 直方图bin数量
    
    Returns:
        spectral_entropy: 频谱熵
    """
    # 归一化幅值谱为概率分布
    magnitudes_norm = magnitudes / (np.sum(magnitudes) + 1e-10)
    
    # 计算熵
    spectral_entropy = entropy(magnitudes_norm + 1e-10)
    
    return spectral_entropy


def analyze_signal_spectrum():
    """分析信号的频谱特性"""
    
    print("=" * 80)
    print("频域分析实验")
    print("=" * 80)
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"\n使用设备: {device}")
    
    config = ModelConfig()
    
    # 生成模拟信号
    print("\n[1/4] 生成模拟铣削振动信号...")
    
    # 稳定状态信号
    t_stable, signal_stable = generate_milling_signal(
        duration=1.0, fs=10000, state="stable", spindle_speed=6000
    )
    
    # 颤振状态信号
    t_chatter, signal_chatter = generate_milling_signal(
        duration=1.0, fs=10000, state="chatter", spindle_speed=6000
    )
    
    # 频谱分析
    print("\n[2/4] 计算频谱特性...")
    
    # 稳定信号频谱
    freqs_stable, mags_stable = compute_spectrum(signal_stable, fs=10000)
    main_freq_stable, amp_stable = find_main_frequency(freqs_stable, mags_stable)
    entropy_stable = compute_spectral_entropy(mags_stable)
    
    # 颤振信号频谱
    freqs_chatter, mags_chatter = compute_spectrum(signal_chatter, fs=10000)
    main_freq_chatter, amp_chatter = find_main_frequency(freqs_chatter, mags_chatter)
    entropy_chatter = compute_spectral_entropy(mags_chatter)
    
    print(f"\n  稳定状态:")
    print(f"    主频率: {main_freq_stable:.2f} Hz")
    print(f"    幅值: {amp_stable:.4f}")
    print(f"    频谱熵: {entropy_stable:.4f}")
    
    print(f"\n  颤振状态:")
    print(f"    主频率: {main_freq_chatter:.2f} Hz")
    print(f"    幅值: {amp_chatter:.4f}")
    print(f"    频谱熵: {entropy_chatter:.4f}")
    
    # 加载工业数据集
    print("\n[3/4] 加载工业数据集并训练模型...")
    dataset = Industrial6061T6Dataset(num_samples=500, noise_level=0.08, seed=46)
    
    train_loader, val_loader, test_loader = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={'num_samples': 500, 'noise_level': 0.08, 'seed': 46},
        batch_size=config.batch_size,
        train_ratio=0.7,
        val_ratio=0.15
    )
    
    # 创建并训练模型
    model = DLLNNWithPhysics(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
        dt=config.ltc_dt,
        dropout=config.dropout
    ).to(device)
    
    # 简单训练
    import torch.nn as nn
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    
    best_loss = float('inf')
    best_state = None
    
    for epoch in range(50):
        model.train()
        train_loss = 0.0
        
        for batch in train_loader:
            x, y_true, _ = batch
            x = x.to(device)
            y_true = y_true.to(device)
            
            optimizer.zero_grad()
            output = model(x)
            if isinstance(output, tuple):
                y_pred = output[0]
            else:
                y_pred = output
            
            if y_pred.shape != y_true.shape:
                y_pred = y_pred.view_as(y_true)
            
            loss = criterion(y_pred, y_true)
            loss.backward()
            optimizer.step()
            
            train_loss += loss.item()
        
        train_loss /= len(train_loader)
        
        # 验证
        model.eval()
        val_loss = 0.0
        
        with torch.no_grad():
            for batch in val_loader:
                x, y_true, _ = batch
                x = x.to(device)
                y_true = y_true.to(device)
                
                output = model(x)
                if isinstance(output, tuple):
                    y_pred = output[0]
                else:
                    y_pred = output
                
                if y_pred.shape != y_true.shape:
                    y_pred = y_pred.view_as(y_true)
                
                loss = criterion(y_pred, y_true)
                val_loss += loss.item()
        
        val_loss /= len(val_loader)
        
        if val_loss < best_loss:
            best_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        
        if (epoch + 1) % 10 == 0:
            print(f"  Epoch [{epoch+1}/50] Train: {train_loss:.4f} Val: {val_loss:.4f}")
    
    # 加载最佳模型
    if best_state is not None:
        model.load_state_dict(best_state)
    
    # 测试集预测
    print("\n[4/4] 分析模型预测的频谱特性...")
    
    model.eval()
    predictions = []
    actuals = []
    
    with torch.no_grad():
        for batch in test_loader:
            x, y_true, _ = batch
            x = x.to(device)
            
            output = model(x)
            if isinstance(output, tuple):
                y_pred = output[0]
            else:
                y_pred = output
            
            if y_pred.shape != y_true.shape:
                y_pred = y_pred.view_as(y_true)
            
            predictions.append(y_pred.cpu().numpy())
            actuals.append(y_true.numpy())
    
    predictions = np.concatenate(predictions, axis=0)
    actuals = np.concatenate(actuals, axis=0)
    
    # 分析预测结果的频谱
    pred_signal = predictions.flatten()
    actual_signal = actuals.flatten()
    
    # 计算频谱
    freqs_pred, mags_pred = compute_spectrum(pred_signal, fs=10000)
    freqs_actual, mags_actual = compute_spectrum(actual_signal, fs=10000)
    
    # 识别主频率
    pred_main_freq, pred_amp = find_main_frequency(freqs_pred, mags_pred)
    actual_main_freq, actual_amp = find_main_frequency(freqs_actual, mags_actual)
    
    # 计算频率误差
    freq_error = abs(pred_main_freq - actual_main_freq)
    
    # 计算频谱相似度 (使用相关系数)
    spectral_similarity = np.corrcoef(mags_pred, mags_actual)[0, 1]
    
    print(f"\n  模型预测频谱分析:")
    print(f"    预测主频率: {pred_main_freq:.2f} Hz")
    print(f"    实际主频率: {actual_main_freq:.2f} Hz")
    print(f"    频率误差: {freq_error:.2f} Hz")
    print(f"    频谱相似度: {spectral_similarity:.4f}")
    
    # 整理结果
    results = {
        'timestamp': datetime.now().isoformat(),
        'signal_analysis': {
            'stable': {
                'main_freq': float(main_freq_stable),
                'amplitude': float(amp_stable),
                'entropy': float(entropy_stable)
            },
            'chatter': {
                'main_freq': float(main_freq_chatter),
                'chatter_freq': 850.0,
                'amplitude': float(amp_chatter),
                'entropy': float(entropy_chatter)
            }
        },
        'model_prediction_spectrum': {
            'predicted_main_freq': float(pred_main_freq),
            'actual_main_freq': float(actual_main_freq),
            'frequency_error_hz': float(freq_error),
            'spectral_similarity': float(spectral_similarity)
        }
    }
    
    # 保存结果
    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)
    
    output_file = output_dir / "frequency_domain_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*80}")
    print(f"实验完成！结果已保存到: {output_file}")
    print(f"{'='*80}")
    
    return results


if __name__ == "__main__":
    set_global_seed(42)
    results = analyze_signal_spectrum()
