"""配置契约单元测试.

对应 core-contracts-design.md 第 6 章 / app/contracts/config.py.

覆盖：
- ConfigField（type 校验、sweep 校验）
- ConfigSpec（version semver、字段名唯一性、validate、materialize）
- _is_valid_semver（两段式 / 三段式）
- _check_type（bool 与 int 区分）
- IConfigStore / IConfigSource 抽象接口
"""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts.config import (
    VALID_FIELD_TYPES,
    VALID_SWEEP_KINDS,
    ConfigField,
    ConfigSpec,
    IConfigSource,
    IConfigStore,
    _check_type,
    _is_valid_semver,
)


@pytest.mark.unit
@pytest.mark.contracts
class TestConfigField:
    """ConfigField dataclass 构造校验."""

    def test_valid_field(self):
        f = ConfigField(name="lr", type="float", default=0.001, description="学习率")
        assert f.name == "lr"
        assert f.type == "float"

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            ConfigField(name="", type="float", default=0.0)

    def test_non_string_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            ConfigField(name=123, type="float", default=0.0)  # type: ignore[arg-type]

    def test_invalid_type_rejected(self):
        with pytest.raises(ValueError, match="type"):
            ConfigField(name="x", type="tensor", default=None)

    def test_all_valid_types_accepted(self):
        """所有 VALID_FIELD_TYPES 中的类型都应通过."""
        for t in VALID_FIELD_TYPES:
            f = ConfigField(name=f"col_{t}", type=t, default=None)
            assert f.type == t

    def test_sweep_grid_valid(self):
        f = ConfigField(
            name="lr",
            type="float",
            default=0.001,
            sweep={"kind": "grid", "values": [0.001, 0.01, 0.1]},
        )
        assert f.sweep["kind"] == "grid"

    def test_sweep_random_valid(self):
        f = ConfigField(
            name="lr",
            type="float",
            default=0.001,
            sweep={"kind": "random", "values": [0.001, 0.01, 0.1]},
        )
        assert f.sweep["kind"] == "random"

    def test_sweep_bayesian_valid(self):
        f = ConfigField(
            name="lr",
            type="float",
            default=0.001,
            sweep={"kind": "bayesian", "values": [0.001, 0.01, 0.1]},
        )
        assert f.sweep["kind"] == "bayesian"

    def test_sweep_invalid_kind_rejected(self):
        with pytest.raises(ValueError, match="sweep.kind"):
            ConfigField(
                name="lr",
                type="float",
                default=0.001,
                sweep={"kind": "magic", "values": [0.001]},
            )

    def test_sweep_empty_values_rejected(self):
        with pytest.raises(ValueError, match="sweep.values"):
            ConfigField(
                name="lr",
                type="float",
                default=0.001,
                sweep={"kind": "grid", "values": []},
            )

    def test_sweep_non_list_values_rejected(self):
        with pytest.raises(ValueError, match="sweep.values"):
            ConfigField(
                name="lr",
                type="float",
                default=0.001,
                sweep={"kind": "grid", "values": "not-a-list"},
            )

    def test_optional_fields_default(self):
        f = ConfigField(name="x", type="int", default=0)
        assert f.description == ""
        assert f.required is False
        assert f.choices == []
        assert f.min is None
        assert f.max is None
        assert f.sweep is None


