"""技能数据模型定义。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from collections.abc import Callable


class SkillLevel(str, Enum):
    """技能级别：三级分层架构。"""

    GLOBAL = "global"
    PROJECT = "project"
    AGENT = "agent"


class SkillPriority(int, Enum):
    """技能优先级：数字越小优先级越高。"""

    GLOBAL = 100
    PROJECT = 50
    AGENT = 10


PRIORITY_MAP = {
    SkillLevel.GLOBAL: SkillPriority.GLOBAL,
    SkillLevel.PROJECT: SkillPriority.PROJECT,
    SkillLevel.AGENT: SkillPriority.AGENT,
}


@dataclass
class SkillVersion:
    """技能版本信息。"""

    version: str
    content_hash: str
    file_path: str
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillMetadata:
    """技能元数据。"""

    skill_id: str
    name: str
    display_name: str = ""
    description: str = ""
    version: str = "1.0.0"
    level: SkillLevel = SkillLevel.GLOBAL
    priority: SkillPriority = SkillPriority.GLOBAL
    applicable_tasks: list[str] = field(default_factory=list)
    required_context: list[str] = field(default_factory=list)
    author: str = ""
    tags: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    ratings: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "version": self.version,
            "level": self.level.value,
            "priority": self.priority.value,
            "applicable_tasks": self.applicable_tasks,
            "required_context": self.required_context,
            "author": self.author,
            "tags": self.tags,
            "dependencies": self.dependencies,
            "parameters": self.parameters,
            "avg_rating": round(sum(self.ratings) / len(self.ratings), 2) if self.ratings else None,
            "rating_count": len(self.ratings),
        }

    def applicable_to(self, task_type: str) -> bool:
        """检查技能是否适用于指定任务类型。"""
        if not self.applicable_tasks:
            return True
        return task_type in self.applicable_tasks or "*" in self.applicable_tasks

    def contexts_satisfied(self, available_context: set[str]) -> tuple[bool, list[str]]:
        """检查所需上下文是否满足。"""
        if not self.required_context:
            return True, []
        missing = [c for c in self.required_context if c not in available_context]
        return len(missing) == 0, missing


@dataclass
class Skill:
    """技能实例。"""

    metadata: SkillMetadata
    raw_content: str = ""
    body: str = ""
    code_blocks: list[tuple[str, str]] = field(default_factory=list)
    versions: dict[str, SkillVersion] = field(default_factory=dict)
    executor: Callable | None = None
    context: dict[str, Any] = field(default_factory=dict)
    is_loaded: bool = False
    is_active: bool = True
    loaded_at: float = field(default_factory=time.time)

    @property
    def current_version(self) -> SkillVersion | None:
        """获取当前版本。"""
        if not self.versions:
            return None
        sorted_versions = sorted(self.versions.items(), key=lambda x: [int(p) for p in x[0].split(".")])
        return sorted_versions[-1][1]

    def execute(self, **kwargs) -> Any:
        """执行技能。"""
        if not self.is_loaded or self.executor is None:
            raise RuntimeError(f"Skill '{self.metadata.name}' is not loaded")
        if not self.is_active:
            raise RuntimeError(f"Skill '{self.metadata.name}' is not active")
        merged_kwargs = {**self.context, **kwargs}
        return self.executor(**merged_kwargs)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "metadata": self.metadata.to_dict(),
            "is_loaded": self.is_loaded,
            "is_active": self.is_active,
            "loaded_at": self.loaded_at,
            "context": self.context,
            "versions": {v: vs.version for v, vs in self.versions.items()},
        }
