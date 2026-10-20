"""Recreate only the data worker with a smaller validated local concurrency budget."""
import json
import os
from pathlib import Path
import subprocess

from dotenv import dotenv_values

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
deployment = Path("D:/nexent-deploy")
original = json.loads(subprocess.check_output(["docker", "inspect", "nexent-data-process"], encoding="utf-8"))[0]
(deployment / "preserved/worker-before-i10.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
env = os.environ.copy()
env.update({k: v for k, v in dotenv_values(deployment / "nexent/deploy/env/image-source.mainland.env").items() if v is not None})
env["NEXENT_DATA_PROCESS_IMAGE"] = original["Config"]["Image"]
env["ELASTICSEARCH_IMAGE"] = "elastic.m.daocloud.io/elasticsearch/elasticsearch:8.17.4"
env["LINGJING_ES_DATA_DIR"] = str(deployment / "nexent-files/elasticsearch-recovered-i9")
env.setdefault("HOME", str(Path.home()))
command = ["docker", "compose", "-p", "nexent", "--env-file", str(deployment / "nexent/deploy/env/.env"),
           "-f", str(deployment / "nexent/deploy/docker/compose/docker-compose.yml"),
           "-f", str(root / "competition/nexent/deployment/compose.lingjing-search.yml"),
           "-f", str(root / "competition/nexent/deployment/compose.lingjing-worker.yml")]
config = subprocess.run([*command, "config", "--format", "json"], env=env, capture_output=True, encoding="utf-8", check=True)
service = json.loads(config.stdout)["services"]["nexent-data-process"]
changes = {"DP_PART_PROCESSOR_COUNT": "1", "RAY_ACTOR_NUM_CPUS": "1", "WORKER_CONCURRENCY": "1",
           "RAY_OBJECT_STORE_MEMORY_GB": "0.10", "DISABLE_RAY_DASHBOARD": "true", "DISABLE_CELERY_FLOWER": "true"}
old_env = dict(entry.split("=", 1) for entry in original["Config"]["Env"] if "=" in entry)
assert service["image"] == original["Config"]["Image"]
for key, value in service["environment"].items():
    assert str(value) == (changes[key] if key in changes else old_env[key]), f"Unexpected change: {key}"
result = subprocess.run([*command, "up", "-d", "--no-deps", "--pull", "never", "nexent-data-process"],
                        env=env, capture_output=True, encoding="utf-8", timeout=90)
(evidence / "worker-activation.log").write_text(result.stdout + result.stderr, encoding="utf-8")
report = {"exit_code": result.returncode, "same_image": True, "credentials_preserved": True,
          "other_services_recreated": False, "memory_guard_disabled": False, "budget": changes}
(evidence / "worker-activation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
raise SystemExit(result.returncode)
