"""postprocessor/config_loader 覆盖率补强测试。

覆盖：
- ConfigLimiter：主轴转速/进给/坐标轴软限位（安全关键）
- ConfigValidator：类型/范围校验
- ConfigLoader：YAML 配置加载与深度合并
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.postprocessor.config_loader import (
    ConfigLimiter,
    ConfigLoader,
    ConfigValidator,
    _deep_merge,
)

pytestmark = pytest.mark.unit


# ConfigLimiter（安全关键限幅器）


class TestConfigLimiter:
    def test_defaults(self):
        limiter = ConfigLimiter({})
        assert limiter.limit_spindle_rpm(1000) == 1000

    def test_limit_spindle_low_clamps_to_min(self):
        limiter = ConfigLimiter({"spindle": {"min_rpm": 100, "max_rpm": 24000}})
        assert limiter.limit_spindle_rpm(10) == 100  # 低于下限被钳到 min

    def test_limit_spindle_high_clamps_to_max(self):
        limiter = ConfigLimiter({"spindle": {"min_rpm": 100, "max_rpm": 12000}})
        assert limiter.limit_spindle_rpm(99999) == 12000

    def test_limit_spindle_in_range_unchanged(self):
        limiter = ConfigLimiter({"spindle": {"min_rpm": 100, "max_rpm": 24000}})
        assert limiter.limit_spindle_rpm(8000) == 8000

    def test_limit_feed_clamps(self):
        limiter = ConfigLimiter({"feed": {"min_rate": 10, "max_rate": 5000}})
        assert limiter.limit_feed_rate(1) == 10
        assert limiter.limit_feed_rate(99999) == 5000
        assert limiter.limit_feed_rate(1000) == 1000

    def test_limit_axis_x(self):
        limiter = ConfigLimiter({"axis_limits": {"enabled": True, "x_min": -100, "x_max": 100}})
        assert limiter.limit_axis_position("x", -500) == -100
        assert limiter.limit_axis_position("x", 500) == 100
        assert limiter.limit_axis_position("x", 50) == 50

    def test_limit_axis_y_z(self):
        limiter = ConfigLimiter(
            {"axis_limits": {"enabled": True, "y_min": -50, "y_max": 50, "z_min": -10, "z_max": 10}}
        )
        assert limiter.limit_axis_position("y", -999) == -50
        assert limiter.limit_axis_position("z", 999) == 10


# ConfigValidator


class TestConfigValidator:
    def test_valid_config_no_errors(self):
        v = ConfigValidator()
        ok = v._check_type("a", 1, int)
        assert ok is True
        assert v.errors == []

    def test_type_error_recorded(self):
        v = ConfigValidator()
        ok = v._check_type("feed.max_rate", "abc", float)
        assert ok is False
        assert len(v.errors) == 1
        assert "类型错误" in v.errors[0]

    def test_allow_none(self):
        v = ConfigValidator()
        assert v._check_type("a", None, int, allow_none=True) is True

    def test_positive_int(self):
        v = ConfigValidator()
        assert v._check_positive_int("n", 5) is True
        assert v._check_positive_int("n", -5) is False
        assert len(v.errors) == 1

    def test_positive_float_accepts_int(self):
        v = ConfigValidator()
        assert v._check_positive_float("f", 5) is True  # int 自动转 float
        assert v._check_positive_float("f", -0.5) is False


# _deep_merge


class TestDeepMerge:
    def test_merge_scalars(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_merge_nested_dicts(self):
        base = {"spindle": {"min": 100, "max": 24000}}
        override = {"spindle": {"max": 30000}}
        result = _deep_merge(base, override)
        assert result == {"spindle": {"min": 100, "max": 30000}}

    def test_merge_lists_override(self):
        base = {"tools": ["a", "b"]}
        override = {"tools": ["c"]}
        assert _deep_merge(base, override) == {"tools": ["c"]}

    def test_merge_new_keys(self):
        base = {"a": 1}
        override = {"b": 2}
        assert _deep_merge(base, override) == {"a": 1, "b": 2}


# ConfigLoader


class TestConfigLoader:
    def test_load_missing_file_raises(self, tmp_path):
        loader = ConfigLoader()
        with pytest.raises(Exception):
            loader.load(str(tmp_path / "nope.yaml"))

    def test_load_project_config(self):
        # 用仓库真实配置验证完整加载+验证路径
        repo_root = Path(__file__).resolve().parents[4]
        cfg_path = repo_root / "config" / "postprocessor_config.yaml"
        if not cfg_path.exists():
            pytest.skip("仓库真实配置不存在")
        loader = ConfigLoader()
        cfg = loader.load(str(cfg_path), controller_id="fanuc")
        assert "_controller_id" in cfg
        assert "decimal_places" in cfg  # base 段字段已合并到顶层

    def test_load_missing_base_section_raises(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("spindle:\n  min_rpm: 100\n", encoding="utf-8")
        loader = ConfigLoader()
        with pytest.raises(Exception):
            loader.load(str(p))

    def test_clear_cache(self, tmp_path):
        p = tmp_path / "c.yaml"
        p.write_text("base:\n  units: mm\n", encoding="utf-8")
        loader = ConfigLoader()
        ConfigLoader.clear_cache()
        assert ConfigLoader._cache == {}
