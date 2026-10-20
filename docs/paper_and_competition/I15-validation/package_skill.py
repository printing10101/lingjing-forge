"""Build only the new Nexent skill, with a deterministic ZIP and verified tool names."""
import hashlib
import json
from pathlib import Path
import zipfile

import yaml

directory = Path(__file__).resolve().parent
root = directory.parents[2]
name = "manufacturing-knowledge-update"
source = root / "competition/nexent/skills" / name / "SKILL.md"
body = source.read_text(encoding="utf-8")
metadata = yaml.safe_load(body.split("---", 2)[1])
assert metadata["name"] == name
assert set(metadata["allowed-tools"]) == {"cam_submit_knowledge_candidate", "cam_get_knowledge_candidate", "cam_recommend_process"}
archive = root / "competition/nexent/dist" / f"{name}.zip"
entry = zipfile.ZipInfo("SKILL.md", date_time=(2026, 10, 17, 0, 0, 0))
entry.compress_type = zipfile.ZIP_DEFLATED
with zipfile.ZipFile(archive, "w") as output:
    output.writestr(entry, body.encode("utf-8"))
with zipfile.ZipFile(archive) as output:
    assert output.testzip() is None and output.namelist() == ["SKILL.md"]
report = {"name": name, "allowed_tools": metadata["allowed-tools"], "zip_integrity": True,
          "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "online_import_evidence": "nexent-skill-installation.json",
          "llm_autonomous_execution": False}
(directory / "skill-package.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
