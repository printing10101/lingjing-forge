"""DXF模块测试用例。

覆盖以下场景：
1. DXF解析器 - 文件不存在/空文件/有效文件/多版本兼容
2. 特征提取器 - 孔识别/平面检测/尺寸关联/深度推断
3. 模型转换器 - 基础外形/孔创建/STL导出
4. 端到端流水线 - 完整DXF→G代码流程
5. 错误处理 - 各种异常场景
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import ezdxf
import pytest

from app.dxf.dxf_parser import (
    DxfParser,
    DxfParseResult,
    DxfLine,
    DxfCircle,
    DxfDimension,
)
from app.dxf.feature_extractor import (
    FeatureExtractor,
    FeatureExtractionResult,
    HoleFeatureInfo,
    extract_tolerance_from_text,
    is_counterbore_text,
)
from app.dxf.exceptions import (
    DxfParseError,
    DxfFormatError,
    DxfFeatureError,
    DxfModelError,
    DxfError,
)
from app.dxf.pipeline import DxfProcessPipeline


class TestDxfParser:
    """DXF解析器测试。"""

    def test_file_not_found(self):
        parser = DxfParser()
        with pytest.raises(DxfParseError, match="文件不存在"):
            parser.parse("nonexistent_file.dxf")

    def test_empty_file(self):
        parser = DxfParser()
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            f.write(b"")
            temp_path = f.name

        try:
            with pytest.raises(DxfParseError, match="文件为空"):
                parser.parse(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_invalid_dxf_content(self):
        parser = DxfParser()
        with tempfile.NamedTemporaryFile(suffix=".dxf", mode="w", delete=False, encoding="utf-8") as f:
            f.write("这不是有效的DXF文件内容")
            temp_path = f.name

        try:
            with pytest.raises((DxfParseError, DxfFormatError)):
                parser.parse(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_parse_simple_dxf_with_lines(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_line((0, 0), (100, 0))
        msp.add_line((100, 0), (100, 80))
        msp.add_line((100, 80), (0, 80))
        msp.add_line((0, 80), (0, 0))

        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            doc.saveas(f.name)
            temp_path = f.name

        try:
            parser = DxfParser()
            result = parser.parse(temp_path)

            assert result.success
            assert len(result.lines) == 4
            assert result.total_entities >= 4
            assert "2010" in result.dxf_version
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_parse_dxf_with_circles(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_circle((50, 40), radius=10)
        msp.add_circle((80, 60), radius=5)

        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            doc.saveas(f.name)
            temp_path = f.name

        try:
            parser = DxfParser()
            result = parser.parse(temp_path)

            assert result.success
            assert len(result.circles) == 2
            circle1 = result.circles[0]
            assert circle1.center[0] == 50
            assert circle1.center[1] == 40
            assert circle1.radius == 10

            circle2 = result.circles[1]
            assert circle2.radius == 5
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_parse_dxf_with_dimensions(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_circle((50, 40), radius=10)
        dim = msp.add_linear_dim(
            base=(0, 0),
            p1=(0, 0),
            p2=(100, 0),
            location=(50, -20),
        )
        dim.render()
        try:
            dim.dxf.text = "100"
        except AttributeError:
            pass

        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            doc.saveas(f.name)
            temp_path = f.name

        try:
            parser = DxfParser()
            result = parser.parse(temp_path)

            assert result.success
            assert len(result.circles) == 1
            assert len(result.dimensions) >= 1
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_parse_dxf_with_multiple_versions(self):
        versions = ["R12", "R2000", "R2004", "R2010", "R2013", "R2018"]

        for ver in versions:
            doc = ezdxf.new(ver)
            msp = doc.modelspace()
            msp.add_line((0, 0), (50, 50))

            with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
                doc.saveas(f.name)
                temp_path = f.name

            try:
                parser = DxfParser()
                result = parser.parse(temp_path)
                assert result.success, f"版本{ver}解析失败"
                assert len(result.lines) >= 1, f"版本{ver}未提取到直线"
            finally:
                Path(temp_path).unlink(missing_ok=True)

    def test_parse_dxf_with_text(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        text_entity = msp.add_text("Φ20", dxfattribs={"height": 3.5})
        text_entity.dxf.insert = (50, 40, 0)

        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            doc.saveas(f.name)
            temp_path = f.name

        try:
            parser = DxfParser()
            result = parser.parse(temp_path)

            assert result.success
            assert len(result.texts) >= 1
            assert "Φ20" in result.texts[0].content
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_parse_dxf_extents(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_line((10, 20), (110, 20))
        msp.add_line((110, 20), (110, 100))
        msp.add_circle((60, 60), radius=30)

        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            doc.saveas(f.name)
            temp_path = f.name

        try:
            parser = DxfParser()
            result = parser.parse(temp_path)

            assert result.success
            assert "min_x" in result.extents
            assert "max_x" in result.extents
            assert result.extents["min_x"] <= 10
            assert result.extents["max_x"] >= 110
            assert result.extents["min_y"] <= 20
            assert result.extents["max_y"] >= 100
        finally:
            Path(temp_path).unlink(missing_ok=True)


class TestFeatureExtractor:
    """特征提取器测试。"""

    def _create_parse_result_with_rectangle_and_holes(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()

        msp.add_line((0, 0), (100, 0))
        msp.add_line((100, 0), (100, 80))
        msp.add_line((100, 80), (0, 80))
        msp.add_line((0, 80), (0, 0))

        msp.add_circle((30, 40), radius=5)
        msp.add_circle((70, 40), radius=8)

        dim1 = msp.add_linear_dim(
            base=(0, 0), p1=(0, 0), p2=(100, 0), location=(50, -15),
        )
        dim1.render()
        try:
            dim1.dxf.text = "100"
        except AttributeError:
            pass

        dim2 = msp.add_linear_dim(
            base=(0, 0), p1=(0, 0), p2=(0, 80), location=(-15, 40),
        )
        dim2.render()
        try:
            dim2.dxf.text = "80"
        except AttributeError:
            pass

        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            doc.saveas(f.name)
            temp_path = f.name

        try:
            from app.dxf.dxf_parser import DxfParser
            parser = DxfParser()
            parse_result = parser.parse(temp_path)
            return parse_result
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_extract_holes_from_circles(self):
        parse_result = self._create_parse_result_with_rectangle_and_holes()

        extractor = FeatureExtractor()
        result = extractor.extract(parse_result)

        assert result.success
        assert result.hole_count >= 2

        diameters = sorted([h.diameter for h in result.holes])
        assert diameters[0] >= 9.9
        assert diameters[-1] >= 15.9

    def test_extract_plane_features(self):
        parse_result = self._create_parse_result_with_rectangle_and_holes()

        extractor = FeatureExtractor()
        result = extractor.extract(parse_result)

        assert result.plane_count >= 0

    def test_overall_dimensions_from_extents(self):
        parse_result = self._create_parse_result_with_rectangle_and_holes()

        extractor = FeatureExtractor()
        result = extractor.extract(parse_result)

        assert result.overall_length > 0
        assert result.overall_width > 0
        assert result.overall_height > 0

    def test_hole_depth_inference(self):
        parse_result = self._create_parse_result_with_rectangle_and_holes()

        extractor = FeatureExtractor()
        result = extractor.extract(parse_result)

        for hole in result.holes:
            assert hole.depth > 0
            assert hole.diameter > 0

    def test_empty_parse_result(self):
        extractor = FeatureExtractor()

        with pytest.raises(DxfFeatureError, match="解析结果为空"):
            extractor.extract(None)

    def test_extract_tolerance_from_text(self):
        assert extract_tolerance_from_text("IT7") == "IT7"
        assert extract_tolerance_from_text("Φ20±0.02") == "IT6"
        assert extract_tolerance_from_text("H7") == "IT7"
        assert extract_tolerance_from_text("无公差信息") == ""

    def test_counterbore_detection(self):
        assert is_counterbore_text("沉头孔Φ20")
        assert is_counterbore_text("C'BORE Φ20")
        assert not is_counterbore_text("通孔Φ20")
        assert not is_counterbore_text("普通孔")


class TestDxfProcessPipeline:
    """DXF端到端流水线测试。"""

    def _create_test_dxf(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()

        msp.add_line((0, 0), (100, 0))
        msp.add_line((100, 0), (100, 60))
        msp.add_line((100, 60), (0, 60))
        msp.add_line((0, 60), (0, 0))

        msp.add_circle((25, 30), radius=6)
        msp.add_circle((75, 30), radius=8)

        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            doc.saveas(f.name)
            return f.name

    def test_full_pipeline_run(self):
        temp_path = self._create_test_dxf()

        try:
            pipeline = DxfProcessPipeline()
            result = pipeline.run(
                file_path=temp_path,
                material="45#钢",
                controller_type="fanuc_0i",
            )

            assert result.parse_result is not None
            assert result.feature_result is not None
            assert result.feature_result.hole_count >= 2

            assert len(result.stages) >= 5

            stage_names = [s.name for s in result.stages]
            assert "DXF解析" in stage_names
            assert "特征提取" in stage_names
            assert "数据组装" in stage_names
            assert "工艺规划与G代码生成" in stage_names

        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_pipeline_with_nonexistent_file(self):
        pipeline = DxfProcessPipeline()
        result = pipeline.run("nonexistent_file.dxf")

        assert not result.success
        assert len(result.stages) >= 1
        assert result.stages[0].status == "failed"

    def test_pipeline_result_to_dict(self):
        temp_path = self._create_test_dxf()

        try:
            pipeline = DxfProcessPipeline()
            result = pipeline.run(temp_path, material="45#钢")
            result_dict = result.to_dict()

            assert "success" in result_dict
            assert "stages" in result_dict
            assert "parse" in result_dict
            assert "features" in result_dict
            assert "process" in result_dict
        finally:
            Path(temp_path).unlink(missing_ok=True)


class TestDxfToModelConverter:
    """模型转换器测试。"""

    def test_create_model_from_dimensions(self):
        from app.dxf.dxf_to_model import DxfToModelConverter

        converter = DxfToModelConverter()
        model = converter.create_model_from_dimensions(
            length=100,
            width=80,
            height=20,
            holes=[
                {"center_x": 30, "center_y": 40, "diameter": 10, "depth": 20},
                {"center_x": 70, "center_y": 40, "diameter": 16, "depth": 20},
            ],
        )

        assert model is not None
        assert hasattr(model, 'val')

    def test_export_stl(self):
        from app.dxf.dxf_to_model import DxfToModelConverter

        converter = DxfToModelConverter()
        model = converter.create_model_from_dimensions(100, 80, 20, [])

        from app.dxf.dxf_to_model import ModelConversionResult
        conv_result = ModelConversionResult(
            workplane=model,
            length=100,
            width=80,
            height=20,
        )

        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as f:
            output_path = f.name

        try:
            result_path = converter.export_stl(conv_result, output_path)
            assert Path(result_path).exists()
            assert Path(result_path).stat().st_size > 0
        finally:
            Path(output_path).unlink(missing_ok=True)

    def test_convert_with_invalid_input(self):
        from app.dxf.dxf_to_model import DxfToModelConverter

        converter = DxfToModelConverter()
        with pytest.raises(DxfModelError):
            converter.convert(None)

    def test_empty_feature_result(self):
        from app.dxf.dxf_to_model import DxfToModelConverter
        from app.dxf.feature_extractor import FeatureExtractionResult

        converter = DxfToModelConverter()
        result = FeatureExtractionResult()
        conversion = converter.convert(result)

        assert not conversion.success
        assert len(conversion.errors) > 0


class TestErrorHandling:
    """错误处理测试。"""

    def test_exception_hierarchy(self):
        assert issubclass(DxfParseError, DxfError)
        assert issubclass(DxfFormatError, DxfParseError)
        assert issubclass(DxfFeatureError, DxfError)
        assert issubclass(DxfModelError, DxfError)

    def test_parse_error_message_quality(self):
        parser = DxfParser()
        try:
            parser.parse("nonexistent_file.dxf")
        except DxfParseError as e:
            msg = str(e)
            assert "文件不存在" in msg
            assert "nonexistent_file" in msg
            assert "文件路径" in msg


class TestDxfDataClasses:
    """数据类测试。"""

    def test_dxf_line_creation(self):
        line = DxfLine(
            start=(0, 0, 0),
            end=(100, 0, 0),
            layer="OUTLINE",
            color=1,
            handle="ABC",
        )
        assert line.start == (0, 0, 0)
        assert line.layer == "OUTLINE"

    def test_dxf_circle_creation(self):
        circle = DxfCircle(
            center=(50, 40, 0),
            radius=10.0,
        )
        assert circle.center[0] == 50
        assert circle.radius == 10.0

    def test_dxf_dimension_creation(self):
        dim = DxfDimension(
            dim_type="LINEAR",
            measurement=100.0,
            text="100",
        )
        assert dim.dim_type == "LINEAR"
        assert dim.measurement == 100.0

    def test_parse_result_to_dict(self):
        result = DxfParseResult(
            file_name="test.dxf",
            file_size=1024,
            dxf_version="AC1027 (2013)",
            lines=[DxfLine((0, 0, 0), (100, 0, 0))],
            circles=[DxfCircle((50, 40, 0), 10.0)],
        )
        d = result.to_dict()
        assert d["file_name"] == "test.dxf"
        assert d["lines_count"] == 1
        assert d["circles_count"] == 1

    def test_feature_result_to_dict(self):
        result = FeatureExtractionResult(
            holes=[HoleFeatureInfo(
                hole_id="H001",
                center_x=50, center_y=40,
                diameter=10.0, depth=20.0,
            )],
            overall_length=100, overall_width=80, overall_height=15,
        )
        d = result.to_dict()
        assert d["hole_count"] == 1
        assert d["overall_length"] == 100
