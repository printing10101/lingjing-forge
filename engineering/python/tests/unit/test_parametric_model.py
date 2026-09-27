"""NL2CAD 参数化直调单元测试（AST 提取 + 白名单覆写 + 免 LLM 重执行）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_parametric_model.py -v --no-cov
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.cad.cadquery_gen import CadQueryError
from app.cad.nl2cad_llm import generate_cadquery_script
from app.cad.parametric_model import (
    ParameterError,
    apply_parameters,
    extract_parameters,
    regenerate_model,
    script_to_parametric,
)

_NAMED_BOX = (
    "length = 50.0\n"
    "width = 30.0\n"
    "height = 20.0\n"
    "result = cq.Workplane('XY').box(length, width, height)"
)
_INLINE_BOX = "result = cq.Workplane('XY').box(50, 30, 20)"


def _run(coro):
    return asyncio.run(coro)


class TestExtractParameters:
    def test_named_dimensions_extracted(self) -> None:
        params = extract_parameters(_NAMED_BOX)
        assert params == {"length": 50.0, "width": 30.0, "height": 20.0}

    def test_inline_script_has_no_params(self) -> None:
        """内联字面量脚本 → 空参数表（诚实降级，前端不展示滑杆）。"""
        assert extract_parameters(_INLINE_BOX) == {}
        assert script_to_parametric(_INLINE_BOX).is_parametric is False

    def test_int_and_scientific(self) -> None:
        script = "a = 10\nc = 1e2\nresult = cq.Workplane('XY').box(a, c, 5)"
        params = extract_parameters(script)
        assert params == {"a": 10.0, "c": 100.0}

    def test_unary_minus_not_extracted(self) -> None:
        """`b = -2.5` 是 UnaryOp 非常量——负数尺寸本就违反建模契约，不入参数表。"""
        script = "b = -2.5\nresult = cq.Workplane('XY').box(1, 1, 1)"
        assert extract_parameters(script) == {}

    def test_ignores_strings_bools_and_function_scopes(self) -> None:
        script = (
            "name = 'plate'\n"
            "flag = True\n"
            "x, y = 1, 2\n"
            "def _f():\n"
            "    inner = 3.0\n"
            "result = cq.Workplane('XY').box(1, 1, 1)"
        )
        assert extract_parameters(script) == {}

    def test_syntax_error_raises(self) -> None:
        with pytest.raises(ParameterError, match="语法错误"):
            extract_parameters("result = (cq.Workplane")


class TestApplyParameters:
    def test_override_replaces_value(self) -> None:
        new_script = apply_parameters(_NAMED_BOX, {"length": 60.0})
        assert extract_parameters(new_script)["length"] == 60.0
        # 其余参数不受影响
        assert extract_parameters(new_script)["width"] == 30.0
        assert "result = cq.Workplane" in new_script

    def test_empty_overrides_returns_original(self) -> None:
        assert apply_parameters(_NAMED_BOX, {}) == _NAMED_BOX

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ParameterError, match="未知参数"):
            apply_parameters(_NAMED_BOX, {"hacker_var": 1.0})

    def test_non_positive_values_rejected(self) -> None:
        for bad in (0.0, -1.0):
            with pytest.raises(ParameterError, match="有限正数"):
                apply_parameters(_NAMED_BOX, {"length": bad})

    def test_non_finite_values_rejected(self) -> None:
        with pytest.raises(ParameterError, match="有限正数"):
            apply_parameters(_NAMED_BOX, {"length": float("nan")})

    def test_non_numeric_value_rejected(self) -> None:
        """字符串值直接拒绝——覆写值不可能携带代码注入。"""
        with pytest.raises(ParameterError, match="必须是数字"):
            apply_parameters(_NAMED_BOX, {"length": "50) or __import__('os')"})


class TestRegenerateModel:
    def test_regenerates_with_new_dimension(self, tmp_path) -> None:
        """覆写 length=60 → B-rep 校验通过的新模型 + 更新后的参数表。"""
        output_path, new_params = _run(
            regenerate_model(_NAMED_BOX, {"length": 60.0}, task_id="pm-1", output_format="step")
        )
        assert Path(output_path).exists()
        assert output_path.endswith(".step")
        assert new_params["length"] == 60.0

    def test_invalid_overwrite_fails_before_execution(self, tmp_path) -> None:
        with pytest.raises(ParameterError):
            _run(regenerate_model(_NAMED_BOX, {"nope": 1.0}, task_id="pm-2"))

    def test_zero_dimension_fails_fast(self, tmp_path) -> None:
        """0 尺寸在参数校验层拦截（不进入沙箱执行）。"""
        with pytest.raises(ParameterError):
            _run(regenerate_model(_NAMED_BOX, {"height": 0.0}, task_id="pm-3"))

    def test_execution_failure_surfaced(self, tmp_path) -> None:
        """几何不可行（圆角半径溢出）→ OCCT 异常经沙箱包装为 CadQueryError 透传。"""
        fillet_script = (
            "length = 10.0\n"
            "corner_r = 2.0\n"
            "result = cq.Workplane('XY').box(length, length, length).edges('>Z').fillet(corner_r)"
        )
        # corner_r 覆写为 50 → 远超 box 尺寸，OCCT BRepFilletAPI 抛 StdFail_NotDone
        with pytest.raises(CadQueryError):
            _run(regenerate_model(fillet_script, {"corner_r": 50.0}, task_id="pm-4"))


class TestGenerationIntegration:
    def test_generate_result_carries_parameters(self) -> None:
        """LLM 生成路径返回的参数表可直接喂给 regenerate_model。"""
        script = (
            "length = 40.0\n"
            "width = 20.0\n"
            "height = 10.0\n"
            "result = cq.Workplane('XY').box(length, width, height)"
        )

        class _MockLLM:
            async def __call__(self, prompt: str) -> str:
                return script

        result = _run(generate_cadquery_script("盒子", llm_call=_MockLLM(), max_attempts=1, task_id="pm-5"))
        assert result.parameters == {"length": 40.0, "width": 20.0, "height": 10.0}

        # 生成 → 调参 → 重执行，全程零 LLM 参与
        output_path, _ = _run(
            regenerate_model(result.script, {"length": 55.0}, task_id="pm-5b", output_format="step")
        )
        assert Path(output_path).exists()


class TestTemplateScriptParametric:
    """/generate 的模板脚本路径：命名变量化后可提取、可覆写、几何与直建路径一致。"""

    def test_template_script_is_parametric(self) -> None:
        from app.cad._cadquery_helpers import _build_shape_script

        script = _build_shape_script("box", {"length": 50, "width": 30, "height": 20}, {"x": 0, "y": 0, "z": 0})
        assert extract_parameters(script) == {"length": 50.0, "width": 30.0, "height": 20.0}

    @pytest.mark.parametrize("shape", ["box", "sphere", "cylinder", "cone", "unknown_shape"])
    def test_template_regenerates_all_shapes(self, shape: str) -> None:
        """五种输入（含未知形状回退 box）的模板脚本均可沙箱重执行。

        各形状只暴露真实驱动尺寸（sphere 只有 radius 等），因此覆写键从
        提取结果中动态选取，取值放大 1.5 倍（保持有限正数）。
        """
        from app.cad._cadquery_helpers import _build_shape_script

        script = _build_shape_script(shape, {"length": 40, "width": 30, "height": 20}, {"x": 0, "y": 0, "z": 0})
        original = extract_parameters(script)
        assert original, f"{shape} 模板脚本应至少暴露一个可调参数"

        key = sorted(original)[0]
        overrides = {key: round(original[key] * 1.5, 2)}
        output_path, new_params = _run(
            regenerate_model(script, overrides, task_id=f"pm-shape-{shape}", output_format="step")
        )
        assert Path(output_path).exists()
        assert new_params[key] == overrides[key]
