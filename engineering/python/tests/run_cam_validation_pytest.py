"""CAM 校验 pytest 用例本地运行器（阶段 7）。

背景：
- 本地 Python 3.14 的 _overlapped C 扩展损坏（WinError 10038），
  anyio / langsmith 插件在 entrypoints 加载阶段就触发 asyncio → _overlapped
  导入失败，导致 pytest 命令行直接退出（即使 -p no:anyio 也来不及禁用）
- 项目根 conftest.py 在导入期强制加载 app.api.v1.auth → bcrypt，
  bcrypt 未在本地安装会触发 ModuleNotFoundError
- 本脚本绕过上述两个障碍：
  1. 在 sys.modules 中预注入 _overlapped / matplotlib / slowapi 假模块
  2. 通过 --noconftest 绕过 conftest.py 强制加载
  3. 通过 --override-ini="addopts=" 清空 pytest.ini 的 --cov 选项
     （本地无需覆盖率统计；CI 环境由 pytest.ini 自动启用 --cov）
- test_cam_validation.py 不依赖任何 conftest fixture，可独立运行

使用方法：
    cd python
    python tests/run_cam_validation_pytest.py

退出码：
    0 = 全部通过
    非 0 = 存在失败用例或收集错误
"""

from __future__ import annotations

import os
import secrets
import sys
import types
from pathlib import Path

# Windows WinSock 初始化（必须在 asyncio / socket 派生模块之前）
# 项目记忆硬约束：WinSock initialization issues may occur; implement workaround
# by forcing WinSock initialization before imports
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes as _wt

    class _WSAData(ctypes.Structure):
        _fields_ = [
            ("wVersion", _wt.WORD),
            ("wHighVersion", _wt.WORD),
            ("szDescription", ctypes.c_char * 257),
            ("szSystemStatus", ctypes.c_char * 129),
            ("iMaxSockets", _wt.USHORT),
            ("iMaxUdpDg", _wt.USHORT),
            ("lpVendorInfo", ctypes.c_char_p),
        ]

    try:
        _ws_data = _WSAData()
        _ws2_32 = ctypes.windll.ws2_32
        _wsa_rc = _ws2_32.WSAStartup(0x0202, ctypes.byref(_ws_data))
        if _wsa_rc == 0:
            import socket as _socket_mod

            _ws_init_sock = _socket_mod.socket(_socket_mod.AF_INET, _socket_mod.SOCK_STREAM)
            _ws_init_sock.close()
    except (OSError, AttributeError):
        pass

# _overlapped 假模块注入（项目记忆硬约束：Python 3.14 _overlapped 损坏 workaround）
# asyncio 在 Windows 上依赖 _overlapped C 扩展，Python 3.14 该扩展损坏
# 假模块包含 IocpProactor 构造所需的最小属性集
if sys.platform == "win32" and "_overlapped" not in sys.modules:
    _ov = types.ModuleType("_overlapped")
    _ov.Overlapped = type(
        "Overlapped",
        (),
        {
            "__init__": lambda self, *a, **kw: None,
        },
    )
    _ov.NULL = 0
    _ov.INVALID_HANDLE_VALUE = -1
    _ov.OVERLAPPED_VERSION = 1
    _ov.CreateEvent = lambda *a, **kw: 0
    _ov.SetEvent = lambda *a, **kw: True
    _ov.ResetEvent = lambda *a, **kw: True
    _ov.CloseHandle = lambda *a, **kw: True
    _ov.GetQueuedCompletionStatus = lambda *a, **kw: (0, 0, 0)
    _ov.PostQueuedCompletionStatus = lambda *a, **kw: True
    _ov.RegisterWaitWithQueue = lambda *a, **kw: 0
    _ov.UnregisterWaitEx = lambda *a, **kw: True
    _ov.BindIoCompletionCallback = lambda *a, **kw: None
    _ov.CreateIoCompletionPort = lambda *a, **kw: 0
    _ov.GetOverlappedResult = lambda *a, **kw: (0, 0)
    _ov.WSARecv = lambda *a, **kw: 0
    _ov.WSASend = lambda *a, **kw: 0
    _ov.AcceptEx = lambda *a, **kw: 0
    _ov.ConnectEx = lambda *a, **kw: 0
    sys.modules["_overlapped"] = _ov

