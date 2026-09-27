"""
审计日志模块 - 哈希链防篡改机制（re-export shim）。

本模块在 P1-5 重构后仅作为组合入口与公开 API 的 re-export shim，原 894 行
God class 已按职责拆分为 4 个子模块：

- ``app.audit.chain``：哈希链算法与共享数据结构（``ChainMixin`` / enums /
  ``AuditLogEntry`` / chain_state 持久化 / ``verify_integrity``）
- ``app.audit.writer``：日志写入（``WriterMixin`` / ``log_decision`` /
  ``log_security_event``）
- ``app.audit.reader``：查询与导出（``ReaderMixin`` / ``get_logs`` /
  ``search_logs`` / ``export_logs`` / ``get_statistics`` /
  ``MAX_AUDIT_EXPORT_LIMIT``）
- ``app.audit.archiver``：归档与轮转（``ArchiverMixin`` / ``_rotate_if_needed`` /
  ``clear_logs`` / ``clear_logs_with_authorization``）

向后兼容：所有原公开符号（``AuditLog`` / ``AuditLogEntry`` / ``UserDecision`` /
``OperationStatus`` / ``AIModule`` / ``MAX_AUDIT_EXPORT_LIMIT`` / ``get_audit_log``）
仍可从 ``app.audit.audit_log`` 导入，类与函数签名不变。

合规依据：
- FDA 21 CFR Part 11：电子记录与电子签名（要求审计追踪不可篡改、可追溯）
- SOC 2 CC7.3：日志完整性（要求系统日志受到完整性保护，防止未授权修改）
- ISO 27001 A.12.4：事件日志记录与保护（要求日志受到保护免遭篡改）

哈希链算法（实现见 ``chain.py``）：
- entry_hash = SHA-256(prev_hash + timestamp_ms + ai_module +
                        json.dumps(ai_recommendation, sort_keys=True) +
                        json.dumps(final_execution, sort_keys=True) +
                        operation_status)
- 初始 prev_hash = "GENESIS"
- chain_seq 从 0 开始单调递增
"""

import os
import sys
import logging
import threading
from pathlib import Path
from typing import Optional

# 从拆分子模块 re-export 公开符号（向后兼容）
from app.audit.chain import (
    ChainMixin,
    AuditLogEntry,
    UserDecision,
    OperationStatus,
    AIModule,
)
from app.audit.writer import WriterMixin
from app.audit.reader import ReaderMixin, MAX_AUDIT_EXPORT_LIMIT
from app.audit.archiver import ArchiverMixin

# 保留原 audit_log 模块的 logger 名称，避免日志配置因重构失效
logger = logging.getLogger("app.audit.audit_log")

__all__ = [
    "AuditLog",
    "AuditLogEntry",
    "UserDecision",
    "OperationStatus",
    "AIModule",
    "MAX_AUDIT_EXPORT_LIMIT",
    "get_audit_log",
]


class AuditLog(ChainMixin, WriterMixin, ReaderMixin, ArchiverMixin):
    """
    审计日志主类 - 通过多重继承组合哈希链 / 写入 / 查询 / 归档四类职责。

    P1-5 重构：本类原为 894 行 God class，已按职责拆分为 4 个 mixin
    （``ChainMixin`` / ``WriterMixin`` / ``ReaderMixin`` / ``ArchiverMixin``），
    本类仅保留 ``__init__`` 与共享状态的初始化，方法实现全部继承自各 mixin。

    状态属性（由 ``__init__`` 初始化，被各 mixin 共享）：
    - ``_log_root``：日志根目录
    - ``max_entries``：单文件最大条数（轮转阈值）
    - ``_chain_state_file``：chain_state.json 路径
    - ``_last_hash``：上一条 entry_hash（初始 "GENESIS"）
    - ``_chain_seq``：链序号，单调递增
    - ``_archives``：归档文件指纹缓存
    - ``_chain_lock``：哈希链并发保护 RLock
    """

    def __init__(self, log_dir: str | None = None, max_entries: int = 10000):
        if log_dir is None:
            _default_root = os.environ.get(
                "LNN_LOG_DIR",
                os.path.join(os.getcwd(), "logs"),
            )
            self._log_root = Path(_default_root)
        else:
            self._log_root = Path(log_dir)
        self.max_entries = max_entries

        # 哈希链状态管理（强制启用，不可禁用）
        self._chain_state_file = self._log_root / "chain_state.json"
        self._last_hash: str = "GENESIS"  # 上一条哈希，初始为 GENESIS
        self._chain_seq: int = 0  # 链序号，单调递增
        self._archives: dict[str, dict] = {}  # 归档文件指纹
        # 哈希链并发保护锁（使用 RLock 以允许持锁方法调用其他持锁方法，
        # 例如 log_decision 内部调用 _save_chain_state）
        self._chain_lock = threading.RLock()
        # 启动时加载链状态
        self._load_chain_state()


# 全局单例工厂（P0-17 修复）
# 多个模块直接 ``AuditLog()`` 创建各自实例会导致 _last_hash / _chain_seq
# 不同步，并发写入时哈希链会断裂。本工厂通过双重检查锁提供进程级单例，
# 所有安全审计调用方应优先使用 ``get_audit_log()`` 而非直接实例化。

_global_audit_log: Optional["AuditLog"] = None
_global_audit_log_lock = threading.Lock()


def get_audit_log() -> "AuditLog":
    """返回进程级 AuditLog 单例。

    P0-17 修复：登录/登出等安全事件审计必须通过本单例写入，避免多实例
    导致 chain_state.json 的 _last_hash / _chain_seq 不同步而破坏哈希链
    连续性。

    使用双重检查锁（double-checked locking）保证线程安全。
    """
    global _global_audit_log
    if _global_audit_log is None:
        with _global_audit_log_lock:
            if _global_audit_log is None:
                _global_audit_log = AuditLog()
    return _global_audit_log


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Audit log integrity verification")
    ap.add_argument("--log-dir", default=None)
    args = ap.parse_args()
    audit = AuditLog(log_dir=args.log_dir)
    ok, breaks = audit.verify_integrity()
    if ok:
        print("✓ Integrity OK: chain verified successfully")
    else:
        print(f"✗ Integrity BROKEN: {len(breaks)} break points")
        for b in breaks[:10]:
            print(f"  - {b}")
        sys.exit(1)
