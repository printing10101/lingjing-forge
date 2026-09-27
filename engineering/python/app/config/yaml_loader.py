"""YAML 配置加载器：解析 + 继承合并 + 环境变量插值 + $ref 跨文件引用。

对应 core-contracts-design.md 第 6.4 节。

设计目标：
- 从 ``ConfigStore.load_yaml`` 抽取 YAML 解析逻辑，单一职责
- 完善功能：
  * 多级继承深度限制（默认 16，防止循环引用）
  * 环境变量插值（``${ENV_VAR}`` / ``${ENV_VAR:default}``）
  * ``$ref`` 跨文件引用（``$ref: path/to/file.yaml#key.subkey``）
  * YAML schema 校验（顶层结构 + 字段类型）
- 线程安全：``threading.RLock`` 保护缓存

用法::

    loader = YamlLoader()
    flat_config = loader.load("experiments/ltc_chatter_v3.yaml")
    # flat_config = {"model.hidden_size": 64, "training.epochs": 50, ...}

YAML 文件结构示例::

    # experiments/ltc_chatter_v3.yaml
    spec: ltc_chatter
    version: "3.0"
    description: "LTC 颤振预测实验配置 v3"
    parent: ltc_chatter_v2.yaml   # 继承 v2，覆盖部分字段

    overrides:
      model:
        hidden_size: 64
        num_layers: 2
      training:
        epochs: 50
        batch_size: ${BATCH_SIZE:32}   # 环境变量插值，默认 32
      data:
        dataset_id: phm2010-milling
        shared_config:
          $ref: "shared/datasets.yaml#phm2010"   # 跨文件引用

    metadata:
      paper_section: "Section 4.2"
      reproducibility_seed: 42

稳定性承诺：本文件为 Stable 契约 v1.0.0 实现，向后兼容扩展，breaking change 需新开 ADR。
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, Union


# 常量

# 最大继承深度（防止循环引用导致栈溢出）
MAX_INHERIT_DEPTH: int = 16

# 最大 $ref 嵌套深度（防止循环 $ref 导致栈溢出）
MAX_REF_DEPTH: int = 16

# 环境变量插值正则：${VAR} 或 ${VAR:default}
# default 可以包含任意字符（除 } 外）
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")

# $ref 引用正则：path/to/file.yaml#key.subkey 或 path/to/file.yaml（取整个文件）
_REF_PATTERN = re.compile(r"^(?P<path>[^#]+)(?:#(?P<keypath>.+))?$")


# 异常


class YamlLoaderError(ValueError):
    """YAML 加载器基础异常。

    继承 ``ValueError`` 以保持与旧 ``ConfigStore.load_yaml`` 的向后兼容
    （旧实现抛 ``ValueError``，调用方用 ``except ValueError`` 捕获）。
    """


class YamlInheritanceError(YamlLoaderError):
    """继承链异常（深度超限 / 循环引用 / 父文件缺失）。"""


class YamlSchemaError(YamlLoaderError):
    """YAML schema 校验失败。"""


class YamlInterpolationError(YamlLoaderError):
    """环境变量插值失败（如变量未设置且无默认值）。"""


class YamlRefError(YamlLoaderError):
    """$ref 跨文件引用解析失败。"""


# 工具函数


def flatten_dict(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """将嵌套字典扁平化为点分路径 key。

    示例::

        {"model": {"hidden_size": 64}} → {"model.hidden_size": 64}

    非 dict 值（包括 list）保持原样，不再展开。

    注意：本函数与 ``spec.py`` 中的 ``flatten_dict`` 行为一致，此处独立实现
    以保持 ``yaml_loader`` 的自包含性（``spec.py`` 后续将委托给 ``yaml_loader``）。
    """
    result: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(flatten_dict(v, key))
        else:
            result[key] = v
    return result


def unflatten_dict(d: dict[str, Any]) -> dict[str, Any]:
    """将点分路径 key 的扁平字典还原为嵌套字典。

    示例::

        {"model.hidden_size": 64} → {"model": {"hidden_size": 64}}

    若同一前缀既作为叶子值又作为子字典出现，子字典优先（覆盖叶子值）。
    """
    result: dict[str, Any] = {}
    for key, value in d.items():
        parts = key.split(".")
        current = result
        for part in parts[:-1]:
            existing = current.get(part)
            if not isinstance(existing, dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value
    return result


# YamlLoader


class YamlLoader:
    """YAML 配置加载器。

    支持：
    - ``parent`` 继承（相对当前文件目录的 YAML 路径，多级递归）
    - ``overrides`` 覆盖
    - 环境变量插值（``${VAR}`` / ``${VAR:default}``）
    - ``$ref`` 跨文件引用（``$ref: path/to/file.yaml#key.subkey``）
    - YAML schema 校验
    - 文件缓存（避免重复解析）

    线程安全：所有公共方法均通过 ``self._lock`` 保护。
    """

    def __init__(
        self,
        max_inherit_depth: int = MAX_INHERIT_DEPTH,
        max_ref_depth: int = MAX_REF_DEPTH,
    ) -> None:
        self._max_inherit_depth = max_inherit_depth
        self._max_ref_depth = max_ref_depth
        # cache_key (resolved path str) 扁平化配置 dict
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    # 公共 API

    def load(
        self,
        path: Union[str, Path],
        *,
        interpolate_env: bool = True,
        resolve_refs: bool = True,
    ) -> dict[str, Any]:
        """加载 YAML 配置文件，应用继承/覆盖/插值/$ref。

        Args:
            path: YAML 文件路径
            interpolate_env: 是否启用环境变量插值（默认 ``True``）
            resolve_refs: 是否启用 ``$ref`` 跨文件引用解析（默认 ``True``）

        Returns:
            扁平化配置字典（key 为点分路径，如 ``model.hidden_size``）

        Raises:
            FileNotFoundError: 文件不存在
            YamlSchemaError: YAML 结构不符合 schema
            YamlInheritanceError: 继承链异常
            YamlInterpolationError: 环境变量插值失败
            YamlRefError: ``$ref`` 解析失败
            ImportError: PyYAML 未安装
        """
        return self._load_recursive(
            path,
            inherit_depth=0,
            inherit_visited=set(),
            interpolate_env=interpolate_env,
            resolve_refs=resolve_refs,
            ref_depth=0,
        )

    def clear_cache(self) -> None:
        """清空文件缓存（用于测试或配置热重载）。"""
        with self._lock:
            self._cache.clear()

    # 内部实现：递归加载 + 继承

    def _load_recursive(
        self,
        path: Union[str, Path],
        *,
        inherit_depth: int,
        inherit_visited: set[str],
        interpolate_env: bool,
        resolve_refs: bool,
        ref_depth: int,
    ) -> dict[str, Any]:
        """递归加载 YAML，处理 parent 继承。

        ``inherit_visited`` 用于循环检测（同一文件不能在继承链中重复出现）。
        ``ref_depth`` 用于 ``$ref`` 嵌套深度限制（与继承深度独立）。
        """
        resolved = Path(path).resolve()
        cache_key = str(resolved)

        # 缓存命中（缓存的是已完整处理的扁平配置）
        # 注意：缓存 key 不区分 interpolate_env / resolve_refs 选项，
        # 因为选项不同时不应共享缓存（避免污染）。这里简化处理：只在
        # 两个选项都为 True（默认）时使用缓存。
        if interpolate_env and resolve_refs:
            with self._lock:
                if cache_key in self._cache:
                    return dict(self._cache[cache_key])

        # 深度限制
        if inherit_depth >= self._max_inherit_depth:
            raise YamlInheritanceError(
                f"YAML inheritance depth exceeds {self._max_inherit_depth}. "
                f"Possible circular reference. Last file: {resolved}"
            )

        # 循环检测
        if cache_key in inherit_visited:
            chain = " → ".join(list(inherit_visited) + [cache_key])
            raise YamlInheritanceError(f"Circular YAML inheritance detected: {chain}")
        inherit_visited = inherit_visited | {cache_key}

        # 读取文件
        raw = self._read_yaml(resolved)

        # schema 校验
        self._validate_schema(raw, resolved)

        # 解析 parent 继承
        parent_flat: dict[str, Any] = {}
        parent_name = raw.get("parent")
        if parent_name and isinstance(parent_name, str):
            parent_path = (resolved.parent / parent_name).resolve()
            if not parent_path.exists():
                raise YamlInheritanceError(f"Parent YAML file not found: {parent_path} (referenced from {resolved})")
            if str(parent_path) == cache_key:
                raise YamlInheritanceError(f"YAML file cannot be its own parent: {resolved}")
            parent_flat = self._load_recursive(
                parent_path,
                inherit_depth=inherit_depth + 1,
                inherit_visited=inherit_visited,
                interpolate_env=interpolate_env,
                resolve_refs=resolve_refs,
                ref_depth=ref_depth,
            )

        # 解析 overrides
        overrides = raw.get("overrides", {}) or {}
        if not isinstance(overrides, dict):
            raise YamlSchemaError(f"YAML 'overrides' must be a mapping, got {type(overrides).__name__}: {resolved}")

        # $ref 解析（在 overrides 内部）
        if resolve_refs:
            overrides = self._resolve_refs(
                overrides,
                resolved.parent,
                ref_depth=ref_depth,
            )

        # 环境变量插值（在 overrides 内部，$ref 解析之后）
        if interpolate_env:
            overrides = self._interpolate_env(overrides)

        # 合并：parent < overrides
        merged_flat: dict[str, Any] = dict(parent_flat)
        merged_flat.update(flatten_dict(overrides))

        # 缓存（仅在默认选项下）
        if interpolate_env and resolve_refs:
            with self._lock:
                self._cache[cache_key] = dict(merged_flat)

        return merged_flat

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        """读取并解析 YAML 文件。"""
        try:
            import yaml  # type: ignore[import-untyped]  # PyYAML 无官方 stub，可选依赖
        except ImportError as e:
            raise ImportError("PyYAML is required for YAML loading. Install with: pip install pyyaml") from e

        if not path.exists():
            raise FileNotFoundError(f"YAML config file not found: {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise YamlSchemaError(f"YAML parse error in {path}: {e}") from e

        if not isinstance(raw, dict):
            raise YamlSchemaError(f"YAML config file must be a mapping at top level, got {type(raw).__name__}: {path}")

        return raw

    def _validate_schema(self, raw: dict[str, Any], path: Path) -> None:
        """校验 YAML 顶层 schema。

        允许的字段：
        - ``spec``: str（可选，ConfigSpec 名）
        - ``version``: str（可选，semver）
        - ``description``: str（可选）
        - ``parent``: str（可选，父 YAML 路径）
        - ``overrides``: dict（可选，覆盖值）
        - ``metadata``: dict（可选，自定义元数据）

        其他字段会被保留（便于扩展）。
        """
        type_checks = [
            ("spec", str),
            ("version", str),
            ("description", str),
            ("parent", str),
        ]
        for key, expected_type in type_checks:
            if key in raw and not isinstance(raw[key], expected_type):
                raise YamlSchemaError(
                    f"YAML field {key!r} must be {expected_type.__name__}, got {type(raw[key]).__name__}: {path}"
                )

        for key in ("overrides", "metadata"):
            if key in raw and not isinstance(raw[key], dict):
                raise YamlSchemaError(f"YAML field {key!r} must be a mapping, got {type(raw[key]).__name__}: {path}")

    # 内部实现：$ref 跨文件引用

    def _resolve_refs(
        self,
        data: Any,
        base_dir: Path,
        *,
        ref_depth: int,
    ) -> Any:
        """递归解析 ``$ref`` 跨文件引用。

        支持语法：
        ``{"$ref": "path/to/file.yaml#key.subkey"}`` → 替换为引用文件中
        ``key.subkey`` 路径的值。

        Args:
            data: 待解析的数据（dict / list / 标量）
            base_dir: ``$ref`` 路径的基准目录（通常是当前 YAML 文件的父目录）
            ref_depth: 当前 ``$ref`` 嵌套深度

        Returns:
            解析后的数据（``$ref`` 已被替换为实际值）

        Raises:
            YamlRefError: ``$ref`` 路径格式错误 / 文件不存在 / keypath 不存在 /
                         嵌套深度超限
        """
        if isinstance(data, dict):
            # 检查是否是 $ref 整字段引用（dict 只含 $ref 一个 key）
            if "$ref" in data and len(data) == 1:
                return self._resolve_single_ref(data["$ref"], base_dir, ref_depth=ref_depth)
            # 递归处理 dict 的值
            return {k: self._resolve_refs(v, base_dir, ref_depth=ref_depth) for k, v in data.items()}
        if isinstance(data, list):
            return [self._resolve_refs(item, base_dir, ref_depth=ref_depth) for item in data]
        return data

    def _resolve_single_ref(
        self,
        ref_str: str,
        base_dir: Path,
        *,
        ref_depth: int,
    ) -> Any:
        """解析单个 ``$ref`` 引用字符串。

        格式：``path/to/file.yaml#key.subkey`` 或 ``path/to/file.yaml``

        - path 部分相对于 ``base_dir``
        - keypath 部分用 ``.`` 分隔，按点分路径在引用文件中取值
        - 若省略 keypath，返回整个引用文件的内容（嵌套形式）
        """
        if ref_depth >= self._max_ref_depth:
            raise YamlRefError(
                f"$ref nesting depth exceeds {self._max_ref_depth}. Possible circular $ref. Last ref: {ref_str!r}"
            )

        if not isinstance(ref_str, str) or not ref_str:
            raise YamlRefError(f"$ref must be a non-empty string, got {ref_str!r}")

        match = _REF_PATTERN.match(ref_str)
        if not match:
            raise YamlRefError(f"Invalid $ref format: {ref_str!r}")

        ref_path_part = match.group("path").strip()
        keypath = match.group("keypath")

        if not ref_path_part:
            raise YamlRefError(f"$ref must contain a file path: {ref_str!r}")

        ref_file = (base_dir / ref_path_part).resolve()
        if not ref_file.exists():
            raise YamlRefError(f"$ref target file not found: {ref_file} (referenced via {ref_str!r})")

        # 加载引用文件（独立继承链，但 $ref 深度 +1）
        ref_flat = self._load_recursive(
            ref_file,
            inherit_depth=0,
            inherit_visited=set(),
            interpolate_env=False,  # 引用解析时不插值，由外层统一处理
            resolve_refs=True,  # 引用文件内部可能还有 $ref
            ref_depth=ref_depth + 1,
        )

        if keypath is None:
            # 返回整个引用文件（嵌套形式，便于嵌入到 overrides 中）
            return unflatten_dict(ref_flat)

        # 按 keypath 取值
        # 1. 直接命中扁平 key（如 "model.hidden_size"）
        if keypath in ref_flat:
            return ref_flat[keypath]

        # 2. keypath 指向子树（如 "model" 对应所有 "model.*" 的 unflatten）
        subtree: dict[str, Any] = {}
        prefix = keypath + "."
        for k, v in ref_flat.items():
            if k.startswith(prefix):
                sub_key = k[len(prefix) :]
                subtree[sub_key] = v
        if subtree:
            return unflatten_dict(subtree)

        raise YamlRefError(
            f"$ref keypath {keypath!r} not found in {ref_file}. "
            f"Available top-level keys: "
            f"{sorted(set(k.split('.')[0] for k in ref_flat))}"
        )

    # 内部实现：环境变量插值

    def _interpolate_env(self, data: Any) -> Any:
        """递归进行环境变量插值。

        支持：
        - ``${VAR}``：替换为环境变量 ``VAR`` 的值（未设置则抛错）
        - ``${VAR:default}``：替换为环境变量 ``VAR`` 的值，未设置时用 ``default``

        类型推断：
        - 整个字符串是 ``${...}`` 时，保留环境变量的原始类型（int/float/bool/str）
        - 字符串内嵌 ``${...}`` 时，强制转为 str
        """
        if isinstance(data, dict):
            return {k: self._interpolate_env(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self._interpolate_env(item) for item in data]
        if isinstance(data, str):
            return self._interpolate_str(data)
        return data

    def _interpolate_str(self, s: str) -> Any:
        """对单个字符串进行环境变量插值。

        若整个字符串是单个 ``${...}``，返回原始类型值；
        否则返回字符串拼接结果。
        """
        # 快速路径：无 ${...} 直接返回
        if "${" not in s:
            return s

        # 检查是否是单个 ${...}（整字段引用，保留原始类型）
        full_match = re.match(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}$", s)
        if full_match:
            var_name = full_match.group(1)
            default = full_match.group(2)
            return self._get_env_value(var_name, default, s)

        # 字符串内嵌 ${...}：逐个替换，结果强制为 str
        def replace_match(m: re.Match) -> str:
            var_name = m.group(1)
            default = m.group(2)
            value = self._get_env_value(var_name, default, s)
            return str(value)

        return _ENV_VAR_PATTERN.sub(replace_match, s)

    def _get_env_value(
        self,
        var_name: str,
        default: str | None,
        context: str,
    ) -> Any:
        """获取环境变量值，进行类型推断。

        - 环境变量未设置且无默认值 → 抛 ``YamlInterpolationError``
        - 环境变量未设置但有默认值 → 使用默认值
        - 环境变量已设置 → 使用环境变量值

        类型推断：
        - ``"true"`` / ``"false"``（不区分大小写）→ ``bool``
        - 纯整数 → ``int``
        - 纯浮点数 → ``float``
        - 其他 → ``str``
        """
        if var_name in os.environ:
            raw_value = os.environ[var_name]
        elif default is not None:
            raw_value = default
        else:
            raise YamlInterpolationError(
                f"Environment variable {var_name!r} is not set and no default provided (in {context!r})"
            )

        # 类型推断
        if raw_value.lower() in ("true", "false"):
            return raw_value.lower() == "true"
        try:
            return int(raw_value)
        except ValueError:
            pass
        try:
            return float(raw_value)
        except ValueError:
            pass
        return raw_value


# 全局单例


_global_loader: YamlLoader | None = None
_global_loader_lock = threading.Lock()


def get_yaml_loader() -> YamlLoader:
    """获取全局 YamlLoader 单例。

    单例惰性初始化，线程安全。整个进程共享同一个 YamlLoader，
    便于 ConfigStore 委托调用并共享缓存。
    """
    global _global_loader
    if _global_loader is None:
        with _global_loader_lock:
            if _global_loader is None:
                _global_loader = YamlLoader()
    return _global_loader


def reset_yaml_loader() -> None:
    """重置全局单例（仅用于测试隔离，生产代码不应调用）。"""
    global _global_loader
    with _global_loader_lock:
        _global_loader = None


__all__ = [
    "YamlLoader",
    "YamlLoaderError",
    "YamlInheritanceError",
    "YamlSchemaError",
    "YamlInterpolationError",
    "YamlRefError",
    "MAX_INHERIT_DEPTH",
    "MAX_REF_DEPTH",
    "get_yaml_loader",
    "reset_yaml_loader",
    "flatten_dict",
    "unflatten_dict",
]
