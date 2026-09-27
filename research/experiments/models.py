"""
DL-LNN 模型实现
包含连续时间液态时间常数网络及其变体
"""

import warnings

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional
import numpy as np

# 连续时间 ODE 求解器（torchdiffeq）
# 用于实现真正的 LTC ODE 积分，见 ACADEMIC_REVIEW_REPORT.md AR-03
# 软依赖：未安装时降级为一阶 Euler 方法，但会打印警告
try:
    from torchdiffeq import odeint as _torchdiffeq_odeint

    _HAS_TORCHDIFFEQ = True
except ImportError:  # pragma: no cover - 降级路径
    _HAS_TORCHDIFFEQ = False
    warnings.warn(
        "torchdiffeq 未安装，LTCCell 将降级为一阶 Euler 方法。"
        "论文声称的连续时间 ODE 优势无法体现，请执行：pip install torchdiffeq==0.2.3 "
        "（见 ACADEMIC_REVIEW_REPORT.md AR-03）",
        RuntimeWarning,
        stacklevel=2,
    )


class LTCODEFunc(nn.Module):
    """
    LTC ODE 右端函数：dh/dt = f(h, x)

    实现 LTC 论文 (Hasani et al., 2021) 的连续时间动力学：
        dh/dt = ( -h + tanh(W x + U h + b) ) / tau

    该函数被 torchdiffeq.odeint 调用以自适应步长积分，
    实现"连续时间"的物理语义（非定步长 Euler）。
    """

    def __init__(self, input_size: int, hidden_size: int):
        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size

        # 可学习参数（与原 LTCCell 保持同名以兼容旧检查点）
        self.W = nn.Parameter(torch.randn(hidden_size, input_size))
        self.U = nn.Parameter(torch.randn(hidden_size, hidden_size))
        self.bias = nn.Parameter(torch.zeros(hidden_size))

        # 可学习时间常数 τ（强制 > 0）
        self.tau = nn.Parameter(torch.ones(hidden_size) * 0.1)

        # 缓存当前批次的输入 x（odeint 调用时 t 是标量时间，无 x）
        self._current_x: Optional[torch.Tensor] = None

        # Xavier 初始化
        nn.init.xavier_uniform_(self.W)
        nn.init.xavier_uniform_(self.U)

    def set_input(self, x: torch.Tensor) -> None:
        """设置当前批次的输入 x，供 ODE 积分过程中读取"""
        self._current_x = x

    def forward(self, t: torch.Tensor, h: torch.Tensor) -> torch.Tensor:
        # t 是 odeint 传入的时间标量，本模型不显式依赖 t（自治系统）
        x = self._current_x
        if x is None:
            raise RuntimeError("LTCODEFunc.set_input() 必须在 odeint 调用前执行")

        tau = torch.clamp(self.tau, min=0.01)
        dh = torch.tanh(torch.mm(x, self.W.t()) + torch.mm(h, self.U.t()) + self.bias)
        # LTC ODE: dh/dt = (dh - h) / tau
        return (dh - h) / tau.unsqueeze(0)


