"""提示词版本持久化（自进化 M1）。

PromptRegistry 是进程内机制，进程重启即失忆。本模块把演化产生的
候选/应用版本落到磁盘（``python/data/prompt_versions.json``，env
``PROMPT_VERSIONS_STORE`` 覆盖），使：

- ``applied`` 状态的版本在重启后由 ``get_prompt_registry()`` 重新播种，
  线上提示词不回退；
- ``proposed`` 状态的提案跨进程可见，人工审核（apply/reject）不丢；
- ``rejected`` / ``rolled_back`` 留痕，迭代历史可审计。

写入策略：整文件原子替换（tmp + os.replace），记录量级 ~个位数，
无需 SQLite。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "get_store_path",
    "load_records",
    "upsert_record",
    "update_status",
    "list_applied",
    "find_record",
]

#: 合法状态机：proposed → applied | rejected；applied → rolled_back
VALID_RECORD_STATUSES = ("proposed", "applied", "rejected", "rolled_back")


def get_store_path() -> Path:
    """版本存储文件路径（env ``PROMPT_VERSIONS_STORE`` 优先）。"""
    env_path = os.environ.get("PROMPT_VERSIONS_STORE")
    if env_path:
        return Path(env_path)
    # app/ai/prompts/persistence.py → parents[3] = engineering/python
    return Path(__file__).resolve().parents[3] / "data" / "prompt_versions.json"


def load_records() -> list[dict[str, Any]]:
    """读取全部版本记录。文件缺失返回空表；损坏时告警并返回空表（不抛出）。"""
    path = get_store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("提示词版本存储损坏，忽略（%s）: %s", path, e)
        return []
    if not isinstance(data, list):
        logger.warning("提示词版本存储格式非法（顶层应为数组），忽略: %s", path)
        return []
    return [rec for rec in data if isinstance(rec, dict)]


def save_records(records: list[dict[str, Any]]) -> None:
    """原子写回全部记录。"""
    path = get_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def upsert_record(record: dict[str, Any]) -> dict[str, Any]:
    """按 (prompt_id, version) 覆盖写一条记录（含 created_at 兜底）。"""
    records = load_records()
    if not record.get("created_at"):
        record["created_at"] = time.time()
    key = (record.get("prompt_id"), record.get("version"))
    records = [r for r in records if (r.get("prompt_id"), r.get("version")) != key]
    records.append(record)
    save_records(records)
    return record


def update_status(
    prompt_id: str,
    version: int,
    status: str,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """更新指定版本的记录状态（gate 结果可选一并落盘）。

    Returns:
        更新后的记录；记录不存在返回 None。
    """
    if status not in VALID_RECORD_STATUSES:
        raise ValueError(f"非法状态: {status}，合法值: {VALID_RECORD_STATUSES}")
    records = load_records()
    updated: dict[str, Any] | None = None
    for rec in records:
        if rec.get("prompt_id") == prompt_id and rec.get("version") == version:
            rec["status"] = status
            if gate is not None:
                rec["gate"] = gate
            rec["updated_at"] = time.time()
            updated = rec
    if updated is not None:
        save_records(records)
    return updated


def list_applied() -> list[dict[str, Any]]:
    """全部 applied 状态的记录（重启后重新播种用）。"""
    return [r for r in load_records() if r.get("status") == "applied"]


def find_record(prompt_id: str, version: int) -> dict[str, Any] | None:
    """按 (prompt_id, version) 查记录。"""
    for rec in load_records():
        if rec.get("prompt_id") == prompt_id and rec.get("version") == version:
            return rec
    return None
