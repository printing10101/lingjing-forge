"""启动后端服务的辅助脚本，设置必要的环境变量。

S3 修复：移除"密钥缺失时自动生成临时密钥"的回退逻辑——
此回退会绕过 ``app.auth.security._validate_and_get_secret()`` 的 fail-fast
保护，使生产环境在缺失 LNN_JWT_SECRET 时仍能启动，但每次重启密钥变化
导致所有已签发 JWT 失效，且无任何告警。

新策略：
- 生产环境（LNN_ENV=production 或未声明为 dev/test）：缺失密钥直接 fail-fast，
  退出码 1，提示运维通过 `python -c "import secrets; print(secrets.token_urlsafe(32))"`
  生成并配置 LNN_JWT_SECRET。
- 开发/测试环境（LNN_ENV=dev 或 test）：允许显式回退临时密钥，但必须打印
  警告。这是为了保留 "clone 后直接 python start_server.py 跑起来" 的开发体验。
"""

import os
import sys


def _is_dev_env() -> bool:
    """判断是否为开发/测试环境。"""
    env = os.environ.get("LNN_ENV", "").lower()
    return env in ("dev", "development", "test", "testing", "local")


def main():
    # 桌面嵌入式场景：.env 自动生成与加载（2026-08-03 桌面实装验证修复）
    # 桌面安装包内无 .env（sidecar 直接启动后端）。首次启动自动生成：
    # 随机 JWT + SQLite 数据库 + 空 REDIS_URL（内存缓存），实现「开箱即用」。
    # 服务端部署（install.sh 已生成 .env）时跳过生成，仅加载。
    # 生成位置：python_dir 的父目录（工程版=仓库根；桌面版=desktop_runtime/）。
    import secrets
    from pathlib import Path

    _py_dir = os.path.dirname(os.path.abspath(__file__))
    # Windows 长路径修复（2026-08-09）：Tauri resource_dir 会返回 \\?\ 前缀路径
    # （\\?\C:\...），直接拼进 SQLite URL 会得到 sqlite+aiosqlite://///?/C:/...
    # 导致 SQLAlchemy 报 unable to open database file（health degraded、
    # 登录/数据功能不可用）。此处剥离 \\?\ 前缀，恢复普通绝对路径。
    if _py_dir.startswith("\\\\?\\"):
        _py_dir = _py_dir[4:]
    # 确保数据库目录存在（桌面首次启动无 data/，SQLite 打开前必须建目录）
    Path(_py_dir, "data").mkdir(parents=True, exist_ok=True)
    _env_file = Path(_py_dir).parent / ".env"
    if not _env_file.exists():
        try:
            _jwt = secrets.token_urlsafe(48)
            _db_url = "sqlite+aiosqlite:///" + _py_dir.replace("\\", "/") + "/data/app.db"
            _env_file.write_text(
                "LNN_JWT_SECRET=" + _jwt + "\nDATABASE_URL=" + _db_url + "\nREDIS_URL=\n",
                encoding="utf-8",
            )
            print(f"[startup] 已生成 {_env_file}（随机 JWT + SQLite 单机模式）")
        except OSError as _e:
            print(f"[startup] WARNING: .env 生成失败（{_e}），继续按环境变量启动")
    try:
        from dotenv import load_dotenv

        load_dotenv(_env_file, override=False)
    except ImportError:
        pass

    # S3 修复：JWT 密钥缺失时的处理策略
    if not os.environ.get("LNN_JWT_SECRET"):
        if _is_dev_env():
            # 开发/测试：显式回退临时密钥（仅本机，跨进程 JWT 不持久）
            import secrets

            os.environ["LNN_JWT_SECRET"] = secrets.token_urlsafe(32)
            print("[startup] WARNING: LNN_JWT_SECRET 未配置，已生成临时密钥（重启后失效）。")
            print("[startup] 此为开发模式回退，生产环境必须固定配置 LNN_JWT_SECRET。")
        else:
            # 生产/未声明环境：fail-fast
            print(
                "[startup] FATAL: LNN_JWT_SECRET 未配置。生产环境拒绝启动。",
                file=sys.stderr,
            )
            print(
                '[startup] 请执行: python -c "import secrets; print(secrets.token_urlsafe(32))" '
                "并将输出设置为环境变量 LNN_JWT_SECRET。",
                file=sys.stderr,
            )
            print(
                "[startup] 如确需开发模式回退，请显式设置 LNN_ENV=dev。",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        print("[startup] LNN_JWT_SECRET 已从环境变量加载")

    # 切换到 python 目录（复用已剥离 \\?\ 前缀的 _py_dir）
    python_dir = _py_dir
    os.chdir(python_dir)
    print(f"Working directory: {python_dir}")

    # Windows asyncio 修复：某些 Python 安装（Anaconda 3.13 / Python 3.11）
    # 的 `_overlapped` 模块损坏，导致默认的 ProactorEventLoop 初始化时报
    # `AttributeError: module '_overlapped' has no attribute 'CreateIoCompletionPort'`。
    # uvicorn 在 reload=False 时不依赖子进程，SelectorEventLoop 完全够用。
    # 此处显式切换到 SelectorEventLoop，绕过损坏的 _overlapped 模块。
    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        print("[startup] 已切换到 WindowsSelectorEventLoop（绕过 _overlapped 损坏）")

    # 启动 uvicorn
    # P0-6 修复：host 从环境变量读取，默认 127.0.0.1（仅本机访问），
    # 避免误用此脚本时在所有网络接口暴露 API。生产对外部署应通过
    # 反向代理（nginx）或显式设置 SERVER_HOST=0.0.0.0。
    import uvicorn

    host = os.environ.get("SERVER_HOST", "127.0.0.1")
    port = int(os.environ.get("SERVER_PORT", "8765"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
