"""Verify a conflicting retry is rejected through real Nexent without altering the candidate."""
from contextlib import closing
import json
from pathlib import Path
import sys

import httpx

directory = Path(__file__).resolve().parent
root = directory.parents[2]
sys.path.insert(0, str(root / "scripts"))
from nexent_client import authenticated_client  # noqa: E402

def unpack(value):
    for _ in range(10):
        if isinstance(value, str):
            value = json.loads(value)
        elif isinstance(value, dict) and value.get("type") == "text":
            value = value["text"]
        elif isinstance(value, list) and len(value) == 1:
            value = value[0]
        else:
            return value
    raise ValueError("Unexpected response nesting")

record_id = json.loads((directory / "nexent-candidate-execution.json").read_text(encoding="utf-8"))["real_vendor_candidates"][0]["record_id"]
token = Path("D:/nexent-deploy/agent-token.txt").read_text(encoding="utf-8").strip()
with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, headers={"Authorization": "Bearer " + token}) as backend:
    response = backend.get(f"/api/agent/v1/cam/knowledge-candidates/{record_id}")
    response.raise_for_status()
    original = response.json()["data"]["record"]
    content = {key: original[key] for key in ("feature", "process", "tool", "material", "parameters", "candidate_context", "chunk_ids", "tags")}
    content["parameters"] = {**content["parameters"], "feed_per_revolution_mm": original["parameters"]["feed_per_revolution_mm"] + 0.01}
    with closing(authenticated_client(Path("D:/nexent-deploy"), timeout=40)) as client:
        tool = next(tool for tool in client.get("/tool/list").json() if tool["name"] == "cam_submit_knowledge_candidate")
        response = client.post("/tool/validate", json={"name": tool["name"], "source": tool["source"], "usage": tool["usage"],
            "inputs": {"submission_id": original["submission"]["submission_id"], "content": content}})
        response.raise_for_status()
        result = unpack(response.json())
        assert result["error"] and result["detail"]["http_status"] == 409 and result["detail"]["retryable"] is False
    after = backend.get(f"/api/agent/v1/cam/knowledge-candidates/{record_id}").json()["data"]["record"]
    assert after == original
report = {"nexent_conflict_observed": True, "http_status": 409, "retryable": False, "candidate_unchanged": True}
(directory / "nexent-conflict-acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
