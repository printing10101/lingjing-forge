"""P3 修复验证脚本（独立运行，绕过 asyncio / unittest.mock / pytest）。

仅验证 P3 修复的代码逻辑正确性，使用纯 Python 替身对象。

运行方式：
    python tests/unit/_verify_p3_fix.py
"""

from __future__ import annotations

import sys
import inspect
import types
from pathlib import Path

# 将 engineering/python 加入 sys.path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


# asyncio stub 注入
# Windows + Python 3.10.11 下 asyncio 模块导入可能因 WinError 10038 失败
# （asyncio/windows_events.py 在非套接字上尝试操作）。
# P3 验证只调用 close()/stop() 方法，不触发 asyncio.sleep 等运行时调用，
# 因此注入 stub 让 sqlite_retry / sqlite_pool 等模块的 ``import asyncio``
# 能成功加载，从而绕过环境问题。
try:
    import asyncio  # noqa: F401
except (OSError, ImportError):
    from contextlib import asynccontextmanager

    _asyncio_stub = types.ModuleType("asyncio")
    _asyncio_stub.asynccontextmanager = asynccontextmanager

    async def _async_sleep(_delay: float) -> None:
        """stub: 实际验证路径不应触发；若被调用则立即返回。"""

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


class CallRecorder:
    """记录方法调用的最简替身，不依赖 unittest.mock。

    支持 __getattr__ 自动创建可调用的子记录器，
    模拟 MagicMock 的 call_count / call_args_list / assert_called_once 等行为。
    """

    def __init__(self, name: str = "", parent: "CallRecorder | None" = None):
        self._name = name
        self._parent = parent
        self.call_count = 0
        self.call_args_list: list = []
        self._side_effect: Exception | None = None
        self._return_value: object = None
        self._children: dict[str, "CallRecorder"] = {}

    def __getattr__(self, attr: str) -> "CallRecorder":
        if attr.startswith("_"):
            raise AttributeError(attr)
        if attr not in self._children:
            child = CallRecorder(attr, self)
            self._children[attr] = child
        return self._children[attr]

    def __call__(self, *args, **kwargs) -> object:
        self.call_count += 1
        self.call_args_list.append((args, kwargs))
        if self._side_effect is not None:
            raise self._side_effect
        return self._return_value

    def assert_called_once(self) -> None:
        assert self.call_count == 1, f"{self._name}: expected 1 call, got {self.call_count}"

    def assert_called_once_with(self, *args) -> None:
        self.assert_called_once()
        actual = self.call_args_list[0][0]
        assert actual == args, f"{self._name}: expected {args}, got {actual}"

    def assert_not_called(self) -> None:
        assert self.call_count == 0, f"{self._name}: expected 0 calls, got {self.call_count}"


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(f"FAIL: {msg}")
    print(f"  PASS: {msg}")


# 1. BudgetManager.close() 幂等性


def test_budget_manager_close_idempotent() -> None:
    section("BudgetManager.close() 幂等性")
    from app.budget.budget import BudgetManager

    manager = BudgetManager.__new__(BudgetManager)
    manager._pool = CallRecorder("pool")
    mock_conn = CallRecorder("conn")
    manager._conn = mock_conn
    manager._closed = False

    # 首次 close
    manager.close()
    assert_true(manager._pool.return_connection.call_count == 1, "首次 close 调用 return_connection 一次")
    assert_true(manager._conn is None, "首次 close 后 _conn 为 None")
    assert_true(manager._closed is True, "首次 close 后 _closed 为 True")

    # 二次 close 应为 no-op
    manager.close()
    assert_true(manager._pool.return_connection.call_count == 1, "二次 close 不再调用 return_connection")

    # 静态契约
    init_src = inspect.getsource(BudgetManager.__init__)
    close_src = inspect.getsource(BudgetManager.close)
    assert_true("self._closed = False" in init_src, "__init__ 初始化 self._closed = False")
    assert_true("if self._closed:" in close_src, "close 方法以 if self._closed: 开头")
    assert_true("self._closed = True" in close_src, "close 方法设置 self._closed = True")


# 2. MultiDimensionCostTracker.close() 幂等性


