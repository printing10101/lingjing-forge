"""Unit tests for Siemens 840D / Heidenhain TNC640 hooks 声明方言（Phase E）。

验证：
1. hooks 类方法正确格式化 CYCLE / CYCL DEF 指令（纯逻辑，无框架依赖）
2. 声明 YAML 结构合法（id/extends/hooks/params 字段齐备）
3. hooks 方法可被 DialectCompiler 加载（方法名 format_* 提取约定）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# 方言 hooks 模块是纯 Python，直接从插件目录导入（模拟编译器加载路径）
_PLUGIN_ROOT = Path(__file__).resolve().parents[4] / "postprocessor-plugins"
_SIEMENS_HOOKS = Path(_PLUGIN_ROOT) / "siemens_840d" / "hooks.py"
_HEIDENHAIN_HOOKS = Path(_PLUGIN_ROOT) / "heidenhain_tnc640" / "hooks.py"


def _load_hooks_module(path: Path, module_name: str):
    """模拟 DialectCompiler._load_hook_methods 的模块加载方式。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"无法加载 {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def siemens_hooks():
    return _load_hooks_module(_SIEMENS_HOOKS, "siemens_840d_hooks_test")


@pytest.fixture(scope="module")
def heidenhain_hooks():
    return _load_hooks_module(_HEIDENHAIN_HOOKS, "heidenhain_tnc640_hooks_test")


# 声明 YAML 结构


class TestDialectDeclarations:
    @pytest.mark.parametrize(
        "rel_path,expected_id",
        [
            ("siemens_840d/dialect.yaml", "siemens_840d_declared"),
            ("heidenhain_tnc640/dialect.yaml", "heidenhain_tnc640_declared"),
        ],
    )
    def test_yaml_structure(self, rel_path: str, expected_id: str) -> None:
        path = _PLUGIN_ROOT / rel_path
        assert path.exists(), f"缺少声明文件 {path}"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["id"] == expected_id
        assert data["version"]
        assert data["extends"]
        assert isinstance(data["hooks"], list)
        assert len(data["hooks"]) >= 1
        for hook in data["hooks"]:
            assert hook["module"]
            assert hook["class"]
        assert isinstance(data["params"], dict)

    def test_siemens_hooks_have_format_methods(self) -> None:
        path = _PLUGIN_ROOT / "siemens_840d" / "dialect.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        # hooks 应覆盖 CYCLE 系列关键方法
        assert any("Siemens840DHooks" in h["class"] for h in data["hooks"])
        assert any("Siemens840DHeaderHooks" in h["class"] for h in data["hooks"])

    def test_heidenhain_hooks_have_format_methods(self) -> None:
        path = _PLUGIN_ROOT / "heidenhain_tnc640" / "dialect.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert any("HeidenhainTNC640Hooks" in h["class"] for h in data["hooks"])
        assert any("HeidenhainHeaderHooks" in h["class"] for h in data["hooks"])


# Siemens 840D hooks 逻辑


class _SiemensContext:
    """模拟方言实例（hooks 依赖的最小上下文）。"""

    def __init__(self) -> None:
        self.rapid_feed = 5000.0
        self.safe_z_height = 80.0
        self.program_number = 1000
        self._decimal_places = 3

    def _fmt(self, value: float) -> str:
        return f"{value:.{self._decimal_places}f}"

    def get_spindle_rpm(self) -> float:
        return 8000.0

    def get_feed_rate(self, rpm: float) -> float:
        return rpm * 0.2  # 0.2 mm/rev

    def get_cycle_config(self, name: str, default_code: str) -> dict:
        return {"retract": 2.0, "peck_depth": 5.0, "code": default_code}


