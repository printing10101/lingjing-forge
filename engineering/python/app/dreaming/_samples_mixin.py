"""效果样本持久化/录入 mixin（从 effectiveness_metrics 拆出）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.dreaming._metrics_models import OutcomeSample

logger = logging.getLogger(__name__)


class _SamplesMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _lock: Any
    _samples: Any
    samples_dir: Any

    def _samples_file(self, rule_id: str) -> Path:
        return self.samples_dir / f"{rule_id}.json"

    def _load_samples(self) -> None:
        """启动时加载所有样本文件。"""
        try:
            for samples_file in self.samples_dir.glob("*.json"):
                try:
                    with open(samples_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    rule_id = data.get("rule_id", "")
                    if not rule_id:
                        continue
                    samples_data = data.get("samples", [])
                    samples = [OutcomeSample(**s) for s in samples_data]
                    self._samples[rule_id] = samples
                except (OSError, json.JSONDecodeError, TypeError) as e:
                    logger.warning("加载样本文件失败 %s: %s", samples_file, e)
        except OSError as e:
            logger.warning("扫描样本目录失败：%s", e)

    def _save_samples(self, rule_id: str) -> None:
        """持久化单条规则的样本。"""
        with self._lock:
            samples = self._samples.get(rule_id, [])
            data = {
                "rule_id": rule_id,
                "samples": [s.to_dict() for s in samples],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        try:
            samples_file = self._samples_file(rule_id)
            with open(samples_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("样本持久化失败 rule_id=%s: %s", rule_id, e)

    # 样本录入 API

    def record_sample(self, sample: OutcomeSample) -> bool:
        """录入单次规则触发的效果样本。

        Args:
            sample: 效果样本。

        Returns:
            是否录入成功。
        """
        with self._lock:
            if sample.rule_id not in self._samples:
                self._samples[sample.rule_id] = []
            self._samples[sample.rule_id].append(sample)
            # 保留最近 1000 条样本（防止内存膨胀）
            if len(self._samples[sample.rule_id]) > 1000:
                self._samples[sample.rule_id] = self._samples[sample.rule_id][-1000:]
        self._save_samples(sample.rule_id)
        return True

    def record_samples(self, samples: list[OutcomeSample]) -> int:
        """批量录入样本。

        Args:
            samples: 样本列表。

        Returns:
            成功录入的样本数。
        """
        count = 0
        for sample in samples:
            if self.record_sample(sample):
                count += 1
        return count
