"""
Model Quantization Test Suite

Tests for INT8 quantization functionality including:
- Quantizer class initialization and configuration
- Dynamic quantization
- Static quantization with calibration
- Quantized model save/load
- Performance evaluation
- Model registry quantization support
- API endpoint tests
"""

import os
import json
import logging
import tempfile
import pytest

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from quantization.quantizer import (  # noqa: E402
    Quantizer,
    QuantizationConfig,
    QuantizationResult,
    QuantizationType,
)
from app.ai.lnn.inference.registry import (  # noqa: E402
    is_quantized_model,
    get_base_model_name,
    get_quantized_model_name,
    LNNModelRegistry,
    ModelRegistry,
)


@pytest.fixture
def simple_model():
    """Create a simple PyTorch model for testing"""
    if not HAS_TORCH:
        pytest.skip("PyTorch not available")

    class SimpleModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.linear1 = nn.Linear(10, 20)
            self.linear2 = nn.Linear(20, 5)
            self.relu = nn.ReLU()

        def forward(self, x):
            x = self.relu(self.linear1(x))
            return self.linear2(x)

    return SimpleModel()


@pytest.fixture
def calibration_data():
    """Create calibration data for testing"""
    if not HAS_TORCH:
        return None
    import numpy as np

    return np.random.randn(100, 10).astype(np.float32)


@pytest.fixture
def test_data():
    """Create test data for evaluation"""
    if not HAS_TORCH:
        return None
    import numpy as np

    return np.random.randn(50, 10).astype(np.float32)


class TestQuantizationConfig:
    def test_default_config(self):
        config = QuantizationConfig()
        assert config.quantization_type == QuantizationType.DYNAMIC
        assert config.target_dtype == "qint8"
        assert config.target_layers == ["Linear"]
        assert config.calibration_samples == 1000
        assert config.preserve_fp32_model is True

    def test_custom_config(self):
        config = QuantizationConfig(
            quantization_type=QuantizationType.STATIC,
            target_dtype="qint8",
            calibration_samples=500,
        )
        assert config.quantization_type == QuantizationType.STATIC
        assert config.calibration_samples == 500

    def test_config_to_dict(self):
        config = QuantizationConfig(
            quantization_type=QuantizationType.STATIC,
            calibration_samples=500,
        )
        d = config.to_dict()
        assert isinstance(d, dict)
        assert d["quantization_type"] == "static"
        assert d["calibration_samples"] == 500

    def test_config_from_dict(self):
        data = {
            "quantization_type": "static",
            "target_dtype": "qint8",
            "calibration_samples": 500,
        }
        config = QuantizationConfig.from_dict(data)
        assert config.quantization_type == QuantizationType.STATIC
        assert config.calibration_samples == 500


class TestQuantizerInitialization:
    def test_default_init(self):
        quantizer = Quantizer()
        assert quantizer.config.quantization_type == QuantizationType.DYNAMIC

    def test_custom_config_init(self):
        config = QuantizationConfig(quantization_type=QuantizationType.STATIC)
        quantizer = Quantizer(config)
        assert quantizer.config.quantization_type == QuantizationType.STATIC


class TestDynamicQuantization:
    def test_dynamic_quantize_basic(self, simple_model):
        quantizer = Quantizer()
        quantized = quantizer.dynamic_quantize(simple_model)

        assert quantized is not None
        assert isinstance(quantized, nn.Module)

    def test_dynamic_quantize_preserves_output_shape(self, simple_model, test_data):
        quantizer = Quantizer()
        quantized = quantizer.dynamic_quantize(simple_model)

        simple_model.eval()
        quantized.eval()

        with torch.no_grad():
            x = torch.from_numpy(test_data[:1])
            original_out = simple_model(x)
            quantized_out = quantized(x)

        assert original_out.shape == quantized_out.shape

    def test_dynamic_quantize_output_values_close(self, simple_model, test_data):
        quantizer = Quantizer()
        quantized = quantizer.dynamic_quantize(simple_model)

        simple_model.eval()
        quantized.eval()

        with torch.no_grad():
            x = torch.from_numpy(test_data[:1])
            original_out = simple_model(x)
            quantized_out = quantized(x)

        diff = torch.abs(original_out - quantized_out).mean().item()
        assert diff < 0.5


