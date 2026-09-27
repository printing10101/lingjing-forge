"""
测试运行器和报告生成器

运行生成的测试并输出报告
"""

import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TestResult:
    """单个测试结果"""
    name: str
    status: str  # passed, failed, skipped, error
    duration: float = 0.0
    message: str = ""


@dataclass
class TestReport:
    """测试报告"""
    timestamp: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration: float = 0.0
    results: list[TestResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total * 100

    def to_dict(self) -> dict:
        return {
            'timestamp': self.timestamp,
            'total': self.total,
            'passed': self.passed,
            'failed': self.failed,
            'skipped': self.skipped,
            'errors': self.errors,
            'duration': self.duration,
            'pass_rate': self.pass_rate,
        }


class TestRunner:
    """测试运行器"""

    def __init__(self, test_dir: str | Path = "tests/generated"):
        self.test_dir = Path(test_dir)
        self.report_dir = Path("tests/reports")

    def run(self, pattern: str = "test_*.py", verbose: bool = False) -> TestReport:
        """运行测试并生成报告"""
        if not self.test_dir.exists():
            raise FileNotFoundError(f"Test directory not found: {self.test_dir}")

        self.report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_report = self.report_dir / f"report_{timestamp}.json"

        cmd = [
            "python", "-m", "pytest",
            str(self.test_dir),
            "--tb=short",
            f"--json-report-output={json_report}",
            "-q",
        ]
        if verbose:
            cmd.append("-v")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
                cwd=str(Path.cwd())
            )
            report = self._parse_output(result.stdout, result.returncode)
        except FileNotFoundError:
            # pytest 未安装或不可用，使用简单解析
            report = TestReport(timestamp=datetime.now().isoformat())
            report.total = 0
            report.skipped = 0
        except subprocess.TimeoutExpired:
            report = TestReport(timestamp=datetime.now().isoformat())
            report.errors = 1

        # 保存报告
        report_path = self.report_dir / f"report_{timestamp}.txt"
        self._save_text_report(report, report_path)

        return report

    def _parse_output(self, output: str, returncode: int) -> TestReport:
        """解析 pytest 输出"""
        report = TestReport(timestamp=datetime.now().isoformat())

        for line in output.split('\n'):
            line = line.strip()
            if ' passed' in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == 'passed' and i > 0:
                        try:
                            report.passed = int(parts[i - 1])
                        except ValueError:
                            pass
            if ' failed' in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == 'failed' and i > 0:
                        try:
                            report.failed = int(parts[i - 1])
                        except ValueError:
                            pass
            if ' skipped' in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == 'skipped' and i > 0:
                        try:
                            report.skipped = int(parts[i - 1])
                        except ValueError:
                            pass

        report.total = report.passed + report.failed + report.skipped + report.errors
        return report

    def _save_text_report(self, report: TestReport, path: Path):
        """保存文本报告"""
        lines = [
            "=" * 60,
            "测试报告",
            "=" * 60,
            f"时间: {report.timestamp}",
            f"总计: {report.total}",
            f"通过: {report.passed}",
            f"失败: {report.failed}",
            f"跳过: {report.skipped}",
            f"错误: {report.errors}",
            f"通过率: {report.pass_rate:.1f}%",
            "=" * 60,
        ]
        path.write_text('\n'.join(lines), encoding='utf-8')
