"""数据集契约单元测试.

对应 ADR-005 第 4 章 / app/contracts/dataset.py.

覆盖：
- DatasetStatus 枚举与状态机转换
- DatasetSchema.validate()（fields 非空、type 合法性、primary_key 存在性）
- DatasetVersion（semver 校验、content_hash/row_count/size_bytes/storage_uri 校验）
- LineageRecord（source_type 合法性、record_id/target/source_ref 非空）
- IDatasetStore / ILineageStore 抽象接口
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.contracts.dataset import (
    VALID_DATASET_STATUS_TRANSITIONS,
    DatasetSchema,
    DatasetStatus,
    DatasetVersion,
    IDatasetStore,
    ILineageStore,
    LineageRecord,
    _is_valid_semver,
)


@pytest.mark.unit
@pytest.mark.contracts
class TestDatasetStatus:
    """DatasetStatus 枚举与状态机."""

    def test_enum_values(self):
        """枚举值与字符串字面量对齐."""
        assert DatasetStatus.DRAFT == "draft"
        assert DatasetStatus.PUBLISHED == "published"
        assert DatasetStatus.DEPRECATED == "deprecated"
        assert DatasetStatus.ARCHIVED == "archived"

    def test_status_count(self):
        """共 4 个状态."""
        assert len(list(DatasetStatus)) == 4

    def test_draft_can_publish_or_archive(self):
        """DRAFT 可转向 PUBLISHED 或 ARCHIVED."""
        transitions = VALID_DATASET_STATUS_TRANSITIONS[DatasetStatus.DRAFT]
        assert DatasetStatus.PUBLISHED in transitions
        assert DatasetStatus.ARCHIVED in transitions

    def test_published_can_deprecate_or_archive(self):
        """PUBLISHED 可转向 DEPRECATED 或 ARCHIVED（内容不可变）."""
        transitions = VALID_DATASET_STATUS_TRANSITIONS[DatasetStatus.PUBLISHED]
        assert DatasetStatus.DEPRECATED in transitions
        assert DatasetStatus.ARCHIVED in transitions

    def test_deprecated_can_only_archive(self):
        """DEPRECATED 只能转向 ARCHIVED."""
        transitions = VALID_DATASET_STATUS_TRANSITIONS[DatasetStatus.DEPRECATED]
        assert transitions == {DatasetStatus.ARCHIVED}

    def test_archived_is_terminal(self):
        """ARCHIVED 是终态，无后续转换."""
        assert VALID_DATASET_STATUS_TRANSITIONS[DatasetStatus.ARCHIVED] == set()

    def test_published_cannot_back_to_draft(self):
        """PUBLISHED 不能回到 DRAFT（不可逆）."""
        transitions = VALID_DATASET_STATUS_TRANSITIONS[DatasetStatus.PUBLISHED]
        assert DatasetStatus.DRAFT not in transitions


@pytest.mark.unit
@pytest.mark.contracts
class TestDatasetSchemaValidate:
    """DatasetSchema.validate() 校验逻辑."""

    def test_valid_schema(self):
        """合法 schema 无错误."""
        schema = DatasetSchema(
            fields={
                "force_x": {"type": "float", "required": True},
                "force_y": {"type": "float", "required": True},
                "label": {"type": "str", "required": False},
            },
            primary_key=["force_x", "force_y"],
        )
        assert schema.validate() == []

    def test_empty_fields_rejected(self):
        """fields 为空应报错."""
        schema = DatasetSchema(fields={})
        errors = schema.validate()
        assert len(errors) == 1
        assert "不能为空" in errors[0]

    def test_invalid_field_type_rejected(self):
        """字段 type 不在合法集合中应报错."""
        schema = DatasetSchema(
            fields={
                "col1": {"type": "tensor"},  # 非法类型
            }
        )
        errors = schema.validate()
        assert any("type 不合法" in e for e in errors)

    def test_missing_type_key_rejected(self):
        """字段缺少 type 键应报错."""
        schema = DatasetSchema(
            fields={
                "col1": {"required": True},  # 缺 type
            }
        )
        errors = schema.validate()
        assert any("缺少 type" in e for e in errors)

    def test_primary_key_must_exist_in_fields(self):
        """primary_key 引用的字段必须在 fields 中定义."""
        schema = DatasetSchema(
            fields={
                "col1": {"type": "str"},
            },
            primary_key=["col1", "col2"],  # col2 未定义
        )
        errors = schema.validate()
        assert any("col2" in e and "未在 fields" in e for e in errors)

    def test_all_valid_types_accepted(self):
        """所有合法 type 都应通过."""
        valid_types = ["float", "int", "str", "bool", "datetime", "list", "dict"]
        fields = {f"col_{t}": {"type": t} for t in valid_types}
        schema = DatasetSchema(fields=fields)
        errors = schema.validate()
        assert errors == []

    def test_empty_field_name_rejected(self):
        """空字段名应报错."""
        schema = DatasetSchema(
            fields={
                "": {"type": "str"},
            }
        )
        errors = schema.validate()
        assert any("字段名不能为空" in e for e in errors)


@pytest.mark.unit
@pytest.mark.contracts
class TestDatasetVersion:
    """DatasetVersion dataclass 构造校验."""

    def _make_schema(self) -> DatasetSchema:
        return DatasetSchema(fields={"x": {"type": "float"}})

    def _make_version(self, **overrides) -> DatasetVersion:
        defaults = dict(
            dataset_id="ds-1",
            version="1.0.0",
            status=DatasetStatus.DRAFT,
            schema=self._make_schema(),
            content_hash="sha256:abc",
            row_count=100,
            size_bytes=4096,
            created_at=datetime.utcnow(),
            created_by="user-1",
            storage_uri="file:///data/ds-1/v1",
        )
        defaults.update(overrides)
        return DatasetVersion(**defaults)

    def test_valid_version(self):
        """合法版本构造成功."""
        v = self._make_version()
        assert v.dataset_id == "ds-1"
        assert v.version == "1.0.0"

    def test_empty_dataset_id_rejected(self):
        """dataset_id 为空应报错."""
        with pytest.raises(ValueError, match="dataset_id"):
            self._make_version(dataset_id="")

    def test_invalid_semver_rejected(self):
        """version 不符合 semver 应报错."""
        with pytest.raises(ValueError, match="semver"):
            self._make_version(version="v1.0")

    def test_empty_content_hash_rejected(self):
        """content_hash 为空应报错."""
        with pytest.raises(ValueError, match="content_hash"):
            self._make_version(content_hash="")

    def test_negative_row_count_rejected(self):
        """row_count 为负应报错."""
        with pytest.raises(ValueError, match="row_count"):
            self._make_version(row_count=-1)

    def test_negative_size_bytes_rejected(self):
        """size_bytes 为负应报错."""
        with pytest.raises(ValueError, match="size_bytes"):
            self._make_version(size_bytes=-100)

    def test_empty_storage_uri_rejected(self):
        """storage_uri 为空应报错."""
        with pytest.raises(ValueError, match="storage_uri"):
            self._make_version(storage_uri="")

    def test_lineage_optional(self):
        """lineage 是可选字段，默认 None."""
        v = self._make_version()
        assert v.lineage is None


@pytest.mark.unit
@pytest.mark.contracts
class TestSemverValidator:
    """_is_valid_semver 函数（dataset 版本，三段式严格）."""

    @pytest.mark.parametrize(
        "version,expected",
        [
            ("1.0.0", True),
            ("0.1.0", True),
            ("10.20.30", True),
            ("1.0.0-alpha", True),  # 支持 prerelease
            ("1.0.0-rc.1", True),
            ("", False),
            ("1", False),  # 必须三段
            ("1.0", False),  # dataset 要求三段
            ("1.0.x", False),  # 非数字
            ("v1.0.0", False),  # 前缀
            ("1.0.0.", False),  # 多余点
        ],
    )
    def test_semver_validation(self, version, expected):
        assert _is_valid_semver(version) is expected


@pytest.mark.unit
@pytest.mark.contracts
class TestLineageRecord:
    """LineageRecord dataclass 构造校验."""

    def _make_record(self, **overrides) -> LineageRecord:
        defaults = dict(
            record_id="rec-1",
            target="dataset://my-ds/v1",
            source_type="task",
            source_ref="job-001",
            inputs=["dataset://upstream/v1"],
            outputs=["dataset://my-ds/v1"],
            operation="preprocess",
        )
        defaults.update(overrides)
        return LineageRecord(**defaults)

    def test_valid_record(self):
        """合法记录构造成功."""
        rec = self._make_record()
        assert rec.record_id == "rec-1"
        assert rec.target == "dataset://my-ds/v1"

    @pytest.mark.parametrize("source_type", ["task", "workflow", "manual", "external"])
    def test_valid_source_types(self, source_type):
        """四种合法 source_type 都应通过."""
        rec = self._make_record(source_type=source_type)
        assert rec.source_type == source_type

    def test_invalid_source_type_rejected(self):
        """非法 source_type 应报错."""
        with pytest.raises(ValueError, match="source_type"):
            self._make_record(source_type="hack")

    def test_empty_record_id_rejected(self):
        with pytest.raises(ValueError, match="record_id"):
            self._make_record(record_id="")

    def test_empty_target_rejected(self):
        with pytest.raises(ValueError, match="target"):
            self._make_record(target="")

    def test_empty_source_ref_rejected(self):
        with pytest.raises(ValueError, match="source_ref"):
            self._make_record(source_ref="")

    def test_default_timestamp_auto_filled(self):
        """timestamp 默认自动填充当前时间."""
        rec = self._make_record()
        assert isinstance(rec.timestamp, datetime)

    def test_default_metadata_empty(self):
        rec = self._make_record()
        assert rec.metadata == {}


@pytest.mark.unit
@pytest.mark.contracts
class TestAbstractInterfaces:
    """IDatasetStore / ILineageStore 抽象接口不可实例化."""

    def test_dataset_store_abstract(self):
        with pytest.raises(TypeError):
            IDatasetStore()  # type: ignore[abstract]

    def test_lineage_store_abstract(self):
        with pytest.raises(TypeError):
            ILineageStore()  # type: ignore[abstract]

    def test_dataset_store_can_be_subclassed(self):
        """IDatasetStore 可被具体实现子类化."""

        class DummyStore(IDatasetStore):
            async def create(self, name, schema, *, owner_id, description=""):
                return "ds-1"

            async def commit_version(
                self, dataset_id, records, *, version=None, lineage=None
            ):
                return DatasetVersion(
                    dataset_id=dataset_id,
                    version=version or "1.0.0",
                    status=DatasetStatus.PUBLISHED,
                    schema=schema,
                    content_hash="sha256:x",
                    row_count=len(records),
                    size_bytes=0,
                    created_at=datetime.utcnow(),
                    created_by="test",
                    storage_uri="file:///tmp/x",
                )

            async def get_version(self, dataset_id, version=None):
                raise KeyError

            async def read(self, dataset_id, version=None, *, batch_size=1000):
                if False:  # pragma: no cover
                    yield []

            async def list_versions(self, dataset_id):
                return []

            async def deprecate(self, dataset_id, version):
                return None

        store = DummyStore()
        assert store is not None

    def test_lineage_store_can_be_subclassed(self):
        """ILineageStore 可被具体实现子类化."""

        class DummyLineage(ILineageStore):
            async def record(self, lineage):
                return lineage.record_id

            async def get_upstream(self, target_uri, *, depth=10):
                return []

            async def get_downstream(self, target_uri, *, depth=10):
                return []

            async def visualize(self, target_uri):
                return {"nodes": [], "edges": []}

        store = DummyLineage()
        assert store is not None
