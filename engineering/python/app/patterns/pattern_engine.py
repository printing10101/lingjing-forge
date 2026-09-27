"""Pattern Recognition Engine — discovers workflow patterns, anti-patterns, and combination optimizations."""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.utils.sqlite_pool import get_sqlite_manager

logger = logging.getLogger(__name__)


@dataclass
class ExecutionRecord:
    task_id: str
    branch_id: str
    elements: dict[str, Any]
    conditions: dict[str, Any]
    metrics: dict[str, Any]
    success: bool
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "branch_id": self.branch_id,
            "elements": self.elements,
            "conditions": self.conditions,
            "metrics": self.metrics,
            "success": self.success,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionRecord":
        return cls(
            task_id=data["task_id"],
            branch_id=data["branch_id"],
            elements=data.get("elements", {}),
            conditions=data.get("conditions", {}),
            metrics=data.get("metrics", {}),
            success=data.get("success", True),
            created_at=data.get("created_at", time.time()),
        )


@dataclass
class Pattern:
    pattern_id: str
    pattern_type: str
    description: str
    elements: dict[str, Any]
    conditions: dict[str, Any]
    metrics: dict[str, Any]
    sample_size: int
    suggestion: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type,
            "description": self.description,
            "elements": self.elements,
            "conditions": self.conditions,
            "metrics": self.metrics,
            "sample_size": self.sample_size,
            "suggestion": self.suggestion,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pattern":
        return cls(**data)


