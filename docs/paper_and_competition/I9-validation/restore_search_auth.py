"""Restore ES's built-in account to the already-configured deployment password."""
import json
from pathlib import Path
import subprocess
from dotenv import dotenv_values

evidence = Path(__file__).resolve().parent
deployment = Path("D:/nexent-deploy")
password = dotenv_values(deployment / "nexent/deploy/env/.env").get("ELASTIC_PASSWORD")
assert isinstance(password, str) and len(password) >= 6 and "\n" not in password
result = subprocess.run([
    "docker", "exec", "-i", "nexent-elasticsearch",
    "/usr/share/elasticsearch/bin/elasticsearch-reset-password", "-u", "elastic", "-i", "-b",
], input=password + "\n" + password + "\n", capture_output=True, encoding="utf-8", errors="replace", timeout=90)
# Keep diagnostics private; no terminal transcripts or passwords enter acceptance artifacts.
(deployment / "preserved/es-auth-restore-i9.log").write_text(result.stdout + result.stderr, encoding="utf-8")
report = {"exit_code": result.returncode,
          "reset_confirmed": "successfully reset" in result.stdout.lower(),
          "configured_password_changed": False, "indices_deleted": False,
          "forced_unhealthy_reset": False}
(evidence / "search-auth-restore.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
raise SystemExit(result.returncode)