def test_cost_tracker_close_idempotent() -> None:
    section("MultiDimensionCostTracker.close() 幂等性")
    from app.budget.cost_tracker import MultiDimensionCostTracker

    tracker = MultiDimensionCostTracker.__new__(MultiDimensionCostTracker)
    tracker._pool = CallRecorder("pool")
    mock_conn = CallRecorder("conn")
    tracker._conn = mock_conn
    tracker._closed = False

    tracker.close()
    assert_true(tracker._pool.return_connection.call_count == 1, "首次 close 调用 return_connection 一次")
    assert_true(tracker._conn is None, "首次 close 后 _conn 为 None")
    assert_true(tracker._closed is True, "首次 close 后 _closed 为 True")

    tracker.close()
    assert_true(tracker._pool.return_connection.call_count == 1, "二次 close 不再调用 return_connection")

    # fallback 路径：无 _pool 时回退到 conn.close()
    tracker2 = MultiDimensionCostTracker.__new__(MultiDimensionCostTracker)
    tracker2._pool = None
    mock_conn2 = CallRecorder("conn2")
    tracker2._conn = mock_conn2
    tracker2._closed = False

    tracker2.close()
    assert_true(mock_conn2.close.call_count == 1, "无 _pool 时回退到 conn.close()")
    assert_true(tracker2._closed is True, "fallback close 后 _closed 为 True")

    # 静态契约
    init_src = inspect.getsource(MultiDimensionCostTracker.__init__)
    close_src = inspect.getsource(MultiDimensionCostTracker.close)
    assert_true("self._closed = False" in init_src, "__init__ 初始化 self._closed = False")
    assert_true("if self._closed:" in close_src, "close 方法包含 if self._closed:")
    assert_true("self._closed = True" in close_src, "close 方法设置 self._closed = True")


# 3. RuleDatabase.close() 幂等性


def test_rule_database_close_idempotent() -> None:
    section("RuleDatabase.close() 幂等性")
    from app.database.rule_db import RuleDatabase

    db = RuleDatabase.__new__(RuleDatabase)
    db._pool = CallRecorder("pool")
    mock_conn = CallRecorder("conn")
    db._conn = mock_conn
    db._closed = False

    db.close()
    assert_true(db._pool.return_connection.call_count == 1, "首次 close 调用 return_connection 一次")
    assert_true(db._pool.close_all.call_count == 1, "首次 close 调用 close_all 一次")
    assert_true(db._conn is None, "首次 close 后 _conn 为 None")
    assert_true(db._closed is True, "首次 close 后 _closed 为 True")

    db.close()
    assert_true(db._pool.close_all.call_count == 1, "二次 close 不再调用 close_all（关键幂等性）")
    assert_true(db._pool.return_connection.call_count == 1, "二次 close 不再调用 return_connection")

    # close_all 抛异常不应阻断 close 流程
    db2 = RuleDatabase.__new__(RuleDatabase)
    db2._pool = CallRecorder("pool2")
    db2._pool.close_all._side_effect = RuntimeError("pool closed")
    db2._conn = CallRecorder("conn2")
    db2._closed = False

    db2.close()  # 不应抛出
    assert_true(db2._closed is True, "close_all 抛异常时 _closed 仍被置位")

    # 静态契约
    init_src = inspect.getsource(RuleDatabase.__init__)
    close_src = inspect.getsource(RuleDatabase.close)
    assert_true("self._closed = False" in init_src, "__init__ 初始化 self._closed = False")
    assert_true("if self._closed:" in close_src, "close 方法以 if self._closed: 开头")
    assert_true("self._closed = True" in close_src, "close 方法设置 self._closed = True")


# 4. WakeupQueue.close() 幂等性


def test_wakeup_queue_close_idempotent() -> None:
    section("WakeupQueue.close() 幂等性")
    from app.heartbeat.heartbeat import WakeupQueue

    queue = WakeupQueue.__new__(WakeupQueue)
    queue._pool = CallRecorder("pool")
    mock_conn = CallRecorder("conn")
    queue._conn = mock_conn
    queue._closed = False

    queue.close()
    assert_true(queue._pool.return_connection.call_count == 1, "首次 close 调用 return_connection 一次")
    assert_true(queue._conn is None, "首次 close 后 _conn 为 None")
    assert_true(queue._closed is True, "首次 close 后 _closed 为 True")

    queue.close()
    assert_true(queue._pool.return_connection.call_count == 1, "二次 close 不再调用 return_connection")

    # 静态契约
    init_src = inspect.getsource(WakeupQueue.__init__)
    close_src = inspect.getsource(WakeupQueue.close)
    assert_true("self._closed = False" in init_src, "__init__ 初始化 self._closed = False")
    assert_true("if self._closed:" in close_src, "close 方法以 if self._closed: 开头")
    assert_true("self._closed = True" in close_src, "close 方法设置 self._closed = True")


# 5. VectorStore.close() 幂等性（重点：原 AttributeError 修复验证）


