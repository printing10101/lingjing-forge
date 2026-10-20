"""Broader regressions, with applicability coverage enabled after pytest setup."""
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import coverage
import pytest

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]
os.environ.pop("PYTHONPATH", None)
os.chdir(ROOT)
paths = set()
for case in ET.parse(DIRECTORY.parent / "I15-validation/regression.xml").iter("testcase"):
    parts = case.attrib["classname"].split(".")
    while parts and not parts[-1].startswith("test_"):
        parts.pop()
    paths.add("/".join(parts) + ".py")
for name in ("test_process_applicability", "test_source_review_packet", "test_cam_recommendation_status",
             "test_mcp_cam_tools", "test_agent_runtime_tools"):
    paths.add(f"engineering/python/tests/unit/{name}.py")


class MeasureApplicability:
    def pytest_sessionstart(self, session):
        self.measurement = coverage.Coverage(config_file=False, branch=True, data_file=str(DIRECTORY / ".coverage"),
            include=["*/process_applicability.py"])
        self.measurement.start()

    def pytest_sessionfinish(self, session, exitstatus):
        self.measurement.stop()
        self.measurement.save()
        self.measurement.json_report(outfile=str(DIRECTORY / "coverage.json"))
        if self.measurement.report() < 80:
            session.exitstatus = 1


raise SystemExit(pytest.main([*sorted(paths), "-q", "--tb=short", "-p", "no:cov",
    f"--junitxml={DIRECTORY / 'backend-regression.xml'}"], plugins=[MeasureApplicability()]))
