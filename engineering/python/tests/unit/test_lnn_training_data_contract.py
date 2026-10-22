"""Reject training inputs that would shift columns or leak labels into features."""

import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.api.v1.lnn import _training_executor as executor

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_csv_preserves_feature_and_label_columns(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("x1,x2,y\n1,2,3\n4,5,6\n", encoding="utf-8")
    features, labels, width = await executor._load_training_data(str(path))
    np.testing.assert_array_equal(features, [[1, 2], [4, 5]])
    np.testing.assert_array_equal(labels, [3, 6])
    assert width == 2


@pytest.mark.parametrize("content", [
    "x,y\n1,2\n",  # one row must never become two duplicated samples
    "y\n1\n2\n",  # a label column must never become its own feature
    "x,y\n1,NaN\n2,3\n",
    "x,y\n1,inf\n2,3\n",
    "x1,x2,y\n1,bad,2\n3,4,5\n",
    "x,y\n1,2\n3,4,5\n",
])
async def test_invalid_csv_is_rejected(tmp_path, content):
    path = tmp_path / "data.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        await executor._load_training_data(str(path))


async def test_missing_file_is_not_swallowed(tmp_path):
    with pytest.raises(FileNotFoundError):
        await executor._load_training_data(str(tmp_path / "missing.csv"))


async def test_training_initializes_executor_before_loading_data(monkeypatch):
    from app.api.v1.lnn import services

    monkeypatch.setattr(executor, "_lazy_init_training_stack", lambda: False)
    loader = AsyncMock()
    monkeypatch.setattr(executor, "_load_training_data", loader)
    with pytest.raises(RuntimeError, match="训练不可用"):
        await services.run_training_task_v2(
            "job", "model", "missing.csv", {}, "cpu", asyncio.Event(), AsyncMock()
        )
    loader.assert_not_awaited()


async def test_training_uses_successfully_initialized_executor(monkeypatch):
    from app.api.v1.lnn import services

    initialized = []

    def initialize():
        initialized.append(True)
        return True

    async def load(path):
        assert initialized == [True]
        raise ValueError("invalid input")

    monkeypatch.setattr(executor, "_lazy_init_training_stack", initialize)
    monkeypatch.setattr(executor, "_load_training_data", load)
    with pytest.raises(ValueError, match="invalid input"):
        await services.run_training_task_v2(
            "job", "model", "data.csv", {}, "cpu", asyncio.Event(), AsyncMock()
        )


@pytest.mark.parametrize("missing_tracking", [False, True])
async def test_training_readiness_requires_all_dependencies(monkeypatch, missing_tracking):
    from app.ai.lnn import _research_bridge as bridge

    monkeypatch.setattr(executor, "_HAS_TRAINING_STACK", False)
    monkeypatch.setattr(executor, "_HAS_TORCH", True)
    names = (
        "get_lnn_config_factory", "get_trainer_factory", "get_device_detect",
        "get_mlflow_start_run", "get_mlflow_log_params", "get_mlflow_log_metrics",
        "get_mlflow_log_model", "get_device_optimal_batch_size", "get_device_optimal_num_workers",
    )
    for name in names:
        monkeypatch.setattr(bridge, name, lambda: (lambda: None))
    if missing_tracking:
        monkeypatch.setattr(bridge, "get_mlflow_log_model", lambda: None)
    for name in (
        "LNNConfig", "LNNTrainer", "detect_device", "mlflow_start_run", "mlflow_log_params",
        "mlflow_log_metrics", "mlflow_log_model", "get_optimal_batch_size", "get_optimal_num_workers",
    ):
        monkeypatch.setattr(executor, name, None)
    assert executor._lazy_init_training_stack() is (not missing_tracking)