class TestSiemens840DHooks:
    def test_cycle81_plain_drill(self, siemens_hooks) -> None:
        ctx = _SiemensContext()
        h = siemens_hooks.Siemens840DHooks()
        # 绑定上下文
        for name in ("_fmt", "get_spindle_rpm", "get_feed_rate", "get_cycle_config"):
            setattr(h, name, getattr(ctx, name))
        out = h.format_cycle_drill(10.0, 20.0, 5.0, 15.0, pecking=False)
        assert "CYCLE81(5.000, 2.000, -15.000)" in out

    def test_cycle83_peck_drill(self, siemens_hooks) -> None:
        ctx = _SiemensContext()
        h = siemens_hooks.Siemens840DHooks()
        for name in ("_fmt", "get_spindle_rpm", "get_feed_rate", "get_cycle_config"):
            setattr(h, name, getattr(ctx, name))
        out = h.format_cycle_drill(10.0, 20.0, 5.0, 15.0, pecking=True)
        assert out.startswith("CYCLE83(")
        assert "-15.000" in out
        assert "5.000" in out  # peck_depth

    def test_cycle84_tapping(self, siemens_hooks) -> None:
        ctx = _SiemensContext()
        h = siemens_hooks.Siemens840DHooks()
        for name in ("_fmt", "get_spindle_rpm", "get_feed_rate", "get_cycle_config"):
            setattr(h, name, getattr(ctx, name))
        out = h.format_cycle_tapping(10.0, 20.0, 5.0, 12.0)
        assert out.startswith("CYCLE84(")
        assert "8000" in out  # rpm int

    def test_cycle85_boring(self, siemens_hooks) -> None:
        ctx = _SiemensContext()
        h = siemens_hooks.Siemens840DHooks()
        for name in ("_fmt", "get_spindle_rpm", "get_feed_rate", "get_cycle_config"):
            setattr(h, name, getattr(ctx, name))
        out = h.format_cycle_boring(10.0, 20.0, 5.0, 15.0)
        assert out.startswith("CYCLE85(")

    def test_tool_change_uses_d_comp(self, siemens_hooks) -> None:
        h = siemens_hooks.Siemens840DHooks()
        out = h.format_tool_change(12)
        assert out == "T12 D1"
        assert "H" not in out  # Siemens 无 H 补偿

    def test_header_mpf_format(self, siemens_hooks) -> None:
        h = siemens_hooks.Siemens840DHeaderHooks()
        # 签名与基类约定对齐：程序号由 format_header(program_number) 传入
        out = h.format_header(1000)
        assert "%_N_1000_MPF" in out
        assert ";$PATH=/_N_MPF_DIR" in out
        assert "TRAFOF" in out
        assert "G90 G71 G94" in out


# Heidenhain TNC640 hooks 逻辑


class TestHeidenhainTNC640Hooks:
    def test_cyc_def_200_plain_drill(self, heidenhain_hooks) -> None:
        ctx = _SiemensContext()
        h = heidenhain_hooks.HeidenhainTNC640Hooks()
        for name in ("_fmt", "get_spindle_rpm", "get_feed_rate", "get_cycle_config", "rapid_feed"):
            if hasattr(ctx, name):
                setattr(h, name, getattr(ctx, name))
        out = h.format_cycle_drill(10.0, 20.0, 5.0, 15.0, pecking=False)
        assert "CYCL DEF 200 DRILLING" in out
        assert "Q201=-15.000" in out

    def test_cyc_def_241_peck_drill(self, heidenhain_hooks) -> None:
        ctx = _SiemensContext()
        h = heidenhain_hooks.HeidenhainTNC640Hooks()
        for name in ("_fmt", "get_spindle_rpm", "get_feed_rate", "get_cycle_config", "rapid_feed"):
            if hasattr(ctx, name):
                setattr(h, name, getattr(ctx, name))
        out = h.format_cycle_drill(10.0, 20.0, 5.0, 15.0, pecking=True)
        assert "CYCL DEF 241 DRILLING DEEP" in out
        assert "Q202=5.000" in out  # peck depth

    def test_cyc_def_240_tapping(self, heidenhain_hooks) -> None:
        ctx = _SiemensContext()
        h = heidenhain_hooks.HeidenhainTNC640Hooks()
        for name in ("_fmt", "get_spindle_rpm", "get_feed_rate", "get_cycle_config", "rapid_feed"):
            if hasattr(ctx, name):
                setattr(h, name, getattr(ctx, name))
        # 签名与基类约定对齐：第 5 个参数是 pitch（旧实现此处是 dwell 且
        # Q239 硬编码 1.5，与基类 format_cycle_tapping 调用约定不一致）
        out = h.format_cycle_tapping(10.0, 20.0, 5.0, 12.0, pitch=1.5)
        assert "CYCL DEF 240 TAPPING" in out
        assert "Q239=1.500" in out

    def test_tool_call_format(self, heidenhain_hooks) -> None:
        ctx = _SiemensContext()
        h = heidenhain_hooks.HeidenhainTNC640Hooks()
        for name in ("_fmt", "get_spindle_rpm", "get_feed_rate", "get_cycle_config"):
            if hasattr(ctx, name):
                setattr(h, name, getattr(ctx, name))
        out = h.format_tool_change(5)
        assert out.startswith("TOOL CALL 05 Z S")
        assert "S8000" in out

    def test_probe_format(self, heidenhain_hooks) -> None:
        ctx = _SiemensContext()
        h = heidenhain_hooks.HeidenhainTNC640Hooks()
        for name in ("_fmt", "get_spindle_rpm", "get_feed_rate", "get_cycle_config"):
            if hasattr(ctx, name):
                setattr(h, name, getattr(ctx, name))
        out = h.format_probe(1, 12.5)
        assert out == "TCH PROBE 1 X12.500"

    def test_begin_end_pgm(self, heidenhain_hooks) -> None:
        h = heidenhain_hooks.HeidenhainHeaderHooks()
        # 签名与基类约定对齐：format_header(program_number) 记录程序号，
        # format_footer() 复用之（旧实现无参 header + 实例属性回退 1000）
        out = h.format_header(1000)
        assert out == "BEGIN PGM 1000 MM"
        out2 = h.format_footer()
        assert out2 == "END PGM 1000 MM"
