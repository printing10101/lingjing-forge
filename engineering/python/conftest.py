"""项目根 conftest.py（python/ 目录）。

职责：
1. 将 ``python/`` 加入 sys.path，使 ``from app.xxx import yyy`` 绝对导入可用。
2. 显式忽略 ``app/`` 目录的收集，防止 pytest 9.x 跨目录调用时 testpaths
   解析不一致导致递归扫描 ``app/**/tests/`` 内嵌测试。

防复发机制 [E-P0-3]：
    ``app/ai/lnn/tests/test_gpu_training.py`` 顶层导入 torch → pandas →
    pyarrow.compute，在 pytest 字节码改写上下文中触发 STATUS_HEAP_CORRUPTION
    (0xC0000374)。本文件与 ``pytest.ini`` 的 ``norecursedirs = app`` 形成双重
    防护，确保默认收集只扫描 ``python/tests`` 集中测试目录。

如需运行模块自测，显式指定路径：
    pytest python/app/ai/lnn/tests/ --no-cov -p no:cacheprovider
"""

import os
import sys
import types
from pathlib import Path

# SQLite 连接池测试模式（M1 修复）
# 启用 fail-fast：连接池耗尽时立即抛出 RuntimeError，而非 30s 忙等死锁。
# 根因：pytest_full_v3.log:231 显示 fixture 阶段 GoalChainStore
# sqlite_pool.get_connection time.sleep(0.1) 自旋触发 Timeout。
# 生产环境不受影响（连接池正常工作时不会进入等待分支）。
os.environ.setdefault("LNN_SQLITE_POOL_FAIL_FAST", "1")

# WinSock 损坏绕过补丁（仅 Windows）
# 本机 _overlapped 模块因系统级 WinSock 损坏无法导入（WinError 10038），
# 导致 anyio asyncio.windows_events _overlapped 导入链失败，
# 进而使 pytest 启动阶段（load_setuptools_entrypoints）崩溃。
# 此处注入一个空实现的 _overlapped 模块以绕过导入阶段失败。
# 测试用例中需要真实异步 IO 的场景应使用 asyncio.SelectorEventLoop，
# 或在 fixture 内显式 patch asyncio.ProactorEventLoop。
# 根因修复：以管理员身份运行 `netsh winsock reset` 并重启系统。
# [2026-09-05] 必须限定 win32：Linux 上 _overlapped 本就不存在，import 抛出的
# ModuleNotFoundError 不是 OSError 子类，原 except 会直接穿透导致 CI 全部
# pytest job 在 conftest 加载期崩溃（exit 4，零测试运行）。
if sys.platform == "win32":
    try:
        import _overlapped  # noqa: F401
    except OSError:
        _patch = types.ModuleType("_overlapped")
        _patch.Overlapped = type("Overlapped", (), {})
        sys.modules["_overlapped"] = _patch
        print("[warn] _overlapped 模块加载失败，已注入空实现绕过 WinSock 损坏。")

# 强制使用 SelectorEventLoop（避免 IOCP/_overlapped 不完整问题）
# 背景：_overlapped stub 仅提供 Overlapped 属性，但 ProactorEventLoop 的
# IocpProactor 还需要 CreateIoCompletionPort / GetQueuedCompletionStatus 等
# 函数。强制使用 SelectorEventLoop 可避免 async 测试因 IOCP 调用失败。
# 根因修复（netsh winsock reset + 重启）后可安全移除此段。
import sys as _sys

if _sys.platform == "win32":
    try:
        import asyncio as _asyncio

        _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# 将 python/ 目录加入 sys.path（与 python/tests/conftest.py 保持一致）
_PYTHON_ROOT = Path(__file__).parent.resolve()
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

# 显式忽略 app/ 目录的测试收集（双重防护：与 pytest.ini norecursedirs 配合）。
# 背景：曾为支持 tests/api 集成测试临时注释，但 pytest.ini 的 norecursedirs
# 已含 app，API 测试并不依赖收集 app/**/tests；且该防护是 E-P0-3 防复发
# 机制的一部分（防止 pytest 9.x 跨目录调用时误扫 app 内嵌测试触发
# torch 导入 STATUS_HEAP_CORRUPTION），必须保留。
collect_ignore = ["app"]
