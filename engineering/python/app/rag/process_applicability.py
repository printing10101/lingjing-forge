"""Versioned drilling limits and pure task applicability checks."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, TypeAdapter, model_validator

Coolant = Literal["dry", "flood", "mist", "through_tool"]
Workholding = Literal["vise", "chuck", "fixture", "collet"]


class DrillingLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: StrictInt = Field(ge=1, le=1)
    hardness_min_hrc: float = Field(ge=0, le=100, allow_inf_nan=False)
    hardness_max_hrc: float = Field(ge=0, le=100, allow_inf_nan=False)
    hardness_max_inclusive: StrictBool
    diameter_min_mm: float = Field(gt=0, allow_inf_nan=False)
    diameter_max_mm: float = Field(gt=0, allow_inf_nan=False)
    max_depth_to_diameter: float = Field(gt=0, allow_inf_nan=False)
    coolants: list[Coolant] = Field(min_length=1)
    workholdings: list[Workholding] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_bounds(self):
        if (self.hardness_min_hrc > self.hardness_max_hrc or self.diameter_min_mm > self.diameter_max_mm
                or (self.hardness_min_hrc == self.hardness_max_hrc and not self.hardness_max_inclusive)):
            raise ValueError("[范围错误] 下界超过上界。建议操作：核对审核范围及单位。")
        return self


class MachiningConditions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: str = Field(min_length=1, max_length=128)
    hardness_hrc: float = Field(ge=0, le=100, allow_inf_nan=False)
    diameter_mm: float = Field(gt=0, allow_inf_nan=False)
    depth_mm: float = Field(gt=0, allow_inf_nan=False)
    coolant: Coolant
    workholding: Workholding

    @model_validator(mode="after")
    def normalize_tool(self):
        self.tool = self.tool.strip().lower()
        if not self.tool:
            raise ValueError("[条件缺失] 刀具为空。建议操作：填写现场刀具标识。")
        return self


def check_applicability(record: dict, conditions: dict | None) -> dict:
    """Approval alone does not establish applicability to a machining task."""
    result = {"record_id": record["record_id"], "revision": record["revision"],
              "status": "matched", "missing": [], "mismatched": []}
    if record["feature"] != "hole" or record["process"] not in ("drill", "drilling"):
        return {**result, "status": "unsupported_process"}
    limits_data = record.get("review", {}).get("evidence", {}).get("constraints")
    if not limits_data:
        return {**result, "status": "record_constraints_missing"}
    limits = DrillingLimits.model_validate(limits_data)
    supplied = conditions or {}
    missing = sorted(set(MachiningConditions.model_fields) - set(supplied))
    if missing:
        return {**result, "status": "conditions_missing", "missing": missing}
    actual = MachiningConditions.model_validate(supplied)
    mismatched = []
    if actual.tool != record["tool"]:
        mismatched.append("tool")
    if not limits.hardness_min_hrc <= actual.hardness_hrc <= limits.hardness_max_hrc:
        mismatched.append("hardness_hrc")
    elif not limits.hardness_max_inclusive and actual.hardness_hrc == limits.hardness_max_hrc:
        mismatched.append("hardness_hrc")
    if not limits.diameter_min_mm <= actual.diameter_mm <= limits.diameter_max_mm:
        mismatched.append("diameter_mm")
    # Multiply rather than round a depth ratio; boundary violations must stay visible.
    if actual.depth_mm > limits.max_depth_to_diameter * actual.diameter_mm:
        mismatched.append("depth_mm")
    if actual.coolant not in limits.coolants:
        mismatched.append("coolant")
    if actual.workholding not in limits.workholdings:
        mismatched.append("workholding")
    return {**result, "status": "conditions_mismatch" if mismatched else "matched", "mismatched": mismatched}


def validate_supplied_conditions(conditions: dict | None) -> None:
    if conditions is None:
        return
    if not isinstance(conditions, dict) or set(conditions) - set(MachiningConditions.model_fields):
        raise ValueError("[条件错误] 现场条件含未知字段或格式错误。建议操作：按接口说明填写。")
    for key, value in conditions.items():
        TypeAdapter(MachiningConditions.model_fields[key].rebuild_annotation()).validate_python(value, strict=True)
    if "tool" in conditions and not conditions["tool"].strip():
        raise ValueError("[条件缺失] 刀具为空。建议操作：填写现场刀具标识。")


def validate_limits_draft(limits: dict) -> None:
    if not isinstance(limits, dict) or set(limits) - set(DrillingLimits.model_fields):
        raise ValueError("[范围错误] 含未知适用范围字段。建议操作：核对审核资料。")
    for key, value in limits.items():
        TypeAdapter(DrillingLimits.model_fields[key].rebuild_annotation()).validate_python(value, strict=True)
    for lower, upper in (("hardness_min_hrc", "hardness_max_hrc"), ("diameter_min_mm", "diameter_max_mm")):
        if lower in limits and upper in limits and limits[lower] > limits[upper]:
            raise ValueError("[范围错误] 下界超过上界。建议操作：核对审核范围。")
