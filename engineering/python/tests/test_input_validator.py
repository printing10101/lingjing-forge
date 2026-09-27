"""
Test Input Validation Utilities

Tests for:
- validate_cutting_parameters: Cutting parameter boundary validation
- validate_prediction_horizon: Prediction horizon range validation
- validate_training_params: Training parameter validation including regex patterns
- sanitize_string: String sanitization with length limits
- validate_rag_query: RAG query text validation
- validate_material_name: Material name validation
- validate_file_path: File path existence validation
- coalesce: Helper function for null-coalescing
"""

import os
import pytest

from app.middleware.input_validator import (
    validate_cutting_parameters,
    validate_prediction_horizon,
    validate_training_params,
    sanitize_string,
    validate_rag_query,
    validate_material_name,
    validate_file_path,
    coalesce,
)


class TestValidateCuttingParameters:
    """Test cutting parameter validation"""

    def test_valid_parameters(self):
        errors = validate_cutting_parameters(
            cutting_speed=100.0,
            feed_rate=0.3,
            depth_of_cut=2.0,
        )
        assert errors == []

    def test_boundary_values(self):
        errors = validate_cutting_parameters(
            cutting_speed=0.001,
            feed_rate=0.001,
            depth_of_cut=0.001,
        )
        assert errors == []

    def test_cutting_speed_zero(self):
        errors = validate_cutting_parameters(
            cutting_speed=0,
            feed_rate=0.3,
            depth_of_cut=2.0,
        )
        assert "切削速度必须大于0" in errors

    def test_cutting_speed_negative(self):
        errors = validate_cutting_parameters(
            cutting_speed=-100.0,
            feed_rate=0.3,
            depth_of_cut=2.0,
        )
        assert "切削速度必须大于0" in errors

    def test_cutting_speed_exceeds_max(self):
        errors = validate_cutting_parameters(
            cutting_speed=10001,
            feed_rate=0.3,
            depth_of_cut=2.0,
        )
        assert "切削速度不能超过10000 m/min" in errors

    def test_cutting_speed_at_max(self):
        errors = validate_cutting_parameters(
            cutting_speed=10000,
            feed_rate=0.3,
            depth_of_cut=2.0,
        )
        assert errors == []

    def test_feed_rate_zero(self):
        errors = validate_cutting_parameters(
            cutting_speed=100.0,
            feed_rate=0,
            depth_of_cut=2.0,
        )
        assert "进给量必须大于0" in errors

    def test_feed_rate_negative(self):
        errors = validate_cutting_parameters(
            cutting_speed=100.0,
            feed_rate=-0.3,
            depth_of_cut=2.0,
        )
        assert "进给量必须大于0" in errors

    def test_feed_rate_exceeds_max(self):
        errors = validate_cutting_parameters(
            cutting_speed=100.0,
            feed_rate=51,
            depth_of_cut=2.0,
        )
        assert "进给量不能超过50 mm/r" in errors

    def test_feed_rate_at_max(self):
        errors = validate_cutting_parameters(
            cutting_speed=100.0,
            feed_rate=50,
            depth_of_cut=2.0,
        )
        assert errors == []

    def test_depth_of_cut_zero(self):
        errors = validate_cutting_parameters(
            cutting_speed=100.0,
            feed_rate=0.3,
            depth_of_cut=0,
        )
        assert "切削深度必须大于0" in errors

    def test_depth_of_cut_negative(self):
        errors = validate_cutting_parameters(
            cutting_speed=100.0,
            feed_rate=0.3,
            depth_of_cut=-2.0,
        )
        assert "切削深度必须大于0" in errors

    def test_depth_of_cut_exceeds_max(self):
        errors = validate_cutting_parameters(
            cutting_speed=100.0,
            feed_rate=0.3,
            depth_of_cut=101,
        )
        assert "切削深度不能超过100 mm" in errors

    def test_depth_of_cut_at_max(self):
        errors = validate_cutting_parameters(
            cutting_speed=100.0,
            feed_rate=0.3,
            depth_of_cut=100,
        )
        assert errors == []

    def test_multiple_errors(self):
        errors = validate_cutting_parameters(
            cutting_speed=-100.0,
            feed_rate=-0.3,
            depth_of_cut=-2.0,
        )
        assert len(errors) == 3
        assert "切削速度必须大于0" in errors
        assert "进给量必须大于0" in errors
        assert "切削深度必须大于0" in errors

    def test_all_errors_at_once(self):
        errors = validate_cutting_parameters(
            cutting_speed=-1,
            feed_rate=-1,
            depth_of_cut=-1,
        )
        assert len(errors) == 3


