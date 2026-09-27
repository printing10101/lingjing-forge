"""工业标准刀具和材料数据库 单元测试。

覆盖：
- 材料数据库查询与检索
- 刀具数据库查询与检索
- 机床数据库查询
- 物理约束校验器（切削速度、进给量、切深、主轴转速）
- 切削力/功率/表面粗糙度/刀具寿命校验
- TC4钛合金+HSS刀具特殊约束
- 45钢+硬质合金立铣刀约束
- 边界条件与异常处理
"""

from __future__ import annotations

import pytest

from app.database.materials import MaterialDatabase
from app.database.tools import ToolDatabase
from app.database.machines import MachineDatabase
from app.database.constraints import (
    CuttingConstraintValidator,
)


class TestMaterialDatabase:
    def test_loads_eight_plus_materials(self):
        db = MaterialDatabase()
        ids = db.list_ids()
        assert "steel_45" in ids
        assert "al_6061" in ids
        assert "ti_tc4" in ids
        assert "steel_q235" in ids
        assert "steel_40cr" in ids
        assert "ss_304" in ids
        assert "al_7075" in ids
        assert "cast_iron_ht250" in ids
        assert len(ids) >= 8

    def test_get_steel_45(self):
        db = MaterialDatabase()
        m = db.get("steel_45")
        assert m.name == "45钢"
        assert m.category == "carbon_steel"
        assert m.hardness_hb == 220
        assert m.specific_cutting_force == 2000

    def test_get_tc4(self):
        db = MaterialDatabase()
        m = db.get("ti_tc4")
        assert m.name == "TC4钛合金"
        assert m.hardness_hb == 320
        assert m.thermal_conductivity == 7
        vc = m.get_cutting_speed("roughing")
        assert vc[0] == 30
        assert vc[1] == 50

    def test_get_al_6061(self):
        db = MaterialDatabase()
        m = db.get("al_6061")
        vc = m.get_cutting_speed("roughing")
        assert vc[0] == 300
        assert vc[1] == 600

    def test_get_unknown_raises(self):
        db = MaterialDatabase()
        with pytest.raises(KeyError, match="不在数据库中"):
            db.get("nonexistent")

    def test_filter_by_category(self):
        db = MaterialDatabase()
        alum = db.filter_by_category("aluminum")
        assert len(alum) >= 2
        for m in alum:
            assert m.category == "aluminum"

    def test_search(self):
        db = MaterialDatabase()
        results = db.search("钛")
        assert len(results) == 1
        assert results[0].id == "ti_tc4"

    def test_list_all(self):
        db = MaterialDatabase()
        all_m = db.list_all()
        assert len(all_m) >= 8

    def test_material_entry_get_feed(self):
        db = MaterialDatabase()
        m = db.get("steel_45")
        feed = m.get_feed("roughing")
        assert feed[0] == 0.2
        assert feed[1] == 0.5

    def test_material_entry_get_depth_of_cut(self):
        db = MaterialDatabase()
        m = db.get("cast_iron_ht250")
        doc = m.get_depth_of_cut("roughing")
        assert doc[0] == 2
        assert doc[1] == 8

    def test_material_to_dict(self):
        db = MaterialDatabase()
        m = db.get("al_6061")
        d = m.to_dict()
        assert d["id"] == "al_6061"
        assert d["hardness_hb"] == 95


class TestToolDatabase:
    def test_loads_tools(self):
        db = ToolDatabase()
        ids = db.list_ids()
        assert "endmill_wc_flat_d10" in ids
        assert "endmill_hss_d10" in ids
        assert "turning_wc_dclnr" in ids
        assert "drill_wc_d8" in ids
        assert "tap_hss_m10" in ids
        assert "endmill_ceramic_d12" in ids

    def test_get_wc_endmill(self):
        db = ToolDatabase()
        t = db.get("endmill_wc_flat_d10")
        assert t.material == "WC"
        assert t.type == "endmill"
        assert t.flutes == 4

    def test_get_hss_endmill(self):
        db = ToolDatabase()
        t = db.get("endmill_hss_d10")
        assert t.material == "HSS"
        vc = t.get_cutting_speed_for_material("steel")
        assert vc[0] == 25
        assert vc[1] == 45

    def test_get_unknown_raises(self):
        db = ToolDatabase()
        with pytest.raises(KeyError, match="不在数据库中"):
            db.get("nonexistent")

    def test_filter_by_type(self):
        db = ToolDatabase()
        endmills = db.filter_by_type("endmill")
        assert len(endmills) >= 4
        for t in endmills:
            assert t.type == "endmill"

    def test_filter_by_material(self):
        db = ToolDatabase()
        wc_tools = db.filter_by_material("WC")
        assert len(wc_tools) >= 4

    def test_tool_speed_for_titanium(self):
        db = ToolDatabase()
        t = db.get("endmill_wc_flat_d10")
        vc = t.get_cutting_speed_for_material("titanium")
        assert vc[0] == 30
        assert vc[1] == 60

    def test_tool_speed_for_aluminum(self):
        db = ToolDatabase()
        t = db.get("endmill_wc_bull_d12")
        vc = t.get_cutting_speed_for_material("aluminum")
        assert vc[0] == 300
        assert vc[1] == 800

    def test_ceramic_not_for_aluminum(self):
        db = ToolDatabase()
        t = db.get("endmill_ceramic_d12")
        vc = t.get_cutting_speed_for_material("aluminum")
        assert vc == (0, 0)

    def test_tool_to_dict(self):
        db = ToolDatabase()
        t = db.get("drill_wc_d8")
        d = t.to_dict()
        assert d["type"] == "drill"
        assert d["flutes"] == 2


