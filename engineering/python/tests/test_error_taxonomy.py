"""面向制造场景的结构化错误分类体系 单元测试。

覆盖：
- 所有错误类型的创建与序列化
- 错误码查询/label映射
- ManufacturingError 异常创建与API响应
- 按阶段/严重程度筛选
- 默认修复建议完整性
- response.py integration 验证
"""

from __future__ import annotations

import pytest

from app.core.error_taxonomy import (
    CATEGORY_TO_NUMERIC,
    ErrorCategory,
    ManufacturingError,
    category_to_numeric,
)
from app.core.response import manufacturing_error as response_manufacturing_error


class TestErrorCategory:
    def test_all_categories_have_valid_tuple(self):
        for cat in ErrorCategory:
            assert isinstance(cat.code, str)
            assert len(cat.code) == 5
            assert cat.code.startswith("E")
            assert isinstance(cat.message, str)
            assert cat.severity in ("critical", "error", "warning")
            assert isinstance(cat.default_suggestion, str)

    def test_code_property(self):
        assert ErrorCategory.DRAWING_PARSE_FAILED.code == "E1001"
        assert ErrorCategory.RECONSTRUCTION_FAILED.code == "E2001"

    def test_message_property(self):
        assert ErrorCategory.COLLISION_DETECTED.message == "检测到刀具碰撞风险"

    def test_severity_property(self):
        assert ErrorCategory.DRAWING_PARSE_FAILED.severity == "critical"
        assert ErrorCategory.PRECISION_BELOW_THRESHOLD.severity == "warning"

    def test_is_critical(self):
        assert ErrorCategory.TOOLPATH_GENERATION_FAILED.is_critical
        assert not ErrorCategory.PARAMETER_OUT_OF_RANGE.is_critical

    def test_is_error(self):
        assert ErrorCategory.NO_SUITABLE_TOOL.is_error
        assert not ErrorCategory.FEATURE_RECOGNITION_INCOMPLETE.is_error

    def test_is_warning(self):
        assert ErrorCategory.PRECISION_BELOW_THRESHOLD.is_warning
        assert not ErrorCategory.COLLISION_DETECTED.is_warning

    def test_from_code_valid(self):
        cat = ErrorCategory.from_code("E3004")
        assert cat is ErrorCategory.PARAMETER_OUT_OF_RANGE

    def test_from_code_invalid(self):
        assert ErrorCategory.from_code("E9999") is None

    def test_list_by_stage_drawing(self):
        cats = ErrorCategory.list_by_stage("drawing")
        assert len(cats) >= 4
        for c in cats:
            assert c.code.startswith("E1")

    def test_list_by_stage_reconstruction(self):
        cats = ErrorCategory.list_by_stage("reconstruction")
        assert len(cats) >= 3
        for c in cats:
            assert c.code.startswith("E2")

    def test_list_by_stage_process(self):
        cats = ErrorCategory.list_by_stage("process")
        assert len(cats) >= 5
        for c in cats:
            assert c.code.startswith("E3")

    def test_list_by_stage_toolpath(self):
        cats = ErrorCategory.list_by_stage("toolpath")
        assert len(cats) >= 1
        for c in cats:
            assert c.code.startswith("E4")

    def test_list_by_stage_system(self):
        cats = ErrorCategory.list_by_stage("system")
        assert len(cats) >= 3
        for c in cats:
            assert c.code.startswith("E5")

    def test_list_by_severity_critical(self):
        cats = ErrorCategory.list_by_severity("critical")
        assert len(cats) >= 3
        for c in cats:
            assert c.severity == "critical"

    def test_list_by_severity_error(self):
        cats = ErrorCategory.list_by_severity("error")
        assert len(cats) >= 8
        for c in cats:
            assert c.severity == "error"

    def test_list_by_severity_warning(self):
        cats = ErrorCategory.list_by_severity("warning")
        assert len(cats) >= 5
        for c in cats:
            assert c.severity == "warning"

    @pytest.mark.parametrize("cat", list(ErrorCategory))
    def test_default_suggestion_not_empty(self, cat):
        assert len(cat.default_suggestion) > 10, f"{cat.code} 缺少默认修复建议"

    def test_drawing_categories_complete(self):
        codes = {c.code for c in ErrorCategory.list_by_stage("drawing")}
        expected = {"E1001", "E1002", "E1003", "E1004", "E1005", "E1006"}
        for e in expected:
            assert e in codes, f"缺少错误码: {e}"

    def test_reconstruction_categories_complete(self):
        codes = {c.code for c in ErrorCategory.list_by_stage("reconstruction")}
        expected = {"E2001", "E2002", "E2003", "E2004", "E2005"}
        for e in expected:
            assert e in codes, f"缺少错误码: {e}"

    def test_process_categories_complete(self):
        codes = {c.code for c in ErrorCategory.list_by_stage("process")}
        expected = {
            "E3001",
            "E3002",
            "E3003",
            "E3004",
            "E3005",
            "E3006",
            "E3007",
            "E3008",
        }
        for e in expected:
            assert e in codes, f"缺少错误码: {e}"

    def test_toolpath_categories_complete(self):
        codes = {c.code for c in ErrorCategory.list_by_stage("toolpath")}
        expected = {"E4001", "E4002", "E4003", "E4004", "E4005"}
        for e in expected:
            assert e in codes, f"缺少错误码: {e}"

    def test_system_categories_complete(self):
        codes = {c.code for c in ErrorCategory.list_by_stage("system")}
        expected = {"E5001", "E5002", "E5003", "E5004", "E5005"}
        for e in expected:
            assert e in codes, f"缺少错误码: {e}"


