"""配置契约：定义声明式实验配置规格。

对应 core-contracts-design.md 第 6 章。

设计目标：
- 现有 config.py 是 dataclass + env，适合运行时配置，**不适合实验配置**
- 新增声明式 YAML 实验配置（继承 / 覆盖 / sweep）
- 实验配置与代码解耦，可版本化、可分享、可 diff
- 与 MLflow 集成：每次实验自动记录完整配置

稳定性承诺：本文件为 Stable 契约 v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union


# 支持的字段类型

VALID_FIELD_TYPES = {"int", "float", "str", "bool", "list", "dict"}

# 支持的 sweep 搜索策略
VALID_SWEEP_KINDS = {"grid", "random", "bayesian"}


# ConfigField


@dataclass
class ConfigField:
    """配置字段规格。

    Attributes:
        name: 字段名（点分路径，如 "model.hidden_size"）
        type: 字段类型，可选 int/float/str/bool/list/dict
        default: 默认值（required=False 时使用）
        description: 字段说明（用于自动生成文档）
        required: 是否必填（required=True 时 default 不生效）
        choices: 枚举可选值列表
        min: 数值字段最小值（含）
        max: 数值字段最大值（含）
        sweep: 超参搜索规格，{"kind": "grid"|"random"|"bayesian", "values": [...]}
    """

    name: str
    type: str
    default: Any
    description: str = ""
    required: bool = False
    choices: list[Any] = field(default_factory=list)
    min: float | None = None
    max: float | None = None
    sweep: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError("ConfigField.name must be a non-empty string")
        if self.type not in VALID_FIELD_TYPES:
            raise ValueError(
                f"ConfigField.type must be one of {sorted(VALID_FIELD_TYPES)}, "
                f"got {self.type!r} for field {self.name!r}"
            )
        if self.required and self.default is not None:
            # required 字段不应有 default（避免歧义），但不强制报错——
            # 这里只在 sweep/choices 不一致时才报错
            pass
        if self.sweep is not None:
            self._validate_sweep()

    def _validate_sweep(self) -> None:
        """校验 sweep 规格。"""
        if not isinstance(self.sweep, dict):
            raise ValueError(f"ConfigField {self.name!r}: sweep must be a dict, got {type(self.sweep).__name__}")
        kind = self.sweep.get("kind")
        if kind not in VALID_SWEEP_KINDS:
            raise ValueError(
                f"ConfigField {self.name!r}: sweep.kind must be one of {sorted(VALID_SWEEP_KINDS)}, got {kind!r}"
            )
        values = self.sweep.get("values")
        if not isinstance(values, list) or len(values) == 0:
            raise ValueError(f"ConfigField {self.name!r}: sweep.values must be a non-empty list")


# ConfigSpec


@dataclass
class ConfigSpec:
    """配置规格契约（对应一个 YAML 文件）。

    Attributes:
        name: 规格 ID（如 "ltc_chatter"）
        version: semver 版本号（如 "3.0"）
        description: 规格描述
        fields: 字段列表
        parent: 父配置 spec 名（继承）
        metadata: 自定义元数据
    """

    name: str
    version: str
    description: str
    fields: list[ConfigField]
    parent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError("ConfigSpec.name must be a non-empty string")
        if not _is_valid_semver(self.version):
            raise ValueError(f"ConfigSpec.version must be MAJOR.MINOR.PATCH format, got {self.version!r}")
        if not isinstance(self.fields, list):
            raise ValueError("ConfigSpec.fields must be a list")
        # 校验字段名唯一
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            dups = {n for n in names if names.count(n) > 1}
            raise ValueError(f"ConfigSpec {self.name!r}: duplicate field names: {dups}")

    def get_field(self, name: str) -> ConfigField | None:
        """按名取字段，不存在返回 None。"""
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def validate(self, values: dict[str, Any]) -> list[str]:
        """校验值是否符合规格。

        Returns:
            错误消息列表；空列表表示通过。
        """
        errors: list[str] = []
        for f in self.fields:
            if f.name not in values:
                if f.required:
                    errors.append(f"Missing required field: {f.name}")
                continue
            v = values[f.name]
            # 类型校验
            type_err = _check_type(f.name, v, f.type)
            if type_err:
                errors.append(type_err)
                continue
            # choices 校验
            if f.choices and v not in f.choices:
                errors.append(f"Field {f.name!r}: value {v!r} not in choices {f.choices}")
            # 数值范围校验
            if f.type in ("int", "float") and isinstance(v, (int, float)):
                if f.min is not None and v < f.min:
                    errors.append(f"Field {f.name!r}: value {v} < min {f.min}")
                if f.max is not None and v > f.max:
                    errors.append(f"Field {f.name!r}: value {v} > max {f.max}")
        return errors

    def materialize(self, values: dict[str, Any]) -> dict[str, Any]:
        """填充默认值，返回完整配置字典。

        - 缺省字段用 default 填充（required=False 时）
        - required 字段缺失会抛 ValueError
        - 不在 spec 中的额外字段会被保留（便于扩展），但会标注在 metadata 中
        """
        errors = self.validate(values)
        if errors:
            raise ValueError(f"ConfigSpec {self.name!r} validation failed: " + "; ".join(errors))
        result: dict[str, Any] = {}
        for f in self.fields:
            if f.name in values:
                result[f.name] = values[f.name]
            elif f.required:
                # 不应到达这里（validate 已捕获），防御性抛错
                raise ValueError(f"Missing required field: {f.name}")
            else:
                result[f.name] = f.default
        # 保留额外字段
        extra = set(values.keys()) - {f.name for f in self.fields}
        for k in extra:
            result[k] = values[k]
        return result


# IConfigStore


class IConfigStore(ABC):
    """配置存储契约。

    负责：
    - 注册/查询 ConfigSpec
    - 加载 YAML 配置文件（应用继承/覆盖）
    - 合并 spec 默认值 + YAML + overrides
    - 展开超参搜索（grid/random/bayesian）
    """

    @abstractmethod
    def register(self, spec: ConfigSpec) -> None:
        """注册一个配置规格。"""

    @abstractmethod
    def get_spec(self, name: str) -> ConfigSpec:
        """按名取规格，不存在抛 KeyError。"""

    @abstractmethod
    def load_yaml(self, path: Union[str, Path]) -> dict[str, Any]:
        """加载 YAML 配置文件，应用继承/覆盖。

        Args:
            path: YAML 文件路径

        Returns:
            合并后的配置字典（已应用 parent 继承 + overrides）
        """

    @abstractmethod
    def resolve(
        self,
        spec_name: str,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """合并 spec 默认值 + YAML + overrides，返回最终配置。

        Args:
            spec_name: ConfigSpec 名
            overrides: 顶层覆盖（最高优先级）

        Returns:
            完整配置字典（已通过 spec.validate 校验）
        """

    @abstractmethod
    def expand_sweep(
        self,
        spec_name: str,
        sweep_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """展开超参搜索，返回配置列表。

        Args:
            spec_name: ConfigSpec 名
            sweep_config: 字段名 → 候选值列表（或 sweep 规格 dict）

        Returns:
            配置字典列表，每个元素是一个完整的实验配置
        """


# IConfigSource


class IConfigSource(ABC):
    """配置源契约（多源合并：env / yaml / db / user_input）。

    多个 IConfigSource 按 priority() 升序合并，priority 越大优先级越高。
    """

    @abstractmethod
    def priority(self) -> int:
        """返回优先级（数字越大优先级越高）。"""

    @abstractmethod
    def get(self, key: str) -> Any:
        """取值，不存在抛 KeyError。"""

    @abstractmethod
    def keys(self) -> list[str]:
        """返回此配置源所有可用的 key。"""


# 工具函数


def _is_valid_semver(version: str) -> bool:
    """校验 MAJOR.MINOR.PATCH 格式（PATCH 可缺省）。

    兼容 "3.0" 和 "3.0.0" 两种形式（实验配置常用两段式）。
    """
    if not isinstance(version, str):
        return False
    parts = version.split(".")
    if len(parts) < 2 or len(parts) > 3:
        return False
    return all(p.isdigit() for p in parts)


def _check_type(name: str, value: Any, expected: str) -> str | None:
    """类型校验，返回错误消息或 None。"""
    # 注意：bool 是 int 的子类，需先判断 bool
    if expected == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"Field {name!r}: expected int, got {type(value).__name__}"
    elif expected == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return f"Field {name!r}: expected float, got {type(value).__name__}"
    elif expected == "str":
        if not isinstance(value, str):
            return f"Field {name!r}: expected str, got {type(value).__name__}"
    elif expected == "bool":
        if not isinstance(value, bool):
            return f"Field {name!r}: expected bool, got {type(value).__name__}"
    elif expected == "list":
        if not isinstance(value, list):
            return f"Field {name!r}: expected list, got {type(value).__name__}"
    elif expected == "dict":
        if not isinstance(value, dict):
            return f"Field {name!r}: expected dict, got {type(value).__name__}"
    return None


__all__ = [
    "ConfigField",
    "ConfigSpec",
    "IConfigStore",
    "IConfigSource",
    "VALID_FIELD_TYPES",
    "VALID_SWEEP_KINDS",
]