class LTCCell(nn.Module):
    """
    LTC 单元
    实现液态时间常数机制

    求解策略：
        - 优先使用 torchdiffeq.odeint 的自适应求解器（dopri5）
          实现真正的连续时间 ODE 积分（论文方法描述）
        - 数值发散时（真实数据上 dopri5 会因刚性段报 "underflow in dt"，
          或"成功"返回含 NaN 的解，见《真实数据验证现状》§五）自动降级为
          一阶 Euler 完成当前步——训练不中断，且发一级 warning 提示
    """

    # Euler 降级告警只发一次，避免长序列训练刷屏
    _degrade_warned = False

    # 同一单元累计失败达此次数后锁定 Euler：刚性数据上每步 dopri5
    # 抛异常再捕获的开销远超求解本身（实测数倍减速），锁存后直接走
    # Euler；数据良性时永远不会触发，dopri5 精度照常保留
    _max_ode_failures = 3

    def __init__(self, input_size: int, hidden_size: int, solver: str = "dopri5"):
        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.solver = solver
        self._ode_failures = 0

        # ODE 右端函数（封装 W/U/bias/tau 参数）
        self.ode_func = LTCODEFunc(input_size, hidden_size)

    @property
    def W(self) -> nn.Parameter:
        """兼容旧检查点访问"""
        return self.ode_func.W

    @property
    def U(self) -> nn.Parameter:
        """兼容旧检查点访问"""
        return self.ode_func.U

    @property
    def bias(self) -> nn.Parameter:
        """兼容旧检查点访问"""
        return self.ode_func.bias

    @property
    def tau(self) -> nn.Parameter:
        """兼容旧检查点访问"""
        return self.ode_func.tau

    def forward(self, x: torch.Tensor, h: torch.Tensor, dt: float = 0.1) -> torch.Tensor:
        """
        前向传播

        Args:
            x: 输入 [batch_size, input_size]
            h: 隐藏状态 [batch_size, hidden_size]
            dt: 时间步长（在 [0, dt] 区间内积分 ODE）

        Returns:
            新的隐藏状态
        """
        if _HAS_TORCHDIFFEQ and self._ode_failures < self._max_ode_failures:
            # 连续时间 ODE 积分：在 [0, dt] 区间内求解 dh/dt = f(h, x)
            # dopri5 为自适应步长 Runge-Kutta 求解器，精度优于定步长 Euler
            self.ode_func.set_input(x)
            t_span = torch.tensor(
                [0.0, float(dt)],
                device=h.device,
                dtype=h.dtype,
            )
            degrade_reason = ""
            try:
                # odeint 返回 [2, batch, hidden]，取终点状态
                h_new = _torchdiffeq_odeint(self.ode_func, h, t_span, method=self.solver)[-1]
            except Exception as exc:  # 数值发散（如 underflow in dt nan）→ 降级
                degrade_reason = f"求解器异常: {exc}"
            else:
                if not torch.isfinite(h_new).all():
                    # 求解器"成功"但产出 NaN/Inf（stage-2 NaN 的直接病灶）
                    degrade_reason = "输出含非有限值"
            if not degrade_reason:
                return h_new
            self._ode_failures += 1
            if not LTCCell._degrade_warned:
                warnings.warn(
                    f"odeint({self.solver}) 数值求解失败（{degrade_reason}），"
                    f"当前步降级为一阶 Euler；累计 {self._max_ode_failures} 次后"
                    "锁定 Euler。训练可继续，但应排查输入刚性/学习率。",
                    RuntimeWarning,
                    stacklevel=2,
                )
                LTCCell._degrade_warned = True

        return self._euler_step(x, h, dt)

    def _euler_step(self, x: torch.Tensor, h: torch.Tensor, dt: float) -> torch.Tensor:
        """一阶 Euler 降级求解（torchdiffeq 缺失或数值发散时使用）。"""
        tau = torch.clamp(self.ode_func.tau, min=0.01)
        dh = torch.tanh(torch.mm(x, self.ode_func.W.t()) + torch.mm(h, self.ode_func.U.t()) + self.ode_func.bias)
        return h + dt * (dh - h) / tau.unsqueeze(0)


