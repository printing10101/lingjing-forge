"""In-memory fault injection against authenticated candidate workflow tests."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

directory = Path(__file__).resolve().parent
root = directory.parents[2]
target = "engineering/python/tests/unit/test_agent_knowledge_candidates.py"
mutations = [
    ("owner_isolation", "app.rag.process_quadruple", 'q.submission.get("agent_id") == agent_id', "True", "cross_agent_isolation"),
    ("content_conflict", "app.rag.process_quadruple", "existing.submission != quad.submission", "False", "cross_agent_isolation"),
    ("write_scope", "app.api.v1.agent_gateway.knowledge_candidates", 'write and "W" not in scopes', "False", "scope_guard_is_independent"),
    ("revision_preserves_submission", "app.rag.process_quadruple", "updated.submission = deepcopy(current.submission)", "updated.submission = {}", "survives_revision"),
    ("generic_cache_bypass", "app.auth.middleware", "None if durable_candidate_route else idempotency_store.check_and_set(idem_key, agent_id)", "idempotency_store.check_and_set(idem_key, agent_id)", "authenticated_roundtrip"),
    ("pending_recommendation_gate", "app.rag.process_quadruple", "and is_approved(q.to_dict())", "and True", "authenticated_roundtrip"),
    ("status_approval_gate", "app.api.v1.agent_gateway.knowledge_candidates", "eligible = is_approved(record)", "eligible = True", "mcp_handlers_traverse"),
    ("nonfinite_json", "app.api.v1.agent_gateway.knowledge_candidates", "json.dumps(value, allow_nan=False)", "json.dumps(value)", "nonfinite_json_is_rejected"),
    ("http_retry_classification", "mcp_server.knowledge_candidate_tools", "retryable = status >= 500 or status in {408, 429}", "retryable = True", "http_failures_are_actionable"),
]
if len(sys.argv) == 3 and sys.argv[1] == "--child":
    import pytest
    sys.path.insert(0, str(root / "engineering/python"))
    sys.path.insert(0, str(root))
    _, name, old, new, selection = mutations[int(sys.argv[2])]
    class Mutate:
        def pytest_collection_modifyitems(self, items):
            module = importlib.import_module(name)
            path = Path(module.__file__)
            source = path.read_text(encoding="utf-8")
            assert source.count(old) == 1
            exec(compile(source.replace(old, new, 1), str(path), "exec"), module.__dict__)
    raise SystemExit(pytest.main([target, "-k", selection, "-q"], plugins=[Mutate()]))
only = int(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == "--only" else None
results = json.loads((directory / "mutations.json").read_text(encoding="utf-8")) if only is not None else []
for position, (label, *_) in enumerate(mutations):
    if only is not None and position != only:
        continue
    run = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(position)], cwd=root,
                         capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    output = run.stdout + run.stderr
    (directory / f"mutation-{label}.log").write_text(output, encoding="utf-8")
    results = [row for row in results if row["name"] != label]
    results.append({"name": label, "exit_code": run.returncode, "caught": run.returncode == 1 and "FAILED " in output})
    print(json.dumps(results[-1]), flush=True)
(directory / "mutations.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
raise SystemExit(0 if all(row["caught"] for row in results) else 1)
