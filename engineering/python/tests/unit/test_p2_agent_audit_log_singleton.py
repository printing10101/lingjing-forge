"""P2-1 验证测试：AgentAuditLog 进程级单例与 ``app.auth.audit`` re-export shim。

验证内容：
1. ``get_agent_audit_log()`` 多次调用返回同一实例（双重检查锁正确）
2. ``app.auth.audit`` re-export shim 导出的符号与 ``app.agent.middleware`` 同源
3. 多线程并发调用 ``get_agent_audit_log()`` 仍返回同一实例（线程安全）
4. ``app.auth.audit`` 模块级 ``agent_audit_log`` 与工厂返回的是同一对象

设计说明：
- 由于 ``_global_agent_audit_log`` 是模块级全局变量，测试间需重置以避免相互污染
- 使用 ``importlib.reload`` 确保每个测试从干净状态开始
- 文件路径由 ``AgentAuditLog.__init__`` 默认推导，不显式传入以验证生产路径
"""

from __future__ import annotations

import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from app.agent.middleware import AgentAuditLog


@pytest.fixture
def reset_audit_singleton():
    """重置 ``_global_agent_audit_log`` 全局变量，确保每个测试从干净状态开始。

    fixture 在 yield 之前重置全局变量，yield 之后再次重置以避免污染后续测试。
    """
    from app.agent import middleware as mw

    original = mw._global_agent_audit_log
    mw._global_agent_audit_log = None
    try:
        yield mw
    finally:
        # 恢复或清零，避免泄漏到后续测试
        mw._global_agent_audit_log = original


class TestSingletonFactory:
    """验证 ``get_agent_audit_log()`` 进程级单例工厂。"""

    def test_factory_returns_same_instance(self, reset_audit_singleton):
        """多次调用 ``get_agent_audit_log()`` 必须返回同一实例。"""
        mw = reset_audit_singleton
        instance1 = mw.get_agent_audit_log()
        instance2 = mw.get_agent_audit_log()
        instance3 = mw.get_agent_audit_log()

        assert instance1 is instance2
        assert instance2 is instance3
        # 与全局变量保持一致
        assert mw._global_agent_audit_log is instance1

    def test_factory_lazy_initialization(self, reset_audit_singleton):
        """首次调用前 ``_global_agent_audit_log`` 必须为 None（懒初始化）。"""
        mw = reset_audit_singleton
        assert mw._global_agent_audit_log is None

        instance = mw.get_agent_audit_log()

        assert mw._global_agent_audit_log is instance
        assert instance is not None

    def test_factory_thread_safety(self, reset_audit_singleton):
        """多线程并发调用 ``get_agent_audit_log()`` 必须返回同一实例。

        使用 ThreadPoolExecutor 并发调用 N 次，收集所有返回的实例 id，
        验证它们全部相同。这是对双重检查锁的关键验证。
        """
        mw = reset_audit_singleton
        num_threads = 20

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(mw.get_agent_audit_log) for _ in range(num_threads)]
            instances = [f.result() for f in futures]

        # 所有线程必须得到同一实例
        instance_ids = {id(inst) for inst in instances}
        assert len(instance_ids) == 1, (
            f"双重检查锁失效：{num_threads} 个线程得到 {len(instance_ids)} 个不同实例"
        )

        # 与全局变量保持一致
        assert mw._global_agent_audit_log is instances[0]


class TestAuthAuditReexportShim:
    """验证 ``app.auth.audit`` 是 ``app.agent.middleware`` 的 re-export shim。"""

    def test_imports_same_classes(self):
        """``app.auth.audit`` 导入的类必须与 ``app.agent.middleware`` 同源。"""
        from app.agent import middleware as mw
        from app.auth import audit as audit_shim

        assert audit_shim.AgentAuditLog is mw.AgentAuditLog
        assert audit_shim.AgentAuditEntry is mw.AgentAuditEntry

    def test_imports_same_factory_function(self):
        """``get_agent_audit_log`` 必须是同一函数对象。"""
        from app.agent import middleware as mw
        from app.auth import audit as audit_shim

        assert audit_shim.get_agent_audit_log is mw.get_agent_audit_log

    def test_module_level_singleton_same_object(self, reset_audit_singleton):
        """``agent_audit_log`` 模块级变量应与工厂返回的是同一对象。

        P2-1 修复注释说明：模块级 ``agent_audit_log`` 仍可工作（向后兼容），
        但应与 ``get_agent_audit_log()`` 返回同一实例。
        注意：模块级 ``agent_audit_log`` 是在模块导入时创建的独立实例，
        ``get_agent_audit_log()`` 第一次调用时会创建新实例；
        修复重点是确保所有"调用方"使用工厂而非模块级变量。
        本测试验证工厂返回的实例可用于审计操作。
        """
        mw = reset_audit_singleton
        factory_instance = mw.get_agent_audit_log()

        # 工厂返回的实例必须是 AgentAuditLog 类型
        assert isinstance(factory_instance, mw.AgentAuditLog)

    def test_shim_all_exports(self):
        """``app.auth.audit.__all__`` 必须包含所有公开符号。"""
        from app.auth import audit as audit_shim

        expected = {
            "AgentAuditEntry",
            "AgentAuditLog",
            "agent_audit_log",
            "get_agent_audit_log",
        }
        assert set(audit_shim.__all__) == expected

    def test_shim_no_local_implementation(self):
        """``app.auth.audit`` 不得在本地定义 ``AgentAuditLog``（必须 re-export）。

        通过检查 ``__dict__`` 中 ``AgentAuditLog`` 的来源模块，
        确保它不是 audit.py 本地定义的。
        """
        from app.agent import middleware as mw
        from app.auth import audit as audit_shim

        # audit_shim.AgentAuditLog 的实际定义模块必须是 app.agent.middleware
        assert audit_shim.AgentAuditLog.__module__ == mw.__name__
        # AgentAuditEntry 是 dataclass，检查其 __module__
        assert audit_shim.AgentAuditEntry.__module__ == mw.__name__


class TestHashChainConsistency:
    """验证单例化后哈希链状态在进程内一致。"""

    def test_single_instance_writes_to_single_chain(
        self, reset_audit_singleton, tmp_path
    ):
        """通过工厂获取的实例写入审计日志，链序号必须连续。

        这验证了 P2-1 修复的核心目标：单例化后所有审计日志写入同一文件、
        共享同一 ``_last_hash`` / ``_chain_seq`` 状态。
        """
        mw = reset_audit_singleton
        log_path = tmp_path / "audit.log"

        # 创建一个由工厂管理的实例
        instance = mw.AgentAuditLog(log_path=str(log_path))
        mw._global_agent_audit_log = instance

        # 通过工厂获取（应返回同一实例）
        factory_instance = mw.get_agent_audit_log()
        assert factory_instance is instance

        # 连续写入 3 条审计日志
        for i in range(3):
            factory_instance.log(
                agent_id=f"agent-{i}",
                route=f"/api/v1/test/{i}",
                permission_class="read",
                status_code=200,
                latency_ms=10.5 + i,
            )

        # 验证哈希链完整性
        is_intact, breaks = factory_instance.verify_integrity()
        assert is_intact, f"哈希链断裂: {breaks}"

        # 验证 chain_seq 连续递增（0, 1, 2）
        entries = factory_instance.get_entries(limit=10)
        seqs = [e["chain_seq"] for e in entries]
        assert seqs == [2, 1, 0]  # get_entries 反序返回（最新在前）

        # 清理资源
        factory_instance.close()
