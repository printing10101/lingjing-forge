"""工艺规则数据类（从 rule_db 拆出）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class RuleCondition:
    """规则条件项"""

    parameter: str
    operator: str
    value: str
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RuleCondition":
        return cls(
            parameter=d.get("parameter", ""),
            operator=d.get("operator", "="),
            value=d.get("value", ""),
            unit=d.get("unit"),
        )


@dataclass
class RuleResult:
    """规则结果项"""

    parameter: str
    operator: str
    value: str
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RuleResult":
        return cls(
            parameter=d.get("parameter", ""),
            operator=d.get("operator", "<="),
            value=d.get("value", ""),
            unit=d.get("unit"),
        )


@dataclass
class ProcessRule:
    """工艺规则数据模型"""

    id: int | None = None
    name: str = ""
    description: str = ""
    group_id: int | None = None
    conditions: list[RuleCondition] = field(default_factory=list)
    logic_operator: str = "AND"
    result: RuleResult | None = None
    status: str = "active"
    priority: int = 0
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["conditions"] = [c.to_dict() if isinstance(c, RuleCondition) else c for c in self.conditions]
        d["result"] = self.result.to_dict() if isinstance(self.result, RuleResult) else self.result
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProcessRule":
        conditions = []
        for c in d.get("conditions", []):
            if isinstance(c, RuleCondition):
                conditions.append(c)
            elif isinstance(c, dict):
                conditions.append(RuleCondition.from_dict(c))
            elif isinstance(c, str):
                conditions.append(RuleCondition.from_dict(json.loads(c)))

        result_data = d.get("result")
        result = None
        if result_data:
            if isinstance(result_data, RuleResult):
                result = result_data
            elif isinstance(result_data, dict):
                result = RuleResult.from_dict(result_data)
            elif isinstance(result_data, str):
                result = RuleResult.from_dict(json.loads(result_data))

        return cls(
            id=d.get("id"),
            name=d.get("name", ""),
            description=d.get("description", ""),
            group_id=d.get("group_id"),
            conditions=conditions,
            logic_operator=d.get("logic_operator", "AND"),
            result=result,
            status=d.get("status", "active"),
            priority=d.get("priority", 0),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )

    def to_preview_text(self) -> str:
        """生成规则预览文本"""
        parts = ["IF"]
        cond_parts = []
        for c in self.conditions:
            text = f"{c.parameter} {c.operator} {c.value}"
            if c.unit:
                text += f"{c.unit}"
            cond_parts.append(text)
        joiner = f" {self.logic_operator} "
        parts.append(joiner.join(cond_parts))
        if self.result:
            result_text = f"{self.result.parameter} {self.result.operator} {self.result.value}"
            if self.result.unit:
                result_text += f"{self.result.unit}"
            parts.append(f"THEN {result_text}")
        return " ".join(parts)


@dataclass
class RuleGroup:
    """规则分组数据模型"""

    id: int | None = None
    name: str = ""
    description: str = ""
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RuleGroup":
        return cls(
            id=d.get("id"),
            name=d.get("name", ""),
            description=d.get("description", ""),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )
