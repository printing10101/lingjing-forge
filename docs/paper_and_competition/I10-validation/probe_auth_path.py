"""Read auth reachability without printing credentials or returned sessions."""
import json
from pathlib import Path
import subprocess

evidence = Path(__file__).resolve().parent
credentials = json.loads(Path("D:/nexent-deploy/svc-login.json").read_text(encoding="utf-8"))
code = """
import os,json,sys,httpx
credentials=json.load(sys.stdin)
report=[]
for url in ['http://auth:9999/health',os.environ['SUPABASE_URL']+'/auth/v1/health']:
    try:
        r=httpx.get(url,headers={'apikey':os.environ['SUPABASE_KEY']},trust_env=False,timeout=8)
        report.append({'path':url,'status':r.status_code})
    except httpx.HTTPError as exc:
        report.append({'path':url,'error':type(exc).__name__})
try:
    r=httpx.post(os.environ['SUPABASE_URL']+'/auth/v1/token?grant_type=password',json=credentials,
                 headers={'apikey':os.environ['SUPABASE_KEY']},trust_env=False,timeout=12)
    report.append({'path':'kong password login','status':r.status_code,'session_returned':bool(r.json().get('access_token'))})
except httpx.HTTPError as exc:
    report.append({'path':'kong password login','error':type(exc).__name__})
print(json.dumps(report))
"""
result = subprocess.run(["docker", "exec", "-i", "nexent-config", "python", "-c", code],
                        input=json.dumps(credentials), capture_output=True, encoding="utf-8", timeout=45, check=True)
report = json.loads(result.stdout)
(evidence / "auth-path.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
