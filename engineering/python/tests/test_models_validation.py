"""
Test Common Validation and Data Models

Tests for:
- WearDataPoint: Single point on tool wear curve
- WearCurve: Full tool wear prediction curve with data points management
- WearPhase/UrgencyLevel: Enumerations for wear phases and urgency levels
- AdjustmentSuggestionItem/AdjustmentSuggestion: Parameter adjustment suggestions
- ValidationResult: Validation result with merge capability
- PredictionRequest/TrainingRequest: Request models with validation methods
"""

import pytest

from app.models.validation import (
    WearDataPoint,
    WearCurve,
    WearPhase,
    UrgencyLevel,
    AdjustmentSuggestionItem,
    AdjustmentSuggestion,
    ValidationResult,
    PredictionRequest,
    TrainingRequest,
)


class TestWearDataPoint:
    """Test WearDataPoint dataclass"""

    def test_default_initialization(self):
        point = WearDataPoint()
        assert point.time == 0.0
        assert point.wear == 0.0
        assert point.wear_rate == 0.0
        assert point.confidence == 1.0
        assert point.metadata is None

    def test_custom_initialization(self):
        point = WearDataPoint(
            time=100.5,
            wear=0.25,
            wear_rate=0.0025,
            confidence=0.95,
            metadata={"material": "45钢"},
        )
        assert point.time == 100.5
        assert point.wear == 0.25
        assert point.wear_rate == 0.0025
        assert point.confidence == 0.95
        assert point.metadata["material"] == "45钢"

    def test_to_dict(self):
        point = WearDataPoint(
            time=50.0,
            wear=0.1,
            wear_rate=0.002,
            confidence=0.9,
        )
        result = point.to_dict()
        assert result["time"] == 50.0
        assert result["wear"] == 0.1
        assert result["wear_rate"] == 0.002
        assert result["confidence"] == 0.9
        assert "metadata" not in result


class TestWearCurve:
    """Test WearCurve dataclass"""

    def test_default_initialization(self):
        curve = WearCurve()
        assert curve.data_points == []
        assert curve.material == ""
        assert curve.tool_type == ""
        assert curve.cutting_speed == 0.0
        assert curve.feed_rate == 0.0
        assert curve.depth_of_cut == 0.0
        assert curve.total_time == 0.0
        assert curve.max_wear == 0.0
        assert curve.confidence == 0.0

    def test_custom_initialization(self):
        curve = WearCurve(
            material="45钢",
            tool_type="硬质合金",
            cutting_speed=120.0,
            feed_rate=0.3,
            depth_of_cut=2.0,
            confidence=0.85,
        )
        assert curve.material == "45钢"
        assert curve.tool_type == "硬质合金"
        assert curve.cutting_speed == 120.0
        assert curve.feed_rate == 0.3
        assert curve.depth_of_cut == 2.0
        assert curve.confidence == 0.85

    def test_add_point_single(self):
        curve = WearCurve()
        point = WearDataPoint(time=10.0, wear=0.05)
        curve.add_point(point)
        assert len(curve.data_points) == 1
        assert curve.total_time == 10.0
        assert curve.max_wear == 0.05

    def test_add_point_updates_total_time(self):
        curve = WearCurve()
        curve.add_point(WearDataPoint(time=10.0, wear=0.05))
        curve.add_point(WearDataPoint(time=20.0, wear=0.10))
        curve.add_point(WearDataPoint(time=5.0, wear=0.03))
        assert curve.total_time == 20.0

    def test_add_point_updates_max_wear(self):
        curve = WearCurve()
        curve.add_point(WearDataPoint(time=10.0, wear=0.05))
        curve.add_point(WearDataPoint(time=20.0, wear=0.15))
        curve.add_point(WearDataPoint(time=30.0, wear=0.10))
        assert curve.max_wear == 0.15

    def test_add_point_preserves_order(self):
        curve = WearCurve()
        p1 = WearDataPoint(time=10.0, wear=0.05)
        p2 = WearDataPoint(time=20.0, wear=0.10)
        p3 = WearDataPoint(time=30.0, wear=0.15)
        curve.add_point(p1)
        curve.add_point(p2)
        curve.add_point(p3)
        assert curve.data_points == [p1, p2, p3]

    def test_add_point_with_later_smaller_wear(self):
        curve = WearCurve()
        curve.add_point(WearDataPoint(time=10.0, wear=0.20))
        curve.add_point(WearDataPoint(time=20.0, wear=0.10))
        assert curve.max_wear == 0.20

    def test_to_dict_structure(self):
        curve = WearCurve(
            material="不锈钢",
            tool_type="涂层刀具",
            cutting_speed=100.0,
            feed_rate=0.2,
            depth_of_cut=1.5,
        )
        curve.add_point(WearDataPoint(time=10.0, wear=0.05))
        curve.add_point(WearDataPoint(time=20.0, wear=0.10))

        result = curve.to_dict()
        assert "data_points" in result
        assert len(result["data_points"]) == 2
        assert result["material"] == "不锈钢"
        assert result["tool_type"] == "涂层刀具"
        assert result["cutting_speed"] == 100.0
        assert result["feed_rate"] == 0.2
        assert result["depth_of_cut"] == 1.5
        assert result["total_time"] == 20.0
        assert result["max_wear"] == 0.10
        assert result["confidence"] == 0.0

    def test_to_dict_data_points_format(self):
        curve = WearCurve()
        curve.add_point(WearDataPoint(time=5.0, wear=0.02, confidence=0.9))
        result = curve.to_dict()
        assert result["data_points"][0]["time"] == 5.0
        assert result["data_points"][0]["wear"] == 0.02
        assert result["data_points"][0]["confidence"] == 0.9


