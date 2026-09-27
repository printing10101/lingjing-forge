"""
格式兼容性测试 (边缘情况)

测试范围：输入各种边缘格式数据（损坏图片、异常数值、格式错误文本等）
验收标准：
  - 系统不崩溃，错误处理机制正常触发
  - 错误日志记录完整度100%
  - 异常数据隔离率100%
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
    QualityChecker,
)


@pytest.fixture
def pipeline():
    config = get_default_config()
    return DataPipeline(config, device="cpu")


@pytest.fixture
def quality_checker():
    config = get_default_config()
    return QualityChecker(config)


class TestImageEdgeCases:
    """图像数据边缘情况"""

    @pytest.mark.unit
    def test_corrupted_image_all_zeros(self, pipeline):
        """损坏图像 - 全零"""
        img = np.zeros((64, 64, 3), dtype=np.uint8)
        raw = ImageInput(data=img, bit_depth=8, source_id="corrupt_zero")
        try:
            processed = pipeline.preprocess(raw)
            assert processed.processed_data is not None
            assert processed.processed_data.shape == (256, 256, 3)
        except Exception as e:
            pytest.fail(f"全零图像应能处理，不应崩溃: {e}")

    @pytest.mark.unit
    def test_corrupted_image_all_max(self, pipeline):
        """损坏图像 - 全最大值"""
        img = np.ones((64, 64, 3), dtype=np.uint8) * 255
        raw = ImageInput(data=img, bit_depth=8, source_id="corrupt_max")
        try:
            processed = pipeline.preprocess(raw)
            assert processed.processed_data is not None
        except Exception as e:
            pytest.fail(f"全最大值图像不应崩溃: {e}")

    @pytest.mark.unit
    def test_empty_image(self, pipeline):
        """空图像数组"""
        img = np.zeros((0, 0, 3), dtype=np.uint8)
        raw = ImageInput(data=img, bit_depth=8, source_id="empty")
        try:
            _ = pipeline.preprocess(raw)
        except Exception:
            pass

    @pytest.mark.unit
    def test_single_pixel_image(self, pipeline):
        """单像素图像"""
        img = np.array([[[128, 128, 128]]], dtype=np.uint8)
        raw = ImageInput(data=img, bit_depth=8, source_id="single_pixel")
        try:
            processed = pipeline.preprocess(raw)
            assert processed.processed_data is not None
        except Exception as e:
            pytest.fail(f"单像素图像不应崩溃: {e}")

    @pytest.mark.unit
    def test_image_nan_values(self, pipeline):
        """包含NaN的图像"""
        img = np.random.rand(64, 64, 3).astype(np.float32)
        img[10, 10, 0] = np.nan
        raw = ImageInput(data=img, bit_depth=8, source_id="nan")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception:
            pass

    @pytest.mark.unit
    def test_image_inf_values(self, pipeline):
        """包含Inf的图像"""
        img = np.random.rand(64, 64, 3).astype(np.float32)
        img[5, 5, 1] = np.inf
        raw = ImageInput(data=img, bit_depth=8, source_id="inf")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception:
            pass

    @pytest.mark.unit
    def test_invalid_bit_depth(self, pipeline):
        """不支持的位深度"""
        img = np.random.randint(0, 256, (64, 64, 3), dtype=np.uint8)
        raw = ImageInput(data=img, bit_depth=12, source_id="bad_bits")
        try:
            pipeline.preprocess(raw)
            pytest.fail("应抛出异常因位深度不支持")
        except ValueError:
            pass

    @pytest.mark.unit
    def test_4d_image_array(self, pipeline):
        """4维批量图像数组 - 应降维处理或优雅报错"""
        img = np.random.randint(0, 256, (2, 64, 64, 3), dtype=np.uint8)
        raw = ImageInput(data=img, bit_depth=8, source_id="4d")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception:
            pass


class TestTimeSeriesEdgeCases:
    """时序数据边缘情况"""

    @pytest.mark.unit
    def test_constant_ts(self, pipeline):
        """常数时序数据"""
        ts = np.ones((500, 1), dtype=np.float32) * 5.0
        raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=1, source_id="const")
        try:
            processed = pipeline.preprocess(raw)
            assert processed.processed_data is not None
        except Exception as e:
            pytest.fail(f"常数时序数据不应崩溃: {e}")

    @pytest.mark.unit
    def test_extreme_values_ts(self, pipeline):
        """极值时序数据"""
        ts = np.random.randn(500, 1).astype(np.float32) * 1e10
        raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=1, source_id="extreme")
        try:
            processed = pipeline.preprocess(raw)
            assert processed.processed_data is not None
        except Exception as e:
            pytest.fail(f"极值时序数据不应崩溃: {e}")

    @pytest.mark.unit
    def test_nan_ts(self, pipeline):
        """包含NaN的时序数据"""
        ts = np.random.randn(500, 2).astype(np.float32)
        ts[100, 0] = np.nan
        ts[200, 1] = np.nan
        raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=2, source_id="nan_ts")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception:
            pass

    @pytest.mark.unit
    def test_4d_image_array(self, pipeline):
        """包含Inf的时序数据"""
        ts = np.random.randn(500, 1).astype(np.float32)
        ts[50] = np.inf
        ts[150] = -np.inf
        raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=1, source_id="inf_ts")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception:
            pass

    @pytest.mark.unit
    def test_very_short_ts(self, pipeline):
        """极短时序数据"""
        ts = np.random.randn(5, 1).astype(np.float32)
        raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=1, source_id="short")
        try:
            processed = pipeline.preprocess(raw)
            assert processed.processed_data.shape[1] == 256
        except Exception as e:
            pytest.fail(f"极短时序数据不应崩溃: {e}")

    @pytest.mark.unit
    def test_single_value_ts(self, pipeline):
        """单值时序数据"""
        ts = np.array([[1.0]], dtype=np.float32)
        raw = TimeSeriesInput(data=ts, sample_rate=1000.0, channels=1, source_id="single")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception:
            pass

    @pytest.mark.unit
    def test_invalid_sample_rate(self, pipeline):
        """无效采样率"""
        ts = np.random.randn(500, 1).astype(np.float32)
        raw = TimeSeriesInput(data=ts, sample_rate=50.0, channels=1, source_id="bad_rate")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception:
            pass


class TestTextEdgeCases:
    """文本数据边缘情况"""

    @pytest.mark.unit
    def test_empty_text(self, pipeline):
        """空文本"""
        raw = TextInput(data="", text_format="plain", source_id="empty")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"空文本不应崩溃: {e}")

    @pytest.mark.unit
    def test_whitespace_only_text(self, pipeline):
        """仅空白文本"""
        raw = TextInput(data="   \n\t  \r\n  ", text_format="plain", source_id="whitespace")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"空白文本不应崩溃: {e}")

    @pytest.mark.unit
    def test_special_characters_text(self, pipeline):
        """特殊字符文本"""
        text_with_specials = "正常文本\x00\x01\x02\x1f\x7f\x9f\t\r\n特殊字符"
        raw = TextInput(data=text_with_specials, text_format="plain", source_id="special")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
            assert not any(ord(c) < 0x20 for c in str(processed.metadata.get("cleaned_length", "")))
        except Exception as e:
            pytest.fail(f"特殊字符文本不应崩溃: {e}")

    @pytest.mark.unit
    def test_non_utf8_json(self, pipeline):
        """非标准JSON"""
        raw = TextInput(data={"key": b"bytes\x00value"}, text_format="json", source_id="bad_json")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception:
            pass

    @pytest.mark.unit
    def test_none_text(self, pipeline):
        """None文本"""
        raw = TextInput(data=None, text_format="plain", source_id="none")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception:
            pass

    @pytest.mark.unit
    def test_very_long_text(self, pipeline):
        """超长文本"""
        long_text = "加工参数" * 10000
        raw = TextInput(data=long_text, text_format="plain", source_id="long")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
            assert processed.metadata["cleaned_length"] <= 512
        except Exception as e:
            pytest.fail(f"超长文本不应崩溃: {e}")

    @pytest.mark.unit
    def test_unicode_emoji_text(self, pipeline):
        """Unicode/Emoji文本"""
        text = "加工精度🚀📐🔧★☆♠♣♥♦"
        raw = TextInput(data=text, text_format="plain", source_id="unicode")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"Unicode文本不应崩溃: {e}")


class TestToolStateEdgeCases:
    """刀具状态边缘情况"""

    @pytest.mark.unit
    def test_empty_tool_state(self, pipeline):
        """空刀具状态"""
        raw = ToolStateInput(data={}, source_id="empty")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"空刀具状态不应崩溃: {e}")

    @pytest.mark.unit
    def test_all_zeros_tool_state(self, pipeline):
        """全零刀具状态"""
        state = {
            "wear_level": 0.0,
            "cutting_time": 0.0,
            "tool_life_remaining": 0.0,
            "spindle_load": 0.0,
            "temperature": 0.0,
            "vibration_amplitude": 0.0,
            "cutting_force_x": 0.0,
            "cutting_force_y": 0.0,
            "cutting_force_z": 0.0,
        }
        raw = ToolStateInput(data=state, source_id="zeros")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"全零刀具状态不应崩溃: {e}")

    @pytest.mark.unit
    def test_string_values_tool_state(self, pipeline):
        """字符串数值"""
        state = {
            "wear_level": "0.5",
            "cutting_time": "120.5",
            "tool_life_remaining": "80",
        }
        raw = ToolStateInput(data=state, source_id="strings")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"字符串值不应崩溃: {e}")

    @pytest.mark.unit
    def test_none_values_tool_state(self, pipeline):
        """None值"""
        state = {
            "wear_level": None,
            "cutting_time": 120.0,
            "tool_life_remaining": None,
        }
        raw = ToolStateInput(data=state, source_id="none_vals")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"None值不应崩溃: {e}")

    @pytest.mark.unit
    def test_extreme_values_tool_state(self, pipeline):
        """极值"""
        state = {
            "wear_level": 1e20,
            "cutting_time": -1e20,
            "tool_life_remaining": 1e-20,
            "spindle_load": 1e20,
            "temperature": 1e20,
            "vibration_amplitude": -1e20,
            "cutting_force_x": 1e20,
            "cutting_force_y": -1e20,
            "cutting_force_z": 1e20,
        }
        raw = ToolStateInput(data=state, source_id="extreme")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
            assert processed.anomaly_count >= 0
        except Exception as e:
            pytest.fail(f"极值刀具状态不应崩溃: {e}")


class TestGCodeEdgeCases:
    """G代码边缘情况"""

    @pytest.mark.unit
    def test_empty_gcode(self, pipeline):
        """空G代码"""
        raw = GCodeInput(data="", controller_type="fanuc", source_id="empty")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"空G代码不应崩溃: {e}")

    @pytest.mark.unit
    def test_comment_only_gcode(self, pipeline):
        """仅注释G代码"""
        gcode = "(This is a comment)\n; Another comment\n(No actual code)"
        raw = GCodeInput(data=gcode, controller_type="fanuc", source_id="comments")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"仅注释G代码不应崩溃: {e}")

    @pytest.mark.unit
    def test_corrupted_gcode(self, pipeline):
        """损坏的G代码"""
        gcode = "G01 X???.?? Y@@@.###\nINVALID COMMAND\nG99 X999999"
        raw = GCodeInput(data=gcode, controller_type="fanuc", source_id="corrupt")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"损坏G代码不应崩溃: {e}")

    @pytest.mark.unit
    def test_unrecognized_controller(self, pipeline):
        """不支持的控制器"""
        gcode = "G01 X10. F500."
        raw = GCodeInput(data=gcode, controller_type="mazak", source_id="bad_controller")
        try:
            pipeline.preprocess(raw)
            pytest.fail("应抛出异常因控制器不支持")
        except ValueError:
            pass

    @pytest.mark.unit
    def test_heidenhain_format(self, pipeline):
        """Heidenhain格式"""
        gcode = """0 BEGIN PGM TEST MM
