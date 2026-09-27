"""
Test LNN Inference System

8.2 预测器测试规范
Tests for:
- PredictionResult dataclass
- LNNPredictor class (模型加载、单次预测、批量预测、预处理/后处理、错误处理)
- ModelInfo dataclass
- LNNModelRegistry class
- BatchInferenceEngine class
"""

import pytest
import numpy as np
import json
from pathlib import Path
from unittest.mock import MagicMock

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from app.ai.lnn.inference.predictor import LNNPredictor, PredictionResult
from app.ai.lnn.inference.registry import LNNModelRegistry, ModelInfo
from app.ai.lnn.inference.batch_inference import BatchInferenceEngine
from app.ai.lnn.preprocessing import DataPreprocessor, NormalizationMethod
from app.ai.lnn.postprocessing import ResultPostprocessor
from app.ai.lnn.core import EngineType, InferenceResult


class TestPredictionResult:
    def test_create_result(self):
        result = PredictionResult(
            value=np.array([1.0, 2.0]),
            confidence=0.95,
            inference_time=25.5,
            model_info={"name": "test_model"},
        )
        assert result.confidence == 0.95
        assert result.inference_time == 25.5
        assert result.model_info["name"] == "test_model"

    def test_serialization(self):
        result = PredictionResult(
            value=[1.0, 2.0],
            confidence=0.9,
            inference_time=30.0,
            model_info={"name": "test"},
        )
        d = result.to_dict()
        restored = PredictionResult.from_dict(d)
        assert restored.confidence == result.confidence
        assert restored.inference_time == result.inference_time

    def test_numpy_serialization(self):
        result = PredictionResult(
            value=np.array([0.8, 0.2]),
            confidence=0.85,
            inference_time=15.0,
        )
        d = result.to_dict()
        assert isinstance(d["value"], list)
        restored = PredictionResult.from_dict(d)
        assert isinstance(restored.value, np.ndarray)

    def test_default_values(self):
        result = PredictionResult(value=42.0)
        assert result.confidence == 0.0
        assert result.inference_time == 0.0
        assert result.model_info == {}


class TestLNNPredictor:
    @pytest.fixture
    def mock_model(self):
        model = MagicMock()
        model.model_name = "test_model"

        def mock_forward(x):
            batch_size = x.shape[0] if hasattr(x, "shape") else 1
            return np.array([[0.8]] * batch_size)

        model.return_value = np.array([[0.8]])
        model.side_effect = mock_forward
        return model

    def test_single_predict(self, mock_model):
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict(np.array([[1.0, 2.0]]))
        assert result is not None

    def test_predict_with_confidence(self, mock_model):
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict(np.array([[1.0, 2.0]]), return_confidence=True)
        assert isinstance(result, PredictionResult)
        assert hasattr(result, "confidence")

    def test_predict_without_confidence(self, mock_model):
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict(np.array([[1.0, 2.0]]), return_confidence=False)
        assert not isinstance(result, PredictionResult)

    def test_batch_predict(self, mock_model):
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        data = [np.array([[1.0]]) for _ in range(10)]
        results = predictor.predict_batch(data, batch_size=5)
        assert len(results) == 10
        assert all(isinstance(r, PredictionResult) for r in results)

    def test_streaming_predict(self, mock_model):
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        data_stream = [np.array([[1.0]]) for _ in range(5)]
        results = list(predictor.predict_streaming(data_stream))
        assert len(results) == 5

    def test_get_statistics(self, mock_model):
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        predictor.predict(np.array([[1.0]]))
        stats = predictor.get_statistics()
        assert "total_inferences" in stats
        assert "average_inference_time_ms" in stats
        assert "max_inference_time_ms" in stats
        assert "min_inference_time_ms" in stats
        assert "peak_memory_mb" in stats
        assert stats["total_inferences"] == 1

    def test_multiple_inferences_stats(self, mock_model):
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        for _ in range(5):
            predictor.predict(np.array([[1.0]]))
        stats = predictor.get_statistics()
        assert stats["total_inferences"] == 5
        assert stats["average_inference_time_ms"] > 0

    def test_input_standardization_numpy(self, mock_model):
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict(np.array([[1.0, 2.0]]))
        assert result is not None

    def test_input_standardization_list(self, mock_model):
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict([[1.0, 2.0]])
        assert result is not None

    def test_input_standardization_scalar(self, mock_model):
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict([1.0])
        assert result is not None

    def test_error_handling(self, mock_model):
        mock_model.side_effect = RuntimeError("Model error")
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        with pytest.raises(RuntimeError, match="Prediction failed"):
            predictor.predict(np.array([[1.0]]))


