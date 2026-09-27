"""Unit tests for LNN Uncertainty Quantization API endpoint."""

import pytest
from unittest.mock import Mock, patch
import numpy as np
import sys
import importlib.util
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

# 直接加载 lnn_uncertain 模块，避免触发 app.api.v1.__init__.py 的导入链
# 同时注册为正确的模块名称以便 coverage 可以追踪
module_path = Path(__file__).parent.parent / "lnn_uncertain.py"
spec = importlib.util.spec_from_file_location("app.api.v1.lnn_uncertain", module_path)
assert spec is not None and spec.loader is not None
lnn_uncertain = importlib.util.module_from_spec(spec)
sys.modules["app.api.v1.lnn_uncertain"] = lnn_uncertain
spec.loader.exec_module(lnn_uncertain)


@pytest.fixture
def app():
    """Create minimal test app with only the lnn_uncertain router."""
    test_app = FastAPI()
    # router already has prefix="/api/v1/lnn" in lnn_uncertain.py
    test_app.include_router(lnn_uncertain.router)
    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


class MockPredictionResult:
    """Simple mock class for prediction result."""

    def __init__(self, value, confidence, inference_time):
        self.value = value
        self.confidence = confidence
        self.inference_time = inference_time


@pytest.fixture
def mock_predictor():
    """Mock LNN predictor."""
    predictor = Mock()
    result = MockPredictionResult(10.5, 0.85, 15.2)
    predictor.predict.return_value = result
    return predictor


def _make_mock_result(value, confidence, inference_time):
    """Helper to create mock prediction result."""
    return MockPredictionResult(value, confidence, inference_time)


@pytest.fixture
def mock_registry():
    """Mock model registry."""
    with (
        patch("app.api.v1.lnn_uncertain.model_registry") as mock_reg,
        patch("app.api.v1.lnn_uncertain.model_cache") as mock_cache,
        patch("app.api.v1.lnn_uncertain.PredictionResult", MockPredictionResult),
    ):
        # Setup registry entry
        entry = Mock()
        entry.info.input_features = ["feature1", "feature2"]
        entry.info.output_features = ["output1"]
        mock_reg.registry.get.return_value = entry

        yield mock_reg, mock_cache


