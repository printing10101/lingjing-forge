"""Repeat the established I13 backend suite with the current candidate context tests."""
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

directory = Path(__file__).resolve().parent
root = directory.parents[2]
paths = set()
for case in ET.parse(directory.parent / "I13-validation/final-regression.xml").iter("testcase"):
    parts = case.attrib["classname"].split(".")
    while parts and not parts[-1].startswith("test_"):
        parts.pop()
    paths.add("/".join(parts) + ".py")
os.environ.pop("PYTHONPATH", None)
raise SystemExit(subprocess.run([sys.executable, "-m", "pytest", *sorted(paths), "-q", "--tb=short",
                                f"--junitxml={directory / 'backend-regression.xml'}"], cwd=root).returncode)
