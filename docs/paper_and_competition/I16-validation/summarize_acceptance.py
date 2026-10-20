"""Summarize completed local acceptance and explicitly separate live verification."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

directory = Path(__file__).resolve().parent
root = directory.parents[2]
cases = list(ET.parse(directory / "final-regression.xml").iter("testcase"))
assert all(case.find("failure") is None and case.find("error") is None for case in cases)
coverage = json.loads((directory / "coverage.json").read_text(encoding="utf-8"))["totals"]
mutations = json.loads((directory / "mutations.json").read_text(encoding="utf-8"))
assert all(row["caught"] for row in mutations)
packet = json.loads((root / "competition/nexent/knowledge-candidates/dormer-review-packet.json").read_text(encoding="utf-8"))
original = json.loads((root / "competition/nexent/knowledge-candidates/dormer-drill-candidates.json").read_text(encoding="utf-8"))
for candidate, old in zip(packet["candidates"], original["candidates"], strict=True):
    assert candidate["candidate_id"] == old["candidate_id"]
    assert candidate["parameters"] == old["parameters"]
    assert set(old["unresolved"]) <= set(candidate["unresolved"])
    assert candidate["eligible_for_recommendation"] is False
report = {"date": "2026-10-17", "local_regressions_passed": len(cases),
    "new_review_packet_tests": sum("test_source_review_packet" in case.attrib["classname"] for case in cases),
    "new_module_statement_coverage": coverage["percent_statements_covered"],
    "new_module_branch_coverage": coverage["percent_branches_covered"],
    "mutation_guards_caught": len(mutations), "review_packets": packet["count"],
    "original_candidate_ids_and_parameters_preserved": True, "original_blockers_preserved": True,
    "real_approvals_performed": 0, "machine_actions_performed": 0,
    "deployed_readonly": json.loads((directory / "deployed-readonly.json").read_text(encoding="utf-8")),
    "remaining_gaps": ["cutting_speed_unit", "actual_material_and_tool_confirmation",
        "automatic_applicability_matching", "live_service_reverification", "real_human_approval", "llm_autonomous_execution"]}
(directory / "acceptance-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False), flush=True)
