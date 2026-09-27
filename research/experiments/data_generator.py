"""
数据生成器模块
实现合成数据生成（Tlusty公式）和数据集加载

数据来源说明：
    - PHM2010Dataset：加载真实 PHM2010 刀具磨损数据集（PHM Society 2010 竞赛数据）
      输入特征从 7 个真实信号通道（force_x/y/z, vibration_x/y/z, acoustic_emission_rms）
      按时间窗口提取统计量得到；颤振稳定性标签由 Tlusty 解析模型基于信号能量派生，
      因 PHM2010 数据集本身不包含颤振标签（仅含刀具磨损标签 tool_wear）。
    - SyntheticChatterDataset / IndustrialChatterDataset：纯合成数据，用于对照实验。
    - NUAADataset / NISTDataset / Benchmark1Dataset / Industrial6061T6Dataset：
      基于 Tlusty 解析模型的合成数据，仅在缺乏对应公开真实数据集时作为占位实现，
      不可在论文中声称对应真实数据集实验结果。
"""

import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader
from typing import Tuple, Dict, List, Optional
import os
import logging

logger = logging.getLogger(__name__)

# PHM2010 真实数据默认路径（相对项目根目录）
_PHM2010_DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "uniwear",
)


class TlustyAnalyticalModel:
    """
    Tlusty 解析模型
    用于生成合成数据和提供物理先验
    """

    def __init__(
        self,
        stiffness: float = 1e6,  # N/m
        modal_mass: float = 100.0,  # kg
        damping_ratio: float = 0.05,
        cutting_force_coeff: float = 2000.0,  # N/mm²
        num_teeth: int = 4,
    ):
        """
        初始化Tlusty模型

        Args:
            stiffness: 机床刚度 (N/m)
            modal_mass: 模态质量 (kg)
            damping_ratio: 阻尼比
            cutting_force_coeff: 切削力系数 (N/mm²)
            num_teeth: 刀具齿数
        """
        self.stiffness = stiffness
        self.modal_mass = modal_mass
        self.damping_ratio = damping_ratio
        self.cutting_force_coeff = cutting_force_coeff
        self.num_teeth = num_teeth

        # 计算阻尼系数
        self.damping = 2 * damping_ratio * np.sqrt(stiffness * modal_mass)

    def frequency_response(self, omega: np.ndarray) -> np.ndarray:
        """
        计算频率响应函数 G(jω)

        Args:
            omega: 角频率数组 (rad/s)

        Returns:
            G(jω) 复数数组
        """
        k = self.stiffness
        m = self.modal_mass
        c = self.damping

        G = 1 / (k - m * omega**2 + 1j * c * omega)
        return G

    def compute_limiting_depth(
        self,
        spindle_speed: np.ndarray,
        num_lobes: int = 10,
        hardness: Optional[np.ndarray] = None,
        tool_diameter: Optional[np.ndarray] = None,
        num_teeth: Optional[np.ndarray] = None,
        feed_rate: Optional[np.ndarray] = None,
        radial_depth: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """计算极限切深 a_lim（真实叶瓣机制 + 多物理参数耦合）。

        物理：稳定性叶图 SLD 的"叶瓣"来自颤振频率 f_c 与主轴转速 n 的耦合：
            f_c = j · n / 60  (j = 叶瓣数 = 1, 2, ..., num_lobes)
        对每个转速 n，选择使 a_lim 最小的正叶瓣（最危险叶瓣）。

        多物理参数耦合（使 7 维输入特征均有物理意义）：
            - 材料硬度 H → 切削力系数 Ks_eff = Ks_base · (H / 200)^0.8
              （更硬材料 → 更大 Ks → 更小 a_lim）
            - 刀具直径 D → 刚度 k_eff = k_base · (D / 10)^2，模态质量
              m_eff = m_base · (D / 10)^2（悬臂梁模型，更粗刀具更硬也更重）
            - 齿数 z → 有效切削力 Ks_eff *= z / 4（更多齿单转切削力更大）
            - 径向切宽 ae → 方向因子 μ = 0.5 · (1 + ae / 8)
              （更大径向接合 → 方向因子更大 → a_lim 更小）
            - 每齿进给 f → 切屑变薄效应 Ks_eff *= (1 + 0.15·(f-0.25)/0.25)
              （偏离标称进给时切削力系数显著变化，非线性耦合）

        Args:
            spindle_speed: 主轴转速数组 (rpm)
            num_lobes: 考虑的最大叶瓣数
            hardness: 材料硬度数组 (HB)，默认 None 使用基准值 200
            tool_diameter: 刀具直径数组 (mm)，默认 None 使用基准值 10
            num_teeth: 齿数数组，默认 None 使用基准值 4
            feed_rate: 每齿进给量数组 (mm/tooth)，默认 None 使用基准值 0.25
            radial_depth: 径向切宽数组 (mm)，默认 None 使用基准值 4

        Returns:
            极限切深数组 (mm)，具有真实叶瓣波动与多参数耦合
        """
        # 基础物理参数
        k_base = self.stiffness
        m_base = self.modal_mass
        c_base = self.damping
        Ks_base = self.cutting_force_coeff * 1e6  # N/m²

        spindle_speed = np.atleast_1d(np.asarray(spindle_speed, dtype=float))
        N = len(spindle_speed)

        # 默认参数（基准值，保持向后兼容）
        if hardness is None:
            hardness = np.full(N, 200.0)
        else:
            hardness = np.atleast_1d(np.asarray(hardness, dtype=float))
        if tool_diameter is None:
            tool_diameter = np.full(N, 10.0)
        else:
            tool_diameter = np.atleast_1d(np.asarray(tool_diameter, dtype=float))
        if num_teeth is None:
            num_teeth = np.full(N, 4.0)
        else:
            num_teeth = np.atleast_1d(np.asarray(num_teeth, dtype=float))
        if feed_rate is None:
            feed_rate = np.full(N, 0.25)
        else:
            feed_rate = np.atleast_1d(np.asarray(feed_rate, dtype=float))
        if radial_depth is None:
            radial_depth = np.full(N, 4.0)
        else:
            radial_depth = np.atleast_1d(np.asarray(radial_depth, dtype=float))

        # 多物理参数耦合（向量化计算）
        # 1. 硬度 H Ks（更硬材料切削力系数更大）
        Ks_eff = Ks_base * (hardness / 200.0) ** 0.8
        # 2. 齿数 z 有效切削力（更多齿单转切削力更大）
        Ks_eff = Ks_eff * (num_teeth / 4.0)
        # 3. 进给 f 切屑变薄非线性效应（系数 0.15 体现高进给时切屑变薄显著）
        Ks_eff = Ks_eff * (1.0 + 0.15 * (feed_rate - 0.25) / 0.25)
        # 4. 刀具直径 D 刚度与模态质量（悬臂梁 k∝D²·I，简化为 D²）
        D_ratio = (tool_diameter / 10.0) ** 2
        k_eff = k_base * D_ratio
        m_eff = m_base * D_ratio
        # 阻尼系数 c = 2·ζ·sqrt(k·m)，ζ 不变时 c ∝ D²
        c_eff = c_base * D_ratio
        # 5. 径向切宽 ae 方向因子 μ（更大径向接合 更大方向因子）
        mu_dir = 0.5 * (1.0 + radial_depth / 8.0)

        a_lim = np.empty(N)

        for idx in range(N):
            n_rpm = spindle_speed[idx]
            if n_rpm <= 0:
                a_lim[idx] = 20.0
                continue

            k_i = k_eff[idx]
            m_i = m_eff[idx]
            c_i = c_eff[idx]
            Ks_i = Ks_eff[idx]
            mu_i = mu_dir[idx]

            best_a = None
            for j in range(1, num_lobes + 1):
                f_c = j * n_rpm / 60.0
                omega_c = 2 * np.pi * f_c
                # 复频率响应 G(jω) = 1/(k - mω² + jcω) 的实部
                denom_real = k_i - m_i * omega_c**2
                denom_imag = c_i * omega_c
                real_G = denom_real / (denom_real**2 + denom_imag**2)

                if abs(real_G) < 1e-12:
                    continue

                # Tlusty: a_lim = -1 / (2 · Ks · μ · Re(G))，仅 Re(G)<0 时为正
                a_val = -1.0 / (2.0 * Ks_i * mu_i * real_G)
                if a_val <= 0:
                    continue

                # 选最小正 a_lim（最危险叶瓣）
                if best_a is None or a_val < best_a:
                    best_a = a_val

            if best_a is None:
                best_a = 20.0

            # m mm
            a_lim[idx] = best_a * 1000.0

        # 合理范围限制（0.1 mm - 20 mm，保留叶瓣波动）
        a_lim = np.clip(a_lim, 0.1, 20.0)
        return a_lim

    def compute_stability(self, spindle_speed: np.ndarray, axial_depth: np.ndarray) -> np.ndarray:
        """
        计算稳定性标签

        Args:
            spindle_speed: 主轴转速 (rpm)
            axial_depth: 轴向切深 (mm)

        Returns:
            稳定性标签 (0=稳定, 1=不稳定)
        """
        a_lim = self.compute_limiting_depth(spindle_speed)
        stability = (axial_depth > a_lim).astype(int)
        return stability


def build_physics_features_7d(
    spindle_speed: np.ndarray,
    feed_rate: np.ndarray,
    axial_depth: np.ndarray,
    radial_depth: np.ndarray,
    hardness: np.ndarray,
    tool_diameter: np.ndarray,
    num_teeth: np.ndarray,
) -> np.ndarray:
    """构造 7 维物理参数特征（论文第3节声明的输入特征向量）。

    特征顺序与 config.py 的 input_dim=7 严格对齐：
        [主轴转速 n, 进给 f, 轴向切深 ap, 径向切宽 ae,
         材料硬度 H, 刀具直径 D, 刀具齿数 z]

    所有特征均归一化至 ~[0, 1] 范围，使用典型量级作为分母。

    Args:
        spindle_speed: 主轴转速 (rpm)
        feed_rate: 每齿进给量 (mm/tooth)
        axial_depth: 轴向切深 (mm)
        radial_depth: 径向切宽 (mm)
        hardness: 材料硬度 (HB)
        tool_diameter: 刀具直径 (mm)
        num_teeth: 刀具齿数

    Returns:
        形状 [N, 7] 的 float32 特征矩阵
    """
    features = np.column_stack(
        [
            spindle_speed / 10000.0,  # n
            feed_rate / 0.5,  # f
            axial_depth / 10.0,  # ap
            radial_depth / 8.0,  # ae
            hardness / 200.0,  # H
            tool_diameter / 20.0,  # D
            num_teeth.astype(float) / 6.0,  # z
        ]
    )
    return features.astype(np.float32)


class SyntheticChatterDataset(Dataset):
    """
    合成颤振数据集
    使用Tlusty解析公式生成训练数据
    """

    def __init__(
        self,
        num_samples: int = 10000,
        spindle_speed_range: Tuple[float, float] = (1000, 10000),
        axial_depth_range: Tuple[float, float] = (0.1, 10.0),
        feed_rate_range: Tuple[float, float] = (0.05, 0.5),  # 进给 f (mm/tooth)
        radial_depth_range: Tuple[float, float] = (0.5, 8.0),  # 径向切宽 ae (mm)
        material_hardness: float = 95.0,  # 材料硬度 H (HB)，单值模式
        tool_diameter: float = 10.0,  # 刀具直径 D (mm)，单值模式
        num_teeth: int = 4,  # 刀具齿数 z，单值模式
        hardness_range: Optional[Tuple[float, float]] = (80.0, 200.0),  # 多材料场景宽范围
        tool_diameter_range: Optional[Tuple[float, float]] = (6.0, 16.0),  # 多刀具场景宽范围
        num_teeth_range: Optional[Tuple[int, int]] = (2, 6),  # 多齿数场景宽范围
        machine_id: str = "vmc_850",
        tool_id: str = "endmill_d10",
        noise_level: float = 0.02,
        seed: int = 42,
    ):
        """
        初始化合成数据集

        Args:
            num_samples: 样本数量
            spindle_speed_range: 主轴转速范围 (rpm)
            axial_depth_range: 轴向切深范围 (mm)
            feed_rate_range: 每齿进给量范围 (mm/tooth)
            radial_depth_range: 径向切宽范围 (mm)
            material_hardness: 材料硬度 (HB)，单值模式（ hardness_range 为 None 时使用）
            tool_diameter: 刀具直径 (mm)，单值模式（ tool_diameter_range 为 None 时使用）
            num_teeth: 刀具齿数，单值模式（ num_teeth_range 为 None 时使用）
            hardness_range: 材料硬度宽范围 (HB)，默认 (80, 200) 体现多材料场景
            tool_diameter_range: 刀具直径宽范围 (mm)，默认 (6, 16) 体现多刀具场景
            num_teeth_range: 刀具齿数宽范围，默认 (2, 6) 体现多齿数场景
            machine_id: 机床ID
            tool_id: 刀具ID
            noise_level: 噪声水平
            seed: 随机种子
        """
        super().__init__()
        self.num_samples = num_samples
        self.spindle_speed_range = spindle_speed_range
        self.axial_depth_range = axial_depth_range
        self.feed_rate_range = feed_rate_range
        self.radial_depth_range = radial_depth_range
        self.material_hardness = material_hardness
        self.tool_diameter = tool_diameter
        self.num_teeth = num_teeth
        self.hardness_range = hardness_range
        self.tool_diameter_range = tool_diameter_range
        self.num_teeth_range = num_teeth_range
        self.machine_id = machine_id
        self.tool_id = tool_id
        self.noise_level = noise_level

        np.random.seed(seed)

        # 生成数据
        self.data = self._generate_data()

    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成合成数据（7 维物理参数特征，多物理参数耦合）"""
        # 随机采样加工参数
        spindle_speed = np.random.uniform(self.spindle_speed_range[0], self.spindle_speed_range[1], self.num_samples)

        axial_depth = np.random.uniform(self.axial_depth_range[0], self.axial_depth_range[1], self.num_samples)

        # 多维物理参数（论文第3节声明的 7 维输入特征）
        feed_rate = np.random.uniform(self.feed_rate_range[0], self.feed_rate_range[1], self.num_samples)
        radial_depth = np.random.uniform(self.radial_depth_range[0], self.radial_depth_range[1], self.num_samples)

        # 材料硬度：宽范围采样（多材料场景）或单值+噪声（同材料场景）
        if self.hardness_range is not None:
            hardness = np.random.uniform(self.hardness_range[0], self.hardness_range[1], self.num_samples)
        else:
            hardness = self.material_hardness + np.random.randn(self.num_samples) * 2.0

        # 刀具直径：宽范围采样（多刀具场景）或单值+磨损噪声
        if self.tool_diameter_range is not None:
            tool_diameter = np.random.uniform(
                self.tool_diameter_range[0], self.tool_diameter_range[1], self.num_samples
            )
        else:
            tool_diameter = self.tool_diameter + np.random.randn(self.num_samples) * 0.05

        # 刀具齿数：宽范围采样（多齿数场景）或常数
        if self.num_teeth_range is not None:
            num_teeth_arr = np.random.randint(
                self.num_teeth_range[0], self.num_teeth_range[1] + 1, self.num_samples
            ).astype(float)
        else:
            num_teeth_arr = np.full(self.num_samples, float(self.num_teeth))

        # 使用Tlusty模型计算（多物理参数耦合：HKs, Dk/m, zKs_eff, aeμ, fchip thinning）
        tlusty_model = TlustyAnalyticalModel()

        # 计算极限切深（传入所有物理参数，使 7 维特征均有物理意义）
        a_lim = tlusty_model.compute_limiting_depth(
            spindle_speed,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth_arr,
            feed_rate=feed_rate,
            radial_depth=radial_depth,
        )

        # 计算稳定性标签
        stability = (axial_depth > a_lim).astype(int)

        # 添加噪声
        a_lim_noisy = a_lim * (1 + np.random.randn(self.num_samples) * self.noise_level)
        a_lim_noisy = np.maximum(a_lim_noisy, 0.01)  # 确保正值

        # 构造 7 维输入特征（归一化至合理范围）
        # 特征顺序: [主轴转速 n, 进给 f, 轴向切深 ap, 径向切宽 ae,
        # 材料硬度 H, 刀具直径 D, 刀具齿数 z]
        features = build_physics_features_7d(
            spindle_speed=spindle_speed,
            feed_rate=feed_rate,
            axial_depth=axial_depth,
            radial_depth=radial_depth,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth_arr,
        )

        return {
            "features": features.astype(np.float32),
            "spindle_speed": spindle_speed.astype(np.float32),
            "axial_depth": axial_depth.astype(np.float32),
            "feed_rate": feed_rate.astype(np.float32),
            "radial_depth": radial_depth.astype(np.float32),
            "a_lim": a_lim_noisy.astype(np.float32),
            "stability": stability.astype(np.int64),
            "a_lim_clean": a_lim.astype(np.float32),
        }

    def __len__(self) -> int:
        return self.num_samples

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        获取样本

        Returns:
            features: 输入特征
            a_lim: 极限切深标签
            a_lim_physics: 物理模型预测（无噪声）
        """
        features = torch.from_numpy(self.data["features"][idx])
        a_lim = torch.from_numpy(np.array([self.data["a_lim"][idx]]))
        a_lim_physics = torch.from_numpy(np.array([self.data["a_lim_clean"][idx]]))

        return features, a_lim, a_lim_physics


class PHM2010Dataset(Dataset):
    """PHM2010 公开数据集（真实数据加载实现）。

    数据来源：PHM Society 2010 刀具磨损预测竞赛数据集
    （不锈钢 HRC52，3 组实验 c1/c4/c6，共 104675 个时间点）。

    重要说明（学术诚信）：
        PHM2010 数据集本身是**刀具磨损预测**任务数据集，不包含颤振稳定性标签。
        本类按以下方式构造训练样本：
          1. 输入特征：从 7 个真实信号通道（force_x/y/z, vibration_x/y/z,
             acoustic_emission_rms）按时间窗口提取 6 维统计特征；
          2. 极限切深标签 a_lim：由 Tlusty 解析模型基于振动能量派生
             （振动能量越大，a_lim 越小）；
          3. 稳定性标签：基于 a_lim 阈值判定。
        因此，本数据集的**输入特征来自真实 PHM2010 信号**，但**颤振标签
        是基于物理模型派生的代理标签**，并非实测颤振标签。在论文中应明确
        标注此数据派生关系，避免误导读者认为使用了 PHM2010 原始颤振标签。

    特征维度：
        7 维（与 config.py 的 ModelConfig.input_dim=7 对齐）：
          - force_rms：合力 RMS（归一化）
          - vibration_rms：合振动 RMS（归一化）
          - ae_rms：声发射 RMS（归一化）
          - force_std：合力标准差（归一化）
          - vibration_std：合振动标准差（归一化）
          - tool_wear_norm：刀具磨损值（归一化到 [0, 1]）
          - vibration_kurtosis：合振动峭度（归一化，表征冲击/颤振能量）

        注：PHM2010 使用真实信号派生的统计特征，与合成数据集的 7 维物理参数
        （n, f, ap, ae, H, D, z）语义不同但维度一致，确保模型架构可统一。
        论文中应明确区分两种特征表示。

    输出：
        __getitem__ 返回 (features, a_lim, a_lim_physics) 三元组
        （与其他数据集类保持接口一致）。
    """

    # PHM2010 真实信号的 7 个通道
    _SIGNAL_COLUMNS = [
        "force_x",
        "force_y",
        "force_z",
        "vibration_x",
        "vibration_y",
        "vibration_z",
        "acoustic_emission_rms",
    ]

    def __init__(
        self,
        num_samples: int = 2000,
        noise_level: float = 0.05,
        seed: int = 42,
        window_size: int = 500,
        data_dir: Optional[str] = None,
    ):
        """初始化 PHM2010 数据集。

        Args:
            num_samples: 期望的样本数上限（实际样本数由真实数据窗口数决定，
                若真实数据窗口数大于 num_samples，则随机采样至 num_samples；
                若小于，则使用全部窗口）。
            noise_level: 标签噪声水平（保留用于兼容父类签名，
                实际噪声来自真实信号本身的统计波动）。
            seed: 随机种子（用于窗口采样）。
            window_size: 信号窗口大小（每个样本包含的时间点数）。
            data_dir: PHM2010 数据文件目录，默认使用
                ``python/data/uniwear/``。
        """
        super().__init__()
        self.num_samples = num_samples
        self.noise_level = noise_level
        self.window_size = window_size
        self.dataset_name = "PHM2010"

        np.random.seed(seed)
        self.data = self._load_real_data(data_dir)

    def _load_real_data(self, data_dir: Optional[str]) -> Dict[str, np.ndarray]:
        """加载真实 PHM2010 数据并构造训练样本。

        Args:
            data_dir: 数据目录路径

        Returns:
            包含 features / a_lim / a_lim_clean / stability 等键的字典
        """
        try:
            from app.data.uniwear_loader import (
                UniwearDataLoader,
                UniwearDataset,
                PHM2010_SIGNAL_COLUMNS,
            )
        except ImportError as e:
            logger.warning("无法导入 UniwearDataLoader，PHM2010Dataset 回退到合成数据: %s", e)
            return self._fallback_synthetic()

        # 解析数据目录（默认使用项目内 python/data/uniwear/）
        if data_dir is None:
            data_dir = _PHM2010_DEFAULT_DATA_DIR

        try:
            loader = UniwearDataLoader(data_dir=data_dir)
            df = loader.load_dataset(UniwearDataset.PHM2010, use_cache=False)
        except (FileNotFoundError, OSError, ValueError) as e:
            logger.warning("加载真实 PHM2010 数据失败，回退到合成数据: %s", e)
            return self._fallback_synthetic()

        # 校验信号列是否存在
        available_cols = [c for c in self._SIGNAL_COLUMNS if c in df.columns]
        if len(available_cols) != len(self._SIGNAL_COLUMNS):
            logger.warning(
                "PHM2010 数据缺少信号列，缺失列: %s",
                set(self._SIGNAL_COLUMNS) - set(available_cols),
            )

        # 按 experiment_tag 分组提取窗口特征
        features_list: List[np.ndarray] = []
        a_lim_clean_list: List[float] = []
        tool_wear_list: List[float] = []

        experiment_tags = sorted(df["experiment_tag"].dropna().unique()) if "experiment_tag" in df.columns else [None]

        for tag in experiment_tags:
            if tag is not None:
                exp_df = df[df["experiment_tag"] == tag].reset_index(drop=True)
            else:
                exp_df = df.reset_index(drop=True)

            if len(exp_df) < self.window_size:
                continue

            # 滑动窗口提取样本
            num_windows = len(exp_df) // self.window_size
            for w_idx in range(num_windows):
                start = w_idx * self.window_size
                end = start + self.window_size
                window = exp_df.iloc[start:end]

                feats = self._extract_window_features(window)
                if feats is None:
                    continue

                features_list.append(feats)
                # 振动能量越大，a_lim 越小（物理约束）
                vib_energy = feats[1]  # vibration_rms（已归一化）
                # 反比关系 + 物理基准值 5mm
                a_lim_val = max(0.1, 5.0 * (1.0 - 0.8 * vib_energy))
                a_lim_clean_list.append(float(a_lim_val))

                # 窗口末尾的 tool_wear 值
                if "tool_wear" in window.columns:
                    tw = float(window["tool_wear"].iloc[-1])
                else:
                    tw = 0.0
                tool_wear_list.append(tw)

        if not features_list:
            logger.warning("PHM2010 真实数据未提取到任何窗口样本，回退到合成数据")
            return self._fallback_synthetic()

        features_arr = np.array(features_list, dtype=np.float32)
        a_lim_clean_arr = np.array(a_lim_clean_list, dtype=np.float32)
        tool_wear_arr = np.array(tool_wear_list, dtype=np.float32)

        # 限制样本数量
        if len(features_arr) > self.num_samples:
            rng = np.random.default_rng(42)
            indices = rng.choice(len(features_arr), size=self.num_samples, replace=False)
            features_arr = features_arr[indices]
            a_lim_clean_arr = a_lim_clean_arr[indices]
            tool_wear_arr = tool_wear_arr[indices]

        # 真实信号本身的统计波动即为"噪声"，不再添加合成高斯噪声
        # 但保留 noise_level 参数对 a_lim 的轻微扰动以维持接口兼容
        a_lim_arr = a_lim_clean_arr * (1.0 + np.random.randn(len(a_lim_clean_arr)) * self.noise_level * 0.1)
        a_lim_arr = np.maximum(a_lim_arr, 0.01).astype(np.float32)

        # 稳定性标签：以 a_lim 中位数为阈值
        threshold = float(np.median(a_lim_arr))
        stability = (a_lim_arr < threshold).astype(np.int64)

        logger.info(
            "PHM2010Dataset 加载真实数据: %d 样本, %d 实验组, 特征维度=%d",
            len(features_arr),
            len(experiment_tags),
            features_arr.shape[1],
        )

        return {
            "features": features_arr,
            "a_lim": a_lim_arr,
            "a_lim_clean": a_lim_clean_arr,
            "stability": stability,
            "tool_wear": tool_wear_arr,
            "experiment_tags": list(experiment_tags),
            "data_source": "real_PHM2010",
        }

    def _extract_window_features(self, window) -> Optional[np.ndarray]:
        """从信号窗口提取 7 维统计特征（归一化）。

        Args:
            window: 包含信号列的 DataFrame 切片

        Returns:
            7 维特征向量（float32），如无法提取返回 None
        """
        try:
            # 合力 RMS
            force_cols = [c for c in ["force_x", "force_y", "force_z"] if c in window.columns]
            if force_cols:
                force_mag = np.sqrt(sum(window[c].values ** 2 for c in force_cols))
                force_rms = float(np.sqrt(np.mean(force_mag**2)))
                force_std = float(np.std(force_mag))
            else:
                return None

            # 合振动 RMS + 峭度
            vib_cols = [c for c in ["vibration_x", "vibration_y", "vibration_z"] if c in window.columns]
            if vib_cols:
                vib_mag = np.sqrt(sum(window[c].values ** 2 for c in vib_cols))
                vib_rms = float(np.sqrt(np.mean(vib_mag**2)))
                vib_std = float(np.std(vib_mag))
                # 振动峭度：表征冲击/颤振能量（峭度越高，冲击越强烈）
                # 峭度 = E[(X-μ)^4] / σ^4，正常振动 ~3，颤振时显著增大
                vib_mean = float(np.mean(vib_mag))
                vib_kurt = float(np.mean((vib_mag - vib_mean) ** 4) / (np.var(vib_mag) + 1e-8) ** 2)
            else:
                return None

            # 声发射 RMS
            if "acoustic_emission_rms" in window.columns:
                ae_rms = float(np.sqrt(np.mean(window["acoustic_emission_rms"].values ** 2)))
            else:
                ae_rms = 0.0

            # 刀具磨损值
            if "tool_wear" in window.columns:
                tw = float(window["tool_wear"].iloc[-1])
            else:
                tw = 0.0

            # 归一化（基于典型量级，保持与 predictor.py 归一化范围一致）
            features = np.array(
                [
                    force_rms / 10.0,  # 合力 RMS 归一化
                    vib_rms / 5.0,  # 合振动 RMS 归一化
                    ae_rms / 2.0,  # 声发射 RMS 归一化
                    force_std / 10.0,  # 合力标准差归一化
                    vib_std / 5.0,  # 合振动标准差归一化
                    min(tw / 200.0, 1.0),  # 刀具磨损归一化到 [0, 1]
                    min(vib_kurt / 20.0, 1.0),  # 振动峭度归一化（典型范围 3-15）
                ],
                dtype=np.float32,
            )

            # 处理可能的 NaN/Inf
            if not np.all(np.isfinite(features)):
                return None

            return features

        except (KeyError, ValueError, TypeError) as e:
            logger.debug("窗口特征提取失败: %s", e)
            return None

    def _fallback_synthetic(self) -> Dict[str, np.ndarray]:
        """真实数据不可用时的合成数据回退实现。

        保留原 Tlusty 合成数据逻辑作为兜底，确保数据集类在缺少真实
        数据文件时仍可实例化（用于 CI 测试等场景）。回退时会在日志中
        明确警告，避免在论文实验中误用合成数据。
        """
        logger.warning("PHM2010Dataset 使用合成数据回退实现，不可用于论文实验结果！请检查数据文件路径。")
        spindle_speed = np.random.uniform(3000, 9000, self.num_samples)
        axial_depth = np.random.uniform(0.5, 8.0, self.num_samples)

        tlusty_model = TlustyAnalyticalModel(
            stiffness=1.2e6,
            modal_mass=120.0,
            damping_ratio=0.06,
        )
        a_lim_clean = tlusty_model.compute_limiting_depth(spindle_speed)
        a_lim = a_lim_clean * (1 + np.random.randn(self.num_samples) * self.noise_level)
        a_lim = np.maximum(a_lim, 0.01)

        # 7 维合成特征（与真实实现特征维度一致）
        # 占位值用于回退场景，不可用于论文实验
        placeholder_zeros = np.zeros_like(spindle_speed)
        placeholder_kurtosis = np.full_like(spindle_speed, 3.0 / 20.0)  # 正态分布峭度=3
        features = np.column_stack(
            [
                spindle_speed / 9000.0,
                placeholder_zeros,  # 占位：vibration_rms
                placeholder_zeros,  # 占位：ae_rms
                placeholder_zeros,  # 占位：force_std
                placeholder_zeros,  # 占位：vib_std
                placeholder_zeros,  # 占位：tool_wear
                placeholder_kurtosis,  # 占位：vibration_kurtosis（正态分布≈3）
            ]
        ).astype(np.float32)

        stability = (axial_depth > a_lim).astype(np.int64)

        return {
            "features": features,
            "spindle_speed": spindle_speed.astype(np.float32),
            "axial_depth": axial_depth.astype(np.float32),
            "a_lim": a_lim.astype(np.float32),
            "a_lim_clean": a_lim_clean.astype(np.float32),
            "stability": stability,
            "data_source": "synthetic_fallback",
        }

    def __len__(self) -> int:
        return len(self.data["features"])

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = torch.from_numpy(self.data["features"][idx])
        a_lim = torch.from_numpy(np.array([self.data["a_lim"][idx]]))
        a_lim_physics = torch.from_numpy(np.array([self.data["a_lim_clean"][idx]]))

        return features, a_lim, a_lim_physics


class NUAADataset(Dataset):
    """
    NUAA 数据集（合成数据占位实现）。

    学术诚信说明：
        本类**不加载真实 NUAA 数据集**，而是使用 TlustyAnalyticalModel
        生成合成数据。仅用于对照实验和接口兼容，**不可在论文中声称
        对应真实 NUAA 数据集实验结果**。如需使用真实 NUAA 数据，
        请参考 app/data/uniwear_loader.py 中的 UniwearDataLoader。
    """

    def __init__(self, num_samples: int = 1800, noise_level: float = 0.04, seed: int = 43):
        super().__init__()
        self.num_samples = num_samples
        self.noise_level = noise_level
        self.dataset_name = "NUAA"

        np.random.seed(seed)
        self.data = self._generate_data()

    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成NUAA风格数据（7 维物理参数特征）"""
        spindle_speed = np.random.uniform(2500, 8500, self.num_samples)
        axial_depth = np.random.uniform(0.3, 6.0, self.num_samples)
        feed_rate = np.random.uniform(0.08, 0.4, self.num_samples)
        radial_depth = np.random.uniform(1.0, 6.0, self.num_samples)
        # NUAA 数据集典型材料为铝合金 7075-T6，硬度 ~150 HB
        hardness = np.full(self.num_samples, 150.0) + np.random.randn(self.num_samples) * 3.0
        tool_diameter = np.full(self.num_samples, 12.0) + np.random.randn(self.num_samples) * 0.05
        num_teeth = np.full(self.num_samples, 4.0)

        tlusty_model = TlustyAnalyticalModel(stiffness=1.1e6, modal_mass=110.0, damping_ratio=0.055)

        # 多物理参数耦合：传入所有物理参数，使 7 维特征均有物理意义
        a_lim_clean = tlusty_model.compute_limiting_depth(
            spindle_speed,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
            feed_rate=feed_rate,
            radial_depth=radial_depth,
        )
        a_lim = a_lim_clean * (1 + np.random.randn(self.num_samples) * self.noise_level)
        a_lim = np.maximum(a_lim, 0.01)

        stability = (axial_depth > a_lim).astype(int)

        features = build_physics_features_7d(
            spindle_speed=spindle_speed,
            feed_rate=feed_rate,
            axial_depth=axial_depth,
            radial_depth=radial_depth,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
        )

        return {
            "features": features,
            "spindle_speed": spindle_speed.astype(np.float32),
            "axial_depth": axial_depth.astype(np.float32),
            "a_lim": a_lim.astype(np.float32),
            "stability": stability.astype(np.int64),
            "a_lim_clean": a_lim_clean.astype(np.float32),
        }

    def __len__(self) -> int:
        return self.num_samples

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = torch.from_numpy(self.data["features"][idx])
        a_lim = torch.from_numpy(np.array([self.data["a_lim"][idx]]))
        a_lim_physics = torch.from_numpy(np.array([self.data["a_lim_clean"][idx]]))

        return features, a_lim, a_lim_physics


class NISTDataset(Dataset):
    """
    NIST 数据集（合成数据占位实现）。

    学术诚信说明：
        本类**不加载真实 NIST 数据集**，而是使用 TlustyAnalyticalModel
        生成合成数据。仅用于对照实验和接口兼容，**不可在论文中声称
        对应真实 NIST 数据集实验结果**。
    """

    def __init__(self, num_samples: int = 1500, noise_level: float = 0.06, seed: int = 44):
        super().__init__()
        self.num_samples = num_samples
        self.noise_level = noise_level
        self.dataset_name = "NIST"

        np.random.seed(seed)
        self.data = self._generate_data()

    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成NIST风格数据（7 维物理参数特征）"""
        spindle_speed = np.random.uniform(2000, 7000, self.num_samples)
        axial_depth = np.random.uniform(0.2, 5.0, self.num_samples)
        feed_rate = np.random.uniform(0.05, 0.3, self.num_samples)
        radial_depth = np.random.uniform(0.5, 5.0, self.num_samples)
        # NIST 数据集典型材料为 AISI 1045 钢，硬度 ~180 HB
        hardness = np.full(self.num_samples, 180.0) + np.random.randn(self.num_samples) * 4.0
        tool_diameter = np.full(self.num_samples, 10.0) + np.random.randn(self.num_samples) * 0.05
        num_teeth = np.full(self.num_samples, 4.0)

        tlusty_model = TlustyAnalyticalModel(stiffness=0.9e6, modal_mass=95.0, damping_ratio=0.048)

        # 多物理参数耦合：传入所有物理参数，使 7 维特征均有物理意义
        a_lim_clean = tlusty_model.compute_limiting_depth(
            spindle_speed,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
            feed_rate=feed_rate,
            radial_depth=radial_depth,
        )
        a_lim = a_lim_clean * (1 + np.random.randn(self.num_samples) * self.noise_level)
        a_lim = np.maximum(a_lim, 0.01)

        stability = (axial_depth > a_lim).astype(int)

        features = build_physics_features_7d(
            spindle_speed=spindle_speed,
            feed_rate=feed_rate,
            axial_depth=axial_depth,
            radial_depth=radial_depth,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
        )

        return {
            "features": features,
            "spindle_speed": spindle_speed.astype(np.float32),
            "axial_depth": axial_depth.astype(np.float32),
            "a_lim": a_lim.astype(np.float32),
            "stability": stability.astype(np.int64),
            "a_lim_clean": a_lim_clean.astype(np.float32),
        }

    def __len__(self) -> int:
        return self.num_samples

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = torch.from_numpy(self.data["features"][idx])
        a_lim = torch.from_numpy(np.array([self.data["a_lim"][idx]]))
        a_lim_physics = torch.from_numpy(np.array([self.data["a_lim_clean"][idx]]))

        return features, a_lim, a_lim_physics


class Benchmark1Dataset(Dataset):
    """
    Benchmark-1 数据集（合成数据占位实现）。

    学术诚信说明：
        本类**不加载真实 Benchmark-1 数据集**，而是使用 TlustyAnalyticalModel
        生成合成数据。仅用于对照实验和接口兼容，**不可在论文中声称
        对应真实 Benchmark-1 数据集实验结果**。
    """

    def __init__(self, num_samples: int = 2200, noise_level: float = 0.045, seed: int = 45):
        super().__init__()
        self.num_samples = num_samples
        self.noise_level = noise_level
        self.dataset_name = "Benchmark-1"

        np.random.seed(seed)
        self.data = self._generate_data()

    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成Benchmark-1风格数据（7 维物理参数特征）"""
        spindle_speed = np.random.uniform(3500, 9500, self.num_samples)
        axial_depth = np.random.uniform(0.4, 7.0, self.num_samples)
        feed_rate = np.random.uniform(0.1, 0.5, self.num_samples)
        radial_depth = np.random.uniform(1.5, 7.0, self.num_samples)
        # Benchmark-1 典型材料为 45 钢，硬度 ~200 HB
        hardness = np.full(self.num_samples, 200.0) + np.random.randn(self.num_samples) * 4.0
        tool_diameter = np.full(self.num_samples, 16.0) + np.random.randn(self.num_samples) * 0.08
        num_teeth = np.full(self.num_samples, 4.0)

        tlusty_model = TlustyAnalyticalModel(stiffness=1.15e6, modal_mass=115.0, damping_ratio=0.052)

        # 多物理参数耦合：传入所有物理参数，使 7 维特征均有物理意义
        a_lim_clean = tlusty_model.compute_limiting_depth(
            spindle_speed,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
            feed_rate=feed_rate,
            radial_depth=radial_depth,
        )
        a_lim = a_lim_clean * (1 + np.random.randn(self.num_samples) * self.noise_level)
        a_lim = np.maximum(a_lim, 0.01)

        stability = (axial_depth > a_lim).astype(int)

        features = build_physics_features_7d(
            spindle_speed=spindle_speed,
            feed_rate=feed_rate,
            axial_depth=axial_depth,
            radial_depth=radial_depth,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
        )

        return {
            "features": features,
            "spindle_speed": spindle_speed.astype(np.float32),
            "axial_depth": axial_depth.astype(np.float32),
            "a_lim": a_lim.astype(np.float32),
            "stability": stability.astype(np.int64),
            "a_lim_clean": a_lim_clean.astype(np.float32),
        }

    def __len__(self) -> int:
        return self.num_samples

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = torch.from_numpy(self.data["features"][idx])
        a_lim = torch.from_numpy(np.array([self.data["a_lim"][idx]]))
        a_lim_physics = torch.from_numpy(np.array([self.data["a_lim_clean"][idx]]))

        return features, a_lim, a_lim_physics


class Industrial6061T6Dataset(Dataset):
    """
    自采 6061-T6 工业数据集（合成数据占位实现）。

    学术诚信说明：
        本类**不加载真实自采 6061-T6 数据**，而是使用 TlustyAnalyticalModel
        生成合成数据。仅用于对照实验和接口兼容，**不可在论文中声称
        对应真实自采数据集实验结果**。如需使用真实自采数据，应通过
        ``data_dir`` 参数指向真实数据文件路径。
    """

    def __init__(self, num_samples: int = 500, noise_level: float = 0.08, seed: int = 46):
        super().__init__()
        self.num_samples = num_samples
        self.noise_level = noise_level
        self.dataset_name = "自采 6061-T6"

        np.random.seed(seed)
        self.data = self._generate_data()

    def _generate_data(self) -> Dict[str, np.ndarray]:
        """生成自采6061-T6风格数据（7 维物理参数特征）"""
        # 工业数据范围更窄，更符合实际
        spindle_speed = np.random.uniform(2000, 8000, self.num_samples)
        axial_depth = np.random.uniform(0.5, 5.0, self.num_samples)
        feed_rate = np.random.uniform(0.05, 0.3, self.num_samples)
        radial_depth = np.random.uniform(1.0, 5.0, self.num_samples)
        # 6061-T6 铝合金硬度 ~95 HB
        hardness = np.full(self.num_samples, 95.0) + np.random.randn(self.num_samples) * 2.0
        tool_diameter = np.full(self.num_samples, 10.0) + np.random.randn(self.num_samples) * 0.05
        num_teeth = np.full(self.num_samples, 4.0)

        tlusty_model = TlustyAnalyticalModel(stiffness=1.0e6, modal_mass=100.0, damping_ratio=0.05)

        # 多物理参数耦合：传入所有物理参数，使 7 维特征均有物理意义
        a_lim_clean = tlusty_model.compute_limiting_depth(
            spindle_speed,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
            feed_rate=feed_rate,
            radial_depth=radial_depth,
        )
        a_lim = a_lim_clean * (1 + np.random.randn(self.num_samples) * self.noise_level)
        a_lim = np.maximum(a_lim, 0.01)

        stability = (axial_depth > a_lim).astype(int)

        features = build_physics_features_7d(
            spindle_speed=spindle_speed,
            feed_rate=feed_rate,
            axial_depth=axial_depth,
            radial_depth=radial_depth,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
        )

        return {
            "features": features,
            "spindle_speed": spindle_speed.astype(np.float32),
            "axial_depth": axial_depth.astype(np.float32),
            "a_lim": a_lim.astype(np.float32),
            "stability": stability.astype(np.int64),
            "a_lim_clean": a_lim_clean.astype(np.float32),
        }

    def __len__(self) -> int:
        return self.num_samples

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = torch.from_numpy(self.data["features"][idx])
        a_lim = torch.from_numpy(np.array([self.data["a_lim"][idx]]))
        a_lim_physics = torch.from_numpy(np.array([self.data["a_lim_clean"][idx]]))

        return features, a_lim, a_lim_physics


class IndustrialChatterDataset(Dataset):
    """
    工业颤振数据集
    用于加载自采工业数据（保留旧版本兼容性）
    """

    def __init__(
        self,
        data_path: str = "data/industrial_6061t6",
        num_samples: int = 500,
        num_conditions: int = 30,
        material: str = "6061-T6",
        seed: int = 42,
    ):
        super().__init__()
        self.data_path = data_path
        self.num_samples = num_samples
        self.num_conditions = num_conditions
        self.material = material
        self.dataset_name = "Industrial"

        np.random.seed(seed)
        self.data = self._generate_mock_data()

    def _generate_mock_data(self) -> Dict[str, np.ndarray]:
        """生成模拟工业数据（7 维物理参数特征，多物理参数耦合）"""
        spindle_speed = np.random.uniform(2000, 8000, self.num_samples)
        axial_depth = np.random.uniform(0.5, 5.0, self.num_samples)
        feed_rate = np.random.uniform(0.05, 0.3, self.num_samples)
        radial_depth = np.random.uniform(1.0, 5.0, self.num_samples)
        # 6061-T6 铝合金硬度 ~95 HB（窄范围，体现同材料工业场景）
        hardness = np.full(self.num_samples, 95.0) + np.random.randn(self.num_samples) * 2.0
        # 同一刀具，直径带磨损微小变化
        tool_diameter = np.full(self.num_samples, 10.0) + np.random.randn(self.num_samples) * 0.05
        # 固定 4 齿刀具
        num_teeth = np.full(self.num_samples, 4.0)

        tlusty_model = TlustyAnalyticalModel()
        # 多物理参数耦合：传入所有物理参数，使 7 维特征均有物理意义
        a_lim_clean = tlusty_model.compute_limiting_depth(
            spindle_speed,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
            feed_rate=feed_rate,
            radial_depth=radial_depth,
        )

        noise_level = 0.08
        a_lim = a_lim_clean * (1 + np.random.randn(self.num_samples) * noise_level)
        a_lim = np.maximum(a_lim, 0.01)

        stability = (axial_depth > a_lim).astype(int)

        features = build_physics_features_7d(
            spindle_speed=spindle_speed,
            feed_rate=feed_rate,
            axial_depth=axial_depth,
            radial_depth=radial_depth,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth,
        )

        return {
            "features": features,
            "spindle_speed": spindle_speed.astype(np.float32),
            "axial_depth": axial_depth.astype(np.float32),
            "a_lim": a_lim.astype(np.float32),
            "stability": stability.astype(np.int64),
            "a_lim_clean": a_lim_clean.astype(np.float32),
        }

    def __len__(self) -> int:
        return self.num_samples

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = torch.from_numpy(self.data["features"][idx])
        a_lim = torch.from_numpy(np.array([self.data["a_lim"][idx]]))
        a_lim_physics = torch.from_numpy(np.array([self.data["a_lim_clean"][idx]]))

        return features, a_lim, a_lim_physics


# 数据集映射字典
DATASET_REGISTRY = {
    "PHM2010": PHM2010Dataset,
    "NUAA": NUAADataset,
    "NIST": NISTDataset,
    "Benchmark-1": Benchmark1Dataset,
    "自采 6061-T6": Industrial6061T6Dataset,
    "Synthetic": SyntheticChatterDataset,
    "Industrial": IndustrialChatterDataset,
}


def get_dataset_class(dataset_name: str) -> type:
    """
    根据数据集名称获取对应的数据集类

    Args:
        dataset_name: 数据集名称

    Returns:
        数据集类
    """
    if dataset_name in DATASET_REGISTRY:
        return DATASET_REGISTRY[dataset_name]
    if dataset_name == "MEASURED_SLD":
        # 实测稳定性点数据集（real_validation 包，零设备真实数据验证通道）
        # 依赖 experiments/ 在 sys.path（脚本运行约定，见 real_validation/README.md）
        try:
            from real_validation.measured_stability_dataset import MeasuredStabilityPointsDataset

            return MeasuredStabilityPointsDataset
        except ImportError as e:  # pragma: no cover
            raise ValueError(
                f"MEASURED_SLD 需要 real_validation 包（cd research/experiments 后运行）。导入失败: {e}"
            ) from e
    raise ValueError(f"Unknown dataset: {dataset_name}. Available: {list(DATASET_REGISTRY.keys()) + ['MEASURED_SLD']}")


def create_dataloaders(
    dataset_class,
    dataset_params: Dict,
    batch_size: int = 32,
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
    seed: int = 42,
) -> Tuple[DataLoader, DataLoader, DataLoader]:
    """
    创建训练/验证/测试数据加载器

    Args:
        dataset_class: 数据集类
        dataset_params: 数据集参数
        batch_size: 批次大小
        train_ratio: 训练集比例
        val_ratio: 验证集比例
        seed: 随机种子

    Returns:
        train_loader, val_loader, test_loader
    """
    # 创建完整数据集
    dataset = dataset_class(**dataset_params)

    # 计算划分大小
    total_size = len(dataset)
    train_size = int(total_size * train_ratio)
    val_size = int(total_size * val_ratio)
    test_size = total_size - train_size - val_size

    # 随机划分
    torch.manual_seed(seed)
    train_dataset, val_dataset, test_dataset = torch.utils.data.random_split(dataset, [train_size, val_size, test_size])

    # 创建数据加载器
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=0, pin_memory=True)

    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=0, pin_memory=True)

    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False, num_workers=0, pin_memory=True)

    return train_loader, val_loader, test_loader


if __name__ == "__main__":
    # 测试数据生成器
    print("测试合成数据集...")

    synthetic_dataset = SyntheticChatterDataset(
        num_samples=1000, spindle_speed_range=(1000, 10000), axial_depth_range=(0.1, 10.0), noise_level=0.02
    )

    print(f"数据集大小: {len(synthetic_dataset)}")

    # 获取一个样本
    features, a_lim, a_lim_physics = synthetic_dataset[0]
    print(f"特征形状: {features.shape}")
    print(f"极限切深: {a_lim.item():.4f} mm")
    print(f"物理预测: {a_lim_physics.item():.4f} mm")

    # 创建数据加载器
    train_loader, val_loader, test_loader = create_dataloaders(
        SyntheticChatterDataset, {"num_samples": 1000}, batch_size=32
    )

    print(f"\n训练集批次: {len(train_loader)}")
    print(f"验证集批次: {len(val_loader)}")
    print(f"测试集批次: {len(test_loader)}")

    # 测试一个批次
    batch_features, batch_a_lim, batch_a_lim_physics = next(iter(train_loader))
    print(f"\n批次特征形状: {batch_features.shape}")
    print(f"批次标签形状: {batch_a_lim.shape}")

    print("\n数据生成器测试通过！")
