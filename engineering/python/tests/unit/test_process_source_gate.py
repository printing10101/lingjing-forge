"""Unreviewed manufacturer and built-in seed records cannot become formal advice."""
import pytest

from app.rag.process_quadruple import ProcessQuadruple, ProcessQuadrupleIndex

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("source", [None, "unverified", "manufacturer_candidate", "repo_seed_v1", "handbook_common_range", "example", " Manufacturer_Candidate ", " "])
def test_unverified_sources_remain_reference_only(source):
    data = {"feature": "hole", "process": "drill", "tool": "R10A", "material": "amg_1_3",
            "parameters": {"feed_per_revolution_mm": 0.16002}}
    if source is not None:
        data["source"] = source
    quad = ProcessQuadruple.from_dict(data)
    if source is None:
        assert quad.source == "unverified"
    index = ProcessQuadrupleIndex()
    index.add(quad)
    assert index.recommend_process("hole", "amg_1_3") == []
    assert index.find_similar("hole", "amg_1_3")[0]["source"] == quad.source


def test_non_string_source_rejected():
    with pytest.raises(ValueError, match="source"):
        ProcessQuadruple("hole", "drill", "fixture", {}, source={"status": "approved"})


def test_simulation_evidence_remains_distinct_from_machine_validation():
    index = ProcessQuadrupleIndex()
    index.add(ProcessQuadruple("hole", "mill", "unspecified", {"spindle_rpm": 1000}, source="generated_validated"))
    assert index.recommend_process("hole") == []
    assert index.find_similar("hole")[0]["source"] == "generated_validated"
