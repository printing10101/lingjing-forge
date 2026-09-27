"""W9.1 主权策略引擎单元测试（docs/产品叙事与战略对标-2026-09.md）。

覆盖：五级决策表、按动作开关、置信度阈值、持久化 roundtrip、
非法输入拒绝、重置语义。
"""

from __future__ import annotations

import pytest

from app.services.sovereignty import (
    AUTONOMY_CONFIDENCE_THRESHOLD,
    SovereigntyPolicyEngine,
    SovereigntySettings,
)

pytestmark = [pytest.mark.unit]


@pytest.fixture
def engine(tmp_path) -> SovereigntyPolicyEngine:
    return SovereigntyPolicyEngine(db_path=tmp_path / "sov.db")


class TestDecisionTable:
    def test_level0_full_manual_requires_confirmation(self, engine):
        engine.update_settings({"ai_autonomy_level": 0})
        for action in ("predict", "train", "agent_action"):
            d = engine.evaluate_action(action)
            assert d.requires_confirmation is True
            assert "完全手动" in d.reason

    def test_level1_advice_requires_confirmation(self, engine):
        engine.update_settings({"ai_autonomy_level": 1})
        assert engine.evaluate_action("predict", confidence=0.99).requires_confirmation is True

    def test_level2_recommended_mode_uses_toggles(self, engine):
        engine.update_settings({"ai_autonomy_level": 2})
        # 默认：predict 不需确认、train 需确认、agent_action 需确认（用户决定）
        assert engine.evaluate_action("predict").requires_confirmation is False
        assert engine.evaluate_action("train").requires_confirmation is True
        assert engine.evaluate_action("agent_action").requires_confirmation is True
        # 用户关掉 train 确认后放行
        engine.update_settings({"require_confirmation_for_train": False})
        assert engine.evaluate_action("train").requires_confirmation is False

    def test_level3_semiauto_confidence_threshold(self, engine):
        engine.update_settings({"ai_autonomy_level": 3})
        hi, lo = AUTONOMY_CONFIDENCE_THRESHOLD, AUTONOMY_CONFIDENCE_THRESHOLD - 0.1
        assert engine.evaluate_action("predict", confidence=hi).requires_confirmation is False
        assert engine.evaluate_action("predict", confidence=lo).requires_confirmation is True
        # 置信度缺失 → 保守要求确认
        assert engine.evaluate_action("predict").requires_confirmation is True
        # train 默认仍受开关约束（半自动档不自动开训练）
        assert engine.evaluate_action("train", confidence=hi).requires_confirmation is True

    def test_level4_full_auto(self, engine):
        engine.update_settings({"ai_autonomy_level": 4})
        for action in ("predict", "train", "agent_action"):
            assert engine.evaluate_action(action).requires_confirmation is False

    def test_default_settings_is_recommended_mode(self, engine):
        settings = engine.get_settings()
        assert settings.ai_autonomy_level == 2
        assert settings.require_confirmation_for_predict is False
        assert settings.require_confirmation_for_train is True


class TestPersistence:
    def test_update_persists_and_reloads(self, tmp_path):
        db = tmp_path / "sov.db"
        engine1 = SovereigntyPolicyEngine(db_path=db)
        engine1.update_settings({"ai_autonomy_level": 4, "require_confirmation_for_train": False})

        engine2 = SovereigntyPolicyEngine(db_path=db)
        settings = engine2.get_settings()
        assert settings.ai_autonomy_level == 4
        assert settings.require_confirmation_for_train is False
        assert engine2.evaluate_action("train").requires_confirmation is False

    def test_partial_update_preserves_other_fields(self, engine):
        engine.update_settings({"ai_autonomy_level": 0})
        engine.update_settings({"require_confirmation_for_predict": True})
        settings = engine.get_settings()
        assert settings.ai_autonomy_level == 0  # 未触及字段保持
        assert settings.require_confirmation_for_predict is True

    def test_reset_restores_defaults(self, engine):
        engine.update_settings({"ai_autonomy_level": 4})
        settings = engine.reset()
        assert settings.ai_autonomy_level == 2


class TestValidation:
    def test_rejects_invalid_level_on_update(self, engine):
        with pytest.raises(ValueError, match="ai_autonomy_level"):
            engine.update_settings({"ai_autonomy_level": 5})

    def test_rejects_bool_level(self, engine):
        # bool 是 int 子类，必须显式拒绝（True 不能冒充等级 1）
        with pytest.raises(ValueError):
            SovereigntySettings(ai_autonomy_level=True)

    def test_rejects_unknown_action_type(self, engine):
        with pytest.raises(ValueError, match="action_type"):
            engine.evaluate_action("format_disk")

    def test_rejects_out_of_range_confidence(self, engine):
        with pytest.raises(ValueError, match="confidence"):
            engine.evaluate_action("predict", confidence=1.5)

    def test_update_ignores_unknown_fields(self, engine):
        settings = engine.update_settings({"nonexistent_field": "x"})
        assert settings.ai_autonomy_level == 2
