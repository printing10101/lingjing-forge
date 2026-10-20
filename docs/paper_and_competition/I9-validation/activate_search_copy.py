"""Activate only the verified ES copy, preserving image, credentials and other services."""
import json
import os
from pathlib import Path
import subprocess
from dotenv import dotenv_values

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
deployment = Path("D:/nexent-deploy")
original = json.loads(subprocess.check_output(["docker", "inspect", "nexent-elasticsearch"], encoding="utf-8"))[0]
(deployment / "preserved/es-container-before-i9.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
env = os.environ.copy()
env.update({k: v for k, v in dotenv_values(deployment / "nexent/deploy/env/image-source.mainland.env").items() if v is not None})
env["ELASTICSEARCH_IMAGE"] = original["Config"]["Image"]
env["LINGJING_ES_DATA_DIR"] = str(deployment / "nexent-files/elasticsearch-recovered-i9")
env.setdefault("HOME", str(Path.home()))
project = original["Config"]["Labels"]["com.docker.compose.project"]
command = ["docker", "compose", "-p", project, "--env-file", str(deployment / "nexent/deploy/env/.env"),
           "-f", str(deployment / "nexent/deploy/docker/compose/docker-compose.yml"),
           "-f", str(root / "competition/nexent/deployment/compose.lingjing-search.yml")]
config = subprocess.run([*command, "config", "--format", "json"], env=env, capture_output=True, encoding="utf-8", check=True)
service = json.loads(config.stdout)["services"]["nexent-elasticsearch"]
assert service["image"] == original["Config"]["Image"]
old_env = dict(entry.split("=", 1) for entry in original["Config"]["Env"] if "=" in entry)
for key, value in service["environment"].items():
    if key != "ES_JAVA_OPTS":
        assert str(value) == old_env[key], f"Unexpected setting change: {key}"
assert service["environment"]["ES_JAVA_OPTS"] == "-Xms512m -Xmx512m"
mounts = [v for v in service["volumes"] if v["target"] == "/usr/share/elasticsearch/data"]
assert len(mounts) == 1 and Path(mounts[0]["source"]).resolve() == Path(env["LINGJING_ES_DATA_DIR"]).resolve()
assert service["healthcheck"]["test"] == original["Config"]["Healthcheck"]["Test"]
result = subprocess.run([*command, "up", "-d", "--no-deps", "--pull", "never", "nexent-elasticsearch"],
                        env=env, capture_output=True, encoding="utf-8", timeout=90)
(evidence / "search-activation.log").write_text(result.stdout + result.stderr, encoding="utf-8")
report = {"exit_code": result.returncode, "same_image": True, "credentials_and_settings_preserved": True,
          "compose_project": project,
          "data_mount_verified": True, "jvm_heap": "512m", "original_data_deleted": False,
          "other_services_recreated": False}
(evidence / "search-activation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
raise SystemExit(result.returncode)
