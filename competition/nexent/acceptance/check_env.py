"""自主编排验收的前置环境检查。

只做只读探测，不改任何配置。用于回答一个问题：现在能不能开始跑
`agent-orchestration-acceptance.md` 里的用例。

检查项：
1. docker 引擎可达（Nexent 全部容器依赖它）
2. Nexent 相关容器是否在运行
3. 本体提案数据文件的当前位置（决定工具能否读到）
4. MCP 入口能否以模块方式导入

退出码 0 = 全部就绪；1 = 有阻塞项。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _diagnose_engine_log() -> str:
    """引擎没起来时，从 Docker 后端日志里找根因（只读最近日志尾部）。

    常见两种：
    - `Access is denied` 执行 wsl.exe：沙箱或安全软件拦了 WSL，Docker 起不来；
    - WSL 发行版缺失：`docker-desktop` 未注册。
    直接读日志比看 docker 的泛化报错快得多。
    """
    base = Path.home() / "AppData" / "Local" / "Docker" / "log" / "host"
    log = base / "com.docker.backend.exe.log"
    if not log.exists():
        return ""
    try:
        tail = log.read_bytes()[-40000:].decode("utf-8", errors="ignore")
    except OSError:
        return ""
    if "wsl.exe" in tail and "Access is denied" in tail:
        return (
            "根因：后台进程执行 wsl.exe 被拒绝（Access is denied）。"
            "若 Docker 是从代理沙箱内启动的，子进程会继承限制；"
            "请在桌面手动启动 Docker Desktop 后重试。"
        )
    if "engine linux/wsl failed to start" in tail:
        return "根因：linux/wsl 引擎启动失败，见 Docker 后端日志。"
    return ""


def check_docker() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "docker 命令不在 PATH"
    code, out = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    if code != 0:
        first = out.strip().splitlines()[0] if out.strip() else "未知错误"
        hint = _diagnose_engine_log()
        return False, f"引擎不可达：{first}" + (f"；{hint}" if hint else "")
    return True, f"引擎版本 {out.strip()}"


def check_containers() -> tuple[bool, str]:
    code, out = _run(["docker", "ps", "--format", "{{.Names}}"])
    if code != 0:
        return False, "无法列出容器"
    names = [n for n in out.splitlines() if n.strip()]
    nexent = [n for n in names if "nexent" in n.lower()]
    if not nexent:
        return False, f"运行中容器 {len(names)} 个，未发现 nexent 相关容器"
    return True, f"运行中容器 {len(names)} 个，其中 nexent 相关 {len(nexent)} 个"


def check_mcp_entry() -> tuple[bool, str]:
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import mcp_server.ontology_tools as mod  # noqa: F401

        return True, "mcp_server.ontology_tools 可导入"
    except Exception as exc:  # noqa: BLE001 - 诊断脚本要把原因原样带出来
        return False, f"导入失败：{exc}"


def check_ontology_store() -> tuple[bool, str]:
    path = REPO_ROOT / "data" / "ontology" / "ontology.sqlite"
    if path.exists():
        return True, f"已存在：{path.relative_to(REPO_ROOT)}"
    return True, "尚未创建（首次调用提案工具时自动创建，不阻塞）"


def main() -> int:
    checks = [
        ("docker 引擎", check_docker()),
        ("Nexent 容器", check_containers()),
        ("MCP 入口", check_mcp_entry()),
        ("本体存储", check_ontology_store()),
    ]
    blockers = 0
    for name, (ok, detail) in checks:
        mark = "OK  " if ok else "BLOCK"
        print(f"[{mark}] {name}: {detail}")
        if not ok:
            blockers += 1
    print()
    if blockers:
        print(f"{blockers} 项阻塞，暂不能执行编排验收。参见 acceptance/agent-orchestration-acceptance.md")
    else:
        print("全部就绪，可以执行编排验收用例 A/B/C。")
    return 0 if blockers == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