class TestWearPhase:
    """Test WearPhase enumeration"""

    def test_phase_values(self):
        assert WearPhase.INITIAL == "initial"
        assert WearPhase.STEADY == "steady"
        assert WearPhase.ACCELERATED == "accelerated"

    def test_phase_is_string(self):
        assert isinstance(WearPhase.INITIAL, str)
        assert isinstance(WearPhase.STEADY, str)
        assert isinstance(WearPhase.ACCELERATED, str)


class TestUrgencyLevel:
    """Test UrgencyLevel enumeration"""

    def test_urgency_values(self):
        assert UrgencyLevel.NORMAL == "normal"
        assert UrgencyLevel.WARNING == "warning"
        assert UrgencyLevel.CRITICAL == "critical"
        assert UrgencyLevel.IMMINENT == "imminent"

    def test_urgency_order_implied(self):
        levels = [
            UrgencyLevel.NORMAL,
            UrgencyLevel.WARNING,
            UrgencyLevel.CRITICAL,
            UrgencyLevel.IMMINENT,
        ]
        for level in levels:
            assert isinstance(level, str)


class TestAdjustmentSuggestionItem:
    """Test AdjustmentSuggestionItem dataclass"""

    def test_required_fields(self):
        item = AdjustmentSuggestionItem(
            parameter="cutting_speed",
            current_value=100.0,
            suggested_value=120.0,
            change_percent=20.0,
            reason="减少刀具磨损",
        )
        assert item.parameter == "cutting_speed"
        assert item.current_value == 100.0
        assert item.suggested_value == 120.0
        assert item.change_percent == 20.0
        assert item.reason == "减少刀具磨损"

    def test_default_confidence_and_priority(self):
        item = AdjustmentSuggestionItem(
            parameter="feed_rate",
            current_value=0.3,
            suggested_value=0.25,
            change_percent=-16.67,
            reason="优化表面质量",
        )
        assert item.confidence == 0.8
        assert item.priority == "medium"

    def test_custom_confidence_and_priority(self):
        item = AdjustmentSuggestionItem(
            parameter="depth_of_cut",
            current_value=2.0,
            suggested_value=1.5,
            change_percent=-25.0,
            reason="减少切削力",
            confidence=0.95,
            priority="high",
        )
        assert item.confidence == 0.95
        assert item.priority == "high"


