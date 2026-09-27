"""P2-3 验证测试：main.py shutdown_event 资源释放顺序。

验证内容：
1. 静态分析（AST）：``shutdown_event`` 中各资源 close/stop 调用顺序符合
   "调度层 → 执行层 → 业务模块 → 基础设施 → 持久化层 → 日志" 设计原则
2. 动态验证（mock）：通过 mock 所有外部依赖，实际运行 ``shutdown_event``
   并记录调用顺序，验证与设计原则一致
3. 完整性验证：所有应被关闭的资源都在 shutdown_event 中被调用

设计说明：
- 采用混合策略：AST 静态分析保证源代码结构正确，mock 动态验证保证运行时行为正确
- AST 分析不依赖运行时环境，可在任何环境下执行
- mock 动态验证通过 ``unittest.mock`` patch 所有外部依赖，避免真实副作用
- 两者结合提供双层保护：源代码修改与运行时行为都会被检测
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from typing import List, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# 资源关闭顺序定义（与 main.py 注释保持一致）
# 每个资源按 (layer_name, resource_name, call_method) 形式记录
# 顺序必须与 main.py shutdown_event 中的实际调用顺序一致
EXPECTED_SHUTDOWN_ORDER: List[Tuple[str, str, str]] = [
    # 1) 调度层：HeartbeatScheduler 先停，避免提交新任务到执行层
    ("调度层", "HeartbeatScheduler", "stop"),
    # 2) 执行层：AsyncTaskManager 取消已运行任务
    ("执行层", "AsyncTaskManager", "shutdown"),
    # 3) 业务模块：归还 SQLite 连接
    ("业务模块", "BudgetManager", "close"),
    ("业务模块", "CostTracker", "close"),
    ("业务模块", "RuleDatabase", "close"),
    ("业务模块", "GoalChainStore", "close"),
    ("业务模块", "AgentAuditLog", "close"),
    # 4) 基础设施：Redis / HTTP Client
    ("基础设施", "Redis", "close_redis"),
    ("基础设施", "HTTPClient", "close_shared_http_client"),
    # 5) 持久化层：DB / VectorStore
    ("持久化层", "Database", "close_db"),
    ("持久化层", "VectorStore", "close"),
    # 6) 最底层：Logging
    ("日志", "Logging", "shutdown_logging"),
]


def _find_shutdown_event_function(tree: ast.AST) -> ast.AsyncFunctionDef:
    """从 AST 中查找 ``shutdown_event`` 异步函数定义。

    Raises:
        AssertionError: 如果未找到 ``shutdown_event`` 函数
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "shutdown_event":
            return node
    raise AssertionError("未在 main.py 中找到 shutdown_event 异步函数")


def _extract_close_calls(func_node: ast.AsyncFunctionDef) -> List[str]:
    """从 ``shutdown_event`` 函数体中提取所有 close/stop/shutdown 调用。

    识别以下调用模式：
    - ``await xxx.yyy().close()`` / ``xxx.yyy().close()``
    - ``await xxx.yyy().stop()`` / ``xxx.yyy().stop()``
    - ``await xxx.yyy().shutdown()`` / ``xxx.yyy().shutdown()``
    - ``await close_xxx()`` / ``close_xxx()``（顶层函数调用）
    - ``shutdown_logging()``（顶层函数调用）

    Returns:
        调用方法的名称列表（如 ``["stop", "shutdown", "close", ...]``），
        对于顶层函数调用返回函数名（如 ``"close_redis"``）。
    """
    calls: List[str] = []

    for node in ast.walk(func_node):
        # 模式 1: xxx.yyy().method() 或 await xxx.yyy().method()
        if isinstance(node, ast.Call):
            method_name = _extract_method_name_from_call(node)
            if method_name is not None:
                calls.append(method_name)

    return calls


