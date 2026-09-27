"""IConfigStore 实现：声明式实验配置存储。

对应 core-contracts-design.md 第 6 章。

设计目标：
- 注册/查询 ConfigSpec
- 加载 YAML 配置文件（应用继承/覆盖）
- 合并 spec 默认值 + YAML + overrides，返回最终配置
- 展开超参搜索（grid/random/bayesian）

实现说明：
- ``load_yaml`` 委托给 ``app/config/yaml_loader.py`` 的 ``YamlLoader``
  （p3-8 抽取，支持 parent 继承 / 环境变量插值 / ``$ref`` 跨文件引用 / schema 校验）
- ``expand_sweep`` 完整支持 grid/random/bayesian 三种策略（p3-10）：
  - grid: 笛卡尔积（全空间）
  - random: 从全空间随机采样 ``count`` 个组合（支持 ``seed`` 复现）
  - bayesian: 返回 warmup 批次（随机采样），实际 BO 迭代循环在
    ``app/config/bayesian_optimizer.py`` 的 ``BayesianOptimizer`` 中
- 现有 ``LNN_<SECTION>_<KEY>`` 环境变量适配 ``IConfigSource`` 在 p3-9 实现
  （``app/config_contract_adapter.py``）

线程安全：使用 ``threading.RLock`` 保护 ``_specs`` 注册表与 ``_yaml_bindings``。

稳定性承诺：本文件为 Stable 契约 v1.0.0 实现，向后兼容扩展，breaking change 需新开 ADR。
"""

from __future__ import annotations

import itertools
import random
import threading
from pathlib import Path
from typing import Any, Union

from app.config.yaml_loader import (
    flatten_dict,
    get_yaml_loader,
    unflatten_dict,
)
from app.contracts.config import ConfigSpec, IConfigStore


# flatten_dict / unflatten_dict 从 yaml_loader 导入并重新导出，
# 以保持 ``from app.config.spec import flatten_dict`` 的向后兼容。


# ConfigStore 实现