class TestModelInfo:
    def test_create_model_info(self):
        info = ModelInfo(
            name="test",
            model_type="CFC",
            model_path="/tmp/test.pt",
            input_features=["f1"],
            output_features=["o1"],
        )
        assert info.name == "test"
        assert info.version == "1.0.0"

    def test_empty_name_validation(self):
        with pytest.raises(ValueError, match="name cannot be empty"):
            ModelInfo(
                name="",
                model_type="CFC",
                model_path="/tmp/test.pt",
                input_features=["f1"],
                output_features=["o1"],
            )

    def test_empty_type_validation(self):
        with pytest.raises(ValueError, match="type cannot be empty"):
            ModelInfo(
                name="test",
                model_type="",
                model_path="/tmp/test.pt",
                input_features=["f1"],
                output_features=["o1"],
            )

    def test_empty_path_validation(self):
        with pytest.raises(ValueError, match="path cannot be empty"):
            ModelInfo(
                name="test",
                model_type="CFC",
                model_path="",
                input_features=["f1"],
                output_features=["o1"],
            )

    def test_empty_features_validation(self):
        with pytest.raises(ValueError, match="Input features cannot be empty"):
            ModelInfo(
                name="test",
                model_type="CFC",
                model_path="/tmp/test.pt",
                input_features=[],
                output_features=["o1"],
            )

    def test_serialization(self):
        info = ModelInfo(
            name="test",
            model_type="CFC",
            model_path="/tmp/test.pt",
            input_features=["f1", "f2"],
            output_features=["o1"],
            version="2.0.0",
        )
        d = info.to_dict()
        restored = ModelInfo.from_dict(d)
        assert restored.name == info.name
        assert restored.model_type == info.model_type
        assert restored.input_features == info.input_features
        assert restored.version == info.version


class TestLNNModelRegistry:
    def test_predefined_models(self):
        registry = LNNModelRegistry()
        models = registry.list_models()
        assert "cutting_force" in models
        assert "wear_prediction" in models
        assert "surface_roughness" in models
        assert "temperature" in models

    def test_get_model_info_exact(self):
        registry = LNNModelRegistry()
        info = registry.get_model_info("cutting_force")
        assert info is not None
        assert info.name == "cutting_force"
        assert info.model_type == "CFC"

    def test_get_model_info_not_found(self):
        registry = LNNModelRegistry()
        info = registry.get_model_info("nonexistent")
        assert info is None

    def test_fuzzy_match(self):
        registry = LNNModelRegistry()
        info = registry.get_model_info("cutting", fuzzy_match=True)
        assert info is not None
        assert "cutting_force" == info.name

    def test_fuzzy_match_not_found(self):
        registry = LNNModelRegistry()
        info = registry.get_model_info("xyz", fuzzy_match=True)
        assert info is None

    def test_register_model(self):
        registry = LNNModelRegistry()
        new_model = ModelInfo(
            name="test_model",
            model_type="CFC",
            model_path="/tmp/test.pt",
            input_features=["f1"],
            output_features=["o1"],
        )
        result = registry.register_model(new_model)
        assert result is True
        assert registry.get_model_info("test_model") is not None

    def test_duplicate_register(self):
        registry = LNNModelRegistry()
        new_info = ModelInfo(
            name="cutting_force",
            model_type="CFC",
            model_path="/tmp/test.pt",
            input_features=["f1"],
            output_features=["o1"],
        )
        result = registry.register_model(new_info)
        assert result is False

    def test_list_models_names(self):
        registry = LNNModelRegistry()
        names = registry.list_models(return_objects=False)
        assert isinstance(names, list)
        assert "cutting_force" in names

    def test_list_models_objects(self):
        registry = LNNModelRegistry()
        objects = registry.list_models(return_objects=True)
        assert isinstance(objects, list)
        assert all(isinstance(obj, ModelInfo) for obj in objects)

    def test_validate_model_not_found(self):
        registry = LNNModelRegistry()
        result = registry.validate_model("nonexistent")
        assert result["valid"] is False
        assert "not found" in result["reason"]

    def test_validate_model_file_not_exists(self):
        """注册表有登记但权重文件缺失的模型应判定无效（fail-closed）。

        历史说明：此用例曾以 cutting_force 为对象；2026-09-09 起
        cutting_force.npz 已真实生成入库，"文件不存在"前提失效（这正是
        权重产出落地的正面信号）。改用注册表内有登记、但按 DATA_INSUFFICIENT
        策略从未产出权重的 surface_roughness 占位模型验证该路径。
        """
        registry = LNNModelRegistry()
        result = registry.validate_model("surface_roughness")
        assert result["valid"] is False
        assert result["file_exists"] is False
        assert result["model_name"] == "surface_roughness"

    def test_validate_model_file_exists(self):
        """权重文件真实存在且可加载的模型应判定有效。"""
        # 注册表模板里的 model_path 是 CWD 相对路径（"models/lnn/..."），只有
        # 打包流程在仓库根生成过 dist 副本时才能按默认路径命中；测试显式传
        # 入仓库内真实权重路径，消除对 CWD/打包产物的依赖
        repo_weights = Path(__file__).resolve().parents[2] / "models" / "lnn" / "cutting_force.npz"
        registry = LNNModelRegistry()
        result = registry.validate_model("cutting_force", model_path=str(repo_weights))
        assert result["file_exists"] is True
        assert result["valid"] is True
        assert result["model_name"] == "cutting_force"