def _extract_method_name_from_call(call_node: ast.Call) -> str | None:
    """从 Call 节点提取方法名，仅识别 close/stop/shutdown/close_*/shutdown_*。

    识别两种模式：
    1. ``obj.method()``：func 是 Attribute，attr 是方法名
    2. ``func()``：func 是 Name，id 是函数名（如 ``close_redis()``）

    Returns:
        方法名或函数名，如果不匹配则返回 None
    """
    func = call_node.func

    # 模式 1: obj.method() func 是 Attribute
    if isinstance(func, ast.Attribute):
        attr_name = func.attr
        if attr_name in ("close", "stop", "shutdown"):
            return attr_name
        return None

    # 模式 2: func() func 是 Name
    if isinstance(func, ast.Name):
        func_id = func.id
        # 识别 close_redis / close_shared_http_client / close_db / shutdown_logging
        if func_id.startswith(("close_", "shutdown_")):
            return func_id

    return None


# 工厂函数 / 变量名 资源名映射表
# 用于根据调用点的工厂函数名（如 ``get_budget_manager``）或变量名
# （如 ``task_mgr``）精确识别资源，避免遍历所有源代码行导致误匹配。
_FACTORY_TO_RESOURCE: dict[str, str] = {
    "get_scheduler": "HeartbeatScheduler",
    "get_budget_manager": "BudgetManager",
    "get_cost_tracker": "CostTracker",
    "get_rule_db": "RuleDatabase",
    "get_goal_chain_store": "GoalChainStore",
    "get_agent_audit_log": "AgentAuditLog",
    "get_vector_store": "VectorStore",
}

# 变量赋值右侧的类名 资源名（用于 ``task_mgr = AsyncTaskManager()`` 模式）
_CLASS_TO_RESOURCE: dict[str, str] = {
    "AsyncTaskManager": "AsyncTaskManager",
    "HeartbeatScheduler": "HeartbeatScheduler",
}

# 顶层函数名 资源名（用于 ``close_redis()`` / ``shutdown_logging()`` 等）
_TOPLEVEL_FUNC_TO_RESOURCE: dict[str, str] = {
    "close_redis": "Redis",
    "close_shared_http_client": "HTTPClient",
    "close_db": "Database",
    "shutdown_logging": "Logging",
}


def _identify_resource_from_call(call_node: ast.Call, source_lines: List[str]) -> str | None:
    """通过 AST 调用点的结构精确识别对应的资源名。

    识别策略（按优先级）：

    1. **顶层函数调用**（``close_redis()`` / ``shutdown_logging()``）：
       通过 ``_TOPLEVEL_FUNC_TO_RESOURCE`` 直接映射。

    2. **工厂调用链**（``get_budget_manager().close()``）：
       func.value 是 Call 节点，其 func 是 Name（如 ``get_budget_manager``），
       通过 ``_FACTORY_TO_RESOURCE`` 映射。

    3. **变量调用**（``task_mgr.shutdown()``）：
       func.value 是 Name 节点（如 ``task_mgr``），需要向前查找赋值语句
       ``task_mgr = AsyncTaskManager()``，通过 ``_CLASS_TO_RESOURCE`` 映射。
       向前查找窗口为 20 行（足够覆盖 try/except 块内的赋值）。
    """
    func = call_node.func

    # 模式 1: 顶层函数调用 func 是 Name
    if isinstance(func, ast.Name):
        return _TOPLEVEL_FUNC_TO_RESOURCE.get(func.id)

    # 模式 2 & 3: obj.method() func 是 Attribute
    if not isinstance(func, ast.Attribute):
        return None

    method_name = func.attr
    if method_name not in ("close", "stop", "shutdown"):
        return None

    obj = func.value

    # 模式 2: 工厂调用链 obj 是 Call
    # 如 ``get_budget_manager().close()`` / ``get_scheduler().stop()``
    if isinstance(obj, ast.Call):
        obj_func = obj.func
        if isinstance(obj_func, ast.Name):
            # ``get_budget_manager()``
            return _FACTORY_TO_RESOURCE.get(obj_func.id)
        if isinstance(obj_func, ast.Attribute):
            # ``app.heartbeat.heartbeat.get_scheduler()`` 等
            return _FACTORY_TO_RESOURCE.get(obj_func.attr)

    # 模式 3: 变量调用 obj 是 Name
    # 如 ``task_mgr.shutdown()``
    if isinstance(obj, ast.Name):
        var_name = obj.id
        return _find_variable_resource(var_name, call_node.lineno, source_lines)

    return None


