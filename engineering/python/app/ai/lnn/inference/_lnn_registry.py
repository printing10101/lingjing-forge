"""LNNModelRegistry 预定义模型注册表（从 registry 拆出）。"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

try:
    from app.ai.lnn.models.base_lnn import BaseLNNModel
    from app.ai.lnn.models.cfc_model import CFCModel
    from app.ai.lnn.models.ltc_model import LTCModel
    from app.ai.lnn.models.hybrid_lnn import HybridLNNModel

    _HAS_TORCH_MODELS = True
except ImportError:
    BaseLNNModel = None
    CFCModel = None
    LTCModel = None
    HybridLNNModel = None
    _HAS_TORCH_MODELS = False

from app.ai.lnn.inference._base_registry import BaseModelRegistry
from app.ai.lnn.inference._registry_models import ModelEntry, ModelInfo


class LNNModelRegistry(BaseModelRegistry):
    """
    LNN Model Registry with predefined models and validation support.

    Features:
    - Predefined models: cutting_force, wear_prediction, surface_roughness, temperature
    - Model registration with duplicate checking
    - Exact and fuzzy model lookup
    - Model file existence and structure validation
    """

    PREDEFINED_MODELS = {
        "cutting_force": ModelInfo(
            name="cutting_force",
            model_type="CFC",
            # 权重路径统一 .npz（v2 格式，与 BaseLNNModel.save/load 解析器一致）。
            # 历史 .pt 声明是口径分裂：文件从未存在，np.load 也不识别 torch zip。
            model_path="models/lnn/cutting_force.npz",
            # 2026-09 契约修正（breaking）：原输入含 force_x/y/z 属「用力预测力」，
            # 无预测价值；改为「加工参数 → 切削力」，与合成数据集
            # （synthetic_machining_params_v1）的生成网格对齐。
            # material_encoded：材料类别索引（编码表见特征清单 feature_manifest.json）
            input_features=["spindle_rpm", "feed_rate", "depth_of_cut", "material_encoded"],
            output_features=["predicted_cutting_force"],
            version="2.0.0",
        ),
        "wear_prediction": ModelInfo(
            name="wear_prediction",
            # 学术诚信说明 [S6]：model_type="LTC" 指向 LTCModel 类
            # （MODEL_CLASS_MAP["LTC"] = LTCModel）。LTCModel 同时提供：
            # - NumPy 前向推理（forward / predict）：功能性实现，可独立运行
            # - PyTorch 训练（_train_step / _train_step_torch）：真实梯度更新
            # - NumPy 训练（_train_step_numpy）：非功能性占位（详见 S2 修复）
            # 当权重文件不存在时，模型以 NumPy 随机权重初始化，
            # 仍可执行前向推理（用于演示/接口验证），运行时会以
            # weights_source="random_init" 显式标记，不得对外宣称为 AI 能力。
            # 对外报告训练结果时必须确认权重文件由训练管线生成（v2 npz，
            # is_trained=True，manifest 可溯源）。
            model_type="LTC",
            model_path="models/lnn/wear_prediction.npz",
            # 2026-09 契约修正（breaking，v2）：原输入以 vb（后刀面磨损带宽度）
            # 预测磨损属循环输入（用目标预测目标），且 spindle/feed/depth 在
            # 仓库唯一实测磨损数据集 uniwear（NUAA，39.9k 行）中不存在。
            # 改为传感器信号 → 磨损：力/振动/时间，与 uniwear 列一一对应。
            input_features=["force_z", "vibration_x", "vibration_y", "time_s"],
            output_features=["predicted_wear"],
            version="2.0.0",
        ),
        "surface_roughness": ModelInfo(
            name="surface_roughness",
            model_type="HybridLNN",
            model_path="models/lnn/surface_roughness.npz",
            input_features=["roughness_ra", "cutting_speed", "feed_rate", "tool_wear"],
            output_features=["predicted_surface_roughness"],
            version="1.0.0",
        ),
        "temperature": ModelInfo(
            name="temperature",
            model_type="CFC",
            model_path="models/lnn/temperature.npz",
            input_features=["temp_zone1", "temp_zone2", "coolant_flow", "cutting_time"],
            output_features=["predicted_temperature"],
            version="1.0.0",
        ),
    }

    MODEL_CLASS_MAP: dict[str, type[BaseLNNModel]] = {
        "CFC": CFCModel,
        "LTC": LTCModel,
        "HybridLNN": HybridLNNModel,
    }

    def __init__(self, cache_size: int = 10, model_dir: str | None = None):
        self.cache_size = cache_size
        self.model_dir = model_dir
        self.registry: dict[str, ModelEntry] = {}
        self.cache_hits = 0
        self.cache_misses = 0
        self._lock = threading.Lock()  # 保护 registry 字典的线程安全
        self._register_predefined_models()

    def _register_predefined_models(self) -> None:
        """Register all predefined models"""
        for name, template in self.PREDEFINED_MODELS.items():
            # 写时拷贝：PREDEFINED_MODELS 是类级共享模板，直接改写 template.model_path
            # 会把本实例的 model_dir 泄漏到所有其他实例（跨实例路径污染）
            info = ModelInfo.from_dict(template.to_dict())
            if self.model_dir:
                info.model_path = os.path.join(self.model_dir, os.path.basename(info.model_path))
            entry = ModelEntry(info=info)
            self.registry[name] = entry

    def get_model_info(
        self,
        model_name: str,
        fuzzy_match: bool = False,
    ) -> ModelInfo | None:
        with self._lock:
            if not fuzzy_match:
                entry = self.registry.get(model_name)
                return entry.info if entry else None

            matches = [name for name in self.registry.keys() if model_name.lower() in name.lower()]
            if matches:
                return self.registry[matches[0]].info
            return None

    def list_models(self, return_objects: bool = False) -> list[Any]:
        with self._lock:
            if return_objects:
                return [entry.info for entry in self.registry.values()]
            return list(self.registry.keys())

    def register_model(self, model_info: ModelInfo) -> bool:
        with self._lock:
            if model_info.name in self.registry:
                return False
            entry = ModelEntry(info=model_info)
            self.registry[model_info.name] = entry
            return True

    def register_quantized_model(
        self,
        base_model_name: str,
        quantized_model_path: str,
        quantization_type: str = "dynamic",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        with self._lock:
            quantized_name = f"{base_model_name}_int8" if not base_model_name.endswith("_int8") else base_model_name

            if quantized_name in self.registry:
                return False

            base_entry = self.registry.get(base_model_name)
            if base_entry and base_entry.info:
                model_type = base_entry.info.model_type
                input_features = base_entry.info.input_features
                output_features = base_entry.info.output_features
            else:
                model_type = "CFC"
                input_features = []
                output_features = []

            quantized_info = ModelInfo(
                name=quantized_name,
                model_type=model_type,
                model_path=quantized_model_path,
                input_features=input_features,
                output_features=output_features,
                version="1.0.0-int8",
            )

            quant_meta = metadata or {}
            quant_meta.update(
                {
                    "is_quantized": True,
                    "quantization_type": quantization_type,
                    "quantization_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "base_model": base_model_name,
                }
            )

            entry = ModelEntry(info=quantized_info, metadata=quant_meta)
            self.registry[quantized_name] = entry
            return True

    def get(self, model_name: str) -> ModelEntry:
        """Get a model entry by name."""
        with self._lock:
            entry = self.registry.get(model_name)
            if entry is None:
                raise KeyError(f"Model '{model_name}' not found in registry")
            return entry

    def load_model(self, model_name: str) -> Any:
        """加载模型实例并挂到 entry（按权重扩展名分发 npz/onnx）。

        权重装载记账与 ModelRegistry._load_model 同口径：weights_source ∈
        {trained, file_untrained, random_init}。返回模型实例。

        Raises:
            KeyError: 模型未注册。
            ValueError: 未知模型类型。
            (np.load/OSError 等): 权重文件损坏时向上传播。
        """
        entry = self.get(model_name)
        info = entry.info
        assert info is not None
        model: Any
        weights_source: str

        if info.model_path.endswith(".onnx"):
            from app.ai.lnn.models.onnx_runtime_model import OnnxLNNModel

            model = OnnxLNNModel(
                model_name=info.name,
                input_dim=len(info.input_features),
                output_dim=len(info.output_features),
            )
            model.load(info.model_path)
            weights_source = "trained"
        else:
            model_class = self.MODEL_CLASS_MAP.get(info.model_type)
            if model_class is None:
                raise ValueError(
                    f"模型加载失败：未知的模型类型 '{info.model_type}'。"
                    f"支持类型：{sorted(k for k in self.MODEL_CLASS_MAP)}"
                )
            model = model_class(
                model_name=info.name,
                input_dim=len(info.input_features),
                output_dim=len(info.output_features),
            )
            import os as _os

            if info.model_path and _os.path.exists(info.model_path):
                model.load(info.model_path)
                weights_source = "trained" if getattr(model, "is_trained", False) else "file_untrained"
            else:
                model.build()
                weights_source = "random_init"

        with self._lock:
            entry.model = model
            entry.is_loaded = True
            entry.metadata = {**(entry.metadata or {}), "weights_source": weights_source}
        return model

    def validate_model(self, model_name: str, model_path: str | None = None) -> dict[str, Any]:
        with self._lock:
            entry = self.registry.get(model_name)
            if not entry:
                return {
                    "valid": False,
                    "reason": f"Model '{model_name}' not found in registry",
                    "details": {},
                }
            if entry.info is None:
                return {
                    "valid": False,
                    "reason": f"Model '{model_name}' 缺少元数据信息",
                    "details": {},
                }

            path = model_path or entry.info.model_path
            file_exists = os.path.exists(path)
            structure_valid = True
            load_test_passed = False
            weights_trained: bool | None = None

            if file_exists:
                try:
                    if path.endswith(".onnx"):
                        # ONNX 工件只能由训练管线产出，加载通过即视为已训练
                        from app.ai.lnn.models.onnx_runtime_model import OnnxLNNModel

                        model: Any = OnnxLNNModel(
                            model_name=entry.info.name,
                            input_dim=len(entry.info.input_features),
                            output_dim=len(entry.info.output_features),
                        )
                        model.load(path)
                        weights_trained = True
                    else:
                        model_class = self.MODEL_CLASS_MAP.get(entry.info.model_type)
                        if model_class:
                            model = model_class(
                                model_name=entry.info.name,
                                input_dim=len(entry.info.input_features),
                                output_dim=len(entry.info.output_features),
                            )
                            model.load(path)
                            # v2 npz 携带 is_trained（v1 仅元数据文件为 False）——
                            # 上层据此区分「已训练权重」与「随机/未训练文件」
                            weights_trained = bool(getattr(model, "is_trained", False))
                    model.build()
                    load_test_passed = True
                except (ImportError, AttributeError, RuntimeError, ValueError, TypeError, OSError):
                    # 模型加载测试可能因模块导入、属性访问、文件 IO 等环节失败，
                    # 此处无需详细错误信息（仅作有效性标记）
                    structure_valid = False
                    load_test_passed = False

            weights_source = "random_init"
            if load_test_passed:
                weights_source = "trained" if weights_trained else "file_untrained"
            return {
                "valid": file_exists and structure_valid and load_test_passed,
                "file_exists": file_exists,
                "structure_valid": structure_valid,
                "load_test_passed": load_test_passed,
                "weights_source": weights_source,
                "model_name": model_name,
                "model_path": path,
            }
