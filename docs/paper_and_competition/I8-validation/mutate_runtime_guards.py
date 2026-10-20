"""Mutate loaded build functions in child processes; never edit working files."""
import json
from pathlib import Path
import subprocess
import sys

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
script = root / "engineering/python/scripts/build_desktop_runtime.py"
target = "engineering/python/tests/unit/test_desktop_runtime_build.py"
mutations = [
    ("native_exit", "if probe.returncode != 0:", "if False:", "native_probe_requires_clean_exit"),
    ("nan_geometry", "or not math.isfinite(volume)", "", "invalid_geometry_is_rejected"),
    ("source_overlap", 'raise ValueError(f"Build source and destination overlap: {source} / {resolved}")', "pass", "dependency_source_inside_output"),
    ("stale_manifest", '(runtime_dir / "runtime.json").unlink(missing_ok=True)', "pass", "build_publishes_manifest_only_after_native_success"),
    ("copy_ignore", "dirs[:] = [name for name in dirs if name not in ignore]", "dirs[:] = dirs", "copy_tree_excludes"),
    ("clear_boundary", "if not target.resolve().is_relative_to(root) or target.resolve() == root:", "if False:", "clear_rejects_wrong_target"),
]

if len(sys.argv) == 3 and sys.argv[1] == "--child":
    import pytest
    _, old, new, select = mutations[int(sys.argv[2])]
    source = script.read_text(encoding="utf-8")
    assert source.count(old) == 1
    class Mutation:
        def pytest_collection_modifyitems(self, items):
            module = items[0].module.build
            exec(compile(source.replace(old, new, 1), str(script), "exec"), module.__dict__)
    raise SystemExit(pytest.main([target, "-k", select, "-q"], plugins=[Mutation()]))

results = []
for index, (name, _, _, _) in enumerate(mutations):
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(index)],
                          cwd=root, capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    output = proc.stdout + proc.stderr
    (evidence / f"mutation-{name}.log").write_text(output, encoding="utf-8")
    results.append({"name": name, "exit_code": proc.returncode,
                    "caught": proc.returncode == 1 and "FAILED " in output})
(evidence / "runtime-mutations.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results), flush=True)
raise SystemExit(0 if all(r["caught"] for r in results) else 1)
