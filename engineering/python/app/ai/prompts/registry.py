"""提示词注册表机制（自进化 M0 · Prompt Registry v0）。

把 LLM 提示词收编为带 ``prompt_id + version`` 的注册表条目，使 trace /
失败案例可追溯到「哪个版本的提示词产出」——这是 T1 提示词迭代
（自进化第一级）的前提：改版本、对比版本、按失败类别回滚都靠它。

线程安全：注册 / 读取均走锁。同一 ``(prompt_id, version)`` 重复注册
视为热更新覆盖（迭代实验用）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

__all__ = ["PromptTemplate", "PromptRegistry"]


@dataclass(frozen=True)
class PromptTemplate:
    """一条注册表条目。

    Attributes:
        prompt_id: 逻辑标识（如 ``orchestrator.gcode_repair.system``）。
        version: 整数版本号，从 1 起；``get`` 缺省取最新版本。
        template: 模板正文。占位符用 ``{name}`` 字面形式，
            由 ``render`` 做字面替换。
        description: 用途说明（审计用，不进提示词）。
    """

    prompt_id: str
    version: int
    template: str
    description: str = ""

    @property
    def key(self) -> tuple[str, int]:
        return (self.prompt_id, self.version)


class PromptRegistry:
    """进程内提示词注册表。

    占位符约定：``render`` 用**字面替换**（``str.replace``）而非
    ``str.format``——提示词正文常含 JSON 输出示例的花括号，format 会把
    示例当占位符解析并抛 KeyError/ValueError；字面替换对正文零约束。
    未提供的占位符原样保留（不报错），便于渐进迁移。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._templates: dict[tuple[str, int], PromptTemplate] = {}

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(
        self,
        prompt_id: str,
        version: int,
        template: str,
        description: str = "",
    ) -> PromptTemplate:
        """注册（或热更新覆盖）一条模板。"""
        if not prompt_id or not template:
            raise ValueError("prompt_id 与 template 均不能为空")
        if not isinstance(version, int) or version < 1:
            raise ValueError(f"version 必须为正整数，实际: {version!r}")
        entry = PromptTemplate(prompt_id=str(prompt_id), version=version, template=template, description=description)
        with self._lock:
            self._templates[entry.key] = entry
        return entry

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get(self, prompt_id: str, version: int | None = None) -> PromptTemplate:
        """取模板；``version=None`` 取该 id 的最新版本。

        Raises:
            KeyError: 未注册。
        """
        with self._lock:
            if version is not None:
                entry = self._templates.get((prompt_id, int(version)))
                if entry is None:
                    raise KeyError(f"提示词未注册: {prompt_id}@v{version}")
                return entry
            versions = [v for pid, v in self._templates if pid == prompt_id]
            if not versions:
                raise KeyError(f"提示词未注册: {prompt_id}")
            return self._templates[(prompt_id, max(versions))]

    def render(
        self,
        template_id: str,
        version: int | None = None,
        **kwargs: Any,
    ) -> tuple[str, PromptTemplate]:
        """渲染模板，返回 ``(渲染文本, 模板)``。

        模板选择参数命名为 ``template_id``（而非 prompt_id），使模板
        占位符可以安全地叫 ``{prompt_id}``（不与关键字参数撞名）。
        返回模板本体是为了让调用方把 ``prompt_id``/``version`` 一并写入
        trace / 案例库——版本可追溯是本注册表存在的意义。
        """
        entry = self.get(template_id, version)
        text = entry.template
        for name, value in kwargs.items():
            text = text.replace("{" + name + "}", str(value))
        return text, entry

    def list_ids(self) -> list[str]:
        """列出全部已注册 prompt_id（去重升序）。"""
        with self._lock:
            return sorted({pid for pid, _ in self._templates})

    def unregister(self, prompt_id: str, version: int) -> bool:
        """注销一条模板（提案被拒/回滚时移除候选版本）。

        Returns:
            是否确实移除（本就不存在返回 False）。
        """
        with self._lock:
            return self._templates.pop((prompt_id, int(version)), None) is not None

    def versions(self, prompt_id: str) -> list[int]:
        """某 prompt_id 的全部已注册版本号（升序；未注册返回空表）。"""
        with self._lock:
            return sorted(v for pid, v in self._templates if pid == prompt_id)
