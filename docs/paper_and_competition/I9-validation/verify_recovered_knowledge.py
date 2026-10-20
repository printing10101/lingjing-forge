"""Read-only acceptance of recovered search data and authenticated knowledge API."""
import hashlib
import json
from contextlib import closing
from pathlib import Path
import subprocess
import sys

from dotenv import dotenv_values

evidence = Path(__file__).resolve().parent
sys.path.insert(0, str(evidence.parents[2] / "scripts"))
from nexent_client import authenticated_client

deployment = Path("D:/nexent-deploy")
password = dotenv_values(deployment / "nexent/deploy/env/.env")["ELASTIC_PASSWORD"]
code = """
import httpx,json,sys
password=json.load(sys.stdin)
with httpx.Client(base_url='http://nexent-elasticsearch:9200',auth=('elastic',password),trust_env=False,timeout=20) as c:
    result={}
    for name,path in [('health','/_cluster/health'),('indices','/_cat/indices?format=json&expand_wildcards=all&h=index,uuid,docs.count')]:
        r=c.get(path); r.raise_for_status(); result[name]=r.json()
    print(json.dumps(result))
"""
probe = subprocess.run(["docker", "exec", "-i", "nexent-config", "python", "-c", code],
                       input=json.dumps(password), capture_output=True, encoding="utf-8", timeout=60, check=True)
live = json.loads(probe.stdout)
baseline = json.loads((evidence / "search-live-indices.json").read_text())
current = {row["uuid"]: int(row["docs.count"]) for row in live["indices"]}
counts_match = all(current.get(row["uuid"]) == int(row["docs.count"]) for row in baseline)
manifest = json.loads((evidence / "search-lab-preparation.json").read_text())["backup_files"]
original = Path("C:/Users/Lenovo/nexent-data/elasticsearch")
unchanged = all(hashlib.sha256((original / row["path"]).read_bytes()).hexdigest() == row["sha256"] for row in manifest)
report = {"health": live["health"], "original_indices": len(baseline), "original_index_counts_match": counts_match,
          "original_directory_unchanged": unchanged, "knowledge_api": []}
with closing(authenticated_client(deployment, timeout=60)) as client:
    response = client.get("/indices", params={"include_stats": True})
    report["knowledge_api"].append({"path": "/indices", "status": response.status_code, "body": response.json()})
    response.raise_for_status()
    for row in baseline:
        if row["index"].startswith(("1-", "2-")):
            response = client.get(f"/indices/{row['index']}/files")
            report["knowledge_api"].append({"path": f"/indices/{row['index']}/files", "status": response.status_code, "body": response.json()})
assert counts_match and unchanged and live["health"]["status"] == "green"
(evidence / "knowledge-recovery-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({key: value for key, value in report.items() if key != "knowledge_api"}, ensure_ascii=False))
print(json.dumps({"knowledge_api_statuses": [item["status"] for item in report["knowledge_api"]]}))
