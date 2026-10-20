"""Run regressions with coverage started after pytest environment configuration."""
import faulthandler
import os
from pathlib import Path

import coverage
import pytest

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]
os.environ.pop("PYTHONPATH", None)
os.chdir(ROOT)
faulthandler.dump_traceback_later(60, repeat=False)


class MeasureReviewPacket:
    def pytest_sessionstart(self, session):
        self.measurement = coverage.Coverage(config_file=str(DIRECTORY / "coverage.ini"),
            include=["*/source_review_packet.py"])
        self.measurement.start()

    def pytest_sessionfinish(self, session, exitstatus):
        self.measurement.stop()
        self.measurement.save()
        self.measurement.json_report(outfile=str(DIRECTORY / "coverage.json"))
        if self.measurement.report() < 80:
            session.exitstatus = 1
        faulthandler.cancel_dump_traceback_later()


names = ["test_agent_knowledge_candidates", "test_source_review_packet", "test_source_candidates", "test_process_review"]
raise SystemExit(pytest.main([*[f"engineering/python/tests/unit/{name}.py" for name in names],
    "-q", "--tb=short", "-p", "no:cov", f"--junitxml={DIRECTORY / 'final-regression.xml'}"],
    plugins=[MeasureReviewPacket()]))
