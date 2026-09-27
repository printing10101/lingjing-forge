"""切削力推理接口。

提供简洁的切削力预测 API，封装 PINN 模型推理逻辑。

用法:
    from app.simulation.cutting_force.predictor import predict_cutting_force

    result = predict_cutting_force(
        material='45steel',
        tool='endmill_d10',
        params={'speed': 3500, 'feed': 1200, 'depth': 1.5}
    )
    # result = {'Fx': ..., 'Fy': ..., 'Fz': ..., 'method': 'pinn'}
"""

from __future__ import annotations

import os
import logging
from typing import Any

# torch 软依赖：桌面 MVP 打包时排除 torch，此时仅 Kienzle 解析解可用
try:
    import torch

    _HAS_TORCH = True
except ImportError:
    torch = None
    _HAS_TORCH = False

from app.simulation.cutting_force.kienzle import (
    compute_cutting_forces,
)

# CuttingForcePINN 依赖 torch.nn，torch 不可用时置 None
try:
    from app.simulation.cutting_force.pinn import CuttingForcePINN
except ImportError:
    CuttingForcePINN = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# 全局模型缓存
_model_cache: dict[str, Any] = {}
_model_dir = os.path.join(os.path.dirname(__file__), "checkpoints")


def _load_model(device: str = "cpu") -> Any:
    """加载训练好的 PINN 模型。

    若检查点不存在，则创建默认模型（推理时使用 Kienzle 解析解回退）。
    torch 不可用时返回 None，调用方需检查。
    """
    if not _HAS_TORCH or CuttingForcePINN is None:
        return None

    cache_key = device
    if cache_key in _model_cache:
        return _model_cache[cache_key]

    model = CuttingForcePINN()
    checkpoint_path = os.path.join(_model_dir, "best_model.pt")

    if os.path.exists(checkpoint_path):
        # 安全修复 [P1-BE-2]：weights_only=True 防止 pickle 反序列化任意代码执行
        checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=True)
        model.load_state_dict(checkpoint["model_state_dict"])
        logger.info("已加载模型检查点: %s", checkpoint_path)
    else:
        logger.warning(f"未找到模型检查点: {checkpoint_path}。请先运行训练脚本，或使用 Kienzle 解析解。")

    model.to(device)
    model.eval()
    _model_cache[cache_key] = model
    return model


def predict_cutting_force(
    material: str = "45steel",
    tool: str = "endmill_d10",
    params: dict | None = None,
    use_pinn: bool = True,
) -> dict[str, Any]:
    """预测切削力。

    Args:
        material: 材料名称 (如 '45steel', 'aluminum_6061')
        tool: 刀具标识 (当前仅用于接口兼容，不影响计算)
        params: 切削参数字典，包含:
            - speed: 主轴转速 (rpm)
            - feed: 进给量 (mm/min)
            - depth: 切深 (mm)
        use_pinn: 是否使用 PINN 模型。False 时仅使用 Kienzle 解析解。

    Returns:
        包含以下键的字典:
        - Fx: 进给力 (N)
        - Fy: 径向力 (N)
        - Fz: 主切削力 (N)
        - method: 预测方法 ('pinn' 或 'kienzle')
        - confidence: 置信度 (0.0-1.0)，PINN 模型为 0.85，Kienzle 为 0.60
        - model_version: 模型版本标识
    """
    if params is None:
        params = {"speed": 3500, "feed": 1200, "depth": 1.5}

    speed = params.get("speed", 3500)
    feed = params.get("feed", 1200)
    depth = params.get("depth", 1.5)

    # Kienzle 解析解计算
    # 映射: 切屑厚度 h = depth * 0.1, 切削宽度 b = depth
    chip_thickness = max(depth * 0.1, 0.001)
    width = max(depth, 0.01)

    kienzle_result = compute_cutting_forces(
        material=material,
        width=width,
        chip_thickness=chip_thickness,
    )

    if not use_pinn:
        return {
            "Fx": kienzle_result["Fx"],
            "Fy": kienzle_result["Fy"],
            "Fz": kienzle_result["Fz"],
            "method": "kienzle",
            "confidence": 0.60,  # 解析解置信度较低
            "model_version": "kienzle_v1.0",
        }

    # PINN 推理（torch 不可用时直接回退到 Kienzle）
    if not _HAS_TORCH or CuttingForcePINN is None:
        logger.warning("torch 不可用，PINN 推理已跳过，使用 Kienzle 解析解")
        return {
            "Fx": kienzle_result["Fx"],
            "Fy": kienzle_result["Fy"],
            "Fz": kienzle_result["Fz"],
            "method": "kienzle_no_torch",
            "confidence": 0.50,
            "model_version": "kienzle_v1.0_no_torch",
            "warning": "torch 未安装，PINN 模型不可用，已使用解析解",
        }

    try:
        device = "cpu"
        model = _load_model(device)

        if model is None:
            return {
                "Fx": kienzle_result["Fx"],
                "Fy": kienzle_result["Fy"],
                "Fz": kienzle_result["Fz"],
                "method": "kienzle_fallback",
                "confidence": 0.50,
                "model_version": "kienzle_v1.0_fallback",
                "warning": "PINN 模型加载失败，已回退到解析解",
            }

        with torch.no_grad():
            x_norm = CuttingForcePINN.normalize_params(speed, feed, depth)
            # [N-H5] 设备同步防御：确保输入 tensor 与 model 在同一设备上，
            # 避免 future 将 device 改为 cuda 时触发 "Expected all tensors to be on the same device"
            try:
                model_device = next(model.parameters()).device
            except (StopIteration, AttributeError):
                # model 无参数（未训练）或非标准 nn.Module，回退到 cpu
                model_device = torch.device("cpu")
            x_norm = x_norm.to(model_device)
            pred = model(x_norm)
            forces = pred[0].detach().cpu().numpy()

        # 若模型未经训练（输出接近零），回退到 Kienzle
        if forces.sum() < 1.0:
            logger.warning("PINN 输出异常，回退到 Kienzle 解析解")
            return {
                "Fx": kienzle_result["Fx"],
                "Fy": kienzle_result["Fy"],
                "Fz": kienzle_result["Fz"],
                "method": "kienzle_fallback",  # 明确标注为回退
                "confidence": 0.50,  # 回退模式置信度更低
                "model_version": "kienzle_v1.0_fallback",
                "warning": "PINN 模型未训练或输出异常，已回退到解析解",
            }

        return {
            "Fx": float(forces[0]),
            "Fy": float(forces[1]),
            "Fz": float(forces[2]),
            "method": "pinn",
            "confidence": 0.85,  # PINN 模型置信度较高
            "model_version": "pinn_v1.0",
        }
    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("PINN 推理失败 (%s)，回退到 Kienzle 解析解", e)
        return {
            "Fx": kienzle_result["Fx"],
            "Fy": kienzle_result["Fy"],
            "Fz": kienzle_result["Fz"],
            "method": "kienzle_fallback",
            "confidence": 0.50,
            "model_version": "kienzle_v1.0_fallback",
            "warning": f"PINN 推理失败: {str(e)}，已回退到解析解",
        }


def predict_cutting_force_batch(
    material: str,
    params_list: list,
    use_pinn: bool = True,
) -> list:
    """批量预测切削力。

    Args:
        material: 材料名称
        params_list: 切削参数列表，每个元素为参数字典
        use_pinn: 是否使用 PINN

    Returns:
        预测结果列表
    """
    return [predict_cutting_force(material=material, params=p, use_pinn=use_pinn) for p in params_list]
