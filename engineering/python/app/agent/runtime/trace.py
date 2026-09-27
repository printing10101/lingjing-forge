"""Agent 执行轨迹存储（自进化 M2）。

每次 ``AgentRuntime.run`` 产出一条完整轨迹（任务 → 多轮工具调用 →
最终回答），JSONL 追加落盘——这是产品内第一批「可被训练的多轮 LLM
轨迹」，也是 AGL（T3 权重级 RL）未来的 rollout 数据源。

存储约定：默认 ``python/data/agent_traces.jsonl``，env
``LNN_AGENT_RUNTIME_TRACE`` 覆盖。追加写 + 失败静默（轨迹是旁路
观测，任何 I/O 问题不允许影响主流程）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["TrajectoryStore", "get_trajectory_store", "reset_trajectory_store"]


class TrajectoryStore:
    """轨迹 JSONL 存储（线程安全追加写）。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_path()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, trace: dict[str, Any]) -> bool:
        """追加一条轨迹。返回是否成功（失败仅告警，不抛出）。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(trace, ensure_ascii=False, default=str)
            with self._lock:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            return True
        except (OSError, TypeError, ValueError) as e:
            logger.warning("轨迹写入失败（跳过）: %s", e)
            return False

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """按时间倒序读取最近 N 条轨迹（文件缺失/损坏行跳过）。"""
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            logger.warning("轨迹读取失败: %s", e)
            return []
        traces: list[dict[str, Any]] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                traces.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(traces) >= limit:
                break
        return traces


def _default_path() -> Path:
    env_path = os.environ.get("LNN_AGENT_RUNTIME_TRACE")
    if env_path:
        return Path(env_path)
    # app/agent/runtime/trace.py → parents[3] = engineering/python
    return Path(__file__).resolve().parents[3] / "data" / "agent_traces.jsonl"


_store: TrajectoryStore | None = None
_store_lock = threading.Lock()


def get_trajectory_store() -> TrajectoryStore:
    """获取全局轨迹存储（线程安全懒加载）。"""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            _store = TrajectoryStore()
        return _store


def reset_trajectory_store() -> None:
    """重置全局单例（测试用）。"""
    global _store
    with _store_lock:
        _store = None