class TestManufacturingError:
    def test_create_with_category(self):
        err = ManufacturingError(
            category=ErrorCategory.PARAMETER_OUT_OF_RANGE,
            detail="LNN推荐的切削速度250 m/min超出TC4钛合金推荐范围[30, 80] m/min",
            recoverable=True,
            adjusted_values={"cutting_speed": 80.0},
        )
        assert err.code == "E3004"
        assert err.severity == "warning"
        assert err.recoverable
        assert err.adjusted_values["cutting_speed"] == 80.0

    def test_create_with_custom_suggestion(self):
        err = ManufacturingError(
            category=ErrorCategory.NO_SUITABLE_TOOL,
            detail="需要φ12硬质合金立铣刀",
            suggestion="请在刀具管理界面添加刀具类型: endmill_wc_flat_d12",
        )
        assert "刀具管理界面" in err.suggestion

    def test_default_suggestion_used(self):
        err = ManufacturingError(
            category=ErrorCategory.COLLISION_DETECTED,
            detail="G00 N50处检测到碰撞",
        )
        assert "安全高度" in err.suggestion

    def test_to_dict_basic(self):
        err = ManufacturingError(
            category=ErrorCategory.GEOMETRY_INVALID,
            detail="模型存在自相交曲面",
        )
        d = err.to_dict()
        assert d["code"] == "E2002"
        assert d["severity"] == "error"
        assert d["message"] == "重建几何体无效（非封闭/自相交）"
        assert d["detail"] == "模型存在自相交曲面"
        assert "suggestion" in d
        assert d["recoverable"] is False

    def test_to_dict_with_adjusted(self):
        err = ManufacturingError(
            category=ErrorCategory.PARAMETER_OUT_OF_RANGE,
            detail="切削速度超出范围",
            recoverable=True,
            adjusted_values={"cutting_speed": 80.0, "feed": 0.15},
        )
        d = err.to_dict()
        assert d["adjusted_values"]["cutting_speed"] == 80.0
        assert d["recoverable"] is True

    def test_to_response(self):
        err = ManufacturingError(
            category=ErrorCategory.CUTTING_FORCE_EXCEEDED,
            detail="Fc=5200N > 5000N",
        )
        resp = err.to_response()
        assert "request_id" in resp
        assert resp["code"] == "E3005"

    def test_from_code_valid(self):
        err = ManufacturingError.from_code(
            code="E4004",
            detail="G00 N12 穿过毛坯区域",
            recoverable=False,
        )
        assert err.code == "E4004"
        assert err.severity == "critical"

    def test_from_code_invalid_fallback(self):
        err = ManufacturingError.from_code(code="E9999", detail="未知错误")
        assert err is not None
        assert err.code != "E9999"

    def test_exception_inheritance(self):
        err = ManufacturingError(
            category=ErrorCategory.DRAWING_PARSE_FAILED,
            detail="test",
        )
        assert isinstance(err, Exception)

    def test_str_representation(self):
        err = ManufacturingError(
            category=ErrorCategory.DRAWING_PARSE_FAILED,
            detail="test",
        )
        assert str(err) == "图纸解析失败"


