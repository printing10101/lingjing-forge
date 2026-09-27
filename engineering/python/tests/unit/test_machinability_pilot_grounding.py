"""grounding 层单测：卡片索引完整性、意图选择、错误分类、修复知识组装。"""

from __future__ import annotations

from app.benchmarks.machinability_pilot.grounding import (
    build_api_cards,
    classify_error,
    format_static_block,
    load_api_cards,
    load_idiom_cards,
    repair_knowledge,
    select_static_cards,
)


def test_api_card_index_builds_and_loads() -> None:
    cards = build_api_cards()
    assert len(cards) >= 100  # Workplane 公开方法在 150 量级
    names = {c["name"] for c in cards}
    assert "Workplane.loft" in names
    assert "Workplane.twistExtrude" in names
    assert "Wire.makeHelix" in names  # 手工增补的非 Workplane API
    loft = load_api_cards()["Workplane.loft"]
    assert "ruled" in loft["signature"]
    assert "sections" in loft["doc"]  # 混淆对纠偏已注入


def test_idiom_library_covers_measured_failure_modes() -> None:
    idioms = load_idiom_cards()
    ids = {c["id"] for c in idioms}
    assert len(idioms) >= 10
    assert {"loft_multi_section", "helical_sweep", "twisted_prism",
            "sampled_profile", "sandbox_contract"} <= ids


def test_static_selection_by_intent() -> None:
    api_cards = load_api_cards()
    selected = select_static_cards(
        "A vertical conveyor screw with helical flights swept along a helix", api_cards, load_idiom_cards()
    )
    api_names = {c["name"] for c in selected["api"]}
    assert "Wire.makeHelix" in api_names
    assert "Workplane.sweep" in api_names
    assert {c["id"] for c in selected["idioms"]} == {"helical_sweep"}
    block = format_static_block(selected)
    assert "Available CadQuery APIs" in block
    assert "Idiom: 螺旋扫掠" in block


def test_error_classifier_and_repair_knowledge() -> None:
    hit = classify_error("Workplane.loft() got an unexpected keyword argument 'sections'")
    assert hit["kind"] == "api_misuse"
    assert hit["api_target"] == "Workplane.loft"
    knowledge = repair_knowledge(
        "Workplane.loft() got an unexpected keyword argument 'sections'",
        load_api_cards(), load_idiom_cards(),
    )
    assert "[Correct API] Workplane.loft" in knowledge
    assert "Nothing to loft" in knowledge  # 混淆对纠偏随卡注入

    hit2 = classify_error("Script execution failed: name 'math' is not defined")
    assert hit2["kind"] == "sandbox_contract"
    assert "math` is NOT available" in repair_knowledge(
        "name 'math' is not defined", load_api_cards(), load_idiom_cards()
    )


def test_unknown_error_gets_guardrail_only() -> None:
    knowledge = repair_knowledge("mystery failure xyz", load_api_cards(), load_idiom_cards())
    assert knowledge == ""  # 未命中分类不注入（盲改对照口径）
    assert repair_knowledge("Workplane.extrude() got an unexpected keyword argument 'taper'",
                            load_api_cards(), load_idiom_cards()).startswith("[Correct API]")
