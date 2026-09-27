"""
审计日志归档模块 - 日志轮转、清空与归档备份。

本模块为 P1-5 重构从原 ``audit_log.py`` 拆分而来，提供 ``ArchiverMixin``：
- ``_compute_file_hash``：文件 SHA-256 指纹（用于归档完整性校验）
- ``_rotate_if_needed``：日志轮转（归档而非重写，保持 append-only 语义）
- ``clear_logs``：合规禁止清空（直接抛 RuntimeError）
- ``clear_logs_with_authorization``：授权清空（tar.gz 备份 + 重命名归档，不删除）

合规要求（FDA 21 CFR Part 11 / SOC 2 / ISO 27001）：
- 审计日志不可任意清空，必须授权
- 清空操作仅归档不删除
- 日志轮转采用重命名归档，保持 append-only 语义

H15 修复：``_rotate_if_needed`` 调整为 hash → 准备空文件 → replace 旧→归档 →
replace 空→audit.log → save_state 的顺序，确保任何步骤失败后系统都能继续工作或自愈。
H18 修复：``clear_logs_with_authorization`` 不用 exists() 预检，直接 try 操作避免 TOCTOU。

跨 mixin 调用：
- ``self._save_chain_state``：来自 ``ChainMixin``
- ``self._compute_entry_hash``：来自 ``ChainMixin``
- ``self._get_all_log_files``：来自 ``ReaderMixin``
- ``self._get_current_log_file``：来自 ``WriterMixin``
"""

import os
from typing import Any
from collections.abc import Callable

import json
import time
import tarfile
import logging
import hashlib
from pathlib import Path

from app.audit.chain import AuditLogEntry

# 保留原 audit_log 模块的 logger 名称，避免日志配置因重构失效
logger = logging.getLogger("app.audit.audit_log")


class ArchiverMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _compute_entry_hash: Callable[..., Any]
    _get_all_log_files: Callable[..., Any]
    _get_current_log_file: Callable[..., Any]
    _save_chain_state: Callable[..., Any]
    _archives: Any
    _chain_lock: Any
    _chain_seq: Any
    _chain_state_file: Any
    _last_hash: Any
    _log_root: Any
    max_entries: Any

    """审计日志归档与轮转 mixin。

    依赖 ``AuditLog`` 实例的以下属性（由 ``AuditLog.__init__`` 初始化）：
    ``_log_root`` / ``_chain_state_file`` / ``_chain_lock`` / ``_last_hash`` /
    ``_chain_seq`` / ``_archives`` / ``max_entries``。
    """

    def _compute_file_hash(self, file_path: Path) -> str:
        """计算文件的 SHA-256 哈希指纹（用于归档完整性校验）。"""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _rotate_if_needed(self, log_file: Path):
        """
        日志轮转：归档而非重写。

        当文件超过 max_entries 时，将旧日志归档到
        audit.log.archived.{timestamp}（重命名），新建空 audit.log。
        归档前计算并保存归档文件的哈希指纹到 chain_state.json 的 archives 字段。
        这样保持 append-only 语义，旧日志不可修改。

        H15 修复：原实现顺序为 hash → replace → save_state → touch，
        若 save_state 或 touch 在 replace 之后失败，会留下不一致状态：
        audit.log 不存在（touch 没执行）但归档已完成，下次 log_decision 会失败。
        新顺序为 hash → 准备空文件 → replace旧→归档 → replace空→audit.log
        → save_state（失败可恢复），确保任何步骤失败后系统都能继续工作或自愈。
        """
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            if len(lines) <= self.max_entries:
                return

            # 计算归档前的文件哈希指纹
            archive_hash = self._compute_file_hash(log_file)
            archive_timestamp = int(time.time())
            archive_path = log_file.with_name(f"audit.log.archived.{archive_timestamp}")

            # H15 修复：先创建新的空日志文件（临时名），确保归档后立即有可用日志。
            # 这样即使 os.replace(旧归档) 成功但后续步骤失败，
            # 下次 log_decision 仍能正常写入（旧日志已归档，新日志已就位）。
            new_log_tmp = log_file.with_name(f"{log_file.name}.rotating")
            # 以 "w" 模式打开确保文件为空（若残留上次失败的 .rotating 文件）
            with open(new_log_tmp, "w", encoding="utf-8") as f:
                pass  # 创建空文件

            # 原子重命名1：旧日志 归档文件（保持 append-only 语义，旧日志不可修改）
            os.replace(str(log_file), str(archive_path))

            # 原子重命名2：空临时文件 audit.log
            # 至此，audit.log 已是空文件，后续 log_decision 可正常写入
            os.replace(str(new_log_tmp), str(log_file))

            # 记录归档指纹到链状态并持久化
            # 即使此步失败，归档文件已存在且 chain_state.json 仍记录旧状态，
            # 下次启动时 verify_integrity 会扫描归档文件并可通过 chain_seq
            # 重建连续性校验，故此步失败不影响合规性，仅影响指纹缓存。
            self._archives[str(archive_path)] = {
                "hash": archive_hash,
                "entries": len(lines),
                "archived_at": archive_timestamp,
            }
            try:
                self._save_chain_state()
            except (OSError, IOError) as state_err:
                logger.warning(
                    "Archive completed but failed to persist chain state: %s. "
                    "Archive fingerprint will be recovered on next verify_integrity.",
                    state_err,
                    exc_info=True,
                )

            logger.info(
                "Audit log archived: %d entries moved to %s (sha256=%s)",
                len(lines),
                archive_path,
                archive_hash[:16],
            )

        except (OSError, IOError, PermissionError) as e:
            logger.error("Failed to rotate audit log: %s", e, exc_info=True)

    def clear_logs(self) -> int:
        """
        清空审计日志 - 默认禁止。

        合规要求（FDA 21 CFR Part 11 / SOC 2 / ISO 27001）规定审计日志
        不可任意清空。请使用 clear_logs_with_authorization() 并提供授权信息。
        """
        raise RuntimeError(
            "Audit logs cannot be cleared for compliance "
            "(FDA 21 CFR Part 11 / SOC 2). "
            "Use clear_logs_with_authorization() with proper authorization."
        )

    def clear_logs_with_authorization(
        self,
        authorizer_id: str,
        authorizer_role: str,
        reason: str,
    ) -> dict:
        """
        授权清空审计日志。

        要求：
        - 授权人 ID、角色、清空原因
        - 清空前将所有日志归档到 audit_logs_backup_{timestamp}.tar.gz
        - 记录一条 CLEAR_OPERATION 审计日志到新文件
        - 不真正删除，只归档

        返回: {"cleared_count": N, "backup_path": ..., "authorizer": ...}
        """
        if not authorizer_id or not isinstance(authorizer_id, str):
            raise ValueError("authorizer_id is required and must be a non-empty string")
        if not authorizer_role or not isinstance(authorizer_role, str):
            raise ValueError("authorizer_role is required and must be a non-empty string")
        if not reason or not isinstance(reason, str):
            raise ValueError("reason is required and must be a non-empty string")

        # 统计当前条数并准备归档文件列表
        log_files = self._get_all_log_files(include_archived=True)
        cleared_count = 0
        for log_file in log_files:
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    cleared_count += sum(1 for line in f if line.strip())
            except (OSError, IOError) as read_err:
                # 单个日志文件读取失败不阻塞归档流程，记录便于排查
                logger.debug("Skip unreadable log file %s: %s", log_file, read_err)

        # 创建 tar.gz 备份（不真正删除）
        backup_timestamp = int(time.time())
        backup_filename = f"audit_logs_backup_{backup_timestamp}.tar.gz"
        backup_path = self._log_root / backup_filename
        self._log_root.mkdir(parents=True, exist_ok=True)

        with tarfile.open(backup_path, "w:gz") as tar:
            for log_file in log_files:
                # H18 修复：TOCTOU — 不用 exists() 预检，直接 try tar.add
                try:
                    arcname = str(log_file.relative_to(self._log_root))
                    tar.add(log_file, arcname=arcname)
                except (OSError, IOError, ValueError) as add_err:
                    # 文件可能已被其他线程轮转/删除，跳过即可
                    logger.debug("Skip file during tar backup %s: %s", log_file, add_err)
            # 同时备份 chain_state.json（同样避免 TOCTOU）
            try:
                tar.add(
                    self._chain_state_file,
                    arcname="chain_state.json",
                )
            except (OSError, IOError) as cs_err:
                logger.debug("Skip chain_state.json during backup: %s", cs_err)

        # 归档（重命名）所有日志文件，不删除
        for log_file in log_files:
            # H18 修复：TOCTOU — 不用 exists() 预检，直接 try os.replace
            archive_name = f"{log_file.name}.cleared.{backup_timestamp}"
            archive_path = log_file.with_name(archive_name)
            try:
                os.replace(str(log_file), str(archive_path))
            except (OSError, IOError) as e:
                # 文件可能已被轮转或不存在，记录但不中断
                logger.debug(
                    "Skip archive during clear (file may not exist): %s: %s",
                    log_file,
                    e,
                )

        # 记录 CLEAR_OPERATION 审计日志到新文件（继续哈希链，不重置）
        clear_entry = AuditLogEntry(
            timestamp_ms=int(time.time() * 1000),
            ai_module="audit_system",
            ai_recommendation={"action": "clear_logs", "reason": reason},
            user_decision="auto_executed",
            final_execution={
                "authorizer_id": authorizer_id,
                "authorizer_role": authorizer_role,
                "backup_path": str(backup_path),
                "cleared_count": cleared_count,
            },
            operation_status="success",
            user_id=authorizer_id,
            username=authorizer_role,
            metadata={
                "clear_operation": True,
                "backup_timestamp": backup_timestamp,
            },
        )
        # 填充哈希链字段并写入（加锁保证 chain_seq 单调递增与哈希链连续）
        with self._chain_lock:
            clear_entry.chain_seq = self._chain_seq
            clear_entry.prev_hash = self._last_hash
            clear_entry.entry_hash = self._compute_entry_hash(clear_entry, self._last_hash)

            # 写入新日志文件
            new_log_file = self._get_current_log_file()
            with open(new_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(clear_entry.to_dict(), ensure_ascii=False) + "\n")

            # 更新链状态
            self._last_hash = clear_entry.entry_hash
            self._chain_seq += 1
            self._save_chain_state()

        logger.warning(
            "Audit logs cleared with authorization by %s (%s): %d entries archived to %s",
            authorizer_id,
            authorizer_role,
            cleared_count,
            backup_path,
        )

        return {
            "cleared_count": cleared_count,
            "backup_path": str(backup_path),
            "authorizer": f"{authorizer_id} ({authorizer_role})",
        }
