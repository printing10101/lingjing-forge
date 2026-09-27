"""回滚冷却期与历史持久化 mixin（从 rollback_manager 拆出）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _CooldownMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _cooldowns: Any
    _lock: Any
    _rollback_history: Any
    history_dir: Any

    def _history_file(self) -> Path:
        return self.history_dir / "rollback_history.json"

    def _cooldown_file(self) -> Path:
        return self.history_dir / "cooldowns.json"

    def _load_history(self) -> None:
        """加载回滚历史和冷却期。"""
        # 加载回滚历史
        try:
            hist_file = self._history_file()
            if hist_file.exists():
                with open(hist_file, "r", encoding="utf-8") as f:
                    self._rollback_history = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("加载回滚历史失败：%s", e)

        # 加载冷却期
        try:
            cd_file = self._cooldown_file()
            if cd_file.exists():
                with open(cd_file, "r", encoding="utf-8") as f:
                    self._cooldowns = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("加载冷却期失败：%s", e)

    def _save_history(self) -> None:
        """持久化回滚历史。"""
        try:
            with open(self._history_file(), "w", encoding="utf-8") as f:
                json.dump(
                    self._rollback_history[-200:],
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError as e:
            logger.warning("回滚历史持久化失败：%s", e)

    def _save_cooldowns(self) -> None:
        """持久化冷却期。"""
        try:
            with open(self._cooldown_file(), "w", encoding="utf-8") as f:
                json.dump(self._cooldowns, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("冷却期持久化失败：%s", e)

    def _is_in_cooldown(self, rule_id: str) -> bool:
        """检查规则是否在冷却期内。"""
        with self._lock:
            cooldown_str = self._cooldowns.get(rule_id)
            if cooldown_str is None:
                return False
            try:
                cooldown_until = datetime.fromisoformat(cooldown_str)
                return datetime.now(timezone.utc) < cooldown_until
            except (ValueError, TypeError):
                # TypeError：旧 naive datetime 与 aware 比较时触发，
                # 视为冷却失效（兼容旧数据）
                return False

    def get_cooldown_remaining(self, rule_id: str) -> timedelta | None:
        """获取规则剩余冷却时间。

        Args:
            rule_id: 规则 ID。

        Returns:
            剩余冷却时间；None 表示不在冷却期。
        """
        with self._lock:
            cooldown_str = self._cooldowns.get(rule_id)
            if cooldown_str is None:
                return None
            try:
                cooldown_until = datetime.fromisoformat(cooldown_str)
                remaining = cooldown_until - datetime.now(timezone.utc)
                return remaining if remaining.total_seconds() > 0 else None
            except (ValueError, TypeError):
                # TypeError：旧 naive datetime 与 aware 比较时触发
                return None

    def clear_cooldown(self, rule_id: str) -> bool:
        """手动清除规则冷却期（用于人工干预后重新发布）。

        Args:
            rule_id: 规则 ID。

        Returns:
            是否清除成功。
        """
        with self._lock:
            if rule_id in self._cooldowns:
                del self._cooldowns[rule_id]
                self._save_cooldowns()
                logger.info("规则 %s 冷却期已手动清除", rule_id)
                return True
            return False