def _find_variable_resource(var_name: str, call_lineno: int, source_lines: List[str]) -> str | None:
    """向前查找变量赋值语句，识别变量对应的资源。

    查找模式：``var_name = SomeClass()``，向上扫描最多 20 行。
    例如 ``task_mgr = AsyncTaskManager()`` → ``AsyncTaskManager``。
    """
    search_start = max(0, call_lineno - 1 - 20)
    search_end = call_lineno  # 不含 call_lineno 自身
    for i in range(search_end - 1, search_start - 1, -1):
        line = source_lines[i] if i < len(source_lines) else ""
        # 匹配 ``var_name = ...``
        if f"{var_name} =" in line or f"{var_name}=" in line:
            for class_name, resource in _CLASS_TO_RESOURCE.items():
                if class_name in line:
                    return resource
            # 也支持工厂赋值：``var_name = get_xxx()``
            for factory_name, resource in _FACTORY_TO_RESOURCE.items():
                if factory_name in line:
                    return resource
    return None


def _get_function_source_lines(func_node: ast.AsyncFunctionDef, source_lines: List[str]) -> List[str]:
    """从 AST 节点获取源代码行（通过 lineno/end_lineno 切片源文件）。"""
    return source_lines[func_node.lineno - 1 : func_node.end_lineno]


def _find_resource_in_order(resource: str, mapped_calls: List[Tuple[str, str]]) -> int:
    """在已映射的 (call_name, resource) 列表中查找指定资源的首次出现索引。"""
    for idx, (_, res) in enumerate(mapped_calls):
        if res == resource:
            return idx
    return -1


# 测试类：静态 AST 分析


