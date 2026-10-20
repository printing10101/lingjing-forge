"""In-memory tests of candidate annotation validation and preservation."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

directory = Path(__file__).resolve().parent
root = directory.parents[2]
target = "engineering/python/tests/unit/test_process_review.py"
mutations = [
    ("context_preserved", 'candidate_context=deepcopy(data.get("candidate_context", {}))', "candidate_context={}", "pending_context_survives"),
    ("context_object", 'if not isinstance(self.candidate_context, dict):', "if False:", "malformed_pending_context"),
    ("unknown_context_field", 'if set(context) - text_fields - {"page", "parameter_units", "unresolved"}:', "if False:", "malformed_pending_context"),
    ("context_text", 'if any(not isinstance(context[key], str) for key in text_fields & context.keys()):', "if False:", "malformed_pending_context"),
    ("context_page", 'if context.get("page") is not None and (type(context["page"]) is not int or context["page"] < 1):', "if False:", "malformed_pending_context"),
    ("context_units", 'if not isinstance(units, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in units.items()):', "if False:", "malformed_pending_context"),
    ("context_unresolved", 'if not isinstance(unresolved, list) or any(not isinstance(value, str) for value in unresolved):', "if False:", "malformed_pending_context"),
]
if len(sys.argv) == 3 and sys.argv[1] == "--child":
    import pytest
    sys.path.insert(0, str(root / "engineering/python"))
    module = importlib.import_module("app.rag.process_quadruple")
    source_path = Path(module.__file__)
    source = source_path.read_text(encoding="utf-8")
    _, old, new, selection = mutations[int(sys.argv[2])]
    assert source.count(old) == 1

    class Mutate:
        def pytest_collection_modifyitems(self, items):
            exec(compile(source.replace(old, new, 1), str(source_path), "exec"), module.__dict__)

    raise SystemExit(pytest.main([target, "-k", selection, "-q"], plugins=[Mutate()]))
results = []
for position, (label, *_) in enumerate(mutations):
    run = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(position)], cwd=root,
                         capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    output = run.stdout + run.stderr
    (directory / f"mutation-{label}.log").write_text(output, encoding="utf-8")
    results.append({"name": label, "exit_code": run.returncode, "caught": run.returncode == 1 and "FAILED " in output})
(directory / "candidate-context-mutations.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results), flush=True)
raise SystemExit(0 if all(row["caught"] for row in results) else 1)
