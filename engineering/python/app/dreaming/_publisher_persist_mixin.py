"""_PublisherPersistMixin (split from ProgressivePublisher)."""

from __future__ import annotations

from __future__ import annotations
import json
from app.dreaming._publisher_models import (  # noqa: F401
    PublicationStage,
    PublicationRecord,
    PublicationResult,
)

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable
from app.dreaming.rule_validator import ValidationResult
from datetime import timezone


logger = logging.getLogger(__name__)


class _PublisherPersistMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_audit_recorder: Callable[..., Any]
    _lock: Any
    _records: Any
    state_dir: Any

    def _load_state(self) -> None:
        """启动时加载所有灰度发布记录。"""
        try:
            for state_file in self.state_dir.glob("*.json"):
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    record = PublicationRecord.from_dict(data)
                    self._records[record.rule_id] = record
                except (OSError, json.JSONDecodeError, KeyError) as e:
                    logger.warning("加载灰度状态文件失败 %s: %s", state_file, e)
        except OSError as e:
            logger.warning("扫描灰度状态目录失败：%s", e)

    def _save_record(self, record: PublicationRecord) -> None:
        """持久化单条灰度记录。"""
        try:
            state_file = self._state_file(record.rule_id)
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("灰度状态持久化失败 rule_id=%s: %s", record.rule_id, e)

    def _get_or_create_record(self, rule_id: str) -> PublicationRecord:
        """获取或创建灰度记录。调用方须持有 _lock。"""
        if rule_id not in self._records:
            self._records[rule_id] = PublicationRecord(rule_id=rule_id)
        return self._records[rule_id]

    def _state_file(self, rule_id: str) -> Path:
        return self.state_dir / f"{rule_id}.json"

    def get_record(self, rule_id: str) -> PublicationRecord | None:
        """查询规则的灰度发布记录。"""
        with self._lock:
            return self._records.get(rule_id)

    def list_publications(self) -> list[PublicationRecord]:
        """列出所有灰度发布中的规则。"""
        with self._lock:
            return list(self._records.values())

    def update_metrics(
        self,
        rule_id: str,
        metrics: dict[str, Any],
    ) -> bool:
        """更新规则的效果指标快照。

        Args:
            rule_id: 规则 ID。
            metrics: 效果指标字典。

        Returns:
            是否更新成功。
        """
        with self._lock:
            record = self._records.get(rule_id)
            if record is None:
                logger.warning("规则 %s 未发布，无法更新指标", rule_id)
                return False
            record.last_metrics = dict(metrics)
            record.stage_history.append(
                {
                    "action": "metrics_update",
                    "operated_at": datetime.now(timezone.utc).isoformat(),
                    "metrics": dict(metrics),
                }
            )
            self._save_record(record)
            return True

    def _update_stage_only(
        self,
        rule_id: str,
        target_stage: PublicationStage,
        operated_at: str,
        validation_result: ValidationResult | None,
    ) -> PublicationResult:
        """非 FULL 阶段晋级：仅更新灰度记录，不真正 apply。"""
        with self._lock:
            record = self._records.get(rule_id)
            if record is None:
                return PublicationResult(
                    success=False,
                    rule_id=rule_id,
                    stage=target_stage,
                    operated_at=operated_at,
                    error="灰度记录丢失",
                )
            record.current_stage = target_stage
            record.entered_at = operated_at
            self._save_record(record)

        # 写入审计日志
        try:
            self._get_audit_recorder().record_rule_application(
                rule_id=rule_id,
                rule_description=f"灰度晋级到 {target_stage.value}",
                validation_passed=(validation_result.passed if validation_result is not None else True),
                applied=False,
                rollback_triggered=False,
            )
        except Exception as e:
            logger.error("灰度晋级审计写入失败：%s", e)

        return PublicationResult(
            success=True,
            rule_id=rule_id,
            stage=target_stage,
            traffic_percentage=target_stage.traffic_percentage,
            operated_at=operated_at,
            validation_result=validation_result,
        )
