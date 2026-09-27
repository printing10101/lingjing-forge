#!/usr/bin/env python
"""
PyInstaller 打包脚本：将 Python 后端打包为单目录可执行文件，
并复制到 src-tauri/binaries/ 目录下（Tauri Sidecar 要求位置）。

使用方法：
    python scripts/build_backend.py
    python scripts/build_backend.py --onefile    # 单文件模式（启动更慢）
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # 脚本在 python/scripts/ 下，parents[2] 才是项目根
PYTHON_DIR = PROJECT_ROOT / "python"
TAURI_BINARIES = PROJECT_ROOT / "src-tauri" / "binaries"


def get_target_triple() -> str:
    """根据当前平台推导 Tauri sidecar 文件名后缀"""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        if machine in ("amd64", "x86_64"):
            return "x86_64-pc-windows-msvc"
        if machine in ("arm64", "aarch64"):
            return "aarch64-pc-windows-msvc"
    if system == "darwin":
        if machine == "arm64":
            return "aarch64-apple-darwin"
        return "x86_64-apple-darwin"
    if system == "linux":
        if machine in ("amd64", "x86_64"):
            return "x86_64-unknown-linux-gnu"
        if machine in ("arm64", "aarch64"):
            return "aarch64-unknown-linux-gnu"
    raise RuntimeError(f"不支持的平台: {system}/{machine}")


def run_pyinstaller(onefile: bool = False) -> Path:
    spec_file = PYTHON_DIR / "lingjing-backend.spec"
    if not spec_file.exists():
        print(f"[ERROR] 找不到 spec 文件: {spec_file}")
        sys.exit(1)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(spec_file),
    ]
    print(f"[INFO] 运行 PyInstaller: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PYTHON_DIR))
    if result.returncode != 0:
        print("[ERROR] PyInstaller 构建失败")
        sys.exit(result.returncode)

    # --onefile 模式产物为单文件：lingjing-backend.exe (Windows) 或 lingjing-backend (Linux/macOS)
    suffix = ".exe" if platform.system().lower() == "windows" else ""
    dist_file = PYTHON_DIR / "dist" / f"lingjing-backend{suffix}"
    if not dist_file.exists():
        print(f"[ERROR] 预期产物不存在: {dist_file}")
        sys.exit(1)
    return dist_file


def copy_to_tauri(src_file: Path) -> Path:
    TAURI_BINARIES.mkdir(parents=True, exist_ok=True)
    triple = get_target_triple()
    suffix = ".exe" if platform.system().lower() == "windows" else ""
    target_name = f"lingjing-backend-{triple}{suffix}"
    target_path = TAURI_BINARIES / target_name

    if target_path.exists():
        if target_path.is_dir():
            shutil.rmtree(target_path)
        else:
            target_path.unlink()

    print(f"[INFO] 复制构建产物 -> {target_path}")

    # --onefile 模式产物为单个可执行文件，直接复制并重命名为 Tauri sidecar 约定名称
    shutil.copy2(src_file, target_path)
    os.chmod(target_path, 0o755)
    return target_path


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 Python 后端 Sidecar")
    parser.add_argument("--onefile", action="store_true", help="单文件模式（启动更慢但分发更简单）")
    args = parser.parse_args()

    if not shutil.which("pyinstaller") and not _has_module("PyInstaller"):
        print("[ERROR] 未检测到 PyInstaller，请先安装： pip install pyinstaller")
        return 1

    dist_dir = run_pyinstaller(onefile=args.onefile)
    target = copy_to_tauri(dist_dir)
    print(f"[OK] Sidecar 可执行文件已就绪: {target}")
    return 0


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
