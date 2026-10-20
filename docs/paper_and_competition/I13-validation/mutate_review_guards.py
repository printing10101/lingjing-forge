"""In-memory mutations; production files and knowledge stores stay untouched."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import secrets

directory = Path(__file__).resolve().parent
root = directory.parents[2]
target = "engineering/python/tests/unit/test_process_review.py"
mutations = [
    ("content_binding", "process_review", 'review.get("digest") != content_digest(record)', 'False', "label_and_body"),
    ("unresolved", "process_review", 'evidence.get("unresolved") != []', 'False', "incomplete_evidence"),
    ("units", "process_review", 'set(units) != set(record["parameters"])', 'False', "incomplete_evidence"),
    ("version_conflict", "process_quadruple", 'current.revision != expected_revision', 'False', "stale_version"),
    ("write_rollback", "process_quadruple", 'self._all, self._dirty = previous, was_dirty', 'self._dirty = was_dirty', "failed_write"),
    ("pending_filter", "process_quadruple", 'and is_approved(q.to_dict())', 'and True', "complete_lifecycle"),
    ("reset_on_add", "process_quadruple", 'stored.review = {"status": "pending"}', 'stored.review = deepcopy(stored.review)', "label_and_body"),
]

if len(sys.argv) == 3 and sys.argv[1] == "--child":
    import pytest
    os.environ["LNN_ENV"] = "test"
    os.environ["LNN_JWT_SECRET"] = secrets.token_urlsafe(48)
    _, name, old, new, selection = mutations[int(sys.argv[2])]
    sys.path.insert(0, str(root / "engineering/python"))
    module = importlib.import_module("app.rag." + name)
    path = Path(module.__file__)
    source = path.read_text(encoding="utf-8")
    assert source.count(old) == 1

    class Mutate:
        def pytest_collection_modifyitems(self, items):
            exec(compile(source.replace(old, new, 1), str(path), "exec"), module.__dict__)
            if name == "process_review":
                index_module = importlib.import_module("app.rag.process_quadruple")
                index_module.is_approved = module.is_approved
                index_module.validate_approval = module.validate_approval
                for item in items:
                    item.module.is_approved = module.is_approved

    raise SystemExit(pytest.main([target, "--noconftest", "-k", selection, "-q"], plugins=[Mutate()]))

results = []
for position, (label, *_) in enumerate(mutations):
    run = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(position)], cwd=root,
                         capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    output = run.stdout + run.stderr
    (directory / f"mutation-{label}.log").write_text(output, encoding="utf-8")
    results.append({"name": label, "exit_code": run.returncode,
                    "caught": run.returncode == 1 and "FAILED " in output})
(directory / "mutations.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results), flush=True)
raise SystemExit(0 if all(row["caught"] for row in results) else 1)