class TestValidatePredictionHorizon:
    """Test prediction horizon validation"""

    def test_valid_horizon(self):
        errors = validate_prediction_horizon(horizon=3600.0)
        assert errors == []

    def test_horizon_at_minimum(self):
        errors = validate_prediction_horizon(horizon=0.001)
        assert errors == []

    def test_horizon_zero(self):
        errors = validate_prediction_horizon(horizon=0)
        assert "预测时长必须大于0" in errors

    def test_horizon_negative(self):
        errors = validate_prediction_horizon(horizon=-100)
        assert "预测时长必须大于0" in errors

    def test_horizon_exceeds_max(self):
        errors = validate_prediction_horizon(horizon=86401)
        assert "预测时长不能超过24小时" in errors

    def test_horizon_at_max_boundary(self):
        errors = validate_prediction_horizon(horizon=86400)
        assert errors == []

    def test_horizon_just_under_max(self):
        errors = validate_prediction_horizon(horizon=86399.99)
        assert errors == []

    def test_horizon_just_over_max(self):
        errors = validate_prediction_horizon(horizon=86400.01)
        assert "预测时长不能超过24小时" in errors


class TestValidateTrainingParams:
    """Test training parameter validation"""

    @pytest.fixture
    def temp_dataset_file(self, tmp_path):
        file_path = tmp_path / "test_dataset.csv"
        file_path.write_text("col1,col2\n1,2\n3,4")
        return str(file_path)

    def test_valid_params(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model_123",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert errors == []

    def test_empty_model_name(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "模型名称不能为空" in errors

    def test_whitespace_only_model_name(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="   ",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "模型名称不能为空" in errors

    def test_model_name_with_invalid_chars(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test@model!",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "模型名称只能包含字母、数字、下划线和连字符，长度1-64" in errors

    def test_model_name_with_space(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "模型名称只能包含字母、数字、下划线和连字符，长度1-64" in errors

    def test_model_name_with_dot(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test.model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "模型名称只能包含字母、数字、下划线和连字符，长度1-64" in errors

    def test_model_name_max_length(self, temp_dataset_file):
        long_name = "a" * 64
        errors = validate_training_params(
            model_name=long_name,
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert errors == []

    def test_model_name_exceeds_max_length(self, temp_dataset_file):
        long_name = "a" * 65
        errors = validate_training_params(
            model_name=long_name,
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "模型名称只能包含字母、数字、下划线和连字符，长度1-64" in errors

    def test_model_name_underscore(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model_v2",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert errors == []

    def test_model_name_hyphen(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test-model-v2",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert errors == []

    def test_model_name_numbers(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="model12345",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert errors == []

    def test_empty_dataset_path(self):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path="",
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "数据集路径不能为空" in errors

    def test_nonexistent_dataset_path(self):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path="/nonexistent/path/dataset.csv",
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert any("数据集路径不存在" in e for e in errors)

    def test_epochs_zero(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=0,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "训练轮数必须在1-10000之间" in errors

    def test_epochs_negative(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=-100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "训练轮数必须在1-10000之间" in errors

    def test_epochs_exceeds_max(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=10001,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "训练轮数必须在1-10000之间" in errors

    def test_epochs_at_min(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=1,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert errors == []

    def test_epochs_at_max(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=10000,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert errors == []

    def test_batch_size_zero(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=0,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "批量大小必须在1-512之间" in errors

    def test_batch_size_negative(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=-32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "批量大小必须在1-512之间" in errors

    def test_batch_size_exceeds_max(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=513,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert "批量大小必须在1-512之间" in errors

    def test_batch_size_at_min(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=1,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert errors == []

    def test_batch_size_at_max(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=512,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert errors == []

    def test_learning_rate_zero(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0,
            validation_split=0.2,
        )
        assert "学习率必须在0-1之间" in errors

    def test_learning_rate_negative(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=-0.001,
            validation_split=0.2,
        )
        assert "学习率必须在0-1之间" in errors

    def test_learning_rate_exceeds_max(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=1.1,
            validation_split=0.2,
        )
        assert "学习率必须在0-1之间" in errors

    def test_learning_rate_at_max(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=1.0,
            validation_split=0.2,
        )
        assert errors == []

    def test_learning_rate_at_zero_boundary(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.000001,
            validation_split=0.2,
        )
        assert errors == []

    def test_validation_split_below_range(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=-0.1,
        )
        assert "验证集比例必须在0.0-0.5之间" in errors

    def test_validation_split_above_range(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.6,
        )
        assert "验证集比例必须在0.0-0.5之间" in errors

    def test_validation_split_at_min(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.0,
        )
        assert errors == []

    def test_validation_split_at_max(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.5,
        )
        assert errors == []

    def test_invalid_model_type(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
            model_type="INVALID",
        )
        assert "不支持的模型类型: INVALID" in errors

    def test_valid_model_types(self, temp_dataset_file):
        for model_type in ["CFC", "LTC", "HYBRID", "cnn", "CNN"]:
            errors = validate_training_params(
                model_name="test_model",
                dataset_path=temp_dataset_file,
                epochs=100,
                batch_size=32,
                learning_rate=0.001,
                validation_split=0.2,
                model_type=model_type,
            )
            assert errors == [], f"Model type {model_type} should be valid"

    def test_default_model_type(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="test_model",
            dataset_path=temp_dataset_file,
            epochs=100,
            batch_size=32,
            learning_rate=0.001,
            validation_split=0.2,
        )
        assert errors == []

    def test_multiple_errors(self, temp_dataset_file):
        errors = validate_training_params(
            model_name="",
            dataset_path="/nonexistent",
            epochs=0,
            batch_size=0,
            learning_rate=-1,
            validation_split=1.0,
            model_type="INVALID",
        )
        assert len(errors) >= 4


class TestSanitizeString:
    """Test string sanitization"""

    def test_normal_string(self):
        result = sanitize_string("Hello World")
        assert result == "Hello World"

    def test_string_with_whitespace(self):
        result = sanitize_string("  Hello World  ")
        assert result == "Hello World"

    def test_string_with_leading_whitespace(self):
        result = sanitize_string("  Hello")
        assert result == "  Hello".strip()

    def test_string_with_trailing_whitespace(self):
        result = sanitize_string("Hello  ")
        assert result == "Hello  ".strip()

    def test_string_with_tabs_and_newlines(self):
        result = sanitize_string("  Hello\t\nWorld  ")
        assert result == "Hello\t\nWorld"

    def test_string_truncated_to_max_length(self):
        long_string = "a" * 300
        result = sanitize_string(long_string, max_length=256)
        assert len(result) == 256

    def test_string_at_max_length(self):
        exact_string = "a" * 256
        result = sanitize_string(exact_string, max_length=256)
        assert len(result) == 256

    def test_string_shorter_than_max_length(self):
        short_string = "Hello"
        result = sanitize_string(short_string, max_length=256)
        assert result == "Hello"

    def test_empty_string(self):
        result = sanitize_string("")
        assert result == ""

    def test_only_whitespace(self):
        result = sanitize_string("   \t\n  ")
        assert result == ""

    def test_default_max_length(self):
        result = sanitize_string("a" * 300)
        assert len(result) == 256

    def test_unicode_characters(self):
        result = sanitize_string("中文测试字符串")
        assert result == "中文测试字符串"

    def test_unicode_truncation(self):
        long_unicode = "中文" * 200
        result = sanitize_string(long_unicode, max_length=256)
        assert len(result) == 256


class TestValidateRagQuery:
    """Test RAG query validation"""

    def test_valid_query(self):
        errors = validate_rag_query("刀具磨损预测参数")
        assert errors == []

    def test_empty_query(self):
        errors = validate_rag_query("")
        assert "查询文本不能为空" in errors

    def test_whitespace_only_query(self):
        errors = validate_rag_query("   ")
        assert "查询文本不能为空" in errors

    def test_query_at_max_length(self):
        max_query = "a" * 2000
        errors = validate_rag_query(max_query)
        assert errors == []

    def test_query_exceeds_max_length(self):
        long_query = "a" * 2001
        errors = validate_rag_query(long_query)
        assert "查询文本不能超过2000个字符" in errors

    def test_query_just_under_max(self):
        query = "a" * 1999
        errors = validate_rag_query(query)
        assert errors == []

    def test_query_just_over_max(self):
        query = "a" * 2001
        errors = validate_rag_query(query)
        assert "查询文本不能超过2000个字符" in errors

    def test_unicode_query(self):
        errors = validate_rag_query("刀具磨损预测参数切削速度优化")
        assert errors == []


class TestValidateMaterialName:
    """Test material name validation"""

    def test_valid_material(self):
        errors = validate_material_name("45号钢")
        assert errors == []

    def test_empty_material(self):
        errors = validate_material_name("")
        assert "材料名称不能为空" in errors

    def test_whitespace_only_material(self):
        errors = validate_material_name("   ")
        assert "材料名称不能为空" in errors

    def test_material_at_max_length(self):
        max_material = "a" * 100
        errors = validate_material_name(max_material)
        assert errors == []

    def test_material_exceeds_max_length(self):
        long_material = "a" * 101
        errors = validate_material_name(long_material)
        assert "材料名称不能超过100个字符" in errors

    def test_material_just_under_max(self):
        material = "a" * 99
        errors = validate_material_name(material)
        assert errors == []

    def test_material_just_over_max(self):
        material = "a" * 101
        errors = validate_material_name(material)
        assert "材料名称不能超过100个字符" in errors


class TestValidateFilePath:
    """Test file path validation"""

    def test_valid_existing_file(self, tmp_path, monkeypatch):
        """合法路径下已存在文件应通过校验。"""
        monkeypatch.setenv("LNN_DATA_DIR", str(tmp_path))
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        errors = validate_file_path(str(test_file), must_exist=True)
        assert errors == []

    def test_nonexistent_file_with_must_exist(self, tmp_path, monkeypatch):
        """当路径合法（落在白名单内）但文件不存在时，必须返回 '文件路径不存在' 错误。"""
        monkeypatch.setenv("LNN_DATA_DIR", str(tmp_path))
        missing = tmp_path / "missing.txt"
        errors = validate_file_path(str(missing), must_exist=True)
        assert any("文件路径不存在" in e for e in errors)

    def test_nonexistent_file_without_must_exist(self, tmp_path, monkeypatch):
        """must_exist=False 时白名单内不存在的路径应通过（用于生成新文件路径）。"""
        monkeypatch.setenv("LNN_DATA_DIR", str(tmp_path))
        missing = tmp_path / "missing.txt"
        errors = validate_file_path(str(missing), must_exist=False)
        assert errors == []

    def test_empty_path(self):
        errors = validate_file_path("")
        assert "文件路径不能为空" in errors

    def test_none_path(self):
        errors = validate_file_path(None, must_exist=True)
        assert "文件路径不能为空" in errors

    def test_relative_path_existing(self, tmp_path, monkeypatch):
        """把 LNN_DATA_DIR 指向 tmp_path，模拟白名单放行的相对路径。"""
        monkeypatch.setenv("LNN_DATA_DIR", str(tmp_path))
        original_dir = os.getcwd()
        try:
            os.chdir(tmp_path)
            test_file = tmp_path / "test.txt"
            test_file.write_text("content")
            errors = validate_file_path("test.txt", must_exist=True)
            assert errors == []
        finally:
            os.chdir(original_dir)

    def test_path_normalization(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LNN_DATA_DIR", str(tmp_path))
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        normalized_path = os.path.normpath(str(test_file))
        errors = validate_file_path(normalized_path, must_exist=True)
        assert errors == []

    def test_path_traversal_rejected(self, tmp_path, monkeypatch):
        """路径遍历攻击应被白名单校验拒绝。"""
        monkeypatch.setenv("LNN_DATA_DIR", str(tmp_path))
        outside = tmp_path.parent / "outside_secret.txt"
        outside.write_text("secret")
        try:
            errors = validate_file_path(str(outside), must_exist=True)
            assert any("不在允许的访问范围内" in e for e in errors)
        finally:
            outside.unlink(missing_ok=True)

    def test_path_traversal_dotdot_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LNN_DATA_DIR", str(tmp_path))
        traversal = (
            str(tmp_path) + os.sep + ".." + os.sep + ".." +
            os.sep + "etc" + os.sep + "passwd"
        )
        errors = validate_file_path(traversal, must_exist=False)
        assert any("不在允许的访问范围内" in e for e in errors)

    def test_non_string_path(self):
        errors = validate_file_path(12345)  # type: ignore[arg-type]
        assert "文件路径类型不合法" in errors

    def test_custom_allowed_roots(self, tmp_path):
        """调用方可通过 allowed_roots 扩展白名单。"""
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        target = custom_dir / "data.bin"
        target.write_text("x")
        errors = validate_file_path(
            str(target),
            must_exist=True,
            allowed_roots=[str(custom_dir)],
        )
        assert errors == []


class TestCoalesce:
    """Test coalesce helper function"""

    def test_first_non_none(self):
        result = coalesce("first", "second", "third")
        assert result == "first"

    def test_second_non_none(self):
        result = coalesce(None, "second", "third")
        assert result == "second"

    def test_last_non_none(self):
        result = coalesce(None, None, "third")
        assert result == "third"

    def test_all_none(self):
        result = coalesce(None, None, None)
        assert result is None

    def test_with_zero(self):
        result = coalesce(0, "second")
        assert result == 0

    def test_with_empty_string(self):
        result = coalesce("", "second")
        assert result == ""

    def test_with_false(self):
        result = coalesce(False, "second")
        assert result is False

    def test_single_value(self):
        result = coalesce("only")
        assert result == "only"

    def test_single_none(self):
        result = coalesce(None)
        assert result is None

    def test_mixed_types(self):
        result = coalesce(None, 42, "string", [1, 2, 3])
        assert result == 42

    def test_integers(self):
        result = coalesce(None, 1, 2, 3)
        assert result == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
