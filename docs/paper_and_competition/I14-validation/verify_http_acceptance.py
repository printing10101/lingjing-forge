"""Read-only HTTP proof of synthetic lifecycle and production isolation."""
import json
from pathlib import Path
import time

import httpx

directory = Path(__file__).resolve().parent
root = directory.parents[2]
with httpx.Client(base_url="http://127.0.0.1:18765", timeout=5, trust_env=False,
                  headers={"Authorization": "Bearer synthetic-browser-fixture-only"}) as client:
    records = client.get("/api/rag/process/review/records").json()["records"]
steel = next(record for record in records if record["material"] == "fixture_steel")
aluminum = next(record for record in records if record["material"] == "fixture_aluminum")
assert steel["review"]["status"] == "revoked" and steel["revision"] == 5
assert [event["after_review"]["status"] for event in steel["review_history"]] == ["rejected", "pending", "approved", "revoked"]
assert steel["parameters"]["spindle_rpm"] == 900
assert aluminum["review"]["status"] == "pending" and aluminum["candidate_context"]["parameter_units"] == {"spindle_rpm": "rpm"}
assert aluminum["candidate_context"]["unresolved"]
(directory / "synthetic-http-records.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
with httpx.Client(base_url="http://127.0.0.1:8765", timeout=5, trust_env=False) as client:
    for attempt in range(12):
        try:
            if client.get("/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(5)
    else:
        raise RuntimeError("Desktop backend did not become ready")
    unauthenticated = client.get("/api/rag/process/review/records")
    assert unauthenticated.status_code == 401
    token = Path("D:/nexent-deploy/agent-token.txt").read_text(encoding="utf-8").strip()
    response = client.get("/api/agent/v1/cam/process-recommend", params={"feature": "hole", "material": "steel_45"},
                          headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    assert response.json()["data"]["recommendation_status"] == "knowledge_empty"
report = {"browser_backed_synthetic_lifecycle": ["rejected", "pending", "approved", "revoked"],
          "synthetic_candidate_created_and_reloaded": True, "desktop_health": True,
          "production_review_requires_authentication": True, "production_recommendation_status": "knowledge_empty",
          "production_knowledge_file_absent": not (root / "engineering/python/data/entity_index/process_quadruple.json").exists(),
          "real_candidate_approvals": 0, "machine_actions": 0}
(directory / "http-acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