def test_vector_store_close_idempotent() -> None:
    section("VectorStore.close() 幂等性（修复 AttributeError）")
    from app.rag.vector_store import VectorStore

    # 场景 1：首次 close 调用 client.close()
    store = VectorStore.__new__(VectorStore)
    store._client = CallRecorder("client")
    store._collection = CallRecorder("collection")
    store._closed = False

    # 保存原始 close 引用（因为 close() 后 _client 会被置为 None）
    original_close = store._client.close

    store.close()
    assert_true(original_close.call_count == 1, "首次 close 调用 client.close() 一次")
    assert_true(store._client is None, "首次 close 后 _client 为 None")
    assert_true(store._collection is None, "首次 close 后 _collection 为 None")
    assert_true(store._closed is True, "首次 close 后 _closed 为 True")

    # 场景 2：二次 close 为 no-op
    store2 = VectorStore.__new__(VectorStore)
    store2._client = CallRecorder("client2")
    store2._collection = CallRecorder("collection2")
    store2._closed = False
    original_close2 = store2._client.close

    store2.close()
    store2.close()  # 二次调用前 client 已为 None，应直接返回
    assert_true(original_close2.call_count == 1, "二次 close 不再调用 client.close()")
    assert_true(store2._closed is True, "二次 close 后 _closed 仍为 True")

    # 场景 3：client.close() 抛异常时 _closed 仍被置位
    store3 = VectorStore.__new__(VectorStore)
    store3._client = CallRecorder("client3")
    store3._client.close._side_effect = RuntimeError("client broken")
    store3._collection = CallRecorder("collection3")
    store3._closed = False
    original_close3 = store3._client.close

    store3.close()  # 不应抛出异常
    assert_true(store3._closed is True, "client.close() 抛异常时 _closed 仍被置位（finally 块）")
    assert_true(original_close3.call_count == 1, "异常路径下 client.close() 仍只被调用一次")

    store3.close()  # 二次调用应为 no-op
    assert_true(original_close3.call_count == 1, "异常路径后二次 close 不再触发 client.close()")

    # 场景 4：无 client 时 close 安全执行
    store4 = VectorStore.__new__(VectorStore)
    store4._client = None
    store4._collection = None
    store4._closed = False

    store4.close()  # 不应抛出
    assert_true(store4._closed is True, "无 client 时 close 设置 _closed 为 True")

    # 静态契约
    init_src = inspect.getsource(VectorStore.__init__)
    close_src = inspect.getsource(VectorStore.close)
    assert_true("self._closed = False" in init_src, "__init__ 初始化 self._closed = False")
    assert_true("if self._closed:" in close_src, "close 方法以 if self._closed: 开头")
    assert_true("self._closed = True" in close_src, "close 方法设置 self._closed = True")


# 6. HeartbeatScheduler.stop() 静态契约（asyncio 测试因环境问题跳过运行时验证）


def test_heartbeat_scheduler_static_contract() -> None:
    section("HeartbeatScheduler.stop() 静态契约（asyncio 环境问题，仅静态验证）")
    from app.heartbeat.heartbeat import HeartbeatScheduler

    init_src = inspect.getsource(HeartbeatScheduler.__init__)
    stop_src = inspect.getsource(HeartbeatScheduler.stop)

    assert_true("self._stopped = False" in init_src, "__init__ 初始化 self._stopped = False")
    assert_true("if self._stopped:" in stop_src, "stop 方法以 if self._stopped: 开头")
    assert_true("self._stopped = True" in stop_src, "stop 方法设置 self._stopped = True")


# 7. 测试文件本身：验证修复后的测试用例不再访问 None.close


def test_test_file_fix_correct() -> None:
    section("验证 test_p3_idempotent_close.py 修复正确")

    test_file = ROOT / "tests" / "unit" / "test_p3_idempotent_close.py"
    src = test_file.read_text(encoding="utf-8")

    # 检查 test_first_close_calls_client_close 是否保存了 original_close
    assert_true(
        "original_close = store._client.close" in src and "original_close.assert_called_once()" in src,
        "test_first_close_calls_client_close 保存了 original_close 引用",
    )

    # 检查 test_close_sets_closed_even_on_exception 是否保存了 original_close
    assert_true(
        "original_close = store._client.close" in src
        and "original_close.side_effect = RuntimeError" in src
        and "original_close.call_count == 1" in src,
        "test_close_sets_closed_even_on_exception 保存了 original_close 引用",
    )

    # 确保不再有 store._client.close.assert_called_once() 这种危险调用
    # （这种调用在 _client 被 close() 置 None 后会抛 AttributeError）
    dangerous_pattern = "store._client.close.assert_called_once()"
    assert_true(dangerous_pattern not in src, f"测试文件中不再包含危险调用: {dangerous_pattern}")


# 主入口


def main() -> int:
    print(f"Python: {sys.version.split()[0]}")
    print(f"Root: {ROOT}")

    tests = [
        test_budget_manager_close_idempotent,
        test_cost_tracker_close_idempotent,
        test_rule_database_close_idempotent,
        test_wakeup_queue_close_idempotent,
        test_vector_store_close_idempotent,
        test_heartbeat_scheduler_static_contract,
        test_test_file_fix_correct,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  FAIL: {test.__name__}: {type(e).__name__}: {e}")

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed (total {len(tests)})")
    print(f"{'=' * 60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
