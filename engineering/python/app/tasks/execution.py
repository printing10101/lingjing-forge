"""
Execution and Recovery Module

Implements standardized task execution flow with adapter pattern for LNN inference,
training, and analysis tasks. Features structured logging, cost tracking,
session persistence, and orphaned task recovery.

本模块为门面：实现已拆分至 _session_manager / _task_executor / _engine。
"""

from __future__ import annotations

from app.tasks._engine import (  # noqa: F401
    ExecutionEngine,
    get_engine,
    get_execution_engine,
    init_execution_engine,
)
from app.tasks._execution_models import (  # noqa: F401
    ExecutionResult,
    ExecutionSession,
    ExecutionStatus,
    StructuredLogger,
)
from app.tasks._session_manager import SessionManager  # noqa: F401
from app.tasks._task_executor import TaskExecutor  # noqa: F401
