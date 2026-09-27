"""Unit tests for cutting_experience repository (P2-2) with SQLite in-memory DB."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.contracts.cutting_experience import (
    CuttingExperience,
    CuttingParameters,
    CuttingResults,
    ExperienceQuery,
    MachiningAnomaly,
    MachiningResult,
    MachiningType,
)
from app.database.models.cutting_experience import (
    Base,
    CuttingExperienceRecord,
)
from app.services.domain import cutting_experience_repository as repo


@pytest.fixture
def sessionmaker():
    """SQLite in-memory async sessionmaker with the cutting_experience table."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import asyncio

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def patch_sessionmaker(sessionmaker, monkeypatch):
    """Replace get_sessionmaker with the test sessionmaker."""
    monkeypatch.setattr(
        "app.services.domain.cutting_experience_repository.get_sessionmaker",
        lambda: sessionmaker,
    )
    return sessionmaker


def _make_record(**overrides) -> CuttingExperience:
    base = {
        "machine_id": "VM-001",
        "tool_id": "T-12",
        "material": "AL6061",
        "parameters": CuttingParameters(
            depth_of_cut_mm=2.0, feed_mm_per_rev=0.2, spindle_rpm=8000
        ),
        "results": CuttingResults(
            cycle_time_s=120.5, surface_roughness_ra=1.2, result=MachiningResult.OK
        ),
    }
    base.update(overrides)
    return CuttingExperience(**base)


class TestCreateCuttingExperience:
    @pytest.mark.asyncio
    async def test_create_and_fetch(self, patch_sessionmaker) -> None:
        rec = _make_record()
        created = await repo.create_cutting_experience(rec)
        assert created["machine_id"] == "VM-001"
        assert "created_at" in created

        # 查询时使用数据库存储的 ID 格式（exp_{hex}）
        # _id_or_new 会将 UUID 转为 exp_{hex} 格式
        hex_id = rec.id.hex.replace("-", "")
        pk = f"exp_{hex_id}"
        fetched_by_pk = await repo.get_cutting_experience(pk)
        assert fetched_by_pk is not None, f"Cannot fetch record with ID: {pk}"
        assert fetched_by_pk["tool_id"] == "T-12"
        assert fetched_by_pk["parameters"]["spindle_rpm"] == 8000

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, patch_sessionmaker) -> None:
        # 使用字符串形式 ID（与数据库一致）
        fake_id = str(uuid.uuid4())
        assert await repo.get_cutting_experience(fake_id) is None

    @pytest.mark.asyncio
    async def test_create_many(self, patch_sessionmaker) -> None:
        recs = [_make_record() for _ in range(3)]
        count = await repo.create_many_cutting_experiences(recs)
        assert count == 3

    @pytest.mark.asyncio
    async def test_create_many_empty(self, patch_sessionmaker) -> None:
        assert await repo.create_many_cutting_experiences([]) == 0


class TestListCuttingExperiences:
    @pytest.mark.asyncio
    async def test_list_with_filter(self, patch_sessionmaker) -> None:
        await repo.create_cutting_experience(_make_record(machine_id="VM-001"))
        await repo.create_cutting_experience(_make_record(machine_id="VM-002"))

        result = await repo.list_cutting_experiences(
            ExperienceQuery(machine_id="VM-001")
        )
        assert result["total"] == 1
        assert result["records"][0]["machine_id"] == "VM-001"

    @pytest.mark.asyncio
    async def test_list_pagination(self, patch_sessionmaker) -> None:
        for i in range(5):
            await repo.create_cutting_experience(
                _make_record(machine_id=f"VM-{i:03d}")
            )

        page = await repo.list_cutting_experiences(
            ExperienceQuery(limit=2, offset=0)
        )
        assert page["total"] == 5
        assert len(page["records"]) == 2

        page2 = await repo.list_cutting_experiences(
            ExperienceQuery(limit=2, offset=2)
        )
        assert len(page2["records"]) == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_result(self, patch_sessionmaker) -> None:
        await repo.create_cutting_experience(
            _make_record(
                results=CuttingResults(
                    cycle_time_s=10, result=MachiningResult.REWORK
                )
            )
        )
        await repo.create_cutting_experience(_make_record())

        result = await repo.list_cutting_experiences(
            ExperienceQuery(result=MachiningResult.REWORK)
        )
        assert result["total"] == 1
        assert result["records"][0]["results"]["result"] == "rework"

    @pytest.mark.asyncio
    async def test_list_filter_has_anomaly(self, patch_sessionmaker) -> None:
        await repo.create_cutting_experience(
            _make_record(
                anomalies=[
                    MachiningAnomaly(anomaly_type="chatter", severity=5)
                ]
            )
        )
        await repo.create_cutting_experience(_make_record())

        with_anomaly = await repo.list_cutting_experiences(
            ExperienceQuery(has_anomaly=True)
        )
        assert with_anomaly["total"] == 1

        clean = await repo.list_cutting_experiences(
            ExperienceQuery(has_anomaly=False)
        )
        assert clean["total"] == 1


class TestAggregateStats:
    @pytest.mark.asyncio
    async def test_stats_empty(self, patch_sessionmaker) -> None:
        stats = await repo.aggregate_experience_stats(ExperienceQuery())
        assert stats.total_records == 0
        assert stats.avg_cycle_time_s is None

    @pytest.mark.asyncio
    async def test_stats_with_data(self, patch_sessionmaker) -> None:
        await repo.create_cutting_experience(
            _make_record(
                results=CuttingResults(
                    cycle_time_s=100.0,
                    surface_roughness_ra=1.0,
                    tool_wear_percent=10.0,
                    result=MachiningResult.OK,
                )
            )
        )
        await repo.create_cutting_experience(
            _make_record(
                results=CuttingResults(
                    cycle_time_s=200.0,
                    surface_roughness_ra=3.0,
                    tool_wear_percent=30.0,
                    result=MachiningResult.SCRAP,
                )
            )
        )
        stats = await repo.aggregate_experience_stats(ExperienceQuery())
        assert stats.total_records == 2
        assert stats.avg_cycle_time_s == 150.0
        assert stats.avg_surface_roughness_ra == 2.0
        assert stats.avg_tool_wear_percent == 20.0
        assert stats.ok_rate == 0.5
        assert stats.anomaly_rate == 0.0


class TestDeleteCuttingExperience:
    @pytest.mark.asyncio
    async def test_delete_existing(self, patch_sessionmaker) -> None:
        rec = _make_record()
        created = await repo.create_cutting_experience(rec)
        # 删除时使用数据库存储的 ID 格式（exp_{hex}）
        hex_id = rec.id.hex.replace("-", "")
        pk = f"exp_{hex_id}"
        assert await repo.delete_cutting_experience(pk) is True
        assert await repo.get_cutting_experience(rec.id) is None

    @pytest.mark.asyncio
    async def test_delete_missing(self, patch_sessionmaker) -> None:
        assert await repo.delete_cutting_experience(uuid.uuid4()) is False


class TestDatabaseNotConfigured:
    @pytest.mark.asyncio
    async def test_create_raises_runtime_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.services.domain.cutting_experience_repository.get_sessionmaker",
            lambda: None,
        )
        with pytest.raises(RuntimeError):
            await repo.create_cutting_experience(_make_record())
