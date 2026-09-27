"""贝叶斯优化器：基于 GP 代理模型 + EI 采集函数的迭代式超参搜索。

对应 core-contracts-design.md 第 6 章 expand_sweep 的 ``bayesian`` 策略补全。

设计说明
========

``ConfigStore.expand_sweep(strategy="bayesian")`` 只能返回 **warmup 批次**（初始
随机采样），因为贝叶斯优化本质上是**顺序反馈循环**：

    suggest → evaluate → update → suggest → evaluate → ...

本模块提供 ``BayesianOptimizer`` 类，封装这个反馈循环：

1. 初始化时从 ``ConfigStore.expand_sweep(strategy="bayesian")`` 获取 warmup 批次
2. 用户对每个 suggested config 调用目标函数得到 score
3. 调用 ``update(config, score)`` 记录结果
4. 调用 ``suggest()`` 获取下一个 config（基于 GP + EI）
5. 重复 2-4 直到 ``max_evals`` 达到

代理模型与采集函数
==================

- **代理模型**：高斯过程回归（``sklearn.gaussian_process.GaussianProcessRegressor``）。
  若 scikit-learn 不可用，自动降级为随机搜索（仍能跑通，但失去 BO 优势）。
- **采集函数**：Expected Improvement (EI)。
  对候选点 ``x``，EI(x) = ``(μ_best - μ(x)) * Φ(z) + σ(x) * φ(z)``，
  其中 ``z = (μ_best - μ(x)) / (σ(x) + ε)``。
- **候选空间**：离散组合的全空间（与 ``expand_sweep`` 一致），EI 在已观测点之外
  的候选上评估，取 argmax。

复现性
======

- ``seed`` 控制 warmup 采样与 GP 随机性
- 所有观测记录在 ``self._observations`` 中，可序列化供 MLflow 记录

稳定性承诺：本文件为 Stable 契约 v1.0.0 实现，向后兼容扩展。
"""

from __future__ import annotations

import math
import random
import threading
from typing import Any
from collections.abc import Callable

from app.config.spec import ConfigStore
from app.config.yaml_loader import flatten_dict


# 软依赖：scikit-learn（GaussianProcessRegressor）

try:
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, ConstantKernel

    _HAS_SKLEARN = True
except ImportError:  # pragma: no cover
    _HAS_SKLEARN = False