class TestBatchInferenceEngine:
    @pytest.fixture
    def mock_predictor(self):
        predictor = MagicMock(spec=LNNPredictor)

        def mock_predict_batch(data_list, batch_size=32):
            return [PredictionResult(value=[0.8], confidence=0.9, inference_time=20.0) for _ in range(len(data_list))]

        predictor.predict_batch = mock_predict_batch
        return predictor

    def test_process_batch(self, mock_predictor):
        engine = BatchInferenceEngine(predictor=mock_predictor, max_concurrency=4)
        data = [np.array([[1.0]]) for _ in range(20)]
        future = engine.process_batch(data)
        results = future.result(timeout=10)
        assert results is not None
        assert len(results) == 20

    def test_statistics(self, mock_predictor):
        engine = BatchInferenceEngine(predictor=mock_predictor, max_concurrency=4)
        stats = engine.get_statistics()
        assert "total_processed" in stats
        assert "total_success" in stats
        assert "total_failed" in stats
        assert "throughput_samples_per_sec" in stats
        assert "queue_length" in stats
        assert "current_batch_size" in stats

    def test_time_window_stats(self, mock_predictor):
        engine = BatchInferenceEngine(predictor=mock_predictor, max_concurrency=4)
        stats = engine.get_statistics(time_window_seconds=60)
        assert "time_window" in stats
        assert "count" in stats["time_window"]
        assert "success" in stats["time_window"]
        assert "failed" in stats["time_window"]
        assert "throughput_samples_per_sec" in stats["time_window"]

    def test_time_window_stats_5min(self, mock_predictor):
        engine = BatchInferenceEngine(predictor=mock_predictor, max_concurrency=4)
        data = [np.array([[1.0]])]
        future = engine.process_batch(data)
        future.result(timeout=10)
        stats = engine.get_statistics(time_window_seconds=300)
        assert "time_window" in stats
        assert stats["time_window"]["window_seconds"] == 300

    def test_concurrent_tasks(self, mock_predictor):
        engine = BatchInferenceEngine(predictor=mock_predictor, max_concurrency=10)
        futures = []
        for i in range(10):
            data = [np.array([[float(i)]])]
            futures.append(engine.process_batch(data))
        for f in futures:
            f.result(timeout=10)
        stats = engine.get_statistics()
        assert stats["total_success"] == 10

    def test_dynamic_batch_size(self, mock_predictor):
        engine = BatchInferenceEngine(
            predictor=mock_predictor,
            max_concurrency=4,
            enable_dynamic_batching=True,
        )
        data = [np.array([[1.0]]) for _ in range(50)]
        future = engine.process_batch(data)
        future.result(timeout=10)
        assert engine._current_batch_size > 0

    def test_priority_parameter(self, mock_predictor):
        engine = BatchInferenceEngine(predictor=mock_predictor, max_concurrency=4)
        data = [np.array([[1.0]])]
        future = engine.process_batch(data, priority=1)
        assert future is not None
        results = future.result(timeout=10)
        assert len(results) == 1

    def test_max_concurrency_default(self, mock_predictor):
        engine = BatchInferenceEngine(predictor=mock_predictor)
        assert engine.max_concurrency == 10


