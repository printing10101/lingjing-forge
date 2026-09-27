"""桌面 SQLite 数据库在线备份服务。

面向桌面版（SQLite 主库）的数据安全能力，弥补 backup-recovery.md 仅覆盖服务器版
（cron + S3）的空缺。使用 SQLite 在线备份 API（``sqlite3.Connection.backup()``），
在应用运行期间也能生成一致性快照（自动包含 WAL 未 checkpoint 的内容），
无需停服。

职责：
- 枚举已知数据目录下的所有 SQLite 库（*.db，排除备份/临时文件）
- 创建带时间戳与 SHA-256 完整性清单的备份
- 列出 / 按保留数量清理历史备份
- 恢复到指定目录（默认不覆盖运行中的库，需显式确认）

安全与边界：
- 只读打开源库（``file:...?mode=ro`` 以支持 WAL 一致性），失败不阻断主流程
- 备份失败仅记录告警并继续下一个库，避免单个损坏库拖垮整体备份
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import config

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class BackupManifest:
    backup_id: str
    created_at: str
    # 值类型：name/source/sha256 为 str，size_bytes 为 int（见 create_backup 填充）
    files: list[dict[str, Any]] = field(default_factory=list)
    source_dirs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "files": self.files,
            "source_dirs": self.source_dirs,
        }


def _iter_sqlite_databases(source_dirs: list[Path]) -> list[Path]:
    """枚举源目录下的所有 SQLite 库，排除备份与临时文件。"""
    excluded_suffixes = ("-wal", "-shm", "-journal")
    dbs: list[Path] = []
    seen: set[Path] = set()
    for d in source_dirs:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.db")):
            if p.name.endswith(excluded_suffixes):
                continue
            if "_backup_" in p.name:
                continue
            resolved = p.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            dbs.append(p)
    return dbs


def _default_source_dirs() -> list[Path]:
    """从配置推导默认源目录（桌面版数据分散在多处）。"""
    dirs: list[Path] = []
    for raw in (config.paths.db_path, config.paths.vector_db_path):
        parent = Path(raw).expanduser().parent
        if str(parent) not in {str(d) for d in dirs}:
            dirs.append(parent)
    # 桌面运行时历史数据目录（模块级 SQLite 落点）
    for extra in ("./output/data", "./data"):
        p = Path(extra).resolve()
        if str(p) not in {str(d) for d in dirs}:
            dirs.append(p)
    return dirs


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class BackupService:
    """SQLite 在线备份服务。"""

    def __init__(self, backup_dir: str | None = None):
        self.backup_dir = Path(backup_dir or config.paths.backup_dir).expanduser()
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def discover_databases(self, source_dirs: list[Path] | None = None) -> list[Path]:
        dirs = source_dirs or _default_source_dirs()
        return _iter_sqlite_databases(dirs)

    def create_backup(self, source_dirs: list[Path] | None = None) -> dict[str, Any]:
        """创建一次全量备份，返回 manifest dict。"""
        dirs = source_dirs or _default_source_dirs()
        dbs = _iter_sqlite_databases(dirs)
        if not dbs:
            raise ValueError(
                "[备份失败] 未发现任何 SQLite 库。建议操作：检查 DB_PATH/VECTOR_DB_PATH "
                "配置指向的数据目录是否存在且包含 *.db 文件。"
            )

        # 时间戳 + 随机后缀：Windows datetime 分辨率不足（紧密循环内微秒相同），
        # 追加 6 位随机十六进制保证同秒内多次备份 ID 唯一（避免互相覆盖）
        backup_id = f"{_now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        dest_dir = self.backup_dir / backup_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        manifest = BackupManifest(
            backup_id=backup_id,
            created_at=_now().isoformat(),
            source_dirs=[str(d) for d in dirs],
        )

        for src in dbs:
            dst = dest_dir / src.name
            try:
                self._backup_single(src, dst)
                manifest.files.append(
                    {
                        "name": src.name,
                        "source": str(src),
                        "sha256": _compute_sha256(dst),
                        "size_bytes": dst.stat().st_size,
                    }
                )
                logger.info("备份成功: %s -> %s", src, dst)
            except (sqlite3.Error, OSError) as exc:
                # 单个库失败不阻断整体备份
                logger.warning("备份跳过 %s: %s", src, exc)

        if not manifest.files:
            # 全部失败则清理空目录并报错
            try:
                dest_dir.rmdir()
            except OSError:
                pass
            raise RuntimeError(
                "[备份失败] 所有 SQLite 库备份均失败。建议操作：检查磁盘空间与文件权限，"
                "并查看上方 warning 日志定位首因。"
            )

        manifest_path = dest_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("备份完成: %s（%d 个库）", backup_id, len(manifest.files))
        return manifest.to_dict()

    @staticmethod
    def _backup_single(src: Path, dst: Path) -> None:
        """使用 SQLite 在线备份 API 生成一致性快照。

        以只读模式打开源库（URI 模式 + immutable=0 以便共享 WAL），
        通过 ``Connection.backup()`` 复制到目标，避免直接拷贝可能截断的 .db 文件。
        """
        uri = f"file:{src.as_posix()}?mode=ro"
        # 注意：sqlite3.Connection 的 with 只提交事务、不关闭连接；
        # 必须用 closing() 显式 close，否则备份文件句柄不释放，
        # 导致 prune 的 rmtree 无法删除目录（Windows 下文件被锁定）。
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as src_conn:
            src_conn.execute("PRAGMA query_only = ON")
            with closing(sqlite3.connect(dst)) as dst_conn:
                src_conn.backup(dst_conn)

    def list_backups(self) -> list[dict[str, Any]]:
        """列出历史备份（按时间倒序）。"""
        backups: list[dict[str, Any]] = []
        for d in sorted(self.backup_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            manifest_path = d / "manifest.json"
            if manifest_path.exists():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    data["backup_dir"] = str(d)
                    backups.append(data)
                    continue
                except (json.JSONDecodeError, OSError):
                    pass
            backups.append(
                {
                    "backup_id": d.name,
                    "backup_dir": str(d),
                    "created_at": None,
                    "files": [],
                }
            )
        return backups

    def prune_backups(self, keep: int = 7) -> int:
        """按保留数量清理旧备份，返回删除数量。"""
        backups = self.list_backups()
        removed = 0
        for b in backups[keep:]:
            import shutil

            shutil.rmtree(b["backup_dir"], ignore_errors=True)
            removed += 1
            logger.info("清理旧备份: %s", b["backup_id"])
        return removed

    def restore_backup(self, backup_id: str, target_dir: str) -> dict[str, Any]:
        """将备份恢复到指定目录（不覆盖目标中已有同名文件，避免误损运行中数据）。

        Returns:
            {"restored": [文件名...], "skipped": [文件名...]}
        """
        src_dir = self.backup_dir / backup_id
        if not src_dir.exists():
            raise FileNotFoundError(f"[恢复失败] 备份 {backup_id} 不存在。建议操作：先调用 list 确认备份 ID。")
        target = Path(target_dir).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        restored: list[str] = []
        skipped: list[str] = []
        for f in sorted(src_dir.iterdir()):
            if f.name == "manifest.json" or not f.is_file():
                continue
            dst = target / f.name
            if dst.exists():
                skipped.append(f.name)
                continue
            import shutil

            shutil.copy2(f, dst)
            restored.append(f.name)
        return {"restored": restored, "skipped": skipped}


# 模块级单例（与 app 其它服务保持一致）
_backup_service: BackupService | None = None


def get_backup_service() -> BackupService:
    global _backup_service
    if _backup_service is None:
        _backup_service = BackupService()
    return _backup_service
