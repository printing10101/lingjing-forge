"""Shared loading of numeric plugin inputs from metadata or local NumPy files."""

import logging
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

import numpy as np

from app.contracts.task import Artifact

logger = logging.getLogger(__name__)


def load_artifact_data(artifact: Artifact | None) -> np.ndarray | None:
    """Return an array, or None when the artifact cannot supply numeric data."""
    if artifact is None:
        return None
    try:
        if "data" in artifact.metadata:
            return np.asarray(artifact.metadata["data"], dtype=np.float32)

        uri = urlsplit(artifact.uri)
        if uri.scheme != "file":
            logger.warning("Artifact 数据仅支持 file:// 或 metadata.data: %s", artifact.uri)
            return None
        raw_path = artifact.uri[len("file://"):]
        if len(raw_path) >= 2 and raw_path[1] == ":":
            # 兼容现有 file://C:\path 产物，同时支持 Path.as_uri() 的标准形式。
            path = unquote(raw_path)
        elif uri.netloc and uri.netloc.lower() != "localhost":
            # 契约历史上也允许 file://data/array.npy 这样的相对本地路径。
            path = unquote(raw_path)
        else:
            path = url2pathname(uri.path)
        loaded = np.load(path, allow_pickle=False)
        if isinstance(loaded, np.lib.npyio.NpzFile):
            loaded.close()
            logger.warning("Artifact 数据需要单个 .npy 数组，不能使用 .npz 归档: %s", path)
            return None
        return loaded
    except (OSError, ValueError, TypeError, EOFError) as exc:
        logger.warning("加载 Artifact 数据失败: uri=%s err=%s", artifact.uri, exc)
        return None
