"""LNN 配置校验 mixin（从 config_manager 拆出）。"""

from __future__ import annotations

from typing import Any

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


class _ValidationMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    REQUIRED_LNN_KEYS: Any
    REQUIRED_MODEL_KEYS: Any
    REQUIRED_THRESHOLD_KEYS: Any
    VALID_ENVIRONMENTS: Any
    VALID_MODEL_TYPES: Any
    _raw_config: Any

    def validate(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        验证配置结构和参数合法性

        Args:
            config: 要验证的配置字典（可选，默认验证当前配置）

        Returns:
            验证结果字典，包含valid字段和详细的errors/warnings
        """
        target_config = config or self._raw_config
        errors = []
        warnings = []

        errors.extend(self._validate_lnn_section(target_config.get("lnn", {})))
        errors.extend(self._validate_workflow_section(target_config.get("workflow", {})))
        errors.extend(self._validate_environment_section(target_config.get("environment", {})))

        if "lnn" in target_config:
            warnings.extend(self._check_lnn_best_practices(target_config["lnn"]))

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    def _validate_config_value(self, section: str, key: str, value: Any) -> None:
        """验证单个配置值的合法性"""
        # 验证阈值范围
        if key == "quick" or key.endswith(".quick"):
            if not isinstance(value, (int, float)) or not (0.0 <= value <= 1.0):
                raise ValueError(f"阈值 'quick' 必须在 0.0-1.0 范围内，实际值: {value}")
        elif key == "hybrid" or key.endswith(".hybrid"):
            if not isinstance(value, (int, float)) or not (0.0 <= value <= 1.0):
                raise ValueError(f"阈值 'hybrid' 必须在 0.0-1.0 范围内，实际值: {value}")
        elif key == "complexity" or key.endswith(".complexity"):
            if not isinstance(value, int) or value < 1:
                raise ValueError(f"复杂度 'complexity' 必须是正整数，实际值: {value}")

        # 验证设备类型
        elif key == "default_device":
            valid_devices = ["cpu", "cuda", "mps"]
            if not isinstance(value, str) or value not in valid_devices:
                raise ValueError(f"设备类型必须是 {valid_devices} 之一，实际值: {value}")

        # 验证环境名称
        elif key == "name" and section == "environment":
            if not isinstance(value, str) or value not in self.VALID_ENVIRONMENTS:
                raise ValueError(f"环境名称必须是 {self.VALID_ENVIRONMENTS} 之一，实际值: {value}")

        # 验证模型类型
        elif key == "type" and "models" in section:
            if not isinstance(value, str) or value not in self.VALID_MODEL_TYPES:
                raise ValueError(f"模型类型必须是 {self.VALID_MODEL_TYPES} 之一，实际值: {value}")

    def _validate_config(self) -> None:
        """验证当前配置，失败时抛出异常"""
        result = self.validate()
        if not result["valid"]:
            error_msg = (
                "配置验证失败：以下配置项不符合要求:\n"
                + "\n".join(f"  - {e}" for e in result["errors"])
                + "\n\n请检查配置文件中的相关字段，或参考文档了解各配置项的合法取值范围。"
            )
            raise ValueError(error_msg)

    def _validate_lnn_section(self, lnn_config: dict[str, Any]) -> list[str]:
        """验证LNN配置节"""
        errors = []

        for key in self.REQUIRED_LNN_KEYS:
            if key not in lnn_config:
                errors.append(f"Missing required LNN key: {key}")

        if "default_device" in lnn_config:
            device = lnn_config["default_device"]
            valid_devices = ["cpu", "cuda", "mps", "auto"]
            if device not in valid_devices:
                errors.append(f"Invalid default_device: {device}. Must be one of {valid_devices}")

        if "models" in lnn_config:
            models = lnn_config["models"]
            if not isinstance(models, dict):
                errors.append("LNN models must be a dictionary")
            else:
                for model_name, model_config in models.items():
                    if not isinstance(model_config, dict):
                        errors.append(f"Model config for '{model_name}' must be a dictionary")
                        continue

                    for key in self.REQUIRED_MODEL_KEYS:
                        if key not in model_config:
                            errors.append(f"Missing required key '{key}' for model '{model_name}'")

                    if "type" in model_config:
                        model_type = model_config["type"].lower()
                        if model_type not in self.VALID_MODEL_TYPES:
                            errors.append(
                                f"Invalid model type '{model_type}' for model '{model_name}'. "
                                f"Must be one of {self.VALID_MODEL_TYPES}"
                            )

        if "thresholds" in lnn_config:
            thresholds = lnn_config["thresholds"]
            if not isinstance(thresholds, dict):
                errors.append("LNN thresholds must be a dictionary")
            else:
                for key in self.REQUIRED_THRESHOLD_KEYS:
                    if key not in thresholds:
                        errors.append(f"Missing required threshold key: {key}")

                if "quick" in thresholds:
                    val = thresholds["quick"]
                    if not isinstance(val, (int, float)) or not (0 <= val <= 1):
                        errors.append("Threshold 'quick' must be a float between 0 and 1")

                if "hybrid" in thresholds:
                    val = thresholds["hybrid"]
                    if not isinstance(val, (int, float)) or not (0 <= val <= 1):
                        errors.append("Threshold 'hybrid' must be a float between 0 and 1")

        return errors

    def _validate_workflow_section(self, workflow_config: dict[str, Any]) -> list[str]:
        """验证工作流配置节"""
        errors = []

        if "max_steps" in workflow_config:
            val = workflow_config["max_steps"]
            if not isinstance(val, int) or val < 1:
                errors.append("Workflow max_steps must be a positive integer")

        if "timeout_seconds" in workflow_config:
            val = workflow_config["timeout_seconds"]
            if not isinstance(val, int) or val < 1:
                errors.append("Workflow timeout_seconds must be a positive integer")

        return errors

    def _validate_environment_section(self, env_config: dict[str, Any]) -> list[str]:
        """验证环境配置节"""
        errors = []

        if "name" in env_config:
            name = env_config["name"]
            if name not in self.VALID_ENVIRONMENTS:
                errors.append(f"Invalid environment name: {name}. Must be one of {self.VALID_ENVIRONMENTS}")

        return errors

    def _check_lnn_best_practices(self, lnn_config: dict[str, Any]) -> list[str]:
        """检查LNN配置最佳实践"""
        warnings = []

        if lnn_config.get("default_device") == "cuda" and HAS_TORCH:
            if not torch.cuda.is_available():
                warnings.append("CUDA device specified but CUDA is not available")

        if lnn_config.get("enabled", True):
            models = lnn_config.get("models", {})
            if not models:
                warnings.append("LNN is enabled but no models are configured")

        return warnings
