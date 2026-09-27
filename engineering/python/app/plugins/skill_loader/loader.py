"""技能加载器主模块（re-export shim + 主类组合）。

本模块原为 1003 行的 God class，已按职责拆分为 5 个 mixin 模块：
- :mod:`app.plugins.skill_loader.path_safety` — 路径净化
- :mod:`app.plugins.skill_loader.skill_discovery` — 目录结构 + 技能发现
- :mod:`app.plugins.skill_loader.skill_compiler` — 代码编译 + 安全审计
- :mod:`app.plugins.skill_loader.sandbox_executor` — 沙箱执行 + SecurityError
- :mod:`app.plugins.skill_loader.version_control` — 版本控制

为保持向后兼容，:class:`SkillLoader` 仍定义在本模块并通过多继承组合上述 mixin；
所有原公开符号（``SkillLoader``、``SecurityError``、``get_skill_loader`` 等）
仍可从 ``app.plugins.skill_loader.loader`` 路径导入。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import config

from .lifecycle import SkillFileWatcher
from .models import (
    Skill,
    SkillLevel,
)
from .path_safety import PathSafetyMixin
from .registry import SkillRegistry
from .sandbox_executor import (
    SecurityError,
    SandboxExecutorMixin,
)
from .skill_compiler import SkillCompilerMixin
from .skill_discovery import SkillDiscoveryMixin
from .version_control import VersionControlMixin

logger = logging.getLogger(__name__)


DEFAULT_SKILLS_BASE = config.paths.skills_dir


class SkillLoader(
    PathSafetyMixin,
    SkillDiscoveryMixin,
    SkillCompilerMixin,
    SandboxExecutorMixin,
    VersionControlMixin,
):
    """技能加载器 - 三级分层架构 + 热更新 + 版本控制。

    通过多继承组合 5 个 mixin 职责模块：
    - :class:`PathSafetyMixin` — 路径净化
    - :class:`SkillDiscoveryMixin` — 技能发现
    - :class:`SkillCompilerMixin` — 代码编译 + 安全审计
    - :class:`SandboxExecutorMixin` — 沙箱执行配置（``_USE_SUBPROCESS_ISOLATION`` 等）
    - :class:`VersionControlMixin` — 版本控制
    """

    def __init__(self, skills_base_dir: str | None = None):
        if skills_base_dir is None:
            skills_base_dir = DEFAULT_SKILLS_BASE

        self.skills_base = skills_base_dir
        self.registry = SkillRegistry()
        self._context: dict[str, Any] = {}
        self._watcher: SkillFileWatcher | None = None

        self._ensure_directory_structure()
        self._load_all_skills()
        self._start_watcher()

        logger.info("SkillLoader initialized: skills_base=%s", skills_base_dir)

    def _start_watcher(self) -> None:
        self._watcher = SkillFileWatcher(self.skills_base, self, poll_interval=2.0)
        self._watcher.start()

    def stop_watcher(self) -> None:
        if self._watcher:
            self._watcher.stop()

    def get_skills_for_task(
        self,
        task_type: str,
        project_id: str | None = None,
        agent_id: str | None = None,
        available_context: set[str] | None = None,
    ) -> list[Skill]:
        skills: list[Skill] = []

        all_globals = self.registry.get_by_level(SkillLevel.GLOBAL)
        skills.extend(all_globals)

        if project_id:
            project_skills = self.load_project_skills(project_id)
            skills.extend([s for s in project_skills if s.metadata.applicable_to(task_type)])

        if agent_id:
            agent_skills = self.load_agent_skills(agent_id)
            skills.extend(agent_skills)

        skills.sort(key=lambda s: s.metadata.priority.value)

        if available_context is not None:
            skills = [s for s in skills if s.metadata.contexts_satisfied(available_context)[0]]

        logger.info(
            "Retrieved %d skills for task=%s project=%s agent=%s",
            len(skills),
            task_type,
            project_id or "N/A",
            agent_id or "N/A",
        )
        return skills

    def inject_context(self, context: dict[str, Any]) -> None:
        self._context.update(context)
        for skill in self.registry.list_all():
            skill.context.update(context)

    def execute_skill(self, skill_id: str, **kwargs: Any) -> Any:
        skill = self.registry.get(skill_id)
        if skill is None:
            raise KeyError(f"Skill not found: {skill_id}")
        return skill.execute(**kwargs)

    def execute_all(
        self,
        task_type: str,
        project_id: str | None = None,
        agent_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        skills = self.get_skills_for_task(task_type, project_id, agent_id)
        results: dict[str, Any] = {}

        for skill in skills:
            try:
                result = skill.execute(**kwargs)
                results[skill.metadata.skill_id] = {
                    "status": "success",
                    "result": result,
                }
            except (TypeError, ValueError, RuntimeError, KeyError, AttributeError, OSError) as e:
                from app.core.safe_errors import safe_error_message

                safe = safe_error_message(
                    e,
                    context=f"skill.execute.{skill.metadata.skill_id}",
                    fallback="技能执行失败",
                )
                results[skill.metadata.skill_id] = {
                    "status": "error",
                    "error": safe["message"],
                    "error_id": safe["error_id"],
                }
                logger.error(
                    "Skill execution failed %s: %s",
                    skill.metadata.name,
                    e,
                    exc_info=True,
                )

        return results

    async def inject_skills(
        self,
        task_type: str,
        project_id: str | None = None,
        agent_id: str | None = None,
        available_context: set[str] | None = None,
    ) -> str:
        skills = self.get_skills_for_task(task_type, project_id, agent_id, available_context)
        return self._merge_skills_to_context(skills)

    def _merge_skills_to_context(self, skills: list[Skill]) -> str:
        if not skills:
            return ""

        lines: list[str] = [
            "## 已注入技能指南\n",
            "_以下技能为运行时动态注入，用于指导当前任务执行：_\n",
        ]

        for i, skill in enumerate(skills, 1):
            meta = skill.metadata
            lines.append(f"### {i}. {meta.display_name or meta.name} (v{meta.version})")
            if meta.description:
                lines.append(f"**描述**: {meta.description}")
            if meta.tags:
                lines.append(f"**标签**: {', '.join(meta.tags)}")

            level_labels = {
                SkillLevel.GLOBAL: "全局",
                SkillLevel.PROJECT: "项目",
                SkillLevel.AGENT: "代理",
            }
            lines.append(f"**级别**: {level_labels.get(meta.level, meta.level.value)}")

            if meta.required_context:
                lines.append(f"**所需上下文**: {', '.join(meta.required_context)}")

            if meta.parameters:
                params_str = ", ".join(f"{k}={v}" for k, v in meta.parameters.items())
                lines.append(f"**参数**: {params_str}")

            if skill.body:
                lines.append("")
                lines.append(skill.body[:2000])
                if len(skill.body) > 2000:
                    lines.append("\n... (内容已截断)")

            if skill.code_blocks:
                lines.append("")
                lines.append("**代码块**:")
                for lang, code in skill.code_blocks[:3]:
                    lines.append(f"\n```{lang}\n{code[:1000]}\n```")

            lines.append("")

        return "\n".join(lines)

    def hot_reload(self, skill_id: str | None = None) -> dict[str, Any]:
        if skill_id:
            skill = self.registry.get(skill_id)
            if skill and skill.metadata.source_path:
                level = skill.metadata.level
                new_skill = self._load_skill_from_file(skill.metadata.source_path, level)
                if new_skill:
                    self.registry.register(new_skill)
                    return {"status": "reloaded", "skill_id": skill_id}
                return {
                    "status": "error",
                    "skill_id": skill_id,
                    "message": "Parse failed",
                }
            return {"status": "not_found", "skill_id": skill_id}

        self._load_all_skills()
        return {"status": "full_reload", "count": len(self.registry.list_all())}

    def rate_skill(self, skill_id: str, rating: float) -> dict[str, Any]:
        if rating < 0 or rating > 5:
            raise ValueError("Rating must be between 0 and 5")

        skill = self.registry.get(skill_id)
        if skill is None:
            raise KeyError(f"Skill not found: {skill_id}")

        skill.metadata.ratings.append(rating)
        avg = sum(skill.metadata.ratings) / len(skill.metadata.ratings)

        logger.info(
            "Skill rated: %s -> %.2f (%d ratings)",
            skill_id,
            rating,
            len(skill.metadata.ratings),
        )
        return {
            "skill_id": skill_id,
            "rating": rating,
            "avg_rating": round(avg, 2),
            "rating_count": len(skill.metadata.ratings),
        }

    def get_stats(self) -> dict[str, Any]:
        registry_stats = self.registry.get_stats()
        registry_stats["skills_base_dir"] = self.skills_base
        registry_stats["watcher_active"] = self._watcher is not None and self._watcher._running
        return registry_stats

    def shutdown(self) -> None:
        self.stop_watcher()
        logger.info("SkillLoader shut down")


class _LoaderHolder:
    """Thread-safe lazy holder for the :class:`SkillLoader` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: SkillLoader | None = None

    def get(self) -> SkillLoader:
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = SkillLoader()
            return self._instance

    def init(self, skills_base_dir: str | None = None) -> SkillLoader:
        with self._lock:
            if self._instance is not None:
                self._instance.shutdown()
            self._instance = SkillLoader(skills_base_dir)
            return self._instance

    def reset(self) -> None:
        with self._lock:
            self._instance = None


_holder = _LoaderHolder()


def get_skill_loader() -> SkillLoader:
    """获取共享的 :class:`SkillLoader` 单例；首次访问时懒初始化。"""
    return _holder.get()


def init_skill_loader(skills_base_dir: str | None = None) -> SkillLoader:
    """初始化技能加载器，行为与重构前完全一致。"""
    return _holder.init(skills_base_dir)


async def inject_skills(
    task_type: str,
    project_id: str | None = None,
    agent_id: str | None = None,
    available_context: set[str] | None = None,
) -> str:
    loader = get_skill_loader()
    return await loader.inject_skills(task_type, project_id, agent_id, available_context)


__all__ = [
    "SkillLoader",
    "SecurityError",
    "get_skill_loader",
    "init_skill_loader",
    "inject_skills",
]
