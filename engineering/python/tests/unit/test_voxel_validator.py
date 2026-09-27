"""体素材料去除仿真校验器单元测试（阶段 7 闭环强制层）。

覆盖范围：
- 安全程序通过（快速定位在安全高度、切削在毛坯内）
- 快速下扎（G00 切入材料）被判定碰撞（critical）
- 过切（切削段低于毛坯底面）被判定碰撞
- 空程序（无运动段）视为通过 + 警告
- G 代码解析失败抛 VoxelValidationError
- 运动段数超过上限抛 VoxelValidationError（fail-closed，拒绝部分仿真）
- safe_z 不高于 stock_top_z 抛 VoxelValidationError
- 报告结构契约（to_dict 字段完整性 / collision_blocks 归因素材）
- 首段虚拟起点豁免（(0,0,0) 模态起点不产生误报）

测试设计原则：
- 使用小毛坯 + 粗体素（2.0mm）保证纯 Python 回退路径也秒级完成
- 不依赖 trimesh / STL / Rust 原生模块（合成盒状体素网格）
"""

from __future__ import annotations

import pytest

from app.cam_validation import VoxelValidationError
from app.cam_validation.voxel_validator import VoxelValidator
from app.config import CamValidationConfig

# 测试用小毛坯（mm）：40 x 40 x 20，safe_z=50（高于毛坯顶面 20mm）
STOCK = dict(stock_length=40.0, stock_width=40.0, stock_height=20.0, stock_top_z=20.0, safe_z=50.0)

# 安全程序：首段快速定位（虚拟起点豁免）→ 安全高度横移 → 分层切削 → 退刀
SAFE_GCODE = """G90 G21 G17
G00 X20 Y20 Z45
G00 X20 Y20
G01 Z15 F200
G00 Z45
G00 X30 Y30
G01 Z15 F200
G00 Z45
M30
"""

# 快速下扎：G00 直接切入毛坯内部（安全高度以下、材料未切除）
RAPID_PLUNGE_GCODE = """G90 G21 G17
G00 X20 Y20 Z45
G00 Z5
G01 Z2 F100
G00 Z45
M30
"""

# 过切：切削段低于毛坯底面（Z < 0）
OVERCUT_GCODE = """G90 G21 G17
G00 X20 Y20 Z45
G01 Z-5 F100
G00 Z45
M30
"""


def _make_validator(**config_overrides) -> VoxelValidator:
    cfg = CamValidationConfig(**config_overrides)
    return VoxelValidator(cfg)


def _validate(gcode: str, **overrides):
    validator = _make_validator(**overrides)
    return validator.validate(gcode_text=gcode, controller_type="fanuc_0i", **STOCK)


class TestVoxelValidatorSafeProgram:
    """安全程序应通过体素仿真。"""

    @pytest.mark.unit
    def test_safe_program_passes(self):
        """分层切削 + 安全高度横移的程序无碰撞。"""
        report = _validate(SAFE_GCODE)
        assert report.passed is True
        assert report.collision_count == 0
        assert report.collision_blocks == []
        assert report.severity == "none"
        assert report.removed_voxel_count > 0, "切削应移除材料体素"
        assert report.engine in ("rust", "python")
        assert report.voxel_count > 0

    @pytest.mark.unit
    def test_first_virtual_start_segment_exempt(self):
        """首段从虚拟原点 (0,0,0) 出发的快速定位不应误报碰撞。"""
        # 首段 G00 X20 Y20 Z45 从模态起点 (0,0,0) 出发，穿过毛坯角点；
        # 若不豁免首段，此程序会被误判为快移碰撞。
        report = _validate(SAFE_GCODE)
        assert report.passed is True

    @pytest.mark.unit
    def test_empty_program_passes_with_warning(self):
        """无运动段（空程序）视为通过 + 警告告知。"""
        report = _validate("G90 G21\nM30\n")
        assert report.passed is True
        assert report.total_segments == 0
        assert any("无运动段" in w for w in report.warnings)

    @pytest.mark.unit
    def test_report_to_dict_contract(self):
        """to_dict 字段完整（供 cam_report.json 导出与前端消费）。"""
        report = _validate(SAFE_GCODE)
        d = report.to_dict()
        expected_keys = {
            "passed",
            "engine",
            "voxel_size_mm",
            "total_segments",
            "cutting_segments",
            "collision_count",
            "collision_blocks",
            "collision_positions",
            "severity",
            "removed_voxel_count",
            "voxel_count",
            "duration_seconds",
            "warnings",
        }
        assert expected_keys <= set(d.keys()), f"to_dict 缺失字段: {expected_keys - set(d.keys())}"


