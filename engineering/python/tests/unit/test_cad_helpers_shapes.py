"""T6 cad 域补测：参数化模型与 cadquery 沙箱辅助函数。"""

from __future__ import annotations

import math

import pytest

from app.cad import _cadquery_helpers as helpers
from app.cad.parametric_model import (
    ParameterError,
    apply_parameters,
    extract_parameters,
    script_to_parametric,
)

pytestmark = pytest.mark.unit

SCRIPT = """length = 100.0
width = 80.0
height = 20.0
result = cq.Workplane("XY").box(length, width, height)
"""


def test_extract_parameters_reads_top_level_numbers():
    assert extract_parameters(SCRIPT) == {"length": 100.0, "width": 80.0, "height": 20.0}


def test_apply_parameters_overrides_and_keeps_others():
    patched = apply_parameters(SCRIPT, {"length": 120.0})
    assert "length = 120.0" in patched
    assert "width = 80.0" in patched


def test_script_to_parametric_flags_and_errors():
    parametric = script_to_parametric(SCRIPT)
    assert parametric.is_parametric is True
    with pytest.raises(ParameterError):
        script_to_parametric("result = (")
    with pytest.raises(ParameterError):
        apply_parameters("def broken(:", {"x": 1.0})


def test_unpack_generation_params_defaults_and_values():
    assert helpers._unpack_generation_params({}) == ("box", {}, {"x": 0, "y": 0, "z": 0})
    shape, dims, pos = helpers._unpack_generation_params(
        {"shape_type": "cylinder", "dimensions": {"height": 40}, "position": {"x": 5}})
    assert shape == "cylinder"
    assert dims == {"height": 40}
    assert pos == {"x": 5}


@pytest.mark.parametrize(
    ("shape", "expected_method", "expected_args"),
    [
        ("box", "box", [50, 30, 20]),
        ("sphere", "sphere", [25]),
        ("cylinder", "cylinder", [20, 15]),
        ("bracket", "box", [50, 30, 20]),
    ],
)
def test_build_shape_params(shape, expected_method, expected_args):
    params = helpers._build_shape_params(shape, {})
    assert params["method_name"] == expected_method
    assert params["method_args"] == expected_args


def test_build_shape_script_declares_variables_and_result():
    script = helpers._build_shape_script("box", {"length": 60, "width": 40, "height": 10},
                                        {"x": 0, "y": 0, "z": 0})
    assert "length = 60" in script and "width = 40" in script and "height = 10" in script
    assert "result" in script
    fallback = helpers._build_shape_script("unknown", {"length": 60}, {})
    assert "result" in fallback


def test_wrap_script_exports_without_import():
    wrapped = helpers._wrap_script("result = cq.Workplane('XY').box(1,1,1)", "out.step", "step")
    assert "out.step" in wrapped
    assert "exportType=_export_type.STEP" in wrapped
    assert "import cadquery" not in wrapped


def test_wrap_script_unknown_format_falls_back_to_stl():
    wrapped = helpers._wrap_script("result = None", "out.xyz", "xyz")
    assert "_export_type.STL" in wrapped


def test_parse_worker_result_reads_last_json_line():
    stdout = "noise line\n{'not': 'json'}\n{\"status\": \"ok\", \"path\": \"a.step\"}\n"
    assert helpers._parse_worker_result(stdout, "") == {"status": "ok", "path": "a.step"}


def test_parse_worker_result_error_includes_stderr_tail():
    result = helpers._parse_worker_result("garbage", "Traceback: boom")
    assert result["status"] == "error"
    assert "boom" in result["error"]


def test_build_solid_volumes():
    box = helpers._build_solid("box", {"length": 100, "width": 80, "height": 20}, {})
    assert box.val().Volume() == pytest.approx(160000, rel=1e-6)

    sphere = helpers._build_solid("sphere", {"length": 50, "width": 50, "height": 50}, {})
    assert sphere.val().Volume() == pytest.approx(4 / 3 * math.pi * 25 ** 3, rel=1e-3)

    cylinder = helpers._build_solid("cylinder", {"height": 40, "width": 30}, {})
    assert cylinder.val().Volume() == pytest.approx(math.pi * 15 ** 2 * 40, rel=1e-3)

    cone = helpers._build_solid("cone", {"length": 20, "width": 40, "height": 30}, {"x": 1, "y": 2, "z": 3})
    assert cone.val().Volume() > 0


def test_temp_dir_register_and_cleanup(tmp_path):
    created = tmp_path / "cq_tmp"
    created.mkdir()
    helpers._register_cadquery_temp_dir(created)
    helpers._cleanup_cadquery_temp_dirs()
    assert not created.exists()


def test_build_shape_script_all_specs():
    for shape in ("box", "sphere", "cylinder", "cone"):
        script = helpers._build_shape_script(shape, {"length": 40, "width": 20, "height": 30}, {})
        assert "result" in script, shape


def test_script_validator_rejects_dangerous_constructs():
    import ast

    from app.cad.cadquery_gen import CadQueryScriptError, _CadQueryScriptValidator

    validator = _CadQueryScriptValidator()
    for source in ("import os", "from os import path", "x = ().__class__"):
        with pytest.raises(CadQueryScriptError):
            validator.visit(ast.parse(source))
