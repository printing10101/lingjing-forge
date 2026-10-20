"""Start the desktop interpreter on an isolated port/SQLite DB and probe HTTP."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.request
import urllib.error

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
python_root = root / "engineering/python"
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
env = dict(os.environ, PYTHONIOENCODING="utf-8", LNN_ENV="test", SERVER_HOST="127.0.0.1",
           SERVER_PORT=str(port), LNN_JWT_SECRET=secrets.token_urlsafe(48), REDIS_URL="")
env.pop("PYTHONPATH", None)
report = {"port": port, "interpreter": "desktop_runtime/runtime/python.exe", "checks": [],
          "business_chain_validated": False, "authenticated_routes_validated": False}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with tempfile.TemporaryDirectory(prefix="lingjing-i8-api-") as scratch:
    env["DATABASE_URL"] = "sqlite+aiosqlite:///" + (Path(scratch) / "probe.db").as_posix()
    with (evidence / "desktop-backend.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(python_root / "desktop_runtime/runtime/python.exe"),
                                    str(python_root / "start_server.py")], cwd=python_root, env=env,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with opener.open(f"http://127.0.0.1:{port}/api/health/ping", timeout=2) as response:
                        payload = json.load(response)
                        assert response.status == 200
                        report["checks"].append({"path": "/api/health/ping", "status": 200, "response": payload})
                        break
                except (OSError, ValueError):
                    time.sleep(1)
            if report["checks"]:
                for path in ("/api/health", "/openapi.json"):
                    try:
                        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=15) as response:
                            payload = json.load(response)
                            if path == "/openapi.json":
                                payload = {"path_count": len(payload.get("paths", {})),
                                           "cam_route": "/api/agent/v1/cam/process-recommend" in payload.get("paths", {})}
                            report["checks"].append({"path": path, "status": response.status, "response": payload})
                    except urllib.error.HTTPError as exc:
                        report["checks"].append({"path": path, "status": exc.code,
                                                "expected_status": 401 if path == "/openapi.json" else 200})
                    except (OSError, ValueError) as exc:
                        report["checks"].append({"path": path, "error": str(exc)})
            report["premature_exit"] = process.poll()
            checks = {check["path"]: check for check in report["checks"]}
            report["http_ready"] = (
                checks.get("/api/health/ping", {}).get("response", {}).get("ping") is True
                and checks.get("/api/health", {}).get("response", {}).get("status") == "ok"
                and checks.get("/api/health", {}).get("response", {}).get("dependencies", {}).get("database") == "ok"
                and checks.get("/openapi.json", {}).get("status") == 401)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                report["stopped_by_probe"] = True
            report["final_exit_code"] = process.returncode
(evidence / "desktop-backend.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report.get("http_ready") else 1)
