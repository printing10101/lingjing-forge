"""Compile final, non-secret evidence into a reproducible status summary."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

directory = Path(__file__).resolve().parent
def read(name):
    return json.loads((directory / name).read_text(encoding="utf-8-sig"))
suite = ET.parse(directory / "regression.xml").getroot().find("testsuite")
assert suite is not None and suite.get("failures") == "0" and suite.get("errors") == "0"
mutations = read("mutations.json")
assert len(mutations) == 9 and all(row["caught"] for row in mutations)
summary = {"regression_tests": int(suite.get("tests")), "new_module_coverage_percent": read("coverage.json")["totals"]["percent_covered"],
           "mutants_caught": len(mutations), "skill_installation": read("nexent-skill-installation.json"),
           "nexent_execution": read("nexent-candidate-execution.json"), "deployed_status": read("deployed-status.json"),
           "api_docs": read("api-docs-sync.json")["summary"], "llm_autonomous_execution": False, "machine_acceptance": False}
summary["new_candidate_docs"] = read("candidate-docs-sync.json")
summary["nexent_conflict"] = read("nexent-conflict-acceptance.json")
summary["final_registration_recheck_tests"] = len(ET.parse(directory / "final-registration-regression.xml").findall(".//testcase"))
(directory / "acceptance-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({key: summary[key] for key in ("regression_tests", "new_module_coverage_percent", "mutants_caught")}), flush=True)
