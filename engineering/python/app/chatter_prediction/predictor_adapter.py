"""颤振预测双路径适配器：整合 Tlusty 解析法 + LTC 神经网络（实验性）。

设计原则
========
工程优先策略（项目记忆硬约束：工程生产优先于学术价值）：
- 默认走 Tlusty 解析法路径（stability.py 已实现，工程可用）
- LTC 神经网络路径标记为「实验性」，仅在 chatter_model.pt 存在时尝试
- 不使用合成数据训练 LTC 模型（合成数据在真实车间无意义）
- chatter_model.pt 不存在或推理失败时自动回退到 Tlusty 解析法

预测路径：
    路径 A（默认）: Tlusty 解析法
        - 调用 app.simulation.chatter.stability.compute_stability_limit
        - 工程可用，物理意义明确（基于机床 FRF + 切削力系数 K_s）
        - 限制：单自由度假设，复杂模态需扩展
    路径 B（实验性）: LTC 神经网络
        - 调用 app.simulation.chatter.predictor.ChatterPredictor
        - 仅在 chatter_model.pt 存在时启用
        - 限制：模型未训练（当前不存在），推理失败自动回退到路径 A
    路径 C（兜底）: 解析法与神经网络均失败时
        - 返回保守默认值（limit_depth=1.0mm, stable=True, confidence=0.3）
        - 标记 method=fallback，强制工程师审核

HRC52 置信度降低策略（项目记忆硬约束）：
- HRC52 材料强制标注 pending_calibration
- pending_calibration 时 confidence 强制降低到 0.5（默认 0.8）
- 工程师审核时需重点关注低置信度结果

K_s 传递策略（项目记忆硬约束）：
- K_s（cutting_force_coeff）直接取自阶段 4 ChatterParams
- 不进行二次拟合（避免引入额外误差）
- 仅作为追溯字段记录，不参与预测算法

随机种子与可复现性（项目记忆硬约束）：
- LTC 神经网络推理前调用 set_global_seed（如可用）
- MC dropout 路径需锁保护（如启用）
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, cast

from app.chatter_prediction.chatter_store import (
    FeatureChatterResult,
    PredictionMethod,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ChatterPredictorAdapter",
    "PredictorAdapterError",
    "check_ltc_model_available",
]


# 异常类


class PredictorAdapterError(Exception):
    """预测适配器异常。"""


# HRC52 与 pending_calibration 配置


# HRC52 材料 ID 列表（项目记忆硬约束：HRC52 不可使用纯文献数据）
# 这些材料 ID 触发 pending_calibration 标注 + 置信度降低
PENDING_CALIBRATION_MATERIALS: frozenset[str] = frozenset(
    {
        "steel_hrc52",
        "hrc52",
        "hrc_52",
        "hardened_steel_hrc52",
    }
)

# 默认置信度
DEFAULT_CONFIDENCE = 0.8
# pending_calibration 时强制置信度
PENDING_CALIBRATION_CONFIDENCE = 0.5
# 兜底路径置信度
FALLBACK_CONFIDENCE = 0.3

# 安全裕度建议（极限切深的 80%）
SAFETY_MARGIN_RATIO = 0.8


# LTC 模型可用性检查


def check_ltc_model_available() -> bool:
    """检查颤振预测模型是否存在（阶段2 解耦改造后支持 ONNX）。

    工程优先策略：
    - 仅检查文件存在性，不加载模型（避免启动时开销）
    - 模型路径与 predictor.py 保持一致：
        python/app/simulation/chatter/checkpoints/
    - 阶段2 解耦后优先检查 ONNX（model.onnx），向后兼容检查旧 torch 检查点（chatter_model.pt）
    - 文件不存在时返回 False，预测适配器自动走 Tlusty 解析法路径
    """
    try:
        # 与 predictor.py 中 _model_dir 保持一致
        model_dir = os.path.join(
            os.path.dirname(__file__),
            "..",
            "simulation",
            "chatter",
            "checkpoints",
        )
        model_dir = os.path.normpath(model_dir)
        # 阶段2 解耦：优先 ONNX，向后兼容 torch 检查点
        onnx_path = os.path.join(model_dir, "model.onnx")
        pt_path = os.path.join(model_dir, "chatter_model.pt")
        return os.path.exists(onnx_path) or os.path.exists(pt_path)
    except (OSError, ValueError) as e:
        logger.warning("检查 LTC 模型可用性失败: %s", e)
        return False


# 预测适配器


@dataclass
class _AdaptedPrediction:
    """单条特征预测的内部结果（不含审核字段）。"""

    limit_depth_mm: float
    stable: bool
    method: str  # analytical / neural_network / fallback
    ltc_active: bool
    confidence: float
    inference_time_ms: float
    warnings: list[str]


class ChatterPredictorAdapter:
    """颤振预测双路径适配器。

    使用方式：
        adapter = ChatterPredictorAdapter()
        result = adapter.predict_feature(
            feature_id="feat_plane_001",
            feature_type="plane",
            material_id="al_6061",
            chatter_params_dict={...},  # 阶段 4 to_chatter_params_dict 输出
            source_cutting_params_task_id="cp_xxx",
        )
    """

    def __init__(self, force_analytical: bool = False) -> None:
        """初始化适配器。

        Args:
            force_analytical: 强制走解析法路径（忽略 LTC 模型）
                              测试时使用，生产环境应保持 False
        """
        self._force_analytical = force_analytical
        self._ltc_available = check_ltc_model_available() if not force_analytical else False
        if self._ltc_available:
            logger.info("LTC 模型可用，将尝试神经网络路径（实验性）")
        else:
            logger.info("LTC 模型不可用，全部走 Tlusty 解析法路径（工程默认）")

    @property
    def ltc_model_available(self) -> bool:
        """LTC 模型是否可用。"""
        return self._ltc_available

    def predict_feature(
        self,
        feature_id: str,
        feature_type: str,
        material_id: str,
        chatter_params_dict: dict[str, Any],
        source_cutting_params_task_id: str = "",
    ) -> FeatureChatterResult:
        """对单个特征执行双路径预测。

        Args:
            feature_id: 特征 ID
            feature_type: 特征类型 (plane/cylinder/hole/boss)
            material_id: 材料 ID
            chatter_params_dict: 阶段 4 to_chatter_params_dict 输出，结构：
                {
                    "spindle_rpm": float,
                    "machine": {machine_id, stiffness_*, damping_ratio, natural_freq, modal_mass},
                    "tool": {tool_id, diameter, num_flutes, helix_angle, cutting_force_coeff},
                    "axial_depth": float,
                }
            source_cutting_params_task_id: 阶段 4 任务 ID（追溯用）

        Returns:
            FeatureChatterResult
        """
        start_time = time.time()
        warnings: list[str] = []

        # 提取参数（K_s 直接传递，不二次拟合 —— 项目记忆硬约束）
        spindle_rpm = float(chatter_params_dict.get("spindle_rpm", 8000.0))
        axial_depth = float(chatter_params_dict.get("axial_depth", 0.0) or 0.0)
        machine_dict = chatter_params_dict.get("machine", {})
        tool_dict = chatter_params_dict.get("tool", {})
        machine_id = str(machine_dict.get("machine_id", "vmc_850"))
        tool_id = str(tool_dict.get("tool_id", "endmill_d10"))
        cutting_force_coeff = float(tool_dict.get("cutting_force_coeff", 2000.0))

        # HRC52 标定状态注入
        material_calibration_status = self._resolve_calibration_status(material_id)
        if material_calibration_status == "pending_calibration":
            warnings.append(f"材料 {material_id} 标注 pending_calibration，K_s 为工程估算值，置信度已强制降低")

        # 双路径预测
        prediction = self._predict_dual_path(
            chatter_params_dict=chatter_params_dict,
            material_id=material_id,
            warnings=warnings,
        )

        inference_time_ms = (time.time() - start_time) * 1000.0

        # 稳定性裕度（actual / limit）
        if prediction.limit_depth_mm > 0:
            stability_margin = axial_depth / prediction.limit_depth_mm
        else:
            stability_margin = float("inf")

        # 置信度调整（pending_calibration 强制降低）
        confidence = prediction.confidence
        if material_calibration_status == "pending_calibration":
            confidence = min(confidence, PENDING_CALIBRATION_CONFIDENCE)

        # 安全裕度警告（实际切深超过极限切深的 80%）
        if prediction.limit_depth_mm > 0 and axial_depth > SAFETY_MARGIN_RATIO * prediction.limit_depth_mm:
            warnings.append(
                f"实际切深 {axial_depth:.2f}mm 超过极限切深 {prediction.limit_depth_mm:.2f}mm "
                f"的 {SAFETY_MARGIN_RATIO * 100:.0f}%，建议降低切深或主轴转速"
            )

        return FeatureChatterResult(
            feature_id=feature_id,
            feature_type=feature_type,
            material_id=material_id,
            spindle_rpm=spindle_rpm,
            axial_depth_mm=axial_depth,
            limit_depth_mm=prediction.limit_depth_mm,
            stable=prediction.stable,
            stability_margin=stability_margin if stability_margin != float("inf") else -1.0,
            method=prediction.method,
            ltc_active=prediction.ltc_active,
            confidence=confidence,
            inference_time_ms=inference_time_ms,
            # 合并所有警告：本地累积的（pending_calibration + 安全裕度）+ 预测路径内部的
            warnings=warnings + prediction.warnings,
            material_calibration_status=material_calibration_status,
            source_cutting_params_task_id=source_cutting_params_task_id,
            machine_id=machine_id,
            tool_id=tool_id,
            cutting_force_coeff=cutting_force_coeff,
        )

    # 内部：双路径预测

    def _predict_dual_path(
        self,
        chatter_params_dict: dict[str, Any],
        material_id: str,
        warnings: list[str],
    ) -> _AdaptedPrediction:
        """双路径预测：LTC（实验性）→ Tlusty 解析法 → 兜底。"""
        # 路径 B: LTC 神经网络（实验性，仅当模型可用时尝试）
        if self._ltc_available and not self._force_analytical:
            try:
                nn_result = self._predict_via_ltc(chatter_params_dict)
                if nn_result.limit_depth_mm > 0:
                    nn_result.warnings.append("LTC 神经网络路径为实验性，工程师需重点审核")
                    return nn_result
                else:
                    warnings.append("LTC 推理返回无效值，回退到 Tlusty 解析法")
            except (RuntimeError, ValueError, OSError, ImportError) as e:
                warnings.append(f"LTC 推理失败: {e}，回退到 Tlusty 解析法")
                logger.warning("LTC 推理失败 material=%s: %s", material_id, e)

        # 路径 A: Tlusty 解析法（默认工程路径）
        try:
            analytical_result = self._predict_via_analytical(chatter_params_dict)
            return analytical_result
        except (ValueError, RuntimeError, KeyError, ZeroDivisionError) as e:
            warnings.append(f"Tlusty 解析法失败: {e}，使用兜底默认值")
            logger.error("Tlusty 解析法失败 material=%s: %s", material_id, e)

        # 路径 C: 兜底
        return _AdaptedPrediction(
            limit_depth_mm=1.0,  # 保守默认 1mm
            stable=True,
            method=PredictionMethod.FALLBACK.value,
            ltc_active=False,
            confidence=FALLBACK_CONFIDENCE,
            inference_time_ms=0.0,
            warnings=warnings + ["解析法与神经网络均失败，使用兜底默认值，结果不可信"],
        )

    def _predict_via_ltc(self, chatter_params_dict: dict[str, Any]) -> _AdaptedPrediction:
        """通过 LTC 神经网络预测（实验性路径）。

        调用 app.simulation.chatter.predictor.predict_stability。
        """
        from app.simulation.chatter.predictor import predict_stability  # 延迟导入

        spindle_rpm = float(chatter_params_dict.get("spindle_rpm", 8000.0))
        machine_dict = chatter_params_dict.get("machine", {})
        tool_dict = chatter_params_dict.get("tool", {})
        machine_id = str(machine_dict.get("machine_id", "vmc_850"))
        tool_id = str(tool_dict.get("tool_id", "endmill_d10"))

        # predict_stability 内部已实现 LTC 解析法回退逻辑
        # 当 ltc_active=True 时返回 neural_network，否则返回 analytical
        result = predict_stability(
            spindle_rpm=spindle_rpm,
            machine=machine_id,
            tool=tool_id,
        )

        ltc_active = bool(result.get("ltc_active", False))
        # result 是 dict[str, object]（外部 predict_stability 返回），显式 cast 解包
        limit_depth = float(cast(Any, result.get("limit_depth", 0.0)))
        stable = bool(result.get("stable", True))
        inference_time = float(cast(Any, result.get("inference_time_ms", 0.0)))

        # 若 predict_stability 内部已回退到解析法，标记为 analytical
        if not ltc_active:
            return _AdaptedPrediction(
                limit_depth_mm=limit_depth,
                stable=stable,
                method=PredictionMethod.ANALYTICAL.value,
                ltc_active=False,
                confidence=DEFAULT_CONFIDENCE,
                inference_time_ms=inference_time,
                warnings=["LTC 模型不可用，predict_stability 内部已回退到解析法"],
            )

        return _AdaptedPrediction(
            limit_depth_mm=limit_depth,
            stable=stable,
            method=PredictionMethod.NEURAL_NETWORK.value,
            ltc_active=True,
            confidence=DEFAULT_CONFIDENCE,
            inference_time_ms=inference_time,
            warnings=[],
        )

    def _predict_via_analytical(self, chatter_params_dict: dict[str, Any]) -> _AdaptedPrediction:
        """通过 Tlusty 解析法预测（默认工程路径）。

        直接调用 stability.compute_stability_limit，绕过 predictor.predict_stability
        以避免不必要的 LTC 加载尝试。
        """
        from app.simulation.chatter.stability import (
            ChatterParams,
            MachineParams,
            ToolParams,
            compute_stability_limit,
        )

        # 构造 ChatterParams（K_s 直接传递，不二次拟合）
        machine_dict = chatter_params_dict.get("machine", {})
        tool_dict = chatter_params_dict.get("tool", {})

        machine_params = MachineParams(
            machine_id=str(machine_dict.get("machine_id", "vmc_850")),
            stiffness_x=float(machine_dict.get("stiffness_x", 1.5e7)),
            stiffness_y=float(machine_dict.get("stiffness_y", 1.5e7)),
            stiffness_z=float(machine_dict.get("stiffness_z", 2.0e8)),
            damping_ratio=float(machine_dict.get("damping_ratio", 0.05)),
            natural_freq=float(machine_dict.get("natural_freq", 100.0)),
            modal_mass=float(machine_dict.get("modal_mass", 50.0)),
        )

        tool_params = ToolParams(
            tool_id=str(tool_dict.get("tool_id", "endmill_d10")),
            diameter=float(tool_dict.get("diameter", 10.0)),
            num_flutes=int(tool_dict.get("num_flutes", 4)),
            helix_angle=float(tool_dict.get("helix_angle", 30.0)),
            cutting_force_coeff=float(tool_dict.get("cutting_force_coeff", 2000.0)),  # K_s 直接传递
        )

        chatter_params = ChatterParams(
            spindle_rpm=float(chatter_params_dict.get("spindle_rpm", 8000.0)),
            machine=machine_params,
            tool=tool_params,
        )

        limit_depth = compute_stability_limit(chatter_params)

        # 稳定性判断：实际切深 < 极限切深 * 安全裕度
        axial_depth = float(chatter_params_dict.get("axial_depth", 0.0) or 0.0)
        stable = axial_depth < SAFETY_MARGIN_RATIO * limit_depth if limit_depth > 0 else True

        return _AdaptedPrediction(
            limit_depth_mm=limit_depth,
            stable=stable,
            method=PredictionMethod.ANALYTICAL.value,
            ltc_active=False,
            confidence=DEFAULT_CONFIDENCE,
            inference_time_ms=0.0,  # 解析法极快，不单独计时
            warnings=[],
        )

    # 内部：材料标定状态解析

    def _resolve_calibration_status(self, material_id: str) -> str:
        """解析材料标定状态。

        HRC52 材料（PENDING_CALIBRATION_MATERIALS 中的 ID）返回 pending_calibration，
        其余返回 calibrated。

        与阶段 4 material_resolver.calibration_status 保持一致语义，
        但本模块独立判断（不依赖阶段 4 模块加载），便于阶段 5 独立运行。
        """
        material_id_lower = material_id.lower()
        if material_id_lower in PENDING_CALIBRATION_MATERIALS:
            return "pending_calibration"
        return "calibrated"
