"""降维投影模块（PCA / t-SNE / UMAP）.

从原 ``explainability_service.py`` 拆分。封装降维器缓存与投影逻辑，
线程安全（同一 ``model_uri:method:dim`` 的降维器序列化复用）。

设计原则
--------
- PCA / UMAP 支持 ``fit`` + ``transform``，降维器缓存复用
- t-SNE 无 ``transform`` 方法，每次重新拟合（不支持复用）
- 缓存键：``{model_uri}:{method}:{dim}``
- 锁粒度：仅 fit 段加锁，transform 段无锁（reducer 已不可变）
"""

from __future__ import annotations

import os
import threading
from typing import Any

import numpy as np

from app.contracts.explainability import ProjectionError, ProjectionMethod


class ProjectorCache:
    """降维器缓存（按 ``model_uri:method:dim`` 键，线程安全）.

    Parameters
    ----------
    reducers_root : str
        降维器序列化存储目录（v1 仅创建目录，序列化复用暂未实现）。
    """

    def __init__(self, reducers_root: str) -> None:
        self._cache: dict[str, tuple[str, Any]] = {}
        self._lock = threading.Lock()
        self._reducers_root = reducers_root
        os.makedirs(self._reducers_root, exist_ok=True)

    def project(
        self,
        method: str,
        data: np.ndarray,
        dim: int,
        model_uri: str,
    ) -> np.ndarray:
        """降维投影（PCA / t-SNE / UMAP）.

        Parameters
        ----------
        method : str
            降维方法（``ProjectionMethod`` 常量）。
        data : np.ndarray
            输入数据 ``[N, hidden_dim]``。
        dim : int
            目标维度（2 或 3）。
        model_uri : str
            模型 URI（用于降维器缓存键）。

        Returns
        -------
        np.ndarray
            降维后坐标 ``[N, dim]``。

        Raises
        ------
        ProjectionError
            降维失败（样本数不足 / 维度不匹配 / 方法不可用）。
        """
        if data.ndim != 2:
            raise ProjectionError(f"降维输入必须为 2D 数组 [N, hidden_dim]，当前: {data.shape}")
        n_samples, n_features = data.shape
        if n_samples < 2:
            raise ProjectionError(f"降维样本数不足（需要 >=2，当前: {n_samples}）")
        if dim not in (2, 3):
            raise ProjectionError(f"目标维度必须为 2 或 3，当前: {dim}")

        cache_key = f"{model_uri}:{method}:{dim}"

        # PCA：支持 fit + transform，降维器序列化复用
        if method == ProjectionMethod.PCA:
            try:
                from sklearn.decomposition import PCA
            except ImportError as exc:
                raise ProjectionError("PCA 需要 scikit-learn，请安装: pip install scikit-learn") from exc

            with self._lock:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    _, reducer = cached
                else:
                    n_components = min(dim, n_features, n_samples)
                    if n_components < dim:
                        raise ProjectionError(f"PCA 分量数 {n_components} 小于目标维度 {dim}，请减少 dim 或增加样本数")
                    reducer = PCA(n_components=n_components)
                    reducer.fit(data)
                    self._cache[cache_key] = (method, reducer)
            try:
                return reducer.transform(data)[:, :dim]
            except (ValueError, RuntimeError) as exc:
                raise ProjectionError(f"PCA 投影失败: {exc}") from exc

        # t-SNE：无 transform 方法，每次重新拟合（不支持复用）
        if method == ProjectionMethod.TSNE:
            if n_samples > 5000:
                raise ProjectionError(f"t-SNE 样本数限制 <=5000，当前: {n_samples}，请改用 PCA 或下采样")
            try:
                from sklearn.manifold import TSNE
            except ImportError as exc:
                raise ProjectionError("t-SNE 需要 scikit-learn，请安装: pip install scikit-learn") from exc

            n_components = min(dim, n_features, n_samples - 1)
            if n_components < dim:
                raise ProjectionError(f"t-SNE 分量数 {n_components} 小于目标维度 {dim}")
            reducer = TSNE(
                n_components=n_components,
                perplexity=min(30.0, max(5.0, n_samples - 1)),
                init="pca",
                learning_rate="auto",
                n_iter=1000,
                random_state=42,
            )
            try:
                result = reducer.fit_transform(data)
                return result[:, :dim]
            except (ValueError, RuntimeError) as exc:
                raise ProjectionError(f"t-SNE 投影失败: {exc}") from exc

        # UMAP：可选依赖
        if method == ProjectionMethod.UMAP:
            try:
                import umap
            except ImportError as exc:
                raise ProjectionError("UMAP 需要 umap-learn，请安装: pip install umap-learn") from exc

            with self._lock:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    _, reducer = cached
                else:
                    n_components = min(dim, n_features, n_samples - 1)
                    if n_components < dim:
                        raise ProjectionError(f"UMAP 分量数 {n_components} 小于目标维度 {dim}")
                    reducer = umap.UMAP(
                        n_components=n_components,
                        random_state=42,
                    )
                    reducer.fit(data)
                    self._cache[cache_key] = (method, reducer)
            try:
                return reducer.transform(data)[:, :dim]
            except (ValueError, RuntimeError) as exc:
                raise ProjectionError(f"UMAP 投影失败: {exc}") from exc

        raise ProjectionError(f"未知降维方法: {method}")


__all__ = ["ProjectorCache"]