class TestVoxelValidatorCollisionDetection:
    """碰撞检测：快移下扎与过切必须被拦截。"""

    @pytest.mark.unit
    def test_rapid_plunge_into_material_rejected(self):
        """G00 快速下扎进未切除材料 → 碰撞（critical），记录涉事 block。"""
        report = _validate(RAPID_PLUNGE_GCODE)
        assert report.passed is False
        assert report.collision_count > 0
        assert report.severity == "critical"
        # 下扎发生在第 3 行（G00 Z5），block_number 应被记录供特征归因
        assert 3 in report.collision_blocks, f"collision_blocks={report.collision_blocks}"

    @pytest.mark.unit
    def test_overcut_below_stock_bottom_rejected(self):
        """切削段低于毛坯底面（过切）→ 碰撞。"""
        report = _validate(OVERCUT_GCODE)
        assert report.passed is False
        assert report.collision_count > 0
        assert report.severity == "critical"
        # 过切发生在第 3 行（G01 Z-5）
        assert 3 in report.collision_blocks, f"collision_blocks={report.collision_blocks}"

    @pytest.mark.unit
    def test_collision_positions_capped(self):
        """碰撞坐标采样截断到上限（与 VoxelCutter 导出口径一致）。"""
        report = _validate(OVERCUT_GCODE)
        assert len(report.collision_positions) <= 20


class TestVoxelValidatorFailClosed:
    """fail-closed 语义：无法完整仿真时必须拒绝而不是冒险放行。"""

    @pytest.mark.unit
    def test_unparseable_gcode_raises(self, monkeypatch):
        """G 代码解析失败 → VoxelValidationError（任务将 FAILED）。"""

        def _boom(*args, **kwargs):
            raise ValueError("模拟解析器内部错误")

        monkeypatch.setattr("app.cam_validation.voxel_validator.ToolpathParser", _boom)
        validator = _make_validator()
        with pytest.raises(VoxelValidationError, match="解析"):
            validator.validate(gcode_text=SAFE_GCODE, controller_type="fanuc_0i", **STOCK)

    @pytest.mark.unit
    def test_segment_cap_rejects(self):
        """运动段数超上限 → VoxelValidationError（拒绝部分仿真）。

        注意配置下限约束：voxel_max_segments < 100 会被 __post_init__
        回退到 50000，因此用合法最小值 100 + 150 段程序触发。
        """
        validator = _make_validator(voxel_max_segments=100)
        many_moves = "G90 G21\nG00 X20 Y20 Z45\n" + "\n".join(
            f"G01 X{i % 30 + 5} Y{i % 25 + 5} Z18 F200" for i in range(150)
        )
        assert len(many_moves.splitlines()) - 2 > 100  # 除去首尾非运动行仍超限
        with pytest.raises(VoxelValidationError, match="上限"):
            validator.validate(gcode_text=many_moves, controller_type="fanuc_0i", **STOCK)

    @pytest.mark.unit
    def test_safe_z_not_above_stock_top_raises(self):
        """safe_z 不高于 stock_top_z → VoxelValidationError。"""
        validator = _make_validator()
        with pytest.raises(VoxelValidationError, match="safe_z"):
            validator.validate(
                gcode_text=SAFE_GCODE,
                controller_type="fanuc_0i",
                stock_length=40.0,
                stock_width=40.0,
                stock_height=20.0,
                stock_top_z=20.0,
                safe_z=20.0,  # 等于 stock_top_z → 非法
            )