class TestResponseIntegration:
    def test_manufacturing_error_response(self):
        err = ManufacturingError(
            category=ErrorCategory.PARAMETER_OUT_OF_RANGE,
            detail="LNN推荐的切削速度250 m/min超出TC4钛合金推荐范围[30, 80] m/min",
            suggestion="已自动调整至推荐上限80 m/min，请确认是否接受",
            recoverable=True,
            adjusted_values={"cutting_speed": 80.0},
        )
        resp = response_manufacturing_error(err)
        assert resp["code"] == "E3004"
        assert resp["error_code"] == "E3004"
        assert resp["severity"] == "warning"
        assert resp["detail"] is not None
        assert resp["suggestion"] is not None
        assert resp["recoverable"] is True
        assert resp["adjusted_values"]["cutting_speed"] == 80.0
        assert "request_id" in resp

    def test_manufacturing_error_response_format(self):
        """验证API响应格式符合规范"""
        err = ManufacturingError(
            category=ErrorCategory.PARAMETER_OUT_OF_RANGE,
            detail="LNN推荐的切削速度250 m/min超出TC4钛合金推荐范围[30, 80] m/min",
            suggestion="已自动调整至推荐上限80 m/min，请确认是否接受",
            recoverable=True,
            adjusted_values={"cutting_speed": 80.0},
        )
        resp = response_manufacturing_error(err)

        # 规范要求的关键字段
        assert "code" in resp
        assert "message" in resp
        assert "severity" in resp
        assert "detail" in resp
        assert "suggestion" in resp
        assert "recoverable" in resp
        assert "adjusted_values" in resp
        assert resp["recoverable"] is True
        assert resp["severity"] == "warning"

    def test_response_error_function_extended(self):
        """验证 response.py 中 error() 函数支持新字段"""
        from app.core.response import error, ErrorCode as EC

        resp = error(
            code=EC.INTERNAL_ERROR,
            message="系统错误",
            detail="详情",
            severity="error",
            recoverable=True,
            adjusted_values={"a": 1},
        )
        assert resp["severity"] == "error"
        assert resp["recoverable"] is True
        assert resp["adjusted_values"] == {"a": 1}

    def test_response_error_response_extended(self):
        """验证 error_response() 支持新字段"""
        from app.core.response import error_response

        resp = error_response(
            code=4001,
            message="刀轨生成失败",
            severity="critical",
            suggestion="检查加工区域",
            recoverable=False,
        )
        assert resp["severity"] == "critical"
        assert resp["suggestion"] == "检查加工区域"
        assert "recoverable" not in resp

    def test_critical_error_response(self):
        err = ManufacturingError(
            category=ErrorCategory.RAPID_MOVE_COLLISION,
            detail="G00 N50 碰撞位置 (X=100, Y=50, Z=-2)",
        )
        resp = response_manufacturing_error(err)
        assert resp["severity"] == "critical"
        assert resp["code"] == "E4004"
        assert "安全平面" in resp["suggestion"]

    def test_warning_recoverable_response(self):
        err = ManufacturingError(
            category=ErrorCategory.TOOL_LIFE_INSUFFICIENT,
            detail="Taylor寿命=12min < 60min",
            recoverable=True,
            adjusted_values={"cutting_speed": 40},
        )
        resp = response_manufacturing_error(err)
        assert resp["recoverable"] is True
        assert resp["severity"] == "warning"


class TestCategoryToNumeric:
    def test_all_error_categories_mapped(self):
        for cat in ErrorCategory:
            num = category_to_numeric(cat)
            assert isinstance(num, int)
            assert 1000 <= num <= 5999, f"{cat.code} 映射到 {num}，超出预期范围"

    def test_category_to_numeric_known(self):
        assert category_to_numeric(ErrorCategory.DRAWING_PARSE_FAILED) == 1001
        assert category_to_numeric(ErrorCategory.COLLISION_DETECTED) == 4003
        assert category_to_numeric(ErrorCategory.MODEL_NOT_LOADED) == 5001

    def test_cat_map_consistency(self):
        for cat in ErrorCategory:
            num = CATEGORY_TO_NUMERIC.get(cat.code)
            assert num is not None, f"{cat.code} 未在 CATEGORY_TO_NUMERIC 中映射"


class TestEdgeCases:
    def test_empty_detail(self):
        err = ManufacturingError(
            category=ErrorCategory.MODEL_NOT_LOADED,
            detail="",
        )
        d = err.to_dict()
        assert d["detail"] == ""

    def test_no_adjusted_values(self):
        err = ManufacturingError(
            category=ErrorCategory.GPU_OUT_OF_MEMORY,
            detail="CUDA OOM",
        )
        d = err.to_dict()
        assert "adjusted_values" not in d

    def test_all_categories_to_response(self):
        """验证所有错误类型都能成功生成响应"""
        for cat in ErrorCategory:
            err = ManufacturingError(
                category=cat,
                detail=f"测试: {cat.code}",
                recoverable=cat.is_warning,
            )
            resp = response_manufacturing_error(err)
            assert resp["code"] == cat.code
            assert resp["severity"] == cat.severity

    def test_unicode_detail(self):
        err = ManufacturingError(
            category=ErrorCategory.DIMENSION_EXTRACTION_FAILED,
            detail="尺寸 ϕ50±0.025 提取失败",
        )
        resp = response_manufacturing_error(err)
        assert "ϕ" in resp["detail"]
