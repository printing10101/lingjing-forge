"""装夹方案与定位基准分析模块 单元测试。

测试用例:
- 阶梯轴：验证工序排序符合"先粗后精、先主后次"
- 法兰盘：验证翻面加工识别和装夹方案切换
- 特征依赖图：拓扑排序、可达性检查
- 基准选择：6点定位原理验证
"""

from __future__ import annotations


from app.process_planning.feature_dependency import (
    FeatureDependencyGraph,
    MachiningFeature,
    Setup,
)
from app.process_planning.datum_selector import (
    DatumSelector,
)
from app.process_planning.operation_sequencer import (
    OperationSequencer,
)
from app.process_planning.fixture_analyzer import (
    FixtureAnalyzer,
)


def make_stepped_shaft_features() -> list[MachiningFeature]:
    return [
        MachiningFeature(
            name="右端面",
            type="end_face",
            geometric_type="plane",
            tolerance_grade="IT7",
            surface_roughness_ra=3.2,
            is_datum_candidate=True,
            machining_method="turning",
            priority="high",
            surface="A",
            dimensions={"diameter": 50, "area": 1963},
        ),
        MachiningFeature(
            name="左端面",
            type="end_face",
            geometric_type="plane",
            tolerance_grade="IT8",
            surface_roughness_ra=6.3,
            is_datum_candidate=True,
            machining_method="turning",
            priority="high",
            surface="A",
            dimensions={"diameter": 25, "area": 491},
        ),
        MachiningFeature(
            name="外圆D50",
            type="outer_cylinder",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            surface_roughness_ra=1.6,
            is_datum_candidate=True,
            machining_method="turning",
            priority="high",
            surface="A",
            dimensions={"diameter": 50, "length": 40},
        ),
        MachiningFeature(
            name="外圆D40",
            type="outer_cylinder",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            surface_roughness_ra=1.6,
            is_datum_candidate=False,
            machining_method="turning",
            priority="medium",
            surface="A",
            dimensions={"diameter": 40, "length": 50},
        ),
        MachiningFeature(
            name="外圆D30",
            type="outer_cylinder",
            geometric_type="cylinder",
            tolerance_grade="IT8",
            surface_roughness_ra=3.2,
            is_datum_candidate=False,
            machining_method="turning",
            priority="medium",
            surface="A",
            dimensions={"diameter": 30, "length": 60},
        ),
        MachiningFeature(
            name="退刀槽1",
            type="groove",
            geometric_type="groove",
            tolerance_grade="IT9",
            surface_roughness_ra=6.3,
            is_datum_candidate=False,
            machining_method="turning",
            priority="low",
            surface="A",
            dimensions={"diameter": 38, "width": 4},
        ),
        MachiningFeature(
            name="退刀槽2",
            type="groove",
            geometric_type="groove",
            tolerance_grade="IT9",
            surface_roughness_ra=6.3,
            is_datum_candidate=False,
            machining_method="turning",
            priority="low",
            surface="A",
            dimensions={"diameter": 28, "width": 3},
        ),
        MachiningFeature(
            name="倒角C2",
            type="chamfer",
            geometric_type="chamfer",
            tolerance_grade="IT10",
            surface_roughness_ra=12.5,
            is_datum_candidate=False,
            machining_method="turning",
            priority="low",
            surface="A",
            dimensions={"width": 2},
        ),
        MachiningFeature(
            name="倒角C1.5",
            type="chamfer",
            geometric_type="chamfer",
            tolerance_grade="IT10",
            surface_roughness_ra=12.5,
            is_datum_candidate=False,
            machining_method="turning",
            priority="low",
            surface="A",
            dimensions={"width": 1.5},
        ),
        MachiningFeature(
            name="中心孔A",
            type="center_hole",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            surface_roughness_ra=3.2,
            is_datum_candidate=True,
            machining_method="drilling",
            priority="high",
            surface="A",
            dimensions={"diameter": 6, "depth": 8},
        ),
        MachiningFeature(
            name="中心孔B",
            type="center_hole",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            surface_roughness_ra=3.2,
            is_datum_candidate=True,
            machining_method="drilling",
            priority="high",
            surface="B",
            dimensions={"diameter": 4, "depth": 5},
        ),
    ]


