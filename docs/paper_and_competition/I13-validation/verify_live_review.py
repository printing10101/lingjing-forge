"""Read-only desktop HTTP acceptance; never inserts or approves production knowledge."""
import json
from pathlib import Path
import time

import httpx

directory = Path(__file__).resolve().parent
root = directory.parents[2]
with httpx.Client(base_url="http://127.0.0.1:8765", timeout=5, trust_env=False) as client:
    for attempt in range(12):
        try:
            response = client.get("/api/health")
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(5)
    else:
        raise RuntimeError("Desktop backend not ready")
    anonymous = client.get("/api/rag/process/review/records")
    assert anonymous.status_code in (401, 403)
    token = Path("D:/nexent-deploy/agent-token.txt").read_text(encoding="utf-8").strip()
    response = client.get("/api/agent/v1/cam/process-recommend", params={"feature": "hole", "material": "steel_45"},
                          headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["recommendation_status"] == "knowledge_empty"
    assert result["recommendations"] == []
report = {"desktop_health": True, "unauthenticated_review_status": anonymous.status_code,
          "formal_recommendation_status": result["recommendation_status"],
          "production_knowledge_file_absent": not (root / "engineering/python/data/entity_index/process_quadruple.json").exists(),
          "production_approval_performed": False, "machine_operations_performed": False}
(directory / "live-acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