@pytest.mark.unit
@pytest.mark.contracts
class TestConfigSpec:
    """ConfigSpec dataclass."""

    def _make_spec(self, **overrides) -> ConfigSpec:
        defaults = dict(
            name="ltc_chatter",
            version="3.0",
            description="LTC 颤振预测实验配置",
            fields=[
                ConfigField(name="lr", type="float", default=0.001),
                ConfigField(name="epochs", type="int", default=100),
                ConfigField(name="hidden_size", type="int", default=32),
            ],
        )
        defaults.update(overrides)
        return ConfigSpec(**defaults)

    def test_valid_spec(self):
        spec = self._make_spec()
        assert spec.name == "ltc_chatter"
        assert len(spec.fields) == 3

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            self._make_spec(name="")

    def test_invalid_semver_rejected(self):
        with pytest.raises(ValueError, match="version"):
            self._make_spec(version="v3.0")

    def test_two_segment_semver_allowed(self):
        """config 契约的 _is_valid_semver 兼容两段式 '3.0'."""
        spec = self._make_spec(version="3.0")
        assert spec.version == "3.0"

    def test_three_segment_semver_allowed(self):
        spec = self._make_spec(version="3.0.1")
        assert spec.version == "3.0.1"

    def test_duplicate_field_names_rejected(self):
        """字段名重复应报错."""
        with pytest.raises(ValueError, match="duplicate field"):
            self._make_spec(
                fields=[
                    ConfigField(name="lr", type="float", default=0.001),
                    ConfigField(name="lr", type="float", default=0.01),  # 重复
                ]
            )

    def test_get_field_found(self):
        spec = self._make_spec()
        f = spec.get_field("lr")
        assert f is not None
        assert f.type == "float"

    def test_get_field_not_found(self):
        spec = self._make_spec()
        assert spec.get_field("nonexistent") is None

    def test_validate_passes_with_valid_values(self):
        spec = self._make_spec()
        errors = spec.validate({"lr": 0.01, "epochs": 50, "hidden_size": 64})
        assert errors == []

    def test_validate_missing_required_field(self):
        spec = self._make_spec(
            fields=[ConfigField(name="lr", type="float", default=None, required=True)]
        )
        errors = spec.validate({})
        assert any("Missing required" in e for e in errors)

    def test_validate_wrong_type(self):
        spec = self._make_spec()
        errors = spec.validate({"lr": "not-a-number", "epochs": 50, "hidden_size": 64})
        assert any("expected float" in e for e in errors)

    def test_validate_choices_violation(self):
        spec = self._make_spec(
            fields=[
                ConfigField(
                    name="optimizer",
                    type="str",
                    default="adam",
                    choices=["adam", "sgd", "rmsprop"],
                )
            ]
        )
        errors = spec.validate({"optimizer": "magic"})
        assert any("not in choices" in e for e in errors)

    def test_validate_min_max_range(self):
        spec = self._make_spec(
            fields=[
                ConfigField(name="lr", type="float", default=0.001, min=0.0, max=1.0),
            ]
        )
        # 低于 min
        errors = spec.validate({"lr": -0.1})
        assert any("< min" in e for e in errors)
        # 高于 max
        errors = spec.validate({"lr": 2.0})
        assert any("> max" in e for e in errors)
        # 合法范围
        errors = spec.validate({"lr": 0.5})
        assert errors == []

    def test_materialize_fills_defaults(self):
        """materialize 用 default 填充缺失字段."""
        spec = self._make_spec()
        result = spec.materialize({"lr": 0.01})  # epochs / hidden_size 缺失
        assert result["lr"] == 0.01
        assert result["epochs"] == 100  # default
        assert result["hidden_size"] == 32  # default

    def test_materialize_keeps_extra_fields(self):
        """materialize 保留 spec 之外的额外字段."""
        spec = self._make_spec()
        result = spec.materialize({"lr": 0.01, "custom_flag": True})
        assert result["custom_flag"] is True

    def test_materialize_raises_on_validation_failure(self):
        """validate 失败时 materialize 抛 ValueError."""
        spec = self._make_spec(
            fields=[ConfigField(name="lr", type="float", default=None, required=True)]
        )
        with pytest.raises(ValueError, match="validation failed"):
            spec.materialize({})


@pytest.mark.unit
@pytest.mark.contracts
class TestSemverValidator:
    """_is_valid_semver（config 版本，兼容两段式）."""

    @pytest.mark.parametrize(
        "version,expected",
        [
            ("3.0", True),  # 两段式
            ("3.0.0", True),  # 三段式
            ("1.2", True),
            ("1.2.3", True),
            ("", False),
            ("1", False),  # 只有一段
            ("1.2.3.4", False),  # 四段
            ("v1.0", False),  # 前缀
            ("1.x", False),  # 非数字
            (None, False),  # type: ignore[arg-type]
            (123, False),  # type: ignore[arg-type]
        ],
    )
    def test_semver_validation(self, version, expected):
        assert _is_valid_semver(version) is expected


@pytest.mark.unit
@pytest.mark.contracts
class TestCheckType:
    """_check_type 函数（特别注意 bool 是 int 子类）."""

    def test_int_accepts_int(self):
        assert _check_type("x", 42, "int") is None

    def test_int_rejects_bool(self):
        """bool 不应被当作 int 接受."""
        assert _check_type("x", True, "int") is not None

    def test_int_rejects_float(self):
        assert _check_type("x", 3.14, "int") is not None

    def test_float_accepts_int_and_float(self):
        """float 字段接受 int 和 float."""
        assert _check_type("x", 42, "float") is None
        assert _check_type("x", 3.14, "float") is None

    def test_float_rejects_bool(self):
        assert _check_type("x", True, "float") is not None

    def test_bool_rejects_int(self):
        assert _check_type("x", 1, "bool") is not None

    def test_str_rejects_int(self):
        assert _check_type("x", 42, "str") is not None

    def test_list_rejects_tuple(self):
        assert _check_type("x", (1, 2), "list") is not None

    def test_dict_rejects_list(self):
        assert _check_type("x", [1, 2], "dict") is not None


@pytest.mark.unit
@pytest.mark.contracts
class TestAbstractInterfaces:
    """IConfigStore / IConfigSource 抽象接口."""

    def test_config_store_abstract(self):
        with pytest.raises(TypeError):
            IConfigStore()  # type: ignore[abstract]

    def test_config_source_abstract(self):
        with pytest.raises(TypeError):
            IConfigSource()  # type: ignore[abstract]

    def test_config_store_can_be_subclassed(self):
        class DummyStore(IConfigStore):
            def register(self, spec):
                return None

            def get_spec(self, name):
                raise KeyError(name)

            def load_yaml(self, path):
                return {}

            def resolve(self, spec_name, overrides=None):
                return {}

            def expand_sweep(self, spec_name, sweep_config):
                return []

        store = DummyStore()
        assert store is not None

    def test_config_source_can_be_subclassed(self):
        class DummySource(IConfigSource):
            def priority(self):
                return 10

            def get(self, key):
                raise KeyError(key)

            def keys(self):
                return []

        src = DummySource()
        assert src.priority() == 10
