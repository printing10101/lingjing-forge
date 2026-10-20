"""Check current and preserved ES credentials; output no credential values."""
import json
import subprocess
from pathlib import Path
from dotenv import dotenv_values

deployment = Path("D:/nexent-deploy")
evidence = Path(__file__).resolve().parent
candidates = []
for relative in ("nexent/deploy/env/.env", "preserved/.env"):
    values = dotenv_values(deployment / relative)
    if values.get("ELASTIC_PASSWORD"):
        candidates.append((relative, values["ELASTIC_PASSWORD"]))
saved = json.loads((deployment / "runtime_inspect.json").read_text(encoding="utf-8"))
if isinstance(saved, dict):
    saved = [saved]
for item in saved:
    for entry in item.get("Config", {}).get("Env", []):
        if entry.startswith("ELASTIC_PASSWORD="):
            candidates.append(("runtime_inspect.json", entry.split("=", 1)[1]))
seen = set()
unique = []
for source, password in candidates:
    if password in seen:
        continue
    seen.add(password)
    unique.append((source, password))
code = """
import json, sys, httpx
results=[]
for index,password in enumerate(json.load(sys.stdin)):
    response=httpx.get('http://nexent-elasticsearch:9200/_cluster/health',auth=('elastic',password),trust_env=False,timeout=10)
    item={'index':index,'http_status':response.status_code}
    if response.status_code==200:
        obj=response.json(); item.update(cluster_status=obj.get('status'),nodes=obj.get('number_of_nodes'))
    results.append(item)
print(json.dumps(results))
"""
probe = subprocess.run(["docker", "exec", "-i", "nexent-config", "python", "-c", code],
                       input=json.dumps([password for _, password in unique]), capture_output=True,
                       encoding="utf-8", timeout=40, check=True)
results = json.loads(probe.stdout)
for item in results:
    source, password = unique[item.pop("index")]
    item["credential_source"] = source
    if item["http_status"] == 200:
        # Save the already-existing working credential only in private deployment storage.
        (deployment / "preserved/es-working-password.txt").write_text(password, encoding="utf-8")
(evidence / "search-auth-probe.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results), flush=True)