class PatternEngine:
    """Detects workflow patterns, anti-patterns, and combination optimizations from execution data."""

    MIN_SAMPLES = 10

    def __init__(
        self,
        db_path: str = "data/templates/patterns.db",
    ):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._db: sqlite3.Connection
        self._executions: list[ExecutionRecord] = []
        self._patterns: list[Pattern] = []
        # 自动初始化数据库
        self.initialize()

    def initialize(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("patterns", db_path=self.db_path)
        self._db = self._pool.get_connection()
        assert self._db is not None, "pattern_engine 连接池返回空连接"
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS pattern_executions (
                task_id TEXT PRIMARY KEY,
                branch_id TEXT,
                elements TEXT,
                conditions TEXT,
                metrics TEXT,
                success INTEGER,
                created_at REAL
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS patterns (
                pattern_id TEXT PRIMARY KEY,
                pattern_type TEXT NOT NULL,
                description TEXT NOT NULL,
                elements TEXT,
                conditions TEXT,
                metrics TEXT,
                sample_size INTEGER,
                suggestion TEXT,
                created_at REAL
            )
        """)
        self._db.commit()
        self._load_data()
        logger.info("PatternEngine initialized: db=%s", self.db_path)

    def _load_data(self) -> None:
        cursor = self._db.execute("SELECT * FROM pattern_executions ORDER BY created_at")
        for row in cursor.fetchall():
            self._executions.append(
                ExecutionRecord(
                    task_id=row["task_id"],
                    branch_id=row["branch_id"],
                    elements=json.loads(row["elements"]),
                    conditions=json.loads(row["conditions"]),
                    metrics=json.loads(row["metrics"]),
                    success=bool(row["success"]),
                    created_at=row["created_at"],
                )
            )

        cursor = self._db.execute("SELECT * FROM patterns ORDER BY created_at")
        for row in cursor.fetchall():
            self._patterns.append(
                Pattern(
                    pattern_id=row["pattern_id"],
                    pattern_type=row["pattern_type"],
                    description=row["description"],
                    elements=json.loads(row["elements"]) if row["elements"] else {},
                    conditions=json.loads(row["conditions"]) if row["conditions"] else {},
                    metrics=json.loads(row["metrics"]) if row["metrics"] else {},
                    sample_size=row["sample_size"],
                    suggestion=row["suggestion"],
                    created_at=row["created_at"],
                )
            )

    def record_execution(
        self,
        task_id: str,
        branch_id: str,
        elements: dict[str, Any],
        conditions: dict[str, Any],
        metrics: dict[str, Any],
        success: bool,
    ) -> ExecutionRecord:
        with self._lock:
            record = ExecutionRecord(
                task_id=task_id,
                branch_id=branch_id,
                elements=elements,
                conditions=conditions,
                metrics=metrics,
                success=success,
            )
            self._executions.append(record)
            self._db.execute(
                """INSERT OR REPLACE INTO pattern_executions
                   (task_id, branch_id, elements, conditions, metrics, success, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    branch_id,
                    json.dumps(elements),
                    json.dumps(conditions),
                    json.dumps(metrics),
                    int(success),
                    record.created_at,
                ),
            )
            self._db.commit()
            return record

    def analyze_patterns(self, min_samples: int | None = None) -> list[Pattern]:
        if min_samples is None:
            min_samples = self.MIN_SAMPLES

        with self._lock:
            new_patterns = []
            new_patterns.extend(self._detect_workflow_patterns(min_samples))
            new_patterns.extend(self._detect_anti_patterns(min_samples))
            new_patterns.extend(self._detect_combination_patterns(min_samples))

            # 批量插入新发现的 pattern（避免 N+1 查询）
            patterns_to_insert = []
            existing_ids = {p.pattern_id for p in self._patterns}
            for p in new_patterns:
                if p.pattern_id not in existing_ids:
                    self._patterns.append(p)
                    patterns_to_insert.append(p)

            if patterns_to_insert:
                # 使用 executemany 批量插入
                self._db.executemany(
                    """INSERT OR REPLACE INTO patterns
                       (pattern_id, pattern_type, description, elements, conditions, metrics, sample_size, suggestion, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            p.pattern_id,
                            p.pattern_type,
                            p.description,
                            json.dumps(p.elements),
                            json.dumps(p.conditions),
                            json.dumps(p.metrics),
                            p.sample_size,
                            p.suggestion,
                            p.created_at,
                        )
                        for p in patterns_to_insert
                    ],
                )
            self._db.commit()
            return new_patterns

    def _detect_workflow_patterns(self, min_samples: int) -> list[Pattern]:
        groups: dict[str, list[ExecutionRecord]] = {}
        for exe in self._executions:
            key = json.dumps(exe.elements, sort_keys=True)
            groups.setdefault(key, []).append(exe)

        patterns = []
        for key, records in groups.items():
            if len(records) < min_samples:
                continue

            success_rate = sum(1 for r in records if r.success) / len(records)
            avg_time = sum(r.metrics.get("execution_time", 0) for r in records) / len(records)
            avg_cost = sum(r.metrics.get("resource_cost", 0) for r in records) / len(records)

            if success_rate >= 0.9 and avg_time > 0:
                elements = json.loads(key)
                pattern = Pattern(
                    pattern_id=f"wf_{uuid.uuid4().hex[:8]}",
                    pattern_type="workflow",
                    description=f"High-quality workflow: {', '.join(f'{k}={v}' for k, v in elements.items())}",
                    elements=elements,
                    conditions={},
                    metrics={
                        "success_rate": round(success_rate, 3),
                        "avg_execution_time": round(avg_time, 2),
                        "avg_resource_cost": round(avg_cost, 2),
                    },
                    sample_size=len(records),
                    suggestion="Consider using this workflow pattern for similar tasks",
                )
                patterns.append(pattern)

        return patterns

    def _detect_anti_patterns(self, min_samples: int) -> list[Pattern]:
        groups: dict[str, list[ExecutionRecord]] = {}
        for exe in self._executions:
            key = json.dumps(exe.elements, sort_keys=True)
            groups.setdefault(key, []).append(exe)

        patterns = []
        for key, records in groups.items():
            if len(records) < min_samples:
                continue

            error_rate = sum(1 for r in records if not r.success) / len(records)
            avg_retries = sum(r.metrics.get("retry_count", 0) for r in records) / len(records)
            avg_cost = sum(r.metrics.get("resource_cost", 0) for r in records) / len(records)

            is_anti = False
            reason = ""
            if error_rate > 0.3:
                is_anti = True
                reason = f"high error rate ({error_rate:.0%})"
            elif avg_retries > 2:
                is_anti = True
                reason = f"frequent retries (avg {avg_retries:.1f})"
            elif avg_cost > 0 and len(records) > min_samples:
                baseline_cost = sum(r.metrics.get("resource_cost", 0) for r in self._executions) / max(
                    len(self._executions), 1
                )
                if avg_cost > baseline_cost * 1.5:
                    is_anti = True
                    reason = f"resource waste ({avg_cost / baseline_cost:.1f}x baseline)"

            if is_anti:
                elements = json.loads(key)
                pattern = Pattern(
                    pattern_id=f"ap_{uuid.uuid4().hex[:8]}",
                    pattern_type="anti_pattern",
                    description=f"Anti-pattern detected: {reason}",
                    elements=elements,
                    conditions={},
                    metrics={
                        "error_rate": round(error_rate, 3),
                        "avg_retry_count": round(avg_retries, 2),
                        "avg_resource_cost": round(avg_cost, 2),
                    },
                    sample_size=len(records),
                    suggestion="Avoid this element combination. Consider adding constraint checking.",
                )
                patterns.append(pattern)

        return patterns

    def _detect_combination_patterns(self, min_samples: int) -> list[Pattern]:
        cond_groups: dict[str, list[ExecutionRecord]] = {}
        for exe in self._executions:
            key = json.dumps(exe.conditions, sort_keys=True)
            cond_groups.setdefault(key, []).append(exe)

        patterns = []
        for cond_key, records in cond_groups.items():
            if len(records) < min_samples:
                continue

            elem_groups: dict[str, list[ExecutionRecord]] = {}
            for r in records:
                ekey = json.dumps(r.elements, sort_keys=True)
                elem_groups.setdefault(ekey, []).append(r)

            for elem_key, elem_records in elem_groups.items():
                if len(elem_records) < min_samples:
                    continue

                success_rate = sum(1 for r in elem_records if r.success) / len(elem_records)
                baseline = sum(1 for r in records if r.success) / len(records)

                if success_rate > baseline + 0.1 and success_rate > 0.8:
                    conditions = json.loads(cond_key)
                    elements = json.loads(elem_key)
                    improvement = round((success_rate - baseline) * 100, 1)
                    pattern = Pattern(
                        pattern_id=f"cp_{uuid.uuid4().hex[:8]}",
                        pattern_type="combination",
                        description=f"Combination optimization: {', '.join(f'{k}={v}' for k, v in elements.items())} under {', '.join(f'{k}={v}' for k, v in conditions.items())}",
                        elements=elements,
                        conditions=conditions,
                        metrics={
                            "success_rate": round(success_rate, 3),
                            "baseline_rate": round(baseline, 3),
                            "improvement_pct": improvement,
                            "confidence": min(0.99, round(0.5 + len(elem_records) * 0.004, 2)),
                        },
                        sample_size=len(elem_records),
                        suggestion=f"Use this combination when conditions match: improvement {improvement}%",
                    )
                    patterns.append(pattern)

        return patterns

    def get_patterns(
        self,
        pattern_type: str | None = None,
        conditions: dict[str, Any] | None = None,
    ) -> list[Pattern]:
        with self._lock:
            patterns = self._patterns
            if pattern_type:
                patterns = [p for p in patterns if p.pattern_type == pattern_type]
            if conditions:
                patterns = [p for p in patterns if all(p.conditions.get(k) == v for k, v in conditions.items())]
            return sorted(patterns, key=lambda p: p.sample_size, reverse=True)

    def get_anti_patterns(self) -> list[Pattern]:
        return self.get_patterns(pattern_type="anti_pattern")

    def generate_suggestions(self, pattern_id: str) -> dict[str, Any] | None:
        with self._lock:
            pattern = next((p for p in self._patterns if p.pattern_id == pattern_id), None)
            if pattern is None:
                return None
            return {
                "pattern_id": pattern_id,
                "type": pattern.pattern_type,
                "description": pattern.description,
                "suggestion": pattern.suggestion,
                "confidence": pattern.metrics.get("confidence", 0),
                "sample_size": pattern.sample_size,
            }

    def close(self) -> None:
        """关闭数据库连接，归还连接到连接池"""
        if hasattr(self, "_db") and self._db:
            self._pool.return_connection(self._db)
            self._db = None  # type: ignore[assignment]
            logger.info("PatternEngine closed")


class _PatternEngineHolder:
    """Thread-safe lazy holder for the :class:`PatternEngine` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: PatternEngine | None = None

    def get(self) -> PatternEngine:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = PatternEngine()
                self._instance.initialize()
            return self._instance

    def init(self, db_path: str = "data/templates/patterns.db") -> PatternEngine:
        """强制重新创建实例（用于启动时指定 db_path 的场景）。"""
        with self._lock:
            if self._instance is not None:
                self._instance.close()
            self._instance = PatternEngine(db_path=db_path)
            self._instance.initialize()
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _PatternEngineHolder()


def get_pattern_engine() -> PatternEngine:
    """获取共享的 :class:`PatternEngine` 单例；首次访问时懒初始化。

    Returns:
        :class:`PatternEngine` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_pattern_engine)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


def init_pattern_engine(
    db_path: str = "data/templates/patterns.db",
) -> PatternEngine:
    """初始化模式引擎，行为与重构前完全一致。"""
    return _holder.init(db_path)
