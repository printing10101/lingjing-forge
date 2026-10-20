"""Inspect deployed candidates; never submit, revise, approve or start services."""
import json
from pathlib import Path

import httpx

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]


def main():
    report = {"write_requests": 0, "machine_actions": 0, "request_timeout_seconds": 15, "checks": []}
    previous = json.loads((DIRECTORY.parent / "I15-validation/nexent-candidate-execution.json").read_text(encoding="utf-8"))
    token = Path("D:/nexent-deploy/agent-token.txt").read_text(encoding="utf-8").strip()
    try:
        with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, timeout=15) as client:
            health = client.get("/api/health")
            health.raise_for_status()
            report["backend_reachable"] = True
            headers = {"Authorization": "Bearer " + token}
            for candidate in previous["real_vendor_candidates"]:
                status = client.get("/api/agent/v1/cam/knowledge-candidates/" + candidate["record_id"], headers=headers)
                status.raise_for_status()
                data = status.json()["data"]
                recommendation = client.get("/api/agent/v1/cam/process-recommend", headers=headers,
                    params={"feature": "hole", "material": candidate["material"]})
                recommendation.raise_for_status()
                report["checks"].append({"record_id": candidate["record_id"], "material": candidate["material"],
                    "review_status": data["record"]["review"]["status"],
                    "revision": data["record"]["revision"], "eligible": data["eligible_for_recommendation"],
                    "recommendation_count": len(recommendation.json()["data"]["recommendations"])})
    except httpx.HTTPError as error:
        # Do not serialize requests or headers, which contain credentials.
        report["backend_reachable"] = bool(report.get("backend_reachable"))
        report["verification_complete"] = False
        report["failure_type"] = type(error).__name__
    else:
        report["verification_complete"] = True
    (DIRECTORY / "deployed-readonly.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
