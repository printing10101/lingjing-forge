"""世界模型网络结构：LSTM + LTC 混合架构.

对应 ADR-017 第 1.2 节。世界模型负责预测加工过程的状态轨迹：

    s(t+1), s(t+2), ..., s(t+N) = WorldModelNet(s(t), a(t), a(t+1), ..., a(t+N-1))

其中 ``s`` 是状态向量（颤振概率/刀具磨损/表面质量等），
``a`` 是动作向量（切削参数：主轴转速/进给量/切深）。

架构选择理由
------------
1. **LSTM 编码器**：将历史状态 + 候选动作序列编码为隐状态 ``h``
2. **LTC 解码器**：基于隐状态自回归预测未来 N 步状态轨迹。
   LTC 的连续时间动力学特性（``dt`` 门控）适合建模加工过程的
   连续物理演化（颤振发生/刀具磨损累积）
3. **混合架构**：LSTM 提供"短期记忆"编码能力，LTC 提供"长期连续动力学"
   预测能力，两者互补

工程现实约束
------------
- torch 不可用时回退到 NumPy 朴素实现（精度降低但可运行）
- 所有可训练参数初始化使用固定种子（学术可复现性）
- 推理路径不修改模型状态（线程安全）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    import torch
    import torch.nn as nn

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    torch = None
    nn = None

logger = logging.getLogger(__name__)


@dataclass
class WorldModelConfig:
    """世界模型网络配置.

    Attributes
    ----------
    state_dim : int
        状态向量维度（颤振概率/磨损/质量等特征数）。
    action_dim : int
        动作向量维度（切削参数数：转速/进给/切深等）。
    hidden_dim : int
        LSTM 与 LTC 共用的隐状态维度。
    num_lstm_layers : int
        LSTM 编码器层数。
    num_ltct_layers : int
        LTC 解码器层数。
    dropout : float
        Dropout 比例（训练时启用，推理时关闭）。
    max_trajectory_length : int
        最大轨迹长度（防止自回归显存爆炸）。
    seed : int
        随机种子（保证初始化可复现）。
    use_fusion : bool
        是否启用 ADR-020 思路 1 的统一表示融合模式。**默认 True**（生产服务
        经 ``WORLD_MODEL_USE_FUSION`` 环境变量显式控制）。融合模式下 LSTM
        输入层接受融合 embedding（fused_dim + action_dim）；关闭则走原始
        state_dim 字段拼接路径（如性能基准的 StateField 8 维输入）。两种
        模式下 LTC 解码器自回归路径都用 state_dim + action_dim，state_head
        输出仍是 state_dim 维，保证 ADR-017 输出契约不变。
    feature_dim : int
        几何特征向量维度（ADR-007 平面/圆柱/孔统计向量，默认 32）。
        仅 use_fusion=True 时生效。
    d_model : int
        GeometryEncoder/DynamicsEncoder 输出维度（默认 64）。仅
        use_fusion=True 时生效。
    fused_dim : int
        FusionLayer 输出维度（默认 128）。仅 use_fusion=True 时生效。
    """

    state_dim: int = 8
    action_dim: int = 4
    hidden_dim: int = 64
    num_lstm_layers: int = 2
    num_ltc_layers: int = 2
    dropout: float = 0.1
    max_trajectory_length: int = 100
    seed: int = 42
    # ADR-020 思路 1：统一表示融合模式（P3 默认启用）
    # torch 不可用时由 predictor/plugin 层自动降级到传统路径
    use_fusion: bool = True
    feature_dim: int = 32
    d_model: int = 64
    fused_dim: int = 128

    def validate(self) -> None:
        """参数校验."""
        if self.state_dim <= 0:
            raise ValueError(f"state_dim 必须为正数: {self.state_dim}")
        if self.action_dim <= 0:
            raise ValueError(f"action_dim 必须为正数: {self.action_dim}")
        if self.hidden_dim <= 0:
            raise ValueError(f"hidden_dim 必须为正数: {self.hidden_dim}")
        if self.num_lstm_layers <= 0:
            raise ValueError(f"num_lstm_layers 必须为正数: {self.num_lstm_layers}")
        if self.num_ltc_layers <= 0:
            raise ValueError(f"num_ltc_layers 必须为正数: {self.num_ltc_layers}")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError(f"dropout 必须在 [0, 1): {self.dropout}")
        if self.max_trajectory_length <= 0:
            raise ValueError(f"max_trajectory_length 必须为正数: {self.max_trajectory_length}")
        if self.use_fusion:
            if self.feature_dim <= 0:
                raise ValueError(f"use_fusion=True 时 feature_dim 必须为正数: {self.feature_dim}")
            if self.d_model <= 0:
                raise ValueError(f"use_fusion=True 时 d_model 必须为正数: {self.d_model}")
            if self.fused_dim <= 0:
                raise ValueError(f"use_fusion=True 时 fused_dim 必须为正数: {self.fused_dim}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_dim": self.state_dim,
            "action_dim": self.action_dim,
            "hidden_dim": self.hidden_dim,
            "num_lstm_layers": self.num_lstm_layers,
            "num_ltc_layers": self.num_ltc_layers,
            "dropout": self.dropout,
            "max_trajectory_length": self.max_trajectory_length,
            "seed": self.seed,
            "use_fusion": self.use_fusion,
            "feature_dim": self.feature_dim,
            "d_model": self.d_model,
            "fused_dim": self.fused_dim,
        }


if HAS_TORCH:

    class _LTCCell(nn.Module):
        """简化版 LTC 单元：连续时间 RNN 近似.

        实现思路：用可学习的 ``dt`` 门控近似 LTC 的 ODE 求解，
        ``h(t+dt) = h(t) + dt * f(h(t), x(t))``。
        完整 LTC 求解器见 ``app/ai/lnn/core.py``，此处为轻量版本，
        适配世界模型的自回归预测需求。
        """

        def __init__(self, input_dim: int, hidden_dim: int) -> None:
            super().__init__()
            self.input_dim = input_dim
            self.hidden_dim = hidden_dim
            # 输入到隐状态的映射
            self.W_in = nn.Linear(input_dim, hidden_dim)
            # 隐状态自循环
            self.W_h = nn.Linear(hidden_dim, hidden_dim)
            # dt 门控（可学习的时间步长）
            self.dt_gate = nn.Linear(input_dim + hidden_dim, hidden_dim)
            # 输出门控
            self.out_gate = nn.Linear(hidden_dim, hidden_dim)
            self._init_weights()

        def _init_weights(self) -> None:
            for module in self.modules():
                if isinstance(module, nn.Linear):
                    nn.init.xavier_uniform_(module.weight)
                    if module.bias is not None:
                        nn.init.zeros_(module.bias)

        def forward(
            self,
            x: "torch.Tensor",
            h: "torch.Tensor" | None = None,
        ) -> "torch.Tensor":
            """前向传播.

            Args:
                x: 输入张量，shape ``[batch, input_dim]``。
                h: 上一时刻隐状态，shape ``[batch, hidden_dim]``。None 表示初始。

            Returns
            -------
            torch.Tensor
                下一时刻隐状态，shape ``[batch, hidden_dim]``。
            """
            batch_size = x.size(0)
            if h is None:
                h = x.new_zeros(batch_size, self.hidden_dim)
            # dt 门控：sigmoid 保证 ∈ (0, 1)，控制记忆更新速率
            dt = torch.sigmoid(self.dt_gate(torch.cat([x, h], dim=-1)))
            # 连续时间近似：h(t+dt) = h(t) + dt * tanh(W_in*x + W_h*h)
            candidate = torch.tanh(self.W_in(x) + self.W_h(h))
            new_h = h + dt * (candidate - h)
            # 输出门控
            return torch.tanh(self.out_gate(new_h))

    class WorldModelNet(nn.Module):
        """世界模型网络：LSTM 编码 + LTC 自回归解码.

        前向传播流程：
            1. （可选）融合层把几何/动力学 embedding 投影到统一空间
            2. LSTM 编码历史序列 → 上下文隐状态 ``h_ctx``
            3. LTC 解码器以 ``h_ctx`` 为初始状态，逐步预测未来状态轨迹
            4. 每步输入 = [上一步预测状态, 当前候选动作]

        融合模式（ADR-020 思路 1，``config.use_fusion=True``）：
            - LSTM 输入 = [fused_embedding, action]
            - 融合 embedding 由 GeometryEncoder + DynamicsEncoder +
              FusionLayer 跨模态生成
            - LTC 解码器自回归路径保持原 state_dim + action_dim，state_head
              输出仍是 state_dim 维，保证 ADR-017 输出契约不变

        Parameters
        ----------
        config : WorldModelConfig
            网络配置.
        """

        def __init__(self, config: WorldModelConfig) -> None:
            super().__init__()
            config.validate()
            self.config = config

            # 固定种子（学术可复现）
            torch.manual_seed(config.seed)

            # 融合模式：LSTM 输入 = fused_dim + action_dim
            # 非融合模式：LSTM 输入 = state_dim + action_dim（向后兼容）
            if config.use_fusion:
                from app.plugins.world_model.geometry_encoder import (
                    GeometryEncoder,
                )
                from app.plugins.world_model.dynamics_encoder import (
                    DynamicsEncoder,
                )
                from app.plugins.world_model.fusion_layer import FusionLayer

                encoder_input_dim = config.fused_dim + config.action_dim
                self.geometry_encoder = GeometryEncoder(feature_dim=config.feature_dim, d_model=config.d_model)
                self.dynamics_encoder = DynamicsEncoder(d_model=config.d_model)
                self.fusion_layer = FusionLayer(d_model=config.d_model, fused_dim=config.fused_dim)
            else:
                encoder_input_dim = config.state_dim + config.action_dim

            # LSTM 编码器
            self.encoder = nn.LSTM(
                input_size=encoder_input_dim,
                hidden_size=config.hidden_dim,
                num_layers=config.num_lstm_layers,
                batch_first=True,
                dropout=config.dropout if config.num_lstm_layers > 1 else 0.0,
            )

            # LTC 解码器：始终用 state_dim + action_dim（保持自回归路径兼容）
            self.decoder = _LTCCell(
                input_dim=config.state_dim + config.action_dim,
                hidden_dim=config.hidden_dim,
            )

            # 状态预测头：hidden state
            self.state_head = nn.Linear(config.hidden_dim, config.state_dim)

            # 轨迹指标预测头：hidden metrics（颤振峰值/最大磨损等）
            self.metrics_head = nn.Linear(config.hidden_dim, 3)

            self._init_weights()

        def _init_weights(self) -> None:
            for module in self.modules():
                if isinstance(module, nn.Linear) and module is not self.encoder:
                    nn.init.xavier_uniform_(module.weight)
                    if module.bias is not None:
                        nn.init.zeros_(module.bias)

        def _fuse_unified_states(
            self,
            unified_states: tuple["torch.Tensor", "torch.Tensor"],
        ) -> "torch.Tensor":
            """把 (geometry_tensor, dynamics_tensor) 投影到融合 embedding.

            Args:
                unified_states: (geometry_tensor [batch, T, 37],
                    dynamics_tensor [batch, T, 6])

            Returns
            -------
            torch.Tensor
                融合 embedding，shape ``[batch, T, fused_dim]``。
            """
            geometry_tensor, dynamics_tensor = unified_states
            # (batch, T, input_dim) (batch*T, input_dim) 以复用 MLP
            batch, T, _ = geometry_tensor.shape
            geo_emb = self.geometry_encoder(geometry_tensor.reshape(batch * T, -1))
            dyn_emb = self.dynamics_encoder(dynamics_tensor.reshape(batch * T, -1))
            fused = self.fusion_layer(geo_emb, dyn_emb)
            return fused.reshape(batch, T, -1)

        def forward(
            self,
            states: "torch.Tensor" | None,
            actions: "torch.Tensor",
            horizon: int,
            unified_states: tuple["torch.Tensor", "torch.Tensor"] | None = None,
        ) -> dict[str, "torch.Tensor"]:
            """前向传播：预测未来 ``horizon`` 步的状态轨迹.

            Args:
                states: 历史状态序列，shape ``[batch, T, state_dim]``。
                    非融合模式必填；融合模式下可传 None（由 unified_states
                    推导 T 与 batch）。
                actions: 候选动作序列，shape ``[batch, T + horizon, action_dim]``。
                    前 T 步对应历史，后 horizon 步对应未来要评估的动作。
                horizon: 预测步长.
                unified_states: 融合模式输入，元组
                    ``(geometry_tensor, dynamics_tensor)``，其中：
                    - geometry_tensor: shape ``[batch, T, 37]``（bbox 3 +
                      feature_vector 32 + symmetry 1 + complexity 1）
                    - dynamics_tensor: shape ``[batch, T, 6]``（主轴转速/
                      进给/切深/磨损/振动RMS/温度）
                    仅 ``config.use_fusion=True`` 时使用。

            Returns
            -------
            dict[str, torch.Tensor]
                - ``predicted_trajectory``: shape ``[batch, horizon, state_dim]``
                - ``trajectory_metrics``: shape ``[batch, 3]``（颤振峰值/最大磨损/平均质量）
                - ``final_hidden``: shape ``[batch, hidden_dim]``（用于 RL value 估计）
            """
            if horizon <= 0 or horizon > self.config.max_trajectory_length:
                raise ValueError(f"horizon 必须在 [1, {self.config.max_trajectory_length}], 当前: {horizon}")

            # 推断 batch / T 与构造 LSTM 输入
            # ADR-020 P3：按输入类型判定路径，而非仅依赖 config.use_fusion——
            # use_fusion=True 但收到 legacy states（无 unified_states）时
            # 降级到非融合分支，保证 use_fusion 开启后 legacy 调用不崩溃。
            if self.config.use_fusion and unified_states is not None:
                geometry_tensor, dynamics_tensor = unified_states
                T = geometry_tensor.size(1)
                fused = self._fuse_unified_states(unified_states)
                encoder_input = torch.cat([fused, actions[:, :T, :]], dim=-1)
            elif states is not None:
                T = states.size(1)
                encoder_input = torch.cat([states, actions[:, :T, :]], dim=-1)
            else:
                raise ValueError("必须提供 unified_states（融合模式）或 states（非融合模式）")

            # 输入校验
            if actions.size(1) != T + horizon:
                raise ValueError(f"actions 时间维度 ({actions.size(1)}) 必须等于 T + horizon ({T + horizon})")

            # 1. LSTM 编码历史
            _, (h_n, c_n) = self.encoder(encoder_input)
            # 取最后一层 LSTM 的隐状态作为解码器初始状态
            h = h_n[-1]  # [batch, hidden_dim]

            # 2. LTC 自回归解码
            predicted_states = []
            metrics_accumulator: list["torch.Tensor"] = []
            # 初始 prev_state：
            # - 融合模式：从 LSTM 上下文投影（state_head(h)），避免依赖
            # 未传入的 states，纯融合路径
            # - 非融合模式：取历史最后一个状态（向后兼容）
            if self.config.use_fusion:
                prev_state = self.state_head(h)  # [batch, state_dim]
            else:
                if states is None:
                    raise RuntimeError("非融合模式需要传入 states 历史状态")
                prev_state = states[:, -1, :]  # [batch, state_dim]

            for t in range(T, T + horizon):
                action_t = actions[:, t, :]  # [batch, action_dim]
                decoder_input = torch.cat([prev_state, action_t], dim=-1)
                h = self.decoder(decoder_input, h)
                # 预测下一步状态
                prev_state = self.state_head(h)
                predicted_states.append(prev_state)
                # 累积每步指标
                metrics_accumulator.append(self.metrics_head(h))

            # 3. 汇总输出
            trajectory = torch.stack(predicted_states, dim=1)  # [batch, horizon, state_dim]
            metrics_stack = torch.stack(metrics_accumulator, dim=1)  # [batch, horizon, 3]
            # 轨迹指标：颤振峰值 / 最大磨损 / 平均质量
            trajectory_metrics = (
                torch.stack(
                    [
                        trajectory[:, :, 0].max(dim=1).values,  # 颤振概率峰值
                        trajectory[:, :, 1].max(dim=1).values,  # 最大磨损
                        trajectory[:, :, 2].mean(dim=1),  # 平均质量（mean 返回 Tensor，无 .values）
                    ],
                    dim=-1,
                )
                if trajectory.size(-1) >= 3
                else metrics_stack.mean(dim=1)
            )

            return {
                "predicted_trajectory": trajectory,
                "trajectory_metrics": trajectory_metrics,
                "final_hidden": h,
            }

else:
    # torch 不可用时的 NumPy 回退实现（仅前向，无梯度）

    class WorldModelNet:  # type: ignore[no-redef]
        """NumPy 回退版世界模型（仅推理，无训练能力）."""

        def __init__(self, config: WorldModelConfig) -> None:
            config.validate()
            self.config = config
            if config.use_fusion:
                # ADR-020 P3 降级兜底：torch 不可用时不再 raise，
                # 改为降级到 NumPy 随机权重路径（融合 embedding 无法计算，
                # 但保证构造不崩溃，让上层 predict() 路由到原始路径）。
                # _predict_fused() 不会进入此实例（predictor 层已降级），
                # forward(unified_states=...) 调用时也只走 NumPy 随机轨迹。
                logger.warning(
                    "WorldModelNet NumPy 回退模式忽略 use_fusion=True："
                    "融合路径需要 torch 支持，当前环境未安装 torch。"
                    "已降级为 NumPy 随机权重路径，预测结果无实际意义。"
                    "请安装 torch 以启用融合模式。"
                )
            self._rng = np.random.default_rng(config.seed)
            # 用随机权重初始化（仅满足接口契约，精度无意义）
            self._W_enc = self._rng.standard_normal((config.state_dim + config.action_dim, config.hidden_dim)) * 0.1
            self._W_dec = self._rng.standard_normal((config.state_dim + config.action_dim, config.hidden_dim)) * 0.1
            self._W_state = self._rng.standard_normal((config.hidden_dim, config.state_dim)) * 0.1
            logger.warning(
                "WorldModelNet 运行在 NumPy 回退模式，仅用于接口验证，预测结果无实际意义。请安装 torch 以启用完整功能。"
            )

        def forward(
            self,
            states: np.ndarray,
            actions: np.ndarray,
            horizon: int,
            unified_states: tuple[np.ndarray, np.ndarray] | None = None,
        ) -> dict[str, np.ndarray]:
            """NumPy 前向传播（简化版）.

            Note
            ----
            融合模式（``use_fusion=True``）在 ``__init__`` 已降级为
            NumPy 随机权重路径（不再 raise）。此处 ``unified_states``
            参数仅为对齐 torch 版本签名，实际不会使用——上层
            ``TrajectoryPredictor`` 在 torch 不可用时会路由到原始
            ``_predict_numpy()`` 路径，不会传入 ``unified_states``。
            """
            if horizon <= 0 or horizon > self.config.max_trajectory_length:
                raise ValueError(f"horizon 必须在 [1, {self.config.max_trajectory_length}]")
            batch_size = states.shape[0]
            trajectory = self._rng.standard_normal((batch_size, horizon, self.config.state_dim)) * 0.01
            metrics = np.zeros((batch_size, 3))
            if trajectory.shape[-1] >= 3:
                metrics[:, 0] = trajectory[:, :, 0].max(axis=1)
                metrics[:, 1] = trajectory[:, :, 1].max(axis=1)
                metrics[:, 2] = trajectory[:, :, 2].mean(axis=1)
            return {
                "predicted_trajectory": trajectory,
                "trajectory_metrics": metrics,
                "final_hidden": np.zeros((batch_size, self.config.hidden_dim)),
            }
