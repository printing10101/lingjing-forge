#!/usr/bin/env python3
"""灵境制造 CLI — 服务生命周期管理（doctor/start/stop/update/uninstall）

用法:
    lingjing doctor      全面自检（Python/依赖/配置/端口/健康）
    lingjing start       后台启动服务（uvicorn）
    lingjing stop        优雅停止服务
    lingjing restart     重启服务
    lingjing status      查看运行状态
    lingjing update      更新代码与依赖（git pull + pip install）
    lingjing uninstall   卸载（保留 .env 与数据前会确认）
    lingjing version     显示版本
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# 路径解析（install.sh 注入环境变量，默认 ~/.lingjing）
HOME = Path.home()


def _norm_path(p: str) -> Path:
    """规范化路径：处理 Git Bash 的 POSIX 风格路径（/c/xxx）在 Windows Python 下的解析"""
    if os.name == "nt" and p.startswith("/"):
        m = re.match(r"^/([a-zA-Z])/(.*)$", p)
        if m:
            p = f"{m.group(1).upper()}:/{m.group(2)}"
    return Path(p).expanduser()


LINGJING_HOME = _norm_path(os.environ.get("LINGJING_HOME", str(HOME / ".lingjing-manufacturing")))
LINGJING_SRC = _norm_path(os.environ.get("LINGJING_SRC", str(LINGJING_HOME / "lingjing")))
LINGJING_VENV = _norm_path(os.environ.get("LINGJING_VENV", str(LINGJING_HOME / "venv")))
API_HOST = "127.0.0.1"
API_PORT = int(os.environ.get("LINGJING_PORT", "8765"))
PID_FILE = LINGJING_HOME / "lingjing.pid"
LOG_DIR = LINGJING_HOME / "logs"
BACKEND_LOG = LOG_DIR / "backend.log"

GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"


def python_bin() -> Path:
    if os.name == "nt":
        return LINGJING_VENV / "Scripts" / "python.exe"
    return LINGJING_VENV / "bin" / "python"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{NC} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{NC} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{NC} {msg}")


# 工具函数
def is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # Windows 的 os.kill(pid, 0) 不验证进程存在（Python 平台差异），改用 tasklist
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in (r.stdout or "")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def http_get(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def http_post(url: str, timeout: float = 3.0):
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def load_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def save_pid(pid: int) -> None:
    PID_FILE.write_text(str(pid))


def read_version() -> str:
    vf = LINGJING_SRC / "VERSION"
    if vf.exists():
        return vf.read_text().strip()
    return "unknown"


# doctor — 全面自检
def cmd_doctor() -> int:
    print(f"灵境制造 doctor（版本 {read_version()}）\n")
    errors = 0

    # 1. 目录结构
    print("[-] 目录结构")
    for name, p in (("安装目录", LINGJING_HOME), ("源码目录", LINGJING_SRC), ("虚拟环境", LINGJING_VENV)):
        if p.exists():
            ok(f"{name}: {p}")
        else:
            fail(f"{name} 不存在: {p}")
            errors += 1
    if not (LINGJING_SRC / "engineering/python/requirements.txt").exists():
        fail("源码目录缺少 engineering/python/requirements.txt（可能未完整 clone）")
        errors += 1

    # 2. Python 环境
    print("[-] Python 环境")
    py = python_bin()
    if not py.exists():
        fail(f"虚拟环境 Python 不存在: {py}")
        return errors + 1
    try:
        ver = subprocess.run([str(py), "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        ok(f"Python: {ver}")
        m = re.search(r"(\d+)\.(\d+)", ver)
        if m and (int(m.group(1)), int(m.group(2))) < (3, 11):
            warn("Python 版本低于 3.11，可能存在依赖兼容问题")
    except Exception as e:
        fail(f"Python 探测失败: {e}")
        errors += 1

    # 3. 关键依赖
    print("[-] 关键依赖")
    for mod in ("fastapi", "uvicorn", "sqlalchemy", "pydantic"):
        r = subprocess.run([str(py), "-c", f"import {mod}"], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            ok(f"已安装: {mod}")
        else:
            fail(f"缺少依赖: {mod}（请运行 lingjing update）")
            errors += 1

    # 4. 配置
    print("[-] 配置")
    env_file = LINGJING_SRC / ".env"
    if env_file.exists():
        ok(f".env 存在: {env_file}")
        content = env_file.read_text(encoding="utf-8")
        m = re.search(r"^LNN_JWT_SECRET=(\S*)$", content, re.M)
        jwt = m.group(1) if m else ""
        if jwt and len(jwt) >= 32:
            ok("LNN_JWT_SECRET 已配置且长度 ≥32")
        elif jwt:
            warn("LNN_JWT_SECRET 长度 <32，建议重新生成")
        else:
            warn("LNN_JWT_SECRET 未配置（启动时会自动生成临时密钥，重启后令牌失效）")
    else:
        fail(f".env 不存在: {env_file}（运行 install.sh 或手动 cp .env.example .env）")
        errors += 1
    data_dir = LINGJING_SRC / "engineering/python/data"
    if not data_dir.exists():
        warn(f"数据目录不存在: {data_dir}（首次启动会自动创建）")

    # 5. 端口与健康
    print("[-] 服务状态")
    pid = load_pid()
    if pid and is_running(pid):
        ok(f"进程运行中 (PID {pid})")
    else:
        warn(f"进程未运行（PID 文件: {pid if pid else '无'}）")
    if port_open(API_HOST, API_PORT):
        try:
            status, body = http_get(f"http://{API_HOST}:{API_PORT}/api/health/ping")
            ok(f"健康检查: HTTP {status} {body.strip()}")
        except Exception as e:
            warn(f"端口 {API_PORT} 有进程监听但健康检查失败: {e}")
    else:
        warn(f"端口 {API_PORT} 空闲（服务未启动）")

    print()
    if errors:
        fail(f"检测到 {errors} 个问题，请根据上方提示修复")
        return 1
    ok("全部检查通过")
    return 0


# start / stop / restart / status
def cmd_start() -> int:
    py = python_bin()
    if not py.exists():
        print(f"错误: 虚拟环境 Python 不存在 {py}，请先运行 install.sh")
        return 1
    pid = load_pid()
    if pid and is_running(pid):
        print(f"服务已在运行 (PID {pid})")
        return 0
    if port_open(API_HOST, API_PORT):
        print(f"警告: 端口 {API_PORT} 已被占用，可能已有实例运行")
        return 1

    # 加载 .env（install.sh 生成于源码根目录）到进程环境，否则安全门拒绝启动
    env_file = LINGJING_SRC / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)
        except ImportError:  # 兜底：手动解析（python-dotenv 缺失时）
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    elif not os.environ.get("LNN_JWT_SECRET"):
        print("警告: 未找到 .env 且未设置 LNN_JWT_SECRET，服务可能无法启动（请运行 install.sh）")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    src_dir = LINGJING_SRC / "engineering/python"
    if not src_dir.exists():
        print(f"错误: 后端源码目录不存在 {src_dir}")
        return 1

    print(f"正在启动服务 (http://{API_HOST}:{API_PORT}) ...")
    env = dict(os.environ)
    env.setdefault("SERVER_HOST", API_HOST)
    env.setdefault("SERVER_PORT", str(API_PORT))
    env.setdefault("LNN_HOST", API_HOST)
    env.setdefault("LNN_PORT", str(API_PORT))

    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    with open(BACKEND_LOG, "ab") as log_f:
        proc = subprocess.Popen(
            [
                str(py),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                API_HOST,
                "--port",
                str(API_PORT),
                "--log-level",
                "info",
            ],
            cwd=str(src_dir),
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
    save_pid(proc.pid)
    print(f"PID: {proc.pid} | 日志: {BACKEND_LOG}")

    # 等待健康检查（最长 60s）
    for _ in range(60):
        if proc.poll() is not None:
            print(f"错误: 进程提前退出 (code={proc.returncode})，请查看日志 {BACKEND_LOG}")
            return 1
        try:
            status, body = http_get(f"http://{API_HOST}:{API_PORT}/api/health/ping", timeout=1.5)
            if status == 200:
                print(f"服务已就绪: HTTP {status} {body.strip()}")
                return 0
        except Exception:
            time.sleep(1)
    print("警告: 60 秒内未就绪，服务可能仍在启动，请查看日志")
    return 1


def cmd_stop() -> int:
    pid = load_pid()
    # 优先 HTTP 优雅关闭
    try:
        http_post(f"http://{API_HOST}:{API_PORT}/api/v1/admin/shutdown", timeout=2)
        print("已发送优雅关闭请求")
        for _ in range(10):
            if not port_open(API_HOST, API_PORT):
                break
            time.sleep(0.5)
    except Exception:
        pass
    if pid and is_running(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                if not is_running(pid):
                    break
                time.sleep(0.5)
        except OSError:
            pass
        if is_running(pid):
            print(f"警告: 进程 {pid} 未能终止，请手动处理")
            return 1
        print(f"进程已停止 (PID {pid})")
    else:
        print("服务未在运行")
    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except OSError:
            # 某些受限环境（安全删除守卫）会拦截 unlink；服务已停止，忽略即可
            pass
    return 0


def cmd_restart() -> int:
    cmd_stop()
    time.sleep(1)
    return cmd_start()


def cmd_status() -> int:
    pid = load_pid()
    if pid and is_running(pid):
        print(f"运行中: PID {pid} | http://{API_HOST}:{API_PORT}")
    else:
        print(f"未运行: http://{API_HOST}:{API_PORT}")
    if port_open(API_HOST, API_PORT):
        try:
            status, body = http_get(f"http://{API_HOST}:{API_PORT}/api/health/ping")
            print(f"健康检查: HTTP {status} {body.strip()}")
        except Exception as e:
            print(f"健康检查失败: {e}")
    return 0


# update / uninstall / version
def cmd_update() -> int:
    if not (LINGJING_SRC / ".git").exists():
        print("错误: 源码目录不是 git 仓库（跳过 git pull）")
        return 1
    print("更新代码...")
    r = subprocess.run(["git", "-C", str(LINGJING_SRC), "pull", "--ff-only"], capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        print(f"错误: git pull 失败\n{r.stderr.strip()}")
        return 1
    print("更新依赖...")
    py = python_bin()
    r = subprocess.run(
        ["uv", "pip", "install", "--python", str(py), "-r", str(LINGJING_SRC / "engineering/python/requirements.txt")],
        capture_output=True,
        text=True,
    )
    print(r.stdout.strip())
    if r.returncode != 0:
        print(f"错误: 依赖更新失败\n{r.stderr.strip()}")
        return 1
    print("更新完成（若服务在运行，请执行 lingjing restart）")
    return 0


def cmd_uninstall() -> int:
    print("将卸载灵境制造：")
    print(f"  - 删除安装目录 {LINGJING_HOME}")
    print(f"  - 删除 CLI 软链 {HOME / '.local/bin/lingjing'}")
    print("  （.env 与数据库位于安装目录内，将一并删除，请提前备份）")
    try:
        confirm = input("确认卸载? 输入 yes 继续: ")
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        return 1
    if confirm.strip().lower() != "yes":
        print("已取消")
        return 1
    cmd_stop()
    import shutil

    if LINGJING_HOME.exists():
        shutil.rmtree(LINGJING_HOME)
    link = HOME / ".local/bin/lingjing"
    if link.exists():
        link.unlink()
    print("卸载完成")
    return 0


def cmd_version() -> int:
    print(read_version())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="lingjing", description="灵境制造服务管理 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="全面自检")
    sub.add_parser("start", help="启动服务")
    sub.add_parser("stop", help="停止服务")
    sub.add_parser("restart", help="重启服务")
    sub.add_parser("status", help="查看状态")
    sub.add_parser("update", help="更新代码与依赖")
    sub.add_parser("uninstall", help="卸载")
    sub.add_parser("version", help="显示版本")
    args = parser.parse_args()

    handlers = {
        "doctor": cmd_doctor,
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "status": cmd_status,
        "update": cmd_update,
        "uninstall": cmd_uninstall,
        "version": cmd_version,
    }
    return handlers[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
