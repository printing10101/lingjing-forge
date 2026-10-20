"""Upload the authored ZIPs, verify tool binding and attach to the existing draft."""
import hashlib
import json
from pathlib import Path
import httpx
import yaml

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
private = Path("D:/nexent-deploy/preserved")
private.mkdir(exist_ok=True)
client = httpx.Client(base_url="http://127.0.0.1:3000/api", timeout=30, trust_env=False,
    headers={"Authorization": "Bearer " + Path("D:/nexent-deploy/svc-token.txt").read_text().strip()})
def request(method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()

tools = request("GET", "/tool/list")
tool_ids = {t.get("name") or t.get("tool_name"): t.get("tool_id") or t.get("id") for t in tools}
before = request("POST", "/agent/search_info", json={"agent_id": 1, "version_no": 0})
assert before["name"] == "process_advisor", "Unexpected target agent"
backup = private / "agent-before-i9.json"
if not backup.exists():
    backup.write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
existing = {s["name"]: s for s in request("GET", "/skills")["skills"]}
results = []
for source in sorted((root / "competition/nexent/skills").glob("*/SKILL.md")):
    text = source.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    meta = yaml.safe_load(frontmatter)
    expected_ids = sorted(tool_ids[name] for name in meta["allowed-tools"])
    name = meta["name"]
    archive = root / "competition/nexent/dist" / f"{name}.zip"
    if name in existing:
        skill = existing[name]
    else:
        skill = request("POST", "/skills/upload", files={"file": (archive.name, archive.read_bytes(), "application/zip")},
                        data={"skill_name": name, "source": "custom"})
    skill_id = skill.get("skill_id") or skill.get("id")
    skill = request("GET", f"/skills/{skill_id}")
    assert skill["name"] == name
    assert skill["content"].strip() == body.strip(), f"Content mismatch: {name}"
    assert sorted(skill["tool_ids"]) == expected_ids, f"Tool binding mismatch: {name}"
    results.append({"name": name, "skill_id": skill_id, "tool_ids": expected_ids,
                    "content_match": True, "zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "upload_verified": True})
    (evidence / "skills-upload.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

old_ids = {s["skill_id"] for s in before.get("skills", []) if s.get("enabled", True)}
bound_ids = sorted(old_ids | {s["skill_id"] for s in results})
payload = {"agent_id": 1, "enabled_skill_ids": bound_ids}
for key in ("prompt_template_id", "prompt_template_name"):
    if before.get(key) is not None:
        payload[key] = before[key]
request("POST", "/agent/update", json=payload)
after = request("POST", "/agent/search_info", json={"agent_id": 1, "version_no": 0})
preserved = ("name", "display_name", "model_ids", "max_steps", "duty_prompt", "constraint_prompt",
             "few_shots_prompt", "tools", "sub_agent_id_list", "prompt_template_id", "prompt_template_name")
drift = [key for key in preserved if after.get(key) != before.get(key)]
assert not drift, f"Unexpected existing config changes: {drift}"
actual_ids = {s["skill_id"] for s in after.get("skills", []) if s.get("enabled", True)}
assert actual_ids == set(bound_ids), (actual_ids, bound_ids)
report = {"agent_id": 1, "name": after["name"], "skills": results, "bound_skill_ids": bound_ids,
          "existing_config_preserved": True, "model_execution_verified": False}
(evidence / "skills-binding.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False), flush=True)
