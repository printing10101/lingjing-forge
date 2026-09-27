"""data/pipeline/preprocessors 覆盖率补强测试。

覆盖 5 类预处理器（图像/时序/文本/刀具状态/G代码）的
preprocess / validate / 内部辅助方法全部分支。
"""

from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.unit

from app.data.pipeline.config import (
    GCodeProcessorConfig,
    ImageProcessorConfig,
    TextProcessorConfig,
    TimeSeriesProcessorConfig,
    ToolStateProcessorConfig,
)
from app.data.pipeline.datatypes import (
    DataSourceType,
    GCodeInput,
    ImageInput,
    ProcessedData,
    TextInput,
    TimeSeriesInput,
    ToolStateInput,
)
from app.data.pipeline.preprocessors import (
    BasePreprocessor,
    GCodePreprocessor,
    ImagePreprocessor,
    TextPreprocessor,
    TimeSeriesPreprocessor,
    ToolStatePreprocessor,
)


class TestBasePreprocessor:
    class _Concrete(BasePreprocessor):
        def preprocess(self, raw_input):
            return ProcessedData(
                source_type=DataSourceType.UNKNOWN,
                original_data=None,
                processed_data=np.array([]),
            )

    def test_default_validate(self):
        p = self._Concrete(config=None)
        assert p.is_fitted is False
        m = p.validate(
            ProcessedData(
                source_type=DataSourceType.UNKNOWN,
                original_data=None,
                processed_data=np.array([]),
            )
        )
        assert m.validation_errors == []


# ---------------------------------------------------------------- ImagePreprocessor


class TestImagePreprocessor:
    def _make(self, **kw):
        cfg = ImageProcessorConfig(image_size=kw.pop("image_size", 32), **kw)
        return ImagePreprocessor(cfg)

    def test_2d_gray_to_rgb(self):
        p = self._make()
        img = np.random.randint(0, 256, (16, 16), dtype=np.uint8)
        out = p.preprocess(ImageInput(data=img)).processed_data
        assert out.ndim == 3 and out.shape[-1] == 3
        assert out.dtype == np.float32

    def test_chw_to_hwc(self):
        p = self._make()
        img = np.random.randint(0, 256, (3, 16, 16), dtype=np.uint8)
        out = p.preprocess(ImageInput(data=img)).processed_data
        assert out.shape == (32, 32, 3)

    def test_single_channel_repeat(self):
        p = self._make()
        img = np.random.randint(0, 256, (16, 16, 1), dtype=np.uint8)
        out = p.preprocess(ImageInput(data=img)).processed_data
        assert out.shape[-1] == 3

    def test_four_channel_drop_alpha(self):
        p = self._make()
        img = np.random.randint(0, 256, (16, 16, 4), dtype=np.uint8)
        out = p.preprocess(ImageInput(data=img)).processed_data
        assert out.shape[-1] == 3

    def test_16bit_normalize(self):
        p = self._make()
        img = np.random.randint(0, 65535, (16, 16, 3), dtype=np.uint16)
        out = p.preprocess(ImageInput(data=img, bit_depth=16)).processed_data
        assert out.max() <= 1.0 and out.min() >= 0.0

    def test_unsupported_bit_depth(self):
        p = self._make()
        img = np.zeros((16, 16, 3), dtype=np.uint8)
        with pytest.raises(ValueError, match="位深度"):
            p.preprocess(ImageInput(data=img, bit_depth=12))

    def test_zero_size_image(self):
        p = self._make()
        img = np.zeros((0, 16, 3), dtype=np.uint8)
        with pytest.raises(ValueError, match="尺寸无效"):
            p.preprocess(ImageInput(data=img))

    def test_metadata_shape(self):
        p = self._make()
        img = np.random.randint(0, 256, (16, 16, 3), dtype=np.uint8)
        pd = p.preprocess(ImageInput(data=img))
        assert pd.metadata["original_shape"] == (16, 16, 3)
        assert pd.metadata["processed_shape"] == (32, 32, 3)
        assert pd.feature_dim == 32 * 32 * 3

    def test_validate_ok(self):
        p = self._make()
        img = np.zeros((32, 32, 3), dtype=np.float32)
        pd = ProcessedData(
            source_type=DataSourceType.IMAGE, original_data=img, processed_data=img, feature_dim=32 * 32 * 3
        )
        m = p.validate(pd)
        assert m.validation_errors == []

    def test_validate_out_of_range_and_nan(self):
        p = self._make()
        # 注意：含 NaN 的数组 min()/max() 均为 NaN，比较恒 False，须分开构造
        out_of_range = np.full((32, 32, 3), 2.0, dtype=np.float32)
        m1 = p.validate(
            ProcessedData(
                source_type=DataSourceType.IMAGE,
                original_data=out_of_range,
                processed_data=out_of_range,
                feature_dim=out_of_range.size,
            )
        )
        assert any("范围" in e for e in m1.validation_errors)
        assert m1.consistency == 0.8

        with_nan = np.zeros((32, 32, 3), dtype=np.float32)
        with_nan[0, 0, 0] = np.nan
        m2 = p.validate(
            ProcessedData(
                source_type=DataSourceType.IMAGE,
                original_data=with_nan,
                processed_data=with_nan,
                feature_dim=with_nan.size,
            )
        )
        assert any("NaN" in e for e in m2.validation_errors)
        assert m2.completeness == 0.0


