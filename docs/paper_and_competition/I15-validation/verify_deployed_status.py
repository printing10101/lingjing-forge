"""Read-only deployed status verification after the final backend restart."""
import json
from pathlib import Path
import time

import httpx

directory = Path(__file__).resolve().parent
token = Path("D:/nexent-deploy/agent-token.txt").read_text(encoding="utf-8").strip()
with httpx.Client(base_url="http://127.0.0.1:8765", timeout=4, trust_env=False) as client:
    for _ in range(12):
        try:
            if client.get("/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(5)
    else:
        raise RuntimeError("Desktop backend not ready")
    assert client.get("/api/rag/process/review/records").status_code == 401
    headers = {"Authorization": "Bearer " + token}
    stats = client.get("/api/agent/v1/cam/quadruple-stats", headers=headers).json()["data"]
    assert stats["total_quadruples"] == 3 and stats["approved_quadruples"] == 0
    response = client.get("/api/agent/v1/cam/process-recommend", params={"feature": "hole", "material": "dormer_amg_1_3"}, headers=headers)
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["recommendation_status"] == "knowledge_requires_review" and result["recommendations"] == []
report = {"desktop_health": True, "unauthenticated_review_rejected": True,
          "total_quadruples": stats["total_quadruples"], "approved_quadruples": stats["approved_quadruples"],
          "recommendation_status": result["recommendation_status"], "machine_actions": 0}
(directory / "deployed-status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
