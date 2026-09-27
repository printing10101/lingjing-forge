"""VectorStore 初始化自愈测试：legacy/corrupt 库自动归档重建.

背景（2026-09 引擎验证修复收尾）：chromadb 1.0.0 的 Rust sqlite 层对
legacy schema 库触发 ``PanicException``（不继承 Exception）。此前归档
为一次性手工操作，客户机从旧版本升级仍会复现"向量库起不来"。本套件
锁定 ``_ensure_client`` 的自动归档→重建→（仍失败才报错）自愈路径。

全部用例 mock chromadb 模块，不依赖真实 chromadb 安装；所有路径均在
pytest 管理的 tmp_path 内。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from app.rag.vector_store import VectorStore

_MARKER = "chroma.sqlite3"


class PanicException(BaseException):
    """模拟 pyo3 PanicException（名字必须一致：_ensure_client 按类型名识别放行）。"""


def _install_fake_chromadb(monkeypatch: pytest.MonkeyPatch, behaviors: list) -> list:
    """注入 fake chromadb 模块；behaviors 为每次 PersistentClient 调用的脚本。

    每个元素是异常实例（抛出）或任意返回值（成功）。返回调用记录列表。
    """
    calls: list = []
    fake_module = types.ModuleType("chromadb")

    def fake_persistent_client(path: str, *_args, **_kwargs):
        calls.append(path)
        behavior = behaviors.pop(0) if behaviors else RuntimeError("no script")
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior

    fake_module.PersistentClient = fake_persistent_client
    monkeypatch.setitem(sys.modules, "chromadb", fake_module)
    return calls


def _make_store(tmp_path: Path, marker: bool = True) -> tuple[VectorStore, Path]:
    """在 tmp_path 下建 persist 目录，返回 (store, 目录 Path)。"""
    persist = tmp_path / "chroma_db"
    persist.mkdir(parents=True, exist_ok=True)
    if marker:
        (persist / _MARKER).write_bytes(b"legacy-schema-bytes")
    return VectorStore(persist_directory=str(persist)), persist


def _legacy_archives(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.name.startswith("chroma_db.legacy-"))


def test_panic_archives_legacy_dir_and_rebuilds(tmp_path: Path, monkeypatch):
    """首次 panic → 归档旧库（含原文件）→ 重建空库成功。"""
    store, persist = _make_store(tmp_path)
    fake_client = object()
    calls = _install_fake_chromadb(
        monkeypatch, [PanicException("legacy schema migration panic"), fake_client]
    )

    store._ensure_client()

    assert store._client is fake_client
    assert len(calls) == 2  # 归档后重试恰好一次
    assert persist.is_dir()  # 重建路径存在
    archives = _legacy_archives(tmp_path)
    assert len(archives) == 1
    # 归档目录完整保留旧库文件（零数据损失可回溯）
    assert (archives[0] / _MARKER).is_file()


def test_archive_failure_raises_original_error(tmp_path: Path, monkeypatch):
    """归档失败（返回 None）→ 保留原目录并抛结构化 RuntimeError。"""
    store, persist = _make_store(tmp_path)
    calls = _install_fake_chromadb(monkeypatch, [PanicException("panic")])
    monkeypatch.setattr(store, "_archive_persist_dir", lambda: None)

    with pytest.raises(RuntimeError, match="向量存储初始化失败"):
        store._ensure_client()

    assert store._client is None
    assert len(calls) == 1  # 归档失败不重试
    assert (persist / _MARKER).is_file()  # 原目录未被破坏


def test_retry_failure_after_archive_reports_both(tmp_path: Path, monkeypatch):
    """归档成功但重建仍失败 → 报错信息含"归档重建后"。"""
    store, _ = _make_store(tmp_path)
    _install_fake_chromadb(monkeypatch, [PanicException("panic"), OSError("disk full")])

    with pytest.raises(RuntimeError, match="归档重建后"):
        store._ensure_client()

    assert store._client is None


def test_keyboard_interrupt_propagates_without_archive(tmp_path: Path, monkeypatch):
    """Ctrl+C / SystemExit 必须原样穿透，不得触发归档吞掉。"""
    store, persist = _make_store(tmp_path)
    calls = _install_fake_chromadb(monkeypatch, [KeyboardInterrupt()])

    with pytest.raises(KeyboardInterrupt):
        store._ensure_client()

    assert calls == [str(persist)]  # 只尝试一次
    assert (persist / _MARKER).is_file()  # 目录未被归档


def test_normal_init_never_archives(tmp_path: Path, monkeypatch):
    """正常初始化零副作用：不产生任何 legacy 归档目录。"""
    store, _ = _make_store(tmp_path)
    fake_client = object()
    _install_fake_chromadb(monkeypatch, [fake_client])

    store._ensure_client()

    assert store._client is fake_client
    assert _legacy_archives(tmp_path) == []


def test_archive_name_collision_gets_suffix(tmp_path: Path, monkeypatch):
    """同名归档目录已存在时自动加序号后缀，不覆盖既有备份。"""
    store, persist = _make_store(tmp_path)
    existing = persist.with_name(f"{persist.name}.legacy-19990101_000000")
    existing.mkdir()
    # 固定时间戳使 base 名与既有目录冲突
    monkeypatch.setattr("app.rag.vector_store.time.strftime", lambda *_: "19990101_000000")
    calls = _install_fake_chromadb(monkeypatch, [PanicException("panic"), object()])

    store._ensure_client()

    assert len(calls) == 2
    assert existing.is_dir()  # 既有备份未被覆盖
    assert persist.with_name(f"{persist.name}.legacy-19990101_000000_2").is_dir()  # 新归档落序号后缀
