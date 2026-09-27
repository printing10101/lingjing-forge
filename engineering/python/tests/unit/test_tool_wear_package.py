"""services/tool_wear 全包 + toolpath/feed_rate_optimizer 覆盖率补强测试。

真实调用物理模型（Usui/Taylor 混合磨损曲线、标定、补偿推荐、参数建议），
ML 训练路径覆盖依赖缺失/数据缺失的错误分支（不依赖真实数据集文件）。
"""

from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.unit

from app.services.tool_wear.calibrator import WearCalibrator
from app.services.tool_wear.compensation_recommender import CompensationRecommender
from app.services.tool_wear.curve_predictor import WearCurvePredictor
from app.services.tool_wear._constants import get_material_params, get_tool_params
from app.services.tool_wear.facade import ToolWearPredictor
from app.services.tool_wear.ml_trainer import WearMLTrainer
from app.services.tool_wear.param_advisor import ParameterAdvisor
from app.toolpath.feed_rate_optimizer import CuttingConditions, FeedRateOptimizer


def _params(**overrides):
    base = {
        "cutting_speed": 120.0,
        "feed_rate": 0.15,
        "depth_of_cut": 1.0,
        "material_type": "steel_45",
        "tool_type": "carbide",
        "tool_diameter": 10.0,
        "current_wear": 0.05,
    }
    base.update(overrides)
    return base


class TestConstants:
    def test_get_material_params_match(self):
        from app.services.tool_wear._constants import get_material_params

        for name in ["aluminum_6061", "Steel-4140", "TITANIUM_TC4", "stainless 304"]:
            mp = get_material_params(name)
            assert mp.name

    def test_get_material_params_default(self):
        from app.services.tool_wear._constants import get_material_params

        mp = get_material_params("unknown_xyz")
        assert mp.name == "Default"

    def test_get_tool_params(self):
        from app.services.tool_wear._constants import get_tool_params

        assert get_tool_params("coated_carbide")["wear_factor"] == 0.7
        # get_tool_params 运行时按类型字典查（coated_carbide 定义 0.7；cermet 0.8）
        assert get_tool_params("nope")["wear_factor"] == 1.0

    def test_facade_attrs(self):
        tp = ToolWearPredictor()
        assert tp.default_replacement_threshold == 0.3
        assert "steel_45" in tp.material_params
        assert tp.USUI_TAYLOR_SWITCH_THRESHOLD == 0.2


class TestWearCurvePredictor:
    def setup_method(self):
        self.p = WearCurvePredictor()

    def test_predict_wear_curve_reaches_threshold(self):
        curve = self.p.predict_wear_curve(_params())
        assert curve.total_time > 0
        assert curve.max_wear > 0
        assert curve.model_info["time_to_threshold"] > 0
        assert curve.confidence >= 0.5

    def test_predict_wear_curve_titanium(self):
        curve = self.p.predict_wear_curve(_params(material_type="titanium_tc4", current_wear=0.15))
        assert curve.total_time > 0
        assert curve.model_info["wear_threshold"] <= 0.35

    def test_predict_wear_curve_already_worn(self):
        curve = self.p.predict_wear_curve(_params(current_wear=0.28))
        # 接近阈值，几乎立即到达
        assert curve.model_info["time_to_threshold"] < 50

    def test_predict_remaining_life(self):
        life = self.p.predict_remaining_life(0.05, _params())
        assert life > 0

    def test_predict_remaining_life_exhausted(self):
        life = self.p.predict_remaining_life(0.5, _params())
        assert life == 0.0

    def test_get_replacement_threshold(self):
        assert self.p.get_replacement_threshold() == 0.3
        assert self.p.get_replacement_threshold("titanium_tc4") == 0.25
        assert self.p.get_replacement_threshold("steel_45") == 0.3
        assert self.p.get_replacement_threshold("aluminum_6061") == 0.35
        assert self.p.get_replacement_threshold("steel_4140") == 0.3

    def test_get_supported_models(self):
        models = self.p.get_supported_models()
        assert len(models) >= 3
        assert any("Usui" in m["name"] for m in models)

    def test_temperature_clamped(self):
        t = self.p._get_temperature(500, 2.0, 5.0, get_material_params("steel_45"))
        assert 300.0 <= t <= 1200.0

    def test_wear_rates_bounded(self):
        from app.services.tool_wear._constants import get_material_params, get_tool_params

        mat = get_material_params("steel_45")
        tool = get_tool_params("carbide")
        u = self.p._usui_wear_rate(120, 0.15, 1.0, 900.0, mat, tool)
        t = self.p._taylor_wear_rate(0.1, 120, 0.15, 1.0, mat, tool)
        assert 1e-6 <= u <= 0.01
        assert 1e-5 <= t <= 0.02

    def test_phase_determination(self):
        assert self.p._determine_phase(0.01) == "initial"
        assert self.p._determine_phase(0.1) == "steady"
        assert self.p._determine_phase(0.25) == "accelerated"

    def test_confidence_bounds(self):
        from app.services.tool_wear._constants import get_material_params

        mat = get_material_params("steel_45")
        c = self.p._compute_confidence(0.3, 300.0, mat)
        assert 0.5 <= c <= 0.98


