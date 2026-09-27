"""技能发现 Mixin。

从原 ``app.plugins.skill_loader.loader`` 拆分而来，提供：
- 目录结构初始化（:meth:`_ensure_directory_structure`）
- 三级分层技能加载（:meth:`_load_all_skills`）
- 内置技能创建（:meth:`_create_builtin_skills`）
- 目录/文件级技能加载（:meth:`_load_skills_from_directory`、:meth:`_load_skill_from_file`）
- 项目/代理级技能加载入口（:meth:`load_project_skills`、:meth:`load_agent_skills`）

被 :class:`SkillLoader` 通过多继承组合使用。依赖宿主类组合的：
- :class:`PathSafetyMixin` 提供 ``_resolve_safe_subpath`` 方法
- :class:`SkillCompilerMixin` 提供 ``_compile_code`` / ``_compute_content_hash`` 方法
以及宿主类的 ``self.skills_base`` / ``self.registry`` 属性。
"""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any
from collections.abc import Callable

from .models import (
    PRIORITY_MAP,
    Skill,
    SkillLevel,
    SkillMetadata,
    SkillPriority,
    SkillVersion,
)
from .registry import SkillRegistry
from .validator import MarkdownSkillParser

logger = logging.getLogger(__name__)


class SkillDiscoveryMixin:
    """技能发现 Mixin。

    依赖宿主类组合的 :class:`PathSafetyMixin`、:class:`SkillCompilerMixin`，
    以及 ``self.skills_base`` / ``self.registry`` 属性。
    """

    # 宿主契约：由兄弟 mixin 提供（PathSafetyMixin/SkillCompilerMixin）
    _compute_content_hash: Callable[..., str]
    _compile_code: Callable[..., Any]
    _resolve_safe_subpath: Callable[..., str]

    # 类型提示：声明 mixin 依赖的方法/属性（由其他 mixin 或宿主类提供）
    skills_base: str
    registry: SkillRegistry

    def _ensure_directory_structure(self) -> None:
        for subdir in ["global", "projects", "agents"]:
            os.makedirs(os.path.join(self.skills_base, subdir), exist_ok=True)

    def _load_all_skills(self) -> None:
        self.registry.clear_level(SkillLevel.GLOBAL)
        self.registry.clear_level(SkillLevel.PROJECT)
        self.registry.clear_level(SkillLevel.AGENT)

        global_dir = os.path.join(self.skills_base, "global")
        self._load_skills_from_directory(global_dir, SkillLevel.GLOBAL)

        projects_dir = os.path.join(self.skills_base, "projects")
        if os.path.exists(projects_dir):
            for project_id in os.listdir(projects_dir):
                project_dir = os.path.join(projects_dir, project_id)
                if os.path.isdir(project_dir):
                    self._load_skills_from_directory(project_dir, SkillLevel.PROJECT)

        agents_dir = os.path.join(self.skills_base, "agents")
        if os.path.exists(agents_dir):
            for agent_id in os.listdir(agents_dir):
                agent_dir = os.path.join(agents_dir, agent_id)
                if os.path.isdir(agent_dir):
                    self._load_skills_from_directory(agent_dir, SkillLevel.AGENT)

        self._create_builtin_skills()

        logger.info("All skills loaded: total=%d", len(self.registry.list_all()))

    def _create_builtin_skills(self) -> None:
        builtins = [
            Skill(
                metadata=SkillMetadata(
                    skill_id="error_handling",
                    name="error_handling",
                    display_name="错误处理与恢复",
                    description="标准化错误处理和自动恢复机制，支持重试、降级、熔断策略",
                    level=SkillLevel.GLOBAL,
                    priority=SkillPriority.GLOBAL,
                    applicable_tasks=["*"],
                    required_context=["error", "retry_count"],
                    tags=["error", "recovery", "retry", "circuit_breaker"],
                ),
                raw_content="# 错误处理与恢复\n\n标准化错误处理技能。",
                body="标准化错误处理技能。",
                is_loaded=True,
                is_active=True,
            ),
            Skill(
                metadata=SkillMetadata(
                    skill_id="constraint_checking",
                    name="constraint_checking",
                    display_name="工艺参数约束校验",
                    description="切削参数安全约束校验，确保加工参数在设备安全范围内",
                    level=SkillLevel.GLOBAL,
                    priority=SkillPriority.GLOBAL,
                    applicable_tasks=["prediction", "optimization"],
                    required_context=["material", "tool_type", "parameters"],
                    tags=["constraint", "validation", "safety"],
                    parameters={
                        "max_spindle_speed": 60000,
                        "max_feed_rate": 10000,
                        "max_depth_of_cut": 5.0,
                    },
                ),
                raw_content="# 工艺参数约束校验\n\n确保切削参数在安全范围内。",
                body="确保切削参数在安全范围内。",
                is_loaded=True,
                is_active=True,
            ),
            Skill(
                metadata=SkillMetadata(
                    skill_id="safety_guidelines",
                    name="safety_guidelines",
                    display_name="安全操作指南",
                    description="加工安全操作规程，包括紧急停机、防护装置检查、人员安全要求",
                    level=SkillLevel.GLOBAL,
                    priority=SkillPriority.GLOBAL,
                    applicable_tasks=["*"],
                    required_context=[],
                    tags=["safety", "guidelines", "operation"],
                ),
                raw_content="# 安全操作指南\n\n加工安全操作规程。",
                body="加工安全操作规程。",
                is_loaded=True,
                is_active=True,
            ),
        ]

        for skill in builtins:
            if not self.registry.get(skill.metadata.skill_id):
                self.registry.register(skill)

    def _load_skills_from_directory(self, directory: str, level: SkillLevel) -> list[Skill]:
        skills: list[Skill] = []
        if not os.path.exists(directory):
            return skills

        for file_name in sorted(os.listdir(directory)):
            file_path = os.path.join(directory, file_name)
            if file_name.endswith(".md"):
                skill = self._load_skill_from_file(file_path, level)
                if skill:
                    skills.append(skill)
                    self.registry.register(skill)

        return skills

    def _load_skill_from_file(self, file_path: str, level: SkillLevel) -> Skill | None:
        parsed = MarkdownSkillParser.parse(file_path)
        if parsed is None:
            return None

        meta_dict = parsed.get("metadata", {})

        priority = PRIORITY_MAP.get(level, SkillPriority.GLOBAL)

        metadata = SkillMetadata(
            skill_id=meta_dict.get("skill_id", Path(file_path).stem),
            name=meta_dict.get("name", Path(file_path).stem),
            display_name=meta_dict.get("display_name", meta_dict.get("name", "")),
            description=meta_dict.get("description", ""),
            version=meta_dict.get("version", "1.0.0"),
            level=level,
            priority=priority,
            applicable_tasks=meta_dict.get("applicable_tasks", ["*"]),
            required_context=meta_dict.get("required_context", []),
            author=meta_dict.get("author", ""),
            tags=meta_dict.get("tags", []),
            dependencies=meta_dict.get("dependencies", []),
            parameters=meta_dict.get("parameters", {}),
            source_path=file_path,
        )

        content_hash = self._compute_content_hash(parsed.get("raw_content", ""))

        version_obj = SkillVersion(
            version=metadata.version,
            content_hash=content_hash,
            file_path=file_path,
            metadata=deepcopy(meta_dict),
        )

        skill = Skill(
            metadata=metadata,
            raw_content=parsed.get("raw_content", ""),
            body=parsed.get("body", ""),
            code_blocks=parsed.get("code_blocks", []),
            versions={metadata.version: version_obj},
        )

        for lang, code in parsed.get("code_blocks", []):
            if lang == "python":
                try:
                    executor = self._compile_code(code, metadata.skill_id)
                    if executor:
                        skill.executor = executor
                        skill.is_loaded = True
                except (OSError, RuntimeError, ValueError, TypeError, NameError) as e:
                    logger.warning(
                        "Failed to load code block from %s: %s",
                        file_path,
                        e,
                        exc_info=True,
                    )

        return skill

    def load_project_skills(self, project_id: str) -> list[Skill]:
        project_dir = self._resolve_safe_subpath("projects", project_id)
        if os.path.exists(project_dir):
            return self._load_skills_from_directory(project_dir, SkillLevel.PROJECT)
        return []

    def load_agent_skills(self, agent_id: str) -> list[Skill]:
        agent_dir = self._resolve_safe_subpath("agents", agent_id)
        if os.path.exists(agent_dir):
            return self._load_skills_from_directory(agent_dir, SkillLevel.AGENT)
        return []


__all__ = ["SkillDiscoveryMixin"]
