"""P4-2 白盒测试：方言插件生命周期状态机（零框架依赖，CI 独立跑）。"""

from __future__ import annotations

import pytest

from app.postprocessor.dialect._lifecycle import (
    DialectLifecycleStage as Stage,
    LIFECYCLE_TRANSITIONS,
    assert_transition_allowed,
    can_discover,
    can_transition,
    is_terminal,
    next_stage_after_failure,
    next_stage_after_success,
)


class TestTransitions:
    def test_happy_path(self) -> None:
        # DISCOVERED COMPILED REGISTERED
        assert can_transition(Stage.DISCOVERED, Stage.COMPILED) is True
        assert can_transition(Stage.COMPILED, Stage.REGISTERED) is True

    def test_register_then_unregister(self) -> None:
        assert can_transition(Stage.REGISTERED, Stage.UNREGISTERED) is True
        # 卸载后可重新注册
        assert can_transition(Stage.UNREGISTERED, Stage.REGISTERED) is True

    def test_skip_compile_illegal(self) -> None:
        # DISCOVERED 直接 REGISTERED 非法
        assert can_transition(Stage.DISCOVERED, Stage.REGISTERED) is False

    def test_skip_register_illegal(self) -> None:
        assert can_transition(Stage.COMPILED, Stage.UNREGISTERED) is False

    def test_failure_paths(self) -> None:
        assert can_transition(Stage.DISCOVERED, Stage.FAILED) is True
        assert can_transition(Stage.COMPILED, Stage.FAILED) is True
        assert can_transition(Stage.REGISTERED, Stage.FAILED) is True

    def test_recover_from_failed(self) -> None:
        assert can_transition(Stage.FAILED, Stage.DISCOVERED) is True

    def test_terminal_has_no_forward(self) -> None:
        # REGISTERED 只能去 UNREGISTERED/FAILED，无非法目标
        assert can_transition(Stage.REGISTERED, Stage.COMPILED) is False

    def test_string_inputs_accepted(self) -> None:
        assert can_transition("discovered", "compiled") is True
        assert can_transition("registered", "unregistered") is True

    def test_unknown_stage_illegal(self) -> None:
        with pytest.raises(ValueError):
            can_transition("bogus", Stage.COMPILED)

    def test_rules_cover_all_stages(self) -> None:
        # 每个阶段都有规则
        covered = {r.current for r in LIFECYCLE_TRANSITIONS}
        assert covered == set(Stage)


class TestAssertTransition:
    def test_ok_no_raise(self) -> None:
        assert_transition_allowed(Stage.COMPILED, Stage.REGISTERED)  # 不抛

    def test_illegal_raises(self) -> None:
        with pytest.raises(ValueError, match="非法生命周期转移"):
            assert_transition_allowed(Stage.DISCOVERED, Stage.REGISTERED)


class TestNextStage:
    def test_success_operations(self) -> None:
        assert next_stage_after_success(Stage.DISCOVERED, "compile") == Stage.COMPILED
        assert next_stage_after_success(Stage.COMPILED, "register") == Stage.REGISTERED
        assert next_stage_after_success(Stage.REGISTERED, "unregister") == Stage.UNREGISTERED
        assert next_stage_after_success(Stage.UNREGISTERED, "register") == Stage.REGISTERED

    def test_discover_from_failed(self) -> None:
        assert next_stage_after_success(Stage.FAILED, "discover") == Stage.DISCOVERED

    def test_failure_always_failed(self) -> None:
        assert next_stage_after_failure(Stage.COMPILED) == Stage.FAILED
        assert next_stage_after_failure(Stage.REGISTERED) == Stage.FAILED

    def test_unknown_operation_raises(self) -> None:
        with pytest.raises(ValueError, match="未知操作"):
            next_stage_after_success(Stage.DISCOVERED, "bogus")


class TestHelpers:
    def test_can_discover(self) -> None:
        assert can_discover(Stage.FAILED) is True
        assert can_discover(Stage.UNREGISTERED) is True
        assert can_discover(Stage.DISCOVERED) is True  # 幂等可重发现
        assert can_discover(Stage.REGISTERED) is False  # 已注册不可直接重发现

    def test_is_terminal(self) -> None:
        assert is_terminal(Stage.REGISTERED) is True
        assert is_terminal(Stage.COMPILED) is False
        assert is_terminal(Stage.FAILED) is False
