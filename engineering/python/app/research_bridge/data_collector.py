"""使用数据收集器：把产品中真实使用数据脱敏后落盘，供研究使用。

文件位置：data/bridge/usage_logs/<filename>.jsonl
每行一条记录（jsonl 格式）。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .data_anonymizer import DataAnonymizer

logger = logging.getLogger(__name__)

# 默认数据落盘根目录
DEFAULT_BRIDGE_ROOT = "data/bridge"


class UsageDataCollector:
    """使用数据收集器（单例）。"""

    _instance: "UsageDataCollector" | None = None

    def __init__(self, bridge_root: str | None = None):
        self._bridge_root = Path(bridge_root or DEFAULT_BRIDGE_ROOT)
        self._usage_logs_dir = self._bridge_root / "usage_logs"
        self._error_samples_dir = self._bridge_root / "error_samples"
        self._anonymizer = DataAnonymizer()
        # log rotate 配置
        self._max_bytes_per_file = int(
            os.environ.get("BRIDGE_LOG_MAX_BYTES", str(20 * 1024 * 1024))  # 20MB
        )
        self._max_backups = int(os.environ.get("BRIDGE_LOG_BACKUPS", "3"))
        # 确保目录存在
        self._usage_logs_dir.mkdir(parents=True, exist_ok=True)
        self._error_samples_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_instance(cls) -> "UsageDataCollector":
        """获取单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def record_recognition(
        self,
        feature: str,
        dxf_path: str,
        success: bool,
        latency_ms: int,
        user_id: str | None = None,
        extra: dict | None = None,
    ) -> None:
        """记录一次特征识别调用。

        feature: 功能名（如 "ijepa_3d_recognizer" / "rule_based_recognizer"）
        dxf_path: 输入 DXF 路径（会被脱敏）
        success: 是否成功
        latency_ms: 耗时（毫秒）
        user_id: 用户 ID（会被脱敏）
        extra: 额外结构化字段
        """
        payload = {
            "feature": feature,
            "dxf_path": dxf_path,
            "success": success,
            "latency_ms": latency_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            payload["extra"] = extra
        sample = self._anonymizer.anonymize_payload(
            feature_name=f"recognition.{feature}",
            payload=payload,
            user_id=user_id,
        )
        self._append_jsonl(self._usage_logs_dir / "recognition.jsonl", sample)

    def record_shadow_diff(
        self,
        feature: str,
        baseline_result: Any,
        research_result: Any,
        dxf_path: str,
        user_id: str | None = None,
    ) -> None:
        """记录一次影子模式下的 baseline 与 research 的 diff。"""
        payload = {
            "feature": feature,
            "dxf_path": dxf_path,
            "baseline": baseline_result,
            "research": research_result,
            "match": baseline_result == research_result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        sample = self._anonymizer.anonymize_payload(
            feature_name=f"shadow_diff.{feature}",
            payload=payload,
            user_id=user_id,
        )
        self._append_jsonl(self._usage_logs_dir / "shadow_diff.jsonl", sample)

    def record_error(
        self,
        feature: str,
        error_type: str,
        error_message: str,
        context: dict | None = None,
        user_id: str | None = None,
    ) -> None:
        """记录一次产品错误。"""
        payload = {
            "feature": feature,
            "error_type": error_type,
            "error_message": error_message,
            "context": context or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        sample = self._anonymizer.anonymize_payload(
            feature_name=f"error.{feature}",
            payload=payload,
            user_id=user_id,
        )
        self._append_jsonl(self._error_samples_dir / "errors.jsonl", sample)

    def record_batch_errors(
        self,
        feature: str,
        error_type: str,
        error_messages: list[str],
        context: dict | None = None,
        user_id: str | None = None,
    ) -> None:
        """批量记录错误，避免循环中的 N+1 I/O 操作。"""
        if not error_messages:
            return
        timestamp = datetime.now(timezone.utc).isoformat()
        samples = []
        for msg in error_messages:
            payload = {
                "feature": feature,
                "error_type": error_type,
                "error_message": msg,
                "context": context or {},
                "timestamp": timestamp,
            }
            sample = self._anonymizer.anonymize_payload(
                feature_name=f"error.{feature}",
                payload=payload,
                user_id=user_id,
            )
            samples.append(sample)
        # 批量写入文件
        path = self._error_samples_dir / "errors.jsonl"
        try:
            self._maybe_rotate(path)
            with open(path, "a", encoding="utf-8") as f:
                for sample in samples:
                    f.write(json.dumps(sample.__dict__, ensure_ascii=False))
                    f.write("\n")
        except (OSError, IOError, ValueError, TypeError) as e:
            logger.warning("record_batch_errors failed: %s", e)

    def record_user_feedback(
        self,
        feature: str,
        feedback: str,
        rating: int | None = None,
        context: dict | None = None,
        user_id: str | None = None,
    ) -> None:
        """记录用户主动反馈。"""
        payload = {
            "feature": feature,
            "feedback": feedback,
            "rating": rating,
            "context": context or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        sample = self._anonymizer.anonymize_payload(
            feature_name=f"feedback.{feature}",
            payload=payload,
            user_id=user_id,
        )
        self._append_jsonl(self._usage_logs_dir / "feedback.jsonl", sample)

    def _append_jsonl(self, path: Path, sample: Any) -> None:
        """追加一行 jsonl 记录（带 rotate）。"""
        try:
            self._maybe_rotate(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(sample.__dict__, ensure_ascii=False))
                f.write("\n")
        except (OSError, IOError, ValueError, TypeError) as e:
            logger.warning("append_jsonl_failed path=%s err=%s", path, e)

    def _maybe_rotate(self, path: Path) -> None:
        """单文件大小超过上限时 rotate 成 .1 .2 ..."""
        try:
            if not path.exists():
                return
            if path.stat().st_size < self._max_bytes_per_file:
                return
            # 找到最大的备份编号
            for i in range(self._max_backups - 1, 0, -1):
                src = path.with_suffix(path.suffix + f".{i}")
                dst = path.with_suffix(path.suffix + f".{i + 1}")
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
            # 当前 -> .1
            first = path.with_suffix(path.suffix + ".1")
            if first.exists():
                first.unlink()
            path.rename(first)
        except (OSError, IOError) as e:
            logger.debug("rotate failed path=%s err=%s", path, e)

    def record_batch(
        self,
        feature: str,
        items: list[dict[str, Any]],
        user_id: str | None = None,
    ) -> int:
        """批量记录（如端到端测试一次性写 20 个 fixture 的结果）。

        items 中每项至少包含 ``{"dxf_path": str, "success": bool, ...}``。
        返回成功落盘条数。
        """
        ok = 0
        path = self._usage_logs_dir / "recognition.jsonl"
        try:
            self._maybe_rotate(path)
            with open(path, "a", encoding="utf-8") as f:
                for item in items:
                    payload = dict(item)
                    payload["feature"] = feature
                    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
                    sample = self._anonymizer.anonymize_payload(
                        feature_name=f"recognition.{feature}",
                        payload=payload,
                        user_id=user_id,
                    )
                    f.write(json.dumps(sample.__dict__, ensure_ascii=False))
                    f.write("\n")
                    ok += 1
        except (OSError, IOError, ValueError, TypeError) as e:
            logger.warning("record_batch failed: %s", e)
        return ok

    def health_check(self) -> dict:
        """健康检查：用于 /api/v1/status 端点。"""
        info: dict = {
            "bridge_root": str(self._bridge_root),
            "logs": {},
        }
        total = 0
        for sub in (self._usage_logs_dir, self._error_samples_dir):
            for f in sorted(sub.glob("*.jsonl")):
                size = f.stat().st_size if f.exists() else 0
                info["logs"][str(f.relative_to(self._bridge_root))] = {
                    "size_bytes": size,
                }
                total += size
        info["total_log_bytes"] = total
        return info

    def summary(self) -> dict:
        """统计当前落盘情况。"""
        result = {}
        for sub in (self._usage_logs_dir, self._error_samples_dir):
            for f in sub.glob("*.jsonl"):
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        lines = sum(1 for _ in fp)
                except (OSError, IOError):
                    lines = -1
                result[str(f.relative_to(self._bridge_root))] = lines
        return result


__all__ = ["UsageDataCollector", "DEFAULT_BRIDGE_ROOT"]
