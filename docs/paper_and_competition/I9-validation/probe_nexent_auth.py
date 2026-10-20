"""Check deployed authenticated APIs without logging account credentials."""
import json
from pathlib import Path

folder = Path("D:/nexent-deploy")
evidence = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(evidence.parents[2] / "scripts"))
from nexent_client import authenticated_client

client = authenticated_client(folder, timeout=30)
report = {"calls": []}
for path in ("/skills", "/tool/list"):
    response = client.get(path)
    item = {"path": path, "status": response.status_code}
    if response.status_code == 200:
        data = response.json()
        item["response_type"] = type(data).__name__
        item["keys"] = list(data) if isinstance(data, dict) else []
        if path == "/skills":
            item["skills"] = [{"name": s.get("name"), "id": s.get("id") or s.get("skill_id")}
                              for s in data.get("skills", [])]
    report["calls"].append(item)
response = client.post("/agent/search_info", json={"agent_id": 1, "version_no": 0})
item = {"path": "/agent/search_info", "status": response.status_code}
if response.status_code == 200:
    data = response.json().get("data", {})
    item["keys"] = list(data)
    item["tools"] = [{"id": t.get("tool_id") or t.get("id"), "name": t.get("name") or t.get("tool_name")}
                     for t in data.get("tools", [])]
    item["skill_count"] = len(data.get("skills") or [])
report["calls"].append(item)
(evidence / "nexent-auth.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False), flush=True)

client.close()