# ---------------------------------------------------------------- TimeSeriesPreprocessor


class TestTimeSeriesPreprocessor:
    def _make(self, **kw):
        cfg = TimeSeriesProcessorConfig(
            window_size=kw.pop("window_size", 32),
            overlap_ratio=kw.pop("overlap_ratio", 0.5),
            sample_rate=kw.pop("sample_rate", 1000.0),
            denoising_algorithm=kw.pop("denoising_algorithm", "none"),
            **kw,
        )
        return TimeSeriesPreprocessor(cfg)

    def test_1d_reshape_and_windows(self):
        p = self._make(denoising_algorithm="none")
        data = np.arange(100, dtype=np.float32)
        pd = p.preprocess(TimeSeriesInput(data=data))
        assert pd.processed_data.ndim == 3
        assert pd.processed_data.shape[1] == 32
        assert pd.metadata["n_windows"] == 5  # (100-32)//16+1

    def test_short_data_padding(self):
        p = self._make(denoising_algorithm="none")
        data = np.arange(10, dtype=np.float32)
        pd = p.preprocess(TimeSeriesInput(data=data))
        assert pd.processed_data.shape[1] == 32
        assert pd.metadata["n_windows"] == 1

    def test_moving_average(self):
        p = self._make(denoising_algorithm="moving_average")
        data = np.arange(100, dtype=np.float32)
        pd = p.preprocess(TimeSeriesInput(data=data))
        assert pd.processed_data.shape[0] > 0

    def test_butterworth_filter(self):
        p = self._make(denoising_algorithm="butterworth")
        data = np.sin(np.linspace(0, 10, 200)) + 0.01 * np.random.randn(200)
        pd = p.preprocess(TimeSeriesInput(data=data))
        assert pd.processed_data.shape[0] > 0

    def test_butterworth_import_error_degrade(self, monkeypatch):
        p = self._make(denoising_algorithm="butterworth")
        import scipy.signal

        monkeypatch.delattr(scipy.signal, "butter", raising=False)
        p._filter_coeff = None
        p._design_butterworth_filter()
        assert p._filter_coeff is None

    def test_non_butterworth_design_skipped(self):
        p = self._make(denoising_algorithm="moving_average")
        p._design_butterworth_filter()
        assert p._filter_coeff is None

    def test_validate_nan_inf(self):
        p = self._make(denoising_algorithm="none")
        data = np.array([[1.0], [np.nan], [np.inf]], dtype=np.float32)
        pd = ProcessedData(
            source_type=DataSourceType.TIME_SERIES,
            original_data=data,
            processed_data=data,
            feature_dim=1,
        )
        m = p.validate(pd)
        assert m.completeness == 0.0
        assert any("NaN" in e for e in m.validation_errors)
        assert any("Inf" in e for e in m.validation_errors)


# ---------------------------------------------------------------- TextPreprocessor


