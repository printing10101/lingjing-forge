"""否决案例库 v0（B2）：被拦截的加工提案结构化沉淀.

用途（五用，各一句）
--------------------
1. 教学：用真实拦截案例讲"为什么这条参数不能上机"；
2. 销售：演示"AI 产出必经物理校验"的硬闸证据；
3. 论文：作为校验闸有效性的案例材料（脱敏后引用）；
4. RAG：作为工艺知识库的反例条目，检索"类似参数曾被拦截"；
5. 再训练：为阈值/模型提供负样本，但**不得直接进模型训练集**，
   需经论文/数据隔离流程复核。

脱敏规则（硬约束）
------------------
写入前必须经过 :func:`redact_text`：绝对路径、邮箱、连续 ≥8 位数字 ID、
主机名/IP 一律替换为占位符；只保留材料族（steel_carbon / aluminium / ...）、
控制器型号、切削参数与拦截原因。未脱敏文本由 :meth:`RejectionCase.validate`
直接拒绝，避免客户信息入库。

存储：JSONL 追加写，``case_id`` 去重（同 source 重复导入不产生新记录）。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STAGES = ("cam_validation", "gcode_gate", "stability_gate", "manual_review")
DECISIONS = ("blocked", "warned")
SEVERITIES = ("critical", "high", "medium", "low")

# 参数白名单：只允许工艺参数，禁止任何可识别客户/零件的信息
ALLOWED_PARAM_KEYS = frozenset({
    "axial_depth_mm", "radial_depth_mm", "limit_depth_mm", "spindle_speed_rpm",
    "feed_rate_mm_per_min", "feed_per_tooth_mm", "cutting_speed_m_per_min",
    "tool_diameter_mm", "num_flutes",
})

_PATH_RE = re.compile(r"(?:[A-Za-z]:\\[^\s，。；]+|/(?:[\w.-]+/)+[\w.-]+)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_LONG_ID_RE = re.compile(r"\b\d{8,}\b")
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def redact_text(text: str) -> tuple[str, list[str]]:
    """脱敏：返回（清洗后文本, 命中规则列表）。"""
    notes: list[str] = []
    cleaned = text
    for pattern, label in ((_PATH_RE, "path"), (_EMAIL_RE, "email"),
                           (_IP_RE, "ip"), (_LONG_ID_RE, "long_id")):
        if pattern.search(cleaned):
            cleaned = pattern.sub(f"<{label}>", cleaned)
            notes.append(label)
    return cleaned, notes


@dataclass
class RejectionCase:
    """一条否决案例（脱敏后）。"""

    case_id: str
    source_task_id: str
    stage: str
    decision: str
    severity: str
    error_codes: list[str] = field(default_factory=list)
    evidence_summary: str = ""
    material_family: str = ""
    controller_type: str = ""
    params: dict[str, float] = field(default_factory=dict)
    created_at: float = 0.0
    desensitized: bool = True
    redaction_notes: list[str] = field(default_factory=list)
    schema_version: int = 1

    def validate(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(f"非法 stage: {self.stage}，合法值: {STAGES}")
        if self.decision not in DECISIONS:
            raise ValueError(f"非法 decision: {self.decision}，合法值: {DECISIONS}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"非法 severity: {self.severity}，合法值: {SEVERITIES}")
        if not self.case_id or not self.source_task_id:
            raise ValueError("case_id 与 source_task_id 不能为空")
        if not self.error_codes:
            raise ValueError("error_codes 不能为空（否决必须有结构化原因）")
        if not self.desensitized:
            raise ValueError("仅接受已脱敏案例（desensitized=True）")
        if len(self.evidence_summary) > 500:
            raise ValueError("evidence_summary 超过 500 字，请截断后写入")
        leaked = [label for pattern, label in ((_PATH_RE, "path"), (_EMAIL_RE, "email"))
                  if pattern.search(self.evidence_summary)]
        if leaked:
            raise ValueError(f"evidence_summary 仍含未脱敏内容: {leaked}，请先 redact_text")
        unknown = set(self.params) - ALLOWED_PARAM_KEYS
        if unknown:
            raise ValueError(f"params 含非白名单键: {sorted(unknown)}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def make_case_id(*parts: str) -> str:
    """稳定 ID：同一来源重复导入不产生重复记录。"""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"rc_{digest[:16]}"


class RejectionCaseStore:
    """JSONL 追加存储（case_id 去重）。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def _existing_ids(self) -> set[str]:
        if not self._path.exists():
            return set()
        ids: set[str] = set()
        with self._path.open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    ids.add(json.loads(line)["case_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
        return ids

    def append(self, cases: list[RejectionCase]) -> int:
        """批量写入；返回实际新增条数（重复 ID 跳过）。"""
        existing = self._existing_ids()
        new_cases = [case for case in cases if case.case_id not in existing]
        if not new_cases:
            return 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as stream:
            for case in new_cases:
                stream.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")
        return len(new_cases)

    def load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        cases = []
        with self._path.open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if line:
                    cases.append(json.loads(line))
        return cases

    def stats(self) -> dict[str, Any]:
        cases = self.load()
        return {"total": len(cases),
                "by_stage": dict(Counter(case["stage"] for case in cases)),
                "by_severity": dict(Counter(case["severity"] for case in cases)),
                "by_error_code": dict(Counter(code for case in cases for code in case["error_codes"]))}
