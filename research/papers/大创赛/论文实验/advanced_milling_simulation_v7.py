"""
高级非线性铣削稳定性数据集生成器（v7 - 平衡比例版）

修复v6版本中稳定/不稳定比例失衡问题：
- v6问题：过程阻尼效应过强（max 150%），导致临界切深被放大到测试范围之上
- 修复：降低过程阻尼上限至50%，目标临界切深0.8mm，使稳定/不稳定比例接近40-60%
"""

import numpy as np
import json
from typing import Dict, List, Tuple


class AdvancedMillingSimulator:
    """高级非线性铣削动力学仿真器"""
    
    def __init__(self, config: Dict):
        self.config = config
        
        # 机床动力学参数
        self.m_x = config.get('m_x', 10.0)
        self.m_y = config.get('m_y', 12.0)
        self.c_x = config.get('c_x', 50.0)
        self.c_y = config.get('c_y', 60.0)
        self.k_x = config.get('k_x', 1e6)
        self.k_y = config.get('k_y', 1.2e6)
        
        # 固有频率和阻尼比
        self.omega_n_x = np.sqrt(self.k_x / self.m_x)
        self.omega_n_y = np.sqrt(self.k_y / self.m_y)
        self.zeta_x = self.c_x / (2 * np.sqrt(self.k_x * self.m_x))
        self.zeta_y = self.c_y / (2 * np.sqrt(self.k_y * self.m_y))
        
        # 刀具参数
        self.N = config.get('N', 4)
        self.D = config.get('D', 0.010)
        self.runout = config.get('runout', 0.0)
        
        # 切削力系数
        self.K_s = config.get('K_s', 1e8)
        self.alpha = config.get('alpha', 0.5)
        
        # 过程阻尼参数
        self.C_pd = config.get('C_pd', 0.1)
        
        # 叶瓣结构参数
        self.lobe_amplitude = config.get('lobe_amplitude', 0.3)
        
        # 材料
        self.material = config.get('material', 'Al6061-T6')
        
        # 预计算FRF最小实部
        self.Re_G_min, self.freq_at_Re_G_min = self._compute_min_real_FRF()
        
        print(f"✓ SLD生成器: {self.material}")
        print(f"  f_n_x={self.omega_n_x/(2*np.pi):.1f}Hz, ζ_x={self.zeta_x:.4f}")
        print(f"  f_n_y={self.omega_n_y/(2*np.pi):.1f}Hz, ζ_y={self.zeta_y:.4f}")
        print(f"  K_s={self.K_s:.2e}N/m, α={self.alpha}")
        print(f"  Re[G]_min={self.Re_G_min:.3e} @ f={self.freq_at_Re_G_min:.1f}Hz")
    
    def _compute_min_real_FRF(self):
        """
        计算组合FRF G(ω) = 0.5*(H_x + H_y)的最小实部
        
        对于SDOF系统，Re[H(ω)]在ω略高于ω_n时达到最小值
        最小值约为 -1/(4kζ)
        """
        f_n_x = self.omega_n_x / (2 * np.pi)
        f_n_y = self.omega_n_y / (2 * np.pi)
        
        f_min = min(f_n_x, f_n_y) * 0.8
        f_max = max(f_n_x, f_n_y) * 1.2
        
        freq_range = np.linspace(f_min, f_max, 1000)
        
        Re_G_min = 0.0
        freq_at_min = f_n_x
        
        for f in freq_range:
            G = self.compute_frequency_response(f)
            Re_G = np.real(G)
            if Re_G < Re_G_min:
                Re_G_min = Re_G
                freq_at_min = f
        
        return Re_G_min, freq_at_min
    
    def compute_frequency_response(self, freq: float) -> complex:
        """计算频率响应函数"""
        omega = 2 * np.pi * freq
        H_x = 1.0 / (self.k_x - self.m_x * omega**2 + 1j * self.c_x * omega)
        H_y = 1.0 / (self.k_y - self.m_y * omega**2 + 1j * self.c_y * omega)
        return 0.5 * (H_x + H_y)
    
    def compute_a_crit_lobe(self, n_spindle: float) -> float:
        """
        计算给定转速下的临界切深（考虑叶瓣结构）
        
        基于Tlusty零阶法：a_lim = -1 / (2 * K_s * α * Re[G]_min)
        """
        if self.Re_G_min >= 0:
            a_base = 5e-3
        else:
            a_base = -1.0 / (2.0 * self.K_s * self.alpha * self.Re_G_min)
        
        # 叶瓣结构调制
        f_tooth = self.N * n_spindle / 60.0
        f_n_avg = 0.5 * (self.omega_n_x + self.omega_n_y) / (2 * np.pi)
        
        lobe_index = f_n_avg / max(f_tooth, 1.0)
        phase = 2 * np.pi * lobe_index
        modulation = 1.0 + self.lobe_amplitude * np.cos(phase)
        
        a_lobe = a_base * max(modulation, 0.1)
        
        # 过程阻尼修正（降低上限至50%）
        v_c = np.pi * self.D * n_spindle / 60.0
        pd_factor = self.C_pd * v_c / max(a_lobe, 1e-6)
        a_crit = a_lobe * (1.0 + min(pd_factor, 0.5))  # 最多提高50%
        
        return a_crit
    
    def generate_sld(self, n_range, a_p_range, n_n=50, n_a=50, noise_std=0.12):
        """生成稳定性叶瓣图数据"""
        n_values = np.linspace(n_range[0], n_range[1], n_n)
        a_p_values = np.linspace(a_p_range[0], a_p_range[1], n_a)
        
        sld_data = []
        
        print(f"\n生成SLD ({n_n}x{n_a}={n_n*n_a}点)...")
        
        a_crits = [self.compute_a_crit_lobe(n) for n in n_values]
        a_crit_min = min(a_crits)
        a_crit_max = max(a_crits)
        a_crit_mean = np.mean(a_crits)
        
        print(f"  临界切深范围: [{a_crit_min*1e3:.3f}, {a_crit_max*1e3:.3f}]mm, "
              f"均值={a_crit_mean*1e3:.3f}mm")
        
        for i, n in enumerate(n_values):
            a_crit = self.compute_a_crit_lobe(n)
            
            for j, a_p in enumerate(a_p_values):
                ratio = a_p / max(a_crit, 1e-10)
                log_ratio = np.log(max(ratio, 1e-10))
                noisy = log_ratio + noise_std * np.random.randn()
                is_stable = noisy < 0
                
                features = self._gen_features(n, a_p, a_crit, is_stable)
                
                sld_data.append({
                    'n_spindle': float(n),
                    'a_p': float(a_p),
                    'is_stable': bool(is_stable),
                    'a_crit': float(a_crit),
                    'stability_margin': float((a_crit - a_p) / max(a_crit, 1e-10)),
                    **features
                })
            
            if (i + 1) % 10 == 0:
                print(f"  进度: {i+1}/{n_n}")
        
        n_stable = sum(1 for d in sld_data if d['is_stable'])
        n_total = len(sld_data)
        print(f"✓ 完成: {n_total}点, 稳定{n_stable}({n_stable/n_total*100:.1f}%), "
              f"不稳定{n_total-n_stable}({(n_total-n_stable)/n_total*100:.1f}%)")
        
        return {
            'config': {k: float(v) if isinstance(v, (int, float, np.floating)) else v 
                       for k, v in self.config.items()},
            'sld_data': sld_data,
            'n_values': n_values.tolist(),
            'a_p_values': a_p_values.tolist(),
        }
    
    def _gen_features(self, n, a_p, a_crit, is_stable):
        """生成振动特征"""
        f_tooth = self.N * n / 60.0
        
        if is_stable:
            amp_ratio = a_p / max(a_crit, 1e-10)
            x_rms = 5e-6 + 2e-5 * amp_ratio
            x_peak = x_rms * 2.5
            dominant_freq = f_tooth
            harmonic_ratio = 0.1 + 0.2 * amp_ratio
        else:
            excess_ratio = a_p / max(a_crit, 1e-10) - 1.0
            x_rms = 5e-5 + 1e-4 * excess_ratio
            x_peak = x_rms * 3.0
            f_n_avg = 0.5 * (self.omega_n_x + self.omega_n_y) / (2 * np.pi)
            dominant_freq = f_n_avg * (1 + 0.05 * np.random.randn())
            harmonic_ratio = 0.5 + 0.5 * min(excess_ratio, 1.0)
        
        x_rms *= (1 + 0.1 * np.random.randn())
        x_peak *= (1 + 0.1 * np.random.randn())
        x_rms = max(x_rms, 1e-7)
        x_peak = max(x_peak, x_rms * 1.5)
        
        return {
            'x_rms': float(x_rms),
            'x_peak': float(x_peak),
            'dominant_freq': float(dominant_freq),
            'harmonic_ratio': float(harmonic_ratio),
        }