class TestTextPreprocessor:
    def _make(self, **kw):
        cfg = TextProcessorConfig(
            max_text_length=kw.pop("max_text_length", 512),
            clean_special_chars=kw.pop("clean_special_chars", True),
            normalize_whitespace=kw.pop("normalize_whitespace", True),
            **kw,
        )
        return TextPreprocessor(cfg)

    def test_clean_whitespace(self):
        p = self._make()
        text = "  多  空格\t和\n换行  "
        assert p._clean_text(text) == "多 空格 和 换行"

    def test_clean_special_chars(self):
        p = self._make()
        text = "a\x00b\x1fc"
        # \x1f 是 Unicode 分隔符，先被 \s+ 规范化为空格；\x00 被删除
        assert p._clean_text(text) == "ab c"

    def test_truncate(self):
        p = self._make(max_text_length=5)
        assert p._clean_text("abcdefgh") == "abcde"

    def test_preprocess_str(self):
        p = self._make()
        pd = p.preprocess(TextInput(data="工艺 参数"))
        assert pd.feature_dim == 512
        assert pd.processed_data.dtype == np.float32
        assert pd.metadata["original_length"] == 5

    def test_preprocess_dict(self):
        p = self._make()
        pd = p.preprocess(TextInput(data={"材料": "铝合金"}, text_format="json"))
        assert pd.metadata["text_format"] == "json"
        assert pd.metadata["original_length"] > 0

    def test_preprocess_none_raises(self):
        p = self._make()
        with pytest.raises(ValueError, match="None"):
            p.preprocess(TextInput(data=None))

    def test_validate_empty(self):
        p = self._make()
        pd = ProcessedData(
            source_type=DataSourceType.TEXT, original_data="", processed_data=np.array([]), feature_dim=512
        )
        m = p.validate(pd)
        assert m.completeness == 0.0
        assert any("为空" in e for e in m.validation_errors)


# ---------------------------------------------------------------- ToolStatePreprocessor


class TestToolStatePreprocessor:
    def _make(self, **kw):
        cfg = ToolStateProcessorConfig(
            state_fields=kw.pop("state_fields", ["load", "wear", "temp"]),
            encoding_method=kw.pop("encoding_method", "one_hot"),
            anomaly_detection_method=kw.pop("anomaly_detection_method", "iqr"),
            anomaly_threshold=kw.pop("anomaly_threshold", 3.0),
            **kw,
        )
        return ToolStatePreprocessor(cfg)

    def test_normalize_types(self):
        p = self._make()
        data = {"load": 10.0, "wear": "20", "temp": "abc", "extra": 99.0}
        p._fit_min_max(data)
        feats = p._normalize(data)
        assert feats.shape == (3,)
        assert 0.0 <= feats[0] <= 1.0
        assert feats[2] == 0.0  # 非数值 → 0.0

    def test_preprocess_one_hot(self):
        p = self._make()
        data = {"load": 5.0, "wear": 3.0, "temp": 80.0}
        pd = p.preprocess(ToolStateInput(data=data))
        assert pd.processed_data.shape[0] == 6  # 3 特征 + 3 二值
        assert pd.feature_dim == 32  # config.tool_state_dim，非实际维数
        assert pd.anomaly_count >= 0

    def test_preprocess_no_one_hot(self):
        p = self._make(encoding_method="none")
        data = {"load": 5.0, "wear": 3.0, "temp": 80.0}
        pd = p.preprocess(ToolStateInput(data=data))
        assert pd.processed_data.shape[0] == 3

    def test_anomaly_zscore(self):
        # 归一化后 load=0.5 / wear=0.1 / temp=0.2 z 值约 1.12，阈值 0.5 触发
        p = self._make(anomaly_detection_method="z_score", anomaly_threshold=0.5)
        data = {"load": 100.0, "wear": 0.1, "temp": 0.2}
        pd = p.preprocess(ToolStateInput(data=data))
        assert pd.anomaly_count >= 1  # 100 是明显离群

    def test_validate_outlier(self):
        p = self._make(anomaly_detection_method="z_score", anomaly_threshold=0.5)
        # 归一化后 load=0.5 / wear≈0.017 / temp≈0.001 load 明显离群
        data = {"load": 100.0, "wear": 0.1, "temp": 0.2}
        pd = p.preprocess(ToolStateInput(data=data))
        m = p.validate(pd)
        assert any("异常" in e for e in m.validation_errors)
        assert m.outlier_ratio >= 0


