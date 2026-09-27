"""
Pytest configuration for lnn_uncertain tests.

This conftest.py pre-injects mock modules into sys.modules to prevent
import errors from the existing codebase (missing TrainingTask model).
"""

import sys
from unittest.mock import MagicMock

# Pre-inject mock for app.tasks.task_system to avoid TrainingTask import error
# This must happen before any app.api imports are triggered
if "app.tasks.task_system" not in sys.modules:
    mock_task_system = MagicMock()
    mock_task_system.AsyncTaskManager = MagicMock()
    mock_task_system.TrainingTask = MagicMock()
    mock_task_system.TaskStatusEnum = MagicMock()
    sys.modules["app.tasks.task_system"] = mock_task_system

# Also mock app.database.models.TrainingTask if needed
try:
    from app.database import models as db_models

    if not hasattr(db_models, "TrainingTask"):
        db_models.TrainingTask = MagicMock()  # type: ignore[misc]  # 测试运行时补 mock
    if not hasattr(db_models, "TaskStatusEnum"):
        db_models.TaskStatusEnum = MagicMock()  # type: ignore[misc]  # 测试运行时补 mock
except ImportError:
    pass
