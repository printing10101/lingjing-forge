"""Regression for family midpoint diameters and parameter material mismatches."""
import math
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.data.process_data_manager import ProcessPlanningDataManager, QueryError, ToolEntry
from app.process_planning.tool_param_matcher import ToolParamMatcher

pytestmark = pytest.mark.unit


def tool(diameter, **changes):
    return ToolEntry(**{"id": "fixture", "series": "twist_drill", "name": "fixture drill", "diameter_mm": diameter,
                        "material": "WC", "application": "钻孔", **changes})


def test_real_material_alias_links_legacy_parameter_ids_without_grade_transfer():
    data = ProcessPlanningDataManager()
    canonical = data.get_cutting_parameters("steel_45", "twist_drill", "HSS")
    legacy = data.get_cutting_parameters("material_45steel", "twist_drill", "HSS")
    assert canonical and [p.id for p in canonical] == [p.id for p in legacy]
    assert all(p.material_id == "material_45steel" and p.tool_material == "HSS" for p in canonical)
    assert all(p.material_id != "material_45steel" for p in data.get_cutting_parameters("steel_q235", "twist_drill", "HSS"))
    assert all(data.normalize_tool_material(p.tool_material) == "carbide" for p in data.get_cutting_parameters("steel_45", "twist_drill", "WC"))
    assert data.get_cutting_parameters("steel_45", "endmill", "WC")
    assert all(p.tool_material == "coated_carbide" for p in data.get_cutting_parameters("steel_45", "endmill", "coated_carbide"))
    for canonical_id, legacy_id, series in [("ss_304", "material_ss304", "twist_drill"),
                                            ("al_6061", "material_al6061", "endmill")]:
        canonical_rows = data.get_cutting_parameters(canonical_id, series)
        assert canonical_rows
        assert [p.id for p in canonical_rows] == [p.id for p in data.get_cutting_parameters(legacy_id, series)]


def test_parameter_filter_does_not_borrow_hss_or_coated_parameters():
    data = ProcessPlanningDataManager()
    base = data.get_cutting_parameters("steel_45", "twist_drill", "HSS")[0]
    data._cutting_parameters = {"hss": base, "coated": replace(base, id="coated", tool_material="coated_carbide")}
    assert data.get_cutting_parameters("steel_45", "twist_drill", "WC") == []
    assert [p.id for p in data.get_cutting_parameters("steel_45", "twist_drill", "HSS")] == [base.id]


def test_exact_unique_grade_alias_is_allowed_but_ambiguous_or_near_name_is_not():
    data = ProcessPlanningDataManager()
    base = data.get_cutting_parameters("steel_45", "twist_drill", "HSS")[0]
    q235 = data.get_material_by_id("steel_q235")
    data._cutting_parameters = {"fixture": replace(base, material_id="legacy_fixture", material_name=q235.name)}
    assert data.get_cutting_parameters("steel_q235", "twist_drill", "HSS")
    assert data.get_cutting_parameters("steel_45", "twist_drill", "HSS") == []
    data._materials["duplicate"] = replace(q235, id="duplicate")
    assert data.get_cutting_parameters("steel_q235", "twist_drill", "HSS") == []
    data._cutting_parameters["fixture"] = replace(base, material_id="legacy_fixture", material_name="450钢")
    assert data.get_cutting_parameters("steel_45", "twist_drill", "HSS") == []


def test_family_loading_keeps_distinct_tool_materials(monkeypatch):
    records = [SimpleNamespace(id=material, type="drill", subtype="twist", name="fixture family", material=material,
                               diameter_range=[6, 20]) for material in ["WC", "HSS"]]
    monkeypatch.setattr("app.database.tools.ToolDatabase", lambda: SimpleNamespace(list_all=lambda: records))
    data = ProcessPlanningDataManager.__new__(ProcessPlanningDataManager)
    data._tools = {}
    data._load_tools()
    assert set(data._tools) == {"WC", "HSS"}
    assert all(t.diameter_mm == 0 and not t.diameter_verified for t in data._tools.values())


def test_family_range_is_not_a_fixed_midpoint():
    assert ProcessPlanningDataManager._tool_nominal_diameter([6, 20]) == 0
    assert ProcessPlanningDataManager._tool_nominal_diameter([8, 8]) == 8
    assert ProcessPlanningDataManager._tool_nominal_diameter([]) == 0


def test_family_choice_is_a_detached_candidate_with_visible_warning():
    original = tool(0, diameter_range_mm=[6, 20], diameter_verified=False)
    matcher = ToolParamMatcher.__new__(ToolParamMatcher)
    chosen = matcher._select_best_diameter([original], 8)
    assert chosen.diameter_mm == 8 and not chosen.diameter_verified
    assert original.diameter_mm == 0 and original.diameter_range_mm == [6, 20]
    assert "候选" in matcher._build_match_reason(chosen, 8, None)
    assert any("未确认" in warning for warning in matcher._generate_warnings(chosen, 8, None))
    assert matcher._calculate_suitability(chosen, 8, None) <= 50


def test_fixed_exact_tool_precedes_family_and_oversize_never_matches_drilling():
    matcher = ToolParamMatcher.__new__(ToolParamMatcher)
    fixed = tool(8)
    family = tool(0, diameter_range_mm=[6, 20], diameter_verified=False)
    assert matcher._select_best_diameter([family, fixed], 8) is fixed
    for diameter in [6, 13]:
        missing = matcher._select_best_diameter([tool(diameter)], 8)
        assert missing.id == "unavailable" and missing.diameter_mm == 8 and not missing.diameter_verified


@pytest.mark.parametrize("bounds", [[9, 20], [20, 6], [0, 20], [math.nan, 20], [True, 20], [6], []])
def test_outside_or_invalid_family_never_becomes_selected_inventory(bounds):
    matcher = ToolParamMatcher.__new__(ToolParamMatcher)
    chosen = matcher._select_best_diameter([tool(0, diameter_range_mm=bounds, diameter_verified=False)], 8)
    assert chosen.id == "unavailable" and not chosen.diameter_verified


@pytest.mark.parametrize("diameter", [0, -8, math.inf, math.nan, True])
def test_invalid_hole_diameter_rejected(diameter):
    matcher = ToolParamMatcher.__new__(ToolParamMatcher)
    with pytest.raises(QueryError, match="孔径"):
        matcher.match_tool_for_hole("steel_45", "carbon_steel", diameter, "钻孔")


def test_live_data_match_no_longer_claims_13mm_drill_for_8mm_hole():
    matcher = ToolParamMatcher()
    match = matcher.match_tool_for_hole("steel_45", "carbon_steel", 8, "钻孔")
    result = match.to_dict()
    assert result["diameter_mm"] == 8
    assert result["diameter_verified"] is False
    low, high = result["diameter_range_mm"]
    assert low <= 8 <= high and low != high
    assert match.cutting_params is not None
    assert match.cutting_params.tool_material == "carbide"
    assert result["cutting_parameters"]["source_verified"] is False
    assert any("未确认" in warning for warning in result["warnings"])


def test_missing_center_drill_does_not_inherit_final_hole_diameter():
    matcher = ToolParamMatcher.__new__(ToolParamMatcher)
    matcher._data = SimpleNamespace(get_tools_by_material_and_process=lambda *args: [])
    match = matcher.match_tool_for_hole("steel_45", "carbon_steel", 8, "打中心孔定位")
    assert match.tool.series == "center_drill" and match.tool.diameter_mm == 0
    assert not match.tool.diameter_verified and match.cutting_params is None