# ---------------------------------------------------------------- GCodePreprocessor


class TestGCodePreprocessor:
    def _make(self, **kw):
        cfg = GCodeProcessorConfig(
            max_instructions_per_segment=kw.pop("max_instructions_per_segment", 500),
            segment_by_operation=kw.pop("segment_by_operation", True),
            **kw,
        )
        return GCodePreprocessor(cfg)

    GCODE = """(comment line)
G90 G21
G00 X10 Y20
M06 T1
G01 X30 Y40 F100
M30
"""

    def test_parse_instructions(self):
        p = self._make()
        insts = p._parse_instructions(self.GCODE)
        assert len(insts) == 5
        assert insts[0]["line"] == 2
        assert "G90" in insts[0]["tokens"]

    def test_parse_comment_and_blank(self):
        p = self._make()
        insts = p._parse_instructions("; header\n\n(\nG01 X1\n")
        assert len(insts) == 1
        assert insts[0]["tokens"] == ["G01", "X1"]

    def test_segment_by_operation(self):
        p = self._make()
        insts = p._parse_instructions(self.GCODE)
        segs = p._segment_by_operation(insts)
        assert len(segs) >= 3  # rapid / (M06) / linear / end

    def test_segment_no_marker(self):
        p = self._make()
        insts = p._parse_instructions("X10 Y20\nX30 Y40\n")
        segs = p._segment_by_operation(insts)
        assert len(segs) == 1

    def test_segment_empty_fallback(self):
        p = self._make()
        segs = p._segment_by_operation([])
        assert segs == [[]] or segs == []

    def test_encode_instructions(self):
        p = self._make()
        insts = p._parse_instructions("G01 X10.5 Y-2\n")
        enc = p._encode_instructions(insts)
        assert enc.shape == (1, 21)
        assert enc[0, 0] == 1.0  # G
        assert enc[0, 5] == 10.5  # X
        assert enc[0, 6] == -2.0  # Y

    def test_encode_invalid_number_defaults(self, caplog):
        p = self._make()
        insts = [{"line": 1, "tokens": ["X1.2.3"], "text": "X1.2.3"}]
        enc = p._encode_instructions(insts)
        assert enc[0, 5] == 0.0

    def test_preprocess_unsupported_controller(self):
        p = self._make()
        with pytest.raises(ValueError, match="控制器"):
            p.preprocess(GCodeInput(data="G01 X1", controller_type="FANUC-NOPE"))

    def test_preprocess_segments(self):
        p = self._make()
        pd = p.preprocess(GCodeInput(data=self.GCODE, controller_type="fanuc"))
        assert pd.metadata["n_instructions"] == 5
        assert pd.metadata["n_segments"] >= 1
        assert pd.processed_data.shape[1] == 21

    def test_preprocess_no_segment(self):
        p = self._make(segment_by_operation=False)
        pd = p.preprocess(GCodeInput(data=self.GCODE, controller_type="fanuc"))
        assert pd.metadata["n_segments"] == 1

    def test_preprocess_empty_gcode(self):
        p = self._make()
        pd = p.preprocess(GCodeInput(data="", controller_type="fanuc"))
        assert pd.processed_data.shape == (0, 21)
        assert pd.metadata["n_instructions"] == 0

    def test_preprocess_truncate_segment(self):
        p = self._make(max_instructions_per_segment=2)
        pd = p.preprocess(GCodeInput(data=self.GCODE, controller_type="fanuc"))
        assert pd.metadata["n_instructions"] == 5  # 解析数不变

    def test_validate_empty_parse(self):
        p = self._make()
        pd = p.preprocess(GCodeInput(data="", controller_type="fanuc"))
        m = p.validate(pd)
        assert any("有效指令" in e for e in m.validation_errors)
        assert any("解析后为空" in e for e in m.validation_errors)
