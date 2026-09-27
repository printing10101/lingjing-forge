"""Backend pytest conftest - shared fixtures for organized test framework.

本文件是后端测试套件的**根 conftest**，承担跨所有测试目录共享的职责：

1. **环境配置**：补齐 ``LNN_JWT_SECRET`` 等在导入期读取的强制环境变量
2. **Torch 加载策略**：真实 torch 优先，无则跳过（避免桩模块掩盖覆盖空洞）
3. **懒加载桩包**：``_install_lazy_app_api_v1`` 避免 lnn 子模块副作用
4. **CJK 兼容**：traceback 中文编码补丁
5. **autouse fixture**：``_env_setup`` 统一测试环境
6. **通用 fixtures**：数据类（MaterialSpec/SensorDataStream/...）、
   G-code 样本、计时器等跨目录复用资产

分层 conftest 组织（pytest 自动发现机制）：
    ``tests/conftest.py``（本文件）           → 全局共享
    ``tests/api/conftest.py``                  → API TestClient fixture
    ``tests/security/conftest.py``             → 安全测试环境变量
    ``tests/simulation/conftest.py``           → rust_engine / voxel_cutter fixtures
    ``app/api/v1/tests/conftest.py``           → mock task_system
    ``app/simulation/chatter/tests/conftest.py``→ sys.path 设置

子目录 conftest 仅承载该目录专属的 fixture，避免污染全局作用域。
若 fixture 需跨目录复用，应上提到本文件；若仅单目录使用，应下沉到对应子 conftest。
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

# Windows WinSock 损坏兜底：asyncio 导入期失败的 stub 注入
# 背景：
# - 当前 Windows 环境下 ``_overlapped`` 模块损坏（OSError [WinError 10038]
# 在一个非套接字上尝试了一个操作），导致 ``asyncio.windows_events`` 导入失败，
# 进而使整个 ``asyncio`` 包不可用，pytest 无法启动任何依赖 asyncio 的测试。
# - 根本修复需管理员执行 ``netsh winsock reset`` 并重启系统；在修复之前，
# 本 conftest 在导入早期注入一个最小 asyncio stub，让 sqlite_retry /
# sqlite_pool / pydantic_core / typing_extensions 等模块的 ``import asyncio``
# 能成功加载，使测试套件可以运行。
# - stub 仅提供类型注解和最基本的同步语义；任何实际触发异步执行的测试
# （如 asyncio.run / await sleep）应当通过 ``pytest.importorskip`` 跳过，
# 而非依赖此 stub 完成真实异步行为。
# - 该 stub 与 ``tests/unit/_verify_p3_fix.py`` 中的实现保持一致，便于维护。
try:
    import asyncio  # noqa: F401
except (OSError, ImportError):
    from contextlib import asynccontextmanager

    _asyncio_stub = types.ModuleType("asyncio")
    _asyncio_stub.asynccontextmanager = asynccontextmanager

    async def _async_sleep(_delay: float) -> None:
        """stub: 真实测试路径不应触发；若被调用则立即返回。"""

    _asyncio_stub.sleep = _async_sleep

    class _DummyAsync:
        """通用异步原语 stub：构造与基本协议满足模块级类型注解需求。"""

        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "_DummyAsync":
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        def set(self) -> None:
            pass

        def is_set(self) -> bool:
            return False

        def acquire(self) -> "_DummyAsync":
            return self

        def release(self) -> None:
            pass

    _asyncio_stub.Event = _DummyAsync
    _asyncio_stub.Lock = _DummyAsync
    _asyncio_stub.Semaphore = _DummyAsync
    _asyncio_stub.Future = _DummyAsync
    _asyncio_stub.Task = _DummyAsync
    _asyncio_stub.get_event_loop = lambda: None
    _asyncio_stub.new_event_loop = lambda: None
    _asyncio_stub.set_event_loop = lambda _loop: None
    _asyncio_stub.iscoroutinefunction = lambda _func: False
    _asyncio_stub.iscoroutine = lambda _obj: False
    _asyncio_stub.run = lambda _coro: None

    # pydantic_core / typing_extensions 会 ``import asyncio.coroutines``
    # 以及 ``import asyncio.futures``，需补充子模块 stub 避免导入失败。
    _coroutines_stub = types.ModuleType("asyncio.coroutines")
    _coroutines_stub.iscoroutine = lambda _obj: False
    _coroutines_stub.iscoroutinefunction = lambda _func: False
    _asyncio_stub.coroutines = _coroutines_stub
    sys.modules["asyncio.coroutines"] = _coroutines_stub

    _futures_stub = types.ModuleType("asyncio.futures")
    _futures_stub.Future = _DummyAsync
    _futures_stub.isfuture = lambda _obj: False
    _asyncio_stub.futures = _futures_stub
    sys.modules["asyncio.futures"] = _futures_stub

    _tasks_stub = types.ModuleType("asyncio.tasks")
    _tasks_stub.Task = _DummyAsync
    _tasks_stub.iscoroutine = lambda _obj: False
    _tasks_stub.sleep = _async_sleep
    _tasks_stub.ensure_future = lambda _coro: _coro
    _tasks_stub.gather = lambda *coros: coros[0] if coros else None
    _asyncio_stub.tasks = _tasks_stub
    sys.modules["asyncio.tasks"] = _tasks_stub

    _base_events_stub = types.ModuleType("asyncio.base_events")
    _asyncio_stub.base_events = _base_events_stub
    sys.modules["asyncio.base_events"] = _base_events_stub

    sys.modules["asyncio"] = _asyncio_stub

# Python 3.10 兼容：为 ``enum.StrEnum`` 注入 polyfill
# 背景：
# - ``enum.StrEnum`` 是 Python 3.11+ 引入的枚举类型；项目代码
# （如 ``app.core.response``）以及部分性能测试直接 ``from enum import StrEnum``。
# - 当前 CI 使用 Python 3.10.11，导入会触发 ImportError，导致依赖
# StrEnum 的测试被 skip（掩盖性能覆盖），生产路径在 3.10 下也无法加载。
# - polyfill 实现 ``class StrEnum(str, Enum)`` 与 3.11 内建语义等价（PEP 663），
# 使 3.10 环境也能跑通相关测试与代码路径。3.11+ 直接用原版 StrEnum，不覆盖。
import enum as _enum

if not hasattr(_enum, "StrEnum"):

    class StrEnum(str, _enum.Enum):
        """``enum.StrEnum`` 的 Python 3.10 polyfill（与 3.11+ 语义等价）。"""

        def __str__(self) -> str:  # noqa: D401
            return self._name_  # type: ignore[no-any-return]

        def __format__(self, format_spec: str) -> str:
            return str.__format__(self._name_, format_spec)  # type: ignore[arg-type]

    _enum.StrEnum = StrEnum  # type: ignore[attr-defined]

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 测试环境配置：补齐 ``app.auth.security`` 在导入期读取的强制环境变量。
# 背景：``app.auth.security.SECRET_KEY`` 在模块加载时会调用
# ``_validate_and_get_secret``，未设置 ``LNN_JWT_SECRET`` 时会抛出 RuntimeError
# 阻断后续任何依赖 ``app.api.v1.auth`` 的测试。这里在 conftest 入口补齐默认值。
if not os.environ.get("LNN_JWT_SECRET"):
    # P2-13 修复：使用 secrets.token_hex 动态生成测试密钥，避免硬编码已知密钥。
    # 硬编码密钥提交到版本控制后，若测试环境意外暴露（CI 日志泄露、外部访问），
    # 攻击者可用此已知密钥伪造 JWT。动态生成确保每次测试运行使用不同密钥。
    import secrets as _secrets

    os.environ["LNN_JWT_SECRET"] = _secrets.token_hex(32)

# 认证开关默认关闭（app import 前固化）
# 背景：本 conftest 的 ``# [2026-08-13 审计修复] lazy stub 会空化 app.api.v1/__init__.py 的聚合 re-export
# （lnn_uncertain 等符号丢失），导致测试环境路由注册不完整（630 559）：
# from app.api.v1 import lnn_uncertain ImportError 域注册器降级吞错 lnn 路由 404。
# 原目的（避免 app.api.v1 导入链触发 torch 初始化）已无必要：本 conftest 顶部已
# 真实导入 torch（S7 策略），且 pytest-cov 冲突有专项处理。保留函数定义便于回退。
# _install_lazy_app_api_v1()`` 会立即 resolve ``app.api.v1.auth``，
# 其模块链会提前创建 ``app.config.config`` 实例；而 config 的 SecurityConfig 在
# 创建时读取认证环境变量并固化。若此时未设置开关，认证默认启用（LNN/JWT/AGENT
# 均为 True），后续测试即使通过 fixture 修改环境变量也无法改变已固化的中间件配置，
# 导致所有 API 测试 401。此处用 setdefault 在 app 首次 import 前预置测试环境
# 默认值（用户/CI 显式设置的值优先）。fixture ``_env_setup`` 仍用 setenv 强制
# 覆盖，双保险。
os.environ.setdefault("LNN_AUTH_ENABLED", "false")
os.environ.setdefault("AGENT_AUTH_ENABLED", "false")
os.environ.setdefault("LNN_JWT_AUTH_ENABLED", "false")
os.environ.setdefault("LNN_PERMISSION_ENFORCED", "false")
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("LNN_REGISTRATION_CODE", "SECRET-1234")


# Torch 加载策略：真实 torch 优先，无 torch 时显式标记并跳过
# 学术诚信修复 [S7]：
# 原实现通过 _LazyStubModule 注入完整的 torch 桩模块，使所有依赖 torch 的
# 测试在不安装 torch 的环境下也能"通过"——这掩盖了真实的测试覆盖空洞。
#
# 新策略：
# 1. 优先尝试导入真实 torch；成功则直接使用，测试执行真实计算路径。
# 2. 若真实 torch 不可用，打印明确警告并让依赖 torch 的测试通过
# ``pytest.importorskip("torch")`` 自然跳过，而非用桩模块伪装通过。
# 3. 仅保留对 torch C 扩展冲突的容错处理（retry 机制），不再注入桩。
#
# 这保证了：
# - 有 torch 时：测试走真实 torch 路径，结果可信；
# - 无 torch 时：测试显式跳过，不会产出虚假的"全绿"报告。
if "torch" not in sys.modules:
    try:
        import torch  # noqa: F401  # 真实 torch 导入
    except ImportError:
        # 真实 torch 不可用：不注入桩模块，让 pytest.importorskip 自然跳过
        import warnings

        warnings.warn(
            "[S7] 真实 torch 不可用。依赖 torch 的测试将通过 "
            "pytest.importorskip('torch') 跳过，而非通过桩模块伪装通过。"
            "如需运行完整测试套件，请安装 PyTorch。",
            stacklevel=2,
        )
    except RuntimeError as _torch_init_err:
        # torch C 扩展冲突（如 pytest-cov 重复加载场景）
        # 清理后重试一次；若仍失败则回退到无 torch 模式
        import warnings

        for _mod_name in list(sys.modules.keys()):
            if _mod_name == "torch" or _mod_name.startswith("torch."):
                sys.modules.pop(_mod_name, None)
        try:
            import torch  # noqa: F401  # 重试导入
        except Exception:
            warnings.warn(
                f"[S7] torch 导入失败（C 扩展冲突）：{_torch_init_err}。依赖 torch 的测试将跳过。",
                stacklevel=2,
            )


# Stub ``app.api.v1`` package: 避免导入 auth 模块时触发 ``lnn`` 等子模块的副作用链
# 背景：
# - ``app.api.v1/__init__.py`` 一次性导入所有子模块（lnn/jobs/plugins/...）。
# - 其中 lnn 会进一步触发 ``app.ai.lnn`` -> ``torch`` 的初始化，pytest-cov 多次
# 运行时会与 C 扩展冲突。
# - 我们关心的只有 ``app.api.v1.auth``，因此在测试环境下将 ``app.api.v1`` 替换为
# 懒加载式的桩包：仅当真正访问 ``app.api.v1.auth`` 等子模块时才进行按需加载。
# - 这种方法对其他测试无影响，因为生产代码中 ``app.api.v1.auth`` 仍然按需加载。
def _install_lazy_app_api_v1() -> None:
    """将 ``app.api.v1`` 替换为懒加载桩包。"""
    import importlib
    import importlib.util
    from pathlib import Path as _Path

    # 清理已加载的中间包，确保 stub 生效
    for mod_name in [
        "app.api",
        "app.api.v1",
        "app.api.v1.lnn",
        "app.api.v1.jobs",
        "app.api.v1.plugins",
        "app.api.v1.skills",
        "app.api.v1.sse",
        "app.api.v1.agent_gateway",
        "app.api.v1.cost_budget",
        "app.api.v1.governance",
        "app.api.v1.goal_alignment",
        "app.api.v1.heartbeat",
        "app.api.v1.wear_prediction",
        "app.api.v1.user_sovereignty",
        "app.api.v1.task_checkout",
        "app.api.v1.template_ab_testing_routes",
        "app.api.v1.template_branching_routes",
        "app.api.v1.template_evolution_routes",
        "app.api.v1.template_market",
        "app.api.v1.template_update_routes",
        "app.api.v1.pattern_engine_routes",
        "app.ai.lnn",
    ]:
        sys.modules.pop(mod_name, None)

    # 从本 conftest.py 位置推断 app 包物理路径
    _python_dir = _Path(__file__).resolve().parent.parent
    _real_app_path = _python_dir / "app"
    _real_app_api_path = _real_app_path / "api"
    _real_app_api_v1_path = _real_app_api_path / "v1"

    if not _real_app_api_v1_path.exists():
        raise RuntimeError(f"无法定位 app.api.v1 目录: {_real_app_api_v1_path}")

    # 安装一个非常薄的 ``app.api`` 桩，保持 __path__ 指向真实目录
    _app_api_pkg = types.ModuleType("app.api")
    _app_api_pkg.__path__ = [str(_real_app_api_path)]  # type: ignore[attr-defined]
    sys.modules["app.api"] = _app_api_pkg

    # 安装 ``app.api.v1`` 桩，但其 ``__init__`` 是空白（不触发子模块导入）
    _app_api_v1_pkg = types.ModuleType("app.api.v1")
    _app_api_v1_pkg.__path__ = [str(_real_app_api_v1_path)]  # type: ignore[attr-defined]
    sys.modules["app.api.v1"] = _app_api_v1_pkg

    def _resolve(name: str):
        # 先尝试从真实 v1 目录加载子模块
        spec = importlib.util.spec_from_file_location(f"app.api.v1.{name}", _real_app_api_v1_path / f"{name}.py")
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load app.api.v1.{name}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    # 暴露常用子模块（按需加载）。仅在测试中真正访问时才加载。
    setattr(_app_api_v1_pkg, "auth", _resolve("auth"))


_install_lazy_app_api_v1()


# Python 3.11.0rc2 traceback 模块 CJK 异常消息 UnicodeDecodeError 兼容补丁
# 背景：
# - 当前 CI 使用 Python 3.11.0rc2，其 ``traceback.TracebackException.format_frame_summary``
# 在格式化包含非 ASCII 字符（如 CJK）的异常链时会出现 UnicodeDecodeError。
# - 该问题在 Python 3.11.0 正式版及更高版本已修复。
# - 我们的异常处理器大量使用中文提示信息来告知用户具体错误原因，
# 所以测试中需要规避此 traceback bug。
# - 此处 monkey-patch 掉 ``TracebackException.format`` 与 ``print_exception``，
# 在 UnicodeDecodeError 时回退到 ``format_exception_only`` 输出来自异常类型 + 消息，
# 保证测试不因 traceback bug 崩溃，同时保留足够的诊断信息。
def _patch_traceback_for_cjk() -> None:
    import sys as _sys

    if _sys.version_info[:3] >= (3, 11, 1):
        # 正式版或更高版本无需该补丁
        return

    try:
        import traceback as _tb
    except ImportError:
        return

    if getattr(_tb, "_lingjing_cjk_patch_applied", False):
        return

    _orig_print_exception = _tb.print_exception

    def _safe_print_exception(exc, value, tb, limit=None, file=None, chain=True):
        try:
            return _orig_print_exception(exc, value, tb, limit, file, chain)
        except UnicodeDecodeError:
            # 退化输出：只保留异常类型 + 消息摘要，避免格式化整个 traceback 链
            try:
                if file is None:
                    import io as _io

                    file = _io.StringIO()
                for line in _tb.format_exception_only(exc, value):
                    file.write(line + "\n")
                return file
            except Exception:
                return file

    _tb.print_exception = _safe_print_exception

    # 同时对 logging.Formatter.formatException 进行容错（该方法内部调用
    # traceback.print_exception），从而覆盖 pytest 捕获异常格式化路径。
    _orig_format_exception = _tb.format_exception

    def _safe_format_exception(etype, value, tb, limit=None, chain=True):
        try:
            return _orig_format_exception(etype, value, tb, limit, chain)
        except UnicodeDecodeError:
            return _tb.format_exception_only(etype, value)

    _tb.format_exception = _safe_format_exception

    _tb._lingjing_cjk_patch_applied = True


_patch_traceback_for_cjk()


@pytest.fixture(autouse=True)
def _env_setup(monkeypatch, tmp_path):
    """Ensure test environment variables are set before each test.

    同时为 ``LNN_BANNED_TOKENS_FILE`` 指向临时目录，避免测试间持久化状态泄漏
    （特别是 ``test_jwt_with_banned_token_returns_401`` 会写入 ban list，
    其他测试不应被该状态影响）。

    注意：``app.auth.security.BANNED_TOKENS_FILE`` 是在模块导入时计算的常量，
    改变环境变量后必须同步更新该常量并重置 ``_token_ban_list`` 单例。
    """
    monkeypatch.setenv("LNN_AUTH_ENABLED", "false")
    monkeypatch.setenv("AGENT_AUTH_ENABLED", "false")
    monkeypatch.setenv("LNN_JWT_AUTH_ENABLED", "false")
    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
    monkeypatch.setenv("LNN_GSTACK_DIR", ".lingjing/.gstack_test")
    monkeypatch.setenv("ENVIRONMENT", "testing")
    # 使用临时文件隔离 ban list 状态，防止跨测试污染
    banned_file = str(tmp_path / ".lnn_banned_tokens.json")
    monkeypatch.setenv("LNN_BANNED_TOKENS_FILE", banned_file)
    # 强制重置 ban list 单例并更新 BANNED_TOKENS_FILE 常量，使新的临时文件生效
    try:
        from app.auth import security as _sec

        _sec.BANNED_TOKENS_FILE = banned_file
        _sec._token_ban_list = None
    except Exception:
        pass
    # 失败案例库隔离（自进化 M0）：编排器 / NL2CAD 的成败入册钩子会在
    # 测试中触发写库，统一重定向到临时目录并重置单例，防止污染开发库
    monkeypatch.setenv("FAILURE_CASES_DB", str(tmp_path / "failure_cases.db"))
    try:
        from app.gcode_generation.failure_case_store import (
            reset_failure_case_store as _reset_fc_store,
        )

        _reset_fc_store()
    except Exception:
        pass
    yield
    try:
        from app.gcode_generation.failure_case_store import (
            reset_failure_case_store as _reset_fc_store,
        )

        _reset_fc_store()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_rate_limiter_state():
    """每测试前后重置全部限流器状态，防止跨测试文件累积触发 429.

    根因（2026-09 工程体检）：app/api/v1/auth.py 在同一 pytest 进程内可能被
    执行两次（conftest 懒加载桩 _resolve("auth") 一次 + 正常 import 链一次，
    是否发生取决于模块收集顺序，非确定）。slowapi 的 Limiter 按
    "模块路径.函数名" 向 _route_limits 追加限流条目，双次执行产生两条相同
    3/hour 条目 → 每请求计数两次 → test_auth_register 的 409/429 断言被
    提前触发的 429 击穿（全量混跑偶发、单文件跑无法复现）。

    清理实现见 tests/utils/rate_limiter_reset.py（可单测）。
    """
    try:
        from tests.utils.rate_limiter_reset import reset_all_rate_limiters

        reset_all_rate_limiters()
    except Exception:
        pass
    yield
    try:
        from tests.utils.rate_limiter_reset import reset_all_rate_limiters

        reset_all_rate_limiters()
    except Exception:
        pass


# 集成测试专用 Fixtures


@dataclass
class MaterialSpec:
    """材料规格数据类."""

    name: str
    density: float  # g/cm³
    hardness_hb: float
    tensile_strength: float  # MPa
    machinability: float  # 0-1 可加工性
    thermal_conductivity: float  # W/(m·K)
    cutting_speed_range: tuple[float, float]  # m/min
    feed_range: tuple[float, float]  # mm/r
    depth_of_cut_range: tuple[float, float]  # mm


@dataclass
class SensorDataStream:
    """模拟传感器数据流."""

    timestamp: float
    vibration_x: float
    vibration_y: float
    vibration_z: float
    temperature: float
    acoustic_emission: float
    spindle_speed: float
    feed_rate: float
    cutting_force: float


@dataclass
class ProcessCard:
    """工艺卡片数据结构."""

    material: str
    part_name: str
    operations: list[dict[str, Any]] = field(default_factory=list)
    cutting_parameters: dict[str, Any] = field(default_factory=dict)
    estimated_time: float = 0.0  # hours
    batch_size: int = 1


@dataclass
class RiskItem:
    """风险条目."""

    risk_id: str
    category: str  # 安全/质量/效率/设备
    description: str
    severity: str  # 高/中/低
    probability: str  # 高/中/低
    mitigation: str


# 材料数据 fixtures
@pytest.fixture
def material_steel_45() -> MaterialSpec:
    """45号钢材料参数."""
    return MaterialSpec(
        name="45号钢",
        density=7.85,
        hardness_hb=197,
        tensile_strength=600,
        machinability=0.65,
        thermal_conductivity=50.2,
        cutting_speed_range=(100, 250),
        feed_range=(0.1, 0.5),
        depth_of_cut_range=(0.5, 5.0),
    )


@pytest.fixture
def material_tc4() -> MaterialSpec:
    """TC4钛合金材料参数."""
    return MaterialSpec(
        name="TC4钛合金",
        density=4.43,
        hardness_hb=330,
        tensile_strength=895,
        machinability=0.22,
        thermal_conductivity=7.2,
        cutting_speed_range=(30, 80),
        feed_range=(0.05, 0.15),
        depth_of_cut_range=(0.3, 2.5),
    )


@pytest.fixture
def material_aluminum_6061() -> MaterialSpec:
    """6061铝合金材料参数."""
    return MaterialSpec(
        name="6061铝合金",
        density=2.70,
        hardness_hb=95,
        tensile_strength=310,
        machinability=0.90,
        thermal_conductivity=167,
        cutting_speed_range=(200, 600),
        feed_range=(0.1, 1.0),
        depth_of_cut_range=(0.5, 6.0),
    )


# 三视图模拟数据 fixtures
@pytest.fixture
def standard_3view_images(temp_dir) -> dict[str, str]:
    """生成标准三视图模拟图像文件（PNG占位）. 实际测试中应替换为真实工程图."""
    views = {}
    for view_name in ["front", "side", "top"]:
        filepath = temp_dir / f"{view_name}_view.png"
        # 创建最小的PNG文件作为占位符
        _write_minimal_png(filepath, 1920, 1080)
        views[view_name] = str(filepath)
    return views


# IT8公差数据 fixtures
@pytest.fixture
def it8_tolerance_data() -> dict[str, Any]:
    """IT8级公差数值范围."""
    return {
        "grade": "IT8",
        "nominal_ranges": {
            "1-3mm": 0.014,
            "3-6mm": 0.018,
            "6-10mm": 0.022,
            "10-18mm": 0.027,
            "18-30mm": 0.033,
            "30-50mm": 0.039,
            "50-80mm": 0.046,
            "80-120mm": 0.054,
            "120-180mm": 0.063,
            "180-250mm": 0.072,
        },
    }


# 加工参数 fixtures
@pytest.fixture
def machining_params_steel() -> dict[str, Any]:
    """45号钢加工参数集."""
    return {
        "cutting_speed": 150.0,  # m/min
        "feed_rate": 0.2,  # mm/r
        "depth_of_cut": 2.0,  # mm
        "spindle_speed": 4775,  # r/min
        "coolant": True,
        "tool_material": "硬质合金",
        "tool_diameter": 10.0,  # mm
    }


@pytest.fixture
def machining_params_tc4() -> dict[str, Any]:
    """TC4钛合金加工参数集."""
    return {
        "cutting_speed": 50.0,
        "feed_rate": 0.08,
        "depth_of_cut": 1.0,
        "spindle_speed": 1592,
        "coolant": True,
        "tool_material": "硬质合金涂层",
        "tool_diameter": 10.0,
    }


# 传感器数据流 fixtures
@pytest.fixture
def normal_sensor_stream() -> list[SensorDataStream]:
    """正常加工状态传感器数据流（10秒，1kHz采样率）."""
    data = []
    base_time = time.time()
    for i in range(10000):  # 10秒 * 1kHz
        t = float(i) / 1000.0
        data.append(
            SensorDataStream(
                timestamp=base_time + t,
                vibration_x=0.5 + random.gauss(0, 0.05),
                vibration_y=0.4 + random.gauss(0, 0.04),
                vibration_z=0.3 + random.gauss(0, 0.03),
                temperature=35.0 + random.gauss(0, 0.1),
                acoustic_emission=0.02 + random.gauss(0, 0.002),
                spindle_speed=4775 + random.gauss(0, 5),
                feed_rate=0.2 + random.gauss(0, 0.01),
                cutting_force=150 + random.gauss(0, 5),
            )
        )
    return data


@pytest.fixture
def anomaly_sensor_stream() -> list[SensorDataStream]:
    """异常加工状态传感器数据流（刀具磨损/振动异常）."""
    data = []
    base_time = time.time()
    for i in range(10000):
        t = float(i) / 1000.0
        is_anomaly = i > 5000  # 5秒后开始异常
        vibration_mult = 3.0 if is_anomaly else 1.0
        temp_trend = 35.0 + (t * 0.5 if is_anomaly else 0)
        data.append(
            SensorDataStream(
                timestamp=base_time + t,
                vibration_x=(0.5 + random.gauss(0, 0.05)) * vibration_mult,
                vibration_y=(0.4 + random.gauss(0, 0.04)) * vibration_mult,
                vibration_z=(0.3 + random.gauss(0, 0.03)) * vibration_mult,
                temperature=temp_trend + random.gauss(0, 0.15),
                acoustic_emission=0.06 + random.gauss(0, 0.005),
                spindle_speed=4775 + random.gauss(0, 5),
                feed_rate=0.2 + random.gauss(0, 0.01),
                cutting_force=180 + random.gauss(0, 10),
            )
        )
    return data


# 生产批次数据 fixtures
@pytest.fixture
def production_batch_100() -> dict[str, Any]:
    """100件生产批次管理信息."""
    return {
        "batch_id": "BATCH-2026-001",
        "quantity": 100,
        "material": "45号钢",
        "part_name": "法兰盘-FL-001",
        "order_date": "2026-06-04",
        "due_date": "2026-06-18",
        "priority": "normal",
        "sub_batches": [
            {"sub_id": "BATCH-2026-001-A", "quantity": 50, "machine": "CNC-01"},
            {"sub_id": "BATCH-2026-001-B", "quantity": 50, "machine": "CNC-02"},
        ],
    }


# 工艺路线 fixtures
@pytest.fixture
def sample_process_card() -> ProcessCard:
    """标准工艺卡片样例."""
    card = ProcessCard(
        material="45号钢",
        part_name="法兰盘-FL-001",
        batch_size=100,
    )
    card.operations = [
        {"step": 1, "operation": "下料", "machine": "锯床GZ4230", "description": "按毛坯尺寸下料", "time_min": 2},
        {
            "step": 2,
            "operation": "粗车外圆",
            "machine": "数控车床CK6150",
            "description": "粗车外圆至Φ102mm",
            "time_min": 8,
        },
        {"step": 3, "operation": "粗车端面", "machine": "数控车床CK6150", "description": "粗车两端面", "time_min": 5},
        {"step": 4, "operation": "钻孔", "machine": "加工中心VMC850", "description": "钻4×Φ8mm通孔", "time_min": 10},
        {
            "step": 5,
            "operation": "精车外圆",
            "machine": "数控车床CK6150",
            "description": "精车外圆至Φ100mm(IT8)",
            "time_min": 12,
        },
        {"step": 6, "operation": "铰孔", "machine": "加工中心VMC850", "description": "铰孔至Φ8H8", "time_min": 8},
        {"step": 7, "operation": "检验", "machine": "三坐标测量机", "description": "全尺寸检验", "time_min": 15},
    ]
    card.cutting_parameters = {
        "rough_turning": {"v": 120, "f": 0.3, "ap": 2.0, "n": 800},
        "finish_turning": {"v": 180, "f": 0.1, "ap": 0.5, "n": 1200},
        "drilling": {"v": 25, "f": 0.15, "n": 1000},
        "reaming": {"v": 8, "f": 0.3, "n": 320},
    }
    card.estimated_time = sum(op["time_min"] for op in card.operations) / 60.0
    return card


# NC代码安全规则 fixtures
@pytest.fixture
def nc_validation_rules() -> dict[str, Any]:
    """NC代码验证规则集."""
    return {
        "mandatory_codes": ["G21", "G90", "M30"],
        "forbidden_patterns": [
            r"M00",  # 不应有无条件停止
        ],
        "safety_checks": {
            "spindle_speed_max": 8000,
            "feed_rate_max": 1000,
            "rapid_height_min": 5.0,
        },
    }


# 风险评估模板
@pytest.fixture
def risk_assessment_template() -> list[RiskItem]:
    """加工风险评估模板."""
    return [
        RiskItem("R01", "安全", "切屑飞溅伤害", "高", "中", "使用防护罩，佩戴防护眼镜"),
        RiskItem("R02", "质量", "刀具磨损导致尺寸超差", "中", "中", "定期检测刀具磨损，设置刀具寿命管理"),
        RiskItem("R03", "质量", "切削热导致工件变形", "中", "低", "充分使用切削液，控制切削参数"),
        RiskItem("R04", "设备", "主轴过载", "中", "低", "监控主轴负载率，合理设置切削参数"),
        RiskItem("R05", "效率", "工艺路线不合理导致工时增加", "低", "低", "优化工序顺序，减少换刀次数"),
    ]


# 辅助工具


def _write_minimal_png(filepath: Path, width: int, height: int) -> None:
    """生成最小合法PNG文件."""
    import struct
    import zlib

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_data = b""
    for y in range(height):
        raw_data += b"\x00" + b"\xff\xff\xff" * width
    idat_data = zlib.compress(raw_data)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr_data) + chunk(b"IDAT", idat_data) + chunk(b"IEND", b"")
    filepath.write_bytes(png)


@pytest.fixture
def high_precision_timer():
    """高精度计时器 fixture - 精度>=1ms."""

    class HighPrecisionTimer:
        def __init__(self):
            self._start = 0.0
            self._end = 0.0

        def __enter__(self):
            self._start = time.perf_counter()
            return self

        def __exit__(self, *args):
            self._end = time.perf_counter()

        @property
        def elapsed_ms(self) -> float:
            return (self._end - self._start) * 1000.0

        @property
        def elapsed_s(self) -> float:
            return self._end - self._start

        def reset(self):
            self._start = time.perf_counter()
            self._end = 0.0

        def stop(self):
            self._end = time.perf_counter()

    return HighPrecisionTimer()


@pytest.fixture
def test_report_collector():
    """测试报告收集器."""

    class ReportCollector:
        def __init__(self):
            self.results: list[dict[str, Any]] = []
            self.performance_metrics: dict[str, list[float]] = {}

        def add_result(self, test_name: str, passed: bool, details: str = "", metrics: dict[str, Any] | None = None):
            self.results.append(
                {
                    "test": test_name,
                    "passed": passed,
                    "details": details,
                    "metrics": metrics or {},
                    "timestamp": time.time(),
                }
            )

        def record_metric(self, name: str, value: float):
            if name not in self.performance_metrics:
                self.performance_metrics[name] = []
            self.performance_metrics[name].append(value)

        def get_summary(self) -> dict[str, Any]:
            total = len(self.results)
            passed = sum(1 for r in self.results if r["passed"])
            return {
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "pass_rate": passed / total if total > 0 else 0.0,
                "metrics_avg": {k: sum(v) / len(v) for k, v in self.performance_metrics.items()},
            }

        def to_json(self) -> str:
            return json.dumps(self.get_summary(), indent=2, ensure_ascii=False)

    return ReportCollector()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@dataclass
class Point3D:
    """3D point representation for geometry tests."""

    x: float
    y: float
    z: float = 0.0


@dataclass
class Circle2D:
    """2D circle representation for geometry tests."""

    center_x: float
    center_y: float
    radius: float


@dataclass
class Polygon2D:
    """2D polygon representation for geometry tests."""

    vertices: list[tuple[float, float]]


@pytest.fixture
def sample_circle() -> Circle2D:
    """Sample circle: center at origin, radius 10."""
    return Circle2D(center_x=0.0, center_y=0.0, radius=10.0)


@pytest.fixture
def sample_polygon_square() -> Polygon2D:
    """Sample square polygon: 10x10 centered at origin."""
    return Polygon2D(
        vertices=[
            (-5.0, -5.0),
            (5.0, -5.0),
            (5.0, 5.0),
            (-5.0, 5.0),
        ]
    )


@pytest.fixture
def sample_polygon_triangle() -> Polygon2D:
    """Sample triangle polygon."""
    return Polygon2D(
        vertices=[
            (0.0, 10.0),
            (-8.66, -5.0),
            (8.66, -5.0),
        ]
    )


@pytest.fixture
def sample_gcode_fanuc() -> str:
    """Sample Fanuc G-code for testing."""
    return """%
