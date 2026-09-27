"""编排器跨管线记忆（AI 深度参与·"越用越懂你"的软件地基）。

此前 ``AgentOrchestrator`` 的"记忆"仅有内存中的 ``_pipeline_history`` 与
每日 trace 落盘——``app/state`` 的 ``MemoryEntry``/checkpoint 体系建好了
却从未接入编排主链路，Agent 每次执行都是无状态的。

本模块给编排器补上**跨管线的持久记忆**，持久化直接复用
``app.state.checkpoint.CheckpointLifecycleManager`` 的既有原语
（压缩落盘 / 生命周期清理 / 目录管理），不另造文件读写：

- 条目：沿用 ``app.models.agent_state`` 的 ``MemoryEntry`` 语义
  （content / memory_type / importance / tags / metadata）。
- 记录（write 路径）：管线结束时提炼结构化经验——修复动作、人工升级
  原因、参数修正，供下次同类任务参考。
- 检索（read 路径）：按材料/特征标签匹配 + importance 排序取 Top-N，
  注入后续管线的规划/参数推荐上下文。

设计约束：
- 记忆是**参考**不是**决策**：检索结果只进上下文，物理钳制与安全校验
  语义不变（"AI 提案、规则裁决"纪律的组成部分）。
- 上限 MAX_MEMORY_ENTRIES=1000，超过后按 (importance, age) 剪枝，
  与 app/state 常量保持一致。
- agent_id 仅允许 ``[A-Za-z0-9_-]``（作为 checkpoint 子目录名，杜绝
  任何目录跳转片段）。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import zlib
from typing import Any

from app.state.checkpoint import CheckpointLifecycleManager

logger = logging.getLogger(__name__)

MAX_MEMORY_ENTRIES = 1000
MEMORY_PRUNING_THRESHOLD = 800
DEFAULT_TOP_K = 5
DEFAULT_AGENT_ID = "orchestrator"
_MEMORY_CHECKPOINT_ID = "long_term_memory"

_AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class OrchestratorMemory:
    """编排器持久记忆存储（基于 app/state checkpoint 原语，线程安全）。"""

    def __init__(
        self,
        base_dir: str | None = None,
        agent_id: str = DEFAULT_AGENT_ID,
    ):
        if not _AGENT_ID_PATTERN.match(agent_id or ""):
            raise ValueError(f"agent_id 非法（仅允许字母/数字/下划线/连字符）: {agent_id!r}")
        self._lifecycle = CheckpointLifecycleManager(base_dir=base_dir) if base_dir else CheckpointLifecycleManager()
        self._agent_id = agent_id
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------------
    # 持久化（复用 app/state checkpoint 原语：zlib 压缩 + 生命周期管理）
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = self._lifecycle.load_checkpoint_file(self._agent_id, _MEMORY_CHECKPOINT_ID)
        except (OSError, ValueError, zlib.error, EOFError) as e:
            # zlib.error/EOFError：checkpoint 截断或损坏（持久化层最常见故障）——
            # 以空记忆启动，绝不让记忆故障击穿管线
            logger.warning("OrchestratorMemory 加载失败（以空记忆启动）: %s", e)
            raw = None
        if not raw:
            return
        try:
            data = json.loads(raw.decode("utf-8"))
            entries = data.get("entries", [])
            if isinstance(entries, list):
                self._entries = [e for e in entries if isinstance(e, dict)]
            logger.info("OrchestratorMemory: 已加载 %d 条记忆", len(self._entries))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning("OrchestratorMemory 记忆文件损坏（以空记忆启动）: %s", e)
            self._entries = []

    def _persist(self) -> None:
        try:
            payload = json.dumps(
                {"entries": self._entries, "updated_at": time.time()},
                ensure_ascii=False,
            ).encode("utf-8")
            self._lifecycle.save_checkpoint_file(self._agent_id, _MEMORY_CHECKPOINT_ID, payload)
        except (OSError, TypeError, ValueError) as e:
            logger.warning("OrchestratorMemory 落盘失败: %s", e)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def record(
        self,
        content: str,
        memory_type: str = "observation",
        importance: float = 0.5,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """记录一条经验记忆。

        Args:
            content: 人可读的经验描述（如 "钛合金 pocket 主轴转速下调至 1500"）
            memory_type: observation / correction / repair / escalation
            importance: 取值区间 [0,1]，影响检索优先级与剪枝顺序
            tags: 检索标签（材料 / 特征类型 / 管线类型）
            metadata: 结构化补充（错误码、参数快照等）
        """
        entry = {
            "content": content,
            "memory_type": memory_type,
            "importance": float(min(max(importance, 0.0), 1.0)),
            "tags": [t for t in (tags or []) if t],
            "metadata": metadata or {},
            "created_at": time.time(),
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > MEMORY_PRUNING_THRESHOLD:
                self._prune()
            self._persist()
        return entry

    def _prune(self) -> None:
        """超过阈值即剪回阈值（P2-6：原实现剪到 MAX 反而是 no-op 区间）。"""
        self._entries.sort(key=lambda e: (e.get("importance", 0.0), e.get("created_at", 0.0)))
        self._entries = self._entries[-MEMORY_PRUNING_THRESHOLD:]

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def recall(self, tags: list[str], top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
        """按标签检索相关记忆（任一标签命中即算），importance 降序 Top-N。"""
        wanted = {str(t).strip().lower() for t in tags if t}
        if not wanted:
            return []
        with self._lock:
            hits = [e for e in self._entries if wanted & {str(t).strip().lower() for t in e.get("tags", [])}]
        hits.sort(key=lambda e: e.get("importance", 0.0), reverse=True)
        return hits[:top_k]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def summarize_pipeline_for_memory(
    pipeline_type: str,
    input_data: dict[str, Any],
    result: Any,
) -> list[dict[str, Any]]:
    """从完成的管线结果提炼应记入长期记忆的经验条目。

    只提炼有复用价值的信号：
    - 自动修复动作（repair_history）：同类图纸下次生成时避开
    - 人工升级（fallback）：记录失败原因供工程师/下次参考
    - 材料与特征标签：作为检索锚点

    Returns:
        记忆条目列表（可直接传给 ``OrchestratorMemory.record`` 的参数字典）
    """
    entries: list[dict[str, Any]] = []
    if not isinstance(input_data, dict):
        input_data = {}
    material = str(input_data.get("material_name") or input_data.get("material") or "").strip()
    tags = [pipeline_type]
    if material:
        tags.append(material)

    repair_history = getattr(result, "repair_history", None) or []
    if repair_history:
        actions = [a for r in repair_history for a in (r.get("applied") or [])]
        if actions:
            entries.append(
                {
                    "content": f"管线 {pipeline_type} 自动修复：{'；'.join(str(a) for a in actions[:5])}",
                    "memory_type": "repair",
                    "importance": 0.6,
                    "tags": tags + [c for r in repair_history for c in (r.get("error_codes") or [])][:3],
                    "metadata": {"repair_count": getattr(result, "repair_count", 0)},
                }
            )

    if getattr(result, "fallback_triggered", False):
        reason = str(getattr(result, "fallback_reason", ""))[:200]
        if reason:
            entries.append(
                {
                    "content": f"管线 {pipeline_type} 升级人工：{reason}",
                    "memory_type": "escalation",
                    "importance": 0.8,
                    "tags": tags,
                    "metadata": {},
                }
            )

    return entries
