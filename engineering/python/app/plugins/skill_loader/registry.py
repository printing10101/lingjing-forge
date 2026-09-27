"""技能注册表 - 管理所有已加载的技能实例。"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .models import Skill, SkillLevel

logger = logging.getLogger(__name__)


class SkillRegistry:
    """技能注册表 - 线程安全的技能存储和查询。"""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._skill_order: list[str] = []
        self._lock = threading.RLock()

    def register(self, skill: Skill) -> None:
        """注册技能到注册表。"""
        with self._lock:
            self._skills[skill.metadata.skill_id] = skill
            if skill.metadata.skill_id not in self._skill_order:
                self._skill_order.append(skill.metadata.skill_id)
        logger.info(
            "Skill registered: %s (level=%s, version=%s)",
            skill.metadata.name,
            skill.metadata.level.value,
            skill.metadata.version,
        )

    def get(self, skill_id: str) -> Skill | None:
        """根据 ID 获取技能。"""
        with self._lock:
            return self._skills.get(skill_id)

    def get_by_level(self, level: SkillLevel) -> list[Skill]:
        """获取指定级别的所有活跃技能。"""
        with self._lock:
            return [s for s in self._skills.values() if s.metadata.level == level and s.is_active]

    def get_by_task(self, task_type: str) -> list[Skill]:
        """获取适用于指定任务类型的所有活跃技能。"""
        with self._lock:
            return [s for s in self._skills.values() if s.is_active and s.metadata.applicable_to(task_type)]

    def list_all(self) -> list[Skill]:
        """列出所有已注册的技能。"""
        with self._lock:
            return list(self._skills.values())

    def list_all_metadata(self) -> list[dict[str, Any]]:
        """列出所有技能的元数据。"""
        with self._lock:
            return [s.metadata.to_dict() for s in self._skills.values()]

    def activate(self, skill_id: str) -> None:
        """激活指定技能。"""
        with self._lock:
            skill = self._skills.get(skill_id)
            if skill:
                skill.is_active = True
                logger.info("Skill activated: %s", skill_id)

    def deactivate(self, skill_id: str) -> None:
        """停用指定技能。"""
        with self._lock:
            skill = self._skills.get(skill_id)
            if skill:
                skill.is_active = False
                logger.info("Skill deactivated: %s", skill_id)

    def remove(self, skill_id: str) -> bool:
        """从注册表中移除技能。"""
        with self._lock:
            if skill_id in self._skills:
                del self._skills[skill_id]
                if skill_id in self._skill_order:
                    self._skill_order.remove(skill_id)
                return True
            return False

    def clear_level(self, level: SkillLevel) -> int:
        """清空指定级别的所有技能，返回移除数量。"""
        with self._lock:
            to_remove = [sid for sid, s in self._skills.items() if s.metadata.level == level]
            for sid in to_remove:
                del self._skills[sid]
                if sid in self._skill_order:
                    self._skill_order.remove(sid)
            return len(to_remove)

    def get_stats(self) -> dict[str, Any]:
        """获取注册表统计信息。"""
        with self._lock:
            skills = list(self._skills.values())
            return {
                "total": len(skills),
                "active": sum(1 for s in skills if s.is_active),
                "by_level": {level.value: sum(1 for s in skills if s.metadata.level == level) for level in SkillLevel},
                "loaded": sum(1 for s in skills if s.is_loaded),
            }
