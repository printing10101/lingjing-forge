"""NL2CADService 参数化模板返回契约测试。

覆盖 generate_model_from_nl 的滑杆脚本降级规则：
- 纯基础形状 → 返回等价模板脚本 + 参数表（前端渲染滑杆）；
- 带 features（chamfer/fillet/step/slot）→ script/parameters 为空
  （模板脚本无法表达特征，滑杆重执行会静默丢特征，诚实降级不展示）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_nl2cad_parametric_service.py -v --no-cov
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.nl2cad.services import NL2CADService
from app.cad.parametric_model import extract_parameters

_TEMPLATE_SCRIPT = "length = 50.0\nwidth = 30.0\nheight = 20.0\nresult = cq.Workplane('XY').box(length, width, height)"


def _make_service(params: dict) -> NL2CADService:
    """构造跳过 LLM 与真实 CAD 执行的 service 替身。"""
    service = NL2CADService()
    service.extract_params_from_nl = AsyncMock(return_value=params)  # type: ignore[method-assign]
    generator = MagicMock()
    generator.generate_with_features = MagicMock(return_value="/tmp/model.stl")
    generator.generate_script_from_params = AsyncMock(return_value=_TEMPLATE_SCRIPT)
    service._cad_generator = generator
    return service


class TestGenerateModelParametricContract:
    """generate_model_from_nl 的 (model_path, params, script, parameters) 契约。"""

    @pytest.mark.unit
    def test_plain_shape_returns_parametric_script(self):
        """纯基础形状 → 模板脚本 + 参数表，供前端滑杆调参。"""
        params = {
            "shape_type": "box",
            "dimensions": {"length": 50.0, "width": 30.0, "height": 20.0},
            "position": {"x": 0, "y": 0, "z": 0},
            "features": [],
            "confidence": 0.9,
        }
        service = _make_service(params)

        model_path, extracted, script, parameters = asyncio.run(
            service.generate_model_from_nl("一个 50x30x20 的方块")
        )

        assert model_path == "/tmp/model.stl"
        assert extracted is params
        assert script == _TEMPLATE_SCRIPT
        assert parameters == extract_parameters(_TEMPLATE_SCRIPT)

    @pytest.mark.unit
    def test_featured_model_degrades_to_empty_script(self):
        """带特征 → script/parameters 为空：模板无法表达特征，不造假滑杆。"""
        params = {
            "shape_type": "box",
            "dimensions": {"length": 50.0, "width": 30.0, "height": 20.0},
            "position": {"x": 0, "y": 0, "z": 0},
            "features": [{"type": "slot", "center_x": 10, "center_y": 10,
                          "length": 20, "width": 8, "depth": 5}],
            "confidence": 0.9,
        }
        service = _make_service(params)

        _, _, script, parameters = asyncio.run(
            service.generate_model_from_nl("带滑槽的方块")
        )

        assert script == ""
        assert parameters == {}
        # 模型本身仍带特征正常生成（特征不应因降级而丢失）
        service._cad_generator.generate_with_features.assert_called_once()

    @pytest.mark.unit
    def test_features_none_treated_as_plain(self):
        """features 缺省（None）按纯基础形状处理，正常返回模板脚本。"""
        params = {
            "shape_type": "cylinder",
            "dimensions": {"radius": 15.0, "height": 30.0,
                           "length": 30.0, "width": 30.0},
            "position": {"x": 0, "y": 0, "z": 0},
            "confidence": 0.9,
        }
        assert params.get("features") is None
        service = _make_service(params)

        _, _, script, parameters = asyncio.run(
            service.generate_model_from_nl("一个圆柱")
        )

        assert script == _TEMPLATE_SCRIPT
        assert parameters
