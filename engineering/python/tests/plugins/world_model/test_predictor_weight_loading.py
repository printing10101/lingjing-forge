"""回归测试：世界模型权重加载必须真正生效（2026-10-21 通电修复）.

背景（修复前的两个真实缺陷）
--------------------------
1. ``TrajectoryPredictor.load_model`` 中 ``load_state_dict`` 位于
   ``except ValueError`` 块内 ``raise`` 之后，是不可达死代码 ——
   即使 torch.load 成功，权重也从未应用到模型；
2. ``FusionWorldModelTrainer.save_checkpoint`` 产出的是含
   ``model_state_dict`` 键的 checkpoint dict（另含 optimizer /
   metrics / history 等），不能直接喂给 ``load_state_dict``。

本测试锁定三条契约：
- checkpoint dict（trainer 格式）→ 权重真正生效、``weights_source=checkpoint``；
- 裸 state_dict → 同样生效（向后兼容早期格式）；
- 无权重路径 → ``weights_source=random_init``（可被上层识别）。
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from app.plugins.world_model.net import WorldModelConfig, WorldModelNet  # noqa: E402
from app.plugins.world_model.predictor import TrajectoryPredictor  # noqa: E402

MODEL_URI = "model://world_model/test"


def _cfg() -> WorldModelConfig:
    """小配置（测试快，不改变代码路径）。"""
    return WorldModelConfig(
        state_dim=8,
        action_dim=4,
        hidden_dim=8,
        num_lstm_layers=1,
        num_ltc_layers=1,
        use_fusion=True,
        feature_dim=4,
        d_model=8,
        fused_dim=16,
        seed=7,
    )


def _perturbed_model(cfg: WorldModelConfig, delta: float) -> WorldModelNet:
    """构造一个参数被整体平移过的模型，便于验证权重是否真的换掉。"""
    model = WorldModelNet(cfg)
    with torch.no_grad():
        for param in model.parameters():
            param.add_(delta)
    return model


def test_loads_trainer_checkpoint_and_applies_weights(tmp_path) -> None:
    """trainer 格式 checkpoint：必须解包 model_state_dict 并真正加载。"""
    cfg = _cfg()
    source = _perturbed_model(cfg, delta=0.123)
    ckpt_path = tmp_path / "world_model_1.0.0.pt"
    torch.save(
        {
            "model_state_dict": source.state_dict(),
            "epoch": 3,
            "best_val_loss": 0.42,
            "training_history": {"train_loss": [1.0, 0.5], "val_loss": [1.1, 0.6]},
            "model_uri": MODEL_URI,
            "trainer_type": "FusionWorldModelTrainer",
        },
        ckpt_path,
    )

    predictor = TrajectoryPredictor(config=cfg, device="cpu")
    predictor.load_model(model_uri=MODEL_URI, weights_path=str(ckpt_path))

    assert predictor._weights_source == "checkpoint"
    assert predictor._weights_path == str(ckpt_path)

    loaded = predictor._model.state_dict()
    for name, tensor in source.state_dict().items():
        assert name in loaded
        assert torch.allclose(tensor, loaded[name], atol=1e-6), f"参数 {name} 未正确加载"


def test_loads_bare_state_dict(tmp_path) -> None:
    """裸 state_dict（非 trainer 格式）：仍需兼容加载。"""
    cfg = _cfg()
    source = _perturbed_model(cfg, delta=-0.25)
    ckpt_path = tmp_path / "bare.pt"
    torch.save(source.state_dict(), ckpt_path)

    predictor = TrajectoryPredictor(config=cfg, device="cpu")
    predictor.load_model(model_uri=MODEL_URI, weights_path=str(ckpt_path))

    assert predictor._weights_source == "checkpoint"
    loaded = predictor._model.state_dict()
    for name, tensor in source.state_dict().items():
        assert torch.allclose(tensor, loaded[name], atol=1e-6), f"参数 {name} 未正确加载"


def test_random_init_when_no_weights() -> None:
    """无权重路径：weights_source 必须显式标记为 random_init。"""
    predictor = TrajectoryPredictor(config=_cfg(), device="cpu")
    predictor.load_model(model_uri=MODEL_URI, weights_path=None)

    assert predictor._weights_source == "random_init"
    assert predictor._weights_path is None


def test_prediction_reports_weights_source(tmp_path) -> None:
    """预测响应 model_info 必须带 weights_source，供端到端验证。"""
    import numpy as np

    from app.plugins.world_model.unified_state import (
        DynamicsState,
        GeometryFeatures,
        UnifiedState,
    )

    cfg = _cfg()
    source = _perturbed_model(cfg, delta=0.05)
    ckpt_path = tmp_path / "wm.pt"
    torch.save({"model_state_dict": source.state_dict()}, ckpt_path)

    predictor = TrajectoryPredictor(config=cfg, device="cpu")
    predictor.load_model(model_uri=MODEL_URI, weights_path=str(ckpt_path))

    unified = UnifiedState(
        geometry=GeometryFeatures(
            bbox_dimensions=(100.0, 60.0, 30.0),
            feature_vector=[0.1] * cfg.feature_dim,
            symmetry_score=0.5,
            complexity_score=0.4,
        ),
        dynamics=DynamicsState(
            spindle_speed=8000.0,
            feed_rate=1200.0,
            depth_of_cut=0.5,
            tool_wear=0.1,
            vibration_rms=0.8,
            temperature=45.0,
        ),
    )
    prediction = predictor.predict(
        unified_state=unified,
        candidate_action=np.array([100.0, -50.0, 0.0, 0.0], dtype=np.float32),
        horizon=3,
    )

    assert prediction.model_info["weights_source"] == "checkpoint"
    assert prediction.model_info["mode"] == "fusion"
    assert prediction.predicted_trajectory.shape == (3, cfg.state_dim)
