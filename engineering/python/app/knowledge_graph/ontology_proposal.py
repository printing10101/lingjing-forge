"""本体扩展提案（Ontology Extension Proposal）—— 低资源半自动本体构建。

背景
----
现有 ``app/models/knowledge_graph`` 只提供**固定本体**（Material / Tool / Feature /
Process 等）。赛题三要求"在低资源场景下半自动构建本体，并支持概念、关系、属性的
扩展"。本模块补的就是"扩展"这一环：

    知识候选（带出处的待审事实）
        → 规则检测：出现固定本体里没有的实体类型 / 关系类型 / 属性键
        → 生成**提案**（proposal），带证据与出现次数
        → 人工批准 / 拒绝（本模块不自动登记）
        → 批准后写入本体注册表，供后续抽取使用

设计约束（与仓库既有知识审核链路一致）：

1. 提案只是提案。检测不等于采纳，任何未经人工批准的类型都不得进入正式本体
   （对应 ``review`` 门禁：``status != approved`` 时 ``is_registered`` 恒为 False）。
2. 每个提案必须携带溯源：来自哪条候选、哪次提交、出现在哪个字段。
3. 同一 (kind, name) 重复出现时合并计数，不重复开提案。
4. 纯标准库实现（json / sqlite3 / dataclasses），不引入新依赖，便于在
   Nexent 侧沙箱与本地单测中直接运行。

用法
----
>>> reg = OntologyRegistry.fixed_default()
>>> p = reg.propose(kind="entity_type", name="coolant_type",
...                 evidence=Evidence(source_id="cand-001", field="content.coolant"))
>>> p.status
'pending'
>>> reg.is_registered("entity_type", "coolant_type")
False
>>> reg.approve(p.proposal_id, reviewer="engineer-01")
True
>>> reg.is_registered("entity_type", "coolant_type")
True
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

ProposalKind = Literal["entity_type", "relation_type", "property_key"]
ProposalStatus = Literal["pending", "approved", "rejected"]

#: 提案允许的种类。概念扩展 = entity_type，关系扩展 = relation_type，
#: 属性扩展 = property_key（对应赛题"实体、关系、属性的扩展"）。
PROPOSAL_KINDS: tuple[ProposalKind, ...] = ("entity_type", "relation_type", "property_key")

#: 固定本体（与 app/models/knowledge_graph.py 的既有实体类型保持一致）。
#: 只作为"已知基线"，本模块不修改它，扩展一律走提案。
FIXED_ENTITY_TYPES: frozenset[str] = frozenset(
    {"Material", "Tool", "Feature", "Process", "FailureMode", "Controller"}
)
FIXED_RELATION_TYPES: frozenset[str] = frozenset(
    {
        "suitable_for",   # (Material, Process)
        "uses_tool",      # (Process, Tool)
        "has_feature",    # (Feature, Process)
        "causes",         # (Process, FailureMode)
        "mitigated_by",   # (FailureMode, Process)
    }
)
FIXED_PROPERTY_KEYS: frozenset[str] = frozenset(
    {"hardness_hrc", "diameter_mm", "depth_mm", "coolant", "workholding", "unit"}
)


def _utc_now() -> str:
    """返回 ISO 8601 UTC 时间戳（秒级）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Evidence:
    """一条提案的出现证据。

    ``source_id`` 是知识候选的稳定标识（对应 ``cam_submit_knowledge_candidate`` 的
    ``submission_id``）；``field`` 指明该类型出现在候选内容的哪个字段，便于审核人
    回溯源文档。``excerpt`` 为原文片段，允许为空——空表示"只见到该键，没见到可
    引用的句子"，审核人应视为证据不足。
    """

    source_id: str
    field: str
    excerpt: str = ""
    doc_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Proposal:
    """一条本体扩展提案。默认状态 ``pending``，必须人工批准才生效。"""

    kind: ProposalKind
    name: str
    proposal_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: ProposalStatus = "pending"
    rationale: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    first_seen: str = field(default_factory=_utc_now)
    last_seen: str = field(default_factory=_utc_now)
    occurrences: int = 0
    reviewer: str = ""
    reviewed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [e.to_dict() for e in self.evidence]
        return payload

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.name)


