"""
Sidecar 启动包装器

该模块作为 PyInstaller 打包的入口，负责：
1. 解析命令行参数
2. 启动 uvicorn 监听
3. 写入 state 文件（供 Tauri Rust 端读取）
4. 处理优雅退出信号

被 PyInstaller 编译后，路径为：
    dist/lingjing-backend/lingjing-backend(.exe)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Lingjing Backend Sidecar")
    p.add_argument("--host", default=os.environ.get("LNN_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("LNN_PORT", "8765")))
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--log-level", default="info")
    p.add_argument(
        "--state-file",
        default=os.environ.get(
            "LNN_STATE_FILE",
            str(Path(os.environ.get("LNN_LOG_DIR", ".")) / "sidecar.json"),
        ),
    )
    return p.parse_args()


def write_state(args: argparse.Namespace, status: str, extra: dict | None = None) -> None:
    payload = {
        "status": status,
        "pid": os.getpid(),
        "host": args.host,
        "port": args.port,
        "ts": int(time.time()),
    }
    if extra:
        payload.update(extra)
    try:
        Path(args.state_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.state_file).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[sidecar] 写入 state 文件失败: {exc}", file=sys.stderr)


def _resolve_gstack_dir_to_absolute() -> str:
    """P0-11/12 修复：在 chdir 到 _MEIPASS 之前，把相对 gstack_dir 解析为绝对路径。

    问题：config.paths.gstack_dir 默认值是相对路径 ".lingjing/.gstack"。
    PyInstaller 模式下 main() 会执行 os.chdir(sys._MEIPASS)，导致相对路径
    实际指向 _MEIPASS/.lingjing/.gstack（临时解包目录，重启后丢失，且可能无写权限）。

    本函数：
    - 若 LNN_GSTACK_DIR 已设置为绝对路径，直接返回。
    - 若为相对路径，以用户 home 目录为基准解析为绝对路径。
    - 若未设置，使用默认值 ".lingjing/.gstack" 并以 home 目录解析。
    返回绝对路径字符串。
    """
    raw = os.environ.get("LNN_GSTACK_DIR", ".lingjing/.gstack")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        # 相对路径：以用户 home 目录为基准解析，避免被 chdir(_MEIPASS) 漂移
        home = Path.home()
        p = (home / raw).resolve()
    return str(p)


def main() -> int:
    args = parse_args()

    # P0-12 修复：在 chdir 到 _MEIPASS 之前，把 gstack_dir 解析为绝对路径，
    # 避免 config.paths.gstack_dir 漂移到临时解包目录导致数据丢失。
    # 同时同步 state_file 到 LNN_LOG_DIR/sidecar.json，与 Rust 端读取路径保持一致。
    abs_gstack_dir = _resolve_gstack_dir_to_absolute()
    os.environ["LNN_GSTACK_DIR"] = abs_gstack_dir
    # 同步 state_file 到 LNN_LOG_DIR/sidecar.json（与 Rust 端 sidecar.rs start() 读取路径一致）
    log_dir = os.environ.get("LNN_LOG_DIR")
    if log_dir:
        canonical_state_file = str(Path(log_dir) / "sidecar.json")
        # 若用户未通过 --state-file 显式指定，则覆盖为 canonical 路径
        if args.state_file != canonical_state_file:
            args.state_file = canonical_state_file

    # 切换工作目录到打包时的资源目录
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        # 让 alembic/config 等相对路径文件可被找到
        # 注意：chdir 只影响后续相对路径的资源文件查找，不影响 gstack_dir（已解析为绝对路径）
        os.chdir(bundle_dir)

    write_state(args, "starting")

    # 注册信号处理
    def _handle_signal(signum, _frame):  # noqa: ANN001
        write_state(args, "stopping", {"signal": signum})
        # 抛 KeyboardInterrupt 让 uvicorn 优雅退出
        raise KeyboardInterrupt()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_signal)

    # 设置 JWT 密钥（必须在导入 app.main 之前，因为 app.auth.security 强制要求此环境变量）
    # S3 修复：桌面 sidecar 是单用户场景，每次启动生成新的随机密钥可接受——
    # - 不同桌面用户之间无共享状态，无需跨实例持久化 JWT
    # - 重启后旧 JWT 失效属于预期行为（用户需重新登录）
    # 但必须打印警告，避免误将 sidecar 模式部署到多用户/生产环境。
    # 生产部署应通过 start_server.py 入口，那里会 fail-fast 拒绝缺失密钥。
    import secrets as _secrets

    if not os.environ.get("LNN_JWT_SECRET"):
        os.environ["LNN_JWT_SECRET"] = _secrets.token_urlsafe(32)
        print("[sidecar] WARNING: LNN_JWT_SECRET 未配置，已生成临时密钥（重启后失效）。")
        print("[sidecar] 此为桌面 sidecar 单用户模式回退；多用户/生产部署必须固定配置 LNN_JWT_SECRET。")

    # P1-1 修复：桌面 sidecar 模式禁用 IdleAutoShutdown 中间件。
    # 设计：30 分钟无请求自动关机的策略源于 SaaS 场景（节省云端资源），
    # 但桌面用户随时可能回来使用，自动关机会导致频繁冷启动（PyInstaller 解包 +
    # Python 启动 + FastAPI 初始化约 3-8 秒），严重损害用户体验。
    # 由 sidecar_main 启动时显式设置 LNN_IDLE_AUTO_SHUTDOWN=false。
    if not os.environ.get("LNN_IDLE_AUTO_SHUTDOWN"):
        os.environ["LNN_IDLE_AUTO_SHUTDOWN"] = "false"

    # 注入 nlopt/casadi stub 模块（必须在导入 app.main 之前）
    # 原因：cadquery 2.7.0 将 nlopt/casadi 声明为硬依赖，
    # - cadquery/occ_impl/sketch_solver.py 顶部 `import nlopt`
    # - cadquery/occ_impl/solver.py 顶部 `import casadi as ca`
    # cadquery/__init__.py from .sketch import Sketch from .occ_impl.sketch_solver import ...
    # 形成 import cadquery 即触发 nlopt 导入的硬链。
    # 桌面 sidecar 不使用 sketch solver 求解功能，PyInstaller excludes 排除 nlopt/casadi
    # （DLL 依赖不完整），这里注入 stub 让 cadquery 能正常导入。
    # 实际调用求解功能时会抛 RuntimeError，给出明确提示。
    import types as _types

    def _make_unavailable_stub(mod_name: str):
        mod = _types.ModuleType(mod_name)
        mod.__doc__ = (
            f"Stub for {mod_name} injected by sidecar_main.py. "
            f"{mod_name} is excluded from the desktop sidecar build; "
            f"sketch solver functionality is unavailable."
        )

        def _unavailable(*args, **kwargs):
            raise RuntimeError(
                f"{mod_name} is not available in the desktop sidecar build. "
                f"Install {mod_name} or run with system Python to use this functionality."
            )

        def __getattr__(attr):
            # 白名单化：只拦截业务属性，放行 dunder（__file__/__name__/__path__ 等）。
            # 背景：torch import 时 inspect.getframeinfo 会遍历 sys.modules 读取模块的
            # __file__，若 dunder 也返回函数对象，会触发
            # "'function' object has no attribute 'endswith'" 崩溃（sidecar 启动失败 P1-4）。
            if attr.startswith("__") and attr.endswith("__"):
                raise AttributeError(attr)
            return _unavailable

        mod.__getattr__ = __getattr__
        return mod

    for _stub_name in ("nlopt", "casadi"):
        if _stub_name not in sys.modules:
            sys.modules[_stub_name] = _make_unavailable_stub(_stub_name)

    # 延迟导入以加快启动反馈
    try:
        import uvicorn
        from app.main import app
    except Exception as exc:  # noqa: BLE001
        print(f"[sidecar] 导入 app.main 失败: {exc}", file=sys.stderr)
        write_state(args, "failed", {"error": str(exc)})
        return 1

    write_state(args, "running")

    config = uvicorn.Config(
        app=app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        workers=args.workers,
        access_log=False,
    )
    server = uvicorn.Server(config)

    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        write_state(args, "failed", {"error": str(exc)})
        return 1
    finally:
        write_state(args, "stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