# 8.2.1 模型加载测试


class TestPredictorModelLoading:
    """测试预测器模型加载功能"""

    @pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
    def test_predictor_with_torch_model(self):
        """测试预测器正确加载torch模型"""
        mock_model = MagicMock()
        mock_model.model_name = "torch_model"
        mock_model.return_value = torch.tensor([[0.8]])

        def mock_forward(x):
            return torch.tensor([[0.8]] * x.shape[0])

        mock_model.side_effect = mock_forward
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        assert predictor.model_name == "torch_model"

    def test_predictor_with_numpy_model(self):
        """测试预测器加载numpy模型"""
        mock_model = MagicMock()
        mock_model.model_name = "numpy_model"

        def mock_forward(x):
            return np.array([[0.8]])

        mock_model.side_effect = mock_forward
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        assert predictor.model_name == "numpy_model"

    def test_predictor_model_name_default(self):
        """测试预测器模型名称默认值"""
        mock_model = MagicMock(spec=[])

        def mock_forward(x):
            return np.array([[0.8]])

        mock_model.side_effect = mock_forward
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        assert predictor.model_name == "unknown"

    def test_predictor_custom_model_name(self):
        """测试预测器自定义模型名称"""
        mock_model = MagicMock()
        mock_model.model_name = "custom_model"

        def mock_forward(x):
            return np.array([[0.8]])

        mock_model.side_effect = mock_forward
        predictor = LNNPredictor(
            model=mock_model,
            model_name="my_predictor",
            use_amp=False,
            auto_device=False,
        )
        assert predictor.model_name == "my_predictor"


# 8.2.2 单次预测测试


class TestPredictorSinglePrediction:
    """测试预测器单次预测功能"""

    @pytest.fixture
    def mock_model(self):
        model = MagicMock()
        model.model_name = "test_model"

        def mock_forward(x):
            return np.array([[0.8]] * x.shape[0])

        model.side_effect = mock_forward
        return model

    def test_predict_result_not_none(self, mock_model):
        """测试单次预测结果不为空"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict(np.array([[1.0, 2.0]]))
        assert result is not None

    def test_predict_output_format(self, mock_model):
        """测试单次预测输出格式"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict(np.array([[1.0, 2.0]]))
        assert isinstance(result, np.ndarray)

    def test_predict_with_confidence_returns_prediction_result(self, mock_model):
        """测试带置信度的预测返回PredictionResult"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict(np.array([[1.0, 2.0]]), return_confidence=True)
        assert isinstance(result, PredictionResult)
        assert 0.0 <= result.confidence <= 1.0

    def test_predict_response_time_under_threshold(self, mock_model):
        """测试单次预测响应时间在合理范围内"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict(np.array([[1.0, 2.0]]), return_confidence=True)
        assert result.inference_time >= 0


# 8.2.3 批量预测测试


class TestPredictorBatchPrediction:
    """测试预测器批量预测功能"""

    @pytest.fixture
    def mock_model(self):
        model = MagicMock()
        model.model_name = "test_model"

        def mock_forward(x):
            return np.array([[0.8]] * x.shape[0])

        model.side_effect = mock_forward
        return model

    def test_batch_prediction_correct_count(self, mock_model):
        """测试批量预测结果数量正确"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        data = [np.array([[1.0]]) for _ in range(20)]
        results = predictor.predict_batch(data, batch_size=5)
        assert len(results) == 20

    def test_batch_prediction_chunking(self, mock_model):
        """测试批量预测数据分块处理"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        data = [np.array([[1.0]]) for _ in range(100)]
        results = predictor.predict_batch(data, batch_size=10)
        assert len(results) == 100

    def test_batch_prediction_all_results_are_prediction_result(self, mock_model):
        """测试批量预测所有结果为PredictionResult"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        data = [np.array([[1.0]]) for _ in range(10)]
        results = predictor.predict_batch(data, batch_size=5)
        assert all(isinstance(r, PredictionResult) for r in results)

    def test_batch_prediction_with_different_batch_sizes(self, mock_model):
        """测试不同批量大小的预测"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        data = [np.array([[1.0]]) for _ in range(30)]
        for bs in [1, 5, 10, 15, 30]:
            results = predictor.predict_batch(data, batch_size=bs)
            assert len(results) == 30


