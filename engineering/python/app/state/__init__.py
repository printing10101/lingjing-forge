"""状态管理模块（V3.0 重构：拆分自 state_persistence.py）。

模块结构：
  exceptions.py   — StatePersistenceError, StateConflictError, StateNotFoundError
  checkpoint.py   — CheckpointLifecycleManager
  compressor.py   — StateCompressor
  migration.py    — StateMigrationEngine
  manager.py      — StatePersistenceManager
  recovery.py     — StateRecoveryManager

原有 ``from app.state.state_persistence import XXX`` 导入路径通过
``state_persistence.py`` 的兼容 re-export shim 保持可用。
"""

from app.state.exceptions import (
    StatePersistenceError,
    StateConflictError,
    StateNotFoundError,
)
from app.state.checkpoint import (
    CheckpointLifecycleManager,
    HEARTBEAT_INTERVAL_SECONDS,
    CHECKPOINT_MAX_AGE_SECONDS,
    CHECKPOINT_MAX_COUNT,
    MAX_MEMORY_ENTRIES,
    MEMORY_PRUNING_THRESHOLD,
    CONTEXT_COMPRESSION_THRESHOLD_BYTES,
    CHECKPOINT_BASE_DIR,
)
from app.state.compressor import StateCompressor
from app.state.migration import StateMigrationEngine
from app.state.manager import StatePersistenceManager
from app.state.recovery import StateRecoveryManager