class TestAdjustmentSuggestion:
    """Test AdjustmentSuggestion dataclass"""

    def test_empty_suggestions(self):
        suggestion = AdjustmentSuggestion()
        assert suggestion.suggestions == []
        assert suggestion.summary == ""
        assert suggestion.expected_improvement == ""

    def test_with_suggestions(self):
        item1 = AdjustmentSuggestionItem(
            parameter="speed",
            current_value=100.0,
            suggested_value=120.0,
            change_percent=20.0,
            reason="reason1",
        )
        item2 = AdjustmentSuggestionItem(
            parameter="feed",
            current_value=0.3,
            suggested_value=0.25,
            change_percent=-16.67,
            reason="reason2",
        )
        suggestion = AdjustmentSuggestion(
            suggestions=[item1, item2],
            summary="优化切削参数",
            expected_improvement="刀具寿命提升15%",
        )
        assert len(suggestion.suggestions) == 2
        assert suggestion.summary == "优化切削参数"
        assert suggestion.expected_improvement == "刀具寿命提升15%"

    def test_to_dict_structure(self):
        suggestion = AdjustmentSuggestion(
            suggestions=[
                AdjustmentSuggestionItem(
                    parameter="test",
                    current_value=1.0,
                    suggested_value=2.0,
                    change_percent=100.0,
                    reason="test reason",
                    confidence=0.9,
                    priority="high",
                )
            ],
            summary="Test Summary",
            expected_improvement="Test Improvement",
        )
        result = suggestion.to_dict()

        assert "suggestions" in result
        assert len(result["suggestions"]) == 1
        assert result["suggestions"][0]["parameter"] == "test"
        assert result["suggestions"][0]["current_value"] == 1.0
        assert result["suggestions"][0]["suggested_value"] == 2.0
        assert result["suggestions"][0]["change_percent"] == 100.0
        assert result["suggestions"][0]["reason"] == "test reason"
        assert result["suggestions"][0]["confidence"] == 0.9
        assert result["suggestions"][0]["priority"] == "high"
        assert result["summary"] == "Test Summary"
        assert result["expected_improvement"] == "Test Improvement"


class TestValidationResult:
    """Test ValidationResult dataclass"""

    def test_valid_result(self):
        result = ValidationResult(is_valid=True)
        assert result.is_valid is True
        assert result.errors == []
        assert result.warnings == []

    def test_invalid_result_with_errors(self):
        result = ValidationResult(
            is_valid=False,
            errors=["错误1", "错误2"],
        )
        assert result.is_valid is False
        assert len(result.errors) == 2
        assert "错误1" in result.errors
        assert "错误2" in result.errors

    def test_result_with_warnings(self):
        result = ValidationResult(
            is_valid=True,
            warnings=["警告1"],
        )
        assert result.is_valid is True
        assert len(result.warnings) == 1

    def test_merge_both_valid(self):
        result1 = ValidationResult(
            is_valid=True,
            errors=["error1"],
            warnings=["warning1"],
        )
        result2 = ValidationResult(
            is_valid=True,
            errors=["error2"],
            warnings=["warning2"],
        )
        merged = result1.merge(result2)
        assert merged.is_valid is True
        assert merged.errors == ["error1", "error2"]
        assert merged.warnings == ["warning1", "warning2"]

    def test_merge_first_invalid(self):
        result1 = ValidationResult(is_valid=False)
        result2 = ValidationResult(is_valid=True)
        merged = result1.merge(result2)
        assert merged.is_valid is False

    def test_merge_second_invalid(self):
        result1 = ValidationResult(is_valid=True)
        result2 = ValidationResult(is_valid=False)
        merged = result1.merge(result2)
        assert merged.is_valid is False

    def test_merge_both_invalid(self):
        result1 = ValidationResult(is_valid=False, errors=["err1"])
        result2 = ValidationResult(is_valid=False, errors=["err2"])
        merged = result1.merge(result2)
        assert merged.is_valid is False
        assert merged.errors == ["err1", "err2"]

    def test_merge_preserves_order(self):
        result1 = ValidationResult(is_valid=True, errors=["first"], warnings=["warn1"])
        result2 = ValidationResult(is_valid=True, errors=["second"], warnings=["warn2"])
        merged = result1.merge(result2)
        assert merged.errors == ["first", "second"]
        assert merged.warnings == ["warn1", "warn2"]