# 8.2.4 预处理/后处理测试


class TestDataPreprocessing:
    """测试数据预处理逻辑"""

    def test_preprocessor_z_score_normalization(self):
        """测试Z-score标准化"""
        preprocessor = DataPreprocessor(normalization=NormalizationMethod.Z_SCORE)
        X = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        result = preprocessor.fit_transform(X)
        assert result.features is not None
        assert result.normalization_method == "z_score"
        assert result.features.shape == X.shape

    def test_preprocessor_min_max_normalization(self):
        """测试Min-Max标准化"""
        preprocessor = DataPreprocessor(normalization=NormalizationMethod.MIN_MAX)
        X = np.array([[0.0, 0.0], [5.0, 5.0], [10.0, 10.0]])
        result = preprocessor.fit_transform(X)
        assert result.features is not None
        assert result.normalization_method == "min_max"

    def test_preprocessor_missing_values_handling(self):
        """测试缺失值处理"""
        preprocessor = DataPreprocessor(missing_strategy="mean")
        X = np.array([[1.0, 2.0], [np.nan, 4.0], [5.0, 6.0]])
        result = preprocessor.fit_transform(X)
        assert result.missing_values_filled >= 1

    def test_preprocessor_outlier_detection(self):
        """测试异常值检测"""
        preprocessor = DataPreprocessor(outlier_method="z_score", outlier_threshold=1.0)
        X = np.array([[1.0], [2.0], [3.0], [100.0]])
        result = preprocessor.fit_transform(X)
        assert result.outliers_detected >= 0

    def test_preprocessor_extract_numeric_features(self):
        """测试数值特征提取"""
        data = {"a": 1.0, "b": 2, "c": [3.0, 4.0]}
        features = DataPreprocessor.extract_numeric_features(data)
        assert isinstance(features, np.ndarray)
        assert len(features) == 4

    def test_preprocessor_encode_categorical(self):
        """测试类别特征编码"""
        categories = ["red", "blue", "red", "green"]
        encoded, vocab = DataPreprocessor.encode_categorical(categories)
        assert encoded.shape == (4, 3)
        assert len(vocab) == 3

    def test_preprocessor_fit_transform_consistency(self):
        """测试fit和transform一致性"""
        preprocessor = DataPreprocessor()
        X = np.array([[1.0, 2.0], [3.0, 4.0]])
        result1 = preprocessor.fit_transform(X)
        result2 = preprocessor.transform(X)
        assert np.allclose(result1.features, result2.features)


class TestDataPostprocessing:
    """测试数据后处理逻辑"""

    def setup_method(self):
        self.postprocessor = ResultPostprocessor(include_metadata=True, include_uncertainty=True)

    def test_postprocess_result_structure(self):
        """测试结果结构化格式"""
        predictions = np.array([[0.8, 0.2], [0.3, 0.7]])
        result = self.postprocessor.process_result(
            predictions=predictions,
            engine=EngineType.LNN,
            model_name="test_model",
            processing_time_ms=15.0,
        )
        assert isinstance(result, InferenceResult)
        assert result.engine_used == EngineType.LNN
        assert result.model_used == "test_model"

    def test_postprocess_confidence_computation(self):
        """测试置信度计算"""
        predictions = np.array([[0.9, 0.1], [0.85, 0.15]])
        result = self.postprocessor.process_result(predictions=predictions, engine=EngineType.LNN)
        assert 0.0 <= result.confidence <= 1.0

    def test_postprocess_uncertainty_assessment(self):
        """测试不确定性评估"""
        predictions = np.array([[0.5, 0.5], [0.5, 0.5]])
        result = self.postprocessor.process_result(predictions=predictions, engine=EngineType.LNN)
        assert result.uncertainty is not None
        assert "entropy" in result.uncertainty
        assert "normalized_entropy" in result.uncertainty

    def test_postprocess_evidence_building(self):
        """测试证据列表构建"""
        predictions = np.array([[0.8, 0.2], [0.3, 0.7]])
        result = self.postprocessor.process_result(predictions=predictions, engine=EngineType.LNN)
        assert len(result.evidence) == 2

    def test_postprocess_to_json(self):
        """测试JSON格式输出"""
        predictions = np.array([[0.8, 0.2]])
        result = self.postprocessor.process_result(predictions=predictions, engine=EngineType.LNN)
        json_str = self.postprocessor.to_json(result)
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert "prediction" in parsed

    def test_postprocess_to_xml(self):
        """测试XML格式输出"""
        predictions = np.array([[0.8, 0.2]])
        result = self.postprocessor.process_result(predictions=predictions, engine=EngineType.LNN)
        xml_str = self.postprocessor.to_xml(result)
        assert isinstance(xml_str, str)
        assert "<?xml" in xml_str
        assert "<InferenceResult>" in xml_str

    def test_postprocess_without_metadata(self):
        """测试不包含元数据的后处理"""
        postprocessor = ResultPostprocessor(include_metadata=False, include_uncertainty=False)
        predictions = np.array([[0.8, 0.2]])
        result = postprocessor.process_result(predictions=predictions, engine=EngineType.LNN)
        assert result.uncertainty is None


