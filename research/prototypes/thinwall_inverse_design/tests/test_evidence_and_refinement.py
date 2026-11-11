import io
import json

import pytest

from prototypes.thinwall_inverse_design import collect_evidence, refine


def test_inventory_keeps_source_counts_separate_and_records_api_failure(tmp_path, monkeypatch):
    module = tmp_path/"module"
    module.mkdir()
    source = tmp_path/"repo"
    csv_dir = source/"research/datasets/measured_stability"
    csv_dir.mkdir(parents=True)
    (csv_dir/"points.csv").write_text("source,stable\nmeasured,1\n", encoding="utf-8")
    py_dir = source/"research/experiments/real_validation"
    py_dir.mkdir(parents=True)
    (py_dir/"test_sample.py").write_text("def test_example():\n    pass\n", encoding="utf-8")
    excluded = py_dir/"__pycache__"
    excluded.mkdir()
    (excluded/"ignored.py").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(collect_evidence, "ROOT", module)
    monkeypatch.setattr(collect_evidence, "REPO", source)
    monkeypatch.setattr(collect_evidence, "DOIS", ["ok", "broken"])
    monkeypatch.setattr(collect_evidence, "QUERIES", ["ok", "broken"])
    def fake_request(url):
        if "broken" in url:
            raise OSError("network unavailable")
        if "crossref" in url:
            return {"message": {"title": ["test"]}}
        return {"results": [{"title": "test", "doi": "10.test", "publication_year": 2024}], "meta": {"count": 1}}
    monkeypatch.setattr(collect_evidence, "request_json", fake_request)
    collect_evidence.main()
    value = json.loads((module/"evidence/inventory_and_metadata.json").read_text(encoding="utf-8"))
    assert value["assets"][2]["test_function_count"] == 1
    assert value["assets"][2]["python_file_count"] == 1
    assert value["csv_inventory"][0]["rows"] == 1
    assert value["openalex"][1]["status"] == "error"
    assert not value["openalex"][0]["relevance_validated"]


def test_request_json_response_wrapper(monkeypatch):
    monkeypatch.setattr(collect_evidence.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b'{"count": 0}'))
    assert collect_evidence.request_json("https://example.com") == {"count": 0}


@pytest.mark.parametrize("depth,passed", [(1.02e-6, True), (None, False)])
def test_refinement_is_targeted_and_deduplicates_identical_profiles(tmp_path, monkeypatch, depth, passed):
    config = {"geometry": {}, "cutting": {}, "verification": {"off_grid_rpms": [6750]}}
    monkeypatch.setattr(refine, "load_frozen", lambda output: config)
    selected = {name: {"design_id": index, "thickness_mm": [2,2,2]}
                for name,index in [("A", 1), ("B", 1), ("C", 2)]}
    rows = [{"design": name, "rpm": 6750, "steps": steps,
             "boundary_mm": (0.001 if steps == 160 else 0.002) if name != "C" else 0.001}
            for name in selected for steps in (80,160)]
    (tmp_path/"verification.json").write_text(json.dumps({"periodic_boundaries": rows}), encoding="utf-8")
    (tmp_path/"screening_summary.json").write_text(json.dumps({"selected": selected}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(refine, "periodic_boundary", lambda *a, **k: calls.append(k) or depth)
    monkeypatch.setattr("sys.argv", ["refine.py", "--output", str(tmp_path)])
    refine.main()
    result = json.loads((tmp_path/"step_refinement.json").read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert len(result["refinements"]) == 2
    assert result["all_refined_checks_below_5_percent"] == passed
