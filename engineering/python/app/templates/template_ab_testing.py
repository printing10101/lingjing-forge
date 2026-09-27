"""A/B Testing Framework — validates template changes via controlled experiments."""

import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.utils.sqlite_pool import get_sqlite_manager

logger = logging.getLogger(__name__)


@dataclass
class ABExperiment:
    experiment_id: str
    name: str
    control_branch: str
    candidate_branch: str
    traffic_split: float
    status: str
    metrics: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    concluded_at: float | None = None
    result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "name": self.name,
            "control_branch": self.control_branch,
            "candidate_branch": self.candidate_branch,
            "traffic_split": self.traffic_split,
            "status": self.status,
            "metrics": self.metrics,
            "created_at": self.created_at,
            "concluded_at": self.concluded_at,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ABExperiment":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ABTestingFramework:
    """Manages A/B experiments for template changes with automatic merge/rollback."""

    MIN_SAMPLE_SIZE = 30
    CONFIDENCE_THRESHOLD = 0.95
    IMPROVEMENT_THRESHOLD = 0.05

    def __init__(self, db_path: str = "data/templates/ab_testing.db"):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._experiments: dict[str, ABExperiment] = {}
        self._traffic_map: dict[str, str] = {}
        # 自动初始化数据库
        self.initialize()

    def initialize(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("ab_testing", db_path=self.db_path)
        self._db = self._pool.get_connection()
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS ab_experiments (
                experiment_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                control_branch TEXT,
                candidate_branch TEXT,
                traffic_split REAL,
                status TEXT,
                metrics TEXT,
                created_at REAL,
                concluded_at REAL,
                result TEXT
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS ab_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT,
                branch TEXT,
                metrics TEXT,
                created_at REAL
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS ab_traffic (
                project_id TEXT PRIMARY KEY,
                experiment_id TEXT,
                assigned_branch TEXT
            )
        """)
        self._db.commit()
        self._load_data()
        logger.info("ABTestingFramework initialized: db=%s", self.db_path)

    def _load_data(self) -> None:
        cursor = self._db.execute("SELECT * FROM ab_experiments")
        for row in cursor.fetchall():
            self._experiments[row["experiment_id"]] = ABExperiment(
                experiment_id=row["experiment_id"],
                name=row["name"],
                control_branch=row["control_branch"],
                candidate_branch=row["candidate_branch"],
                traffic_split=row["traffic_split"],
                status=row["status"],
                metrics=json.loads(row["metrics"]) if row["metrics"] else {},
                created_at=row["created_at"],
                concluded_at=row["concluded_at"],
                result=row["result"],
            )

        cursor = self._db.execute("SELECT * FROM ab_traffic")
        for row in cursor.fetchall():
            self._traffic_map[row["project_id"]] = row["assigned_branch"]

    def create_experiment(
        self,
        name: str,
        control_branch: str,
        candidate_branch: str,
        traffic_split: float = 0.10,
    ) -> ABExperiment:
        with self._lock:
            exp = ABExperiment(
                experiment_id=f"exp_{uuid.uuid4().hex[:8]}",
                name=name,
                control_branch=control_branch,
                candidate_branch=candidate_branch,
                traffic_split=traffic_split,
                status="running",
                metrics={
                    "control": {
                        "count": 0,
                        "execution_times": [],
                        "success_count": 0,
                        "resource_costs": [],
                    },
                    "candidate": {
                        "count": 0,
                        "execution_times": [],
                        "success_count": 0,
                        "resource_costs": [],
                    },
                },
            )
            self._experiments[exp.experiment_id] = exp
            self._db.execute(
                """INSERT INTO ab_experiments
                   (experiment_id, name, control_branch, candidate_branch, traffic_split, status, metrics, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    exp.experiment_id,
                    name,
                    control_branch,
                    candidate_branch,
                    traffic_split,
                    "running",
                    json.dumps(exp.metrics),
                    exp.created_at,
                ),
            )
            self._db.commit()
            logger.info(
                "Experiment created: id=%s, name=%s, split=%.0f%%",
                exp.experiment_id,
                name,
                traffic_split * 100,
            )
            return exp

    def record_execution(
        self,
        experiment_id: str,
        branch: str,
        metrics: dict[str, Any],
    ) -> None:
        with self._lock:
            exp = self._experiments.get(experiment_id)
            if exp is None or exp.status != "running":
                return

            side = exp.metrics.get(branch)
            if side is None:
                return

            side["count"] += 1
            if "execution_time" in metrics:
                side["execution_times"].append(metrics["execution_time"])
            if "success" in metrics:
                side["success_count"] += 1 if metrics["success"] else 0
            if "resource_cost" in metrics:
                side["resource_costs"].append(metrics["resource_cost"])

            self._db.execute(
                """INSERT INTO ab_executions (experiment_id, branch, metrics, created_at)
                   VALUES (?, ?, ?, ?)""",
                (experiment_id, branch, json.dumps(metrics), time.time()),
            )
            self._db.execute(
                "UPDATE ab_experiments SET metrics = ? WHERE experiment_id = ?",
                (json.dumps(exp.metrics), experiment_id),
            )
            self._db.commit()

    def assign_branch(self, project_id: str, experiment_id: str) -> str:
        """Deterministically assign a project to control or candidate branch."""
        with self._lock:
            key = f"{project_id}:{experiment_id}"
            if key in self._traffic_map:
                return self._traffic_map[key]

            exp = self._experiments.get(experiment_id)
            if exp is None:
                return "control"

            # 安全修复：使用 SHA256 替代 MD5，避免分桶预测
            hash_val = int(hashlib.sha256(key.encode()).hexdigest(), 16) % 100
            branch = "candidate" if hash_val < exp.traffic_split * 100 else "control"
            self._traffic_map[key] = branch
            self._db.execute(
                """INSERT OR REPLACE INTO ab_traffic (project_id, experiment_id, assigned_branch)
                   VALUES (?, ?, ?)""",
                (key, experiment_id, branch),
            )
            self._db.commit()
            return branch

    def evaluate(self, experiment_id: str) -> dict[str, Any] | None:
        with self._lock:
            exp = self._experiments.get(experiment_id)
            if exp is None:
                return None

            control = exp.metrics.get("control", {})
            candidate = exp.metrics.get("candidate", {})

            if control["count"] < self.MIN_SAMPLE_SIZE or candidate["count"] < self.MIN_SAMPLE_SIZE:
                return {
                    "status": "insufficient_data",
                    "control_count": control["count"],
                    "candidate_count": candidate["count"],
                    "required": self.MIN_SAMPLE_SIZE,
                }

            result = self._statistical_test(control, candidate)
            result["experiment_id"] = experiment_id
            return result

    def _statistical_test(self, control: dict, candidate: dict) -> dict[str, Any]:
        control_times = control.get("execution_times", [])
        candidate_times = candidate.get("execution_times", [])

        if not control_times or not candidate_times:
            return {
                "status": "insufficient_metrics",
                "message": "No execution time data",
            }

        control_mean = sum(control_times) / len(control_times)
        candidate_mean = sum(candidate_times) / len(candidate_times)
        improvement = (control_mean - candidate_mean) / control_mean if control_mean > 0 else 0

        control_success = control.get("success_count", 0)
        candidate_success = candidate.get("success_count", 0)
        control_rate = control_success / max(control["count"], 1)
        candidate_rate = candidate_success / max(candidate["count"], 1)

        confidence = self._compute_confidence(control_times, candidate_times)

        if improvement > self.IMPROVEMENT_THRESHOLD and confidence > self.CONFIDENCE_THRESHOLD:
            verdict = "winner_candidate"
        elif improvement < 0:
            verdict = "winner_control"
        else:
            verdict = "inconclusive"

        return {
            "status": "concluded",
            "verdict": verdict,
            "improvement": round(improvement, 4),
            "confidence": round(confidence, 4),
            "control_mean_time": round(control_mean, 2),
            "candidate_mean_time": round(candidate_mean, 2),
            "control_success_rate": round(control_rate, 4),
            "candidate_success_rate": round(candidate_rate, 4),
        }

    def _compute_confidence(self, control: list[float], candidate: list[float]) -> float:
        if len(control) < 2 or len(candidate) < 2:
            return 0.0

        n1, n2 = len(control), len(candidate)
        m1 = sum(control) / n1
        m2 = sum(candidate) / n2
        v1 = sum((x - m1) ** 2 for x in control) / max(n1 - 1, 1)
        v2 = sum((x - m2) ** 2 for x in candidate) / max(n2 - 1, 1)

        se_sq = v1 / n1 + v2 / n2
        if se_sq <= 0:
            return 0.999 if m1 != m2 else 0.5

        se = math.sqrt(se_sq)
        t = abs(m2 - m1) / se

        df_num = se_sq**2
        df_den = (v1 / n1) ** 2 / max(n1 - 1, 1) + (v2 / n2) ** 2 / max(n2 - 1, 1)
        df = df_num / max(df_den, 0.001)

        p_value = 2 * (1 - self._t_cdf_approx(t, df))
        confidence = max(0.0, min(1.0, 1 - p_value))
        return confidence

    def _t_cdf_approx(self, t: float, df: float) -> float:
        if df > 120:
            return 0.5 * (1 + math.erf(t / math.sqrt(2)))

        x = df / (df + t * t)
        ibeta = self._incomplete_beta_approx(df / 2, 0.5, x)
        if t >= 0:
            return 1 - 0.5 * ibeta
        return 0.5 * ibeta

    def _incomplete_beta_approx(self, a: float, b: float, x: float) -> float:
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        log_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        front = math.exp(a * math.log(x) + b * math.log(1 - x) - log_beta)
        return front / max(a, 0.001)

    def auto_conclude(self, experiment_id: str) -> ABExperiment | None:
        """Evaluate and automatically merge or rollback based on criteria."""
        with self._lock:
            exp = self._experiments.get(experiment_id)
            if exp is None or exp.status != "running":
                return None

            result = self.evaluate(experiment_id)
            if result is None or result.get("status") != "concluded":
                return None

            exp.concluded_at = time.time()
            exp.result = result["verdict"]

            if result["verdict"] == "winner_candidate":
                exp.status = "merged"
                logger.info(
                    "Experiment auto-merged: id=%s (improvement=%.1f%%, confidence=%.2f)",
                    experiment_id,
                    result["improvement"] * 100,
                    result["confidence"],
                )
            elif result["verdict"] == "winner_control":
                exp.status = "rolled_back"
                logger.info("Experiment auto-rolled back: id=%s", experiment_id)
            else:
                exp.status = "concluded"
                logger.info("Experiment concluded inconclusive: id=%s", experiment_id)

            self._db.execute(
                """UPDATE ab_experiments SET status=?, concluded_at=?, result=?, metrics=?
                   WHERE experiment_id=?""",
                (
                    exp.status,
                    exp.concluded_at,
                    exp.result,
                    json.dumps(exp.metrics),
                    experiment_id,
                ),
            )
            self._db.commit()
            return exp

    def get_active_experiments(self) -> list[ABExperiment]:
        with self._lock:
            return [e for e in self._experiments.values() if e.status == "running"]

    def get_experiment_results(self, experiment_id: str) -> dict[str, Any] | None:
        with self._lock:
            exp = self._experiments.get(experiment_id)
            if exp is None:
                return None
            return exp.to_dict()

    def list_experiments(self, status_filter: str | None = None) -> list[ABExperiment]:
        with self._lock:
            exps = list(self._experiments.values())
            if status_filter:
                exps = [e for e in exps if e.status == status_filter]
            return sorted(exps, key=lambda e: e.created_at, reverse=True)

    def close(self) -> None:
        if self._db:
            self._db.close()


class _ABFrameworkHolder:
    """Thread-safe lazy holder for the :class:`ABTestingFramework` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: ABTestingFramework | None = None

    def get(self) -> ABTestingFramework:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = ABTestingFramework()
                self._instance.initialize()
            return self._instance

    def init(
        self,
        db_path: str = "data/templates/ab_testing.db",
    ) -> ABTestingFramework:
        """强制重新创建实例（用于启动时指定 db_path 的场景）。"""
        with self._lock:
            if self._instance is not None:
                self._instance.close()
            self._instance = ABTestingFramework(db_path=db_path)
            self._instance.initialize()
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _ABFrameworkHolder()


def get_ab_testing() -> ABTestingFramework:
    """获取共享的 :class:`ABTestingFramework` 单例；首次访问时懒初始化。

    Returns:
        :class:`ABTestingFramework` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_ab_testing)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


def init_ab_testing(
    db_path: str = "data/templates/ab_testing.db",
) -> ABTestingFramework:
    """初始化 A/B 测试框架，行为与重构前完全一致。"""
    return _holder.init(db_path=db_path)
