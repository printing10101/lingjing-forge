"""Template Evolution Core — data-driven template evolution with multi-dimensional triggers."""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

from app.utils.sqlite_pool import get_sqlite_manager

logger = logging.getLogger(__name__)


@dataclass
class EvolutionSuggestion:
    suggestion_id: str
    trigger_type: str
    description: str
    data_evidence: dict[str, Any]
    proposed_change: dict[str, Any]
    confidence: float
    created_at: float = field(default_factory=time.time)
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "trigger_type": self.trigger_type,
            "description": self.description,
            "data_evidence": self.data_evidence,
            "proposed_change": self.proposed_change,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvolutionSuggestion":
        return cls(**data)


@dataclass
class EvolutionTrigger:
    trigger_type: str
    condition: Callable[[dict[str, Any]], bool]
    action: Callable[[dict[str, Any]], EvolutionSuggestion | None]
    cooldown_hours: int = 24
    last_triggered: float = 0.0

    def can_trigger(self) -> bool:
        if self.last_triggered == 0:
            return True
        elapsed = time.time() - self.last_triggered
        return elapsed >= self.cooldown_hours * 3600


class TemplateEvolutionEngine:
    """Manages template evolution: trigger evaluation, suggestion creation, and application."""

    def __init__(
        self,
        db_path: str = "data/templates/evolution.db",
        log_dir: str = "data/templates/evolution_log",
    ):
        self.db_path = db_path
        self.log_dir = log_dir
        self._lock = threading.RLock()
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("template_evolution", db_path=self.db_path)
        self._db: sqlite3.Connection
        self._triggers: dict[str, EvolutionTrigger] = {}
        self._suggestions: list[EvolutionSuggestion] = []
        self._metrics_data: dict[str, Any] = {}

    def initialize(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)

        self._db = self._pool.get_connection()
        assert self._db is not None, "template_evolution 连接池返回空连接"
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS evolution_suggestions (
                suggestion_id TEXT PRIMARY KEY,
                trigger_type TEXT NOT NULL,
                description TEXT,
                data_evidence TEXT,
                proposed_change TEXT,
                confidence REAL,
                created_at REAL,
                status TEXT
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS evolution_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id TEXT,
                suggestion_id TEXT,
                action TEXT,
                details TEXT,
                created_at REAL
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS evolution_metrics (
                metric_name TEXT PRIMARY KEY,
                value REAL,
                updated_at REAL
            )
        """)
        self._db.commit()
        self._load_data()
        self._register_default_triggers()
        logger.info("TemplateEvolutionEngine initialized: db=%s", self.db_path)

    def _load_data(self) -> None:
        cursor = self._db.execute("SELECT * FROM evolution_suggestions ORDER BY created_at")
        for row in cursor.fetchall():
            self._suggestions.append(
                EvolutionSuggestion(
                    suggestion_id=row["suggestion_id"],
                    trigger_type=row["trigger_type"],
                    description=row["description"] or "",
                    data_evidence=json.loads(row["data_evidence"]) if row["data_evidence"] else {},
                    proposed_change=json.loads(row["proposed_change"]) if row["proposed_change"] else {},
                    confidence=row["confidence"],
                    created_at=row["created_at"],
                    status=row["status"],
                )
            )

        cursor = self._db.execute("SELECT * FROM evolution_metrics")
        for row in cursor.fetchall():
            self._metrics_data[row["metric_name"]] = {
                "value": row["value"],
                "updated_at": row["updated_at"],
            }

    def _register_default_triggers(self) -> None:
        self.register_trigger(
            trigger_type="skill",
            condition=lambda m: m.get("error_count_same_type", 0) >= 3,
            action=lambda m: self._create_skill_suggestion(m),
            cooldown_hours=48,
        )
        self.register_trigger(
            trigger_type="model_config",
            condition=lambda m: m.get("ab_test_winner", None) is not None,
            action=lambda m: self._create_model_config_suggestion(m),
            cooldown_hours=72,
        )
        self.register_trigger(
            trigger_type="approval_strategy",
            condition=lambda m: m.get("false_positive_rate", 0) > 0.20,
            action=lambda m: self._create_approval_suggestion(m),
            cooldown_hours=168,
        )
        self.register_trigger(
            trigger_type="heartbeat_routine",
            condition=lambda m: m.get("gpu_utilization_avg_7d", 100) < 30,
            action=lambda m: self._create_heartbeat_suggestion(m),
            cooldown_hours=168,
        )
        self.register_trigger(
            trigger_type="budget_strategy",
            condition=lambda m: m.get("overspend_rate", 0) > 0.20 or m.get("resource_waste_rate", 0) > 0.20,
            action=lambda m: self._create_budget_suggestion(m),
            cooldown_hours=168,
        )

    def _create_skill_suggestion(self, metrics: dict[str, Any]) -> EvolutionSuggestion:
        return EvolutionSuggestion(
            suggestion_id=f"ev_{uuid.uuid4().hex[:8]}",
            trigger_type="skill",
            description=f"Error type '{metrics.get('error_type', 'unknown')}' appeared {metrics.get('error_count_same_type', 0)} times. Consider adding skill documentation.",
            data_evidence=metrics,
            proposed_change={
                "action": "add_skill_doc",
                "error_type": metrics.get("error_type"),
            },
            confidence=min(0.95, 0.5 + metrics.get("error_count_same_type", 0) * 0.1),
        )

    def _create_model_config_suggestion(self, metrics: dict[str, Any]) -> EvolutionSuggestion:
        winner = metrics.get("ab_test_winner", {})
        return EvolutionSuggestion(
            suggestion_id=f"ev_{uuid.uuid4().hex[:8]}",
            trigger_type="model_config",
            description=f"A/B test winner: {winner.get('config_name', 'unknown')} with {winner.get('improvement', 0):.1f}% improvement.",
            data_evidence=metrics,
            proposed_change={"action": "update_model_config", "config": winner},
            confidence=metrics.get("confidence", 0.95),
        )

    def _create_approval_suggestion(self, metrics: dict[str, Any]) -> EvolutionSuggestion:
        fpr = metrics.get("false_positive_rate", 0)
        return EvolutionSuggestion(
            suggestion_id=f"ev_{uuid.uuid4().hex[:8]}",
            trigger_type="approval_strategy",
            description=f"False positive rate is {fpr:.0%}. Adjust risk threshold to reduce noise.",
            data_evidence=metrics,
            proposed_change={
                "action": "adjust_risk_threshold",
                "new_threshold": fpr * 0.8,
            },
            confidence=min(0.9, 0.5 + fpr * 0.5),
        )

    def _create_heartbeat_suggestion(self, metrics: dict[str, Any]) -> EvolutionSuggestion:
        gpu = metrics.get("gpu_utilization_avg_7d", 0)
        return EvolutionSuggestion(
            suggestion_id=f"ev_{uuid.uuid4().hex[:8]}",
            trigger_type="heartbeat_routine",
            description=f"GPU utilization at {gpu:.0%} for 7 days. Optimize scheduling frequency.",
            data_evidence=metrics,
            proposed_change={
                "action": "optimize_heartbeat_frequency",
                "current_utilization": gpu,
            },
            confidence=0.85,
        )

    def _create_budget_suggestion(self, metrics: dict[str, Any]) -> EvolutionSuggestion:
        overspend = metrics.get("overspend_rate", 0)
        waste = metrics.get("resource_waste_rate", 0)
        return EvolutionSuggestion(
            suggestion_id=f"ev_{uuid.uuid4().hex[:8]}",
            trigger_type="budget_strategy",
            description=f"Overspend rate: {overspend:.0%}, resource waste: {waste:.0%}. Generate quota adjustment.",
            data_evidence=metrics,
            proposed_change={
                "action": "adjust_quota",
                "overspend_rate": overspend,
                "waste_rate": waste,
            },
            confidence=min(0.9, 0.5 + max(overspend, waste) * 0.5),
        )

    def register_trigger(
        self,
        trigger_type: str,
        condition: Callable[[dict[str, Any]], bool],
        action: Callable[[dict[str, Any]], EvolutionSuggestion | None],
        cooldown_hours: int = 24,
    ) -> None:
        with self._lock:
            self._triggers[trigger_type] = EvolutionTrigger(
                trigger_type=trigger_type,
                condition=condition,
                action=action,
                cooldown_hours=cooldown_hours,
            )
            logger.info(
                "Trigger registered: type=%s, cooldown=%dh",
                trigger_type,
                cooldown_hours,
            )

    def update_metrics(self, metrics: dict[str, Any]) -> None:
        with self._lock:
            for key, value in metrics.items():
                stored_value = value if isinstance(value, (int, float)) else json.dumps(value)
                self._metrics_data[key] = {"value": value, "updated_at": time.time()}
                self._db.execute(
                    """INSERT OR REPLACE INTO evolution_metrics (metric_name, value, updated_at)
                       VALUES (?, ?, ?)""",
                    (key, stored_value, time.time()),
                )
            self._db.commit()

    def evaluate_triggers(self) -> list[EvolutionSuggestion]:
        with self._lock:
            flattened = {
                k: v["value"] if isinstance(v, dict) and "value" in v else v for k, v in self._metrics_data.items()
            }
            new_suggestions = []
            for trigger_type, trigger in self._triggers.items():
                if not trigger.can_trigger():
                    continue
                if not trigger.condition(flattened):
                    continue

                suggestion = trigger.action(flattened)
                if suggestion:
                    self._suggestions.append(suggestion)
                    trigger.last_triggered = time.time()

                    self._db.execute(
                        """INSERT OR REPLACE INTO evolution_suggestions
                           (suggestion_id, trigger_type, description, data_evidence, proposed_change, confidence, created_at, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            suggestion.suggestion_id,
                            suggestion.trigger_type,
                            suggestion.description,
                            json.dumps(suggestion.data_evidence),
                            json.dumps(suggestion.proposed_change),
                            suggestion.confidence,
                            suggestion.created_at,
                            suggestion.status,
                        ),
                    )
                    self._db.commit()
                    new_suggestions.append(suggestion)
                    logger.info(
                        "Trigger fired: type=%s, suggestion=%s",
                        trigger_type,
                        suggestion.suggestion_id,
                    )

            return new_suggestions

    def create_suggestion(
        self,
        trigger_type: str,
        evidence: dict[str, Any],
        proposed_change: dict[str, Any],
    ) -> EvolutionSuggestion:
        with self._lock:
            suggestion = EvolutionSuggestion(
                suggestion_id=f"ev_{uuid.uuid4().hex[:8]}",
                trigger_type=trigger_type,
                description=evidence.get("description", ""),
                data_evidence=evidence,
                proposed_change=proposed_change,
                confidence=evidence.get("confidence", 0.8),
            )
            self._suggestions.append(suggestion)
            self._db.execute(
                """INSERT OR REPLACE INTO evolution_suggestions
                   (suggestion_id, trigger_type, description, data_evidence, proposed_change, confidence, created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    suggestion.suggestion_id,
                    suggestion.trigger_type,
                    suggestion.description,
                    json.dumps(suggestion.data_evidence),
                    json.dumps(suggestion.proposed_change),
                    suggestion.confidence,
                    suggestion.created_at,
                    suggestion.status,
                ),
            )
            self._db.commit()
            return suggestion

    def apply_suggestion(self, suggestion_id: str, branch_id: str) -> EvolutionSuggestion | None:
        with self._lock:
            suggestion = next((s for s in self._suggestions if s.suggestion_id == suggestion_id), None)
            if suggestion is None:
                return None

            suggestion.status = "applied"
            self._db.execute(
                "UPDATE evolution_suggestions SET status = ? WHERE suggestion_id = ?",
                ("applied", suggestion_id),
            )
            self._db.execute(
                """INSERT INTO evolution_history (branch_id, suggestion_id, action, details, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    branch_id,
                    suggestion_id,
                    "applied",
                    json.dumps(suggestion.proposed_change),
                    time.time(),
                ),
            )
            self._db.commit()
            logger.info("Suggestion applied: id=%s, branch=%s", suggestion_id, branch_id)
            return suggestion

    def list_suggestions(self, status_filter: str | None = None) -> list[EvolutionSuggestion]:
        with self._lock:
            suggestions = self._suggestions
            if status_filter:
                suggestions = [s for s in suggestions if s.status == status_filter]
            return sorted(suggestions, key=lambda s: s.created_at, reverse=True)

    def get_evolution_history(self, branch_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if branch_id:
                cursor = self._db.execute(
                    "SELECT * FROM evolution_history WHERE branch_id = ? ORDER BY created_at DESC",
                    (branch_id,),
                )
            else:
                cursor = self._db.execute("SELECT * FROM evolution_history ORDER BY created_at DESC")
            return [
                {
                    "id": row["id"],
                    "branch_id": row["branch_id"],
                    "suggestion_id": row["suggestion_id"],
                    "action": row["action"],
                    "details": json.loads(row["details"]) if row["details"] else {},
                    "created_at": row["created_at"],
                }
                for row in cursor.fetchall()
            ]

    def close(self) -> None:
        """关闭数据库连接，归还连接到连接池。

        close() 后本实例不应再使用。置 None 标记已关闭，防止重复归还连接。
        （连接由连接池托管复用，文件句柄保持打开是池设计固有行为）
        """
        if self._db:
            self._pool.return_connection(self._db)
            self._db = None  # type: ignore[assignment]
            logger.info("TemplateEvolutionEngine closed")


class _EvolutionEngineHolder:
    """Thread-safe lazy holder for the :class:`TemplateEvolutionEngine` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: TemplateEvolutionEngine | None = None

    def get(self) -> TemplateEvolutionEngine:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = TemplateEvolutionEngine()
                self._instance.initialize()
            return self._instance

    def init(
        self,
        db_path: str = "data/templates/evolution.db",
        log_dir: str = "data/templates/evolution_log",
    ) -> TemplateEvolutionEngine:
        """强制重新创建实例（用于启动时指定 db_path/log_dir 的场景）。"""
        with self._lock:
            if self._instance is not None:
                self._instance.close()
            self._instance = TemplateEvolutionEngine(db_path=db_path, log_dir=log_dir)
            self._instance.initialize()
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _EvolutionEngineHolder()


def get_evolution_engine() -> TemplateEvolutionEngine:
    """获取共享的 :class:`TemplateEvolutionEngine` 单例；首次访问时懒初始化。

    Returns:
        :class:`TemplateEvolutionEngine` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_evolution_engine)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


def init_template_evolution(
    db_path: str = "data/templates/evolution.db",
    log_dir: str = "data/templates/evolution_log",
) -> TemplateEvolutionEngine:
    """初始化模板演化引擎，行为与重构前完全一致。"""
    return _holder.init(db_path=db_path, log_dir=log_dir)
