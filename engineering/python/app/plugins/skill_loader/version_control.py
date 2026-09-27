"""版本控制 Mixin。

从原 ``app.plugins.skill_loader.loader`` 拆分而来，提供：
- 版本历史查询（:meth:`get_version_history`）
- 技能文件保存与备份（:meth:`save_skill_file`）
- 技能导出/导入（:meth:`export_skill` / :meth:`import_skill`）

被 :class:`SkillLoader` 通过多继承组合使用。依赖宿主类组合的：
- :class:`PathSafetyMixin` 提供 ``_sanitize_path_segment`` / ``_resolve_safe_subpath``
- :class:`SkillDiscoveryMixin` 提供 ``_load_skill_from_file``
以及宿主类的 ``self.skills_base`` / ``self.registry`` 属性。
"""

from __future__ import annotations

import logging

import os
import shutil
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable

from .models import (
    PRIORITY_MAP,
    Skill,
    SkillLevel,
    SkillMetadata,
    SkillPriority,
)
from .registry import SkillRegistry
from .validator import MarkdownSkillParser

logger = logging.getLogger(__name__)


class VersionControlMixin:
    # 宿主契约：由兄弟 mixin 提供（PathSafetyMixin/SkillDiscoveryMixin）
    _sanitize_path_segment: Callable[..., str]
    _resolve_safe_subpath: Callable[..., str]
    _load_skill_from_file: Callable[..., Any]
    """版本控制 Mixin。

    依赖宿主类组合的 :class:`PathSafetyMixin`、:class:`SkillDiscoveryMixin`，
    以及 ``self.skills_base`` / ``self.registry`` 属性。
    """

    # 类型提示：声明 mixin 依赖的方法/属性（由其他 mixin 或宿主类提供）
    skills_base: str
    registry: SkillRegistry

    def get_version_history(self, skill_id: str) -> list | None:
        skill = self.registry.get(skill_id)
        if skill is None:
            return None

        history = []
        for ver_str, ver_obj in sorted(skill.versions.items()):
            history.append(
                {
                    "version": ver_str,
                    "content_hash": ver_obj.content_hash,
                    "file_path": ver_obj.file_path,
                    "created_at": ver_obj.created_at,
                    "created_at_iso": datetime.fromtimestamp(ver_obj.created_at, tz=timezone.utc).isoformat(),
                }
            )

        return history

    def save_skill_file(
        self,
        skill_id: str,
        content: str,
        level: SkillLevel = SkillLevel.PROJECT,
        sub_id: str | None = None,
    ) -> str:
        safe_skill_id = self._sanitize_path_segment(skill_id)

        if level == SkillLevel.GLOBAL:
            target_dir = os.path.join(self.skills_base, "global")
        elif level == SkillLevel.PROJECT:
            if not sub_id:
                raise ValueError("sub_id (project_id) required for PROJECT level skills")
            target_dir = self._resolve_safe_subpath("projects", sub_id)
        elif level == SkillLevel.AGENT:
            if not sub_id:
                raise ValueError("sub_id (agent_id) required for AGENT level skills")
            target_dir = self._resolve_safe_subpath("agents", sub_id)
        else:
            raise ValueError(f"Unknown skill level: {level}")

        os.makedirs(target_dir, exist_ok=True)

        file_name = f"{safe_skill_id}.md"
        file_path = os.path.join(target_dir, file_name)

        if os.path.exists(file_path):
            backup_dir = os.path.join(target_dir, ".versions")
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(backup_dir, f"{safe_skill_id}_{timestamp}.md")
            shutil.copy2(file_path, backup_path)
            logger.info("Backup created: %s", backup_path)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        skill = self._load_skill_from_file(file_path, level)
        if skill:
            self.registry.register(skill)

        logger.info("Skill saved: %s", file_path)
        return file_path

    def export_skill(self, skill_id: str) -> dict[str, Any] | None:
        skill = self.registry.get(skill_id)
        if skill is None:
            return None

        return {
            "skill_id": skill.metadata.skill_id,
            "name": skill.metadata.name,
            "version": skill.metadata.version,
            "raw_content": skill.raw_content,
            "metadata": skill.metadata.to_dict(),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }

    def import_skill(
        self,
        skill_package: dict[str, Any],
        level: SkillLevel = SkillLevel.PROJECT,
        sub_id: str | None = None,
    ) -> Skill | None:
        skill_id = skill_package.get("skill_id")
        raw_content = skill_package.get("raw_content")

        if not skill_id or not raw_content:
            raise ValueError("Invalid skill package: missing skill_id or raw_content")

        file_path = self.save_skill_file(
            skill_id=skill_id,
            content=raw_content,
            level=level,
            sub_id=sub_id,
        )

        parsed = MarkdownSkillParser.parse(file_path)
        if parsed is None:
            return None

        meta_dict = parsed.get("metadata", {})
        priority = PRIORITY_MAP.get(level, SkillPriority.GLOBAL)

        metadata = SkillMetadata(
            skill_id=meta_dict.get("skill_id", skill_id),
            name=meta_dict.get("name", skill_id),
            display_name=meta_dict.get("display_name", ""),
            description=meta_dict.get("description", ""),
            version=meta_dict.get("version", "1.0.0"),
            level=level,
            priority=priority,
            applicable_tasks=meta_dict.get("applicable_tasks", ["*"]),
            required_context=meta_dict.get("required_context", []),
            author=skill_package.get("metadata", {}).get("author", ""),
            tags=meta_dict.get("tags", []),
            source_path=file_path,
        )

        skill = Skill(
            metadata=metadata,
            raw_content=raw_content,
            body=parsed.get("body", ""),
            code_blocks=parsed.get("code_blocks", []),
        )

        self.registry.register(skill)
        logger.info("Skill imported: %s", skill_id)
        return skill


__all__ = ["VersionControlMixin"]
