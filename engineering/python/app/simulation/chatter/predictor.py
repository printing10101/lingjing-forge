"""颤振稳定性神经网络预测模块。

提供基于神经网络的颤振稳定性快速预测能力，用于实时加工参数优化。

模型架构：
    使用 LTC（Liquid Time-Constant）网络预测稳定性极限切削深度。
    LTC 通过可学习时间常数 tau 实现自适应时间动态：
        h_new = h + dt * (tanh(W @ x + U @ h + b) - h) / tau

    输入特征：
        - 主轴转速 (rpm)
        - 机床刚度参数 (归一化)
        - 刀具参数 (归一化)

    输出：
        - 稳定性状态 (0=稳定, 1=不稳定)
        - 极限切削深度 (mm)

    网络结构：
        LTCCell(6, 64) -> LTCCell(64, 32) -> Linear(32, 2)
"""

from __future__ import annotations

import os
import logging
import time
import numpy as np


logger = logging.getLogger(__name__)

# 全局模型缓存
_model_cache: dict[str, object] = {}
_model_dir = os.path.join(os.path.dirname(__file__), "checkpoints")


class ChatterPredictor:
    """颤振稳定性神经网络预测器。"""

    def __init__(self):
        """初始化预测器。"""
        self.model = None
        self.scaler = None
        self._load_model()

    def _load_model(self):
        """加载训练好的 LTC 颤振预测模型。"""
        try:
            import torch

            # P0#3 解耦: 通过 research_bridge 延迟导入
            from app.ai.lnn._research_bridge import get_ltc_model_factory, get_lnn_config_factory

            LTCModel = get_ltc_model_factory()
            LNNConfig = get_lnn_config_factory()
            if LTCModel is None or LNNConfig is None:
                raise ImportError("Research package not available")

            # 构建 LTC 模型配置（与论文 DL-LNN 架构一致）
            config = LNNConfig(
                input_size=6,  # 6 个颤振特征
                hidden_size=64,  # 隐藏层维度
                output_size=2,  # 稳定性状态 + 极限切深
                num_layers=2,  # 两层 LTC 堆叠
                dropout=0.0,  # 推理阶段关闭 dropout
                time_constant=1.0,  # 默认时间常数
            )

            # 尝试加载模型检查点
            checkpoint_path = os.path.join(_model_dir, "chatter_model.pt")

            if os.path.exists(checkpoint_path):
                self.model = LTCModel(config)
                # 安全修复 [P1-BE-1]：weights_only=True 防止 pickle 反序列化任意代码执行
                # PyTorch >= 2.0 推荐方式；checkpoint 仅含 state_dict 张量，无需自定义类
                checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
                # 兼容两种 checkpoint 格式：直接 state_dict 或包装在键下
                if "model_state_dict" in checkpoint:
                    self.model.load_state_dict(checkpoint["model_state_dict"])
                else:
                    self.model.load_state_dict(checkpoint)
                self.model.eval()
                logger.info("已加载 LTC 颤振预测模型: %s", checkpoint_path)
            else:
                logger.warning("未找到模型检查点: %s，使用解析法回退", checkpoint_path)
                self.model = None

        except ImportError:
            logger.warning("PyTorch 未安装，使用解析法回退")
            self.model = None
        except (OSError, RuntimeError, ValueError, KeyError) as e:
            logger.error("加载 LTC 模型失败: %s", e)
            self.model = None

    def _normalize_inputs(
        self,
        spindle_rpm: float,
        machine_stiffness: float,
        machine_damping: float,
        machine_freq: float,
        tool_diameter: float,
        tool_k_s: float,
    ) -> np.ndarray:
        """归一化输入特征。

        Args:
            spindle_rpm: 主轴转速 (rpm)
            machine_stiffness: 机床刚度 (N/m)
            machine_damping: 阻尼比
            machine_freq: 固有频率 (Hz)
            tool_diameter: 刀具直径 (mm)
            tool_k_s: 切削力系数 (N/mm²)

        Returns:
            归一化后的特征向量
        """
        # 归一化范围（基于典型值）
        return np.array(
            [
                spindle_rpm / 10000.0,  # 转速归一化到 [0, 1]
                machine_stiffness / 1e7,  # 刚度归一化
                machine_damping / 0.1,  # 阻尼比归一化
                machine_freq / 1000.0,  # 频率归一化
                tool_diameter / 20.0,  # 直径归一化
                tool_k_s / 2500.0,  # 切削力系数归一化
            ],
            dtype=np.float32,
        )

    def predict(
        self,
        spindle_rpm: float,
        machine_stiffness: float,
        machine_damping: float,
        machine_freq: float,
        tool_diameter: float,
        tool_k_s: float,
    ) -> tuple[bool, float]:
        """预测稳定性状态和极限切削深度。

        Args:
            spindle_rpm: 主轴转速 (rpm)
            machine_stiffness: 机床刚度 (N/m)
            machine_damping: 阻尼比
            machine_freq: 固有频率 (Hz)
            tool_diameter: 刀具直径 (mm)
            tool_k_s: 切削力系数 (N/mm²)

        Returns:
            (stable, limit_depth): 稳定性状态和极限切削深度
        """
        if self.model is None:
            # 模型不可用：返回负值标记，由上层调用解析法 compute_stability_limit
            # 不再返回固定 5.0，避免误导调用方（如 NX Adaptive Milling 的稳定性约束）
            logger.debug("LTC 模型不可用，调用方应回退到 compute_stability_limit 解析法")
            return True, -1.0

        try:
            import torch

            # 归一化输入
            x_norm = self._normalize_inputs(
                spindle_rpm,
                machine_stiffness,
                machine_damping,
                machine_freq,
                tool_diameter,
                tool_k_s,
            )

            # 转换为张量（batch_size=1, input_size=6）
            x_tensor = torch.tensor(x_norm, dtype=torch.float32).unsqueeze(0)

            # LTC 推理：forward 返回 (output, hidden_state)
            with torch.no_grad():
                output, _hidden = self.model(x_tensor)
                output = output.squeeze(0).numpy()

            # 解析输出
            stability_logit = output[0]
            limit_depth = output[1]

            # 稳定性判断（sigmoid > 0.5 为稳定）
            stable = 1 / (1 + np.exp(-stability_logit)) > 0.5

            # 极限切深（取绝对值，确保为正）
            limit_depth = abs(float(limit_depth))

            return bool(stable), limit_depth

        except (RuntimeError, ValueError, TypeError) as e:
            logger.error("LTC 推理失败: %s，调用方应回退到解析法", e)
            return True, -1.0


