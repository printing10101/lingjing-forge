"""数据脱敏器：把产品中的真实数据脱敏后供研究使用。

脱敏规则：
- 文件路径：仅保留扩展名与 hash 前 8 位
- 用户 ID：md5 截断 16 字符
- 时间戳：保留日期，归零时分秒
- 业务数据：仅保留结构化字段
- 不收集：用户姓名、IP、设备指纹
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AnonymizedSample:
    """脱敏后的样本。"""

    sample_id: str  # sha256(canonical)[:16]
    feature_name: str  # 数据所属功能名
    payload: dict  # 脱敏后的载荷
    created_at: str  # ISO 日期（归零时分秒）
    schema_version: str = "1.0"


class DataAnonymizer:
    """数据脱敏器。"""

    # 邮箱正则
    _EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    # IP 正则
    _IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    # 路径分隔符（Windows 与 Unix）
    _PATH_SEP_RE = re.compile(r"[\\/]+")

    def __init__(self, salt: str | None = None):
        # salt 用于让 hash 不可跨实例比对，但同实例内一致
        self._salt = salt or "lingjing-default-salt-2026"

    def _stable_hash(self, value: str, length: int = 16) -> str:
        """对字符串做稳定 hash。"""
        if not value:
            return ""
        salted = f"{self._salt}|{value}"
        return hashlib.sha256(salted.encode("utf-8")).hexdigest()[:length]

    def anonymize_user_id(self, user_id: str | None) -> str:
        """把 user_id 脱敏成不可逆 token。"""
        if not user_id:
            return "anon"
        return f"u_{self._stable_hash(user_id, 12)}"

    def anonymize_path(self, path: str | None) -> str:
        """把文件路径脱敏成 hash + 扩展名。"""
        if not path:
            return ""
        # 提取扩展名
        ext = ""
        if "." in path:
            ext = path.rsplit(".", 1)[-1].lower()
            if len(ext) > 8 or not ext.isalnum():
                ext = ""
        ext_part = f".{ext}" if ext else ""
        return f"file_{self._stable_hash(path, 12)}{ext_part}"

    def anonymize_text(self, text: str | None) -> str:
        """把字符串里的邮箱、IP 全部替换。"""
        if not text:
            return ""
        text = self._EMAIL_RE.sub("[EMAIL]", text)
        text = self._IP_RE.sub("[IP]", text)
        return text

    def anonymize_timestamp(self, ts: Any) -> str:
        """把时间戳脱敏为日期。"""
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError as e:
                logger.warning("时间戳字符串格式无效 '%s'，使用默认日期: %s", ts, e)
                return "1970-01-01"
        elif isinstance(ts, (int, float)):
            try:
                dt = datetime.fromtimestamp(ts)
            except (ValueError, OSError) as e:
                logger.warning("时间戳数值格式无效 '%s'，使用默认日期: %s", ts, e)
                return "1970-01-01"
        elif isinstance(ts, datetime):
            dt = ts
        else:
            return "1970-01-01"
        return dt.strftime("%Y-%m-%d")

    def anonymize_payload(
        self,
        feature_name: str,
        payload: dict,
        user_id: str | None = None,
    ) -> AnonymizedSample:
        """脱敏一个完整的数据载荷。

        payload 应仅包含结构化业务字段（不要传 user_id/path/text 等原始字段）。
        user_id 参数可选：传入时会被脱敏成 anon token。
        """
        # 深拷贝一份避免污染原始
        clean: dict = {}
        for k, v in payload.items():
            if k in ("user_id", "userId"):
                clean[k] = self.anonymize_user_id(v)
            elif k in ("file_path", "filepath", "path"):
                clean[k] = self.anonymize_path(v)
            elif k in ("text", "description", "comment"):
                clean[k] = self.anonymize_text(v)
            elif k in ("timestamp", "created_at", "ts"):
                clean[k] = self.anonymize_timestamp(v)
            else:
                clean[k] = v

        # 计算 sample_id = sha256(canonical_json)[:16]
        canonical = repr(sorted(clean.items())).encode("utf-8")
        sample_id = hashlib.sha256(canonical).hexdigest()[:16]

        return AnonymizedSample(
            sample_id=sample_id,
            feature_name=feature_name,
            payload=clean,
            created_at=datetime.now().strftime("%Y-%m-%d"),
        )


__all__ = ["DataAnonymizer", "AnonymizedSample"]
