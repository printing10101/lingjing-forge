"""Prepare a reversible lab copy only after every offline Lucene check passes."""
import hashlib
import json
from pathlib import Path
from dotenv import dotenv_values

evidence = Path(__file__).resolve().parent
original = Path("<用户主目录>/nexent-data/elasticsearch").resolve()
backup = Path("D:/nexent-deploy/preserved/es-before-i9").resolve()
lab = Path("D:/nexent-deploy/preserved/es-i9-lab").resolve()
quarantine = Path("D:/nexent-deploy/preserved/es-i9-markers").resolve()
assert len({original, backup, lab, quarantine}) == 4
indices = sorted((lab / "indices").glob("*/0/index"))
log = (evidence / "lucene-check.log").read_text(encoding="utf-8")
assert len(indices) == 8 and log.count("No problems were detected with this index.") == 8
assert "WARNING: would write new segments file" not in log
manifest = []
for path in sorted(backup.rglob("*")):
    if not path.is_file():
        continue
    relative = path.relative_to(backup)
    content = path.read_bytes()
    assert (original / relative).read_bytes() == content, f"Original changed: {relative}"
    assert (lab / relative).read_bytes() == content, f"Lab differs: {relative}"
    manifest.append({"path": relative.as_posix(), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
quarantine.mkdir(exist_ok=True)
moved = []
for marker in sorted((lab / "indices").glob("*/0/index/corrupted_*")):
    assert marker.resolve().is_relative_to(lab / "indices")
    content = marker.read_bytes()
    assert b"Cannot allocate memory" in content, "Unexpected corruption reason; stop recovery"
    destination = quarantine / (marker.parents[2].name + "-" + marker.name)
    assert destination.resolve().is_relative_to(quarantine) and not destination.exists()
    marker.rename(destination)
    moved.append({"relative_path": marker.relative_to(lab).as_posix(), "sha256": hashlib.sha256(content).hexdigest(),
                  "reason": "Cannot allocate memory", "quarantined_in_lab_only": True})
assert len(moved) == 4
password = dotenv_values("D:/nexent-deploy/nexent/deploy/env/.env")["ELASTIC_PASSWORD"]
settings = {"ELASTIC_PASSWORD": password, "ES_JAVA_OPTS": "-Xms512m -Xmx512m", "discovery.type": "single-node",
            "node.name": "es-i9-recovery", "xpack.security.enabled": "true",
            "xpack.security.http.ssl.enabled": "false", "xpack.security.transport.ssl.enabled": "false"}
Path("D:/nexent-deploy/preserved/es-i9-lab.env").write_text("\n".join(f"{k}={v}" for k, v in settings.items()) + "\n", encoding="utf-8")
report = {"offline_indices_checked": 8, "clean_indices": 8, "backup_files": manifest,
          "quarantined_markers": moved, "original_data_modified": False}
(evidence / "search-lab-preparation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({"clean_indices": 8, "backup_files": len(manifest), "lab_markers_quarantined": len(moved),
                  "original_data_modified": False}), flush=True)
