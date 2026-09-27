"""
审计日志哈希链算法模块 - SHA-256 哈希链防篡改核心。

合规依据：
- FDA 21 CFR Part 11：电子记录与电子签名（要求审计追踪不可篡改、可追溯）
- SOC 2 CC7.3：日志完整性（要求系统日志受到完整性保护，防止未授权修改）
- ISO 27001 A.12.4：事件日志记录与保护（要求日志受到保护免遭篡改）

本模块包含哈希链的核心算法与共享数据结构：
1. 每条日志包含 prev_hash（上一条哈希）和 entry_hash（本条哈希）
2. 哈希链状态持久化到 chain_state.json，启动时加载
3. 哈希链不可禁用，强制启用

哈希链算法：
- entry_hash = SHA-256(prev_hash + timestamp_ms + ai_module +
                        json.dumps(ai_recommendation, sort_keys=True) +
                        json.dumps(final_execution, sort_keys=True) +
                        operation_status)
- 初始 prev_hash = "GENESIS"
- chain_seq 从 0 开始单调递增

注：本模块为 P1-5 重构从原 ``audit_log.py`` 拆分而来。``ChainMixin`` 设计为与
``WriterMixin`` / ``ReaderMixin`` / ``ArchiverMixin`` 组合使用，共享 ``AuditLog``
实例的状态（``_last_hash`` / ``_chain_seq`` / ``_chain_lock`` 等）。跨 mixin 的
``self.`` 调用（如 ``self._get_all_log_files`` / ``self._compute_file_hash``）
在组合后通过 MRO 正常解析。
"""

import os
import json
import time
import logging
import hashlib
from pathlib import Path
from typing import Any
from collections.abc import Callable
from dataclasses import dataclass, asdict
from enum import Enum

# 保留原 audit_log 模块的 logger 名称，避免日志配置因重构失效
logger = logging.getLogger("app.audit.audit_log")


class UserDecision(Enum):
    ACCEPT = "accept"
    MODIFY = "modify"
    REJECT = "reject"
    AUTO_EXECUTED = "auto_executed"


class OperationStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PENDING = "pending"


class AIModule(Enum):
    LNN_PREDICT = "lnn_predict"
    LNN_TRAIN = "lnn_train"
    PROCESS_OPTIMIZE = "process_optimize"
    TOOL_WEAR_ANALYZE = "tool_wear_analyze"
    CAD_GENERATE = "cad_generate"
    # ADR-021：Dreaming 离线反思机制的反思决策写入哈希链，
    # 用于审计追踪 Memory Store 的去重/过时更新/规则合成等关键操作。
    DREAMING = "dreaming"


@dataclass
class AuditLogEntry:
    timestamp_ms: int
    ai_module: str
    ai_recommendation: dict
    user_decision: str
    final_execution: dict
    operation_status: str
    input_parameters: dict | None = None
    user_id: str | None = None
    username: str | None = None
    confidence: float | None = None
    reasoning: str | None = None
    user_modifications: dict | None = None
    metadata: dict | None = None
    # 哈希链字段（强制启用，不可禁用，无配置开关）
    prev_hash: str | None = None  # 上一条目的哈希
    entry_hash: str | None = None  # 本条目的哈希
    chain_seq: int = 0  # 链内序号，单调递增

    def __post_init__(self):
        if self.input_parameters is None:
            object.__setattr__(self, "input_parameters", {})

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditLogEntry":
        return cls(
            timestamp_ms=int(data.get("timestamp_ms") or 0),
            ai_module=str(data.get("ai_module") or ""),
            ai_recommendation=data.get("ai_recommendation") or {},
            user_decision=str(data.get("user_decision") or ""),
            final_execution=data.get("final_execution") or {},
            operation_status=str(data.get("operation_status") or ""),
            input_parameters=data.get("input_parameters", {}),
            user_id=data.get("user_id"),
            username=data.get("username"),
            confidence=data.get("confidence"),
            reasoning=data.get("reasoning"),
            user_modifications=data.get("user_modifications"),
            metadata=data.get("metadata"),
            prev_hash=data.get("prev_hash"),
            entry_hash=data.get("entry_hash"),
            chain_seq=data.get("chain_seq", 0),
        )


class ChainMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _chain_lock: Any
    _chain_state_file: Any
    _log_root: Any
    _chain_seq: Any
    _last_hash: Any
    _archives: Any
    _get_all_log_files: Callable[..., Any]

    """哈希链算法 mixin。

    提供 chain_state.json 的加载/持久化、条目哈希计算与整链完整性校验。
    依赖 ``AuditLog`` 实例的以下属性（由 ``AuditLog.__init__`` 初始化）：
    ``_log_root`` / ``_chain_state_file`` / ``_last_hash`` / ``_chain_seq`` /
    ``_archives`` / ``_chain_lock``。

    完整性校验通过组合 ``ReaderMixin._get_all_log_files`` 与本类的
    ``_compute_entry_hash`` 实现；调用方需保证 ``AuditLog`` 同时组合了
    ``ReaderMixin``。
    """

    # 哈希链管理

    def _load_chain_state(self) -> None:
        """启动时从 chain_state.json 加载 _last_hash 和 _chain_seq。"""
        with self._chain_lock:
            if not self._chain_state_file.exists():
                return
            try:
                with open(self._chain_state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self._last_hash = state.get("last_hash", "GENESIS")
                self._chain_seq = int(state.get("chain_seq", 0))
                self._archives = state.get("archives", {})
            except (OSError, IOError, json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning(
                    "Failed to load chain state from %s, starting fresh: %s",
                    self._chain_state_file,
                    e,
                )
                self._last_hash = "GENESIS"
                self._chain_seq = 0
                self._archives = {}

    def _save_chain_state(self) -> None:
        """持久化链状态（原子替换：写临时文件后 os.replace，防止中间状态损坏）。"""
        with self._chain_lock:
            self._log_root.mkdir(parents=True, exist_ok=True)
            state = {
                "last_hash": self._last_hash,
                "chain_seq": self._chain_seq,
                "archives": self._archives,
                "updated_at": int(time.time() * 1000),
            }
            tmp_file = self._chain_state_file.with_suffix(".json.tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            # 原子替换：os.replace 在同一文件系统上是原子的
            os.replace(str(tmp_file), str(self._chain_state_file))

    def _compute_entry_hash(self, entry: AuditLogEntry, prev_hash: str) -> str:
        """
        计算条目的 SHA-256 哈希。

        哈希覆盖关键字段（排除 entry_hash 自身以避免循环）：
        prev_hash + timestamp_ms + ai_module +
        json.dumps(ai_recommendation, sort_keys=True) +
        json.dumps(final_execution, sort_keys=True) +
        operation_status
        """
        payload = (
            str(prev_hash)
            + str(entry.timestamp_ms)
            + str(entry.ai_module)
            + json.dumps(entry.ai_recommendation, sort_keys=True, ensure_ascii=False)
            + json.dumps(entry.final_execution, sort_keys=True, ensure_ascii=False)
            + str(entry.operation_status)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def verify_integrity(self) -> tuple[bool, list[str]]:
        """
        验证整个日志链的完整性。

        - 遍历所有日志文件（含归档），按 chain_seq 排序
        - 重新计算每条哈希，与存储的 entry_hash 比对
        - 检查 prev_hash 链接是否连续
        - 返回 (是否完整, 破坏点列表)
        """
        breaks: list[str] = []
        entries: list[tuple[int, Path, int, dict]] = []

        with self._chain_lock:
            for log_file in self._get_all_log_files(include_archived=True):
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        for line_no, line in enumerate(f, 1):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError:
                                breaks.append(f"{log_file}:{line_no}: invalid JSON (line skipped)")
                                continue
                            seq = data.get("chain_seq")
                            if seq is None:
                                breaks.append(f"{log_file}:{line_no}: missing chain_seq field")
                                continue
                            entries.append((int(seq), log_file, line_no, data))
                except (OSError, IOError) as e:
                    breaks.append(f"{log_file}: read error: {e}")

            # 按 chain_seq 排序
            entries.sort(key=lambda x: x[0])

            expected_prev = "GENESIS"
            expected_seq = 0

            for seq, log_file, line_no, data in entries:
                # 检查 chain_seq 连续性
                if seq != expected_seq:
                    breaks.append(f"{log_file}:{line_no}: chain_seq gap, expected {expected_seq}, got {seq}")
                # 检查 prev_hash 链接
                stored_prev = data.get("prev_hash")
                if stored_prev != expected_prev:
                    breaks.append(
                        f"{log_file}:{line_no}: prev_hash mismatch, "
                        f"expected {str(expected_prev)[:16]}, "
                        f"got {str(stored_prev)[:16]}"
                    )
                # 重新计算 entry_hash 并比对
                stored_hash = data.get("entry_hash")
                try:
                    entry = AuditLogEntry.from_dict(data)
                    recomputed = self._compute_entry_hash(entry, stored_prev or "")
                    if recomputed != stored_hash:
                        breaks.append(
                            f"{log_file}:{line_no}: entry_hash mismatch, "
                            f"stored {str(stored_hash)[:16]}, "
                            f"recomputed {recomputed[:16]}"
                        )
                except Exception as e:
                    breaks.append(f"{log_file}:{line_no}: failed to recompute hash: {e}")
                # 更新期望值为当前条目的 entry_hash
                if stored_hash:
                    expected_prev = stored_hash
                expected_seq = seq + 1

        return (len(breaks) == 0, breaks)
