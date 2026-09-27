"""toolpath/collision_checker + process_planning/physics_validator 覆盖率补强测试。

碰撞检测：几何体格式解析（bbox/bounding_box/get_bbox/dict/元组）、
AABB 相交判定、单点与整路径碰撞聚合、严重度升级。
物理校验：切削力经验模型（车/铣/钻）、机床能力边界、参数建议迭代收敛。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from app.toolpath.collision_checker import CollisionChecker, CollisionResult
from app.process_planning.physics_validator import CuttingForceResult, MachineCapability, PhysicsValidator


# ---------------------------------------------------------------- CollisionResult


class TestCollisionResult:
    def test_defaults(self):
        r = CollisionResult()
        assert r.collided is False
        assert r.collision_points == []
        assert r.collision_segments == []
        assert r.severity == "none"

    def test_to_dict(self):
        r = CollisionResult(
            collided=True,
            collision_points=[(1.0, 2.0, 3.0)],
            collision_segments=[0],
            severity="critical",
        )
        d = r.to_dict()
        assert d["collided"] is True
        assert d["collision_points"] == [(1.0, 2.0, 3.0)]
        assert d["severity"] == "critical"


# ---------------------------------------------------------------- _extract_bbox


class _Geom:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _BBoxMethod:
    def __init__(self, bbox, exc=None):
        self._bbox = bbox
        self._exc = exc

    def get_bbox(self):
        if self._exc:
            raise self._exc
        return self._bbox


class TestExtractBbox:
    def test_bbox_attribute(self):
        g = _Geom(bbox=(0, 0, 0, 10, 10, 10))
        assert CollisionChecker._extract_bbox(g) == (0, 0, 0, 10, 10, 10)

    def test_bounding_box_attribute(self):
        g = _Geom(bounding_box=[1, 2, 3, 4, 5, 6])
        assert CollisionChecker._extract_bbox(g) == (1, 2, 3, 4, 5, 6)

    def test_get_bbox_method(self):
        assert CollisionChecker._extract_bbox(_BBoxMethod((0, 0, 0, 1, 1, 1))) == (0, 0, 0, 1, 1, 1)

    def test_get_bbox_exception(self):
        for exc in (AttributeError("x"), TypeError("x"), ValueError("x")):
            assert CollisionChecker._extract_bbox(_BBoxMethod(None, exc)) is None

    def test_dict_bbox(self):
        assert CollisionChecker._extract_bbox({"bbox": [0, 0, 0, 5, 5, 5]}) == (0, 0, 0, 5, 5, 5)

    def test_dict_min_max(self):
        assert CollisionChecker._extract_bbox({"min": [1, 2, 3], "max": [4, 5, 6]}) == (1, 2, 3, 4, 5, 6)

    def test_dict_incomplete(self):
        assert CollisionChecker._extract_bbox({"min": [1, 2, 3]}) is None
        assert CollisionChecker._extract_bbox({}) is None

    def test_tuple_bbox(self):
        assert CollisionChecker._extract_bbox((0, 0, 0, 2, 2, 2)) == (0, 0, 0, 2, 2, 2)

    def test_unconvertible_values(self):
        # 长度 6 但无法转 float None
        assert CollisionChecker._extract_bbox((0, 0, 0, "x", 2, 2)) is None

    def test_position_offset(self):
        assert CollisionChecker._extract_bbox((0, 0, 0, 1, 1, 1), position=(10, 20, 30)) == (10, 20, 30, 11, 21, 31)

    def test_none_geometry(self):
        assert CollisionChecker._extract_bbox(None) is None
        assert CollisionChecker._extract_bbox(42) is None


# ---------------------------------------------------------------- _aabb_intersect


class TestAabbIntersect:
    def test_intersect_full_overlap(self):
        assert CollisionChecker._aabb_intersect((0, 0, 0, 10, 10, 10), (2, 2, 2, 8, 8, 8))

    def test_intersect_partial(self):
        assert CollisionChecker._aabb_intersect((0, 0, 0, 5, 5, 5), (4, 4, 4, 9, 9, 9))

    def test_no_intersect_x(self):
        assert not CollisionChecker._aabb_intersect((0, 0, 0, 1, 1, 1), (2, 0, 0, 3, 1, 1))

    def test_no_intersect_y(self):
        assert not CollisionChecker._aabb_intersect((0, 0, 0, 1, 1, 1), (0, 2, 0, 1, 3, 1))

    def test_no_intersect_z(self):
        assert not CollisionChecker._aabb_intersect((0, 0, 0, 1, 1, 1), (0, 0, 2, 1, 1, 3))

    def test_touching_edges_intersect(self):
        # 边界相接视为相交（<= 判定）
        assert CollisionChecker._aabb_intersect((0, 0, 0, 1, 1, 1), (1, 0, 0, 2, 1, 1))


# ---------------------------------------------------------------- CollisionChecker


class _ToolGeom:
    bbox = (-1, -1, -1, 1, 1, 1)


class TestCollisionChecker:
    def _make(self):
        return CollisionChecker()

    def test_set_and_clear(self):
        c = self._make()
        c.set_workpiece(_Geom(bbox=(0, 0, 0, 10, 10, 10)))
        assert c._workpiece is not None
        c.add_fixture(_Geom(bbox=(20, 20, 20, 30, 30, 30)))
        c.add_fixture(_Geom(bbox=(40, 40, 40, 50, 50, 50)))
        c.set_tool_holder(_Geom(bbox=(0, 0, 0, 2, 2, 2)))
        assert len(c._fixtures) == 2
        assert c._tool_holder is not None
        c.clear()
        assert c._workpiece is None
        assert c._fixtures == []
        assert c._tool_holder is None

    def test_check_collision_no_tool_bbox(self):
        c = self._make()
        r = c.check_collision((0, 0, 0), "not-geometry")
        assert not r.collided
        assert r.severity == "none"

    def test_collision_with_workpiece_critical(self):
        c = self._make()
        c.set_workpiece(_Geom(bbox=(0, 0, 0, 10, 10, 10)))
        r = c.check_collision((5, 5, 5), _ToolGeom())
        assert r.collided
        assert (5, 5, 5) in r.collision_points
        assert r.severity == "critical"

    def test_collision_with_fixture_critical(self):
        c = self._make()
        c.add_fixture(_Geom(bbox=(4, 4, 4, 8, 8, 8)))
        r = c.check_collision((5, 5, 5), _ToolGeom())
        assert r.collided
        assert r.severity == "critical"

    def test_tool_holder_collision_warning(self):
        c = self._make()
        c.set_workpiece(_Geom(bbox=(0, 0, 0, 10, 10, 10)))
        c.set_tool_holder(_Geom(bbox=(1, 1, 1, 3, 3, 3)))
        # 刀具本身在工作件外，但刀柄与工件相交 warning
        r = c.check_collision((20, 20, 20), _ToolGeom())
        assert r.collided
        assert r.severity == "warning"

    def test_no_collision(self):
        c = self._make()
        c.set_workpiece(_Geom(bbox=(100, 100, 100, 110, 110, 110)))
        c.add_fixture(_Geom(bbox=(200, 200, 200, 210, 210, 210)))
        c.set_tool_holder(_Geom(bbox=(300, 300, 300, 310, 310, 310)))
        r = c.check_collision((0, 0, 0), _ToolGeom())
        assert not r.collided
        assert r.severity == "none"

    def test_toolpath_empty(self):
        c = self._make()
        r = c.check_toolpath([], _ToolGeom())
        assert not r.collided

    def test_toolpath_points_attribute(self):
        c = self._make()
        c.set_workpiece(_Geom(bbox=(0, 0, 0, 10, 10, 10)))
        r = c.check_toolpath(_Geom(points=[(5, 5, 5), (50, 50, 50)]), _ToolGeom())
        assert r.collided
        assert 0 in r.collision_segments

    def test_toolpath_positions_attribute(self):
        c = self._make()
        c.set_workpiece(_Geom(bbox=(0, 0, 0, 10, 10, 10)))
        r = c.check_toolpath(_Geom(positions=[(5, 5, 5)]), _ToolGeom())
        assert r.collided

    def test_toolpath_waypoints_attribute(self):
        c = self._make()
        c.set_workpiece(_Geom(bbox=(0, 0, 0, 10, 10, 10)))
        r = c.check_toolpath(_Geom(waypoints=[(5, 5, 5)]), _ToolGeom())
        assert r.collided

    def test_toolpath_list_input(self):
        c = self._make()
        c.set_workpiece(_Geom(bbox=(0, 0, 0, 10, 10, 10)))
        r = c.check_toolpath([(5, 5, 5), (0, 0, 0), (1, 1, 1)], _ToolGeom())
        assert r.collided
        assert len(r.collision_points) >= 1

    def test_toolpath_object_without_points(self):
        c = self._make()
        r = c.check_toolpath(_Geom(x=1), _ToolGeom())
        assert not r.collided

    def test_toolpath_severity_upgrade(self):
        c = self._make()
        c.set_workpiece(_Geom(bbox=(0, 0, 0, 10, 10, 10)))
        # 100+ 点触发采样；保证碰撞点存在且 severity 聚合
        pts = [(5, 5, 5)] + [(50, 50, 50)] * 120
        r = c.check_toolpath(pts, _ToolGeom())
        assert r.collided
        assert r.severity == "critical"

    def test_extract_points_object_with_coords(self):
        pts = CollisionChecker._extract_toolpath_points([_Geom(x=1, y=2, z=3), _Geom(x=4, y=5, z=6)])
        assert pts == [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]

    def test_extract_points_skips_invalid(self):
        pts = CollisionChecker._extract_toolpath_points([(1, 2, 3), (1, 2), "bad", (4, 5, 6)])
        assert pts == [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]

    def test_extract_points_unsupported(self):
        assert CollisionChecker._extract_toolpath_points(_Geom(nothing=1)) == []


# ---------------------------------------------------------------- PhysicsValidator


class TestPhysicsValidator:
    def _make(self, **cap_kw):
        return PhysicsValidator(MachineCapability(**cap_kw) if cap_kw else None)

    def test_default_capability(self):
        v = PhysicsValidator()
        assert v.machine.max_power_kw == 15.0
        assert v.machine.max_spindle_speed_rpm == 12000.0

    def test_cutting_force_turning(self):
        v = self._make()
        r = v.calculate_cutting_force(
            material="steel",
            cutting_speed_m_min=200.0,
            feed_mm_rev=0.2,
            depth_of_cut_mm=2.0,
            tool_diameter_mm=20.0,
            operation="turning",
        )
        assert isinstance(r, CuttingForceResult)
        assert r.force_feed_n < r.force_tangential_n
        assert r.force_radial_n > r.force_feed_n  # 车削 0.4 > 0.3
        assert r.within_limits is True
        assert r.warnings == []

    def test_cutting_force_milling(self):
        v = self._make()
        r = v.calculate_cutting_force(
            material="aluminum",
            cutting_speed_m_min=300.0,
            feed_mm_rev=0.1,
            depth_of_cut_mm=1.0,
            tool_diameter_mm=10.0,
            operation="milling",
        )
        assert r.force_feed_n == pytest.approx(r.force_tangential_n * 0.5, rel=1e-3)

    def test_cutting_force_drilling(self):
        v = self._make()
        r = v.calculate_cutting_force(
            material="titanium",
            cutting_speed_m_min=50.0,
            feed_mm_rev=0.08,
            depth_of_cut_mm=0.5,
            tool_diameter_mm=8.0,
            operation="drilling",
        )
        assert r.force_feed_n == pytest.approx(r.force_tangential_n * 0.8, rel=1e-3)
        assert r.force_radial_n == pytest.approx(r.force_tangential_n * 0.2, rel=1e-3)

    def test_unknown_material_default_kc(self):
        v = self._make()
        r_unknown = v.calculate_cutting_force(
            material="unobtainium",
            cutting_speed_m_min=200.0,
            feed_mm_rev=0.2,
            depth_of_cut_mm=2.0,
            tool_diameter_mm=20.0,
        )
        r_default = v.calculate_cutting_force(
            material="default",
            cutting_speed_m_min=200.0,
            feed_mm_rev=0.2,
            depth_of_cut_mm=2.0,
            tool_diameter_mm=20.0,
        )
        assert r_unknown.force_tangential_n == r_default.force_tangential_n

    def test_force_over_limit(self):
        v = self._make(max_force_n=100.0)
        r = v.calculate_cutting_force(
            material="steel",
            cutting_speed_m_min=200.0,
            feed_mm_rev=0.2,
            depth_of_cut_mm=2.0,
            tool_diameter_mm=20.0,
        )
        assert not r.within_limits
        assert any("主切削力" in w for w in r.warnings)

    def test_torque_over_limit(self):
        v = self._make(max_torque_nm=0.01)
        r = v.calculate_cutting_force(
            material="steel",
            cutting_speed_m_min=200.0,
            feed_mm_rev=0.2,
            depth_of_cut_mm=2.0,
            tool_diameter_mm=50.0,
        )
        assert not r.within_limits
        assert any("扭矩" in w for w in r.warnings)

    def test_power_over_limit(self):
        v = self._make(max_power_kw=0.001)
        r = v.calculate_cutting_force(
            material="steel",
            cutting_speed_m_min=500.0,
            feed_mm_rev=0.5,
            depth_of_cut_mm=5.0,
            tool_diameter_mm=30.0,
        )
        assert not r.within_limits
        assert any("功率" in w for w in r.warnings)

    def test_validate_valid_parameters(self):
        v = self._make()
        r = v.validate_cutting_parameters(
            material="aluminum",
            cutting_speed_m_min=300.0,
            feed_mm_rev=0.1,
            depth_of_cut_mm=1.0,
            tool_diameter_mm=10.0,
            operation="milling",
        )
        assert r["valid"] is True
        assert r["errors"] == []
        assert r["force_analysis"] is not None
        assert r["force_analysis"]["within_limits"] is True

    def test_validate_nonpositive_params(self):
        v = self._make()
        r = v.validate_cutting_parameters(
            material="steel",
            cutting_speed_m_min=0.0,
            feed_mm_rev=-0.2,
            depth_of_cut_mm=0.0,
            tool_diameter_mm=-1.0,
        )
        assert r["valid"] is False
        assert len(r["errors"]) == 4
        # 非正参数不崩溃（回归：0^(-0.1) 曾抛 ZeroDivisionError）
        assert r["force_analysis"]["within_limits"] is False

    def test_calculate_force_nonpositive_params(self):
        # 直接调用 calculate 时非正参数同样不崩溃（0^(-0.1) 回归）
        v = self._make()
        r = v.calculate_cutting_force(
            material="steel",
            cutting_speed_m_min=0.0,
            feed_mm_rev=0.2,
            depth_of_cut_mm=2.0,
            tool_diameter_mm=20.0,
        )
        assert r.within_limits is False
        assert any("正数" in w for w in r.warnings)

    def test_validate_depth_of_cut_over_limit(self):
        v = self._make(max_depth_of_cut_mm=2.0)
        r = v.validate_cutting_parameters(
            material="steel",
            cutting_speed_m_min=100.0,
            feed_mm_rev=0.1,
            depth_of_cut_mm=10.0,
            tool_diameter_mm=20.0,
        )
        assert r["valid"] is False
        assert any("切削深度" in e for e in r["errors"])
        assert any("建议将切削深度降低" in rec for rec in r["recommendations"])

    def test_validate_high_speed_warning(self):
        v = self._make()
        r = v.validate_cutting_parameters(
            material="steel",
            cutting_speed_m_min=600.0,
            feed_mm_rev=0.8,
            depth_of_cut_mm=1.0,
            tool_diameter_mm=20.0,
        )
        assert any("振动" in w for w in r["warnings"])

    def test_validate_force_recommendations(self):
        v = self._make(max_power_kw=0.001)
        r = v.validate_cutting_parameters(
            material="steel",
            cutting_speed_m_min=500.0,
            feed_mm_rev=0.5,
            depth_of_cut_mm=5.0,
            tool_diameter_mm=30.0,
        )
        assert r["valid"] is False
        assert any("建议将切削速度降低" in rec for rec in r["recommendations"])

    def test_validate_torque_recommendation(self):
        v = self._make(max_torque_nm=0.001)
        r = v.validate_cutting_parameters(
            material="steel",
            cutting_speed_m_min=500.0,
            feed_mm_rev=0.5,
            depth_of_cut_mm=5.0,
            tool_diameter_mm=30.0,
        )
        assert any("建议将进给量降低" in rec for rec in r["recommendations"])

    def test_recommend_safe_default(self):
        v = self._make()
        r = v.recommend_safe_parameters(material="steel", tool_diameter_mm=20.0)
        assert r["cutting_speed_m_min"] > 0
        assert r["feed_mm_rev"] > 0
        assert r["tool_diameter_mm"] == 20.0

    def test_recommend_safe_aluminum(self):
        v = self._make()
        r = v.recommend_safe_parameters(material="aluminum", tool_diameter_mm=10.0)
        assert r["cutting_speed_m_min"] == pytest.approx(300.0, abs=1.0)

    def test_recommend_safe_titanium(self):
        v = self._make()
        r = v.recommend_safe_parameters(material="titanium", tool_diameter_mm=10.0)
        assert r["cutting_speed_m_min"] == pytest.approx(50.0, abs=1.0)

    def test_recommend_safe_stainless(self):
        v = self._make()
        r = v.recommend_safe_parameters(material="stainless", tool_diameter_mm=10.0)
        assert r["cutting_speed_m_min"] == pytest.approx(80.0, abs=1.0)

    def test_recommend_safe_converges_under_strict_limits(self):
        v = self._make(max_force_n=10.0, max_power_kw=0.01, max_torque_nm=0.01)
        r = v.recommend_safe_parameters(material="steel", tool_diameter_mm=20.0)
        # 迭代后参数被显著降低（速度 < 初始 100）
        assert r["cutting_speed_m_min"] < 100.0

    def test_recommend_safe_max_iterations_break(self):
        v = self._make(max_force_n=0.0001, max_power_kw=0.0001, max_torque_nm=0.0001)
        r = v.recommend_safe_parameters(material="titanium", tool_diameter_mm=50.0)
        # 无法收敛时返回最终迭代值（不会死循环）
        assert r["cutting_speed_m_min"] >= 0
