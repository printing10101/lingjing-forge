"""
数据完整性测试套件

测试范围：对100个随机样本（覆盖所有数据类型）进行全流程验证
验收标准：
  - 字段缺失率 < 0.1%
  - 特征维度一致性100%
  - 数据值范围符合预期
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.data.pipeline import (  # noqa: E402
    DataPipeline,
    get_default_config,
    DataSourceType,
    ImageInput,
    TimeSeriesInput,
    TextInput,
    ToolStateInput,
    GCodeInput,
    DataValidator,
    QualityChecker,
)


@pytest.fixture
def pipeline():
    config = get_default_config()
    return DataPipeline(config, device="cpu")


@pytest.fixture
def validator():
    config = get_default_config()
    return DataValidator(config)


@pytest.fixture
def quality_checker():
    config = get_default_config()
    return QualityChecker(config)


class TestImageIntegrity:
    """图像数据完整性测试"""

    @pytest.mark.unit
    def test_image_preprocessing_completeness(self, pipeline):
        """测试图像预处理数据完整性"""
        for _ in range(20):
            img = np.random.randint(0, 256, (128, 128, 3), dtype=np.uint8)
            raw = ImageInput(data=img, bit_depth=8, source_id="test_img")
            processed = pipeline.preprocess(raw)

            assert processed.processed_data is not None
            assert processed.processed_data.size > 0
            assert processed.processed_data.shape == (256, 256, 3)
            assert processed.source_type == DataSourceType.IMAGE
            assert processed.processing_time_ms >= 0

    @pytest.mark.unit
    def test_image_dimension_consistency(self, pipeline, validator):
        """测试图像特征维度一致性"""
        img = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
        raw = ImageInput(data=img, bit_depth=8, source_id="test_dim")
        processed = pipeline.preprocess(raw)

        expected_dim = 256 * 256 * 3
        metrics = validator.validate_dimension_consistency(processed, expected_dim)
        assert metrics.dim_consistency, f"维度不一致: {metrics.feature_dim_actual} vs {metrics.feature_dim_expected}"

    @pytest.mark.unit
    def test_image_value_range(self, pipeline, validator):
        """测试图像值范围"""
        img = np.random.randint(0, 256, (64, 64, 3), dtype=np.uint8)
        raw = ImageInput(data=img, bit_depth=8, source_id="test_range")
        processed = pipeline.preprocess(raw)

        metrics = validator.validate_value_range(processed, 0.0, 1.0)
        assert len(metrics.validation_errors) == 0, f"值范围异常: {metrics.validation_errors}"

    @pytest.mark.unit
    def test_image_16bit_support(self, pipeline):
        """测试16位深度图像"""
        img = np.random.randint(0, 65536, (64, 64, 3), dtype=np.uint16)
        raw = ImageInput(data=img, bit_depth=16, source_id="test_16bit")
        processed = pipeline.preprocess(raw)

        assert processed.processed_data.shape == (256, 256, 3)
        assert processed.metadata["bit_depth"] == 16

    @pytest.mark.unit
    def test_image_grayscale_support(self, pipeline):
        """测试单通道灰度图"""
        img = np.random.randint(0, 256, (64, 64), dtype=np.uint8)
        raw = ImageInput(data=img, bit_depth=8, channels=1, source_id="test_gray")
        processed = pipeline.preprocess(raw)

        assert processed.processed_data.shape == (256, 256, 3)


class TestTimeSeriesIntegrity:
    """时序数据完整性测试"""

    @pytest.mark.unit
    def test_ts_sliding_window_completeness(self, pipeline):
        """测试滑动窗口数据完整性"""
        for _ in range(20):
            ts = np.random.randn(1000, 3).astype(np.float32)
            raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=3, source_id="test_ts")
            processed = pipeline.preprocess(raw)

            assert processed.processed_data is not None
            assert processed.processed_data.size > 0
            assert processed.processed_data.ndim == 3
            assert processed.processed_data.shape[1] == 256
            assert processed.source_type == DataSourceType.TIME_SERIES

    @pytest.mark.unit
    def test_ts_no_nan_values(self, pipeline, validator):
        """测试时序数据无NaN"""
        ts = np.random.randn(500, 2).astype(np.float32)
        raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=2, source_id="test_ts_nan")
        processed = pipeline.preprocess(raw)

        metrics = validator.validate_completeness(processed)
        assert metrics.completeness >= 0.999, f"完整性不足: {metrics.completeness}"
        assert metrics.missing_ratio < 0.001, f"缺失率过高: {metrics.missing_ratio}"

    @pytest.mark.unit
    def test_ts_window_overlap(self, pipeline):
        """测试窗口重叠比例"""
        ts = np.random.randn(1000, 1).astype(np.float32)
        raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=1, source_id="overlap")
        processed = pipeline.preprocess(raw)

        _ = processed.metadata["n_windows"]
        expected_overlap = processed.metadata["overlap_ratio"]
        step = processed.metadata["step"]
        assert step == 128, f"步长应为128: {step}"
        assert expected_overlap == 0.5, f"重叠比例应为0.5: {expected_overlap}"

    @pytest.mark.unit
    def test_ts_short_data_padding(self, pipeline):
        """测试短数据自动填充"""
        ts = np.random.randn(100, 1).astype(np.float32)
        raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=1, source_id="short")
        processed = pipeline.preprocess(raw)

        assert processed.processed_data.shape[1] == 256

    @pytest.mark.unit
    def test_ts_dimension_consistency(self, pipeline, validator):
        """测试时序特征维度一致性"""
        ts = np.random.randn(500, 2).astype(np.float32)
        raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=2, source_id="dim")
        processed = pipeline.preprocess(raw)

        expected_dim = 256 * 2
        metrics = validator.validate_dimension_consistency(processed, expected_dim)
        assert metrics.dim_consistency


class TestTextIntegrity:
    """文本数据完整性测试"""

    @pytest.mark.unit
    def test_text_json_completeness(self, pipeline):
        """测试JSON文本处理"""
        for _ in range(20):
            text_data = {
                "process": "CNC milling",
                "material": "Aluminum 6061",
                "precision": "IT7",
                "notes": "高速切削加工工艺指导",
            }
            raw = TextInput(data=text_data, text_format="json", source_id="test_text")
            processed = pipeline.preprocess(raw)

            assert processed.processed_data is not None
            assert processed.source_type == DataSourceType.TEXT
            assert processed.feature_dim == 512

    @pytest.mark.unit
    def test_text_plain_cleaning(self, pipeline):
        """测试纯文本清洗"""
        text = "  工艺参数:\t切削速度=200  \n\n进给量=0.2  \x00\x01"
        raw = TextInput(data=text, text_format="plain", source_id="clean")
        processed = pipeline.preprocess(raw)

        cleaned_len = processed.metadata["cleaned_length"]
        assert cleaned_len > 0
        assert cleaned_len < len(text)

    @pytest.mark.unit
    def test_text_dimension_consistency(self, pipeline, validator):
        """测试文本特征维度一致性"""
        raw = TextInput(data="工艺参数测试", text_format="plain", source_id="dim")
        processed = pipeline.preprocess(raw)

        metrics = validator.validate_dimension_consistency(processed, 512)
        assert metrics.dim_consistency

    @pytest.mark.unit
    def test_text_long_truncation(self, pipeline):
        """测试长文本截断"""
        long_text = "加工参数" * 300
        raw = TextInput(data=long_text, text_format="plain", source_id="long")
        processed = pipeline.preprocess(raw)

        assert processed.metadata["cleaned_length"] <= 512


class TestToolStateIntegrity:
    """刀具状态数据完整性测试"""

    @pytest.mark.unit
    def test_tool_state_normalization(self, pipeline):
        """测试刀具状态归一化"""
        for _ in range(20):
            state = {
                "wear_level": 0.5,
                "cutting_time": 120.0,
                "tool_life_remaining": 80.0,
                "spindle_load": 60.0,
                "temperature": 45.0,
                "vibration_amplitude": 0.3,
                "cutting_force_x": 150.0,
                "cutting_force_y": 120.0,
                "cutting_force_z": 80.0,
            }
            raw = ToolStateInput(data=state, source_id="test_tool")
            processed = pipeline.preprocess(raw)

            assert processed.processed_data is not None
            assert processed.source_type == DataSourceType.TOOL_STATE
            assert np.all(processed.processed_data >= 0.0)
            assert np.all(processed.processed_data <= 1.0)

    @pytest.mark.unit
    def test_tool_state_anomaly_detection(self, pipeline):
        """测试异常值检测"""
        state = {
            "wear_level": 999.0,
            "cutting_time": 120.0,
            "tool_life_remaining": 80.0,
            "spindle_load": 60.0,
            "temperature": 45.0,
            "vibration_amplitude": 0.3,
            "cutting_force_x": 150.0,
            "cutting_force_y": 120.0,
            "cutting_force_z": 80.0,
        }
        raw = ToolStateInput(data=state, source_id="anomaly")
        processed = pipeline.preprocess(raw)

        assert processed.anomaly_count > 0

    @pytest.mark.unit
    def test_tool_state_missing_fields(self, pipeline):
        """测试缺失字段处理"""
        state = {"wear_level": 0.5, "cutting_time": 120.0}
        raw = ToolStateInput(data=state, source_id="missing")
        processed = pipeline.preprocess(raw)

        assert processed.processed_data is not None
        assert processed.processed_data.size > 0


class TestGCodeIntegrity:
    """G代码数据完整性测试"""

    @pytest.mark.unit
    def test_gcode_parsing(self, pipeline):
        """测试G代码解析"""
        for _ in range(20):
            gcode = """%