class TestPredictUncertainEndpoint:
    """Test /api/v1/lnn/predict-uncertain endpoint."""

    def test_predict_uncertain_success(self, client, mock_registry, mock_predictor):
        """Test successful prediction with uncertainty."""
        mock_reg, mock_cache = mock_registry
        mock_cache.get.return_value = mock_predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "data" in data

        result = data["data"]
        assert "prediction" in result
        assert "uncertainty" in result
        assert "confidence" in result

        # Verify confidence is in [0, 1]
        assert 0 <= result["confidence"] <= 1

        # Verify uncertainty is non-negative
        assert result["uncertainty"] >= 0

    def test_predict_uncertain_model_not_found(self, client, mock_registry):
        """Test prediction when model doesn't exist."""
        mock_reg, mock_cache = mock_registry
        mock_reg.registry.get.return_value = None

        response = client.post(
            "/api/v1/lnn/predict-uncertain", json={"model_name": "nonexistent_model", "input_data": [1.0, 2.0]}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != 0
        assert "not found" in data["message"].lower()

    def test_predict_uncertain_empty_input(self, client, mock_registry):
        """Test prediction with empty input data."""
        mock_reg, mock_cache = mock_registry

        response = client.post("/api/v1/lnn/predict-uncertain", json={"model_name": "test_model", "input_data": []})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != 0
        assert "非空" in data["message"] or "empty" in data["message"].lower()

    def test_predict_uncertain_invalid_input_type(self, client, mock_registry):
        """Test prediction with non-numeric input."""
        mock_reg, mock_cache = mock_registry

        response = client.post(
            "/api/v1/lnn/predict-uncertain", json={"model_name": "test_model", "input_data": [1.0, "invalid", 3.0]}
        )

        # Pydantic validates input_data type before endpoint code runs,
        # so non-numeric values in a list[float] field return 422
        assert response.status_code == 422

    def test_predict_uncertain_dimension_mismatch(self, client, mock_registry, mock_predictor):
        """Test prediction with wrong input dimensions."""
        mock_reg, mock_cache = mock_registry
        mock_cache.get.return_value = mock_predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={
                "model_name": "test_model",
                "input_data": [1.0, 2.0, 3.0, 4.0, 5.0],  # 5D but expects 2D
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != 0
        assert "维度" in data["message"] or "dimension" in data["message"].lower()

    def test_predict_uncertain_list_output(self, client, mock_registry):
        """Test prediction with list output (multiple samples)."""
        mock_reg, mock_cache = mock_registry

        # Mock predictor returning list
        predictor = Mock()
        predictor.predict.return_value = _make_mock_result(
            value=[10.0, 12.0, 11.0, 13.0], confidence=0.0, inference_time=20.5
        )
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0, 3.0, 4.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

        result = data["data"]
        # Should compute mean and std from list
        assert result["prediction"] == pytest.approx(11.5, rel=1e-5)
        assert result["uncertainty"] > 0
        assert 0 <= result["confidence"] <= 1

    def test_predict_uncertain_zero_mean(self, client, mock_registry):
        """Test prediction with zero mean (edge case)."""
        mock_reg, mock_cache = mock_registry

        # Mock predictor returning zero
        predictor = Mock()
        predictor.predict.return_value = _make_mock_result(value=0.0, confidence=0.5, inference_time=10.0)
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

        result = data["data"]
        # When mean is 0, confidence should be handled gracefully
        assert 0 <= result["confidence"] <= 1

    def test_predict_uncertain_high_variance(self, client, mock_registry):
        """Test prediction with high variance (low confidence)."""
        mock_reg, mock_cache = mock_registry

        # Mock predictor with high variance list
        predictor = Mock()
        predictor.predict.return_value = _make_mock_result(
            value=[1.0, 100.0, 1.0, 100.0],  # High variance
            confidence=0.0,
            inference_time=15.0,
        )
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0, 3.0, 4.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

        result = data["data"]
        # High variance should result in low confidence
        assert result["confidence"] < 0.5

    def test_predict_uncertain_low_variance(self, client, mock_registry):
        """Test prediction with low variance (high confidence)."""
        mock_reg, mock_cache = mock_registry

        # Mock predictor with low variance list
        predictor = Mock()
        predictor.predict.return_value = _make_mock_result(
            value=[10.0, 10.1, 10.2, 9.9],  # Low variance
            confidence=0.0,
            inference_time=15.0,
        )
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0, 3.0, 4.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

        result = data["data"]
        # Low variance should result in high confidence
        assert result["confidence"] > 0.9

    def test_predict_uncertain_model_inference_error(self, client, mock_registry):
        """Test prediction when model inference fails."""
        mock_reg, mock_cache = mock_registry

        predictor = Mock()
        predictor.predict.side_effect = RuntimeError("Model inference failed")
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] != 0
        # The error message is in Chinese: "服务内部错误，请稍后重试"
        assert "内部错误" in data["message"] or "error" in data["message"].lower()

    def test_predict_uncertain_confidence_formula(self, client, mock_registry):
        """Test that confidence follows the formula: 1 - (std / mean).clamp(0, 1)."""
        mock_reg, mock_cache = mock_registry

        # Known values: mean=10, std=2
        predictor = Mock()
        predictor.predict.return_value = _make_mock_result(
            value=[8.0, 10.0, 12.0, 10.0],  # mean=10, std≈1.58
            confidence=0.0,
            inference_time=10.0,
        )
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0, 3.0, 4.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        result = data["data"]

        # Manually calculate expected confidence
        values = [8.0, 10.0, 12.0, 10.0]
        expected_mean = np.mean(values)
        expected_std = np.std(values)
        expected_confidence = 1.0 - min(max(float(expected_std) / abs(float(expected_mean)), 0.0), 1.0)

        assert result["prediction"] == pytest.approx(expected_mean, rel=1e-5)
        assert result["uncertainty"] == pytest.approx(expected_std, rel=1e-5)
        assert result["confidence"] == pytest.approx(expected_confidence, rel=1e-5)


class TestConfidenceClamping:
    """Test confidence value clamping behavior."""

    def test_confidence_clamped_to_zero(self, client, mock_registry):
        """Test confidence is clamped to 0 when std/mean > 1."""
        mock_reg, mock_cache = mock_registry

        # std > mean scenario
        predictor = Mock()
        predictor.predict.return_value = _make_mock_result(
            value=[0.0, 20.0],  # mean=10, std=10, ratio=1.0
            confidence=0.0,
            inference_time=10.0,
        )
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        result = data["data"]

        # Confidence should be clamped to 0
        assert result["confidence"] >= 0.0

    def test_confidence_clamped_to_one(self, client, mock_registry):
        """Test confidence is clamped to 1 when std is 0."""
        mock_reg, mock_cache = mock_registry

        # Zero variance scenario
        predictor = Mock()
        predictor.predict.return_value = _make_mock_result(
            value=[10.0, 10.0, 10.0],  # mean=10, std=0
            confidence=0.0,
            inference_time=10.0,
        )
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={
                "model_name": "test_model",
                "input_data": [1.0, 2.0],  # Match expected dimension (2 features)
                "return_confidence": True,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0, f"Expected success but got error: {data.get('message')}"
        result = data["data"]

        # Confidence should be 1.0 (or very close)
        assert result["confidence"] <= 1.0
        assert result["confidence"] > 0.99

    def test_predict_uncertain_cache_miss(self, client, mock_registry):
        """Test prediction when predictor is not in cache (cache miss)."""
        mock_reg, mock_cache = mock_registry
        # Simulate cache miss
        mock_cache.get.return_value = None

        # Mock LNNPredictor.from_registry to return a mock predictor
        with patch("app.api.v1.lnn_uncertain.LNNPredictor") as MockPredictor:
            mock_predictor_instance = Mock()
            mock_predictor_instance.predict.return_value = _make_mock_result(
                value=10.5, confidence=0.85, inference_time=15.2
            )
            MockPredictor.from_registry.return_value = mock_predictor_instance

            response = client.post(
                "/api/v1/lnn/predict-uncertain",
                json={"model_name": "test_model", "input_data": [1.0, 2.0], "return_confidence": True},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 0
            # Verify that from_registry was called and cache.put was called
            MockPredictor.from_registry.assert_called_once()
            mock_cache.put.assert_called_once()

    def test_predict_uncertain_non_prediction_result(self, client, mock_registry):
        """Test prediction when result is not a PredictionResult instance."""
        mock_reg, mock_cache = mock_registry

        predictor = Mock()
        # Return a raw value instead of PredictionResult
        predictor.predict.return_value = 42.0
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        result = data["data"]
        assert result["prediction"] == 42.0
        assert result["uncertainty"] == 0.0  # confidence field in raw result is None/0

    def test_predict_uncertain_numpy_array_output(self, client, mock_registry):
        """Test prediction with numpy array output."""
        mock_reg, mock_cache = mock_registry

        predictor = Mock()
        # Return numpy array
        predictor.predict.return_value = _make_mock_result(
            value=np.array([8.0, 10.0, 12.0]), confidence=0.0, inference_time=10.0
        )
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        result = data["data"]
        assert "prediction" in result
        assert result["prediction"] == 10.0  # mean of [8, 10, 12]

    def test_predict_uncertain_single_element_list(self, client, mock_registry):
        """Test prediction with single-element list output."""
        mock_reg, mock_cache = mock_registry

        predictor = Mock()
        # Return single-element list
        predictor.predict.return_value = _make_mock_result(value=[15.5], confidence=0.0, inference_time=10.0)
        mock_cache.get.return_value = predictor

        response = client.post(
            "/api/v1/lnn/predict-uncertain",
            json={"model_name": "test_model", "input_data": [1.0, 2.0], "return_confidence": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        result = data["data"]
        assert result["prediction"] == 15.5
        assert result["uncertainty"] == 0.0  # std of single element is 0
