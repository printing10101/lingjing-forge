"""重启后自动恢复脚本：验证 WinSock + 下载 torch + 安装 + 验证.

用法（重启系统后）：
    cd C:\\Users\\Lenovo\\Desktop\\灵境制造（上线版）\\python
    python scripts\\post_reboot_recovery.py

退出码：
    0 = 全部成功，L4 阻塞已解除
    1 = WinSock 仍未修复（socket() 仍失败）
    2 = WinSock 已修复但 torch 安装失败
    3 = torch 已安装但验证测试失败
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path


def step1_verify_winsock() -> bool:
    """验证 WinSock 是否已修复."""
    print("=" * 60)
    print("步骤 1: 验证 WinSock 是否已修复")
    print("=" * 60)

    # 测试 socket 创建
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.close()
        print("[OK] socket.socket() 创建成功")
    except OSError as e:
        print(f"[FAIL] socket.socket() 失败: {e}")
        print("WinSock 仍未修复，请确认已执行 netsh winsock reset 并重启系统")
        return False

    # 测试网络连通性
    try:
        req = urllib.request.Request("https://pypi.org/simple/", method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[OK] 网络连通: pypi.org 返回 {resp.status}")
    except Exception as e:
        print(f"[FAIL] 网络不通: {e}")
        print("WinSock 可能部分修复，但网络仍不可用")
        return False

    print("[OK] WinSock 已修复，网络可用")
    return True


def step2_install_torch() -> bool:
    """下载并安装 torch CPU 版本."""
    print()
    print("=" * 60)
    print("步骤 2: 安装 torch CPU 版本")
    print("=" * 60)

    python_exe = sys.executable
    print(f"Python: {python_exe}")
    print(f"版本: {sys.version}")

    # 检查是否已安装 torch
    try:
        import torch  # noqa: F401

        print("[OK] torch 已安装，跳过安装步骤")
        return True
    except ImportError:
        pass

    # 安装 torch CPU 版本
    print("开始安装 torch CPU 版本...")
    cmd = [
        python_exe,
        "-m",
        "pip",
        "install",
        "torch",
        "--index-url",
        "https://download.pytorch.org/whl/cpu",
        "--timeout",
        "300",
    ]
    print(f"执行: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=1800)
        print("STDOUT:")
        print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
        if result.stderr:
            print("STDERR:")
            print(result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr)
        print(f"退出码: {result.returncode}")

        if result.returncode != 0:
            print("[FAIL] torch 安装失败")
            return False
    except subprocess.TimeoutExpired:
        print("[FAIL] torch 安装超时（30 分钟）")
        return False

    # 验证安装
    try:
        import torch  # noqa: F401

        print(f"[OK] torch 安装成功，版本: {torch.__version__}")
        return True
    except ImportError as e:
        print(f"[FAIL] torch 安装后仍无法导入: {e}")
        return False


def step3_verify_torch_ready() -> bool:
    """运行 verify_torch_ready.py 完整验证."""
    print()
    print("=" * 60)
    print("步骤 3: 运行 verify_torch_ready.py 完整验证")
    print("=" * 60)

    python_exe = sys.executable
    script_dir = Path(__file__).parent.parent  # python/scripts -> python
    verify_script = script_dir / "scripts" / "verify_torch_ready.py"

    if not verify_script.exists():
        print(f"[FAIL] 验证脚本不存在: {verify_script}")
        return False

    cmd = [python_exe, str(verify_script)]
    print(f"执行: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, check=False, cwd=str(script_dir))
        print(f"退出码: {result.returncode}")

        if result.returncode == 0:
            print("[OK] 验证通过！L4 阻塞已完全解除")
            return True
        elif result.returncode == 1:
            print("[FAIL] torch 不可用（退出码 1）")
            return False
        elif result.returncode == 2:
            print("[FAIL] torch 可用但有测试失败（退出码 2）")
            return False
        else:
            print(f"[FAIL] 未知退出码: {result.returncode}")
            return False
    except Exception as e:
        print(f"[FAIL] 验证脚本执行异常: {e}")
        return False


def cleanup_startup_shortcut() -> None:
    """成功完成后删除启动项快捷方式，避免每次重启都运行."""
    startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    shortcut = startup_dir / "post_reboot_recovery.lnk"
    if shortcut.exists():
        try:
            shortcut.unlink()
            print(f"[OK] 已清理启动项快捷方式: {shortcut}")
        except OSError as e:
            print(f"[WARN] 清理启动项快捷方式失败: {e}")


def main() -> int:
    print("=" * 60)
    print("  重启后自动恢复脚本")
    print("  WinSock 修复 → torch 安装 → 完整验证")
    print("=" * 60)
    print()

    # 验证 WinSock
    if not step1_verify_winsock():
        print()
        print("=" * 60)
        print("[结果] 步骤 1 失败：WinSock 仍未修复")
        print("请确认已执行 netsh winsock reset 并重启系统")
        print("=" * 60)
        return 1

    # 安装 torch
    if not step2_install_torch():
        print()
        print("=" * 60)
        print("[结果] 步骤 2 失败：torch 安装失败")
        print("WinSock 已修复但 torch 安装失败，请检查网络或手动安装")
        print("=" * 60)
        return 2

    # 完整验证
    if not step3_verify_torch_ready():
        print()
        print("=" * 60)
        print("[结果] 步骤 3 失败：验证测试未通过")
        print("torch 已安装但验证测试失败，请查看上方输出定位问题")
        print("=" * 60)
        return 3

    # 清理启动项快捷方式（仅全部成功时才清理）
    cleanup_startup_shortcut()

    print()
    print("=" * 60)
    print("[结果] 全部成功！L4 环境阻塞已完全解除")
    print()
    print("后续可推进的工作：")
    print("  1. PHM2010 全链路验证")
    print("  2. MLflow tracking 融合层参数记录")
    print("  3. ADR-020 思路 2/3 的 torch 依赖测试用例")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
