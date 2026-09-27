"""SHARP 轨迹存储（M4.1）。

将 `VerificationResult` 持久化到本地 JSONL 文件，供 M4.2 相似度检索使用。

存储方案
--------
- **格式**：JSON Lines（每行一个 JSON 对象，便于追加写入与流式读取）
- **位置**：`~/.lingjing/sharp/trajectories.jsonl`（可通过环境变量 `SHARP_TRAJECTORY_PATH` 覆盖）
- **内存索引**：启动时全量加载到 `_records: list[StoredTrajectory]`，查询走内存
- **追加写入**：每次 `store()` 后立即追加一行，并同步更新内存索引
- **去重**：同一 `verification_id` 不重复写入

容量管理
--------
- `max_records`：内存中保留的最大记录数（默认 1000），超过后淘汰最旧的
- 文件大小无硬性限制，但建议定期归档（由运维侧处理）
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# 存储结构


@dataclass
class StoredTrajectory:
    """持久化的验证轨迹摘要。

    仅保存 M4 检索所需的关键字段，避免完整 trajectory 过大占用内存。

    Attributes
    ----------
    verification_id : str
        验证 ID（唯一）
    triple : dict
        三元组结构（head_type/head_id/relation/tail_type/tail_id）
    verdict : str
        判定结果 supported/refuted/uncertain
    confidence : float
        最终置信度
    reasoning : str
        LLM 推理依据（截断 500 字符）
    stopping_trigger : str
        终止触发器
    steps_taken : int
        实际步数
    elapsed_ms : float
        总耗时
    timestamp : float
        时间戳（Unix epoch）
    key_evidence : list[str]
        关键证据摘要（最多 3 条，每条截断 200 字符）
    """

    verification_id: str
    triple: dict[str, Any]
    verdict: str = "uncertain"
    confidence: float = 0.0
    reasoning: str = ""
    stopping_trigger: str = ""
    steps_taken: int = 0
    elapsed_ms: float = 0.0
    timestamp: float = 0.0
    key_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoredTrajectory":
        return cls(
            verification_id=str(data.get("verification_id", "")),
            triple=data.get("triple", {}) or {},
            verdict=str(data.get("verdict", "uncertain")),
            confidence=float(data.get("confidence", 0.0)),
            reasoning=str(data.get("reasoning", "")),
            stopping_trigger=str(data.get("stopping_trigger", "")),
            steps_taken=int(data.get("steps_taken", 0)),
            elapsed_ms=float(data.get("elapsed_ms", 0.0)),
            timestamp=float(data.get("timestamp", 0.0)),
            key_evidence=list(data.get("key_evidence", []) or []),
        )

    @classmethod
    def from_verification_result(cls, result: Any, timestamp: float | None = None) -> "StoredTrajectory":
        """从 `VerificationResult` 构造存储记录。

        Args:
            result: `VerificationResult` 实例（duck-typed）
            timestamp: 时间戳，None 时使用 time.time()
        """
        triple = result.triple
        triple_dict = {
            "head_type": triple.head_type.value if hasattr(triple.head_type, "value") else str(triple.head_type),
            "head_id": triple.head_id,
            "relation": triple.relation.value if hasattr(triple.relation, "value") else str(triple.relation),
            "tail_type": triple.tail_type.value if hasattr(triple.tail_type, "value") else str(triple.tail_type),
            "tail_id": triple.tail_id,
        }

        # 提取关键证据（最多 3 条，每条截断 200 字符）
        key_evidence: list[str] = []
        for ev in (result.evidence_chain or [])[:3]:
            content = ev.get("content", "") if isinstance(ev, dict) else str(ev)
            key_evidence.append(content[:200])

        return cls(
            verification_id=result.verification_id,
            triple=triple_dict,
            verdict=result.verdict,
            confidence=result.confidence,
            reasoning=(result.reasoning or "")[:500],
            stopping_trigger=result.stopping_decision.get("trigger", "")
            if isinstance(result.stopping_decision, dict)
            else "",
            steps_taken=result.steps_taken,
            elapsed_ms=result.elapsed_ms,
            timestamp=timestamp if timestamp is not None else time.time(),
            key_evidence=key_evidence,
        )


# 轨迹存储


class TrajectoryStore:
    """验证轨迹持久化存储。

    线程安全：通过 `_lock` 保护文件写入与内存索引更新。

    Usage::

        store = TrajectoryStore()
        store.store(result)  # 追加写入
        records = store.list_all()  # 内存查询
    """

    DEFAULT_PATH = str(Path.home() / ".lingjing" / "sharp" / "trajectories.jsonl")

    def __init__(
        self,
        storage_path: str | None = None,
        max_records: int = 1000,
        autoload: bool = True,
    ) -> None:
        """初始化轨迹存储。

        Args:
            storage_path: JSONL 文件路径，None 时使用默认路径或环境变量
            max_records: 内存中保留的最大记录数
            autoload: 是否在初始化时自动加载已有文件
        """
        self._path = storage_path or os.environ.get("SHARP_TRAJECTORY_PATH", self.DEFAULT_PATH)
        self._max_records = max_records
        self._lock = threading.Lock()
        self._records: list[StoredTrajectory] = []
        self._ids: set[str] = set()

        if autoload:
            self._load_from_disk()

    # 公共接口

    def store(self, result: Any, timestamp: float | None = None) -> StoredTrajectory:
        """存储一条验证轨迹。

        Args:
            result: `VerificationResult` 实例
            timestamp: 时间戳，None 时使用 time.time()

        Returns:
            存储的 `StoredTrajectory`
        """
        record = StoredTrajectory.from_verification_result(result, timestamp)

        with self._lock:
            if record.verification_id in self._ids:
                logger.debug("Trajectory already stored: %s, skip", record.verification_id)
                return record

            # 更新内存索引
            self._records.append(record)
            self._ids.add(record.verification_id)

            # 容量淘汰
            while len(self._records) > self._max_records:
                evicted = self._records.pop(0)
                self._ids.discard(evicted.verification_id)

            # 追加写入文件
            self._append_to_file(record)

        logger.debug(
            "Stored trajectory: id=%s verdict=%s conf=%.3f",
            record.verification_id,
            record.verdict,
            record.confidence,
        )
        return record

    def list_all(self) -> list[StoredTrajectory]:
        """返回所有内存中的轨迹记录。"""
        with self._lock:
            return list(self._records)

    def get(self, verification_id: str) -> StoredTrajectory | None:
        """按 ID 查询轨迹。"""
        with self._lock:
            for r in self._records:
                if r.verification_id == verification_id:
                    return r
        return None

    def count(self) -> int:
        """返回记录数。"""
        with self._lock:
            return len(self._records)

    def clear(self) -> int:
        """清空内存与文件。返回被清除的记录数。"""
        with self._lock:
            n = len(self._records)
            self._records.clear()
            self._ids.clear()
        try:
            if os.path.exists(self._path):
                os.remove(self._path)
        except OSError as e:
            logger.warning("Failed to remove trajectory file %s: %s", self._path, e)
        return n

    # 内部方法

    def _load_from_disk(self) -> None:
        """从 JSONL 文件加载已有记录。"""
        if not os.path.exists(self._path):
            return

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        record = StoredTrajectory.from_dict(data)
                        if record.verification_id and record.verification_id not in self._ids:
                            self._records.append(record)
                            self._ids.add(record.verification_id)
                    except (json.JSONDecodeError, KeyError, TypeError) as e:
                        logger.warning("Skip malformed trajectory line: %s", e)
                        continue
        except OSError as e:
            logger.warning("Failed to load trajectory file %s: %s", self._path, e)
            return

        # 淘汰超额记录
        while len(self._records) > self._max_records:
            evicted = self._records.pop(0)
            self._ids.discard(evicted.verification_id)

        logger.info("Loaded %d trajectories from %s", len(self._records), self._path)

    def _append_to_file(self, record: StoredTrajectory) -> None:
        """追加一行到 JSONL 文件。"""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
        except OSError as e:
            logger.warning("Failed to create trajectory dir: %s", e)
            return

        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("Failed to append trajectory: %s", e)


__all__ = ["TrajectoryStore", "StoredTrajectory"]
