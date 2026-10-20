"""Use official temporary file-realm recovery access; issue diagnostic GETs only."""
import json
from pathlib import Path
import secrets
import subprocess

evidence = Path(__file__).resolve().parent
name = "lingjing_i9_recovery"
password = secrets.token_hex(24)
cli = ["docker", "exec", "-i", "nexent-elasticsearch", "/usr/share/elasticsearch/bin/elasticsearch-users"]
create = subprocess.run([*cli, "useradd", name, "-r", "superuser"],
                        input=password + "\n" + password + "\n", capture_output=True, encoding="utf-8", timeout=45)
assert create.returncode == 0, "Temporary monitoring account creation failed"
report = {"role": "superuser", "diagnostic_gets_only": True, "temporary_account_deleted": False, "queries": []}
try:
    code = """
import json,sys,httpx,time
name,password=json.load(sys.stdin)
c=httpx.Client(base_url='http://nexent-elasticsearch:9200',auth=(name,password),trust_env=False,timeout=15)
results=[]
for attempt in range(4):
    ready=c.get('/_cluster/health')
    if ready.status_code != 401:
        break
    time.sleep(6)
for path in ['/_cluster/health','/_cluster/allocation/explain','/_cat/shards?format=json','/_nodes/stats/fs?filter_path=nodes.*.fs.total']:
    r=c.get(path)
    results.append({'path':path,'status':r.status_code,'data':r.json()})
print(json.dumps(results))
"""
    result = subprocess.run(["docker", "exec", "-i", "nexent-config", "python", "-c", code],
                            input=json.dumps([name, password]), capture_output=True, encoding="utf-8", timeout=75, check=True)
    report["queries"] = json.loads(result.stdout)
finally:
    cleanup = subprocess.run([*cli, "userdel", name], capture_output=True, encoding="utf-8", timeout=30)
    report["temporary_account_deleted"] = cleanup.returncode == 0
    (evidence / "search-cluster-diagnostic.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False), flush=True)