def predict_stability(
    spindle_rpm: float = 8000,
    machine: str = "vmc_850",
    tool: str = "endmill_d10",
    workpiece: str = "aluminum",
) -> dict[str, object]:
    """预测颤振稳定性。

    主接口函数，整合解析法和神经网络预测。

    Args:
        spindle_rpm: 主轴转速 (rpm)
        machine: 机床标识 (如 'vmc_850')
        tool: 刀具标识 (如 'endmill_d10')
        workpiece: 工件材料 (如 'aluminum')

    Returns:
        包含以下键的字典：
        - stable: 稳定性状态 (bool)
        - limit_depth: 极限切削深度 (mm)
        - method: 预测方法 ('neural_network' 或 'analytical')
    """
    from app.simulation.chatter.stability import (
        get_machine_params,
        ToolParams,
        ChatterParams,
        compute_stability_limit,
    )
    from app.simulation.chatter.stability import DEFAULT_TOOL_PARAMS

    # 获取机床参数
    machine_params = get_machine_params(machine)

    # 获取刀具参数
    if tool in DEFAULT_TOOL_PARAMS:
        tool_params = ToolParams(tool_id=tool, **DEFAULT_TOOL_PARAMS[tool])
    else:
        tool_params = ToolParams(tool_id=tool)

    # 尝试神经网络预测
    start_time = time.time()
    ltc_active = False

    try:
        predictor = ChatterPredictor()

        if predictor.model is not None:
            stable, limit_depth = predictor.predict(
                spindle_rpm=spindle_rpm,
                machine_stiffness=machine_params.stiffness_z,
                machine_damping=machine_params.damping_ratio,
                machine_freq=machine_params.natural_freq,
                tool_diameter=tool_params.diameter,
                tool_k_s=tool_params.cutting_force_coeff,
            )

            # limit_depth < 0 表示 LTC 不可用或推理失败，需回退到解析法
            if limit_depth > 0:
                inference_time = (time.time() - start_time) * 1000  # ms
                ltc_active = True
                logger.info(f"神经网络推理完成，耗时: {inference_time:.2f} ms")

                return {
                    "stable": stable,
                    "limit_depth": limit_depth,
                    "method": "neural_network",
                    "ltc_active": ltc_active,
                    "inference_time_ms": inference_time,
                }
            else:
                logger.info("LTC 模型不可用，回退到解析法 compute_stability_limit")
    except (RuntimeError, ValueError, OSError) as e:
        logger.warning("神经网络预测失败: %s，回退到解析法", e)

    # 回退到解析法
    chatter_params = ChatterParams(
        spindle_rpm=spindle_rpm,
        machine=machine_params,
        tool=tool_params,
    )

    limit_depth = compute_stability_limit(chatter_params)

    # 稳定性判断：当前切深（假设为 2mm）是否小于极限切深
    assumed_depth = 2.0
    stable = assumed_depth < limit_depth

    return {
        "stable": stable,
        "limit_depth": limit_depth,
        "method": "analytical",
        "ltc_active": False,
        "assumed_depth_for_stability": assumed_depth,
    }


def predict_stability_batch(
    params_list: list,
) -> list:
    """批量预测颤振稳定性。

    Args:
        params_list: 参数列表，每个元素为字典，包含：
            - spindle_rpm: 主轴转速
            - machine: 机床标识
            - tool: 刀具标识
            - workpiece: 工件材料

    Returns:
        预测结果列表
    """
    return [predict_stability(**params) for params in params_list]
