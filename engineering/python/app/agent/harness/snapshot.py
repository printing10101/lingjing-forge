"""Harness 快照（自进化 M1 · 把组件版本绑成整体）。

背景：M0 已把提示词收编为 ``PromptRegistry`` 的 ``prompt_id + version``，
失败案例与一次通过率进了 ``FailureCaseStore``，执行留痕进了
``TrajectoryStore``。但「改一版」目前是逐条 prompt 热更新，缺一个对象
来回答：**这一整套配置是谁、和上一套差在哪、凭什么留下**。本模块补这层。

三条设计约束：
- 快照只读已有存储，不自建第二套真源（prompt 正文仍在 ``PromptRegistry``）。
- 快照可序列化为 JSON，指纹取规范序列化的 sha256 前 12 位，供 trace /
  失败案例关联使用。
- ``mechanism`` 为**必填**：一条改动必须能说出「预期改善哪类失败、靠什么
  机制」，否则不允许进入注册表。这是为了防止「分数涨了但说不清为什么」
  的编辑混进来（对应 Harness² 的 critic 关卡）。

零第三方依赖，仅标准库。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "HarnessSnapshot",
    "HarnessRegistry",
    "capture_snapshot",
    "get_harness_registry",
    "reset_harness_registry",
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class HarnessSnapshot:
    """一套生效中的 harness 配置。

    Attributes:
        harness_id: 人类可读标识（如 ``h-20261023-a``），全局唯一。
        prompt_versions: ``prompt_id -> version``。只记录版本号，正文不入快照。
        notes: 非 prompt 组件的自由描述（工具描述摘要、memory 版本、开关等）。
        mechanism: **必填**。本版预期改善哪类失败、通过什么机制。
        parent_id: 由哪一版演化而来；基线留空。
        created_at: ISO 时间戳。
    """

    harness_id: str
    prompt_versions: dict[str, int] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    mechanism: str = ""
    parent_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.harness_id or not self.harness_id.strip():
            raise ValueError("harness_id 不能为空")
        if not self.mechanism or not self.mechanism.strip():
            raise ValueError(
                f"harness {self.harness_id!r} 缺少 mechanism："
                "必须写明预期改善的失败类型与作用机制，否则不予登记"
            )
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    def canonical(self) -> dict[str, Any]:
        """参与指纹计算的规范形式（时间戳不参与，便于内容相同则指纹相同）。"""
        return {
            "prompt_versions": {k: int(v) for k, v in sorted(self.prompt_versions.items())},
            "notes": {k: str(v) for k, v in sorted(self.notes.items())},
            "mechanism": self.mechanism.strip(),
        }

    def fingerprint(self) -> str:
        """内容指纹（sha256 前 12 位）。同一内容不同时间登记，指纹一致。"""
        payload = json.dumps(self.canonical(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            "prompt_versions": dict(self.prompt_versions),
            "notes": dict(self.notes),
            "mechanism": self.mechanism,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HarnessSnapshot:
        return cls(
            harness_id=str(data["harness_id"]),
            prompt_versions={str(k): int(v) for k, v in (data.get("prompt_versions") or {}).items()},
            notes={str(k): str(v) for k, v in (data.get("notes") or {}).items()},
            mechanism=str(data.get("mechanism") or ""),
            parent_id=str(data.get("parent_id") or ""),
            created_at=str(data.get("created_at") or ""),
        )


class HarnessRegistry:
    """快照注册表（JSON 文件持久化，原子写）。

    同一 ``harness_id`` 重复登记视为覆盖（迭代实验用），但会保留
    ``created_at`` 与 ``parent_id`` 的一致性检查：覆盖时若 mechanism 不同
    则拒绝，避免「同名不同义」污染实验记录。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else _default_path()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"current": "", "items": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"current": "", "items": {}}
        if not isinstance(data, dict):
            return {"current": "", "items": {}}
        data.setdefault("current", "")
        data.setdefault("items", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    def register(self, snapshot: HarnessSnapshot) -> HarnessSnapshot:
        """登记（或覆盖）一条快照。"""
        with self._lock:
            data = self._load()
            old = data["items"].get(snapshot.harness_id)
            if old and old.get("mechanism") != snapshot.mechanism:
                raise ValueError(
                    f"harness_id {snapshot.harness_id!r} 已存在且 mechanism 不同；"
                    "换 ID 而不是改语义"
                )
            data["items"][snapshot.harness_id] = snapshot.to_dict()
            self._save(data)
        return snapshot

    def get(self, harness_id: str) -> HarnessSnapshot:
        data = self._load()
        raw = data["items"].get(harness_id)
        if raw is None:
            raise KeyError(f"harness 未登记: {harness_id}")
        return HarnessSnapshot.from_dict(raw)

    def list_all(self) -> list[HarnessSnapshot]:
        data = self._load()
        return [HarnessSnapshot.from_dict(v) for v in data["items"].values()]

    def current(self) -> HarnessSnapshot | None:
        cid = self._load().get("current") or ""
        if not cid:
            return None
        try:
            return self.get(cid)
        except KeyError:
            return None

    def set_current(self, harness_id: str) -> HarnessSnapshot:
        """切换当前生效快照（等价于一次发布）。"""
        snap = self.get(harness_id)
        with self._lock:
            data = self._load()
            data["current"] = harness_id
            self._save(data)
        return snap


def capture_snapshot(
    prompt_registry: Any,
    prompt_ids: list[str],
    harness_id: str,
    *,
    mechanism: str,
    parent_id: str = "",
    notes: dict[str, str] | None = None,
) -> HarnessSnapshot:
    """从 ``PromptRegistry`` 抓当前各 prompt 的最新版本，组装成快照。

    Args:
        prompt_registry: ``app.ai.prompts.PromptRegistry`` 实例。
        prompt_ids: 纳入本快照的 prompt_id 清单（显式列出，不做隐式枚举）。
        harness_id: 快照标识。
        mechanism: 必填，见 ``HarnessSnapshot``。
        parent_id: 父快照。
        notes: 非 prompt 组件的描述。

    Raises:
        KeyError: 某个 prompt_id 未注册。
    """
    versions: dict[str, int] = {}
    for pid in prompt_ids:
        entry = prompt_registry.get(pid)
        versions[str(pid)] = int(entry.version)
    return HarnessSnapshot(
        harness_id=harness_id,
        prompt_versions=versions,
        notes=dict(notes or {}),
        mechanism=mechanism,
        parent_id=parent_id,
    )


def _default_path() -> Path:
    """约定 ``python/data/harness_snapshots.json``，``HARNESS_REGISTRY`` 可覆盖。"""
    env_path = os.environ.get("HARNESS_REGISTRY")
    if env_path:
        return Path(env_path)
    python_dir = Path(__file__).resolve().parents[3]
    data_dir = python_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "harness_snapshots.json"


_registry: HarnessRegistry | None = None
_registry_lock = threading.Lock()


def get_harness_registry() -> HarnessRegistry:
    """全局单例（双重检查锁）。"""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = HarnessRegistry()
    return _registry


def reset_harness_registry() -> None:
    """测试用：清空单例。"""
    global _registry
    with _registry_lock:
        _registry = None