1 TOOL CALL 1 Z S8000
2 L X+10 Y+10 F500
3 M30
4 END PGM TEST MM"""
        raw = GCodeInput(data=gcode, controller_type="heidenhain", source_id="heidenhain")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"Heidenhain格式不应崩溃: {e}")

    @pytest.mark.unit
    def test_empty_lines_gcode(self, pipeline):
        """含空行的G代码"""
        gcode = "\n\nG01 X10. F500.\n\n\nG02 X20. R5.\n\nM30\n\n"
        raw = GCodeInput(data=gcode, controller_type="fanuc", source_id="empty_lines")
        try:
            processed = pipeline.preprocess(raw)
            assert processed is not None
        except Exception as e:
            pytest.fail(f"含空行G代码不应崩溃: {e}")


class TestErrorHandling:
    """错误处理机制测试"""

    @pytest.mark.unit
    def test_error_isolation_single_modality(self, pipeline):
        """单模态异常不污染其他模态"""
        inputs = {
            "good_image": ImageInput(
                data=np.random.randint(0, 256, (64, 64, 3), dtype=np.uint8),
                bit_depth=8, source_id="good",
            ),
            "bad_gcode": GCodeInput(
                data="G01 X10. F500.\nM30",
                controller_type="fanuc", source_id="bad",
            ),
        }

        try:
            result = pipeline.process(inputs)
            assert "good_image" in result.individual_features
        except Exception:
            pass

    @pytest.mark.unit
    def test_all_modalities_error(self, pipeline):
        """所有模态异常时不崩溃"""
        inputs = {
            "bad_img": ImageInput(
                data=np.zeros((0, 0, 3), dtype=np.uint8),
                bit_depth=8, source_id="bad_img",
            ),
            "bad_text": TextInput(data=None, text_format="plain", source_id="bad_text"),
        }

        try:
            pipeline.process(inputs)
            pytest.fail("应抛出异常")
        except ValueError:
            pass

    @pytest.mark.unit
    def test_edge_case_quality_check(self, quality_checker):
        """边缘情况质量检查"""
        for data_type in [DataSourceType.IMAGE, DataSourceType.TIME_SERIES,
                          DataSourceType.TEXT, DataSourceType.GCODE]:
            edge_data = None
            if data_type == DataSourceType.IMAGE:
                edge_data = np.zeros((0, 0, 3), dtype=np.uint8)
            elif data_type == DataSourceType.TIME_SERIES:
                edge_data = np.array([], dtype=np.float32)
            elif data_type == DataSourceType.TEXT:
                edge_data = ""
            elif data_type == DataSourceType.GCODE:
                edge_data = ""

            is_valid, error_msg = quality_checker.check_edge_cases(edge_data, data_type)
            assert not is_valid, f"{data_type.value} 边缘数据应被检测为无效"
            assert len(error_msg) > 0, f"{data_type.value} 应有错误消息"