class DLLNNModel(nn.Module):
    """
    DL-LNN 模型
    连续时间液态时间常数网络用于颤振预测
    """

    def __init__(
        self,
        input_dim: int = 7,
        hidden_dim: int = 64,
        num_layers: int = 3,
        output_dim: int = 1,
        dt: float = 0.1,
        dropout: float = 0.2,
    ):
        """
        初始化DL-LNN模型

        Args:
            input_dim: 输入维度（默认 7，与 config.py ModelConfig.input_dim 一致）
            hidden_dim: 隐藏层维度
            num_layers: LTC层数
            output_dim: 输出维度
            dt: 时间步长
            dropout: dropout率
        """
        super().__init__()

        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.output_dim = output_dim
        self.dt = dt

        # 输入投影层
        self.input_proj = nn.Linear(input_dim, hidden_dim)

        # LTC层
        self.ltc_cells = nn.ModuleList([LTCCell(hidden_dim, hidden_dim) for _ in range(num_layers)])

        # 输出层
        self.output_proj = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, output_dim),
        )

        # 初始化隐藏状态
        self.hidden_states = None

    def init_hidden(self, batch_size: int, device: torch.device) -> None:
        """初始化隐藏状态"""
        self.hidden_states = [torch.zeros(batch_size, self.hidden_dim, device=device) for _ in range(self.num_layers)]

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        前向传播

        Args:
            x: 输入 [batch_size, input_dim]

        Returns:
            输出 [batch_size, output_dim]
        """
        batch_size = x.size(0)
        device = x.device

        # 初始化隐藏状态（每个批次都重新初始化，避免计算图持久化）
        # 使用 zeros_like 风格但确保 requires_grad 正确传播
        hidden_states = []
        for _ in range(self.num_layers):
            h = torch.zeros(batch_size, self.hidden_dim, device=device)
            hidden_states.append(h)

        # 输入投影
        h = self.input_proj(x)

        # 通过 LTC 层
        for i, ltc_cell in enumerate(self.ltc_cells):
            hidden_states[i] = ltc_cell(h, hidden_states[i], self.dt)
            h = hidden_states[i]

        # 输出投影
        output = self.output_proj(h)

        return output

    def reset_hidden(self):
        """重置隐藏状态"""
        self.hidden_states = None


class DifferentiableTlustyPhysics(nn.Module):
    """
    可微 Tlusty 解析物理分支（PyTorch 实现）

    实现论文第3节声称的"梯度方向一致性"前提：物理分支预测 y_physics 必须是
    输入 x 的可微函数，使 autograd.grad(y_physics, x) 可计算，从而真正实现
    L_pcc = ||∇_x y_pred - ∇_x y_physics||²。

    与 data_generator.py 的 TlustyAnalyticalModel 数值等价（向量化 + soft min 替代 hard min），
    参数完全一致：stiffness=1e6, modal_mass=100, damping_ratio=0.05,
    cutting_force_coeff=2000 N/mm²。

    反归一化常量来自 data_generator.build_physics_features_7d:
        n / 10000, f / 0.5, ap / 10, ae / 8, H / 200, D / 20, z / 6

    soft min 近似：
        hard min 不可微（离散选择），用 -logsumexp(-tau·a) / tau 替代。
        tau 越大越接近 hard min，但梯度越小；tau=100 为合理折中。
        无效叶瓣（Re(G)>=0 或 a<=0 或 n<=0）用大值替代，使其不参与 min。
    """

    def __init__(
        self,
        stiffness: float = 1e6,
        modal_mass: float = 100.0,
        damping_ratio: float = 0.05,
        cutting_force_coeff: float = 2000.0,
        num_lobes: int = 10,
        soft_min_tau: float = 100.0,
    ):
        super().__init__()

        # 物理常数（与 TlustyAnalyticalModel 一致）
        self.register_buffer("k_base", torch.tensor(float(stiffness)))
        self.register_buffer("m_base", torch.tensor(float(modal_mass)))
        # damping = 2 * zeta * sqrt(k * m)
        damping = 2 * damping_ratio * (stiffness * modal_mass) ** 0.5
        self.register_buffer("c_base", torch.tensor(float(damping)))
        # Ks_base: N/mm² N/m² (×1e6)
        self.register_buffer("Ks_base", torch.tensor(float(cutting_force_coeff) * 1e6))

        self.num_lobes = int(num_lobes)
        self.soft_min_tau = float(soft_min_tau)

        # 反归一化常量（来自 build_physics_features_7d）
        self.register_buffer("n_scale", torch.tensor(10000.0))
        self.register_buffer("f_scale", torch.tensor(0.5))
        self.register_buffer("ae_scale", torch.tensor(8.0))
        self.register_buffer("H_scale", torch.tensor(200.0))
        self.register_buffer("D_scale", torch.tensor(20.0))
        self.register_buffer("z_scale", torch.tensor(6.0))

        # 叶瓣数向量 [num_lobes]
        self.register_buffer("lobe_j", torch.arange(1, num_lobes + 1, dtype=torch.float32))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        从归一化输入 x 计算可微的物理预测 a_lim

        Args:
            x: 归一化输入 [batch_size, 7] = [n, f, ap, ae, H, D, z]

        Returns:
            a_lim: 物理极限切深 [batch_size, 1]（单位 mm，范围 [0.1, 20]）
        """
        # 反归一化到真实物理量
        n_rpm = x[:, 0:1] * self.n_scale  # [B, 1]
        f_rate = x[:, 1:2] * self.f_scale  # [B, 1]
        ae = x[:, 3:4] * self.ae_scale  # [B, 1]
        H = x[:, 4:5] * self.H_scale  # [B, 1]
        D = x[:, 5:6] * self.D_scale  # [B, 1]
        z = x[:, 6:7] * self.z_scale  # [B, 1]

        # 输入域钳制：真实数据（PHM2010）在缺失特征上取 0，而 (H/200)**0.8
        # 在 0 处 backward 为 inf，0×inf=NaN 污染全图（stage-2 NaN 的最终
        # 病灶，见《真实数据验证现状》§五）。钳到物理域下界：前向语义不变
        # （0 硬度≈最软材料），梯度处处有限。
        n_rpm = torch.clamp(n_rpm, min=1.0)  # 转速 ≥1 rpm
        f_rate = torch.clamp(f_rate, min=1e-4)  # 每齿进给 ≥1e-4 mm
        ae = torch.clamp(ae, min=1e-3)  # 径向切宽 ≥1 µm
        H = torch.clamp(H, min=1.0)  # 硬度 ≥1 HB
        D = torch.clamp(D, min=0.1)  # 刀具直径 ≥0.1 mm
        z = torch.clamp(z, min=1.0)  # 齿数 ≥1

        # 多物理参数耦合（向量化，与 compute_limiting_depth 一致）
        # 1. 硬度 H Ks
        Ks_eff = self.Ks_base * (H / 200.0) ** 0.8
        # 2. 齿数 z 有效切削力
        Ks_eff = Ks_eff * (z / 4.0)
        # 3. 进给 f 切屑变薄非线性效应
        Ks_eff = Ks_eff * (1.0 + 0.15 * (f_rate - 0.25) / 0.25)
        # 4. 刀具直径 D 刚度与模态质量
        D_ratio = (D / 10.0) ** 2
        k_eff = self.k_base * D_ratio
        m_eff = self.m_base * D_ratio
        c_eff = self.c_base * D_ratio
        # 5. 径向切宽 ae 方向因子 μ
        mu_dir = 0.5 * (1.0 + ae / 8.0)

        # 向量化叶瓣计算 [B, num_lobes]
        # f_c = j * n / 60
        j = self.lobe_j.unsqueeze(0)  # [1, num_lobes]
        f_c = j * n_rpm / 60.0  # [B, num_lobes]
        omega_c = 2.0 * torch.pi * f_c  # [B, num_lobes]

        # 复频率响应 G(jω) 实部
        denom_real = k_eff - m_eff * omega_c**2  # [B, num_lobes]
        denom_imag = c_eff * omega_c  # [B, num_lobes]
        real_G = denom_real / (denom_real**2 + denom_imag**2 + 1e-12)

        # Tlusty: a_lim = -1 / (2 · Ks · μ · Re(G))，仅 Re(G)<0 时为正
        # 广播：Ks_eff [B,1], mu_dir [B,1], real_G [B,num_lobes]
        a_vals = -1.0 / (2.0 * Ks_eff * mu_dir * real_G + 1e-12)  # [B, num_lobes]

        # 处理无效叶瓣：Re(G)>=0 或 a<=0 或 n<=0 用大值替代（不参与 min）
        invalid = (real_G >= 0) | (a_vals <= 0) | (n_rpm <= 0)
        a_vals = torch.where(invalid, torch.full_like(a_vals, 1e6), a_vals)

        # soft min: -logsumexp(-tau·a) / tau （可微近似 hard min）
        soft_min = -torch.logsumexp(-self.soft_min_tau * a_vals, dim=1, keepdim=True) / self.soft_min_tau

        # m mm
        a_lim = soft_min * 1000.0

        # 范围限制 [0.1, 20] mm
        a_lim = torch.clamp(a_lim, min=0.1, max=20.0)

        return a_lim


