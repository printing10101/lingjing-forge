"""Check that fresh-authentication guards are exercised; no source-file mutation."""
import json
from pathlib import Path
import subprocess
import sys

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
source_path = root / "scripts/nexent_client.py"
target = "engineering/python/tests/unit/test_nexent_client.py"
mutants = [
    ("cookie", 'token = client.cookies.get("nexent_access_token")', "token = None", "expired_token"),
    ("fresh_header", 'client.headers["Authorization"] = "Bearer " + token\n        _verify_tools_response', "pass\n        _verify_tools_response", "expired_token"),
    ("response_shape", "if not isinstance(response.json(), list):", "if False:", "http_200"),
    ("server_failure", "if check.status_code != 401:", "if False:", "server_failure"),
    ("verify_before_save", '_verify_tools_response(client.get("/tool/list"))', "pass", "verification_failed"),
]
if len(sys.argv) == 3 and sys.argv[1] == "--child":
    import pytest
    _, old, new, selected = mutants[int(sys.argv[2])]
    source = source_path.read_text(encoding="utf-8")
    assert source.count(old) == 1
    class Mutate:
        def pytest_collection_modifyitems(self, items):
            module = items[0].module.module
            exec(compile(source.replace(old, new, 1), str(source_path), "exec"), module.__dict__)
    raise SystemExit(pytest.main([target, "--noconftest", "-k", selected, "-q"], plugins=[Mutate()]))
results = []
for index, (name, _, _, _) in enumerate(mutants):
    process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(index)],
                             cwd=root, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    output = process.stdout + process.stderr
    (evidence / f"mutation-client-{name}.log").write_text(output, encoding="utf-8")
    results.append({"name": name, "exit_code": process.returncode,
                    "caught": process.returncode == 1 and "FAILED " in output})
(evidence / "client-mutations.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results), flush=True)
raise SystemExit(0 if all(r["caught"] for r in results) else 1)
