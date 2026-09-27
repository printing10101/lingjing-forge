"""Unit tests for cutting_experience contract + ORM conversion (P2-1/P2-2)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.contracts.cutting_experience import (
    CoolantMode,
    CuttingExperience,
    CuttingParameters,
    CuttingResults,
    ExperienceQuery,
    ExperienceStats,
    MachiningAnomaly,
    MachiningResult,
    MachiningType,
)
from app.database.models.cutting_experience import CuttingExperienceRecord


def _make_record(**overrides) -> CuttingExperience:
    """Build a valid CuttingExperience with sensible defaults."""
    base = {
        "machine_id": "VM-001",
        "tool_id": "T-12",
        "material": "AL6061",
        "parameters": CuttingParameters(
            depth_of_cut_mm=2.0,
            feed_mm_per_rev=0.2,
            spindle_rpm=8000,
        ),
        "results": CuttingResults(
            cycle_time_s=120.5,
            surface_roughness_ra=1.2,
            result=MachiningResult.OK,
        ),
    }
    base.update(overrides)
    return CuttingExperience(**base)


# Contract validation


class TestCuttingParameters:
    def test_valid_parameters(self) -> None:
        p = CuttingParameters(depth_of_cut_mm=1.5, feed_mm_per_rev=0.15, spindle_rpm=6000)
        assert p.depth_of_cut_mm == 1.5
        assert p.coolant == CoolantMode.FLOOD  # default

    def test_zero_depth_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CuttingParameters(depth_of_cut_mm=0, feed_mm_per_rev=0.15, spindle_rpm=6000)

    def test_negative_feed_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CuttingParameters(depth_of_cut_mm=1.0, feed_mm_per_rev=-0.1, spindle_rpm=6000)

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            CuttingParameters(
                depth_of_cut_mm=1.0,
                feed_mm_per_rev=0.1,
                spindle_rpm=6000,
                not_a_field=1,
            )


class TestCuttingResults:
    def test_valid_results(self) -> None:
        r = CuttingResults(cycle_time_s=60.0, result=MachiningResult.OK)
        assert r.tool_wear_percent is None

    def test_wear_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CuttingResults(cycle_time_s=60.0, tool_wear_percent=150.0)

    def test_negative_roughness_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CuttingResults(cycle_time_s=60.0, surface_roughness_ra=-0.5)


class TestCuttingExperience:
    def test_valid_record(self) -> None:
        rec = _make_record()
        assert rec.id is not None
        assert rec.machining_type == MachiningType.MILLING
        assert rec.source == "manual"  # default
        assert rec.anomalies == []

    def test_machine_id_required(self) -> None:
        with pytest.raises(ValidationError):
            _make_record(machine_id="")

    def test_program_number_max_length(self) -> None:
        with pytest.raises(ValidationError):
            _make_record(program_number="X" * 100)

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            CuttingExperience(
                machine_id="M1",
                tool_id="T1",
                parameters=CuttingParameters(depth_of_cut_mm=1.0, feed_mm_per_rev=0.1, spindle_rpm=5000),
                results=CuttingResults(cycle_time_s=10),
                bogus=1,
            )

    def test_with_anomalies(self) -> None:
        rec = _make_record(
            anomalies=[
                MachiningAnomaly(
                    anomaly_type="chatter",
                    severity=7,
                    message="高频颤振",
                    measured_value=6.2,
                    threshold_value=5.0,
                )
            ]
        )
        assert len(rec.anomalies) == 1
        assert rec.anomalies[0].anomaly_type == "chatter"


class TestExperienceQuery:
    def test_defaults(self) -> None:
        q = ExperienceQuery()
        assert q.limit == 100
        assert q.offset == 0
        assert q.machine_id is None

    def test_limit_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ExperienceQuery(limit=0)
        with pytest.raises(ValidationError):
            ExperienceQuery(limit=1001)

    def test_full_query(self) -> None:
        q = ExperienceQuery(
            machine_id="M1",
            tool_id="T1",
            material="AL6061",
            machining_type=MachiningType.TURNING,
            result=MachiningResult.OK,
            has_anomaly=True,
            limit=50,
            offset=10,
        )
        assert q.machining_type == MachiningType.TURNING
        assert q.has_anomaly is True


class TestExperienceStats:
    def test_valid_stats(self) -> None:
        s = ExperienceStats(total_records=10, ok_rate=0.9)
        assert s.ok_rate == 0.9

    def test_ok_rate_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ExperienceStats(total_records=1, ok_rate=1.5)


# ORM conversion


class TestCuttingExperienceRecord:
    def test_from_contract_roundtrip(self) -> None:
        rec = _make_record(
            job_id=uuid.uuid4(),
            program_number="O1234",
            tags={"batch": "b1", "recommended": True},
            operator="张三",
            source="mtconnect",
            anomalies=[MachiningAnomaly(anomaly_type="overload", severity=5, message="过载")],
        )
        model = CuttingExperienceRecord.from_contract(rec)
        assert model.machine_id == "VM-001"
        assert model.tool_id == "T-12"
        assert model.machining_type == "milling"
        assert model.result == "ok"
        assert model.cycle_time_s == 120.5
        assert model.anomaly_count == 1
        assert model.parameters["depth_of_cut_mm"] == 2.0
        assert model.parameters["spindle_rpm"] == 8000
        assert model.tags["recommended"] is True
        assert model.anomalies[0]["anomaly_type"] == "overload"

    def test_to_contract_dict_structure(self) -> None:
        model = CuttingExperienceRecord.from_contract(_make_record())
        d = model.to_contract_dict()
        assert d["machine_id"] == "VM-001"
        assert d["parameters"]["depth_of_cut_mm"] == 2.0
        assert d["results"]["cycle_time_s"] == 120.5
        assert d["results"]["result"] == "ok"
        assert d["anomalies"] == []
        assert "created_at" in d
        assert "updated_at" in d

    def test_results_extra_contains_no_flat_keys(self) -> None:
        rec = _make_record()
        model = CuttingExperienceRecord.from_contract(rec)
        assert "cycle_time_s" not in model.results_extra
        assert "result" not in model.results_extra

    def test_id_strategy(self) -> None:
        model = CuttingExperienceRecord.from_contract(_make_record())
        assert model.id.startswith("exp_")
        assert len(model.id) == 4 + 32  # exp_ + 32 hex chars

    def test_none_job_id(self) -> None:
        model = CuttingExperienceRecord.from_contract(_make_record())
        assert model.job_id is None

    def test_batch_conversion_contract_to_dict_roundtrip(self) -> None:
        """ORM → contract dict → 字段一致性（供 API 响应消费）。"""
        rec = _make_record(
            material="SS304",
            results=CuttingResults(
                cycle_time_s=88.0,
                tool_wear_percent=12.5,
                result=MachiningResult.REWORK,
            ),
        )
        model = CuttingExperienceRecord.from_contract(rec)
        d = model.to_contract_dict()
        assert d["material"] == "SS304"
        assert d["results"]["tool_wear_percent"] == 12.5
        assert d["results"]["result"] == "rework"
