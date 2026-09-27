"""B-rep 校验器单元测试（Phase 1a：NL2CAD 拓扑校验 + 失败重生成闭环）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_brep_validator.py -v
"""

from __future__ import annotations

import cadquery as cq
import pytest

from app.cad._brep_validator import (
    ERR_DEGENERATE_DIMENSION,
    ERR_DEGENERATE_EDGE,
    ERR_NOT_SOLID,
    ERR_OVERSIZED_DIMENSION,
    ERR_ZERO_VOLUME,
    BrepValidationError,
    sanitize_dimensions,
    validate_brep,
    validate_exported_model,
    validate_workplane,
)
from app.cad.cadquery_gen import CadQueryGenerator, CadQueryScriptError


# validate_workplane / validate_brep 基本正确性
class TestValidateBasic:
    @pytest.mark.parametrize(
        "factory",
        [
            lambda: cq.Workplane("XY").box(10, 10, 10),
            lambda: cq.Workplane("XY").cylinder(10, 5),
            lambda: cq.Workplane("XY").sphere(5),
        ],
        ids=["box", "cylinder", "sphere"],
    )
    def test_valid_solids_pass(self, factory: object) -> None:
        report = validate_workplane(factory())  # type: ignore[arg-type]
        assert report.is_valid, report.summary()
        assert report.errors == []
        assert report.shape_type is not None and report.shape_type.upper() == "SOLID"
        assert report.volume is not None and report.volume > 0

    def test_wire_is_not_solid(self) -> None:
        """线框（wire）产物必须被拦截。"""
        report = validate_workplane(cq.Workplane("XY").rect(10, 10))
        assert not report.is_valid
        assert ERR_NOT_SOLID in report.error_codes

    def test_oversized_box_error(self) -> None:
        report = validate_brep(cq.Workplane("XY").box(20000, 10, 10).val())
        assert not report.is_valid
        assert ERR_OVERSIZED_DIMENSION in report.error_codes

    def test_degenerate_dimension_warning_then_error(self) -> None:
        thin = cq.Workplane("XY").box(10, 10, 0.0005)
        report = validate_workplane(thin)
        assert report.is_valid  # 非 strict 下仅告警
        assert ERR_DEGENERATE_DIMENSION in report.warning_codes

        strict_report = validate_workplane(thin, strict=True)
        assert not strict_report.is_valid
        assert ERR_DEGENERATE_DIMENSION in strict_report.error_codes

    def test_degenerate_edge_warning(self) -> None:
        tiny = cq.Workplane("XY").box(10, 10, 1e-6)
        report = validate_workplane(tiny)
        assert ERR_DEGENERATE_EDGE in report.warning_codes
        assert ERR_DEGENERATE_DIMENSION in report.warning_codes

    def test_raise_if_invalid(self) -> None:
        report = validate_workplane(cq.Workplane("XY").rect(10, 10))
        with pytest.raises(BrepValidationError):
            if not report.is_valid:
                from app.cad._brep_validator import raise_if_invalid

                raise_if_invalid(report)


# sanitize_dimensions
class TestSanitize:
    def test_clamps_degenerate_and_oversized(self) -> None:
        out = sanitize_dimensions({"shape_type": "box", "dimensions": {"length": 0, "width": -5, "height": 20000}})
        dims = out["dimensions"]
        assert dims["length"] >= 1e-3
        assert dims["width"] >= 1e-3
        assert dims["height"] <= 10000.0

    def test_volume_floor(self) -> None:
        out = sanitize_dimensions({"shape_type": "box", "dimensions": {"length": 1e-4, "width": 1e-4, "height": 1e-4}})
        product = out["dimensions"]["length"] * out["dimensions"]["width"] * out["dimensions"]["height"]
        assert product >= 1e-6

    def test_does_not_mutate_input(self) -> None:
        params = {"shape_type": "box", "dimensions": {"length": 0, "width": 30, "height": 20}}
        sanitize_dimensions(params)
        assert params["dimensions"]["length"] == 0  # 原 dict 未被修改


# 导出文件回读校验
class TestExportedModel:
    def test_step_roundtrip_valid(self, tmp_path) -> None:
        solid = cq.Workplane("XY").box(20, 12, 8).val()
        out = tmp_path / "part.step"
        cq.exporters.export(solid, str(out))
        report = validate_exported_model(out, "step")
        assert report is not None
        assert report.is_valid, report.summary()

    def test_stl_basic_checks(self, tmp_path) -> None:
        solid = cq.Workplane("XY").box(20, 12, 8).val()
        out = tmp_path / "part.stl"
        cq.exporters.export(solid, str(out))
        report = validate_exported_model(out, "stl")
        assert report is not None
        assert report.is_valid  # STL 仅做尺寸级检查

    def test_unvalidated_formats_skip(self, tmp_path) -> None:
        assert validate_exported_model(tmp_path / "x.obj", "obj") is None
        assert validate_exported_model(tmp_path / "x.gltf", "gltf") is None

    def test_missing_file_raises(self, tmp_path) -> None:
        report = validate_exported_model(tmp_path / "missing.step", "step")
        assert report is not None
        assert not report.is_valid


# CadQueryGenerator 集成：拓扑校验 + 失败重生成闭环
class TestGeneratorIntegration:
    def setup_method(self) -> None:
        self.gen = CadQueryGenerator()

    def test_generate_3d_model_valid(self) -> None:
        path = self.gen.generate_3d_model(
            {"shape_type": "box", "dimensions": {"length": 50, "width": 30, "height": 20}}
        )
        assert path.endswith(".stl")

    def test_generate_with_features_valid(self) -> None:
        path = self.gen.generate_with_features(
            {"shape_type": "box", "dimensions": {"length": 50, "width": 30, "height": 20}},
            features=[{"type": "slot", "center_x": 0, "center_y": 0, "length": 10, "width": 4, "depth": 2}],
        )
        assert path.endswith(".stl")

    def test_retry_drops_bad_feature(self) -> None:
        """过大的圆角半径使几何静默失效（isValid=False）→ 校验拦截 → 自动剔除特征重生成。"""
        path, report, attempts = self.gen.generate_3d_model_with_retry(
            {"shape_type": "box", "dimensions": {"length": 10, "width": 10, "height": 10}},
            features=[{"type": "fillet", "radius": 6}],
            max_retries=3,
        )
        assert attempts >= 2  # 第一次校验失败、剔除特征后重生成成功
        assert report is not None and report.is_valid
        assert path.endswith(".stl")

    def test_retry_sanitizes_degenerate_dims(self) -> None:
        """全零尺寸导致 OCCT 构建失败 → 夹取退化尺寸后重生成成功。"""
        path, report, attempts = self.gen.generate_3d_model_with_retry(
            {"shape_type": "box", "dimensions": {"length": 0, "width": 0, "height": 0}},
            max_retries=3,
        )
        assert attempts >= 2
        assert report is not None and report.is_valid
        assert path.endswith(".stl")

    def test_retry_exhaustion_raises(self) -> None:
        """max_retries=1 且特征剔除后仍失败 → 抛 CadQueryScriptError。"""
        with pytest.raises(CadQueryScriptError):
            self.gen.generate_3d_model_with_retry(
                {"shape_type": "box", "dimensions": {"length": 10, "width": 10, "height": 10}},
                features=[{"type": "fillet", "radius": 6}],
                max_retries=1,
            )
