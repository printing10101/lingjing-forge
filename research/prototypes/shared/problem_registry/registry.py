"""问题注册表：研究模块从这里读取产品中遇到的问题。

数据来源：
- data/bridge/error_samples/errors.jsonl（产品自动错误日志）
- data/bridge/error_samples/user_reports.jsonl（用户主动反馈）
- research/shared/problem_registry/registry.jsonl（工单池）
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


class ProblemStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    WONT_FIX = "wont_fix"


class ProblemPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class Problem:
    """一个问题。"""

    ticket_id: str
    title: str
    description: str
    category: str
    severity: str
    priority: ProblemPriority
    status: ProblemStatus
    created_at: str
    feature: Optional[str] = None
    sample_id: Optional[str] = None
    context: dict = field(default_factory=dict)
    occurrences: int = 1  # 同一类问题的累计出现次数
    last_seen_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["priority"] = self.priority.value
        d["status"] = self.status.value
        return d


class ProblemRegistry:
    """问题注册表。"""

    def __init__(self, registry_path: str = "research/shared/problem_registry/registry.jsonl"):
        self._path = Path(registry_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def iter_all(self) -> Iterator[Problem]:
        """遍历所有问题。"""
        if not self._path.exists():
            return
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    yield _parse_problem(rec)
                except Exception:  # noqa: BLE001
                    continue

    def iter_open(self) -> Iterator[Problem]:
        """遍历未关闭的问题。"""
        for p in self.iter_all():
            if p.status in (ProblemStatus.OPEN, ProblemStatus.INVESTIGATING, ProblemStatus.IN_PROGRESS):
                yield p

    def top_n(self, n: int = 10) -> list[Problem]:
        """按优先级 + 出现次数返回 top N。"""
        items = list(self.iter_open())
        # 优先级权重
        weight = {
            ProblemPriority.CRITICAL: 1000,
            ProblemPriority.HIGH: 100,
            ProblemPriority.NORMAL: 10,
            ProblemPriority.LOW: 1,
        }
        items.sort(
            key=lambda p: (weight[p.priority], p.occurrences),
            reverse=True,
        )
        return items[:n]

    def count(self, status: Optional[ProblemStatus] = None) -> int:
        cnt = 0
        for p in self.iter_all():
            if status is None or p.status == status:
                cnt += 1
        return cnt

    def summary(self) -> dict:
        """返回总览统计。"""
        total = 0
        by_priority: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for p in self.iter_all():
            total += 1
            by_priority[p.priority.value] = by_priority.get(p.priority.value, 0) + 1
            by_category[p.category] = by_category.get(p.category, 0) + 1
        return {
            "total": total,
            "by_priority": by_priority,
            "by_category": by_category,
        }


def _parse_problem(rec: dict) -> Problem:
    """把 dict 解析成 Problem。"""
    try:
        priority = ProblemPriority(rec.get("priority", "normal"))
    except ValueError:
        priority = ProblemPriority.NORMAL
    try:
        status = ProblemStatus(rec.get("status", "open"))
    except ValueError:
        status = ProblemStatus.OPEN
    return Problem(
        ticket_id=rec.get("ticket_id", ""),
        title=rec.get("title", ""),
        description=rec.get("description", ""),
        category=rec.get("category", "general"),
        severity=rec.get("severity", "medium"),
        priority=priority,
        status=status,
        created_at=rec.get("created_at", datetime.now().isoformat()),
        feature=rec.get("feature"),
        sample_id=rec.get("sample_id"),
        context=rec.get("context", {}),
        occurrences=int(rec.get("occurrences", 1)),
        last_seen_at=rec.get("last_seen_at", rec.get("created_at", "")),
    )


__all__ = ["ProblemRegistry", "Problem", "ProblemStatus", "ProblemPriority"]
