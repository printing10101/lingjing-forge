"""Unit tests for :mod:`app.models.machining_record` & repository.

覆盖范围：
    1. Pydantic 模型字段校验（合法输入 / 越界输入 / 必填字段）。
    2. Pydantic 模型序列化（``model_dump`` / ``model_dump_json`` /
       ``model_validate``）往返。
    3. SQLAlchemy ORM 模型表结构（``__tablename__``、列、索引、唯一约束）。
    4. Repository 同步 CRUD 端到端（基于内存 SQLite + 同步 sessionmaker）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from collections.abc import Iterator

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models.machining_record import Base, MachiningRecord
from app.database.repository.machining_record_repo import MachiningRecordRepository
from app.models.machining_record import (
    MachiningRecordCreate,
    MachiningRecordRead,
    MachiningRecordUpdate,
)


# 1. Pydantic 模型字段校验


class TestPydanticFieldValidation:
    """Pydantic 模型字段范围 / 必填 / 类型校验。"""

    def _valid_payload(self) -> dict:
        return {
            "machine_id": "CNC-01",
            "tool_id": "T-EM-10",
            "material": "45号钢",
            "spindle_speed": 4500.0,
            "feed_rate": 800.0,
            "tdengine_series_id": "ts_2026_06_11_001",
            "process_params": {
                "depth_of_cut": 1.5,
                "coolant": True,
                "operation": "face_milling",
            },
        }

    def test_valid_create_model(self) -> None:
        record = MachiningRecordCreate(**self._valid_payload())
        assert record.machine_id == "CNC-01"
        assert record.spindle_speed == 4500.0
        assert record.feed_rate == 800.0
        assert record.process_params["coolant"] is True

    def test_default_timestamp_is_utc(self) -> None:
        record = MachiningRecordCreate(**self._valid_payload())
        assert record.timestamp.tzinfo is not None

    def test_default_process_params_is_empty_dict(self) -> None:
        payload = self._valid_payload()
        payload.pop("process_params", None)
        record = MachiningRecordCreate(**payload)
        assert record.process_params == {}

    def test_extra_field_forbidden(self) -> None:
        payload = self._valid_payload()
        payload["unknown_field"] = "x"
        with pytest.raises(ValidationError):
            MachiningRecordCreate(**payload)

    def test_missing_required_machine_id(self) -> None:
        payload = self._valid_payload()
        payload.pop("machine_id")
        with pytest.raises(ValidationError):
            MachiningRecordCreate(**payload)

    def test_spindle_speed_negative_rejected(self) -> None:
        payload = self._valid_payload()
        payload["spindle_speed"] = -1.0
        with pytest.raises(ValidationError):
            MachiningRecordCreate(**payload)

    def test_spindle_speed_zero_accepted(self) -> None:
        payload = self._valid_payload()
        payload["spindle_speed"] = 0.0
        record = MachiningRecordCreate(**payload)
        assert record.spindle_speed == 0.0

    def test_spindle_speed_above_max_rejected(self) -> None:
        payload = self._valid_payload()
        payload["spindle_speed"] = 200001.0
        with pytest.raises(ValidationError):
            MachiningRecordCreate(**payload)

    def test_feed_rate_above_max_rejected(self) -> None:
        payload = self._valid_payload()
        payload["feed_rate"] = 50001.0
        with pytest.raises(ValidationError):
            MachiningRecordCreate(**payload)

    def test_blank_machine_id_rejected(self) -> None:
        payload = self._valid_payload()
        payload["machine_id"] = ""
        with pytest.raises(ValidationError):
            MachiningRecordCreate(**payload)

    def test_machine_id_too_long_rejected(self) -> None:
        payload = self._valid_payload()
        payload["machine_id"] = "x" * 65
        with pytest.raises(ValidationError):
            MachiningRecordCreate(**payload)

    def test_tdengine_series_id_optional(self) -> None:
        payload = self._valid_payload()
        payload["tdengine_series_id"] = None
        record = MachiningRecordCreate(**payload)
        assert record.tdengine_series_id is None

    def test_tdengine_series_id_too_long_rejected(self) -> None:
        payload = self._valid_payload()
        payload["tdengine_series_id"] = "x" * 129
        with pytest.raises(ValidationError):
            MachiningRecordCreate(**payload)

    def test_string_fields_are_stripped(self) -> None:
        payload = self._valid_payload()
        payload["machine_id"] = "  CNC-01  "
        record = MachiningRecordCreate(**payload)
        assert record.machine_id == "CNC-01"


# 2. Pydantic 模型序列化 / 反序列化


class TestPydanticSerialization:
    """序列化 / 反序列化 / ORM 兼容。"""

    def _valid_payload(self) -> dict:
        return {
            "machine_id": "CNC-02",
            "tool_id": "T-EM-20",
            "material": "6061铝合金",
            "spindle_speed": 6000.0,
            "feed_rate": 1200.0,
            "tdengine_series_id": "ts_2026_06_11_002",
            "process_params": {"depth_of_cut": 2.0, "coolant": False},
        }

    def test_dump_and_reload(self) -> None:
        original = MachiningRecordCreate(**self._valid_payload())
        dumped = original.model_dump()
        reloaded = MachiningRecordCreate(**dumped)
        assert reloaded.model_dump() == dumped

    def test_json_round_trip(self) -> None:
        original = MachiningRecordCreate(**self._valid_payload())
        js = original.model_dump_json()
        reloaded = MachiningRecordCreate.model_validate_json(js)
        assert reloaded.machine_id == original.machine_id
        assert reloaded.spindle_speed == original.spindle_speed
        assert reloaded.process_params == original.process_params

    def test_update_partial_fields(self) -> None:
        patch = MachiningRecordUpdate(spindle_speed=5500.0)
        assert patch.spindle_speed == 5500.0
        assert patch.feed_rate is None
        assert patch.tdengine_series_id is None

    def test_update_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            MachiningRecordUpdate(unknown_field="x")  # type: ignore[call-arg]

    def test_update_negative_spindle_speed_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MachiningRecordUpdate(spindle_speed=-1.0)

    def test_read_model_auto_id(self) -> None:
        read = MachiningRecordRead(**self._valid_payload())
        assert read.record_id.startswith("mrec_")

    def test_read_model_from_attributes(self) -> None:
        # 模拟 ORM 实例：Pydantic Read 应当能直接从属性构造
        record = MachiningRecordRead(**self._valid_payload())
        # 测试 model_validate（from_attributes 等价路径）
        from_attributes_dict = record.model_dump()
        from_attributes_dict["record_id"] = record.record_id
        reloaded = MachiningRecordRead.model_validate(from_attributes_dict)
        assert reloaded.machine_id == record.machine_id

    def test_validate_assignment(self) -> None:
        record = MachiningRecordCreate(**self._valid_payload())
        with pytest.raises(ValidationError):
            record.spindle_speed = -1.0


# 3. SQLAlchemy ORM 表结构


class TestSQLAlchemyModelSchema:
    """ORM 模型表结构 / 索引 / 唯一约束。"""

    def test_tablename(self) -> None:
        assert MachiningRecord.__tablename__ == "machining_records"

    def test_required_columns_present(self) -> None:
        columns = {c.name for c in MachiningRecord.__table__.columns}
        expected = {
            "record_id",
            "machine_id",
            "tool_id",
            "material",
            "timestamp",
            "spindle_speed",
            "feed_rate",
            "tdengine_series_id",
            "process_params",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)

    def test_primary_key(self) -> None:
        pk_cols = [c.name for c in MachiningRecord.__table__.primary_key.columns]
        assert pk_cols == ["record_id"]

    def test_not_null_columns(self) -> None:
        not_nullable = {c.name for c in MachiningRecord.__table__.columns if not c.nullable}
        expected = {
            "record_id",
            "machine_id",
            "tool_id",
            "material",
            "timestamp",
            "spindle_speed",
            "feed_rate",
            "process_params",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(not_nullable)

    def test_optional_tdengine_series_id(self) -> None:
        col = MachiningRecord.__table__.columns["tdengine_series_id"]
        assert col.nullable is True

    def test_required_indexes(self) -> None:
        index_names = {idx.name for idx in MachiningRecord.__table__.indexes}
        expected = {
            "ix_machining_records_machine_id",
            "ix_machining_records_tool_id",
            "ix_machining_records_material",
            "ix_machining_records_timestamp",
        }
        assert expected.issubset(index_names)

    def test_unique_constraint(self) -> None:
        constraint_names = {con.name for con in MachiningRecord.__table__.constraints}
        assert "uq_machining_records_machine_tool_ts" in constraint_names

    def test_to_dict_serializable(self) -> None:
        record = MachiningRecord(
            machine_id="CNC-01",
            tool_id="T-EM-10",
            material="45号钢",
            timestamp=datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc),
            spindle_speed=4500.0,
            feed_rate=800.0,
            process_params={"depth_of_cut": 1.5},
        )
        d = record.to_dict()
        assert d["machine_id"] == "CNC-01"
        assert d["process_params"] == {"depth_of_cut": 1.5}
        assert isinstance(d["timestamp"], str)

    def test_repr_contains_key_fields(self) -> None:
        record = MachiningRecord(
            record_id="mrec_test",
            machine_id="CNC-01",
            tool_id="T-EM-10",
            material="45号钢",
            timestamp=datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc),
            spindle_speed=4500.0,
            feed_rate=800.0,
        )
        r = repr(record)
        assert "mrec_test" in r
        assert "CNC-01" in r


# 4. Repository CRUD 集成（基于内存 SQLite）


@pytest.fixture
def repo() -> Iterator[MachiningRecordRepository]:
    """为每个测试创建独立的内存 SQLite + Repository。"""
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    try:
        yield MachiningRecordRepository(session_factory=factory)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


class TestRepositoryCRUD:
    """MachiningRecordRepository 同步 CRUD 端到端测试。"""

    def _payload(self, **overrides) -> dict:
        base = {
            "machine_id": "CNC-01",
            "tool_id": "T-EM-10",
            "material": "45号钢",
            "spindle_speed": 4500.0,
            "feed_rate": 800.0,
            "tdengine_series_id": "ts_2026_06_11_001",
            "process_params": {"depth_of_cut": 1.5, "coolant": True},
        }
        base.update(overrides)
        return base

    def test_create_returns_record_id(self, repo: MachiningRecordRepository) -> None:
        rid = repo.create(MachiningRecordCreate(**self._payload()))
        assert rid.startswith("mrec_")
        assert len(rid) == len("mrec_") + 32

    def test_create_with_explicit_record_id(self, repo: MachiningRecordRepository) -> None:
        rid = repo.create(MachiningRecordCreate(record_id="mrec_custom_001", **self._payload()))
        assert rid == "mrec_custom_001"

    def test_get_returns_record(self, repo: MachiningRecordRepository) -> None:
        rid = repo.create(MachiningRecordCreate(**self._payload()))
        record = repo.get(rid)
        assert record is not None
        assert record.record_id == rid
        assert record.machine_id == "CNC-01"
        assert record.spindle_speed == 4500.0
        assert record.process_params["coolant"] is True

    def test_get_missing_returns_none(self, repo: MachiningRecordRepository) -> None:
        assert repo.get("mrec_does_not_exist") is None

    def test_update_modifies_field(self, repo: MachiningRecordRepository) -> None:
        rid = repo.create(MachiningRecordCreate(**self._payload()))
        updated = repo.update(
            rid,
            MachiningRecordUpdate(spindle_speed=5500.0),
        )
        assert updated is not None
        assert updated.spindle_speed == 5500.0
        assert updated.feed_rate == 800.0  # 未修改字段保持原值

    def test_update_process_params(self, repo: MachiningRecordRepository) -> None:
        rid = repo.create(MachiningRecordCreate(**self._payload()))
        updated = repo.update(
            rid,
            MachiningRecordUpdate(process_params={"depth_of_cut": 3.0, "operation": "drilling"}),
        )
        assert updated is not None
        assert updated.process_params == {
            "depth_of_cut": 3.0,
            "operation": "drilling",
        }

    def test_update_no_change_returns_record(self, repo: MachiningRecordRepository) -> None:
        rid = repo.create(MachiningRecordCreate(**self._payload()))
        updated = repo.update(rid, MachiningRecordUpdate())  # 空 patch
        assert updated is not None
        assert updated.record_id == rid

    def test_update_missing_returns_none(self, repo: MachiningRecordRepository) -> None:
        result = repo.update("mrec_missing", MachiningRecordUpdate(spindle_speed=5500.0))
        assert result is None

    def test_delete_existing_returns_true(self, repo: MachiningRecordRepository) -> None:
        rid = repo.create(MachiningRecordCreate(**self._payload()))
        assert repo.delete(rid) is True
        assert repo.get(rid) is None

    def test_delete_missing_returns_false(self, repo: MachiningRecordRepository) -> None:
        assert repo.delete("mrec_does_not_exist") is False

    def test_list_by_machine(self, repo: MachiningRecordRepository) -> None:
        # 同一机床 3 条 + 另一机床 1 条
        base_ts = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            repo.create(
                MachiningRecordCreate(
                    **self._payload(
                        timestamp=base_ts + timedelta(minutes=i),
                        spindle_speed=4000.0 + i * 100,
                    )
                )
            )
        repo.create(
            MachiningRecordCreate(
                **self._payload(
                    machine_id="CNC-02",
                    tool_id="T-EM-20",
                    timestamp=base_ts,
                )
            )
        )
        records = repo.list_by_machine("CNC-01", limit=10)
        assert len(records) == 3
        assert all(r.machine_id == "CNC-01" for r in records)
        # 按 timestamp desc 排序
        assert records[0].timestamp > records[-1].timestamp

    def test_list_all_pagination(self, repo: MachiningRecordRepository) -> None:
        base_ts = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(5):
            repo.create(
                MachiningRecordCreate(
                    **self._payload(
                        machine_id=f"CNC-{i:02d}",
                        timestamp=base_ts + timedelta(minutes=i),
                    )
                )
            )
        records = repo.list_all(limit=3, offset=0)
        assert len(records) == 3
        records2 = repo.list_all(limit=3, offset=3)
        assert len(records2) == 2

    def test_count(self, repo: MachiningRecordRepository) -> None:
        assert repo.count() == 0
        repo.create(MachiningRecordCreate(**self._payload()))
        assert repo.count() == 1

    def test_get_by_triple(self, repo: MachiningRecordRepository) -> None:
        ts = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
        rid = repo.create(MachiningRecordCreate(**self._payload(timestamp=ts)))
        found = repo.get_by_triple("CNC-01", "T-EM-10", ts)
        assert found is not None
        assert found.record_id == rid

        not_found = repo.get_by_triple("CNC-01", "T-EM-10", ts + timedelta(hours=1))
        assert not_found is None

    def test_unique_constraint_enforced(self, repo: MachiningRecordRepository) -> None:
        ts = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
        repo.create(MachiningRecordCreate(**self._payload(timestamp=ts)))
        with pytest.raises(Exception):  # IntegrityError
            repo.create(MachiningRecordCreate(**self._payload(timestamp=ts)))

    def test_full_crud_flow(self, repo: MachiningRecordRepository) -> None:
        """任务 M0.4 验收脚本的等价测试。"""
        create_data = MachiningRecordCreate(**self._payload())
        record_id = repo.create(create_data)
        assert record_id is not None

        record = repo.get(record_id)
        assert record is not None
        assert record.machine_id == "CNC-01"

        updated = repo.update(record_id, MachiningRecordUpdate(spindle_speed=5000.0))
        assert updated is not None
        assert updated.spindle_speed == 5000.0

        deleted = repo.delete(record_id)
        assert deleted is True
        assert repo.get(record_id) is None


# 5. Schema 校验


class TestSchemaIntegration:
    """Pydantic 与 ORM 之间的双向转换 / 完整性。"""

    def test_pydantic_orm_round_trip(self) -> None:
        from app.models.machining_record import MachiningRecordRead

        # Any 注解：**展开到 Pydantic 参数时 mypy 不按 dict 不变性报错
        payload: dict[str, Any] = {
            "machine_id": "CNC-01",
            "tool_id": "T-EM-10",
            "material": "45号钢",
            "spindle_speed": 4500.0,
            "feed_rate": 800.0,
            "tdengine_series_id": "ts_001",
            "process_params": {"k": "v"},
            "timestamp": datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc),
        }
        record = MachiningRecordRead(record_id="mrec_test_001", **payload)
        # 确保可通过 model_dump 完整序列化
        d = record.model_dump()
        assert d["record_id"] == "mrec_test_001"
        assert d["process_params"] == {"k": "v"}
