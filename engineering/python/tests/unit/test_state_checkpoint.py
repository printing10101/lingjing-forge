"""state 模块覆盖率补强测试（checkpoint / compressor）。

覆盖：
- app/state/checkpoint.py：CheckpointLifecycleManager 文件生命周期管理
- app/state/compressor.py：StateCompressor 压缩/解压/上下文紧凑化
"""

from __future__ import annotations

import os
import time
import zlib

import pytest

from app.state.checkpoint import (
    CheckpointLifecycleManager,
    CHECKPOINT_BASE_DIR,
)
from app.state.compressor import StateCompressor
from app.models.agent_state import SessionContext

pytestmark = pytest.mark.unit


def _make_context(history_size: int = 3) -> SessionContext:
    ctx = SessionContext(
        task_id="task-1",
        task_type="chatter_prediction",
        task_description="测试会话",
        conversation_history=[{"role": "user", "content": f"消息 {i}"} for i in range(history_size)],
    )
    return ctx


# CheckpointLifecycleManager


class TestCheckpointLifecycle:
    def test_init_creates_base_dir(self, tmp_path):
        mgr = CheckpointLifecycleManager(str(tmp_path / "ckpt"))
        assert (tmp_path / "ckpt").is_dir()

    def test_get_agent_dir_creates(self, tmp_path):
        mgr = CheckpointLifecycleManager(str(tmp_path / "ckpt"))
        d = mgr.get_agent_checkpoint_dir("agent-x")
        assert d.is_dir()
        assert d.name == "agent-x"

    def test_save_and_load_roundtrip(self, tmp_path):
        mgr = CheckpointLifecycleManager(str(tmp_path / "ckpt"))
        data = b"model-weights-binary-12345"
        path = mgr.save_checkpoint_file("agent-a", "ckpt-1", data)
        assert path.exists()
        loaded = mgr.load_checkpoint_file("agent-a", "ckpt-1")
        assert loaded == data

    def test_load_missing_returns_none(self, tmp_path):
        mgr = CheckpointLifecycleManager(str(tmp_path / "ckpt"))
        assert mgr.load_checkpoint_file("agent-a", "nope") is None

    def test_remove_checkpoint(self, tmp_path):
        mgr = CheckpointLifecycleManager(str(tmp_path / "ckpt"))
        mgr.save_checkpoint_file("agent-a", "ckpt-1", b"data")
        mgr.remove_checkpoint("agent-a", "ckpt-1")
        assert mgr.load_checkpoint_file("agent-a", "ckpt-1") is None
        # 二次删除不抛错
        mgr.remove_checkpoint("agent-a", "ckpt-1")

    def test_size_bytes(self, tmp_path):
        mgr = CheckpointLifecycleManager(str(tmp_path / "ckpt"))
        mgr.save_checkpoint_file("agent-a", "ckpt-1", b"x" * 100)
        mgr.save_checkpoint_file("agent-a", "ckpt-2", b"y" * 50)
        # zlib 压缩后 size > 0
        assert mgr.size_bytes("agent-a") > 0

    def test_cleanup_by_age(self, tmp_path):
        mgr = CheckpointLifecycleManager(str(tmp_path / "ckpt"))
        mgr.save_checkpoint_file("agent-a", "old", b"data1")
        old_path = mgr.get_checkpoint_path("agent-a", "old")
        # 把文件 mtime 改到 10 天前
        old_ts = time.time() - 10 * 86400
        os.utime(old_path, (old_ts, old_ts))
        mgr.save_checkpoint_file("agent-a", "new", b"data2")
        removed = mgr.cleanup_agent_checkpoints("agent-a", max_age_seconds=3600, max_count=10)
        assert removed >= 1
        assert mgr.load_checkpoint_file("agent-a", "old") is None
        assert mgr.load_checkpoint_file("agent-a", "new") is not None

    def test_cleanup_by_count(self, tmp_path):
        mgr = CheckpointLifecycleManager(str(tmp_path / "ckpt"))
        for i in range(5):
            mgr.save_checkpoint_file("agent-a", f"ckpt-{i}", b"data")
        removed = mgr.cleanup_agent_checkpoints("agent-a", max_age_seconds=999999, max_count=2)
        assert removed == 3  # 保留最新的 2 个


# StateCompressor


class TestStateCompressor:
    def test_should_compress_small_context_false(self):
        ctx = _make_context(history_size=2)
        assert StateCompressor.should_compress(ctx) is False

    def test_compress_decompress_roundtrip(self):
        ctx = _make_context(history_size=5)
        compressed = StateCompressor.compress_context(ctx)
        # zlib 压缩后是 bytes
        assert isinstance(compressed, bytes)
        restored = StateCompressor.decompress_context(compressed)
        assert restored.task_id == ctx.task_id
        assert len(restored.conversation_history) == len(ctx.conversation_history)

    def test_compact_conversation_history_trims(self):
        ctx = _make_context(history_size=300)
        compacted = StateCompressor.compact_conversation_history(ctx, max_entries=50)
        assert len(compacted.conversation_history) == 50
        # 保留的是最后 50 条
        assert compacted.conversation_history[-1]["content"] == "消息 299"

    def test_compact_conversation_history_noop_when_small(self):
        ctx = _make_context(history_size=10)
        result = StateCompressor.compact_conversation_history(ctx, max_entries=200)
        assert result is ctx  # 原对象返回
        assert len(result.conversation_history) == 10
