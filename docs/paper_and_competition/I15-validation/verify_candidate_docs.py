"""Explicitly check new nested gateway routes, beyond the legacy non-recursive scan."""
import json
from pathlib import Path
import sys

directory = Path(__file__).resolve().parent
root = directory.parents[2]
sys.path.insert(0, str(root / "scripts"))
from check_api_docs_sync import extract_routes_from_file, extract_routes_from_docs, compare_routes  # noqa: E402

routes = extract_routes_from_file(str(root / "engineering/python/app/api/v1/agent_gateway/knowledge_candidates.py"))
documented = extract_routes_from_docs(str(root / "docs/api-reference.md"))
comparison = compare_routes(routes, documented)
assert len(routes) == 2 and not comparison["code_only"]
report = {"new_routes": [{"method": route.method, "path": route.full_path} for route in routes],
          "missing_documentation": 0, "legacy_scan_is_non_recursive": True}
(directory / "candidate-docs-sync.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