class DLLNNWithPhysics(nn.Module):
    """
    DL-LNN 带物理分支的完整模型
    包含数据驱动分支和解析物理分支
    """

    def __init__(
        self,
        input_dim: int = 7,
        hidden_dim: int = 64,
        num_layers: int = 3,
        output_dim: int = 1,
        dt: float = 0.1,
        dropout: float = 0.2,
    ):
        super().__init__()

        # 数据驱动分支 (LTC)
        self.ltc_branch = DLLNNModel(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            output_dim=output_dim,
            dt=dt,
            dropout=dropout,
        )

        # 门控融合层
        self.gate = nn.Sequential(nn.Linear(input_dim, 32), nn.ReLU(), nn.Linear(32, 1), nn.Sigmoid())

        # 物理分支参数（可学习）
        self.physics_scale = nn.Parameter(torch.ones(1))
        self.physics_bias = nn.Parameter(torch.zeros(1))

        # 可微 Tlusty 解析物理分支（固定参数，非可学习）
        # 用于实现论文第3节 L_pcc 梯度方向一致性（AR-05 修复）
        self.physics_branch = DifferentiableTlustyPhysics()

    def compute_differentiable_physics(self, x: torch.Tensor) -> torch.Tensor:
        """
        计算可微的物理分支预测（用于 L_pcc 梯度一致性损失）

        论文第3节 L_pcc = ||∇_x y_pred - ∇_x y_physics||² 要求 y_physics 是 x 的可微函数。
        此方法通过可微 Tlusty 解析公式从 x 在线计算 y_physics，保证 autograd.grad 可计算。

        Args:
            x: 归一化输入 [batch_size, 7]，requires_grad 应为 True

        Returns:
            y_physics_diff: 可微物理预测 [batch_size, 1]（原始尺度，未做 target 归一化）
        """
        return self.physics_branch(x)

    def forward(
        self, x: torch.Tensor, physics_pred: Optional[torch.Tensor] = None
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        前向传播

        Args:
            x: 输入特征 [batch_size, input_dim]
            physics_pred: 物理模型预测 [batch_size, output_dim]
                         （预计算值，用于门控融合；若 None 则用可微物理分支）

        Returns:
            final_pred: 最终预测
            ltc_pred: LTC分支预测
        """
        # LTC分支预测
        ltc_pred = self.ltc_branch(x)

        # 如果没有物理预测，使用可微物理分支（保证门控融合仍有物理输入）
        if physics_pred is None:
            physics_pred = self.physics_branch(x)
            # 仅用 LTC 分支（保持与原 None 行为一致，避免阶段一无物理分支干扰）
            return ltc_pred, ltc_pred

        # 门控融合
        alpha = self.gate(x)
        final_pred = alpha * ltc_pred + (1 - alpha) * (self.physics_scale * physics_pred + self.physics_bias)

        return final_pred, ltc_pred

    def reset_hidden(self):
        """重置隐藏状态"""
        self.ltc_branch.reset_hidden()


class BaselineLSTM(nn.Module):
    """基线LSTM模型"""

    def __init__(self, input_dim: int = 7, hidden_dim: int = 64, num_layers: int = 2, output_dim: int = 1):
        super().__init__()

        self.lstm = nn.LSTM(
            input_size=input_dim, hidden_size=hidden_dim, num_layers=num_layers, batch_first=True, dropout=0.2
        )

        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2), nn.ReLU(), nn.Dropout(0.2), nn.Linear(hidden_dim // 2, output_dim)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # LSTM需要序列输入，这里将单个样本视为长度为1的序列
        if x.dim() == 2:
            x = x.unsqueeze(1)

        lstm_out, _ = self.lstm(x)
        output = self.fc(lstm_out[:, -1, :])
        return output


class BaselineTransformer(nn.Module):
    """基线Transformer模型"""

    def __init__(self, input_dim: int = 7, d_model: int = 64, nhead: int = 4, num_layers: int = 2, output_dim: int = 1):
        super().__init__()

        self.input_proj = nn.Linear(input_dim, d_model)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=d_model * 2, dropout=0.2, batch_first=True
        )

        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)

        self.output_proj = nn.Sequential(
            nn.Linear(d_model, d_model // 2), nn.ReLU(), nn.Dropout(0.2), nn.Linear(d_model // 2, output_dim)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == 2:
            x = x.unsqueeze(1)

        x = self.input_proj(x)
        x = self.transformer(x)
        output = self.output_proj(x[:, -1, :])
        return output


class BaselinePINN(nn.Module):
    """基线PINN模型"""

    def __init__(self, input_dim: int = 7, hidden_dim: int = 64, num_layers: int = 4, output_dim: int = 1):
        super().__init__()

        layers = []
        in_dim = input_dim

        for _ in range(num_layers):
            layers.append(nn.Linear(in_dim, hidden_dim))
            layers.append(nn.Tanh())
            in_dim = hidden_dim

        layers.append(nn.Linear(in_dim, output_dim))

        self.network = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)


class BaselineBPNN(nn.Module):
    """基线BPNN模型"""

    def __init__(self, input_dim: int = 7, hidden_dim: int = 64, num_layers: int = 3, output_dim: int = 1):
        super().__init__()

        layers = []
        in_dim = input_dim

        for _ in range(num_layers):
            layers.append(nn.Linear(in_dim, hidden_dim))
            layers.append(nn.ReLU())
            layers.append(nn.Dropout(0.2))
            in_dim = hidden_dim

        layers.append(nn.Linear(in_dim, output_dim))

        self.network = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)


class BaselineCNN(nn.Module):
    """基线CNN模型"""

    def __init__(self, input_dim: int = 7, hidden_dim: int = 64, output_dim: int = 1):
        super().__init__()

        # 1D卷积层 - 移除MaxPool1d以适配短序列
        self.conv_layers = nn.Sequential(
            nn.Conv1d(input_dim, hidden_dim, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(hidden_dim, hidden_dim * 2, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )

        # 全连接层
        self.fc_layers = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim), nn.ReLU(), nn.Dropout(0.2), nn.Linear(hidden_dim, output_dim)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # CNN需要序列输入 [batch, channels, length]
        if x.dim() == 2:
            x = x.unsqueeze(2)  # [batch, features, 1]

        conv_out = self.conv_layers(x)
        conv_out = conv_out.squeeze(2)  # [batch, features]
        output = self.fc_layers(conv_out)
        return output


class BaselineGRU(nn.Module):
    """基线GRU模型"""

    def __init__(self, input_dim: int = 7, hidden_dim: int = 64, num_layers: int = 2, output_dim: int = 1):
        super().__init__()

        self.gru = nn.GRU(
            input_size=input_dim, hidden_size=hidden_dim, num_layers=num_layers, batch_first=True, dropout=0.2
        )

        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2), nn.ReLU(), nn.Dropout(0.2), nn.Linear(hidden_dim // 2, output_dim)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # GRU需要序列输入
        if x.dim() == 2:
            x = x.unsqueeze(1)

        gru_out, _ = self.gru(x)
        output = self.fc(gru_out[:, -1, :])
        return output


class BaselinegPINN(nn.Module):
    """基线gPINN模型（梯度增强PINN）"""

    def __init__(self, input_dim: int = 7, hidden_dim: int = 64, num_layers: int = 4, output_dim: int = 1):
        super().__init__()

        layers = []
        in_dim = input_dim

        for _ in range(num_layers):
            layers.append(nn.Linear(in_dim, hidden_dim))
            layers.append(nn.Tanh())
            in_dim = hidden_dim

        layers.append(nn.Linear(in_dim, output_dim))

        self.network = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)

    def gradient_loss(self, x: torch.Tensor, y_pred: torch.Tensor) -> torch.Tensor:
        """计算梯度损失（gPINN特有）"""
        # 计算一阶导数
        grad_outputs = torch.ones_like(y_pred)
        gradients = torch.autograd.grad(outputs=y_pred, inputs=x, grad_outputs=grad_outputs, create_graph=True)[0]

        # 梯度正则化损失
        grad_loss = torch.mean(gradients**2)
        return grad_loss


class BaselinePeRCNN(nn.Module):
    """基线PeRCNN模型（物理编码循环卷积网络）"""

    def __init__(self, input_dim: int = 7, hidden_dim: int = 64, output_dim: int = 1):
        super().__init__()

        # 物理编码层
        self.physics_encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_dim), nn.Tanh(), nn.Linear(hidden_dim, hidden_dim)
        )

        # 循环卷积层
        self.conv_rnn = nn.Sequential(
            nn.Conv1d(hidden_dim, hidden_dim * 2, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(hidden_dim * 2, hidden_dim * 2, kernel_size=3, padding=1),
            nn.ReLU(),
        )

        # 输出层
        self.output_layer = nn.Sequential(
            nn.AdaptiveAvgPool1d(1),
            nn.Flatten(),
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # 物理编码
        physics_features = self.physics_encoder(x)

        # 转换为序列格式 [batch, channels, length]
        if physics_features.dim() == 2:
            physics_features = physics_features.unsqueeze(2)

        # 循环卷积
        conv_out = self.conv_rnn(physics_features)

        # 输出
        output = self.output_layer(conv_out)
        return output


class BaselineCNNLSTM(nn.Module):
    """基线CNN+LSTM混合模型。

    设计动机：CNN+LSTM 是时序传感器信号建模的经典混合基线，
    CNN 负责局部特征提取（短时窗内的力/振动模式），
    LSTM 负责捕捉长程时序依赖。该架构广泛用于机械信号分类/
    回归任务，是评估 LTC 相对于"传统深度时序模型"优势的标准对照。

    架构：
        Input [B, D]
          -> reshape [B, 1, D]  (视为长度=D 的单通道序列)
          -> Conv1d(1, hidden) -> ReLU -> Conv1d(hidden, hidden) -> ReLU
          -> reshape [B, hidden, 1] -> permute [B, 1, hidden]
          -> LSTM(hidden, hidden, num_layers)
          -> 取最后时步输出
          -> FC -> output

    说明：因实验框架中样本本身是 [B, D] 的特征向量（非原始波形），
    此处将特征向量视为长度为 D 的伪时序，先用 1D 卷积提取局部
    交互特征，再交给 LSTM 处理。这一设计与现有 BaselineLSTM/
    BaselineCNN 保持一致的处理范式。
    """

    def __init__(
        self,
        input_dim: int = 7,
        hidden_dim: int = 64,
        num_layers: int = 2,
        output_dim: int = 1,
        dropout: float = 0.2,
        kernel_size: int = 3,
    ):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim

        # 1D 卷积分支：提取局部交互特征
        # padding=1 保证序列长度不变（kernel_size=3 时）
        self.conv_layers = nn.Sequential(
            nn.Conv1d(1, hidden_dim, kernel_size=kernel_size, padding=1),
            nn.ReLU(),
            nn.Conv1d(hidden_dim, hidden_dim, kernel_size=kernel_size, padding=1),
            nn.ReLU(),
        )

        # LSTM 分支：捕捉长程时序依赖
        self.lstm = nn.LSTM(
            input_size=hidden_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )

        # 输出层
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, output_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [B, D] -> [B, 1, D] (单通道序列)
        if x.dim() == 2:
            x = x.unsqueeze(1)  # [B, 1, D]

        # 1D 卷积：[B, 1, D] -> [B, hidden, D]
        conv_out = self.conv_layers(x)

        # 转换为 LSTM 输入格式：[B, D, hidden] -> [B, seq_len=D, features=hidden]
        lstm_in = conv_out.permute(0, 2, 1)  # [B, D, hidden]

        # LSTM: 输出 [B, D, hidden], h_n [num_layers, B, hidden]
        lstm_out, _ = self.lstm(lstm_in)

        # 取最后时步输出
        last_out = lstm_out[:, -1, :]  # [B, hidden]

        # 全连接输出
        output = self.fc(last_out)
        return output


class SklearnBaselineWrapper(nn.Module):
    """sklearn/xgboost 基线模型的统一包装器。

    将传统 ML 模型（SVR/RF/XGBoost/GP）包装为 nn.Module 接口，
    使其可与现有实验框架的评估流程兼容。

    设计说明（AR-04）：
        - sklearn 模型不接受梯度训练，需通过 fit(X, y) 训练；
        - forward(tensor) 内部转 numpy 预测再转回 tensor，仅供评估使用；
        - 继承 nn.Module 仅为接口兼容，不包含可训练参数；
        - 实际训练由 SklearnBaselineTrainer 调用 fit() 完成。
    """

    def __init__(self, sklearn_model, input_dim: int = 7):
        super().__init__()
        self.input_dim = input_dim
        # sklearn 模型作为普通属性（非 nn.Parameter），不受 .to() 影响
        self.sklearn_model = sklearn_model
        self._is_fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "SklearnBaselineWrapper":
        """训练 sklearn 模型。

        Args:
            X: 输入特征 [N, input_dim]
            y: 目标值 [N] 或 [N, 1]

        Returns:
            self（链式调用）
        """
        self.sklearn_model.fit(X, np.asarray(y).ravel())
        self._is_fitted = True
        return self

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播：tensor -> numpy -> predict -> tensor。

        Args:
            x: 输入张量 [batch_size, input_dim]

        Returns:
            预测张量 [batch_size, 1]
        """
        if not self._is_fitted:
            raise RuntimeError(f"{type(self.sklearn_model).__name__} 尚未训练，请先调用 fit()")
        x_numpy = x.detach().cpu().numpy()
        y_pred = self.sklearn_model.predict(x_numpy)
        return torch.from_numpy(np.asarray(y_pred).reshape(-1, 1)).float().to(x.device)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """numpy 接口预测（供 SklearnBaselineTrainer 使用）。"""
        if not self._is_fitted:
            raise RuntimeError(f"{type(self.sklearn_model).__name__} 尚未训练，请先调用 fit()")
        return np.asarray(self.sklearn_model.predict(X)).reshape(-1, 1)


class BaselineSVR(SklearnBaselineWrapper):
    """支持向量回归基线（RBF 核）。

    论文第4节声明的传统 ML 基线之一，用于评估 LTC 相对于
    核方法回归的优越性。
    """

    def __init__(self, input_dim: int = 7, C: float = 1.0, epsilon: float = 0.1, **kwargs):
        from sklearn.svm import SVR

        super().__init__(
            SVR(kernel="rbf", C=C, epsilon=epsilon, **kwargs),
            input_dim=input_dim,
        )


class BaselineRF(SklearnBaselineWrapper):
    """随机森林回归基线。

    论文第4节声明的传统 ML 基线之一，用于评估 LTC 相对于
    集成树方法的优越性。
    """

    def __init__(
        self, input_dim: int = 7, n_estimators: int = 100, max_depth: int = 10, random_state: int = 42, **kwargs
    ):
        from sklearn.ensemble import RandomForestRegressor

        super().__init__(
            RandomForestRegressor(
                n_estimators=n_estimators,
                max_depth=max_depth,
                random_state=random_state,
                **kwargs,
            ),
            input_dim=input_dim,
        )


class BaselineXGBoost(SklearnBaselineWrapper):
    """XGBoost 梯度提升树基线。

    论文第4节声明的传统 ML 基线之一，用于评估 LTC 相对于
    梯度提升树方法的优越性。
    """

    def __init__(
        self,
        input_dim: int = 7,
        n_estimators: int = 100,
        max_depth: int = 6,
        learning_rate: float = 0.1,
        random_state: int = 42,
        **kwargs,
    ):
        import xgboost as xgb

        super().__init__(
            xgb.XGBRegressor(
                n_estimators=n_estimators,
                max_depth=max_depth,
                learning_rate=learning_rate,
                random_state=random_state,
                **kwargs,
            ),
            input_dim=input_dim,
        )


class BaselineGP(SklearnBaselineWrapper):
    """高斯过程回归基线（RBF 核）。

    论文第4节声明的传统 ML 基线之一，用于评估 LTC 相对于
    贝叶斯非参数方法的优越性。

    重要设计说明（GP 发散修复）：
        - 默认 ``optimizer=None`` 禁用 sklearn 内部 L-BFGS 核参数重优化，
          否则 sklearn 会在 ``fit()`` 时将 ``length_scale`` 压回下界 1e-5，
          导致严重过拟合（MAE≈20，R²≈-2000）。
        - Optuna 超参搜索得到的最佳核参数通过构造函数注入后将被保留。
        - 这一修改使 GP 基线公平地使用搜索到的超参，与论文实验方法一致。
    """

    def __init__(
        self,
        input_dim: int = 7,
        alpha: float = 1e-6,
        length_scale: float = 1.0,
        constant_value: float = 1.0,
        random_state: int = 42,
        **kwargs,
    ):
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import RBF, ConstantKernel

        kernel = ConstantKernel(constant_value) * RBF(length_scale=length_scale)
        # optimizer=None: 禁用 sklearn 内部 L-BFGS 核参数重优化，
        # 保留 Optuna 搜索得到的最佳核参数（否则 length_scale 会被压回 1e-5 导致发散）
        super().__init__(
            GaussianProcessRegressor(kernel=kernel, alpha=alpha, random_state=random_state, optimizer=None, **kwargs),
            input_dim=input_dim,
        )


def create_model(model_name: str, config) -> nn.Module:
    """
    创建模型

    Args:
        model_name: 模型名称
        config: 配置

    Returns:
        模型实例
    """
    input_dim = config.model.input_dim
    hidden_dim = config.model.hidden_dim
    num_layers = config.model.num_layers
    output_dim = config.model.output_dim

    if model_name in ("CT-LTC", "DL-LNN"):
        return DLLNNWithPhysics(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            output_dim=output_dim,
            dt=config.model.ltc_dt,
            dropout=config.model.dropout,
        )
    elif model_name == "LTC":
        return DLLNNModel(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            output_dim=output_dim,
            dt=config.model.ltc_dt,
            dropout=config.model.dropout,
        )
    elif model_name == "LSTM":
        return BaselineLSTM(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim)
    elif model_name == "Transformer":
        return BaselineTransformer(
            input_dim=input_dim, d_model=hidden_dim, num_layers=num_layers, output_dim=output_dim
        )
    elif model_name == "PINN":
        return BaselinePINN(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim)
    elif model_name == "BPNN":
        return BaselineBPNN(input_dim=input_dim, hidden_dim=hidden_dim, num_layers=num_layers, output_dim=output_dim)
    elif model_name == "CNN-LSTM":
        return BaselineCNNLSTM(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            output_dim=output_dim,
            dropout=config.model.dropout,
        )
    elif model_name == "SVR":
        # AR-04: 论文第4节声明的传统 ML 基线（支持向量回归，RBF 核）
        return BaselineSVR(input_dim=input_dim)
    elif model_name == "RF":
        # AR-04: 论文第4节声明的传统 ML 基线（随机森林回归）
        return BaselineRF(input_dim=input_dim)
    elif model_name == "XGBoost":
        # AR-04: 论文第4节声明的传统 ML 基线（XGBoost 梯度提升树）
        return BaselineXGBoost(input_dim=input_dim)
    elif model_name == "GP":
        # AR-04: 论文第4节声明的传统 ML 基线（高斯过程回归，RBF 核）
        # 若 config 上挂载了 Optuna 搜索得到的最佳超参，则注入到 BaselineGP；
        # BaselineGP 内部已设 optimizer=None 防止 sklearn 重优化覆盖搜索结果。
        gp_params = getattr(config, "gp_best_params", None) or {}
        return BaselineGP(
            input_dim=input_dim,
            length_scale=gp_params.get("length_scale", 1.0),
            constant_value=gp_params.get("constant_value", 1.0),
            alpha=gp_params.get("alpha", 1e-6),
        )
    else:
        raise ValueError(f"未知模型: {model_name}")


if __name__ == "__main__":
    # 测试模型
    print("测试DL-LNN模型...")

    config = type(
        "Config",
        (),
        {
            "model": type(
                "ModelConfig",
                (),
                {
                    "input_dim": 7,  # AR-06: 与 config.py 默认值及论文第3节声明的 7 维物理参数特征一致
                    "hidden_dim": 64,
                    "num_layers": 3,
                    "output_dim": 1,
                    "ltc_dt": 0.1,
                    "dropout": 0.2,
                },
            )()
        },
    )()

    model = create_model("DL-LNN", config)

    # 测试前向传播
    x = torch.randn(32, 7)  # AR-06: 与 input_dim 一致
    output, ltc_output = model(x)

    print(f"输入形状: {x.shape}")
    print(f"输出形状: {output.shape}")
    print(f"LTC输出形状: {ltc_output.shape}")

    print("\n模型测试通过！")