O0001 (PROGRAM 1 - TEST)
(POST: Fanuc 0i-MF)
G21 G17 G40 G49 G80 G90 G94
G00 G91 G28 Z0.
G00 G91 G28 X0. Y0.
G00 G90 G54 X0. Y0.
G00 G43 Z50.000 H00
M03 S8000
M08
G01 X10.000 Y10.000 F500.000
G01 X20.000 Y20.000 F500.000
M09
M05
G00 G91 G28 Z0.
G00 G91 G28 X0. Y0.
G90
M30
%"""


@pytest.fixture
def sample_gcode_heidenhain() -> str:
    """Sample Heidenhain code for testing."""
    return """0  BEGIN PGM 0001 MM
1  BLK FORM 0.1 Z X+0 Y+0 Z-50
2  BLK FORM 0.2 X+100 Y+100 Z+0
3  ; PROGRAM 1 - TEST
4  ; POST: Heidenhain TNC
5  TOOL CALL 1 Z S8000
6  L  Z+50.000 R0 FMAX
7  L  X+0 Y+0 R0 FMAX
8  M08
9  L  X+10.000 Y+10.000 F500.000
10  M09
11  M05
12  L  Z+50.000 R0 FMAX
13  L  X+0 Y+0 R0 FMAX
14  M30
15  END PGM 0000 MM"""


@pytest.fixture
def sample_gcode_siemens() -> str:
    """Sample Siemens G-code for testing."""
    return """N00010 ; PROGRAM 1 - TEST
