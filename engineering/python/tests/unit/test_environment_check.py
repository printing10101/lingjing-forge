"""
环境检查测试

确保测试环境配置正确，避免 PYTHONPATH 遮蔽导致 flaky 测试。
"""

import sys
import subprocess
from pathlib import Path
import os

import pytest

# 本文件是本地开发环境守卫：断言 Python 3.14、desktop_runtime/ 存在、仓库根
# 相对路径布局等仅在本机成立的前提。CI（ubuntu + 3.11/3.12）必然不满足，
# 由 CI 的 -m "not skip_ci" 排除（与 tests/performance、tests/image_to_3d 同模式）。
pytestmark = pytest.mark.skip_ci


def test_python_version():
    """测试必须使用 Python 3.14"""
    # 环境守卫验证的是"当前解释器"：路径硬编码会随机器/用户名漂移（曾把
    # AGENTS.md 的 <user> 占位符原样写进路径），改用 sys.executable。
    python_exe = Path(sys.executable)

    assert python_exe.exists(), f"Python 3.14 not found at {python_exe}"
    
    # 验证版本
    result = subprocess.run(
        [str(python_exe), "--version"],
        capture_output=True,
        text=True,
    )
    
    version_output = result.stdout.strip()
    assert "Python 3.14" in version_output, f"Expected Python 3.14, got: {version_output}"
    print(f"✓ Python version: {version_output}")


def test_no_pythonpath_shading():
    """测试无 PYTHONPATH 遮蔽"""
    # 测试在 unset PYTHONPATH 环境下运行
    if os.name == "nt":
        # Windows
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
    else:
        # Unix
        env = {**os.environ, "PYTHONPATH": ""}
    
    # 运行一个简单的模块导入测试
    result = subprocess.run(
        [
            str(Path(sys.executable)),
            "-c",
            "import sys; sys.path.insert(0, 'engineering/python'); from app.core.exceptions import ValidationException; print('OK')",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path.cwd(),
    )
    
    assert result.returncode == 0, f"Module import failed: {result.stderr}"
    assert "OK" in result.stdout, f"Unexpected output: {result.stdout}"
    print(f"✓ Module import works: {result.stdout.strip()}")


def test_ocp_loaded():
    """测试 OCP 依赖正常加载。

    同时验证导入和正常退出。Windows 上 CasADi 3.8.0 与 NLopt 共用
    SWIG 类型表会导致退出期堆损坏；修复依赖为 CasADi 3.8.1。
    """
    result = subprocess.run(
        [sys.executable, "-c", "import cadquery; print('OCP loaded successfully')"],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )

    assert "OCP loaded successfully" in result.stdout, (
        f"OCP failed to load: {result.stderr}"
    )
    assert result.returncode == 0, (
        f"CAD native runtime crashed at exit ({result.returncode}). "
        "Install the pinned CasADi 3.8.1 fix and rerun; stdout success is not readiness. "
        f"{result.stderr[-1500:]}"
    )
    print(f"✓ OCP loaded: {result.stdout.strip()}")


def test_desktop_runtime_python():
    """测试桌面运行时 Python 可用"""
    # 2026-09 修复：仓库根锚定（原 cwd 相对路径从 engineering/python 运行
    # pytest 时必然找不到运行时）
    module_root = Path(__file__).resolve().parents[2]  # .../engineering/python
    desktop_runtime_python = module_root / "desktop_runtime" / "runtime" / "python.exe"
    
    assert desktop_runtime_python.exists(), f"Desktop runtime not found at {desktop_runtime_python}"
    
    # 验证版本
    result = subprocess.run(
        [str(desktop_runtime_python), "--version"],
        capture_output=True,
        text=True,
    )
    
    version_output = result.stdout.strip()
    print(f"✓ Desktop runtime Python: {version_output}")
    
    # 验证包含正确的 Python 版本
    assert "Python 3.12" in version_output, f"Expected Python 3.12.x in desktop runtime, got: {version_output}"


def test_key_modules_import():
    """测试关键模块导入"""
    modules_to_test = [
        "app.core.exceptions",
        "app.core.circuit_breaker",
        "app.core.middleware",
        "app.ai.llm.provider_base",
    ]
    
    for module in modules_to_test:
        result = subprocess.run(
            [
                str(Path(sys.executable)),
                "-c",
                f"import sys; sys.path.insert(0, 'engineering/python'); import {module}; print('OK')",
            ],
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
        
        assert result.returncode == 0, f"{module} import failed: {result.stderr}"
        print(f"✓ Module import: {module}")


def test_exceptions_import():
    """测试所有异常类可正常导入"""
    exceptions_to_test = [
        "ValidationException",
        "NotFoundException",
        "LLMException",
        "LLMTimeoutException",
        "CircuitBreakerOpenException",
        "CadException",
        "NCCodeException",
    ]
    
    for exc_name in exceptions_to_test:
        result = subprocess.run(
            [
                str(Path(sys.executable)),
                "-c",
                f"import sys; sys.path.insert(0, 'engineering/python'); from app.core.exceptions import {exc_name}; print('OK')",
            ],
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
        
        assert result.returncode == 0, f"{exc_name} import failed: {result.stderr}"
        print(f"✓ Import: {exc_name}")


if __name__ == "__main__":
    print("=" * 80)
    print("Running environment checks")
    print("=" * 80)
    
    test_python_version()
    test_no_pythonpath_shading()
    test_ocp_loaded()
    test_desktop_runtime_python()
    test_key_modules_import()
    test_exceptions_import()
    
    print("=" * 80)
    print("All environment checks passed!")
    print("=" * 80)
