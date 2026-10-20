"""Source supplements must preserve uncertainty, identities and review isolation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from app.rag.process_quadruple import ProcessQuadruple, ProcessQuadrupleIndex
from app.rag.source_candidates import build_vendor_candidates
from app.rag.source_review_packet import build_vendor_review_packet

pytestmark = pytest.mark.unit
PDF = b"%PDF-1.7\nsynthetic source-review test"
ROOT = Path(__file__).resolve().parents[4]


def inputs():
    base = ROOT / "competition/nexent/knowledge-candidates"
    manifest = json.loads((base / "dormer-source-manifest.json").read_text(encoding="utf-8"))
    supplement = json.loads((base / "dormer-review-supplement.json").read_text(encoding="utf-8"))
    manifest["sha256"] = supplement["document_sha256"] = hashlib.sha256(PDF).hexdigest()
    return manifest, supplement


def test_review_packet_preserves_identity_parameters_and_every_original_blocker():
    manifest, supplement = inputs()
    before = deepcopy((manifest, supplement))
    original = build_vendor_candidates(PDF, manifest)
    packet = build_vendor_review_packet(PDF, manifest, supplement)
    assert (manifest, supplement) == before
    assert packet["review_status"] == "unreviewed"
    assert packet["eligible_for_recommendation"] is False
    assert packet["count"] == 3
    for candidate, old in zip(packet["candidates"], original, strict=True):
        assert candidate["candidate_id"] == old["candidate_id"]
        assert candidate["parameters"] == old["parameters"]
        assert candidate["conditions"] == old["conditions"]
        assert set(old["unresolved"]) <= set(candidate["unresolved"])
        assert candidate["candidate_context"]["unresolved"] == candidate["unresolved"]
        assert candidate["candidate_context"]["page"] == 4
        assert set(candidate["candidate_context"]["parameter_units"]) == set(old["parameters"])
        assert len(candidate["source_facts"]) == 6
        assert "4×D" in candidate["candidate_context"]["applicability"]
        assert "010120" in candidate["candidate_context"]["applicability"]
        assert "spindle_rpm" not in candidate["parameters"]
    assert build_vendor_review_packet(PDF, manifest, supplement) == packet


@pytest.mark.parametrize("case", ["hash", "no_pages", "bool_pages", "zero_pages", "wrong_material",
    "missing_material", "extra_material", "empty_material", "duplicate_key", "blank_question", "empty_questions",
    "unknown_trust", "unknown_fact", "page_zero", "page_overflow", "page_bool",
    "blank_key", "blank_value", "blank_locator"])
def test_invalid_supplements_fail_closed(case):
    manifest, supplement = inputs()
    if case == "hash":
        supplement["document_sha256"] = "0" * 64
    elif case in ("no_pages", "bool_pages", "zero_pages"):
        manifest["page_count"] = {"no_pages": None, "bool_pages": True, "zero_pages": 0}[case]
    elif case == "wrong_material":
        supplement["materials"]["steel_45"] = supplement["materials"].pop("dormer_amg_1_3")
    elif case == "missing_material":
        supplement["materials"].pop("dormer_amg_1_3")
    elif case == "extra_material":
        supplement["materials"]["steel_45"] = deepcopy(supplement["materials"]["dormer_amg_1_3"])
    elif case == "empty_material":
        supplement["materials"]["dormer_amg_1_3"] = []
    elif case == "duplicate_key":
        supplement["shared_facts"].append(deepcopy(supplement["shared_facts"][0]))
    elif case == "blank_question":
        supplement["open_questions"] = [" "]
    elif case == "empty_questions":
        supplement["open_questions"] = []
    elif case == "unknown_trust":
        supplement["review_status"] = "approved"
    elif case == "unknown_fact":
        supplement["shared_facts"][0]["approved"] = True
    elif case.startswith("page_"):
        supplement["shared_facts"][0]["pdf_pages"] = [{"page_zero": 0, "page_overflow": 41, "page_bool": True}[case]]
    else:
        supplement["shared_facts"][0][case.removeprefix("blank_")] = " "
    with pytest.raises(ValueError):
        build_vendor_review_packet(PDF, manifest, supplement)


def test_open_questions_cannot_clear_original_unknowns_or_infer_material_equivalence():
    manifest, supplement = inputs()
    supplement["open_questions"] = ["custom_review", "custom_review"]
    for candidate in build_vendor_review_packet(PDF, manifest, supplement)["candidates"]:
        assert candidate["unresolved"] == ["cutting_speed_unit", "material_grade_equivalence", "machining_review", "custom_review"]
        assert candidate["material"].startswith("dormer_amg_")


def test_context_cannot_approve_or_mutate_production_store(tmp_path):
    manifest, supplement = inputs()
    packet = build_vendor_review_packet(PDF, manifest, supplement)
    # This is a temporary test index. No deployed source or real reviewer is used.
    index = ProcessQuadrupleIndex(str(tmp_path))
    for candidate in packet["candidates"]:
        index.add(ProcessQuadruple.from_dict(candidate))
    for record in index.list_review_records():
        evidence = {**record["candidate_context"], "material": record["material"], "tool": record["tool"]}
        with pytest.raises(ValueError, match="未解决"):
            index.update_review(record["record_id"], record["revision"], actor="synthetic_test_actor",
                decision="approved", note="Synthetic rejection check, no actual approval", evidence=evidence)
        assert index.recommend_process(record["feature"], record["material"]) == []
    assert index.get_stats()["approved_quadruples"] == 0
    assert all(record["revision"] == 1 and record["review"]["status"] == "pending"
               for record in index.list_review_records())