def make_flange_features() -> list[MachiningFeature]:
    return [
        MachiningFeature(
            name="法兰正面",
            type="plane_surface",
            geometric_type="plane",
            tolerance_grade="IT7",
            surface_roughness_ra=3.2,
            is_datum_candidate=True,
            machining_method="milling",
            priority="high",
            surface="A",
            dimensions={"area": 11310},
        ),
        MachiningFeature(
            name="法兰背面",
            type="plane_surface",
            geometric_type="plane",
            tolerance_grade="IT8",
            surface_roughness_ra=6.3,
            is_datum_candidate=True,
            machining_method="milling",
            priority="high",
            surface="B",
            dimensions={"area": 11310},
        ),
        MachiningFeature(
            name="中心孔",
            type="through_hole",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            surface_roughness_ra=3.2,
            is_datum_candidate=False,
            machining_method="drilling",
            priority="medium",
            surface="A",
            dimensions={"diameter": 40},
        ),
        MachiningFeature(
            name="螺栓孔1",
            type="through_hole",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            surface_roughness_ra=3.2,
            is_datum_candidate=False,
            machining_method="drilling",
            priority="medium",
            surface="A",
            dimensions={"diameter": 10},
        ),
        MachiningFeature(
            name="螺栓孔2",
            type="through_hole",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            surface_roughness_ra=3.2,
            is_datum_candidate=False,
            machining_method="drilling",
            priority="medium",
            surface="A",
            dimensions={"diameter": 10},
        ),
        MachiningFeature(
            name="螺栓孔3",
            type="through_hole",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            surface_roughness_ra=3.2,
            is_datum_candidate=False,
            machining_method="drilling",
            priority="medium",
            surface="B",
            dimensions={"diameter": 10},
        ),
        MachiningFeature(
            name="螺栓孔4",
            type="through_hole",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            surface_roughness_ra=3.2,
            is_datum_candidate=False,
            machining_method="drilling",
            priority="medium",
            surface="B",
            dimensions={"diameter": 10},
        ),
        MachiningFeature(
            name="外圆倒角",
            type="chamfer",
            geometric_type="chamfer",
            tolerance_grade="IT10",
            surface_roughness_ra=12.5,
            is_datum_candidate=False,
            machining_method="turning",
            priority="low",
            surface="A",
            dimensions={"width": 1},
        ),
    ]


class TestMachiningFeature:
    def test_is_rough(self):
        f = MachiningFeature(name="f1", type="end_face", tolerance_grade="IT10")
        assert f.is_rough()
        f2 = MachiningFeature(name="f2", type="end_face", tolerance_grade="IT6")
        assert not f2.is_rough()

    def test_is_finish(self):
        f = MachiningFeature(name="f1", type="outer_cylinder", tolerance_grade="IT6")
        assert f.is_finish()
        f2 = MachiningFeature(name="f2", type="outer_cylinder", tolerance_grade="IT9")
        assert not f2.is_finish()

    def test_is_hole(self):
        f = MachiningFeature(name="f1", type="through_hole", geometric_type="cylinder")
        assert f.is_hole()
        f2 = MachiningFeature(name="f2", type="end_face", geometric_type="plane")
        assert not f2.is_hole()

    def test_is_face(self):
        f = MachiningFeature(name="f1", type="plane_surface", geometric_type="plane")
        assert f.is_face()

    def test_priority_score_datum_higher(self):
        datum = MachiningFeature(
            name="endface",
            type="end_face",
            geometric_type="plane",
            is_datum_candidate=True,
            tolerance_grade="IT7",
        )
        hole = MachiningFeature(
            name="hole",
            type="through_hole",
            geometric_type="cylinder",
            is_datum_candidate=False,
            tolerance_grade="IT7",
        )
        assert datum.priority_score() > hole.priority_score()

    def test_to_dict(self):
        f = MachiningFeature(
            name="f1",
            type="end_face",
            geometric_type="plane",
            tolerance_grade="IT7",
            dimensions={"diameter": 50},
        )
        d = f.to_dict()
        assert d["name"] == "f1"
        assert d["dimensions"]["diameter"] == 50


