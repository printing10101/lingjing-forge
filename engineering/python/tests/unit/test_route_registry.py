"""P4-1 白盒测试：FastAPI 路由声明化注册（零框架依赖，CI 独立跑）。"""

from __future__ import annotations

import pytest

from app.api.routers._route_registry import (
    RouterSpec,
    group_by_domain,
    is_duplicate_registration,
    register_routers,
    validate_spec,
    validate_specs,
)


class FakeRouter:
    """最小 APIRouter 替代（具备 prefix/tags 属性）。"""

    def __init__(self, prefix: str = "", tags: list[str] | None = None) -> None:
        self.prefix = prefix
        self.tags = tags or []


class TestValidateSpec:
    def test_valid_spec_no_problems(self) -> None:
        spec = RouterSpec(name="simulation", router=FakeRouter("/api/simulation"))
        assert validate_spec(spec) == []

    def test_empty_name_problem(self) -> None:
        spec = RouterSpec(name="", router=FakeRouter("/x"))
        problems = validate_spec(spec)
        assert any("name 不能为空" in p for p in problems)

    def test_missing_prefix_problem(self) -> None:
        router = object()  # 无 prefix 属性
        spec = RouterSpec(name="bad", router=router)  # type: ignore[arg-type]
        problems = validate_spec(spec)
        assert any("缺少 prefix" in p for p in problems)


class TestValidateSpecs:
    def test_empty_table_ok(self) -> None:
        assert validate_specs([]) == []

    def test_valid_table_ok(self) -> None:
        specs = [
            RouterSpec(name="a", router=FakeRouter("/a")),
            RouterSpec(name="b", router=FakeRouter("/b")),
        ]
        assert validate_specs(specs) == []

    def test_prefix_conflict_detected(self) -> None:
        specs = [
            RouterSpec(name="a", router=FakeRouter("/dup")),
            RouterSpec(name="b", router=FakeRouter("/dup")),
        ]
        problems = validate_specs(specs)
        assert any("前缀冲突" in p for p in problems)

    def test_name_conflict_detected(self) -> None:
        specs = [
            RouterSpec(name="same", router=FakeRouter("/a")),
            RouterSpec(name="same", router=FakeRouter("/b")),
        ]
        problems = validate_specs(specs)
        assert any("重复注册" in p for p in problems)

    def test_empty_prefix_allowed_multiple(self) -> None:
        # 无 prefix 的路由（如直接挂在根）不参与冲突检测
        specs = [
            RouterSpec(name="a", router=FakeRouter("")),
            RouterSpec(name="b", router=FakeRouter("")),
        ]
        assert validate_specs(specs) == []


class TestDuplicateRegistration:
    def test_same_router_detected(self) -> None:
        router = FakeRouter("/x")
        specs = [RouterSpec(name="a", router=router)]
        assert is_duplicate_registration(specs, router) is True

    def test_different_router_not_detected(self) -> None:
        specs = [RouterSpec(name="a", router=FakeRouter("/x"))]
        assert is_duplicate_registration(specs, FakeRouter("/x")) is False


class TestRegisterRouters:
    def test_registers_all_in_order(self) -> None:
        specs = [
            RouterSpec(name="a", router=FakeRouter("/a")),
            RouterSpec(name="b", router=FakeRouter("/b")),
        ]
        registered: list[str] = []

        def include(router: object) -> None:
            registered.append(getattr(router, "prefix", "?"))

        problems = register_routers(specs, include)
        assert problems == []
        assert registered == ["/a", "/b"]

    def test_conflict_raises_when_fail_on(self) -> None:
        specs = [
            RouterSpec(name="a", router=FakeRouter("/dup")),
            RouterSpec(name="b", router=FakeRouter("/dup")),
        ]
        with pytest.raises(ValueError, match="路由声明表校验失败"):
            register_routers(specs, lambda r: None)

    def test_conflict_collected_when_not_fail(self) -> None:
        specs = [
            RouterSpec(name="a", router=FakeRouter("/dup")),
            RouterSpec(name="b", router=FakeRouter("/dup")),
        ]
        problems = register_routers(specs, lambda r: None, fail_on_conflict=False)
        assert any("前缀冲突" in p for p in problems)


class TestGroupByDomain:
    def test_groups_names(self) -> None:
        specs = [
            RouterSpec(name="sim", router=FakeRouter("/sim"), domain="engineering"),
            RouterSpec(name="chat", router=FakeRouter("/chat"), domain="engineering"),
            RouterSpec(name="lnn", router=FakeRouter("/lnn"), domain="ai"),
        ]
        grouped = group_by_domain(specs)
        assert grouped["engineering"] == ["sim", "chat"]
        assert grouped["ai"] == ["lnn"]