def calibrate_K_s(target_a_crit, simulator_config):
    """
    校准切削力系数K_s
    
    基于Tlusty公式：a_lim = -1 / (2 * K_s * α * Re[G]_min)
    反推：K_s = -1 / (2 * a_lim * α * Re[G]_min)
    """
    temp_sim = AdvancedMillingSimulator(simulator_config)
    Re_G_min = temp_sim.Re_G_min
    
    if Re_G_min >= 0:
        print(f"  警告: Re[G]_min >= 0 ({Re_G_min:.3e})，无法校准K_s")
        return 1e8
    
    alpha = simulator_config.get('alpha', 0.5)
    K_s = -1.0 / (2.0 * target_a_crit * alpha * Re_G_min)
    
    return K_s


def generate_multiple_datasets():
    """生成5个不同工况的数据集"""
    
    # 目标：临界切深0.8mm，使稳定/不稳定比例接近40-60%
    target_a_crit = 0.8e-3  # 0.8mm
    
    configs = [
        {
            'name': 'HighSpeed_Aluminum',
            'material': 'Al6061-T6',
            'm_x': 8.0, 'm_y': 10.0,
            'c_x': 40.0, 'c_y': 50.0,
            'k_x': 1.0e6, 'k_y': 1.2e6,
            'N': 4, 'D': 0.010,
            'runout': 5e-6,
            'alpha': 0.5,
            'lobe_amplitude': 0.3,
            'C_pd': 0.05,
        },
        {
            'name': 'MediumSpeed_Steel',
            'material': 'AISI1045',
            'm_x': 15.0, 'm_y': 18.0,
            'c_x': 80.0, 'c_y': 100.0,
            'k_x': 1.5e6, 'k_y': 1.8e6,
            'N': 3, 'D': 0.016,
            'runout': 8e-6,
            'alpha': 0.45,
            'lobe_amplitude': 0.25,
            'C_pd': 0.06,
        },
        {
            'name': 'LowSpeed_Titanium',
            'material': 'Ti6Al4V',
            'm_x': 20.0, 'm_y': 25.0,
            'c_x': 120.0, 'c_y': 150.0,
            'k_x': 2.0e6, 'k_y': 2.5e6,
            'N': 5, 'D': 0.020,
            'runout': 1e-5,
            'alpha': 0.4,
            'lobe_amplitude': 0.35,
            'C_pd': 0.07,
        },
        {
            'name': 'VariablePitch',
            'material': 'Al7075',
            'm_x': 10.0, 'm_y': 12.0,
            'c_x': 50.0, 'c_y': 60.0,
            'k_x': 1.0e6, 'k_y': 1.2e6,
            'N': 4, 'D': 0.012,
            'runout': 6e-6,
            'alpha': 0.5,
            'lobe_amplitude': 0.28,
            'C_pd': 0.05,
        },
        {
            'name': 'FlexibleTool',
            'material': 'Inconel718',
            'm_x': 5.0, 'm_y': 6.0,
            'c_x': 25.0, 'c_y': 30.0,
            'k_x': 0.5e6, 'k_y': 0.6e6,
            'N': 4, 'D': 0.010,
            'runout': 1.2e-5,
            'alpha': 0.45,
            'lobe_amplitude': 0.32,
            'C_pd': 0.08,
        },
    ]
    
    all_datasets = {}
    
    for i, config in enumerate(configs, 1):
        print(f"\n{'='*80}")
        print(f"生成数据集 {i}/5: {config['name']}")
        print(f"{'='*80}")
        
        K_s = calibrate_K_s(target_a_crit, config)
        config['K_s'] = K_s
        
        print(f"  校准K_s = {K_s:.2e} N/m")
        
        simulator = AdvancedMillingSimulator(config)
        
        sld = simulator.generate_sld(
            n_range=(1000, 15000),
            a_p_range=(0.1e-3, 3.0e-3),
            n_n=50,
            n_a=50,
            noise_std=0.12
        )
        
        all_datasets[config['name']] = sld
        
        filename = f"dataset_{i}_{config['name']}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(sld, f, indent=2, ensure_ascii=False)
        print(f"✓ 已保存: {filename}")
    
    with open("all_advanced_datasets_v7.json", 'w', encoding='utf-8') as f:
        json.dump(all_datasets, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*80}")
    print(f"✓ 所有数据集生成完成")
    print(f"{'='*80}")
    
    return all_datasets


if __name__ == "__main__":
    datasets = generate_multiple_datasets()
    
    print(f"\n数据集统计:")
    for name, data in datasets.items():
        n_points = len(data['sld_data'])
        n_stable = sum(1 for d in data['sld_data'] if d['is_stable'])
        n_unstable = n_points - n_stable
        print(f"  {name}: {n_points}点, 稳定{n_stable} ({n_stable/n_points*100:.1f}%), "
              f"不稳定{n_unstable} ({n_unstable/n_points*100:.1f}%)")
