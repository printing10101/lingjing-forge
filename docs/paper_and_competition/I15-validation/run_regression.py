"""Repeat I14 regressions plus authenticated candidates and existing auth safeguards."""
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

directory = Path(__file__).resolve().parent
root = directory.parents[2]
paths = set()
for case in ET.parse(directory.parent / "I14-validation/backend-regression.xml").iter("testcase"):
    parts = case.attrib["classname"].split(".")
    while parts and not parts[-1].startswith("test_"):
        parts.pop()
    paths.add("/".join(parts) + ".py")
for name in ("test_agent_knowledge_candidates", "test_unified_auth_access_log", "test_unified_auth_middleware", "test_unified_auth_helpers"):
    paths.add(f"engineering/python/tests/unit/{name}.py")
os.environ.pop("PYTHONPATH", None)
raise SystemExit(subprocess.run([sys.executable, "-m", "pytest", *sorted(paths), "-q", "--tb=short",
                                f"--junitxml={directory / 'regression.xml'}"], cwd=root).returncode)