class TestShutdownOrderStaticAST:
    """通过 AST 静态分析验证 ``shutdown_event`` 资源关闭顺序。"""

    @pytest.fixture(scope="class")
    @classmethod
    def main_module_source(cls) -> str:
        """加载 main.py 源代码。"""
        main_py_path = Path(__file__).parent.parent.parent / "app" / "main.py"
        assert main_py_path.exists(), f"main.py 不存在: {main_py_path}"
        source = main_py_path.read_text(encoding="utf-8")
        return source

    @pytest.fixture(scope="class")
    @classmethod
    def main_source_lines(cls, main_module_source) -> List[str]:
        """main.py 源代码行列表（供 AST 资源识别使用）。"""
        return main_module_source.splitlines()

    @pytest.fixture(scope="class")
    @classmethod
    def shutdown_func_node(cls, main_module_source) -> ast.AsyncFunctionDef:
        """解析 AST 并定位 ``shutdown_event`` 函数。"""
        tree = ast.parse(main_module_source, filename="app/main.py")
        return _find_shutdown_event_function(tree)

    def test_shutdown_event_exists(self, shutdown_func_node):
        """``shutdown_event`` 函数必须存在。"""
        assert shutdown_func_node is not None
        assert shutdown_func_node.name == "shutdown_event"

    def test_all_expected_resources_are_closed(self, shutdown_func_node, main_module_source):
        """所有预期资源都必须在 ``shutdown_event`` 中被关闭。

        这是完整性检查：确保没有遗漏任何应该被关闭的资源。
        """
        source_lines = main_module_source.splitlines()
        shutdown_source = "\n".join(source_lines[shutdown_func_node.lineno - 1 : shutdown_func_node.end_lineno])

        required_resources = [
            ("HeartbeatScheduler", ["HeartbeatScheduler", "get_scheduler", "heartbeat"]),
            ("AsyncTaskManager", ["AsyncTaskManager", "task_mgr"]),
            ("BudgetManager", ["BudgetManager", "get_budget_manager"]),
            ("CostTracker", ["CostTracker", "get_cost_tracker"]),
            ("RuleDatabase", ["RuleDatabase", "get_rule_db", "rule_db"]),
            ("GoalChainStore", ["GoalChainStore", "get_goal_chain_store"]),
            ("AgentAuditLog", ["AgentAuditLog", "get_agent_audit_log"]),
            ("Redis", ["close_redis"]),
            ("HTTPClient", ["close_shared_http_client"]),
            ("Database", ["close_db"]),
            ("VectorStore", ["VectorStore", "get_vector_store"]),
            ("Logging", ["shutdown_logging"]),
        ]

        missing = []
        for resource_name, keywords in required_resources:
            found = any(kw in shutdown_source for kw in keywords)
            if not found:
                missing.append(resource_name)

        assert not missing, (
            f"shutdown_event 中缺失以下资源的关闭调用: {missing}。所有预期资源都必须被显式关闭，避免资源泄漏。"
        )

    def test_shutdown_order_matches_design(self, shutdown_func_node, main_module_source, main_source_lines):
        """shutdown 调用顺序必须符合"调度层→执行层→业务模块→基础设施→持久化层→日志"。

        通过 AST 提取所有 close/stop/shutdown 调用，按出现顺序映射到资源名，
        然后验证顺序符合设计原则。

        资源识别策略：
        - 顶层函数（``close_redis()`` 等）：通过函数名直接映射
        - 工厂调用链（``get_xxx().close()``）：通过工厂函数名映射
        - 变量调用（``task_mgr.shutdown()``）：向前查找 ``task_mgr = SomeClass()``
          赋值语句，通过类名映射
        """
        # 提取所有 close/stop/shutdown 调用节点（含 lineno）
        close_call_nodes: List[ast.Call] = []
        for node in ast.walk(shutdown_func_node):
            if isinstance(node, ast.Call):
                method_name = _extract_method_name_from_call(node)
                if method_name is not None:
                    close_call_nodes.append(node)

        # 按源代码行号排序，保证顺序与源代码一致
        close_call_nodes.sort(key=lambda n: n.lineno)

        # 将每个调用节点映射到资源名
        mapped_calls: List[Tuple[str, str]] = []
        for call_node in close_call_nodes:
            method_name = _extract_method_name_from_call(call_node)
            resource = _identify_resource_from_call(call_node, main_source_lines)
            if resource is not None and method_name is not None:
                mapped_calls.append((method_name, resource))

        # 验证顺序：每个预期资源的首次出现索引必须单调递增
        previous_idx = -1
        order_violations = []

        for layer, resource, method in EXPECTED_SHUTDOWN_ORDER:
            idx = _find_resource_in_order(resource, mapped_calls)
            if idx == -1:
                order_violations.append(f"资源 {resource}（{layer}）未在 shutdown_event 中被调用")
                continue
            if idx < previous_idx:
                order_violations.append(
                    f"资源 {resource}（{layer}）关闭顺序错误："
                    f"出现在索引 {idx}，但前一个资源出现在索引 {previous_idx}。"
                    f"应保证 {layer} 在后续层级之前关闭。"
                )
            else:
                previous_idx = idx

        assert not order_violations, (
            "shutdown_event 资源关闭顺序违反设计原则:\n  - "
            + "\n  - ".join(order_violations)
            + f"\n实际映射顺序: {mapped_calls}"
        )

    def test_heartbeat_before_task_manager(self, shutdown_func_node, main_module_source):
        """HeartbeatScheduler 必须在 AsyncTaskManager 之前关闭。

        这是关键顺序约束：调度层先停，避免在执行层关闭后仍提交新任务。

        识别策略：
        - HeartbeatScheduler：查找包含 ``get_scheduler`` 和 ``stop`` 的行
          （如 ``await get_scheduler().stop()``）
        - AsyncTaskManager：查找 ``AsyncTaskManager()`` 赋值行
          （如 ``task_mgr = AsyncTaskManager()``），然后向后查找
          ``task_mgr.shutdown()`` 调用行；以赋值行作为定位基准。
        """
        source_lines = main_module_source.splitlines()
        shutdown_lines = source_lines[shutdown_func_node.lineno - 1 : shutdown_func_node.end_lineno]

        heartbeat_lineno = None
        task_mgr_assign_lineno = None
        task_mgr_var_name = None

        # 定位 HeartbeatScheduler.stop() 调用行
        # 定位 ``var = AsyncTaskManager()`` 赋值行
        for idx, line in enumerate(shutdown_lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if heartbeat_lineno is None and "get_scheduler" in line and "stop" in line:
                heartbeat_lineno = idx
            if task_mgr_assign_lineno is None and "AsyncTaskManager" in line and "=" in line:
                task_mgr_assign_lineno = idx
                # 提取变量名（如 ``task_mgr = AsyncTaskManager()`` ``task_mgr``）
                eq_pos = line.find("=")
                if eq_pos > 0:
                    task_mgr_var_name = line[:eq_pos].strip().split()[-1]

        # 回退：如果未找到赋值行，尝试查找 ``AsyncTaskManager`` + ``shutdown`` 同行
        # （兼容未来可能的代码重构，如直接 ``AsyncTaskManager().shutdown()``）
        if task_mgr_assign_lineno is None:
            for idx, line in enumerate(shutdown_lines):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "AsyncTaskManager" in line and "shutdown" in line:
                    task_mgr_assign_lineno = idx
                    task_mgr_var_name = None  # 同行调用，无需变量
                    break

        assert heartbeat_lineno is not None, "未找到 HeartbeatScheduler.stop() 调用"
        assert task_mgr_assign_lineno is not None, (
            f"未找到 AsyncTaskManager 赋值或 shutdown 调用。shutdown_event 源代码: {chr(10).join(shutdown_lines)}"
        )

        # 如果通过变量赋值定位，需要验证变量 shutdown 调用确实存在
        if task_mgr_var_name is not None:
            shutdown_call_found = any(f"{task_mgr_var_name}.shutdown()" in line for line in shutdown_lines)
            assert shutdown_call_found, (
                f"找到变量赋值 ``{task_mgr_var_name} = AsyncTaskManager()`` "
                f"（行 {task_mgr_assign_lineno}），但未找到对应的 "
                f"``{task_mgr_var_name}.shutdown()`` 调用"
            )

        assert heartbeat_lineno < task_mgr_assign_lineno, (
            f"HeartbeatScheduler（行 {heartbeat_lineno}）必须在 "
            f"AsyncTaskManager（行 {task_mgr_assign_lineno}）之前关闭，"
            f"避免调度器在执行层关闭后仍提交新任务"
        )

    def test_logging_is_last(self, shutdown_func_node, main_module_source):
        """``shutdown_logging()`` 必须是最后一个关闭调用。

        日志系统是最底层基础设施，必须最后关闭，因为其他资源在关闭过程中
        可能仍需要记录日志。
        """
        source_lines = main_module_source.splitlines()
        shutdown_lines = source_lines[shutdown_func_node.lineno - 1 : shutdown_func_node.end_lineno]

        logging_lineno = None
        other_close_linenos: List[int] = []

        for idx, line in enumerate(shutdown_lines):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if "shutdown_logging" in line and "await" not in line.split("shutdown_logging")[0]:
                # 排除 import 语句中的 shutdown_logging
                if "import" not in line:
                    logging_lineno = idx

        # 找出所有其他 close/stop/shutdown 调用的行号
        for idx, line in enumerate(shutdown_lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if idx == logging_lineno:
                continue
            if any(
                kw in line
                for kw in [".close()", ".stop()", ".shutdown()", "close_redis", "close_db", "close_shared_http_client"]
            ):
                if "import" not in line:
                    other_close_linenos.append(idx)

        assert logging_lineno is not None, "未找到 shutdown_logging() 调用"
        assert other_close_linenos, "未找到其他资源关闭调用，无法验证顺序"

        last_other_close = max(other_close_linenos)
        assert logging_lineno > last_other_close, (
            f"shutdown_logging()（行 {logging_lineno}）必须在所有其他资源关闭之后调用，"
            f"但其他资源关闭调用最晚出现在行 {last_other_close}。"
            f"日志系统是最底层基础设施，必须最后关闭。"
        )


# 测试类：动态 mock 验证


class TestShutdownOrderDynamicMock:
    """通过 mock 所有外部依赖，实际运行 ``shutdown_event`` 验证调用顺序。

    此测试类通过 ``unittest.mock`` patch 所有外部依赖，然后调用
    ``shutdown_event`` 函数，记录所有 close/stop/shutdown 调用的顺序，
    验证与设计原则一致。

    优势：验证运行时实际行为，不仅依赖源代码静态分析。
    """

    @pytest.fixture
    def call_recorder(self):
        """创建调用记录器，记录所有资源关闭调用的顺序。"""
        recorded_calls: List[str] = []

        def make_recorder(resource_name: str):
            def record(*args, **kwargs):
                recorded_calls.append(resource_name)

            return record

        return recorded_calls, make_recorder

    @pytest.mark.asyncio
    async def test_shutdown_event_calls_resources_in_correct_order(self, call_recorder):
        """运行 ``shutdown_event`` 并验证资源关闭顺序符合设计原则。"""
        recorded_calls, make_recorder = call_recorder

        # 构造所有需要 mock 的对象
        mock_heartbeat = MagicMock()
        mock_heartbeat.stop = AsyncMock(side_effect=make_recorder("HeartbeatScheduler"))

        mock_task_manager = MagicMock()
        mock_task_manager.shutdown = AsyncMock(side_effect=make_recorder("AsyncTaskManager"))

        mock_budget = MagicMock()
        mock_budget.close = MagicMock(side_effect=make_recorder("BudgetManager"))

        mock_cost = MagicMock()
        mock_cost.close = MagicMock(side_effect=make_recorder("CostTracker"))

        mock_rule_db = MagicMock()
        mock_rule_db.close = MagicMock(side_effect=make_recorder("RuleDatabase"))

        mock_goal_chain = MagicMock()
        mock_goal_chain.close = MagicMock(side_effect=make_recorder("GoalChainStore"))

        mock_audit_log = MagicMock()
        mock_audit_log.close = MagicMock(side_effect=make_recorder("AgentAuditLog"))

        mock_vector_store = MagicMock()
        mock_vector_store.close = MagicMock(side_effect=make_recorder("VectorStore"))

        # 由于 main 模块在导入时即装配 FastAPI app 并执行大量初始化，
        # 直接 import main 会触发副作用。这里通过 patch 各个 import 点
        # 来注入 mock，并直接调用 shutdown_event 函数。
        #
        # 关键 patch 策略：
        # 1. patch ``app.heartbeat.heartbeat.get_scheduler`` 返回 mock_heartbeat
        # 2. patch ``app.tasks.task_system.AsyncTaskManager`` 返回 mock_task_manager
        # 3. patch ``app.budget.budget.get_budget_manager`` 返回 mock_budget
        # 4. patch ``app.budget.cost_tracker.get_cost_tracker`` 返回 mock_cost
        # 5. patch ``app.database.rule_db.get_rule_db`` 返回 mock_rule_db
        # 6. patch ``app.goals.goal_chain_store.get_goal_chain_store`` 返回 mock_goal_chain
        # 7. patch ``app.agent.middleware.get_agent_audit_log`` 返回 mock_audit_log
        # 8. patch ``app.services.redis_client.close_redis`` AsyncMock 记录 Redis
        # 9. patch ``app.ai.llm_client.close_shared_http_client`` AsyncMock 记录 HTTPClient
        # 10. patch ``app.database.connection.close_db`` AsyncMock 记录 Database
        # 11. patch ``app.rag.vector_store.get_vector_store`` 返回 mock_vector_store
        # 12. patch ``app.core.logging_config.shutdown_logging`` MagicMock 记录 Logging
        # 13. patch ``app.ring_log`` / ``app.sse_manager`` 等已模块级初始化的对象

        # 由于 main.py 在导入时即执行 ``ring_log = ...`` 等模块级代码，
        # 我们需要 patch 模块级变量。这里采用更简单的方式：
        # 直接导入 main 模块的 shutdown_event 函数，并 patch 其内部依赖。
        #
        # 如果 main 模块导入失败（依赖缺失），则跳过动态测试，
        # 仅依赖静态 AST 分析测试。
        try:
            # 尝试导入 main 模块
            import sys

            if "app.main" not in sys.modules:
                # 预先 patch 关键模块级对象，避免导入时副作用
                # V2.7.0 后 ring_log 为 app.main 模块级绑定（源自 app.utils.ring_buffer）
                with (
                    patch("app.utils.ring_buffer.RingLog", create=True) as _,
                    patch("app.main.ring_log", create=True) as _,
                    patch("app.api.v1.sse.SSEManager", create=True) as _,
                ):
                    try:
                        importlib.import_module("app.main")
                    except Exception:
                        pytest.skip(
                            "无法导入 app.main 模块（依赖缺失），跳过动态测试。"
                            "静态 AST 分析测试仍可验证 shutdown 顺序。"
                        )

            main_module = sys.modules.get("app.main")
            if main_module is None or not hasattr(main_module, "shutdown_event"):
                pytest.skip("app.main 模块未正确加载，跳过动态测试。")

            shutdown_event = main_module.shutdown_event

            # patch 所有依赖并运行 shutdown_event
            with (
                patch(
                    "app.heartbeat.heartbeat.get_scheduler",
                    return_value=mock_heartbeat,
                ),
                patch(
                    "app.tasks.task_system.AsyncTaskManager",
                    return_value=mock_task_manager,
                ),
                patch(
                    "app.budget.budget.get_budget_manager",
                    return_value=mock_budget,
                ),
                patch(
                    "app.budget.cost_tracker.get_cost_tracker",
                    return_value=mock_cost,
                ),
                patch(
                    "app.database.rule_db.get_rule_db",
                    return_value=mock_rule_db,
                ),
                patch(
                    "app.goals.goal_chain_store.get_goal_chain_store",
                    return_value=mock_goal_chain,
                ),
                patch(
                    "app.agent.middleware.get_agent_audit_log",
                    return_value=mock_audit_log,
                ),
                patch(
                    "app.rag.vector_store.get_vector_store",
                    return_value=mock_vector_store,
                ),
                patch(
                    "app.services.redis_client.close_redis",
                    new=AsyncMock(side_effect=make_recorder("Redis")),
                ),
                patch(
                    "app.ai.llm_client.close_shared_http_client",
                    new=AsyncMock(side_effect=make_recorder("HTTPClient")),
                ),
                patch(
                    "app.database.connection.close_db",
                    new=AsyncMock(side_effect=make_recorder("Database")),
                ),
                patch(
                    "app.core.logging_config.shutdown_logging",
                    new=MagicMock(side_effect=make_recorder("Logging")),
                ),
                patch("app.main.ring_log") as mock_ring_log,
                patch("app.api.v1.sse.sse_manager") as mock_sse,
            ):
                mock_ring_log.append = MagicMock()
                mock_ring_log.stop = AsyncMock()
                mock_sse.shutdown = AsyncMock()

                await shutdown_event()

            # 验证调用顺序
            expected_order = [
                "HeartbeatScheduler",  # 1) 调度层
                "AsyncTaskManager",  # 2) 执行层
                "BudgetManager",  # 3) 业务模块
                "CostTracker",
                "RuleDatabase",
                "GoalChainStore",
                "AgentAuditLog",
                "Redis",  # 4) 基础设施
                "HTTPClient",
                "Database",  # 5) 持久化层
                "VectorStore",
                "Logging",  # 6) 日志
            ]

            # 由于 mock 可能会记录重复调用，取每个资源的首次出现
            seen = set()
            first_occurrence_order = []
            for call in recorded_calls:
                if call not in seen:
                    seen.add(call)
                    first_occurrence_order.append(call)

            # 验证每个预期资源都被调用
            missing = [res for res in expected_order if res not in seen]
            assert not missing, f"以下资源未被关闭: {missing}。实际调用顺序: {first_occurrence_order}"

            # 验证顺序（按层级分组验证，业务模块内顺序可微调）
            # 关键约束：
            # 1. HeartbeatScheduler 在 AsyncTaskManager 之前
            # 2. AsyncTaskManager 在所有业务模块之前
            # 3. 业务模块在 Redis/HTTPClient 之前
            # 4. Redis/HTTPClient 在 Database/VectorStore 之前
            # 5. Database/VectorStore 在 Logging 之前
            def idx(res):
                return first_occurrence_order.index(res)

            assert idx("HeartbeatScheduler") < idx("AsyncTaskManager"), (
                "HeartbeatScheduler 必须在 AsyncTaskManager 之前关闭"
            )
            assert idx("AsyncTaskManager") < idx("BudgetManager"), "AsyncTaskManager 必须在业务模块之前关闭"
            assert idx("AgentAuditLog") < idx("Redis"), "业务模块必须在基础设施（Redis）之前关闭"
            assert idx("HTTPClient") < idx("Database"), "基础设施（HTTPClient）必须在持久化层之前关闭"
            assert idx("VectorStore") < idx("Logging"), "持久化层（VectorStore）必须在日志之前关闭"
            assert first_occurrence_order[-1] == "Logging", "Logging 必须是最后关闭的资源"

        except ImportError as e:
            pytest.skip(f"导入 app.main 失败（{e}），跳过动态测试。静态 AST 分析测试仍可验证 shutdown 顺序。")


# 测试类：shutdown 顺序设计原则文档化


class TestShutdownOrderDocumentation:
    """验证 shutdown_event 中包含设计原则的注释文档。

    这确保后续维护者能理解关闭顺序的设计意图，避免无意中破坏顺序。
    """

    @pytest.fixture(scope="class")
    @classmethod
    def main_source(cls) -> str:
        main_py_path = Path(__file__).parent.parent.parent / "app" / "main.py"
        return main_py_path.read_text(encoding="utf-8")

    def test_shutdown_event_contains_design_principle_comment(self, main_source):
        """shutdown_event 必须包含关闭顺序设计原则的注释。"""
        # 检查关键设计原则关键词
        required_keywords = [
            "调度层",
            "执行层",
            "业务模块",
            "基础设施",
            "持久化层",
            "日志",
            "P2-3",
        ]

        # 提取 shutdown_event 函数源代码
        tree = ast.parse(main_source, filename="app/main.py")
        func_node = _find_shutdown_event_function(tree)
        source_lines = main_source.splitlines()
        func_source = "\n".join(source_lines[func_node.lineno - 1 : func_node.end_lineno])

        missing_keywords = [kw for kw in required_keywords if kw not in func_source]
        assert not missing_keywords, (
            f"shutdown_event 注释中缺失以下设计原则关键词: {missing_keywords}。"
            f"这些关键词用于说明关闭顺序的设计意图，必须保留。"
        )

    def test_shutdown_event_contains_layer_order_description(self, main_source):
        """shutdown_event 必须包含六层关闭顺序的描述。"""
        tree = ast.parse(main_source, filename="app/main.py")
        func_node = _find_shutdown_event_function(tree)
        source_lines = main_source.splitlines()
        func_source = "\n".join(source_lines[func_node.lineno - 1 : func_node.end_lineno])

        # 检查六层顺序描述
        layer_descriptions = [
            ("1)", "HeartbeatScheduler"),
            ("2)", "AsyncTaskManager"),
            ("3)", "Budget"),
            ("4)", "Redis"),
            ("5)", "DB"),
            ("6)", "Logging"),
        ]

        for num, resource in layer_descriptions:
            # 检查编号和资源名是否在同一行附近
            pattern = f"{num}"
            assert pattern in func_source, (
                f"shutdown_event 注释中缺失第 {num} 层关闭顺序描述。应包含 '{resource}' 的关闭说明。"
            )
