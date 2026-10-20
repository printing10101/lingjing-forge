"""Repeat I12 acceptance plus review workflow and runtime regressions."""
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

directory = Path(__file__).resolve().parent
root = directory.parents[2]
paths = set()
for case in ET.parse(directory.parent / "I12-validation/final-regression.xml").iter("testcase"):
    pieces = case.attrib["classname"].split(".")
    while pieces and not pieces[-1].startswith("test_"):
        pieces.pop()
    paths.add("/".join(pieces) + ".py")
paths.update("engineering/python/tests/unit/" + name + ".py" for name in
             ("test_process_review", "test_agent_runtime_tools", "test_cam_recommendation_status"))
os.environ.pop("PYTHONPATH", None)
command = [sys.executable, "-m", "pytest", *sorted(paths), "-q", "--tb=short",
           "--cov=app.rag.process_review", "--cov=app.rag.review_routes", "--cov-fail-under=80",
           "--cov-report=term-missing", f"--cov-report=json:{directory / 'coverage.json'}",
           f"--junitxml={directory / 'final-regression.xml'}"]
raise SystemExit(subprocess.run(command, cwd=root).returncode)