O0001 (TEST)
G21 G17 G40 G49 G80 G90 G94
G00 G91 G28 Z0.
G00 G90 G54 X0. Y0.
G00 G43 Z50.000 H00
M03 S8000
G01 X10.000 Y10.000 F500.000
G01 X20.000 Y20.000 F500.000
M05
G00 G91 G28 Z0.
M30
%"""
            raw = GCodeInput(data=gcode, controller_type="fanuc", source_id="test_gcode")
            processed = pipeline.preprocess(raw)

            assert processed.processed_data is not None
            assert processed.source_type == DataSourceType.GCODE
            assert processed.metadata["n_instructions"] > 0

    @pytest.mark.unit
    def test_gcode_segmentation(self, pipeline):
        """测试G代码语义分割"""
        gcode = """%
G21 G90 G94
G00 X0. Y0.
M03 S8000
G01 X10. F500.
M05
G00 X20. Y20.
M03 S6000
G01 X30. F300.
M30
%"""
        raw = GCodeInput(data=gcode, controller_type="fanuc", source_id="segment")
        processed = pipeline.preprocess(raw)

        assert processed.metadata["n_segments"] > 0
        assert processed.metadata["segment_by_operation"] is True

    @pytest.mark.unit
    def test_gcode_dimension_consistency(self, pipeline, validator):
        """测试G代码特征维度"""
        gcode = "G01 X10. Y10. F500."
        raw = GCodeInput(data=gcode, controller_type="fanuc", source_id="dim")
        processed = pipeline.preprocess(raw)

        metrics = validator.validate_dimension_consistency(processed, 256)
        assert metrics.dim_consistency


class TestFullPipelineIntegrity:
    """全管道完整性测试"""

    @pytest.mark.unit
    def test_full_pipeline_all_modalities(self, pipeline):
        """测试所有模态全流程处理

        该测试包含文本模态，需要 sentence_transformers（依赖 torch）。
        遵循 [S7] 硬约束：torch 不可用时跳过，而非用桩模块伪装通过。
        """
        pytest.importorskip("sentence_transformers")
        # 图像模态走 torchvision ResNet（且需要下载预训练权重）——缺失时
        # CNN 随机权重降级，完整性断言失效（2026-09 存量失败治理）
        pytest.importorskip("torchvision", reason="图像模态完整性需要 torchvision + 预训练权重")
        inputs = {
            "image": ImageInput(
                data=np.random.randint(0, 256, (128, 128, 3), dtype=np.uint8),
                bit_depth=8, source_id="full_img",
            ),
            "time_series": TimeSeriesInput(
                data=np.random.randn(1000, 2).astype(np.float32),
                sample_rate=1000.0, channels=2, source_id="full_ts",
            ),
            "text": TextInput(
                data={"process": "CNC milling", "material": "Al6061"},
                text_format="json", source_id="full_text",
            ),
            "tool_state": ToolStateInput(
                data={
                    "wear_level": 0.3, "cutting_time": 50.0,
                    "tool_life_remaining": 70.0, "spindle_load": 45.0,
                    "temperature": 35.0, "vibration_amplitude": 0.2,
                    "cutting_force_x": 100.0, "cutting_force_y": 80.0,
                    "cutting_force_z": 60.0,
                }, source_id="full_tool",
            ),
            "gcode": GCodeInput(
                data="G01 X10. Y10. F500.\nG02 X20. Y20. R5.\nM30",
                controller_type="fanuc", source_id="full_gcode",
            ),
        }

        # 无 torch 时融合器随机权重降级（官方警告：结果不具备物理意义），
        # 完整性断言无从谈起——诚实 skip 而非伪失败
        pytest.importorskip("torch", reason="完整管线完整性断言需要真实模型权重（torch）")

        result = pipeline.process(inputs)
        assert result.success
        assert result.fused_features is not None
        assert result.fused_features.size > 0
        assert result.total_processing_time_ms > 0
        assert len(result.individual_features) > 0

    @pytest.mark.unit
    def test_100_samples_integrity(self, pipeline):
        """测试100个随机样本全流程验证

        部分样本包含文本模态，需要 sentence_transformers（依赖 torch）。
        遵循 [S7] 硬约束：torch 不可用时跳过，而非用桩模块伪装通过。
        """
        pytest.importorskip("sentence_transformers")
        n_samples = 100
        errors = 0
        dim_errors = 0
        missing_fields = 0

        for i in range(n_samples):
            try:
                inputs = {}
                if i % 5 == 0 or i % 5 == 1:
                    inputs["image"] = ImageInput(
                        data=np.random.randint(0, 256, (64 + i % 100, 64 + i % 100, 3), dtype=np.uint8),
                        bit_depth=8, source_id=f"img_{i}",
                    )
                if i % 5 == 0 or i % 5 == 2:
                    inputs["time_series"] = TimeSeriesInput(
                        data=np.random.randn(300 + i % 800, 2).astype(np.float32),
                        sample_rate=1000.0, channels=2, source_id=f"ts_{i}",
                    )
                if i % 5 == 0 or i % 5 == 3:
                    inputs["text"] = TextInput(
                        data={"process": f"test_{i}"},
                        text_format="json", source_id=f"text_{i}",
                    )
                if i % 5 == 0 or i % 5 == 4:
                    inputs["tool_state"] = ToolStateInput(
                        data={"wear_level": float(i % 100) / 100, "cutting_time": float(i)},
                        source_id=f"tool_{i}",
                    )

                if not inputs:
                    continue

                result = pipeline.process(inputs)

                if not result.success:
                    errors += 1

                for modality, metrics in result.quality_metrics.items():
                    if not metrics.dim_consistency:
                        dim_errors += 1
                    if metrics.missing_ratio >= 0.001:
                        missing_fields += 1

            except Exception:
                errors += 1

        error_rate = errors / n_samples
        missing_rate = missing_fields / max(n_samples, 1)
        dim_consistency_rate = 1 - (dim_errors / max(n_samples, 1))

        # 无 torch/torchvision 时错误率必然超标（随机权重降级）——诚实 skip
        pytest.importorskip("torch", reason="错误率断言需要真实模型权重（torch）")
        pytest.importorskip("torchvision", reason="错误率断言需要真实模型权重（torchvision）")

        assert error_rate < 0.01, f"错误率过高: {error_rate}"
        assert missing_rate < 0.001, f"字段缺失率过高: {missing_rate}"
        assert dim_consistency_rate >= 1.0, f"特征维度一致性不足: {dim_consistency_rate}"

    @pytest.mark.unit
    def test_data_type_coverage(self, pipeline):
        """验证所有数据类型覆盖"""
        source_types = set()

        for i, st in enumerate([DataSourceType.IMAGE, DataSourceType.TIME_SERIES,
                                DataSourceType.TEXT, DataSourceType.TOOL_STATE,
                                DataSourceType.GCODE]):
            processed = None
            if st == DataSourceType.IMAGE:
                raw = ImageInput(data=np.random.randint(0, 256, (64, 64, 3), dtype=np.uint8), bit_depth=8)
                processed = pipeline.preprocess(raw)
            elif st == DataSourceType.TIME_SERIES:
                raw = TimeSeriesInput(data=np.random.randn(500, 1).astype(np.float32), sample_rate=1000.0)
                processed = pipeline.preprocess(raw)
            elif st == DataSourceType.TEXT:
                raw = TextInput(data={"key": "value"}, text_format="json")
                processed = pipeline.preprocess(raw)
            elif st == DataSourceType.TOOL_STATE:
                raw = ToolStateInput(data={"wear_level": 0.5})
                processed = pipeline.preprocess(raw)
            elif st == DataSourceType.GCODE:
                raw = GCodeInput(data="G01 X10. F500.", controller_type="fanuc")
                processed = pipeline.preprocess(raw)

            if processed is not None:
                source_types.add(processed.source_type)

        assert len(source_types) == 5, f"数据类型覆盖不足: {len(source_types)}/5"