class OntologyRegistry:
    """本体注册表 + 提案队列。

    内存对象，可选 ``db_path`` 持久化（SQLite 单文件，便于随作品包一起提交）。
    """

    def __init__(
        self,
        *,
        entity_types: Iterable[str] = FIXED_ENTITY_TYPES,
        relation_types: Iterable[str] = FIXED_RELATION_TYPES,
        property_keys: Iterable[str] = FIXED_PROPERTY_KEYS,
        db_path: str | Path | None = None,
    ) -> None:
        self._registered: dict[str, set[str]] = {
            "entity_type": set(entity_types),
            "relation_type": set(relation_types),
            "property_key": set(property_keys),
        }
        self._proposals: dict[tuple[str, str], Proposal] = {}
        self._db_path = Path(db_path) if db_path else None
        if self._db_path is not None:
            self._init_db()
            self._load()

    # ------------------------------------------------------------------ 构造
    @classmethod
    def fixed_default(cls, *, db_path: str | Path | None = None) -> "OntologyRegistry":
        """以仓库固定本体为基线构造注册表。"""
        return cls(db_path=db_path)

    # ------------------------------------------------------------------ 查询
    def is_registered(self, kind: str, name: str) -> bool:
        """判断某类型/键是否已在本体中生效。

        只有 ``approved`` 的提案才会被登记，因此本方法天然是"人工门禁后"的视图。
        """
        if kind not in PROPOSAL_KINDS:
            return False
        return name in self._registered[kind]

    def registered(self, kind: str) -> list[str]:
        if kind not in PROPOSAL_KINDS:
            raise ValueError(f"unknown proposal kind: {kind}")
        return sorted(self._registered[kind])

    def pending(self) -> list[Proposal]:
        return sorted(
            (p for p in self._proposals.values() if p.status == "pending"),
            key=lambda p: (-p.occurrences, p.kind, p.name),
        )

    def all_proposals(self) -> list[Proposal]:
        """返回全部提案（含已批准/已拒绝），按出现次数降序。

        审核与外部工具读队列都应走本方法，避免直接访问内部字典。
        """
        return sorted(
            self._proposals.values(),
            key=lambda p: (-p.occurrences, p.kind, p.name),
        )

    def get(self, proposal_id: str) -> Proposal | None:
        for p in self._proposals.values():
            if p.proposal_id == proposal_id:
                return p
        return None

    # ------------------------------------------------------------------ 提案
    def propose(
        self,
        *,
        kind: ProposalKind,
        name: str,
        evidence: Evidence | None = None,
        rationale: str = "",
    ) -> Proposal:
        """登记一条扩展提案；同 (kind, name) 已存在则合并计数并返回已有提案。

        已批准的类型再次被"检测到缺失"时不重新开提案——说明注册表与调用方不同步，
        这里选择抛出 ``ValueError``，避免悄悄掩盖状态不一致。
        """
        if kind not in PROPOSAL_KINDS:
            raise ValueError(f"unknown proposal kind: {kind}")
        name = (name or "").strip()
        if not name:
            raise ValueError("proposal name must not be empty")
        if self.is_registered(kind, name):
            raise ValueError(f"{kind} '{name}' already registered; proposal refused")

        key = (kind, name)
        existing = self._proposals.get(key)
        if existing is not None:
            if existing.status == "rejected":
                # 拒绝过的类型不应被静默重开；由审核人显式重新开启。
                return existing
            existing.occurrences += 1
            existing.last_seen = _utc_now()
            if evidence is not None and evidence not in existing.evidence:
                existing.evidence.append(evidence)
            self._persist(existing)
            return existing

        proposal = Proposal(
            kind=kind,
            name=name,
            rationale=rationale,
            evidence=[evidence] if evidence is not None else [],
            occurrences=1,
        )
        self._proposals[key] = proposal
        self._persist(proposal)
        return proposal

    def approve(self, proposal_id: str, *, reviewer: str) -> bool:
        """人工批准并把类型登记进本体。``reviewer`` 为空视为无效批准。"""
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")
        if not reviewer or not reviewer.strip():
            raise ValueError("reviewer is required; anonymous approval is refused")
        if proposal.status == "approved":
            return False
        proposal.status = "approved"
        proposal.reviewer = reviewer
        proposal.reviewed_at = _utc_now()
        self._registered[proposal.kind].add(proposal.name)
        self._persist(proposal)
        return True

    def reject(self, proposal_id: str, *, reviewer: str, reason: str = "") -> bool:
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")
        if not reviewer or not reviewer.strip():
            raise ValueError("reviewer is required; anonymous rejection is refused")
        if proposal.status == "rejected":
            return False
        proposal.status = "rejected"
        proposal.reviewer = reviewer
        proposal.reviewed_at = _utc_now()
        if reason:
            proposal.rationale = f"{proposal.rationale} | rejected: {reason}".strip(" |")
        self._registered[proposal.kind].discard(proposal.name)
        self._persist(proposal)
        return True

    # ------------------------------------------------ 从知识候选半自动抽取
    def scan_candidate(
        self,
        candidate: dict[str, Any],
        *,
        source_id: str,
        doc_sha256: str = "",
    ) -> list[Proposal]:
        """扫描一份知识候选，把固定本体之外的类型/键登记为提案。

        只做**检出**，不做判断：检出后仍须人工批准。返回本次产生的提案列表
        （已存在且未拒绝的合并计数后同样返回）。

        候选结构按 ``manufacturing-knowledge-update`` 技能的 ``content`` 约定：
        ``feature`` / ``process`` / ``tool`` / ``material`` / ``parameters``。
        这里把 ``tool`` / ``material`` 的**取值**当实体实例，不当类型；只有候选里
        显式声明的 ``entity_types`` / ``relation_types`` 才当作类型扩展来源，
        避免把每一个具体牌号都变成"新本体"。
        """
        produced: list[Proposal] = []

        for raw_kind in ("entity_types", "relation_types", "property_keys"):
            values = candidate.get(raw_kind) or []
            if isinstance(values, str):
                values = [values]
            kind = {
                "entity_types": "entity_type",
                "relation_types": "relation_type",
                "property_keys": "property_key",
            }[raw_kind]
            for value in values:
                name = str(value).strip()
                if not name or self.is_registered(kind, name):  # type: ignore[arg-type]
                    continue
                produced.append(
                    self.propose(
                        kind=kind,  # type: ignore[arg-type]
                        name=name,
                        evidence=Evidence(
                            source_id=source_id,
                            field=f"content.{raw_kind}",
                            doc_sha256=doc_sha256,
                        ),
                        rationale="候选声明了固定本体之外的类型",
                    )
                )

        # 属性键：parameters / conditions 里出现未登记的键，视为属性扩展候选。
        # conditions 也扫，是因为现场条件键一旦换写法（hardness vs hardness_hrc），
        # 条件过滤会静默失配——这类别名必须被摆到审核人面前，而不是靠同义词表硬凑。
        for section in ("parameters", "conditions"):
            values = candidate.get(section) or {}
            if not isinstance(values, dict):
                continue
            for key in values:
                key = str(key).strip()
                if not key or key in FIXED_PROPERTY_KEYS_SEMANTIC:
                    continue
                if self.is_registered("property_key", key):
                    continue
                produced.append(
                    self.propose(
                        kind="property_key",
                        name=key,
                        evidence=Evidence(
                            source_id=source_id,
                            field=f"content.{section}.{key}",
                            doc_sha256=doc_sha256,
                        ),
                        rationale=f"{section} 出现未登记的属性键",
                    )
                )
        return produced

    # -------------------------------------------------------------- 持久化
    def _init_db(self) -> None:
        assert self._db_path is not None
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ontology_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    kind        TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    payload     TEXT NOT NULL,
                    UNIQUE (kind, name)
                )
                """
            )
            conn.commit()

    def _persist(self, proposal: Proposal) -> None:
        if self._db_path is None:
            return
        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                """
                INSERT INTO ontology_proposals (proposal_id, kind, name, status, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload
                """,
                (
                    proposal.proposal_id,
                    proposal.kind,
                    proposal.name,
                    proposal.status,
                    json.dumps(proposal.to_dict(), ensure_ascii=False),
                ),
            )
            conn.commit()

    def _load(self) -> None:
        assert self._db_path is not None
        with closing(sqlite3.connect(self._db_path)) as conn:
            rows = conn.execute(
                "SELECT payload FROM ontology_proposals ORDER BY rowid"
            ).fetchall()
        for (payload,) in rows:
            data = json.loads(payload)
            evidence = [Evidence(**e) for e in data.pop("evidence", [])]
            proposal = Proposal(**data, evidence=evidence)
            self._proposals[proposal.key] = proposal
            if proposal.status == "approved":
                self._registered[proposal.kind].add(proposal.name)


#: ``parameters`` 中语义上属于"现场条件"的键，已由 conditions 契约覆盖，
#: 不当作属性扩展候选。
FIXED_PROPERTY_KEYS_SEMANTIC: frozenset[str] = frozenset(
    {"speed", "feed", "rpm", "units", "unit", "value", "min", "max"}
)
