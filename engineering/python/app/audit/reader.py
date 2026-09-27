"""
审计日志查询与导出模块 - 日志枚举、过滤、导出与统计。

本模块为 P1-5 重构从原 ``audit_log.py`` 拆分而来，提供 ``ReaderMixin``：
- ``_get_all_log_files``：枚举所有日志文件（可选含归档）
- ``get_logs``：按时间/模块/决策/用户等多维度过滤查询
- ``search_logs``：关键字搜索
- ``export_logs``：JSON / CSV 导出（CSV 注入防护）
- ``get_statistics``：聚合统计

CSV 注入防护：在以 =/+/-/@/\t/\n/\r 开头的字段值前加单引号前缀，防止下游
电子表格把字段解析为公式。

跨 mixin 调用：``verify_integrity``（ChainMixin）通过 ``self._get_all_log_files``
枚举日志文件，``clear_logs_with_authorization``（ArchiverMixin）同样依赖本方法。
"""

import json
import time
import logging
from pathlib import Path
from typing import Any

from app.audit.chain import AuditLogEntry
from app.config.limits import MAX_AUDIT_EXPORT_LIMIT

# ``MAX_AUDIT_EXPORT_LIMIT``（审计日志导出/统计的最大条数上限）由
# ``app.config.limits`` 集中管理，与 database/rule_db.MAX_EXPORT_LIMIT
# 共享同一基准值（100_000），避免一处调整、多处不同步。

# 保留原 audit_log 模块的 logger 名称，避免日志配置因重构失效
logger = logging.getLogger("app.audit.audit_log")


class ReaderMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _log_root: Any

    """审计日志查询与导出 mixin。

    依赖 ``AuditLog`` 实例的 ``_log_root`` 属性（由 ``AuditLog.__init__`` 初始化）。
    """

    def _get_all_log_files(self, include_archived: bool = False) -> list[Path]:
        files: list[Path] = []
        if self._log_root.exists():
            for date_dir in sorted(self._log_root.iterdir()):
                if date_dir.is_dir():
                    audit_file = date_dir / "audit.log"
                    if audit_file.exists():
                        files.append(audit_file)
                    if include_archived:
                        for archived in sorted(date_dir.glob("audit.log.archived.*")):
                            if archived.exists():
                                files.append(archived)
        return files

    def get_logs(
        self,
        start_time: int | None = None,
        end_time: int | None = None,
        ai_module: str | None = None,
        user_decision: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLogEntry]:
        logs: list[AuditLogEntry] = []

        for log_file in reversed(self._get_all_log_files()):
            if len(logs) >= offset + limit:
                break
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            entry = AuditLogEntry.from_dict(data)

                            if start_time and entry.timestamp_ms < start_time:
                                continue
                            if end_time and entry.timestamp_ms > end_time:
                                continue
                            if ai_module and entry.ai_module != ai_module:
                                continue
                            if user_decision and entry.user_decision != user_decision:
                                continue
                            if user_id and entry.user_id != user_id:
                                continue

                            logs.append(entry)
                        except json.JSONDecodeError as e:
                            logger.debug("跳过损坏的日志行: %s", e, exc_info=True)
                            continue
            except FileNotFoundError:
                logger.debug("日志文件不存在: %s", log_file, exc_info=True)
                continue

        logs.sort(key=lambda x: x.timestamp_ms, reverse=True)
        return logs[offset : offset + limit]

    def search_logs(self, keyword: str, limit: int = 50) -> list[AuditLogEntry]:
        # 修复：之前 search_logs 无条件拉取 10000 条日志；按调用方 limit 加合理
        # 窗口，避免在大日志量场景下触发内存峰值。
        if not keyword:
            return []
        if limit <= 0:
            return []
        scan_window = min(10000, max(limit * 5, 500))
        logs = self.get_logs(limit=scan_window)

        keyword_lower = keyword.lower()
        results: list[AuditLogEntry] = []
        for entry in logs:
            entry_str = json.dumps(entry.to_dict(), ensure_ascii=False).lower()
            if keyword_lower in entry_str:
                results.append(entry)
                if len(results) >= limit:
                    break

        return results

    def export_logs(
        self,
        format: str = "json",
        start_time: int | None = None,
        end_time: int | None = None,
        ai_module: str | None = None,
    ) -> str:
        logs = self.get_logs(
            start_time=start_time, end_time=end_time, ai_module=ai_module, limit=MAX_AUDIT_EXPORT_LIMIT
        )

        if format == "json":
            return json.dumps([entry.to_dict() for entry in logs], ensure_ascii=False, indent=2)
        elif format == "csv":
            if not logs:
                return ""

            headers = [
                "timestamp_ms",
                "ai_module",
                "user_decision",
                "operation_status",
                "user_id",
                "username",
                "confidence",
                "reasoning",
            ]
            lines = [",".join(headers)]

            def _csv_escape(value: str) -> str:
                # 修复：CSV 注入防护 - 在以 =/+/-/@/0x09/0x0A/0x0D 开头的值前加单引号前缀，
                # 防止下游电子表格把字段解析为公式。
                if value and value[0] in ("=", "+", "-", "@", "\t", "\n", "\r"):
                    value = "'" + value
                return '"' + value.replace('"', '""') + '"'

            for entry in logs:
                row = [
                    str(entry.timestamp_ms),
                    entry.ai_module or "",
                    entry.user_decision or "",
                    entry.operation_status or "",
                    entry.user_id or "",
                    entry.username or "",
                    str(entry.confidence if entry.confidence is not None else ""),
                    entry.reasoning or "",
                ]
                lines.append(",".join(_csv_escape(v) for v in row))

            return "\n".join(lines)
        else:
            raise ValueError(f"Unsupported export format: {format}")

    def get_statistics(self) -> dict:
        logs = self.get_logs(limit=MAX_AUDIT_EXPORT_LIMIT)

        stats: dict[str, Any] = {
            "total_entries": len(logs),
            "by_module": {},
            "by_decision": {},
            "by_status": {},
            "avg_confidence": 0.0,
            "recent_24h": 0,
        }

        if not logs:
            return stats

        confidence_values = []
        now_ms = int(time.time() * 1000)
        twenty_four_hours_ms = 24 * 60 * 60 * 1000

        for entry in logs:
            stats["by_module"][entry.ai_module] = stats["by_module"].get(entry.ai_module, 0) + 1
            stats["by_decision"][entry.user_decision] = stats["by_decision"].get(entry.user_decision, 0) + 1
            stats["by_status"][entry.operation_status] = stats["by_status"].get(entry.operation_status, 0) + 1

            if entry.confidence is not None:
                confidence_values.append(entry.confidence)

            if now_ms - entry.timestamp_ms <= twenty_four_hours_ms:
                stats["recent_24h"] += 1

        if confidence_values:
            stats["avg_confidence"] = sum(confidence_values) / len(confidence_values)

        return stats