class TestVoxelValidatorConfigContract:
    """体素仿真配置参数契约（无开关——硬约束，仅性能旋钮）。"""

    @pytest.mark.unit
    def test_config_defaults(self):
        """默认参数：1.0mm 体素 / 10mm 平底刀 / 50000 段上限。"""
        cfg = CamValidationConfig()
        assert cfg.voxel_size_mm == 1.0
        assert cfg.voxel_tool_diameter_mm == 10.0
        assert cfg.voxel_tool_type == "flat"
        assert cfg.voxel_max_segments == 50000

    @pytest.mark.unit
    def test_config_invalid_values_fall_back(self):
        """非法参数回退默认值（__post_init__ 校验）。"""
        cfg = CamValidationConfig(
            voxel_size_mm=99.0,
            voxel_tool_diameter_mm=-5.0,
            voxel_tool_type="lightsaber",
            voxel_max_segments=1,
        )
        assert cfg.voxel_size_mm == 1.0
        assert cfg.voxel_tool_diameter_mm == 10.0
        assert cfg.voxel_tool_type == "flat"
        assert cfg.voxel_max_segments == 50000


class TestVoxelValidatorToolOverride:
    """刀具实参透传：实际装刀直径覆盖配置默认值，来源如实标注。"""

    @pytest.mark.unit
    def test_actual_tool_overrides_config(self):
        """传入实际刀具直径/类型 → 仿真使用实参，tool_source=actual。"""
        validator = _make_validator()
        report = validator.validate(
            gcode_text=SAFE_GCODE,
            controller_type="fanuc_0i",
            **STOCK,
            tool_diameter_mm=8.0,
            tool_type="ball",
        )
        assert report.passed is True
        assert report.tool_diameter_mm == 8.0
        assert report.tool_type == "ball"
        assert report.tool_source == "actual"
        d = report.to_dict()
        assert d["tool_diameter_mm"] == 8.0
        assert d["tool_source"] == "actual"

    @pytest.mark.unit
    def test_declared_report_source_passthrough(self):
        """调用方声明来源（阶段 6 携带）→ report.tool_source 原样落盘。"""
        validator = _make_validator()
        report = validator.validate(
            gcode_text=SAFE_GCODE,
            controller_type="fanuc_0i",
            **STOCK,
            tool_diameter_mm=6.0,
            tool_source="report",
        )
        assert report.tool_diameter_mm == 6.0
        assert report.tool_source == "report"

    @pytest.mark.unit
    def test_default_tool_source_is_config(self):
        """未传实参 → 回退配置默认（10mm flat），tool_source=config_default。"""
        report = _validate(SAFE_GCODE)
        assert report.tool_diameter_mm == 10.0
        assert report.tool_type == "flat"
        assert report.tool_source == "config_default"

    @pytest.mark.unit
    def test_partial_override_keeps_config_tool_type(self):
        """只传直径不传类型 → 直径用实参，类型回退配置默认。"""
        validator = _make_validator()
        report = validator.validate(
            gcode_text=SAFE_GCODE,
            controller_type="fanuc_0i",
            **STOCK,
            tool_diameter_mm=6.0,
        )
        assert report.tool_diameter_mm == 6.0
        assert report.tool_type == "flat"
        assert report.tool_source == "actual"

    @pytest.mark.unit
    def test_invalid_tool_type_falls_back_with_warning(self):
        """类型不在 ToolModel 白名单 → 回退配置默认 + 警告，直径仍用实参（不炸闸门）。"""
        validator = _make_validator()
        report = validator.validate(
            gcode_text=SAFE_GCODE,
            controller_type="fanuc_0i",
            **STOCK,
            tool_diameter_mm=6.0,
            tool_type="lightsaber",
        )
        assert report.passed is True
        assert report.tool_diameter_mm == 6.0
        assert report.tool_type == "flat"
        assert report.tool_source == "actual"
        assert any("lightsaber" in w and "回退" in w for w in report.warnings)