# 8.2.5 错误处理测试


class TestPredictorErrorHandling:
    """测试预测器错误处理机制"""

    @pytest.fixture
    def mock_model(self):
        model = MagicMock()
        model.model_name = "test_model"

        def mock_forward(x):
            return np.array([[0.8]] * x.shape[0])

        model.side_effect = mock_forward
        return model

    def test_invalid_input_type_raises_error(self, mock_model):
        """测试无效输入类型抛出异常"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)

        class InvalidType:
            pass

        with pytest.raises(RuntimeError, match="Prediction failed"):
            predictor.predict(InvalidType())

    def test_model_runtime_error_wrapped(self, mock_model):
        """测试模型运行时错误被包装"""
        mock_model.side_effect = RuntimeError("Internal model error")
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        with pytest.raises(RuntimeError, match="Prediction failed"):
            predictor.predict(np.array([[1.0]]))

    def test_missing_features_handling(self, mock_model):
        """测试缺失特征输入处理"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        input_with_missing = np.array([[np.nan, 2.0, 3.0]])
        result = predictor.predict(input_with_missing)
        assert result is not None

    def test_out_of_range_values_handling(self, mock_model):
        """测试超出范围的值处理"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        extreme_input = np.array([[1e10, -1e10, 0.0]])
        result = predictor.predict(extreme_input)
        assert result is not None

    def test_empty_input_handling(self, mock_model):
        """测试空输入处理"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        empty_input = np.array([]).reshape(0, 1)
        with pytest.raises(Exception):
            predictor.predict(empty_input)

    def test_dict_input_handling(self, mock_model):
        """测试字典输入处理"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        dict_input = {"feature1": 1.0, "feature2": 2.0}
        result = predictor.predict(dict_input)
        assert result is not None

    def test_single_value_input_handling(self, mock_model):
        """测试单值输入处理"""
        predictor = LNNPredictor(model=mock_model, use_amp=False, auto_device=False)
        result = predictor.predict(5.0)
        assert result is not None


class TestPreprocessingErrorHandling:
    """测试预处理错误处理"""

    def test_preprocessor_not_fitted_raises_on_transform(self):
        """测试未拟合预处理器在inverse_transform时抛出异常"""
        preprocessor = DataPreprocessor()
        preprocessor.is_fitted = False
        preprocessor.mean_ = None
        preprocessor.std_ = None
        preprocessor.min_ = None
        preprocessor.max_ = None
        X = np.array([[1.0, 2.0]])
        with pytest.raises(RuntimeError, match="must be fitted"):
            preprocessor.inverse_transform(X)

    def test_preprocessor_handles_all_nan(self):
        """测试预处理器处理全NaN数据"""
        preprocessor = DataPreprocessor(missing_strategy="zero")
        X = np.array([[np.nan, np.nan], [np.nan, np.nan]])
        result = preprocessor.fit_transform(X)
        assert result.missing_values_filled == 4


class TestPostprocessingErrorHandling:
    """测试后处理错误处理"""

    def test_postprocess_empty_predictions(self):
        """测试空预测结果处理"""
        postprocessor = ResultPostprocessor()
        predictions = np.array([]).reshape(0, 1)
        result = postprocessor.process_result(predictions=predictions, engine=EngineType.LNN)
        assert result is not None
        assert result.confidence == 0.0 or len(result.evidence) == 0
