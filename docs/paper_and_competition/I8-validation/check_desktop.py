"""Record the actual bundled interpreter's native readiness, without rebuilding."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
script = root / "engineering/python/scripts/build_desktop_runtime.py"
spec = importlib.util.spec_from_file_location("desktop_runtime_build", script)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
python = root / "engineering/python/desktop_runtime/runtime/python.exe"
report = {}
try:
    report["validation"] = build.validate_runtime(python)
    report["native_exit_code"] = 0
except Exception as exc:
    report["validation_error"] = str(exc)
    if isinstance(exc, subprocess.TimeoutExpired):
        partial = exc.stderr or b""
        report["partial_stderr"] = partial.decode("utf-8", errors="replace") if isinstance(partial, bytes) else partial
check = subprocess.run([str(python), "-m", "pip", "check"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
report["pip_check"] = {"exit_code": check.returncode, "stdout": check.stdout, "stderr": check.stderr}
(evidence / "desktop-readiness.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False), flush=True)
sys.exit(0 if report.get("native_exit_code") == 0 and check.returncode == 0 else 1)
