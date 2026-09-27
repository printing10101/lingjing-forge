"""signal-fusion-kb attention 融合策略学术诚信守卫的回归测试.

背景（空壳修复 2026-09）：``CrossModalAttentionFusion`` 使用随机初始化权重
（未经训练，模块内自带 runtime warning），但知识库端点此前允许直接以
``strategy="attention"`` 调用并输出"融合结果"，存在伪科学输出风险。

修复后行为：
- 默认环境：attention 策略抛 ``ValueError``（引导改用 weighted）；
- 显式设置 ``LNN_SIGNAL_FUSION_ALLOW_UNTRAINED=1``：放行（仅限开发调试）；
- weighted 策略（确定性加权平均）不受影响。
"""

from __future__ import annotations

import pytest

from app.rag.signal_fusion_kb import SignalFusionKnowledgeBase, SignalSample


def _sample(signal_type: str) -> SignalSample:
    return SignalSample(
        signal_type=signal_type,
        source="unit_test",
        features=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 250.0, 248.0, 0.9],
    )


def _two_modal_samples() -> list[SignalSample]:
    return [_sample("vibration"), _sample("cutting_force")]


class TestAttentionGuard:
    def test_attention_refused_by_default(self, monkeypatch):
        monkeypatch.delenv("LNN_SIGNAL_FUSION_ALLOW_UNTRAINED", raising=False)
        kb = SignalFusionKnowledgeBase()
        with pytest.raises(ValueError, match="学术诚信"):
            kb.fuse_signals(_two_modal_samples(), strategy="attention")

    def test_attention_refusal_message_suggests_weighted(self, monkeypatch):
        monkeypatch.delenv("LNN_SIGNAL_FUSION_ALLOW_UNTRAINED", raising=False)
        kb = SignalFusionKnowledgeBase()
        with pytest.raises(ValueError, match="weighted"):
            kb.fuse_signals(_two_modal_samples(), strategy="attention")

    def test_attention_allowed_with_explicit_env(self, monkeypatch):
        monkeypatch.setenv("LNN_SIGNAL_FUSION_ALLOW_UNTRAINED", "1")
        kb = SignalFusionKnowledgeBase()
        result = kb.fuse_signals(_two_modal_samples(), strategy="attention")
        assert result.strategy == "attention"
        assert len(result.fused_vector) > 0
        assert set(result.modality_weights) == {"vibration", "cutting_force"}

    def test_weighted_strategy_unaffected(self, monkeypatch):
        monkeypatch.delenv("LNN_SIGNAL_FUSION_ALLOW_UNTRAINED", raising=False)
        kb = SignalFusionKnowledgeBase()
        result = kb.fuse_signals(_two_modal_samples(), strategy="weighted")
        assert result.strategy == "weighted"
        assert len(result.fused_vector) > 0