N00020 ; POST: Siemens 840D
N00030 G17 G40 G90 G94
N00040 G00 Z50.000
N00050 G00 X0. Y0.
N00060 M03 S8000
N00070 M08
N00080 G01 X10.000 Y10.000 F500.000
N00090 M09
N00100 M05
N00110 G00 Z50.000
N00120 G00 X0. Y0.
N00130 M30"""


@pytest.fixture
def benchmark_gcode_tolerance() -> dict:
    """Default tolerance for G-code regression comparison."""
    return {
        "coordinate_precision": 0.01,
        "feed_rate_tolerance_percent": 5.0,
        "spindle_speed_tolerance_percent": 2.0,
        "ignore_comments": True,
        "ignore_program_numbers": True,
        "ignore_timestamps": True,
    }


@pytest.fixture
def performance_timer():
    """Timer fixture for measuring test execution time."""

    class Timer:
        def __init__(self):
            self.start_time = None
            self.end_time = None

        def __enter__(self):
            self.start_time = time.perf_counter()
            return self

        def __exit__(self, *args):
            self.end_time = time.perf_counter()

        @property
        def elapsed_ms(self) -> float:
            if self.start_time and self.end_time:
                return (self.end_time - self.start_time) * 1000
            return 0.0

        @property
        def elapsed_s(self) -> float:
            return self.elapsed_ms / 1000.0

    return Timer()
