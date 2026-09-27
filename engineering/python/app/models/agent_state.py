"""
Agent State Model - Persistent Agent State & Session Recovery

References Paperclip's Persistent Agent State design:
- Full agent lifecycle state tracking
- Checkpoint management for training resumption
- Serialization/deserialization for persistent storage
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any
from collections.abc import Callable


class AgentStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    PAUSED = "paused"
    ERROR = "error"
    STOPPED = "stopped"
    RECOVERING = "recovering"


class CheckpointType(str, Enum):
    EPOCH = "epoch"
    MANUAL = "manual"
    AUTO = "auto"
    HEARTBEAT = "heartbeat"
    PRE_SHUTDOWN = "pre_shutdown"


@dataclass
class Checkpoint:
    """Training checkpoint - captures model state for resumption"""

    checkpoint_id: str = field(default_factory=lambda: f"ckpt_{uuid.uuid4().hex[:12]}")
    epoch: int = 0
    step: int = 0
    best_metric: float | None = None
    best_metric_name: str = "loss"
    state_dict_path: str = ""
    optimizer_state_path: str = ""
    rng_state: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    checkpoint_type: CheckpointType = CheckpointType.EPOCH
    metrics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    file_size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "epoch": self.epoch,
            "step": self.step,
            "best_metric": self.best_metric,
            "best_metric_name": self.best_metric_name,
            "state_dict_path": self.state_dict_path,
            "optimizer_state_path": self.optimizer_state_path,
            "rng_state": self.rng_state,
            "created_at": self.created_at,
            "checkpoint_type": self.checkpoint_type.value,
            "metrics": self.metrics,
            "metadata": self.metadata,
            "file_size_bytes": self.file_size_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Checkpoint":
        return cls(
            checkpoint_id=data.get("checkpoint_id", f"ckpt_{uuid.uuid4().hex[:12]}"),
            epoch=data.get("epoch", 0),
            step=data.get("step", 0),
            best_metric=data.get("best_metric"),
            best_metric_name=data.get("best_metric_name", "loss"),
            state_dict_path=data.get("state_dict_path", ""),
            optimizer_state_path=data.get("optimizer_state_path", ""),
            rng_state=data.get("rng_state"),
            created_at=data.get("created_at", time.time()),
            checkpoint_type=CheckpointType(data.get("checkpoint_type", "epoch")),
            metrics=data.get("metrics", {}),
            metadata=data.get("metadata", {}),
            file_size_bytes=data.get("file_size_bytes", 0),
        )


@dataclass
class SessionContext:
    """Agent session context - what the agent is currently working on"""

    task_id: str | None = None
    task_type: str | None = None
    task_description: str = ""
    goal_chain: list[dict[str, Any]] = field(default_factory=list)
    current_stage: str = ""
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    injected_skills: list[str] = field(default_factory=list)
    active_context_keys: set[str] = field(default_factory=set)
    custom_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "task_description": self.task_description,
            "goal_chain": self.goal_chain,
            "current_stage": self.current_stage,
            "conversation_history": self.conversation_history,
            "injected_skills": self.injected_skills,
            "active_context_keys": list(self.active_context_keys),
            "custom_context": self.custom_context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionContext":
        return cls(
            task_id=data.get("task_id"),
            task_type=data.get("task_type"),
            task_description=data.get("task_description", ""),
            goal_chain=data.get("goal_chain", []),
            current_stage=data.get("current_stage", ""),
            conversation_history=data.get("conversation_history", []),
            injected_skills=data.get("injected_skills", []),
            active_context_keys=set(data.get("active_context_keys", [])),
            custom_context=data.get("custom_context", {}),
        )

    def increment_update(self, updates: dict[str, Any]) -> "SessionContext":
        """Apply incremental context update without replacing full context"""
        if "task_description" in updates:
            self.task_description = updates["task_description"]
        if "current_stage" in updates:
            self.current_stage = updates["current_stage"]
        if "conversation_history" in updates:
            self.conversation_history = updates["conversation_history"]
        if "injected_skills" in updates:
            self.injected_skills = updates["injected_skills"]
        if "custom_context" in updates:
            self.custom_context.update(updates["custom_context"])
        if "goal_chain" in updates:
            self.goal_chain = updates["goal_chain"]
        return self


@dataclass
class MemoryEntry:
    """Single memory item stored by the agent"""

    memory_id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")
    content: str = ""
    memory_type: str = "observation"
    importance: float = 0.5
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    tags: list[str] = field(default_factory=list)
    embedding_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class StateVersion:
    """Version metadata for state migration tracking"""

    state_version: int = 1
    schema_version: str = "1.0.0"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    migration_history: list[dict[str, Any]] = field(default_factory=list)
    parent_version_id: str | None = None
    change_description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_version": self.state_version,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "migration_history": self.migration_history,
            "parent_version_id": self.parent_version_id,
            "change_description": self.change_description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StateVersion":
        return cls(
            state_version=data.get("state_version", 1),
            schema_version=data.get("schema_version", "1.0.0"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            migration_history=data.get("migration_history", []),
            parent_version_id=data.get("parent_version_id"),
            change_description=data.get("change_description", ""),
        )


@dataclass
class AgentState:
    """Persistent agent state with full lifecycle tracking"""

    agent_id: str
    current_task_id: str | None = None
    session_context: SessionContext = field(default_factory=SessionContext)
    memory: list[MemoryEntry] = field(default_factory=list)
    checkpoint: Checkpoint | None = None
    last_heartbeat: float = field(default_factory=time.time)
    status: AgentStatus = AgentStatus.IDLE
    checkpoints_history: list[Checkpoint] = field(default_factory=list)
    state_version: StateVersion = field(default_factory=StateVersion)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "current_task_id": self.current_task_id,
            "session_context": self.session_context.to_dict(),
            "memory": [m.to_dict() for m in self.memory],
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
            "last_heartbeat": self.last_heartbeat,
            "status": self.status.value,
            "checkpoints_history": [c.to_dict() for c in self.checkpoints_history],
            "state_version": self.state_version.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentState":
        state = cls(
            agent_id=data["agent_id"],
            current_task_id=data.get("current_task_id"),
            session_context=SessionContext.from_dict(data.get("session_context", {})),
            memory=[MemoryEntry.from_dict(m) for m in data.get("memory", [])],
            checkpoint=Checkpoint.from_dict(data["checkpoint"]) if data.get("checkpoint") else None,
            last_heartbeat=data.get("last_heartbeat", time.time()),
            status=AgentStatus(data.get("status", "idle")),
            checkpoints_history=[Checkpoint.from_dict(c) for c in data.get("checkpoints_history", [])],
            state_version=StateVersion.from_dict(data.get("state_version", {})),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            metadata=data.get("metadata", {}),
        )
        return state

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, json_str: str) -> "AgentState":
        return cls.from_dict(json.loads(json_str))

    def update_heartbeat(self):
        self.last_heartbeat = time.time()
        self.updated_at = time.time()

    def add_memory(self, entry: MemoryEntry):
        self.memory.append(entry)
        self.updated_at = time.time()

    def set_checkpoint(self, checkpoint: Checkpoint):
        self.checkpoint = checkpoint
        self.checkpoints_history.append(checkpoint)
        self.updated_at = time.time()

    def add_context_increment(self, updates: dict[str, Any]):
        self.session_context.increment_update(updates)
        self.updated_at = time.time()

    def clone(self, new_agent_id: str | None = None) -> "AgentState":
        """Create a complete clone for A/B testing scenarios"""
        data = self.to_dict()
        if new_agent_id:
            data["agent_id"] = new_agent_id
        clone = AgentState.from_dict(data)
        clone.created_at = time.time()
        clone.updated_at = time.time()
        clone.state_version = StateVersion(
            state_version=1,
            schema_version=self.state_version.schema_version,
            parent_version_id=f"clone_of_{self.agent_id}",
            change_description=f"Cloned from agent {self.agent_id}",
        )
        return clone

    def get_checkpoints_for_rollback(self) -> list[Checkpoint]:
        return sorted(
            self.checkpoints_history,
            key=lambda c: c.created_at,
            reverse=True,
        )

    def rollback_to_checkpoint(self, checkpoint_id: str) -> bool:
        for ckpt in self.checkpoints_history:
            if ckpt.checkpoint_id == checkpoint_id:
                self.checkpoint = ckpt
                self.updated_at = time.time()
                return True
        return False


CURRENT_SCHEMA_VERSION = "1.0.0"

STATE_MIGRATIONS: dict[str, list[Callable[[dict[str, Any]], dict[str, Any]]]] = {}


def register_migration(from_version: str, to_version: str):
    """Decorator to register state migration functions"""

    def decorator(func):
        key = f"{from_version}->{to_version}"
        STATE_MIGRATIONS.setdefault(key, [])
        STATE_MIGRATIONS[key].append(func)
        return func

    return decorator


def migrate_state(data: dict[str, Any], target_version: str = CURRENT_SCHEMA_VERSION) -> dict[str, Any]:
    """Apply registered migrations to bring state data to target version"""
    current = data.get("state_version", {}).get("schema_version", "1.0.0")
    while current != target_version:
        migrators = STATE_MIGRATIONS.get(f"{current}->{target_version}")
        if not migrators:
            break
        for migrator in migrators:
            data = migrator(data)
        current = target_version
    return data