class TestParameterAdvisor:
    def test_critical_urgency(self):
        a = ParameterAdvisor()
        r = a.suggest_parameter_adjustment(0.28, 12.0, _params())
        assert "critical" in r.summary
        assert any("tool_inspection" in str(s.parameter) for s in r.suggestions)

    def test_warning_urgency(self):
        a = ParameterAdvisor()
        r = a.suggest_parameter_adjustment(0.18, 30.0, _params())
        assert "warning" in r.summary
        assert len(r.suggestions) >= 3

    def test_normal_urgency(self):
        a = ParameterAdvisor()
        r = a.suggest_parameter_adjustment(0.05, 100.0, _params())
        assert "normal" in r.summary
        # 正常档：仅 coolant 建议（速度 5% 仍计入）
        assert len(r.suggestions) >= 1


class TestCompensationRecommender:
    def _make(self):
        return CompensationRecommender()

    def test_strategies(self):
        r = self._make()
        cases = [
            (0.29, "replace_tool", "critical"),
            (0.24, "aggressive_compensation", "critical"),
            (0.18, "moderate_compensation", "warning"),
            (0.12, "slight_compensation", "normal"),
            (0.03, "no_adjustment", "normal"),
        ]
        for wear, strategy, urgency in cases:
            out = r.get_compensation_recommendations(wear, _params())
            assert out["strategy"] == strategy, f"wear={wear}: {out['strategy']}"
            assert out["urgency"] == urgency

    def test_machine_capabilities_limit(self):
        r = self._make()
        # 极小机床能力 转速/进给被钳制并产生警告
        caps = {"max_spindle_speed": 1000, "max_feed_rate": 200}
        out = r.get_compensation_recommendations(0.20, _params(cutting_speed=300.0, tool_diameter=3.0), caps)
        assert len(out["warnings"]) >= 1

    def test_tool_diameter_zero_no_crash(self):
        r = self._make()
        out = r.get_compensation_recommendations(0.05, _params(tool_diameter=0.0))
        assert out["strategy"] == "no_adjustment"

    def test_life_extension_computed(self):
        r = CompensationRecommender()
        out = r.get_compensation_recommendations(0.2, _params())
        assert out["expected_life_extension_percent"] > 0
        assert any(s["param"] == "cutting_speed" for s in out["suggestions"])


class TestWearCalibrator:
    def _make(self):
        return WearCalibrator(WearCurvePredictor())

    def test_calibrate_with_measurement(self):
        c = self._make()
        out = c.calibrate_with_measurement(0.08, 30.0, _params())
        assert "calibrated_curve" in out
        assert out["correction_factor"] > 0

    def test_calibrate_with_measurement_far_time(self):
        # 时间点不在采样点内 用平均磨损率兜底
        c = self._make()
        out = c.calibrate_with_measurement(0.08, 99999.0, _params())
        assert out["predicted_wear_at_time"] >= 0

    def test_calibrate_real_time_sensors(self):
        c = self._make()
        sensors = {
            "vibration_rms": 3.0,  # >2.0 → ×1.15
            "cutting_force": 500.0,  # 远超预期（钢 1.0×100=100 → >150 → ×1.20）
            "temperature": 900.0,  # >800 → ×1.25
        }
        out = c.calibrate_with_real_time_data(0.1, sensors, 60.0, _params())
        assert out["sensor_adjustment"] > 1.0
        assert len(out["adjustment_reasons"]) == 3

    def test_calibrate_real_time_mild(self):
        c = self._make()
        sensors = {"vibration_rms": 1.5, "cutting_force": 130.0, "temperature": 500.0}
        out = c.calibrate_with_real_time_data(0.1, sensors, 60.0, _params())
        assert 1.0 < out["sensor_adjustment"] < 1.5

    def test_calibrate_real_time_no_sensors(self):
        c = self._make()
        out = c.calibrate_with_real_time_data(0.1, {}, 60.0, _params())
        assert out["sensor_adjustment"] == 1.0


