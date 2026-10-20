"""Compare every recovered index's document count before staging a separate live copy."""
import hashlib
import json
from pathlib import Path
import re
import shutil

evidence = Path(__file__).resolve().parent
health = json.loads((evidence / "search-lab-health.json").read_text())
assert health["status"] == "green" and health["active_primary_shards"] == 8 and health["unassigned_shards"] == 0
indices = json.loads((evidence / "search-lab-indices.json").read_text())
log = (evidence / "lucene-check.log").read_text(encoding="utf-8")
expected = {}
for block in log.split("CHECKING /work/indices/")[1:]:
    uuid = block.split("/", 1)[0]
    documents = [int(value) for value in re.findall(r"maxDoc=(\d+)", block)]
    assert block.count("no deletions") == len(documents), "Cannot infer live document counts"
    expected[uuid] = sum(documents)
assert len(indices) == len(expected) == 8
for index in indices:
    assert int(index["docs.count"]) == expected[index["uuid"]], index["index"]
original = Path("C:/Users/Lenovo/nexent-data/elasticsearch")
preparation = json.loads((evidence / "search-lab-preparation.json").read_text())
for entry in preparation["backup_files"]:
    assert hashlib.sha256((original / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
lab = Path("D:/nexent-deploy/preserved/es-i9-lab")
destination = Path("D:/nexent-deploy/nexent-files/elasticsearch-recovered-i9")
assert not destination.exists(), "Do not overwrite an existing recovered directory"
shutil.copytree(lab, destination, ignore=shutil.ignore_patterns("tools"))
report = {"lab_health": "green", "indices_verified": 8, "document_counts_match_offline_check": True,
          "original_files_unchanged": True, "activation_directory": str(destination),
          "index_document_counts": [{"index": index["index"], "uuid": index["uuid"], "documents": int(index["docs.count"])} for index in indices]}
(evidence / "search-activation-prepared.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({"indices_verified": 8, "document_counts_match": True, "original_files_unchanged": True}), flush=True)