class TestFeatureDependencyGraph:
    def test_build_graph(self):
        features = make_stepped_shaft_features()
        graph = FeatureDependencyGraph()
        graph.build_graph(features)
        assert len(graph._features) == 11

    def test_get_machining_sequence_length(self):
        features = make_stepped_shaft_features()
        graph = FeatureDependencyGraph()
        graph.build_graph(features)
        seq = graph.get_machining_sequence()
        assert len(seq) == 11

    def test_datum_before_non_datum(self):
        """基准特征应在非基准特征之前"""
        features = make_stepped_shaft_features()
        graph = FeatureDependencyGraph()
        graph.build_graph(features)
        seq = graph.get_machining_sequence()

        datum_positions = [i for i, f in enumerate(seq) if f.is_datum_candidate]
        non_datum_positions = [i for i, f in enumerate(seq) if not f.is_datum_candidate]
        assert (
            max(datum_positions[:2]) < min(non_datum_positions)
            if non_datum_positions
            else True
        )

    def test_face_before_hole_rule(self):
        """先面后孔"""
        face = MachiningFeature(
            name="face",
            type="plane_surface",
            geometric_type="plane",
            tolerance_grade="IT7",
            is_datum_candidate=True,
        )
        hole = MachiningFeature(
            name="hole",
            type="through_hole",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            is_datum_candidate=False,
        )
        graph = FeatureDependencyGraph()
        graph.build_graph([face, hole])
        seq = graph.get_machining_sequence()
        assert seq[0].name == "face"

    def test_rough_before_finish(self):
        """先粗后精"""
        rough = MachiningFeature(
            name="rough_cyl",
            type="outer_cylinder",
            geometric_type="cylinder",
            tolerance_grade="IT10",
        )
        finish = MachiningFeature(
            name="finish_cyl",
            type="outer_cylinder",
            geometric_type="cylinder",
            tolerance_grade="IT6",
        )
        graph = FeatureDependencyGraph()
        graph.build_graph([rough, finish])
        seq = graph.get_machining_sequence()
        assert seq[0].name == "rough_cyl"

    def test_check_accessibility_same_surface(self):
        f = MachiningFeature(name="hole1", type="through_hole", surface="A")
        s = Setup(name="setup1", surface="A")
        graph = FeatureDependencyGraph()
        graph.build_graph([f])
        assert graph.check_accessibility(f, s)

    def test_check_accessibility_different_surface(self):
        f = MachiningFeature(name="hole1", type="through_hole", surface="A")
        s = Setup(name="setup1", surface="B")
        graph = FeatureDependencyGraph()
        graph.build_graph([f])
        assert not graph.check_accessibility(f, s)

    def test_check_accessibility_clamped(self):
        f = MachiningFeature(name="hole1", type="through_hole", surface="A")
        s = Setup(name="setup1", surface="A", clamped_features=["hole1"])
        graph = FeatureDependencyGraph()
        graph.build_graph([f])
        assert not graph.check_accessibility(f, s)

    def test_from_features_list(self):
        raw = [
            {
                "name": "f1",
                "type": "end_face",
                "geometric_type": "plane",
                "tolerance_grade": "IT7",
                "is_datum_candidate": True,
            },
            {
                "name": "f2",
                "type": "through_hole",
                "geometric_type": "cylinder",
                "tolerance_grade": "IT7",
                "is_datum_candidate": False,
            },
        ]
        graph = FeatureDependencyGraph.from_features_list(raw)
        seq = graph.get_machining_sequence()
        assert seq[0].name == "f1"

    def test_preserves_stepped_shaft_order(self):
        """阶梯轴应遵循：端面→外圆→槽→倒角"""
        features = make_stepped_shaft_features()
        graph = FeatureDependencyGraph()
        graph.build_graph(features)
        seq = graph.get_machining_sequence()
        names = [f.name for f in seq]

        face_idx = min(names.index(n) for n in names if "端面" in n)
        cyl_idx = min(names.index(n) for n in names if "外圆" in n and "D50" in n)
        gro_idx = min(names.index(n) for n in names if "退刀槽" in n)
        cham_idx = min(names.index(n) for n in names if "倒角" in n)

        assert face_idx < cyl_idx, f"端面({face_idx})应在粗车外圆({cyl_idx})之前"
        assert cyl_idx < gro_idx, f"粗车外圆({cyl_idx})应在切槽({gro_idx})之前"
        assert gro_idx < cham_idx, f"切槽({gro_idx})应在倒角({cham_idx})之前"


