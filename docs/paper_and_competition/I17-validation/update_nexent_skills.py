"""Refresh existing MCP schemas and update existing draft Skills without duplicating them."""
from contextlib import closing
import json
from pathlib import Path
import sys

import yaml

directory = Path(__file__).resolve().parent
root = directory.parents[2]
sys.path.insert(0, str(root / "scripts"))
from nexent_client import authenticated_client  # noqa: E402


def has_conditions(value):
    if isinstance(value, dict):
        return "conditions" in value or value.get("name") == "conditions" or any(has_conditions(item) for item in value.values())
    if isinstance(value, list):
        return any(has_conditions(item) for item in value)
    return False


def main():
    with closing(authenticated_client(Path("D:/nexent-deploy"), timeout=20)) as client:
        client.post("/mcp/refresh-tools", params={"mcp_id": 1}, timeout=60).raise_for_status()
        client.get("/tool/scan_tool", timeout=60).raise_for_status()
        tool_response = client.get("/tool/list")
        tool_response.raise_for_status()
        tools = {tool["name"]: tool for tool in tool_response.json()}
        assert has_conditions(tools["cam_recommend_process"]), "Nexent still has an outdated tool schema"
        skills_response = client.get("/skills")
        skills_response.raise_for_status()
        skills = {skill["name"]: skill for skill in skills_response.json()["skills"]}
        before_response = client.get("/skills/instance/list", params={"agent_id": 1, "version_no": 0})
        before_response.raise_for_status()
        before = before_response.json()
        rows = before if isinstance(before, list) else before.get("instances", before.get("data", []))
        enabled_before = {row["skill_id"]: row.get("enabled") for row in rows}
        reports = []
        for name in ("hole-process-draft", "manufacturing-evidence-review", "manufacturing-knowledge-update"):
            skill = skills[name]
            archive = root / "competition/nexent/dist" / f"{name}-i17.zip"
            with archive.open("rb") as file:
                response = client.put(f"/skills/{name}/upload", files={"file": (archive.name, file, "application/zip")}, timeout=60)
            response.raise_for_status()
            detail_response = client.get(f'/skills/{skill["skill_id"]}')
            detail_response.raise_for_status()
            detail = detail_response.json()
            meta = yaml.safe_load((root / "competition/nexent/skills" / name / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1])
            allowed = meta.get("allowed_tools", meta.get("allowed-tools"))
            expected = {tools[tool]["tool_id"] for tool in allowed}
            assert set(detail["tool_ids"]) == expected
            assert "conditions" in detail.get("content", "")
            assert detail["skill_id"] == skill["skill_id"]
            reports.append({"name": name, "skill_id": skill["skill_id"], "content_verified": True, "tool_mapping_verified": True})
        after_response = client.get("/skills/instance/list", params={"agent_id": 1, "version_no": 0})
        after_response.raise_for_status()
        after = after_response.json()
        rows = after if isinstance(after, list) else after.get("instances", after.get("data", []))
        enabled_after = {row["skill_id"]: row.get("enabled") for row in rows}
        assert enabled_before == enabled_after
        report = {"updated_existing_skills": reports, "agent_id": 1, "draft_bindings_preserved": True,
                  "conditions_schema_verified": True, "llm_autonomous_execution": False}
    (directory / "nexent-deployment.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