class ConfigStore(IConfigStore):
    """IConfigStore 实现：声明式实验配置存储。

    用法::

        store = get_config_store()
        store.register(ltc_chatter_spec)
        store.bind_yaml("ltc_chatter", "experiments/ltc_chatter_v3.yaml")
        config = store.resolve("ltc_chatter", overrides={"model.hidden_size": 128})
        sweeps = store.expand_sweep("ltc_chatter", {"model.hidden_size": [32, 64]})
    """

    def __init__(self) -> None:
        self._specs: dict[str, ConfigSpec] = {}
        # spec_name YAML 文件绝对路径
        self._yaml_bindings: dict[str, str] = {}
        # YAML 缓存由 YamlLoader 全局单例管理（p3-8 抽取）
        self._lock = threading.RLock()

    # ConfigSpec 注册与查询

    def register(self, spec: ConfigSpec) -> None:
        """注册一个配置规格。

        重复注册同名 spec 抛 ``ValueError``（避免静默覆盖导致实验不可复现）。
        若需更新 spec，应使用新版本号或先 unregister（未来版本支持）。
        """
        with self._lock:
            if spec.name in self._specs:
                raise ValueError(f"ConfigSpec {spec.name!r} already registered. Use a different name or bump version.")
            self._specs[spec.name] = spec

    def get_spec(self, name: str) -> ConfigSpec:
        """按名取规格，不存在抛 ``KeyError``。"""
        with self._lock:
            if name not in self._specs:
                raise KeyError(f"ConfigSpec {name!r} not found. Registered specs: {sorted(self._specs.keys())}")
            return self._specs[name]

    def list_specs(self) -> list[str]:
        """列出所有已注册的 spec 名（调试用，不在 IConfigStore 契约中）。"""
        with self._lock:
            return sorted(self._specs.keys())

    # YAML 绑定与加载

    def bind_yaml(self, spec_name: str, yaml_path: Union[str, Path]) -> None:
        """绑定 YAML 文件到 spec，``resolve`` 时自动加载并合并。

        Args:
            spec_name: 已注册的 ConfigSpec 名
            yaml_path: YAML 文件路径（相对路径会相对于 CWD 解析）

        Raises:
            KeyError: spec_name 未注册
        """
        # 先验证 spec 存在
        self.get_spec(spec_name)
        with self._lock:
            self._yaml_bindings[spec_name] = str(Path(yaml_path).resolve())

    def load_yaml(self, path: Union[str, Path]) -> dict[str, Any]:
        """加载 YAML 配置文件，应用继承/覆盖。

        委托给 ``app/config/yaml_loader.py`` 的 ``YamlLoader``（p3-8 抽取）。

        功能：
        - 读取 YAML 文件（使用 ``yaml.safe_load``）
        - 解析 ``parent`` 字段（相对当前文件目录的 YAML 路径），递归加载父配置
        - 合并 父配置 + 当前文件的 ``overrides``
        - 多级继承深度限制（默认 16，防止循环引用）
        - 环境变量插值（``${ENV_VAR}`` / ``${ENV_VAR:default}``）
        - ``$ref`` 跨文件引用（``$ref: path/to/file.yaml#key.subkey``）
        - YAML schema 校验（顶层结构 + 字段类型）
        - 返回**扁平化**后的字典（key 为点分路径，如 ``model.hidden_size``）

        Args:
            path: YAML 文件路径

        Returns:
            扁平化配置字典（点分路径 key）

        Raises:
            ImportError: PyYAML 未安装
            FileNotFoundError: 文件不存在
            YamlLoaderError: YAML 解析/继承/schema/插值/$ref 错误
                （继承自 ``ValueError``，兼容 ``except ValueError`` 捕获）
        """
        return get_yaml_loader().load(path)

    # resolve：合并 spec 默认值 + YAML + overrides

    def resolve(
        self,
        spec_name: str,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """合并 spec 默认值 + 绑定 YAML + overrides，返回最终配置。

        优先级（低 → 高）：
        1. spec 字段默认值（``ConfigField.default``，``required=False`` 时生效）
        2. 绑定的 YAML 配置（通过 ``bind_yaml`` 关联，自动 ``load_yaml``）
        3. ``overrides`` 参数（最高优先级）

        Args:
            spec_name: ConfigSpec 名
            overrides: 顶层覆盖（最高优先级），支持点分路径 key 或嵌套字典

        Returns:
            完整配置字典（**嵌套形式**，已通过 ``spec.materialize`` 校验）

        Raises:
            KeyError: spec_name 未注册
            ValueError: 校验失败（required 缺失 / 类型不符 / 超出 min/max 等）
        """
        spec = self.get_spec(spec_name)

        # 1. spec 默认值（已使用点分路径 key）
        defaults_flat: dict[str, Any] = {}
        for f in spec.fields:
            if not f.required:
                defaults_flat[f.name] = f.default

        # 2. 绑定的 YAML（如果有）
        yaml_flat: dict[str, Any] = {}
        with self._lock:
            yaml_path = self._yaml_bindings.get(spec_name)
        if yaml_path:
            yaml_flat = self.load_yaml(yaml_path)

        # 3. overrides（扁平化，兼容点分 key 与嵌套 dict）
        override_flat: dict[str, Any] = {}
        if overrides:
            override_flat = flatten_dict(overrides)

        # 4. 合并：defaults < yaml < overrides
        merged_flat: dict[str, Any] = dict(defaults_flat)
        merged_flat.update(yaml_flat)
        merged_flat.update(override_flat)

        # 5. 校验 + 填充默认值（spec.materialize 会保留额外字段）
        materialized = spec.materialize(merged_flat)

        # 6. 还原为嵌套字典（便于消费方按 model.hidden_size 访问）
        return unflatten_dict(materialized)

    # expand_sweep：超参搜索展开

    def expand_sweep(
        self,
        spec_name: str,
        sweep_config: dict[str, Any],
        *,
        strategy: str | None = None,
        count: int | None = None,
        seed: int | None = None,
    ) -> list[dict[str, Any]]:
        """展开超参搜索，返回配置列表（p3-10 完整版）。

        支持三种策略：

        - ``grid``（默认）：笛卡尔积，返回全部组合。``count`` 被忽略。
        - ``random``：从全空间中随机采样 ``count`` 个组合（无放回，若
          ``count`` 超过总数则返回全部）。``seed`` 用于复现。
        - ``bayesian``：返回 warmup 批次（随机采样的子集），用于贝叶斯优化
          的初始探索阶段。实际的 BO 迭代循环（suggest → evaluate → update）
          由 ``app/config/bayesian_optimizer.py`` 的 ``BayesianOptimizer`` 完成。

        ``sweep_config`` 支持三种格式：

        1. **简写形式**：字段名 → 候选值列表::

               {"model.hidden_size": [32, 64, 128]}

        2. **完整形式**：字段名 → sweep 规格 dict（与 ``ConfigField.sweep`` 一致）::

               {"model.hidden_size": {"sweep": {"kind": "grid", "values": [32, 64]}}}

        3. **固定值形式**：字段名 → 标量值（int/float/str/bool），作为每个组合的基础覆盖
           （用于提供 required 字段或固定实验参数）::

               {"experiment_name": "exp1", "model.hidden_size": [32, 64]}

        此外，``ConfigSpec`` 中通过 ``ConfigField.sweep`` 声明的字段也会自动纳入
        sweep（除非 ``sweep_config`` 显式覆盖）。

        **策略推断规则**（当 ``strategy=None`` 时）：
        - 收集所有 sweep 字段的 ``kind``（来自 sweep_config 或 ConfigField.sweep）
        - 全为 ``grid``（或未指定）→ ``grid``
        - 含有 ``random`` → ``random``
        - 含有 ``bayesian`` → ``bayesian``
        - 同时含有 ``random`` 和 ``bayesian`` → 抛 ``ValueError``

        Args:
            spec_name: ConfigSpec 名
            sweep_config: 字段名 → 候选值列表 / sweep 规格 dict / 固定标量值
            strategy: 搜索策略 ``"grid"`` / ``"random"`` / ``"bayesian"``。
                若为 ``None``，从字段 kind 推断（默认 ``grid``）。
            count: ``random`` / ``bayesian`` 策略下返回的配置数。
                ``None`` 时默认 ``min(10, 总组合数)``。
            seed: ``random`` / ``bayesian`` 策略下的随机种子，用于复现。

        Returns:
            配置字典列表（嵌套形式），每个元素是一个完整的实验配置。
            不合法的组合（如超出 min/max）会被静默跳过。

        Raises:
            KeyError: spec_name 未注册
            ValueError: sweep 字段不在 spec 中 / 候选值为空 / 格式错误 /
                       所有组合均校验失败 / 策略非法 / random 与 bayesian 混用
        """
        spec = self.get_spec(spec_name)

        # 解析 sweep_config，分离 sweep 字段与固定值字段
        sweep_fields: dict[str, tuple[list[Any], str]] = {}  # name → (values, kind)
        fixed_overrides: dict[str, Any] = {}
        for field_name, spec_value in sweep_config.items():
            field = spec.get_field(field_name)
            if field is None:
                raise ValueError(
                    f"Sweep field {field_name!r} not in ConfigSpec {spec_name!r}. "
                    f"Available fields: {[f.name for f in spec.fields]}"
                )

            # 标量值 固定覆盖（不参与 sweep）
            if isinstance(spec_value, (int, float, str, bool)):
                fixed_overrides[field_name] = spec_value
                continue

            values, kind = self._parse_sweep_value(field_name, spec_value)
            sweep_fields[field_name] = (values, kind)

        # 合并 spec 中通过 ConfigField.sweep 声明的字段
        for f in spec.fields:
            if f.sweep is not None and f.name not in sweep_fields and f.name not in fixed_overrides:
                values, kind = self._parse_sweep_value(f.name, {"sweep": f.sweep})
                sweep_fields[f.name] = (values, kind)

        # 固定值转嵌套（作为 resolve 的基础 overrides）
        base_overrides = unflatten_dict(fixed_overrides) if fixed_overrides else None

        if not sweep_fields:
            return [self.resolve(spec_name, overrides=base_overrides)]

        # 推断或校验策略
        if strategy is None:
            strategy = self._infer_strategy(sweep_fields)
        else:
            strategy = strategy.lower()
            if strategy not in ("grid", "random", "bayesian"):
                raise ValueError(f"expand_sweep: strategy must be 'grid' / 'random' / 'bayesian', got {strategy!r}")

        # 生成全空间候选组合（用于 grid 或作为 random/bayesian 的采样池）
        field_names = list(sweep_fields.keys())
        field_value_lists = [sweep_fields[name][0] for name in field_names]
        all_combos = list(itertools.product(*field_value_lists))

        if not all_combos:
            return []

        # 根据策略选择组合
        if strategy == "grid":
            selected_combos = all_combos
        elif strategy == "random":
            selected_combos = self._sample_combos(all_combos, count=count, seed=seed, strategy="random")
        else:  # bayesian
            selected_combos = self._sample_combos(all_combos, count=count, seed=seed, strategy="bayesian")

        # 展开为完整配置
        configs: list[dict[str, Any]] = []
        skipped: list[tuple[tuple[Any, ...], str]] = []
        for combo in selected_combos:
            flat_overrides = dict(zip(field_names, combo))
            if fixed_overrides:
                flat_overrides.update(fixed_overrides)
            nested_overrides = unflatten_dict(flat_overrides)
            try:
                cfg = self.resolve(spec_name, overrides=nested_overrides)
                configs.append(cfg)
            except ValueError as e:
                skipped.append((combo, str(e)))
                continue

        if not configs and skipped:
            sample_err = skipped[0][1][:100]
            raise ValueError(
                f"expand_sweep: all {len(skipped)} combinations failed validation. First error: {sample_err}"
            )

        return configs

    @staticmethod
    def _infer_strategy(
        sweep_fields: dict[str, tuple[list[Any], str]],
    ) -> str:
        """从字段的 kind 推断整体策略。"""
        kinds = {k for _, k in sweep_fields.values()}
        has_random = "random" in kinds
        has_bayesian = "bayesian" in kinds
        if has_random and has_bayesian:
            raise ValueError(
                "expand_sweep: cannot mix 'random' and 'bayesian' sweep kinds. Use explicit strategy parameter."
            )
        if has_bayesian:
            return "bayesian"
        if has_random:
            return "random"
        return "grid"

    @staticmethod
    def _sample_combos(
        all_combos: list[tuple[Any, ...]],
        *,
        count: int | None,
        seed: int | None,
        strategy: str,
    ) -> list[tuple[Any, ...]]:
        """从全空间组合中采样。

        - ``random``：无放回采样 ``count`` 个（超过总数则返回全部）
        - ``bayesian``：返回 warmup 批次，默认大小为
          ``min(10, max(1, len(all_combos) // 5))``

        Args:
            all_combos: 全部候选组合
            count: 期望返回数量（None 时按策略默认）
            seed: 随机种子
            strategy: ``"random"`` 或 ``"bayesian"``
        """
        total = len(all_combos)
        if total == 0:
            return []

        if strategy == "random":
            target = count if count is not None else min(10, total)
        else:  # bayesian warmup
            target = count if count is not None else min(10, max(1, total // 5))

        target = max(1, min(target, total))

        rng = random.Random(seed)
        if target >= total:
            # 全部返回（保持稳定排序，便于复现）
            return list(all_combos)
        # 无放回采样
        indices = rng.sample(range(total), target)
        indices.sort()  # 保持稳定顺序
        return [all_combos[i] for i in indices]

    @staticmethod
    def _parse_sweep_value(field_name: str, spec_value: Any) -> tuple[list[Any], str]:
        """解析 sweep_config 中单个字段的值，返回 (候选值列表, kind)。

        支持两种格式：
        - list：直接作为候选值，kind 默认为 ``"grid"``
        - ``{"sweep": {"kind": "grid"|"random"|"bayesian", "values": [...]}}``：
          完整 sweep 规格，kind 从中提取

        Note:
            per-field 的 ``kind`` 仅作为声明（用于 UI 展示与策略推断），
            实际采样策略由 ``expand_sweep`` 的 ``strategy`` 参数控制。
            对 ``random`` / ``bayesian`` 字段，仍返回全部 values 作为候选池。
        """
        if isinstance(spec_value, dict) and "sweep" in spec_value:
            sweep_spec = spec_value["sweep"]
            if not isinstance(sweep_spec, dict):
                raise ValueError(f"Sweep field {field_name!r}: 'sweep' must be a dict, got {type(sweep_spec).__name__}")
            kind = sweep_spec.get("kind", "grid")
            if kind not in ("grid", "random", "bayesian"):
                raise ValueError(
                    f"Sweep field {field_name!r}: kind must be 'grid' / 'random' / 'bayesian', got {kind!r}"
                )
            values = sweep_spec.get("values", [])
        elif isinstance(spec_value, list):
            kind = "grid"
            values = spec_value
        else:
            raise ValueError(
                f"Sweep value for {field_name!r} must be a list or "
                f"{{'sweep': {{'kind': ..., 'values': [...]}}}} dict, "
                f"got {type(spec_value).__name__}"
            )

        if not isinstance(values, list):
            raise ValueError(f"Sweep values for {field_name!r} must be a list, got {type(values).__name__}")
        if len(values) == 0:
            raise ValueError(f"Sweep values for {field_name!r} is empty")

        return list(values), kind


# 全局单例

_global_store: ConfigStore | None = None
_global_store_lock = threading.Lock()


def get_config_store() -> ConfigStore:
    """获取全局 ConfigStore 单例。

    单例惰性初始化，线程安全。整个进程共享同一个 ConfigStore，
    便于插件在启动时注册自己的 ConfigSpec。
    """
    global _global_store
    if _global_store is None:
        with _global_store_lock:
            if _global_store is None:
                _global_store = ConfigStore()
    return _global_store


def reset_config_store() -> None:
    """重置全局单例（仅用于测试隔离，生产代码不应调用）。"""
    global _global_store
    with _global_store_lock:
        _global_store = None


__all__ = [
    "ConfigStore",
    "get_config_store",
    "reset_config_store",
    "flatten_dict",
    "unflatten_dict",
]
