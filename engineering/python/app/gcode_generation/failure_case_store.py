"""失败案例库（Phase 0 自进化第一性管道 · 持久层）。

记录 G 代码生成任务与 Agent 编排器 LLM 提案位的成败结果，沉淀
「输入 → 错误输出 → 失败原因分类」结构化案例，供提示词/检索迭代
与回归门控使用：

- ``failure``：校验失败（safety_validator / unstable_features）、
  流水线异常（pipeline_exception）或 LLM 提案位失败
  （llm_planning / llm_param_aug / gcode_repair / nl2cad_extract，
  见 app/agent/failure_recorder.py 的口径约定），保留完整输出与
  错误分类；
- ``success``：一次通过 GENERATED 的运行，仅记计数要素（不存 G 代码，
  控制库体积；成功案例进 RAG 工艺库是另一条管道，见阶段规划 M4）。

统计口径（M3 基线报表）：
- ``one_pass_rate``：success / (success + failure)，即「一次通过率」；
  同一 task 重跑计为多次运行。gcode 管线与编排器两条入口成败成对
  入册，口径合并统计；按 source 的失败分布见 ``by_source``。
- ``by_code``：按 error_code 聚合的失败分布（L1-L6 安全门禁码、
  UNSTABLE_FEATURES、LLM_INVALID_OUTPUT、异常类名）。

存储约定：SQLite（WAL），默认 ``python/data/failure_cases.db``，
环境变量 ``FAILURE_CASES_DB`` 覆盖（与 llm_providers.db 约定一致）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config.limits import DEFAULT_SQLITE_LOCK_TIMEOUT_SEC

logger = logging.getLogger(__name__)

__all__ = [
    "FailureCase",
    "FailureCaseStore",
    "generate_case_id",
    "get_failure_case_store",
    "reset_failure_case_store",
]

# 合法 outcome / source 值（校验用）。
# llm_* / nl2cad_* 五类为自进化 M0 新增的 LLM 提案位失败来源；
# react_agent 为自进化 M2 新增的 AgentRuntime ReAct 循环格式失败来源
# （写入方与口径约定见 app/agent/failure_recorder.py）。
VALID_OUTCOMES = ("failure", "success")
VALID_SOURCES = (
    "safety_validator",
    "unstable_features",
    "pipeline_exception",
    "llm_planning",
    "llm_param_aug",
    "gcode_repair",
    "nl2cad_extract",
    "react_agent",
    "",
)


def generate_case_id() -> str:
    """生成案例 ID。格式：fc_<uuid4>（与 gc_ / ch_ 前缀风格对齐）。"""
    return f"fc_{uuid.uuid4()}"


@dataclass
class FailureCase:
    """一条运行结果案例。

    Attributes:
        task_id: 关联的 G 代码生成任务 ID
        outcome: "failure" 或 "success"
        source: 失败来源（failure 时必填；success 为空）
        controller_type: 目标控制器
        material_name: 材料名称
        error_codes: 结构化错误码列表（L1-L6 门禁码 / UNSTABLE_FEATURES / 异常类名）
        error_messages: 人类可读错误消息列表
        gcode_text: 失败时的 G 代码文本（success 恒为空串）
        total_features: 特征总数
        unstable_features: 不稳定特征数
        created_at: 记录时间（epoch 秒）
        case_id: 案例唯一 ID
    """

    task_id: str
    outcome: str
    source: str = ""
    controller_type: str = ""
    material_name: str = ""
    error_codes: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    gcode_text: str = ""
    total_features: int = 0
    unstable_features: int = 0
    created_at: float = 0.0
    case_id: str = ""

    def __post_init__(self) -> None:
        if not self.case_id:
            self.case_id = generate_case_id()
        if not self.created_at:
            self.created_at = time.time()
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(f"非法 outcome: {self.outcome}，合法值: {list(VALID_OUTCOMES)}")
        if self.outcome == "success":
            # 成功案例不存 G 代码（控制库体积），也不应有失败来源
            self.gcode_text = ""
            self.error_codes = []
            self.error_messages = []
            self.source = ""
        elif not self.source:
            raise ValueError("failure 案例必须指定 source")
        if self.source and self.source not in VALID_SOURCES:
            raise ValueError(f"非法 source: {self.source}，合法值: {list(VALID_SOURCES)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "task_id": self.task_id,
            "outcome": self.outcome,
            "source": self.source,
            "controller_type": self.controller_type,
            "material_name": self.material_name,
            "error_codes": list(self.error_codes),
            "error_messages": list(self.error_messages),
            "gcode_text": self.gcode_text,
            "total_features": self.total_features,
            "unstable_features": self.unstable_features,
            "created_at": self.created_at,
        }


class FailureCaseStore:
    """失败案例 SQLite 存储（线程安全，单写多读）。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _default_db_path()
        self._lock = threading.Lock()
        self._initialized = False

    # ------------------------------------------------------------------
    # 数据库
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=DEFAULT_SQLITE_LOCK_TIMEOUT_SEC)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._get_conn()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS failure_cases (
                        case_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT '',
                        controller_type TEXT NOT NULL DEFAULT '',
                        material_name TEXT NOT NULL DEFAULT '',
                        error_codes TEXT NOT NULL DEFAULT '[]',
                        error_messages TEXT NOT NULL DEFAULT '[]',
                        gcode_text TEXT NOT NULL DEFAULT '',
                        total_features INTEGER NOT NULL DEFAULT 0,
                        unstable_features INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL DEFAULT 0
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_failure_cases_outcome ON failure_cases(outcome)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_failure_cases_created ON failure_cases(created_at)")
                conn.commit()
            finally:
                conn.close()
            self._initialized = True

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def record(self, case: FailureCase) -> str:
        """记录一条案例，返回 case_id。

        Raises:
            sqlite3.Error: 数据库写入失败（调用方应自行兜底，不影响主流程）
        """
        self._init_db()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO failure_cases
                    (case_id, task_id, outcome, source, controller_type, material_name,
                     error_codes, error_messages, gcode_text, total_features,
                     unstable_features, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case.case_id,
                        case.task_id,
                        case.outcome,
                        case.source,
                        case.controller_type,
                        case.material_name,
                        json.dumps(case.error_codes, ensure_ascii=False),
                        json.dumps(case.error_messages, ensure_ascii=False),
                        case.gcode_text,
                        case.total_features,
                        case.unstable_features,
                        case.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return case.case_id

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_cases(
        self,
        limit: int = 50,
        offset: int = 0,
        source: str | None = None,
        outcome: str | None = None,
    ) -> list[FailureCase]:
        """按时间倒序列出案例（可按 source / outcome 过滤）。"""
        self._init_db()
        conditions = []
        params: list[Any] = []
        if source is not None:
            conditions.append("source = ?")
            params.append(source)
        if outcome is not None:
            conditions.append("outcome = ?")
            params.append(outcome)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM failure_cases {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            return [self._row_to_case(r) for r in rows]
        finally:
            conn.close()

    def stats(self) -> dict[str, Any]:
        """M3 基线报表：一次通过率 + 按来源/错误码的失败分布。"""
        self._init_db()
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM failure_cases").fetchone()[0]
            failures = conn.execute("SELECT COUNT(*) FROM failure_cases WHERE outcome = 'failure'").fetchone()[0]
            successes = total - failures

            by_source = {
                row["source"]: row["n"]
                for row in conn.execute(
                    "SELECT source, COUNT(*) AS n FROM failure_cases "
                    "WHERE outcome = 'failure' GROUP BY source ORDER BY n DESC"
                ).fetchall()
            }

            # error_codes 是 JSON 数组，展开聚合（SQLite 无 JSON_TABLE 时的
            # 兼容做法：取出后在 Python 侧聚合；案例量级 ~千级，可接受）
            by_code: dict[str, int] = {}
            for row in conn.execute("SELECT error_codes FROM failure_cases WHERE outcome = 'failure'").fetchall():
                try:
                    codes = json.loads(row["error_codes"])
                except (json.JSONDecodeError, TypeError):
                    continue
                for code in codes:
                    by_code[code] = by_code.get(code, 0) + 1

            one_pass_rate = round(successes / total, 4) if total > 0 else None

            return {
                "total": total,
                "failures": failures,
                "successes": successes,
                "one_pass_rate": one_pass_rate,
                "by_source": by_source,
                "by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
            }
        finally:
            conn.close()

    def count(self) -> int:
        """案例总数。"""
        self._init_db()
        conn = self._get_conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM failure_cases").fetchone()[0]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_case(row: sqlite3.Row) -> FailureCase:
        try:
            codes = json.loads(row["error_codes"])
        except (json.JSONDecodeError, TypeError):
            codes = []
        try:
            messages = json.loads(row["error_messages"])
        except (json.JSONDecodeError, TypeError):
            messages = []
        return FailureCase(
            case_id=row["case_id"],
            task_id=row["task_id"],
            outcome=row["outcome"],
            source=row["source"],
            controller_type=row["controller_type"],
            material_name=row["material_name"],
            error_codes=codes,
            error_messages=messages,
            gcode_text=row["gcode_text"],
            total_features=row["total_features"],
            unstable_features=row["unstable_features"],
            created_at=row["created_at"],
        )


def _default_db_path() -> Path:
    """获取失败案例库路径。约定 python/data/failure_cases.db，
    环境变量 FAILURE_CASES_DB 覆盖（与 llm_providers.db 约定一致）。"""
    env_path = os.environ.get("FAILURE_CASES_DB")
    if env_path:
        return Path(env_path)
    python_dir = Path(__file__).resolve().parent.parent.parent
    data_dir = python_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "failure_cases.db"


# 全局单例（双重检查锁）

_store: FailureCaseStore | None = None
_store_lock = threading.Lock()


def get_failure_case_store() -> FailureCaseStore:
    """获取全局 FailureCaseStore（线程安全懒加载）。"""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            _store = FailureCaseStore()
        return _store


def reset_failure_case_store() -> None:
    """重置全局单例（测试用）。"""
    global _store
    with _store_lock:
        _store = None
