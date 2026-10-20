"""Repeat the I7 focused regression with native-exit evidence preserved."""
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
baseline = json.loads((evidence.parent / "I7-validation/regression-summary.json").read_text(encoding="utf-8"))
env = dict(os.environ, PYTHONIOENCODING="utf-8")
env.pop("PYTHONPATH", None)
probes = []
for index in range(3):
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c",
        "import casadi,nlopt,cadquery,json; print(json.dumps({'casadi':casadi.__version__,"
        "'path':casadi.__file__,'volume':cadquery.Workplane('XY').box(1,2,3).val().Volume()}),flush=True)"],
        capture_output=True, text=True, env=env, timeout=120)
    probes.append({"repeat": index + 1, "exit_code": result.returncode,
                   "stdout": result.stdout, "stderr": result.stderr})
(evidence / "native-fixed.json").write_text(json.dumps(probes, indent=2, ensure_ascii=False), encoding="utf-8")
if any(probe["exit_code"] for probe in probes):
    raise SystemExit("Native readiness failed; regression not claimed as passed")
targets = baseline["targets"] + ["engineering/python/tests/unit/test_desktop_runtime_build.py",
                                 "engineering/python/tests/unit/test_environment_check.py"]
with (evidence / "regression.log").open("w", encoding="utf-8") as log:
    result = subprocess.run([sys.executable, "-m", "pytest", *targets,
                             f"--junitxml={evidence / 'regression.xml'}"],
                            stdout=log, stderr=subprocess.STDOUT, cwd=root, env=env, timeout=600)
counts = {}
if (evidence / "regression.xml").exists():
    suites = ET.parse(evidence / "regression.xml").getroot().iter("testsuite")
    suites = list(suites)
    counts = {key: sum(int(s.get(key, "0")) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
summary = {"python": sys.version, "exit_code": result.returncode, "counts": counts,
           "targets": targets, "full_repository_suite": False}
(evidence / "regression-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False), flush=True)
raise SystemExit(result.returncode)
