"""backup_service 单元测试。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services.backup_service import BackupService


def _make_db(path: Path, table: str, rows: int) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, v TEXT)")
        for i in range(rows):
            conn.execute(f"INSERT INTO {table} (id, v) VALUES (?, ?)", (i, f"v{i}"))
        conn.commit()


class TestBackupService:
    def test_create_backup_snapshot(self, tmp_path):
        src = tmp_path / "data"
        src.mkdir()
        _make_db(src / "app.db", "items", 5)
        _make_db(src / "rules.db", "rules", 3)
        svc = BackupService(backup_dir=str(tmp_path / "backups"))

        manifest = svc.create_backup(source_dirs=[src])

        assert manifest["backup_id"]
        assert len(manifest["files"]) == 2
        names = {f["name"] for f in manifest["files"]}
        assert names == {"app.db", "rules.db"}
        # manifest.json 落盘且含 sha256
        backup_dir = tmp_path / "backups" / manifest["backup_id"]
        assert (backup_dir / "manifest.json").exists()
        for f in manifest["files"]:
            assert len(f["sha256"]) == 64
            assert (backup_dir / f["name"]).exists()

    def test_backup_content_is_consistent(self, tmp_path):
        src = tmp_path / "data"
        src.mkdir()
        _make_db(src / "app.db", "items", 10)
        svc = BackupService(backup_dir=str(tmp_path / "backups"))

        manifest = svc.create_backup(source_dirs=[src])
        backup_dir = tmp_path / "backups" / manifest["backup_id"]

        # 备份文件是有效的 SQLite 库且数据完整
        with sqlite3.connect(backup_dir / "app.db") as conn:
            count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert count == 10

    def test_excludes_backup_and_wal_files(self, tmp_path):
        src = tmp_path / "data"
        src.mkdir()
        _make_db(src / "app.db", "items", 1)
        # 干扰文件：备份副本与 wal/shm 不应被收集
        _make_db(src / "app_backup_2026.db", "items", 1)
        (src / "app.db-wal").write_bytes(b"")
        (src / "app.db-shm").write_bytes(b"")
        svc = BackupService(backup_dir=str(tmp_path / "backups"))

        manifest = svc.create_backup(source_dirs=[src])

        names = [f["name"] for f in manifest["files"]]
        assert names == ["app.db"]

    def test_create_backup_raises_when_no_db(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        svc = BackupService(backup_dir=str(tmp_path / "backups"))
        with pytest.raises(ValueError, match="未发现任何 SQLite 库"):
            svc.create_backup(source_dirs=[empty])

    def test_list_and_prune(self, tmp_path):
        src = tmp_path / "data"
        src.mkdir()
        _make_db(src / "app.db", "items", 1)
        svc = BackupService(backup_dir=str(tmp_path / "backups"))

        for _ in range(3):
            svc.create_backup(source_dirs=[src])

        backups = svc.list_backups()
        assert len(backups) == 3
        # 按时间倒序
        assert backups[0]["backup_id"] > backups[-1]["backup_id"]

        removed = svc.prune_backups(keep=1)
        assert removed == 2
        assert len(svc.list_backups()) == 1

    def test_restore_backup(self, tmp_path):
        src = tmp_path / "data"
        src.mkdir()
        _make_db(src / "app.db", "items", 4)
        svc = BackupService(backup_dir=str(tmp_path / "backups"))
        manifest = svc.create_backup(source_dirs=[src])

        target = tmp_path / "restore"
        result = svc.restore_backup(manifest["backup_id"], str(target))

        assert result["restored"] == ["app.db"]
        with sqlite3.connect(target / "app.db") as conn:
            count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert count == 4

    def test_restore_skips_existing(self, tmp_path):
        src = tmp_path / "data"
        src.mkdir()
        _make_db(src / "app.db", "items", 2)
        svc = BackupService(backup_dir=str(tmp_path / "backups"))
        manifest = svc.create_backup(source_dirs=[src])

        target = tmp_path / "restore"
        target.mkdir()
        _make_db(target / "app.db", "other", 99)

        result = svc.restore_backup(manifest["backup_id"], str(target))
        assert result["restored"] == []
        assert result["skipped"] == ["app.db"]
        # 已有文件未被覆盖
        with sqlite3.connect(target / "app.db") as conn:
            count = conn.execute("SELECT COUNT(*) FROM other").fetchone()[0]
        assert count == 99


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
