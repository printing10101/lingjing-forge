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

import json
import logging
import zlib

# shared imports moved to state/__init__.py
from app.models.agent_state import (
    SessionContext,
)

HEARTBEAT_INTERVAL_SECONDS = 15 * 60
CHECKPOINT_MAX_AGE_SECONDS = 7 * 24 * 3600
CHECKPOINT_MAX_COUNT = 50
MAX_MEMORY_ENTRIES = 1000
MEMORY_PRUNING_THRESHOLD = 800
CONTEXT_COMPRESSION_THRESHOLD_BYTES = 1024 * 100
CHECKPOINT_BASE_DIR = "data/checkpoints"

logger = logging.getLogger(__name__)


class StateCompressor:
    """Compresses large session contexts to balance performance and storage"""

    @staticmethod
    def should_compress(context: SessionContext) -> bool:
        raw = json.dumps(context.to_dict(), ensure_ascii=False).encode("utf-8")
        return len(raw) > CONTEXT_COMPRESSION_THRESHOLD_BYTES

    @staticmethod
    def compress_context(context: SessionContext) -> bytes:
        raw = json.dumps(context.to_dict(), ensure_ascii=False).encode("utf-8")
        return zlib.compress(raw, level=6)

    @staticmethod
    def decompress_context(data: bytes) -> SessionContext:
        raw = zlib.decompress(data)
        return SessionContext.from_dict(json.loads(raw.decode("utf-8")))

    @staticmethod
    def compact_conversation_history(context: SessionContext, max_entries: int = 200) -> SessionContext:
        if len(context.conversation_history) <= max_entries:
            return context
        context.conversation_history = context.conversation_history[-max_entries:]
        return context
