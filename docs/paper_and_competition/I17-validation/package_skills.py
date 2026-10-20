"""Prepare versioned local Skill packages; this script does not install them."""
import hashlib
import json
from pathlib import Path
import zipfile

directory = Path(__file__).resolve().parent
root = directory.parents[2]
results = []
for name in ("hole-process-draft", "manufacturing-evidence-review", "manufacturing-knowledge-update"):
    source = root / "competition/nexent/skills" / name / "SKILL.md"
    target = root / "competition/nexent/dist" / f"{name}-i17.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = source.read_bytes()
    assert b"conditions" in content
    info = zipfile.ZipInfo("SKILL.md", date_time=(2026, 10, 17, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(info, content)
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == ["SKILL.md"] and archive.read("SKILL.md") == content
    results.append({"name": name, "path": target.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "installed_this_round": False})
(directory / "skill-packages.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results), flush=True)