# 环境变量设置（模拟 conftest.py 的 _env_setup fixture）

os.environ["ENVIRONMENT"] = "testing"
os.environ["LNN_AUTH_ENABLED"] = "false"
os.environ["LNN_PERMISSION_ENFORCED"] = "false"
os.environ["LNN_JWT_SECRET"] = secrets.token_hex(32)
os.environ["LNN_GSTACK_DIR"] = ".lingjing/.gstack_test_cam_pytest"
# CAM 模块默认值（与 CamValidationConfig 默认对齐）
os.environ.setdefault("LNN_CAM_ENABLED", "true")
os.environ.setdefault("LNN_CAM_DEFAULT_BACKEND", "internal_only")
os.environ.setdefault("LNN_CAM_VALIDATION_REQUIRED", "true")
os.environ.setdefault("LNN_CAM_ALLOW_DELETE_SUCCEEDED", "false")
os.environ.setdefault("LNN_CAM_TASK_TIMEOUT", "600")

# 将 python 目录加入 sys.path
_PYTHON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PYTHON_DIR))

# Mock 模块注入（项目记忆硬约束：本地环境缺 matplotlib / slowapi，
# Python 3.14 _overlapped 损坏，需注入假模块绕过导入期失败）

# matplotlib 假模块（含 use() 函数，避免 AttributeError）
if "matplotlib" not in sys.modules:
    _mpl = types.ModuleType("matplotlib")
    _mpl.use = lambda *a, **kw: None  # noqa: E731
    _mpl.rcParams = {}
    _mpl.figure = lambda *a, **kw: None
    _mpl.pyplot = types.ModuleType("matplotlib.pyplot")
    _mpl.pyplot.figure = lambda *a, **kw: None
    _mpl.pyplot.savefig = lambda *a, **kw: None
    _mpl.pyplot.close = lambda *a, **kw: None
    _mpl.pyplot.show = lambda *a, **kw: None
    sys.modules["matplotlib"] = _mpl
    sys.modules["matplotlib.pyplot"] = _mpl.pyplot

# slowapi 假模块（项目记忆硬约束：路由层依赖，cam_validation 间接导入）
if "slowapi" not in sys.modules:
    _slowapi = types.ModuleType("slowapi")
    _slowapi.Limiter = type(
        "Limiter",
        (),
        {
            "__init__": lambda self, *a, **kw: None,
            "limit": lambda self, *a, **kw: lambda f: f,
        },
    )
    _slowapi.RateLimitExceeded = type("RateLimitExceeded", (Exception,), {})
    _slowapi.get_remote_address = lambda req: "127.0.0.1"
    _slowapi.errors = types.ModuleType("slowapi.errors")
    _slowapi.errors.RateLimitExceeded = _slowapi.RateLimitExceeded
    sys.modules["slowapi"] = _slowapi
    sys.modules["slowapi.errors"] = _slowapi.errors


# 调用 pytest.main() 运行 test_cam_validation.py


def main() -> int:
    """运行 test_cam_validation.py。

    通过 --noconftest 绕过 conftest.py 强制加载 app.api.v1.auth，
    通过 --override-ini="addopts=" 清空 pytest.ini 的 --cov 选项
    （本地无需覆盖率统计；CI 环境由 pytest.ini 自动启用 --cov）。
    """
    import pytest as _pytest

    test_file = str(Path(__file__).resolve().parent / "test_cam_validation.py")
    args = [
        test_file,
        "-v",
        "--no-header",
        "--noconftest",
        "-p",
        "no:cacheprovider",
        "--override-ini=addopts=",
        "--override-ini=filterwarnings=",
    ]
    print(f"[runner] 启动 pytest: {test_file}")
    print(f"[runner] args: {' '.join(args)}")
    print("=" * 70)
    return _pytest.main(args)


if __name__ == "__main__":
    sys.exit(main())
