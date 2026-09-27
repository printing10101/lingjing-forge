"""解释 payload 文件持久化.

从原 ``explainability_service.py`` 拆分。封装 payload JSON 文件的写入 / 读取 /
删除操作，统一异常映射为 ``ProjectionError``。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.contracts.explainability import ProjectionError

logger = logging.getLogger(__name__)


class PayloadStore:
    """解释 payload JSON 文件持久化.

    Parameters
    ----------
    payloads_root : str
        payload 文件存储根目录（``<output_dir>/explainability/payloads/``）。
    """

    def __init__(self, payloads_root: str) -> None:
        self._root = payloads_root
        os.makedirs(self._root, exist_ok=True)

    def persist(self, payload: dict[str, Any], explanation_id: str) -> tuple[str, int]:
        """将 payload 写入 JSON 文件.

        Returns
        -------
        tuple[str, int]
            (payload_path, payload_size_bytes)
        """
        payload_path = os.path.join(self._root, f"{explanation_id}.json")
        try:
            with open(payload_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
        except (OSError, IOError, TypeError, ValueError) as exc:
            raise ProjectionError(f"payload 持久化失败: {exc}") from exc
        size = os.path.getsize(payload_path)
        return payload_path, size

    def load(self, payload_path: str) -> dict[str, Any]:
        """读取 payload JSON 文件."""
        try:
            with open(payload_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, IOError, json.JSONDecodeError, TypeError) as exc:
            raise ProjectionError(f"payload 读取失败: {exc}") from exc

    def delete(self, payload_path: str) -> None:
        """删除 payload 文件（不存在时静默忽略）."""
        try:
            if os.path.exists(payload_path):
                os.remove(payload_path)
        except (OSError, IOError) as exc:
            logger.warning(
                "删除 payload 文件失败 path=%s: %s",
                payload_path,
                exc,
            )

    def persist_diff(self, diff_payload: dict[str, Any], comparison_id: str) -> str:
        """将差异 payload 写入 JSON 文件.

        Returns
        -------
        str
            diff_payload_path
        """
        diff_path = os.path.join(self._root, f"{comparison_id}_diff.json")
        try:
            with open(diff_path, "w", encoding="utf-8") as f:
                json.dump(diff_payload, f, ensure_ascii=False, default=str)
        except (OSError, IOError, TypeError, ValueError) as exc:
            raise ProjectionError(f"差异 payload 持久化失败: {exc}") from exc
        return diff_path


__all__ = ["PayloadStore"]
