"""Refresh existing MCP registration, install one skill, bind its draft, and read back."""
from contextlib import closing
import json
from pathlib import Path
import sys

directory = Path(__file__).resolve().parent
root = directory.parents[2]
sys.path.insert(0, str(root / "scripts"))
from nexent_client import authenticated_client  # noqa: E402

name = "manufacturing-knowledge-update"
with closing(authenticated_client(Path("D:/nexent-deploy"), timeout=60)) as client:
    response = client.post("/mcp/refresh-tools", params={"mcp_id": 1})
    response.raise_for_status()
    response = client.get("/tool/scan_tool")
    response.raise_for_status()
    tools = {tool["name"]: tool for tool in client.get("/tool/list").json()}
    wanted = ["cam_submit_knowledge_candidate", "cam_get_knowledge_candidate", "cam_recommend_process"]
    assert all(tool in tools for tool in wanted)
    skills = client.get("/skills").json()["skills"]
    found = next((skill for skill in skills if skill["name"] == name), None)
    if found is None:
        archive = root / "competition/nexent/dist" / f"{name}.zip"
        with archive.open("rb") as source:
            response = client.post("/skills/upload", files={"file": (archive.name, source, "application/zip")},
                                   data={"skill_name": name, "source": "custom"})
        if response.status_code >= 400:
            error = response.json()
            print(json.dumps({"skill_upload_status": response.status_code, "keys": list(error),
                              "detail": error.get("detail"), "message": error.get("message"), "error": error.get("error")}), flush=True)
        response.raise_for_status()
        found = response.json()
    skill_id = found["skill_id"]
    detail = client.get(f"/skills/{skill_id}").json()
    assert set(detail["tool_ids"]) == {tools[tool]["tool_id"] for tool in wanted}
    response = client.post("/skills/instance/update", json={"skill_id": skill_id, "agent_id": 1, "enabled": True, "version_no": 0})
    response.raise_for_status()
    instances = client.get("/skills/instance/list", params={"agent_id": 1, "version_no": 0}).json()
    rows = instances if isinstance(instances, list) else instances.get("instances", instances.get("data", []))
    assert any(row["skill_id"] == skill_id and row.get("enabled") for row in rows)
    report = {"skill": name, "skill_id": skill_id, "agent_id": 1, "bound_to_draft": True,
              "tools": [{"name": tool, "tool_id": tools[tool]["tool_id"]} for tool in wanted],
              "tool_mapping_verified": True, "llm_autonomous_execution": False}
(directory / "nexent-skill-installation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