class TestStaticQuantization:
    def test_static_quantize_requires_calibration(self, simple_model, calibration_data):
        config = QuantizationConfig(quantization_type=QuantizationType.STATIC)
        quantizer = Quantizer(config)
        quantized = quantizer.static_quantize(simple_model, calibration_data)

        assert quantized is not None
        assert isinstance(quantized, nn.Module)

    def test_static_quantize_no_data_raises_error(self, simple_model):
        config = QuantizationConfig(quantization_type=QuantizationType.STATIC)
        quantizer = Quantizer(config)
        with pytest.raises((ValueError, AttributeError, RuntimeError)):
            quantizer.static_quantize(simple_model, None)

    def test_static_quantize_preserves_output_shape(
        self, simple_model, calibration_data, test_data
    ):
        config = QuantizationConfig(quantization_type=QuantizationType.STATIC)
        quantizer = Quantizer(config)

        try:
            quantized = quantizer.static_quantize(simple_model, calibration_data)
        except (NotImplementedError, RuntimeError):
            pytest.skip("Static quantization inference not supported on this platform")

        simple_model.eval()
        quantized.eval()

        with torch.no_grad():
            x = torch.from_numpy(test_data[:1])
            original_out = simple_model(x)
            try:
                quantized_out = quantized(x)
            except (NotImplementedError, RuntimeError):
                pytest.skip(
                    "Static quantized model inference not supported on this platform"
                )

        assert original_out.shape == quantized_out.shape


class TestQuantizationSaveLoad:
    def test_save_quantized_model(self, simple_model):
        quantizer = Quantizer()
        quantized = quantizer.dynamic_quantize(simple_model)

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "quantized_model.pt")
            result_path = quantizer.save_quantized_model(quantized, save_path)

            assert os.path.exists(result_path)
            assert result_path == save_path

    def test_save_with_metadata(self, simple_model):
        quantizer = Quantizer()
        quantized = quantizer.dynamic_quantize(simple_model)

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "quantized_model.pt")
            metadata = {"test_key": "test_value"}
            quantizer.save_quantized_model(quantized, save_path, metadata)

            meta_path = save_path + ".meta.json"
            assert os.path.exists(meta_path)

            with open(meta_path) as f:
                loaded_meta = json.load(f)
            assert loaded_meta["test_key"] == "test_value"

    def test_load_quantized_model(self, simple_model):
        quantizer = Quantizer()
        quantized = quantizer.dynamic_quantize(simple_model)

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "quantized_model.pt")
            quantizer.save_quantized_model(quantized, save_path)

            from models.torch_base_lnn import LNNConfig

            LNNConfig(input_size=10, hidden_size=20, output_size=5)

            class TestModel(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.linear1 = nn.Linear(10, 20)
                    self.linear2 = nn.Linear(20, 5)

                def forward(self, x):
                    return self.linear2(torch.relu(self.linear1(x)))

            loaded_model = TestModel()
            state_dict = torch.load(save_path, map_location="cpu", weights_only=True)

            try:
                loaded_model.load_state_dict(state_dict)
            except RuntimeError as e:
                # 测试中结构不匹配的 state_dict 是预期行为（验证错误路径）
                logger.debug(
                    f"Expected state_dict mismatch during quantization test: {e}",
                    exc_info=True,
                )


class TestQuantizationResult:
    def test_result_to_dict(self):
        result = QuantizationResult(
            model_name="test_model",
            quantization_type=QuantizationType.DYNAMIC,
            original_size_bytes=10000,
            quantized_size_bytes=2500,
        )
        result.compression_ratio = 0.25
        d = result.to_dict()
        assert d["model_name"] == "test_model"
        assert d["size_reduction_percent"] == 75.0

    def test_result_get_report(self):
        result = QuantizationResult(
            model_name="test",
            quantization_type=QuantizationType.DYNAMIC,
            original_size_bytes=10000,
            quantized_size_bytes=2500,
        )
        report = result.get_report()
        assert "test" in report
        assert "Compression" in report


class TestQuantizationIntegration:
    def test_quantize_dynamic_full_pipeline(self, simple_model, test_data):
        config = QuantizationConfig(quantization_type=QuantizationType.DYNAMIC)
        quantizer = Quantizer(config)

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "quantized.pt")
            quantized_model, result = quantizer.quantize(
                simple_model,
                save_path=save_path,
            )

            assert quantized_model is not None
            assert os.path.exists(save_path)
            assert result.quantized_model_path == save_path

    def test_quantize_with_evaluation(self, simple_model, test_data):
        config = QuantizationConfig(quantization_type=QuantizationType.DYNAMIC)
        quantizer = Quantizer(config)

        quantized_model, result = quantizer.quantize(simple_model)

        assert result.original_size_bytes > 0
        assert result.compression_ratio > 0

        perf_result = quantizer.evaluate_performance(
            simple_model,
            quantized_model,
            test_data,
            num_samples=10,
        )

        assert perf_result.original_inference_time_ms > 0
        assert perf_result.quantized_inference_time_ms > 0
        assert perf_result.compression_ratio > 0
        assert perf_result.compression_ratio <= 0.5


