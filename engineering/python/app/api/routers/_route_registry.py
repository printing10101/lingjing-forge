"""P4-1 白盒模块：FastAPI 路由声明化注册（纯 Python，零框架依赖）。

抽取自 `app/api/routers/engineering.py` 的路由注册编排逻辑：

- 路由注册从「手写 include_router 序列」→「声明式 RouterSpec 表 + 统一注册函数」
- 声明表集中管理：路由模块、域分组、注册顺序、路由前缀冲突校验

设计要点：
- 不 import FastAPI/APIRouter（白盒：仅校验 spec 结构，注册由调用方执行）
- 路由冲突检测：同一 prefix 不可重复注册（防双重注册事故）
- 幂等：同一 router 对象重复注册被拒绝（防 include 两次）
- 声明表与 engineering.py 现有路由一一对应（测试锁定防漂移）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class RouterLike(Protocol):
    """FastAPI APIRouter 的最小协议（白盒不 import fastapi）。"""

    prefix: str
    tags: list[str] | None


@dataclass(frozen=True)
class RouterSpec:
    """一条路由注册声明。

    Attributes:
        name: 路由逻辑名（如 "simulation"）。
        router: APIRouter 对象（或具备 prefix/tags 属性的对象）。
        domain: 域分组（engineering / ai / plugins 等，仅文档用途）。
        description: 用途说明（文档同步）。
    """

    name: str
    router: RouterLike
    domain: str = "engineering"
    description: str = ""

    @property
    def prefix(self) -> str:
        return self.router.prefix or ""


def validate_spec(spec: RouterSpec) -> list[str]:
    """校验单条路由声明，返回问题列表（空 = 合法）。

    校验项：
    - name 非空
    - router 具备 prefix 属性
    """
    problems: list[str] = []
    if not spec.name.strip():
        problems.append("路由 name 不能为空")
    if not hasattr(spec.router, "prefix"):
        problems.append(f"路由 {spec.name} 缺少 prefix 属性")
    return problems


def validate_specs(specs: list[RouterSpec]) -> list[str]:
    """校验整张路由表，返回所有问题（空 = 合法）。"""
    problems: list[str] = []
    seen_prefixes: dict[str, str] = {}
    seen_names: set[str] = set()

    for spec in specs:
        problems.extend(f"[{spec.name}] {p}" for p in validate_spec(spec))
        if spec.name in seen_names:
            problems.append(f"[{spec.name}] 重复注册（name 冲突）")
        seen_names.add(spec.name)

        prefix = spec.prefix
        if prefix and prefix in seen_prefixes:
            problems.append(f"[{spec.name}] 路由前缀冲突: {prefix} 已被 {seen_prefixes[prefix]} 注册")
        if prefix:
            seen_prefixes[prefix] = spec.name

    return problems


def is_duplicate_registration(specs: list[RouterSpec], router: Any) -> bool:
    """同一 router 对象是否已在声明表中注册（幂等防重复）。"""
    return any(s.router is router for s in specs)


def register_routers(
    specs: list[RouterSpec],
    include_fn: Any,
    *,
    fail_on_conflict: bool = True,
) -> list[str]:
    """按声明表执行注册。

    Args:
        specs: 路由声明表。
        include_fn: 注册回调（如 app.include_router）。
        fail_on_conflict: 冲突时抛 ValueError 还是仅返回问题。

    Returns:
        注册过程发现的问题列表（空 = 全部成功）。

    Raises:
        ValueError: fail_on_conflict=True 且存在冲突时。
    """
    problems = validate_specs(specs)
    if problems and fail_on_conflict:
        raise ValueError("路由声明表校验失败:\n" + "\n".join(problems))

    for spec in specs:
        include_fn(spec.router)
    return problems


def group_by_domain(specs: list[RouterSpec]) -> dict[str, list[str]]:
    """按域分组返回路由名（文档/审计用）。"""
    grouped: dict[str, list[str]] = {}
    for spec in specs:
        grouped.setdefault(spec.domain, []).append(spec.name)
    return grouped


__all__ = [
    "RouterSpec",
    "RouterLike",
    "validate_spec",
    "validate_specs",
    "is_duplicate_registration",
    "register_routers",
    "group_by_domain",
]