class TestPredictionRequest:
    """Test PredictionRequest dataclass"""

    def test_default_values(self):
        request = PredictionRequest()
        assert request.material == ""
        assert request.tool_type == ""
        assert request.cutting_speed == 0.0
        assert request.feed_rate == 0.0
        assert request.depth_of_cut == 0.0
        assert request.current_wear is None
        assert request.prediction_horizon == 60.0

    def test_custom_values(self):
        request = PredictionRequest(
            material="45钢",
            tool_type="硬质合金",
            cutting_speed=120.0,
            feed_rate=0.3,
            depth_of_cut=2.0,
            current_wear=0.1,
            prediction_horizon=300.0,
        )
        assert request.material == "45钢"
        assert request.tool_type == "硬质合金"
        assert request.cutting_speed == 120.0
        assert request.feed_rate == 0.3
        assert request.depth_of_cut == 2.0
        assert request.current_wear == 0.1
        assert request.prediction_horizon == 300.0

    def test_validate_valid_request(self):
        request = PredictionRequest(
            cutting_speed=100.0,
            feed_rate=0.3,
            depth_of_cut=2.0,
            prediction_horizon=60.0,
        )
        result = request.validate()
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_validate_cutting_speed_zero(self):
        request = PredictionRequest(
            cutting_speed=0,
            feed_rate=0.3,
            depth_of_cut=2.0,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "切削速度必须大于0" in result.errors

    def test_validate_cutting_speed_negative(self):
        request = PredictionRequest(
            cutting_speed=-100.0,
            feed_rate=0.3,
            depth_of_cut=2.0,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "切削速度必须大于0" in result.errors

    def test_validate_feed_rate_zero(self):
        request = PredictionRequest(
            cutting_speed=100.0,
            feed_rate=0,
            depth_of_cut=2.0,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "进给量必须大于0" in result.errors

    def test_validate_depth_of_cut_zero(self):
        request = PredictionRequest(
            cutting_speed=100.0,
            feed_rate=0.3,
            depth_of_cut=0,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "切削深度必须大于0" in result.errors

    def test_validate_prediction_horizon_zero(self):
        request = PredictionRequest(
            cutting_speed=100.0,
            feed_rate=0.3,
            depth_of_cut=2.0,
            prediction_horizon=0,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "预测时长必须大于0" in result.errors

    def test_validate_prediction_horizon_exceeds_warning(self):
        request = PredictionRequest(
            cutting_speed=100.0,
            feed_rate=0.3,
            depth_of_cut=2.0,
            prediction_horizon=3601,
        )
        result = request.validate()
        assert result.is_valid is True
        assert "预测时长超过1小时，精度可能下降" in result.warnings

    def test_validate_prediction_horizon_at_boundary(self):
        request = PredictionRequest(
            cutting_speed=100.0,
            feed_rate=0.3,
            depth_of_cut=2.0,
            prediction_horizon=3600,
        )
        result = request.validate()
        assert result.is_valid is True
        assert len(result.warnings) == 0

    def test_validate_multiple_errors(self):
        request = PredictionRequest(
            cutting_speed=-100.0,
            feed_rate=-0.3,
            depth_of_cut=-2.0,
            prediction_horizon=0,
        )
        result = request.validate()
        assert result.is_valid is False
        assert len(result.errors) == 4

    def test_validate_multiple_params_valid(self):
        request = PredictionRequest(
            cutting_speed=50.0,
            feed_rate=0.1,
            depth_of_cut=1.0,
            prediction_horizon=120.0,
        )
        result = request.validate()
        assert result.is_valid is True


class TestTrainingRequest:
    """Test TrainingRequest dataclass"""

    def test_default_values(self):
        request = TrainingRequest()
        assert request.model_name == ""
        assert request.model_type == "CFC"
        assert request.dataset_path == ""
        assert request.epochs == 100
        assert request.batch_size == 32
        assert request.learning_rate == 0.001
        assert request.validation_split == 0.2
        assert request.device == "auto"

    def test_custom_values(self):
        request = TrainingRequest(
            model_name="custom_model",
            model_type="LTC",
            dataset_path="/data/train.csv",
            epochs=200,
            batch_size=64,
            learning_rate=0.0005,
            validation_split=0.3,
            device="cuda:0",
        )
        assert request.model_name == "custom_model"
        assert request.model_type == "LTC"
        assert request.dataset_path == "/data/train.csv"
        assert request.epochs == 200
        assert request.batch_size == 64
        assert request.learning_rate == 0.0005
        assert request.validation_split == 0.3
        assert request.device == "cuda:0"

    def test_validate_valid_request(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        result = request.validate()
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_validate_empty_model_name(self):
        request = TrainingRequest(
            model_name="",
            dataset_path="/data/train.csv",
        )
        result = request.validate()
        assert result.is_valid is False
        assert "模型名称不能为空" in result.errors

    def test_validate_empty_dataset_path(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="",
        )
        result = request.validate()
        assert result.is_valid is False
        assert "数据集路径不能为空" in result.errors

    def test_validate_epochs_zero(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            epochs=0,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "训练轮数必须在1-10000之间" in result.errors

    def test_validate_epochs_negative(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            epochs=-100,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "训练轮数必须在1-10000之间" in result.errors

    def test_validate_epochs_exceeds_max(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            epochs=10001,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "训练轮数必须在1-10000之间" in result.errors

    def test_validate_epochs_at_min_boundary(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            epochs=1,
        )
        result = request.validate()
        assert result.is_valid is True

    def test_validate_epochs_at_max_boundary(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            epochs=10000,
        )
        result = request.validate()
        assert result.is_valid is True

    def test_validate_batch_size_zero(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            batch_size=0,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "批量大小必须在1-512之间" in result.errors

    def test_validate_batch_size_negative(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            batch_size=-32,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "批量大小必须在1-512之间" in result.errors

    def test_validate_batch_size_exceeds_max(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            batch_size=513,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "批量大小必须在1-512之间" in result.errors

    def test_validate_batch_size_at_boundaries(self):
        request1 = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            batch_size=1,
        )
        assert request1.validate().is_valid is True

        request2 = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            batch_size=512,
        )
        assert request2.validate().is_valid is True

    def test_validate_learning_rate_zero(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            learning_rate=0,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "学习率必须在0-1之间" in result.errors

    def test_validate_learning_rate_negative(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            learning_rate=-0.001,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "学习率必须在0-1之间" in result.errors

    def test_validate_learning_rate_exceeds_max(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            learning_rate=1.1,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "学习率必须在0-1之间" in result.errors

    def test_validate_learning_rate_at_boundaries(self):
        request1 = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            learning_rate=0.000001,
        )
        assert request1.validate().is_valid is True

        request2 = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            learning_rate=1.0,
        )
        assert request2.validate().is_valid is True

    def test_validate_validation_split_below_range(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            validation_split=-0.1,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "验证集比例必须在0.0-0.5之间" in result.errors

    def test_validate_validation_split_above_range(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            validation_split=0.6,
        )
        result = request.validate()
        assert result.is_valid is False
        assert "验证集比例必须在0.0-0.5之间" in result.errors

    def test_validate_validation_split_at_boundaries(self):
        request1 = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            validation_split=0.0,
        )
        assert request1.validate().is_valid is True

        request2 = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            validation_split=0.5,
        )
        assert request2.validate().is_valid is True

    def test_validate_invalid_model_type(self):
        request = TrainingRequest(
            model_name="test_model",
            dataset_path="/data/train.csv",
            model_type="INVALID",
        )
        result = request.validate()
        assert result.is_valid is False
        assert "不支持的模型类型: INVALID" in result.errors

    def test_validate_valid_model_types(self):
        for model_type in ["CFC", "LTC", "HYBRID", "cnn"]:
            request = TrainingRequest(
                model_name="test_model",
                dataset_path="/data/train.csv",
                model_type=model_type,
            )
            result = request.validate()
            assert result.is_valid is True, f"Model type {model_type} should be valid"

    def test_validate_multiple_errors(self):
        request = TrainingRequest(
            model_name="",
            dataset_path="",
            epochs=0,
            batch_size=0,
            learning_rate=-1,
            validation_split=1.0,
            model_type="INVALID",
        )
        result = request.validate()
        assert result.is_valid is False
        assert len(result.errors) >= 5

    def test_validate_typical_valid_request(self):
        request = TrainingRequest(
            model_name="cutting_force_v2",
            model_type="CFC",
            dataset_path="/path/to/dataset.csv",
            epochs=500,
            batch_size=128,
            learning_rate=0.001,
            validation_split=0.2,
            device="cuda:0",
        )
        result = request.validate()
        assert result.is_valid is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