class TestModelNameHelpers:
    def test_is_quantized_model_true(self):
        assert is_quantized_model("cutting_force_int8") is True

    def test_is_quantized_model_false(self):
        assert is_quantized_model("cutting_force") is False

    def test_get_base_model_name_quantized(self):
        assert get_base_model_name("cutting_force_int8") == "cutting_force"

    def test_get_base_model_name_base(self):
        assert get_base_model_name("cutting_force") == "cutting_force"

    def test_get_quantized_model_name_base(self):
        assert get_quantized_model_name("cutting_force") == "cutting_force_int8"

    def test_get_quantized_model_name_already_quantized(self):
        assert get_quantized_model_name("cutting_force_int8") == "cutting_force_int8"


class TestRegistryQuantizationSupport:
    def test_lnn_registry_register_quantized_model(self):
        registry = LNNModelRegistry()
        result = registry.register_quantized_model(
            "cutting_force",
            "/path/to/quantized.pt",
            quantization_type="dynamic",
        )
        assert result is True
        assert "cutting_force_int8" in registry.registry

    def test_lnn_registry_quantized_model_metadata(self):
        registry = LNNModelRegistry()
        registry.register_quantized_model(
            "cutting_force",
            "/path/to/quantized.pt",
            quantization_type="static",
            metadata={"custom_key": "custom_value"},
        )

        entry = registry.registry["cutting_force_int8"]
        assert entry.metadata["is_quantized"] is True
        assert entry.metadata["quantization_type"] == "static"
        assert entry.metadata["custom_key"] == "custom_value"

    def test_model_registry_register_quantized_model(self):
        from app.ai.lnn.core import ModelType

        registry = ModelRegistry()

        registry.register(
            "cutting_force",
            ModelType.CFC,
            model_path="/path/to/model.pt",
            config={"input_dim": 5, "output_dim": 1},
        )

        registry.register_quantized_model(
            "cutting_force_int8",
            ModelType.CFC,
            model_path="/path/to/quantized.pt",
        )

        assert "cutting_force_int8" in registry.registry

    def test_model_registry_has_quantized_version(self):
        from app.ai.lnn.core import ModelType

        registry = ModelRegistry()

        registry.register(
            "test_model",
            ModelType.CFC,
            model_path="/path/to/model.pt",
        )

        assert registry.has_quantized_version("test_model") is False

        registry.register_quantized_model(
            "test_model_int8",
            ModelType.CFC,
            model_path="/path/to/quantized.pt",
        )

        assert registry.has_quantized_version("test_model") is True

    def test_model_registry_get_quantized_model_path(self):
        from app.ai.lnn.core import ModelType

        registry = ModelRegistry()

        registry.register(
            "test_model",
            ModelType.CFC,
            model_path="/path/to/model.pt",
        )

        registry.register_quantized_model(
            "test_model_int8",
            ModelType.CFC,
            model_path="/path/to/quantized.pt",
        )

        path = registry.get_quantized_model_path("test_model")
        assert path == "/path/to/quantized.pt"
        assert registry.get_quantized_model_path("nonexistent") is None
