"""路径净化 Mixin。

从原 ``app.plugins.skill_loader.loader`` 拆分而来，提供路径段净化与
路径遍历检测能力。被 :class:`SkillLoader` 通过多继承组合使用，
依赖宿主类的 ``self.skills_base`` 属性。
"""

from __future__ import annotations

import os
import re


class PathSafetyMixin:
    # 宿主契约：由主类 SkillLoader 提供
    skills_base: str
    """路径净化 Mixin - 提供路径段净化与路径遍历检测。

    依赖宿主类的 ``self.skills_base`` 属性。
    """

    @staticmethod
    def _sanitize_path_segment(segment: str) -> str:
        sanitized = re.sub(r'[<>:"|?*\\/]', "_", str(segment))
        sanitized = sanitized.strip(". ")
        if not sanitized:
            raise ValueError(f"路径段净化后为空: '{segment}'")
        return sanitized

    def _resolve_safe_subpath(self, *segments: str) -> str:
        safe = [self._sanitize_path_segment(s) for s in segments]
        result = os.path.normpath(os.path.join(self.skills_base, *safe))
        normalized_base = os.path.normpath(self.skills_base)
        if not result.startswith(normalized_base):
            raise ValueError(f"路径遍历检测: {result}")
        return result


__all__ = ["PathSafetyMixin"]