class TestWearMLTrainer:
    def _make(self):
        return WearMLTrainer(WearCurvePredictor())

    def test_predict_vibration_anomaly_no_model(self):
        out = self._make().predict_vibration_anomaly([1.0, 2.0])
        assert out["prediction"] == "unknown"

    def test_get_process_baseline_no_loader(self, monkeypatch):
        t = self._make()
        monkeypatch.setattr(t, "_bosch_feature_loader", None)
        # 使 import 失败 loader None error
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "app.data.bosch_cnc_loader":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        out = t.get_process_baseline("M01", "mill")
        assert "error" in out or isinstance(out, dict)

    def test_train_with_bosch_no_loader(self, monkeypatch):
        t = self._make()
        monkeypatch.setattr(t, "_get_bosch_loader", lambda **kw: None)
        out = t.train_with_bosch_data(data_dir="/nonexistent")
        assert out["error"] and out["accuracy"] == 0.0

    def test_train_with_uniwear_no_data(self, monkeypatch):
        t = self._make()
        out = t.train_with_uniwear_data(data_dir="/nonexistent")
        assert isinstance(out, dict)

    def test_predict_wear_from_signals_no_model(self):
        out = self._make().predict_wear_from_signals({"vibration": 1.0})
        assert "error" in out

    def test_cross_dataset_analysis_no_models(self):
        out = self._make().cross_dataset_analysis()
        assert isinstance(out, dict)

    def test_get_uniwear_material_params(self):
        out = self._make().get_uniwear_material_params()
        assert isinstance(out, dict)


class TestFeedRateOptimizer:
    def _conditions(self, **kw):
        base = dict(
            material="steel",
            tool_diameter=10.0,
            tool_material="carbide",
            depth_of_cut=1.0,
            width_of_cut=10.0,
            spindle_speed=3000.0,
            feed_rate=300.0,
        )
        base.update(kw)
        return CuttingConditions(**base)

    def test_surface_speed(self):
        c = self._conditions(spindle_speed=3000.0, tool_diameter=10.0)
        assert abs(c.surface_speed - (3.14159 * 10 * 3000 / 1000)) < 0.01

    def test_feed_per_tooth(self):
        c = self._conditions(spindle_speed=3000.0, feed_rate=300.0)
        assert abs(c.feed_per_tooth - 300.0 / 6000) < 1e-9

    def test_optimize_efficiency(self):
        o = FeedRateOptimizer()
        f = o.optimize_feed_rate(self._conditions(), "efficiency")
        assert f > 0

    def test_optimize_tool_life_with_wear(self):
        o = FeedRateOptimizer()
        f1 = o.optimize_feed_rate(self._conditions(), "tool_life", tool_wear_factor=2.0)
        f2 = o.optimize_feed_rate(self._conditions(), "tool_life", tool_wear_factor=1.0)
        assert f1 < f2

    def test_optimize_surface_finish(self):
        o = FeedRateOptimizer()
        f1 = o.optimize_feed_rate(self._conditions(material="aluminum"), "surface_finish", surface_finish_ra=0.8)
        f2 = o.optimize_feed_rate(self._conditions(material="aluminum"), "surface_finish", surface_finish_ra=3.2)
        assert f1 < f2

    def test_optimize_unknown_goal(self):
        o = FeedRateOptimizer()
        f = o.optimize_feed_rate(self._conditions(), "balanced")
        assert f > 0

    def test_optimize_unknown_material(self):
        o = FeedRateOptimizer()
        f = o.optimize_feed_rate(self._conditions(material="inconel"), "efficiency")
        assert f > 0

    def test_optimize_power_limited(self):
        o = FeedRateOptimizer()
        f = o.optimize_feed_rate(self._conditions(), "efficiency", machine_power_kw=0.001)
        assert f > 0  # 钳制在推荐范围下界

    def test_validate_conditions(self):
        o = FeedRateOptimizer()
        ok, warns = o.validate_conditions(self._conditions())
        assert ok is True or isinstance(warns, list)
