"""In-memory fault injection; repository and live service source remain unchanged."""
import json
from pathlib import Path
import subprocess
import sys

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
target = "engineering/python/tests/unit/test_process_knowledge_persistence.py"
mutations = [
    ("ack_without_save", "if self._persist_path and not self.flush(force=True):", "if False:", "acknowledged_write"),
    ("no_memory_rollback", "self._all = previous", "pass", "failed_replace"),
    ("wrong_material", 'q.material in (material, "general")', "True", "material_mismatch"),
    ("example_recommendation", 'and q.source != "example"', "", "explicit_examples"),
    ("corrupt_as_empty", 'raise RuntimeError(\n                "[知识库错误]', 'return\n            raise RuntimeError(\n                "[知识库错误]', "corrupt_knowledge"),
]

if len(sys.argv) == 3 and sys.argv[1] == "--child":
    import pytest
    sys.path.insert(0, str(root / "engineering/python"))
    _, old, new, selected = mutations[int(sys.argv[2])]
    source_path = root / "engineering/python/app/rag/process_quadruple.py"
    source = source_path.read_text(encoding="utf-8")
    assert source.count(old) == 1
    class Mutate:
        def pytest_collection_modifyitems(self, items):
            module = items[0].module.module
            exec(compile(source.replace(old, new, 1), str(source_path), "exec"), module.__dict__)
    raise SystemExit(pytest.main([target, "--noconftest", "-k", selected, "-q"], plugins=[Mutate()]))

results = []
for index, (name, _, _, _) in enumerate(mutations):
    process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(index)],
                             cwd=root, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    output = process.stdout + process.stderr
    (evidence / f"mutation-{name}.log").write_text(output, encoding="utf-8")
    results.append({"name": name, "exit_code": process.returncode,
                    "caught": process.returncode == 1 and "FAILED " in output})
(evidence / "mutations.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results), flush=True)
raise SystemExit(0 if all(r["caught"] for r in results) else 1)
