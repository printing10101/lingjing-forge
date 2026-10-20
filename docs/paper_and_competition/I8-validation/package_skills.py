"""Validate source skills with the installed Nexent loader and package ZIPs."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import zipfile

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
sys.path.insert(0, str(root))
os.environ.setdefault("LINGJING_MCP_DEV", "1")
os.environ.setdefault("LINGJING_AGENT_TOKEN", "local-package-validation-" + "x" * 40)
from mcp_server.cam_tools import register_cam_tools
from mcp_server.dxf_tools import register_dxf_tools
from mcp_server.process_planning_tools import register_process_planning_tools

class Registry:
    def __init__(self):
        self.names = set()
    def tool(self, name, description):
        def register(fn):
            self.names.add(name)
            return fn
        return register

registry = Registry()
for register in (register_cam_tools, register_dxf_tools, register_process_planning_tools):
    register(registry)
loader_path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("nexent_skill_loader", loader_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
package_root = root / "competition/nexent"
destination = package_root / "dist"
destination.mkdir(exist_ok=True)
results = []
for source in sorted((package_root / "skills").glob("*/SKILL.md")):
    skill = module.SkillLoader.load(str(source))
    assert skill["name"] == source.parent.name
    assert isinstance(skill["allowed_tools"], list) and skill["allowed_tools"]
    assert set(skill["allowed_tools"]) <= registry.names, skill["allowed_tools"]
    assert skill["description"] and skill["content"]
    archive = destination / f"{skill['name']}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.write(source, "SKILL.md")
    with zipfile.ZipFile(archive) as output:
        assert output.namelist() == ["SKILL.md"]
        assert output.testzip() is None
        assert output.read("SKILL.md") == source.read_bytes()
        parsed = module.SkillLoader.parse(output.read("SKILL.md").decode("utf-8"))
        assert parsed["allowed_tools"] == skill["allowed_tools"]
    results.append({"name": skill["name"], "tools": skill["allowed_tools"],
                    "archive": str(archive.relative_to(root)),
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "loader_parse": True, "zip_integrity": True,
                    "online_import": False, "agent_binding": False, "online_execution": False})
report = {"loader_path": str(loader_path), "loader_sha256": hashlib.sha256(loader_path.read_bytes()).hexdigest(),
          "locally_registered_tools": sorted(registry.names), "skills": results}
(evidence / "skills-package.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False), flush=True)