class BayesianOptimizer:
    """贝叶斯优化器：GP + EI 迭代式超参搜索。

    用法::

        store = get_config_store()
        store.register(my_spec)

        opt = BayesianOptimizer(
            store=store,
            spec_name="my_spec",
            sweep_config={"model.hidden_size": [32, 64, 128],
                          "training.lr": [0.001, 0.01, 0.1]},
            maximize=False,  # False = 最小化目标（如 loss）；True = 最大化（如 accuracy）
            seed=42,
        )

        # 1. 获取 warmup 批次
        warmup = opt.warmup(count=5)

        # 2. 评估 warmup 并更新
        for cfg in warmup:
            score = objective(cfg)
            opt.update(cfg, score)

        # 3. 迭代优化
        while opt.eval_count < 30:
            cfg = opt.suggest()
            if cfg is None:
                break
            score = objective(cfg)
            opt.update(cfg, score)

        # 4. 取最优
        best_cfg, best_score = opt.best()

    Note:
        - ``sweep_config`` 格式与 ``ConfigStore.expand_sweep`` 一致
          （字段名 → 候选值列表 / sweep 规格 dict / 固定标量值）
        - 目标函数由用户外部提供（``objective: Callable[[dict], float]``），
          ``BayesianOptimizer`` 不直接调用它，避免对评估方式的假设
        - 若 scikit-learn 不可用，``suggest()`` 降级为随机搜索
    """

    def __init__(
        self,
        *,
        store: ConfigStore,
        spec_name: str,
        sweep_config: dict[str, Any],
        maximize: bool = False,
        seed: int | None = None,
        warmup_count: int | None = None,
    ) -> None:
        """初始化贝叶斯优化器。

        Args:
            store: ConfigStore 实例（已注册 spec_name）
            spec_name: ConfigSpec 名
            sweep_config: sweep 配置（同 ``expand_sweep``）
            maximize: ``True`` 最大化目标（如 accuracy），``False`` 最小化（如 loss）
            seed: 随机种子
            warmup_count: warmup 批次大小，``None`` 时由 ``expand_sweep`` 决定
        """
        self._store = store
        self._spec_name = spec_name
        self._sweep_config = sweep_config
        self._maximize = maximize
        self._seed = seed
        self._warmup_count = warmup_count
        self._rng = random.Random(seed)
        self._lock = threading.RLock()

        # 观测记录：[(flat_config, score), ...]
        self._observations: list[tuple[dict[str, Any], float]] = []

        # 全候选空间（lazy 初始化，避免构造时即展开大空间）
        self._candidate_cache: list[dict[str, Any]] | None = None

    # 公开 API

    def warmup(self, count: int | None = None) -> list[dict[str, Any]]:
        """返回 warmup 批次（随机采样的初始配置列表）。

        调用方应依次评估这些配置，并通过 ``update()`` 反馈结果。

        Args:
            count: warmup 大小，``None`` 用构造时的 ``warmup_count``
        """
        target = count if count is not None else self._warmup_count
        return self._store.expand_sweep(
            self._spec_name,
            self._sweep_config,
            strategy="bayesian",
            count=target,
            seed=self._seed,
        )

    def update(self, config: dict[str, Any], score: float) -> None:
        """记录一次评估结果。

        Args:
            config: 评估的配置（嵌套字典，同 ``expand_sweep`` 返回格式）
            score: 目标函数值（float）
        """
        flat = flatten_dict(config)
        with self._lock:
            self._observations.append((flat, float(score)))

    def suggest(self) -> dict[str, Any] | None:
        """建议下一个评估配置。

        基于已有观测拟合 GP，对未评估的候选点计算 EI，取 argmax。
        若所有候选都已评估，返回 ``None``。
        若 scikit-learn 不可用，从未评估候选中随机选一个。

        Returns:
            下一个配置（嵌套字典），或 ``None``（候选空间已穷尽）
        """
        with self._lock:
            candidates = self._get_candidates()
            observed_flats = {self._config_key(f) for f, _ in self._observations}

            unobserved = [c for c in candidates if self._config_key(flatten_dict(c)) not in observed_flats]
            if not unobserved:
                return None

            if len(self._observations) < 2 or not _HAS_SKLEARN:
                # 观测太少或无 sklearn 随机
                return self._rng.choice(unobserved)

            return self._suggest_by_ei(unobserved)

    def optimize(
        self,
        objective: Callable[[dict[str, Any]], float],
        max_evals: int,
    ) -> tuple[dict[str, Any], float]:
        """运行完整 BO 循环（warmup + 迭代优化）。

        Args:
            objective: 目标函数，输入配置字典，返回 float
            max_evals: 总评估次数（含 warmup）

        Returns:
            (best_config, best_score)
        """
        # warmup
        warmup_configs = self.warmup()
        for cfg in warmup_configs:
            if self.eval_count >= max_evals:
                break
            score = objective(cfg)
            self.update(cfg, score)

        # 迭代
        while self.eval_count < max_evals:
            suggested_cfg = self.suggest()
            if suggested_cfg is None:
                break
            score = objective(suggested_cfg)
            self.update(suggested_cfg, score)

        return self.best()

    def best(self) -> tuple[dict[str, Any], float]:
        """返回当前最优 (config, score)。

        Raises:
            RuntimeError: 还没有任何观测
        """
        with self._lock:
            if not self._observations:
                raise RuntimeError(
                    "BayesianOptimizer.best(): no observations yet. Call warmup() + update() or optimize() first."
                )
            if self._maximize:
                best_flat, best_score = max(self._observations, key=lambda x: x[1])
            else:
                best_flat, best_score = min(self._observations, key=lambda x: x[1])
            from app.config.yaml_loader import unflatten_dict

            return unflatten_dict(best_flat), best_score

    @property
    def eval_count(self) -> int:
        """已评估的配置数。"""
        with self._lock:
            return len(self._observations)

    @property
    def has_sklearn(self) -> bool:
        """是否可用 scikit-learn（GP 代理模型）。"""
        return _HAS_SKLEARN

    # 内部实现

    def _get_candidates(self) -> list[dict[str, Any]]:
        """获取全候选空间（lazy 缓存）。"""
        if self._candidate_cache is None:
            self._candidate_cache = self._store.expand_sweep(
                self._spec_name,
                self._sweep_config,
                strategy="grid",
            )
        return self._candidate_cache

    @staticmethod
    def _config_key(flat_config: dict[str, Any]) -> str:
        """将扁平配置转为可哈希的 key（用于去重）。"""
        return ",".join(f"{k}={flat_config[k]}" for k in sorted(flat_config.keys()))

    def _suggest_by_ei(self, unobserved: list[dict[str, Any]]) -> dict[str, Any]:
        """用 GP + EI 从未观测候选中选下一个。"""
        import numpy as np

        # 准备训练数据：X = 候选编码，y = scores
        candidates = self._get_candidates()
        all_flats = [flatten_dict(c) for c in candidates]
        field_names = sorted({k for f in all_flats for k in f.keys()})

        # 编码：每个字段值映射到数值（分类 one-hot 索引，数值 原值）
        # 简化：所有字段统一转 float（分类用 index 编码）
        field_value_maps: dict[str, dict[Any, float]] = {}
        for fname in field_names:
            unique_vals = sorted({str(f.get(fname, "")) for f in all_flats})
            field_value_maps[fname] = {v: float(i) for i, v in enumerate(unique_vals)}

        def encode(flat: dict[str, Any]) -> list[float]:
            return [field_value_maps[fname].get(flat.get(fname, ""), 0.0) for fname in field_names]

        X_train = [encode(f) for f, _ in self._observations]
        y_train = [s for _, s in self._observations]

        # 拟合 GP
        X_arr = np.array(X_train, dtype=float)
        y_arr = np.array(y_train, dtype=float)

        kernel = ConstantKernel(1.0) * RBF(1.0)
        gp = GaussianProcessRegressor(
            kernel=kernel,
            alpha=1e-6,
            normalize_y=True,
            random_state=self._seed,
        )
        gp.fit(X_arr, y_arr)

        # 计算每个未观测候选的 EI
        unobserved_flats = [flatten_dict(c) for c in unobserved]
        X_cand = np.array([encode(f) for f in unobserved_flats], dtype=float)

        mu, sigma = gp.predict(X_cand, return_std=True)
        mu = np.asarray(mu, dtype=float)
        sigma = np.asarray(sigma, dtype=float)

        # 最优值（maximize 最大；minimize 最小）
        if self._maximize:
            best_y = max(y_train)
            improvement = mu - best_y  # >0 表示更好
        else:
            best_y = min(y_train)
            improvement = best_y - mu  # >0 表示更好

        # EI = improvement * Φ(z) + σ * φ(z), z = improvement / (σ + ε)
        # 使用 numpy 向量化（避免 scipy 依赖）
        eps = 1e-9
        z = improvement / (sigma + eps)
        # 标准正态 CDF/PDF 的 numpy 实现
        cdf_z = 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))
        pdf_z = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
        ei = improvement * cdf_z + sigma * pdf_z

        # 取 EI 最大的候选（sigma=0 时 EI=0，避免选已观测点）
        best_idx = int(np.argmax(ei))
        return unobserved[best_idx]


__all__ = ["BayesianOptimizer"]