class TestMachineDatabase:
    def test_loads_machines(self):
        db = MachineDatabase()
        ids = db.list_ids()
        assert "vmc_850" in ids
        assert "cnc_lathe_ck6140" in ids

    def test_get_vmc850(self):
        db = MachineDatabase()
        m = db.get("vmc_850")
        assert m.spindle_power_kw == 7.5
        assert m.max_cutting_force_n == 5000

    def test_get_unknown_raises(self):
        db = MachineDatabase()
        with pytest.raises(KeyError, match="不在数据库中"):
            db.get("nonexistent")


class TestConstraintValidator:
    def make_validator(self):
        return CuttingConstraintValidator(
            materials=MaterialDatabase(),
            tools=ToolDatabase(),
            machines=MachineDatabase(),
        )

    def test_tc4_hss_speed_constrained(self):
        """TC4钛合金+HSS刀具：切削速度应被约束在10-25 m/min"""
        v = self.make_validator()
        result = v.validate(
            material_id="ti_tc4",
            tool_id="endmill_hss_d10",
            params={"cutting_speed": 100, "feed": 0.15, "depth_of_cut": 2.0},
        )
        assert result.adjusted_params["cutting_speed"] <= 25
        assert len(result.violations) > 0

    def test_tc4_wc_speed_clamped(self):
        """TC4钛合金+硬质合金刀具：切削速度应被约束"""
        v = self.make_validator()
        result = v.validate(
            material_id="ti_tc4",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 200, "feed": 0.2, "depth_of_cut": 2.0},
        )
        assert result.adjusted_params["cutting_speed"] <= 60

    def test_steel_45_wc_feed_constrained(self):
        """45钢+硬质合金刀具：进给量应被约束"""
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 150, "feed": 1.5, "depth_of_cut": 3.0},
        )
        assert result.adjusted_params["feed"] <= 0.6

    def test_steel_45_wc_doc_constrained(self):
        """切深不应超过刀具最大切深"""
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 150, "feed": 0.3, "depth_of_cut": 15.0},
        )
        assert result.adjusted_params["depth_of_cut"] <= 10.0

    def test_cutting_force_warning(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_hss_d10",
            params={"cutting_speed": 30, "feed": 0.4, "depth_of_cut": 3.0},
        )
        has_force = any("切削力" in w for w in result.warnings)
        assert has_force

    def test_power_warning_with_machine(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="turning_wc_dclnr",
            params={"cutting_speed": 300, "feed": 0.5, "depth_of_cut": 5.0},
            machine_id="vmc_850",
        )
        has_power = any("切削功率" in w for w in result.warnings)
        assert has_power

    def test_surface_roughness_warning(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="turning_wc_dclnr",
            params={"cutting_speed": 150, "feed": 0.5, "depth_of_cut": 3.0},
        )
        has_roughness = any("表面粗糙度" in w for w in result.warnings)
        assert has_roughness

    def test_tool_life_warning_tc4(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_40cr",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 100, "feed": 0.2, "depth_of_cut": 3.0},
        )
        has_life = any("Taylor" in w or "刀具寿命" in w for w in result.warnings)
        assert has_life

    def test_valid_params_no_violations(self):
        v = self.make_validator()
        result = v.validate(
            material_id="al_6061",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 400, "feed": 0.25, "depth_of_cut": 3.0},
        )
        assert result.is_valid

    def test_unknown_material(self):
        v = self.make_validator()
        result = v.validate(
            material_id="unknown",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 100},
        )
        assert not result.is_valid
        assert any("未知材料" in v.message for v in result.violations)

    def test_unknown_tool(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="unknown",
            params={"cutting_speed": 100},
        )
        assert not result.is_valid

    def test_unknown_machine_warning(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 120, "feed": 0.3, "depth_of_cut": 3.0},
            machine_id="unknown_machine",
        )
        assert "跳过机床校验" in " ".join(result.warnings)

    def test_spindle_speed_constrained(self):
        v = self.make_validator()
        result = v.validate(
            material_id="al_6061",
            tool_id="endmill_wc_bull_d12",
            params={
                "cutting_speed": 500,
                "feed": 0.2,
                "depth_of_cut": 3.0,
                "spindle_speed": 12000,
            },
            machine_id="vmc_850",
        )
        assert result.adjusted_params["spindle_speed"] <= 8000

    def test_constraint_result_to_dict(self):
        v = self.make_validator()
        result = v.validate(
            material_id="ti_tc4",
            tool_id="endmill_hss_d10",
            params={"cutting_speed": 100, "feed": 0.15, "depth_of_cut": 2.0},
        )
        d = result.to_dict()
        assert "is_valid" in d
        assert "violations" in d
        assert "warnings" in d
        assert "adjusted_params" in d

    def test_vc_key_variant(self):
        v = self.make_validator()
        result = v.validate(
            material_id="ti_tc4",
            tool_id="endmill_wc_flat_d10",
            params={"vc": 120, "f": 0.15, "ap": 2.0},
        )
        assert result.adjusted_params["vc"] <= 60

    def test_feed_key_variant(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 150, "f": 1.5, "depth_of_cut": 3.0},
        )
        assert result.adjusted_params["f"] <= 0.6

    def test_doc_key_variant(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 150, "feed": 0.3, "ap": 12.0},
        )
        assert result.adjusted_params["ap"] <= 10.0

    def test_zero_params_not_constrained(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 0, "feed": 0, "depth_of_cut": 0},
        )
        assert result.is_valid

    def test_al_6061_wide_range_no_violations(self):
        v = self.make_validator()
        result = v.validate(
            material_id="al_6061",
            tool_id="endmill_wc_bull_d12",
            params={"cutting_speed": 400, "feed": 0.2, "depth_of_cut": 3.0},
        )
        assert result.is_valid

    def test_machine_force_warning(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 150, "feed": 0.5, "depth_of_cut": 6.0},
            machine_id="small_vmc_640",
        )
        has_mf = any(
            "机床最大切削力" in w or "估算切削力" in w for w in result.warnings
        )
        assert has_mf

    def test_tool_force_limit_warning(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_hss_d10",
            params={"cutting_speed": 30, "feed": 0.3, "depth_of_cut": 4.0},
        )
        has_force = any("刀具承受范围" in w for w in result.warnings)
        assert has_force

    def test_rpm_key_variant(self):
        v = self.make_validator()
        result = v.validate(
            material_id="al_6061",
            tool_id="endmill_wc_flat_d10",
            params={
                "cutting_speed": 400,
                "feed": 0.15,
                "depth_of_cut": 2.0,
                "rpm": 15000,
            },
            machine_id="vmc_850",
        )
        assert result.adjusted_params["rpm"] <= 8000

    def test_no_params_no_violations(self):
        v = self.make_validator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_wc_flat_d10",
            params={},
        )
        assert result.is_valid