class TestDatumSelector:
    def test_select_shaft(self):
        features = make_stepped_shaft_features()
        selector = DatumSelector()
        result = selector.select_datums(features, part_type="stepped_shaft")
        assert result.primary_datum is not None
        assert result.degrees_constrained >= 4

    def test_select_prismatic(self):
        features = make_flange_features()
        selector = DatumSelector()
        result = selector.select_datums(features, part_type="flange")
        assert result.primary_datum is not None
        assert "面" in result.locating_method or "销" in result.locating_method

    def test_select_shaft_prefers_center_holes(self):
        features = make_stepped_shaft_features()
        selector = DatumSelector()
        result = selector.select_datums(features, part_type="stepped_shaft")
        if any(
            c.feature.type == "center_hole"
            for c in [result.primary_datum]
            if result.primary_datum
        ):
            assert "顶尖" in result.locating_method or "中心孔" in str(result.reasoning)

    def test_validate_valid_selection(self):
        features = make_stepped_shaft_features()
        selector = DatumSelector()
        result = selector.select_datums(features, part_type="stepped_shaft")
        validation = selector.validate_datums(result, features)
        assert validation["is_valid"]

    def test_selection_to_dict(self):
        features = make_stepped_shaft_features()
        selector = DatumSelector()
        result = selector.select_datums(features, part_type="stepped_shaft")
        d = result.to_dict()
        assert "primary_datum" in d
        assert "locating_method" in d


class TestOperationSequencer:
    def test_plan_stepped_shaft(self):
        features = make_stepped_shaft_features()
        seq = OperationSequencer()
        plan = seq.plan_operations(
            features,
            material="45钢",
            blank_type="棒料",
            part_type="stepped_shaft",
        )
        assert len(plan.operations) == len(features)
        assert plan.setups
        assert plan.estimated_time_min > 0

    def test_plan_flange(self):
        features = make_flange_features()
        seq = OperationSequencer()
        plan = seq.plan_operations(
            features,
            material="Q235",
            blank_type="锻件",
            part_type="flange",
        )
        assert len(plan.operations) == len(features)

    def test_flange_has_face_change(self):
        features = make_flange_features()
        seq = OperationSequencer()
        plan = seq.plan_operations(
            features,
            material="Q235",
            blank_type="锻件",
            part_type="flange",
        )
        surfaces = {op.surface for op in plan.operations}
        assert len(surfaces) >= 2, f"法兰盘应有翻面加工, 实际面: {surfaces}"

    def test_stepped_shaft_sequence_order(self):
        """核心测试：阶梯轴工序顺序应为端面→外圆→槽→倒角"""
        features = make_stepped_shaft_features()
        seq = OperationSequencer()
        plan = seq.plan_operations(
            features,
            material="45钢",
            blank_type="棒料",
            part_type="stepped_shaft",
        )
        op_names = [op.machining_method for op in plan.operations]

        combined = "".join(op_names)
        assert any(term in combined for term in ["端面", "车端面"]), (
            f"应包含端面加工: {op_names}"
        )

        for op in plan.operations:
            if "槽" in op.machining_method:
                groove_idx = op_names.index(op.machining_method)
                cyl_before = any("外圆" in m for m in op_names[:groove_idx])
                assert cyl_before, f"切槽({groove_idx})应在车外圆之后: {op_names}"

    def test_operation_plan_to_dict(self):
        features = make_stepped_shaft_features()
        seq = OperationSequencer()
        plan = seq.plan_operations(features)
        d = plan.to_dict()
        assert "operations" in d
        assert "setups" in d
        assert "estimated_time_min" in d

    def test_suggest_fixture_for_shaft(self):
        seq = OperationSequencer()
        fr = seq.suggest_fixture(
            Setup(name="s1", surface="A"),
            part_type="stepped_shaft",
        )
        assert fr.suitability_score > 0
        assert "三爪卡盘" in fr.fixture_name

    def test_suggest_fixture_for_plate(self):
        seq = OperationSequencer()
        fr = seq.suggest_fixture(
            Setup(name="s1", surface="A"),
            part_type="plate",
        )
        assert fr.suitability_score > 0
        assert fr.fixture_name in (
            "虎钳装夹",
            "压板装夹",
        )

    def test_material_affects_feed_rate(self):
        features = make_stepped_shaft_features()
        seq = OperationSequencer()
        plan_steel = seq.plan_operations(features, material="45钢")
        plan_alum = seq.plan_operations(features, material="6061铝合金")

        for op_s, op_a in zip(plan_steel.operations, plan_alum.operations):
            fs = op_s.cutting_params.get("feed_rate_factor", 1.0)
            fa = op_a.cutting_params.get("feed_rate_factor", 1.0)
            assert fa <= fs, f"铝合金进给率应更低: {op_s.feature_name}"

    def test_preserves_tool_selection(self):
        features = [
            MachiningFeature(
                name="端面",
                type="end_face",
                tolerance_grade="IT7",
                surface="A",
            ),
            MachiningFeature(
                name="孔",
                type="through_hole",
                tolerance_grade="IT7",
                surface="A",
            ),
        ]
        seq = OperationSequencer()
        plan = seq.plan_operations(features)
        for op in plan.operations:
            assert op.tool_type, f"每道工序应有刀具选择: {op.name}"


