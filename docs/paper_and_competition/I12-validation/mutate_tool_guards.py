"""In-memory regressions: no tool catalog or live service files are modified."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
mutations = [
    ("center_inherits_hole", "process_planning._matching_mixin", 'is_center_drill = process_name == "打中心孔定位"', "is_center_drill = False", "missing_center"),
    ("parameter_material", "data.process_data_manager", "if tool_material is not None and self.normalize_tool_material", "if False and self.normalize_tool_material", "parameter_filter"),
    ("grade_alias", "data.process_data_manager", "aliases = dict(self._LEGACY_PARAMETER_MATERIAL_IDS)", "aliases = {}", "real_material_alias"),
    ("midpoint", "data.process_data_manager", "return 0.0\n\n    @staticmethod\n    def _tool_application", "return (lower + upper) / 2\n\n    @staticmethod\n    def _tool_application", "family_range"),
    ("family_verified", "process_planning._scoring_mixin", "replace(tool, diameter_mm=target_diameter, diameter_verified=False)", "replace(tool, diameter_mm=target_diameter, diameter_verified=True)", "family_choice"),
    ("oversize_drill", "process_planning._scoring_mixin", 'if tools and all(tool.series == "twist_drill" for tool in tools):', "if False:", "fixed_exact"),
    ("candidate_score", "process_planning._scoring_mixin", "100.0 if tool.diameter_verified else 50.0", "100.0", "family_choice"),
]
target = "engineering/python/tests/unit/test_tool_material_consistency.py"
if len(sys.argv) == 3 and sys.argv[1] == "--child":
    import pytest
    _, name, old, new, selected = mutations[int(sys.argv[2])]
    sys.path.insert(0, str(root / "engineering/python"))
    module = importlib.import_module("app." + name)
    source_path = Path(module.__file__)
    source = source_path.read_text(encoding="utf-8")
    assert source.count(old) == 1
    class Mutate:
        def pytest_collection_modifyitems(self, items):
            exec(compile(source.replace(old, new, 1), str(source_path), "exec"), module.__dict__)
            for item in items:
                if hasattr(module, "ProcessPlanningDataManager"):
                    item.module.ProcessPlanningDataManager = module.ProcessPlanningDataManager
                if name.endswith("_scoring_mixin"):
                    for method in ("_select_best_diameter", "_calculate_suitability", "_build_match_reason", "_generate_warnings"):
                        setattr(item.module.ToolParamMatcher, method, getattr(module._ScoringMixin, method))
                if name.endswith("_matching_mixin"):
                    item.module.ToolParamMatcher.match_tool_for_hole = module._MatchingMixin.match_tool_for_hole
    raise SystemExit(pytest.main([target, "--noconftest", "-k", selected, "-q"], plugins=[Mutate()]))
results = []
for index, (label, *_) in enumerate(mutations):
    process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(index)], cwd=root,
                             capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    output = process.stdout + process.stderr
    (evidence / f"mutation-{label}.log").write_text(output, encoding="utf-8")
    results.append({"name": label, "exit_code": process.returncode, "caught": process.returncode == 1 and "FAILED " in output})
(evidence / "mutations.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results), flush=True)
raise SystemExit(0 if all(row["caught"] for row in results) else 1)
