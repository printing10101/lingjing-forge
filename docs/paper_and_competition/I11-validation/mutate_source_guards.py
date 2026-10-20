"""Verify source guards by in-memory mutations, leaving service files unchanged."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
mutations = [
    ("false_verified", "process_planning._tool_models", '"source_verified": False', '"source_verified": True', "test_cutting_parameter_provenance", "source_survives"),
    ("hash", "rag.source_candidates", 'if digest != manifest.get("sha256"):', "if False:", "test_source_candidates", "invalid_source"),
    ("unit", "rag.source_candidates", "feed_mm = feed * 25.4", "feed_mm = feed", "test_source_candidates", "confirmed_feed"),
    ("review", "rag.source_candidates", '"eligible_for_recommendation": False', '"eligible_for_recommendation": True', "test_source_candidates", "confirmed_feed"),
    ("candidate_gate", "rag.process_quadruple", '"example", "manufacturer_candidate",', '"example",', "test_process_source_gate", "unverified_sources"),
    ("missing_source", "rag.process_quadruple", 'source=data.get("source", "unverified")', 'source=data.get("source", "manual")', "test_process_source_gate", "unverified_sources"),
]
if len(sys.argv) == 3 and sys.argv[1] == "--child":
    import pytest
    _, name, old, new, target, selected = mutations[int(sys.argv[2])]
    sys.path.insert(0, str(root / "engineering/python"))
    module = importlib.import_module("app." + name)
    source_path = Path(module.__file__)
    source = source_path.read_text(encoding="utf-8")
    assert source.count(old) == 1
    class Mutate:
        def pytest_collection_modifyitems(self, items):
            exec(compile(source.replace(old, new, 1), str(source_path), "exec"), module.__dict__)
            for item in items:
                for symbol in ("build_vendor_candidates", "ProcessQuadruple", "ProcessQuadrupleIndex", "MatchedTool"):
                    if hasattr(module, symbol) and hasattr(item.module, symbol):
                        setattr(item.module, symbol, getattr(module, symbol))
    raise SystemExit(pytest.main([f"engineering/python/tests/unit/{target}.py", "--noconftest", "-k", selected, "-q"], plugins=[Mutate()]))
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
