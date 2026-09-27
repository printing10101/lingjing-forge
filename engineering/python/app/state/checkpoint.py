"""
State Persistence System - Persistent Agent State & Session Recovery

References Paperclip's Persistent Agent State design:
- Three-tier storage: Redis (cache) + PostgreSQL (metadata) + Filesystem (checkpoints)
- Dual-trigger save: timer heartbeat (15min) + event-driven (epoch complete, status change)
- Async non-blocking save operations
- State compression for large contexts
- Checkpoint lifecycle management
- Multi-agent state isolation
- Permission-controlled state operations
- Comprehensive audit logging
"""

from __future__ import annotations

import logging
import time
import zlib
from pathlib import Path


# shared imports moved to state/__init__.py

HEARTBEAT_INTERVAL_SECONDS = 15 * 60
CHECKPOINT_MAX_AGE_SECONDS = 7 * 24 * 3600
CHECKPOINT_MAX_COUNT = 50
MAX_MEMORY_ENTRIES = 1000
MEMORY_PRUNING_THRESHOLD = 800
CONTEXT_COMPRESSION_THRESHOLD_BYTES = 1024 * 100
CHECKPOINT_BASE_DIR = "data/checkpoints"

logger = logging.getLogger(__name__)


class CheckpointLifecycleManager:
    """Manages checkpoint file lifecycle to prevent disk space exhaustion"""

    def __init__(self, base_dir: str = CHECKPOINT_BASE_DIR):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_agent_checkpoint_dir(self, agent_id: str) -> Path:
        agent_dir = self.base_dir / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir

    def cleanup_agent_checkpoints(
        self,
        agent_id: str,
        max_age_seconds: float = CHECKPOINT_MAX_AGE_SECONDS,
        max_count: int = CHECKPOINT_MAX_COUNT,
    ) -> int:
        agent_dir = self.get_agent_checkpoint_dir(agent_id)
        now = time.time()
        removed = 0
        files = sorted(
            agent_dir.glob("*.pt"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for f in files:
            if f.stat().st_mtime < now - max_age_seconds:
                f.unlink(missing_ok=True)
                removed += 1
        remaining = sorted(
            agent_dir.glob("*.pt"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for f in remaining[max_count:]:
            f.unlink(missing_ok=True)
            removed += 1
        return removed

    def get_checkpoint_path(self, agent_id: str, checkpoint_id: str) -> Path:
        return self.get_agent_checkpoint_dir(agent_id) / f"{checkpoint_id}.pt"

    def size_bytes(self, agent_id: str) -> int:
        agent_dir = self.get_agent_checkpoint_dir(agent_id)
        return sum(f.stat().st_size for f in agent_dir.glob("*.pt"))

    def save_checkpoint_file(self, agent_id: str, checkpoint_id: str, data: bytes) -> Path:
        path = self.get_checkpoint_path(agent_id, checkpoint_id)
        compressed = zlib.compress(data, level=6)
        path.write_bytes(compressed)
        return path

    def load_checkpoint_file(self, agent_id: str, checkpoint_id: str) -> bytes | None:
        path = self.get_checkpoint_path(agent_id, checkpoint_id)
        if not path.exists():
            return None
        compressed = path.read_bytes()
        return zlib.decompress(compressed)

    def remove_checkpoint(self, agent_id: str, checkpoint_id: str):
        path = self.get_checkpoint_path(agent_id, checkpoint_id)
        path.unlink(missing_ok=True)