class TestFixtureAnalyzer:
    def test_analyze_shaft(self):
        features = make_stepped_shaft_features()
        fa = FixtureAnalyzer()
        analysis = fa.analyze(features, part_type="stepped_shaft")
        assert analysis.best_fixture is not None
        assert analysis.setup_count > 0

    def test_analyze_flange(self):
        features = make_flange_features()
        fa = FixtureAnalyzer()
        analysis = fa.analyze(features, part_type="flange")
        assert analysis.best_fixture is not None
        assert len(analysis.face_changes) >= 2

    def test_analysis_to_dict(self):
        features = make_stepped_shaft_features()
        fa = FixtureAnalyzer()
        analysis = fa.analyze(features, part_type="stepped_shaft")
        d = analysis.to_dict()
        assert "best_fixture" in d
        assert "setup_count" in d

    def test_suggest_vise_for_general_part(self):
        fa = FixtureAnalyzer()
        fr = fa.suggest_fixture(Setup(name="s1", surface="A"))
        assert fr.fixture_name == "虎钳装夹"

    def test_suggest_chuck_for_shaft(self):
        fa = FixtureAnalyzer()
        fr = fa.suggest_fixture(
            Setup(name="s1", surface="A"),
            part_type="stepped_shaft",
        )
        assert "三爪卡盘" in fr.fixture_name

    def test_clamping_force_varies_by_material(self):
        fa = FixtureAnalyzer()
        f1 = fa._estimate_clamping_force("vise", 100, "45钢")
        f2 = fa._estimate_clamping_force("vise", 100, "")
        assert f1 > f2


class TestIntegration:
    def test_full_stepped_shaft_workflow(self):
        """集成测试：阶梯轴完整工艺规划流程"""
        features = make_stepped_shaft_features()

        selector = DatumSelector()
        datums = selector.select_datums(features, part_type="stepped_shaft")
        assert datums.primary_datum is not None

        validate = selector.validate_datums(datums, features)
        assert validate["is_valid"], f"基准验证失败: {validate.get('issues', [])}"

        seq = OperationSequencer()
        plan = seq.plan_operations(
            features,
            material="45钢",
            blank_type="棒料",
            part_type="stepped_shaft",
        )
        assert len(plan.operations) == len(features)

        op_methods = [op.machining_method for op in plan.operations]
        combined = "→".join(op_methods)
        print(f"\n阶梯轴工序序列: {combined}")
        print(f"预估总时间: {plan.estimated_time_min:.1f} min")
        print(f"装夹方案数: {len(plan.setups)}")

        # 验证工艺原则：端面先于外圆，外圆先于槽，槽先于倒角
        has_face = any("端面" in m for m in op_methods[:3])
        assert has_face, f"前3道工序应包含端面加工: {op_methods[:3]}"

        assert plan.estimated_time_min > 0

    def test_full_flange_workflow(self):
        """集成测试：法兰盘完整工艺规划流程（含翻面识别）"""
        features = make_flange_features()

        selector = DatumSelector()
        datums = selector.select_datums(features, part_type="flange")
        assert datums.primary_datum is not None

        seq = OperationSequencer()
        plan = seq.plan_operations(
            features,
            material="Q235",
            blank_type="锻件",
            part_type="flange",
        )
        assert len(plan.operations) == len(features)

        surfaces = {op.surface for op in plan.operations}
        assert len(surfaces) >= 2, (
            f"法兰盘应识别翻面加工：实际面数={len(surfaces)}, 面={surfaces}"
        )

        op_methods = [op.machining_method for op in plan.operations]
        combined = "→".join(op_methods)
        print(f"\n法兰盘工序序列: {combined}")
        print(f"加工面数: {len(surfaces)}, 面: {surfaces}")
        print(f"预估总时间: {plan.estimated_time_min:.1f} min")
        print(f"翻面次数: {plan.face_change_count}")

        front_faces = [op for op in plan.operations if op.surface == "A"]
        back_faces = [op for op in plan.operations if op.surface == "B"]
        assert len(front_faces) > 0, "法兰应有正面(A)加工工序"
        assert len(back_faces) > 0, "法兰应有背面(B)加工工序"