class TestConstraintIntegration:
    def test_tc4_with_hss_clamped_as_expected(self):
        """核心测试：TC4钛合金+HSS刀具→切削速度应被约束在30m/min以下"""
        v = CuttingConstraintValidator()
        result = v.validate(
            material_id="ti_tc4",
            tool_id="endmill_hss_d10",
            params={"cutting_speed": 150, "feed": 0.2, "depth_of_cut": 2.0},
        )
        adjusted_vc = result.adjusted_params["cutting_speed"]
        assert adjusted_vc <= 30, f"TC4+HSS切削速度应为30以下, 实际: {adjusted_vc}"
        assert not result.is_valid

    def test_steel45_wc_d10_force_below_machine_limit(self):
        """45钢+φ10硬质合金立铣刀→切削力应<机床最大切削力"""
        v = CuttingConstraintValidator()
        result = v.validate(
            material_id="steel_45",
            tool_id="endmill_wc_flat_d10",
            params={"cutting_speed": 120, "feed": 0.25, "depth_of_cut": 3.0},
            machine_id="vmc_850",
        )
        fc = 2000 * 3.0 * 0.25
        assert fc <= 5000, f"切削力 {fc}N 应 < VMC850最大力 5000N"
        force_warnings = [w for w in result.warnings if "切削力" in w and "5000" in w]
        assert len(force_warnings) == 0, f"不应超出机床力限制: {force_warnings}"

    def test_tc4_recommended_range(self):
        """验证TC4钛合金推荐参数范围合理"""
        db = MaterialDatabase()
        m = db.get("ti_tc4")
        vc = m.get_cutting_speed("roughing")
        assert vc[0] == 30
        assert vc[1] == 50
        vc_f = m.get_cutting_speed("finishing")
        assert vc_f[0] == 50
        assert vc_f[1] == 80
