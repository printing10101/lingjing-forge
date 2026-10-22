"""ADR-023 状态反馈契约测试.

覆盖：
1. 全量 initial_state → 5 元组 + collate 产出 [B, state_dim]；
2. 无 initial_state → 4 元组（向后兼容）；
3. 部分样本提供 initial_state → 构造期报错（一致性契约）；
4. 含 initial_state 的数据集可完成一步训练且 checkpoint 记录 state_feedback=True。
"""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from app.plugins.world_model.net import WorldModelConfig, WorldModelNet  # noqa: E402
from app.plugins.world_model.training.fusion_dataset import (  # noqa: E402
    FusionSampleError,
    FusionTrajectoryDataset,
    fusion_collate_fn,
)
from app.plugins.world_model.training.fusion_trainer import (  # noqa: E402
    FusionWorldModelTrainer,
)

T, H, GEO, DYN, ACT, STATE = 4, 2, 37, 6, 4, 8


def _sample(with_initial: bool) -> dict:
    rng = np.random.default_rng(0)
    sample = {
        "geometry_seq": rng.normal(size=(T, GEO)).astype(np.float32),
        "dynamics_seq": rng.normal(size=(T, DYN)).astype(np.float32),
        "actions": rng.normal(size=(T + H, ACT)).astype(np.float32),
        "target_trajectory": rng.normal(size=(H, STATE)).astype(np.float32),
    }
    if with_initial:
        sample["initial_state"] = rng.normal(size=(STATE,)).astype(np.float32)
    return sample


def test_full_initial_state_returns_5_tuple() -> None:
    ds = FusionTrajectoryDataset([_sample(True) for _ in range(3)])
    assert ds.has_initial_state is True
    assert len(ds[0]) == 5
    init = ds[0][4]
    assert init.shape == (STATE,)


def test_no_initial_state_returns_4_tuple() -> None:
    ds = FusionTrajectoryDataset([_sample(False) for _ in range(3)])
    assert ds.has_initial_state is False
    assert len(ds[0]) == 4


def test_partial_initial_state_rejected() -> None:
    with pytest.raises(FusionSampleError, match="全量提供或全量缺省"):
        FusionTrajectoryDataset([_sample(True), _sample(False), _sample(True)])


def test_collate_5_tuple_shape() -> None:
    ds = FusionTrajectoryDataset([_sample(True) for _ in range(4)])
    batch = fusion_collate_fn([ds[i] for i in range(4)])
    assert len(batch) == 5
    geo, dyn, act, tgt, init = batch
    assert init.shape == (4, STATE)
    assert geo.shape == (4, T, GEO)
    assert act.shape == (4, T + H, ACT)


def test_collate_4_tuple_backward_compatible() -> None:
    ds = FusionTrajectoryDataset([_sample(False) for _ in range(4)])
    batch = fusion_collate_fn([ds[i] for i in range(4)])
    assert len(batch) == 4


def test_trainer_step_with_initial_state(tmp_path) -> None:
    """含 initial_state 的小数据集能完成训练，checkpoint 记录 state_feedback."""
    cfg = WorldModelConfig(
        state_dim=STATE, action_dim=ACT, use_fusion=True, feature_dim=32
    )
    model = WorldModelNet(cfg)
    trainer = FusionWorldModelTrainer(model=model, model_uri="test://sf", device="cpu", epochs=1)

    samples = []
    for i in range(6):
        s = _sample(True)
        s["target_trajectory"] = np.zeros((H, STATE), dtype=np.float32)  # 稳定目标
        samples.append(s)
    train_ds = FusionTrajectoryDataset(samples)
    from torch.utils.data import DataLoader

    loader = DataLoader(train_ds, batch_size=2, collate_fn=fusion_collate_fn)
    loss = trainer._train_epoch(loader, horizon=H)
    assert np.isfinite(loss)
    assert trainer.saw_initial_state is True

    ckpt_path = trainer.save_checkpoint("0.0.1-sf-test", epoch=1, metrics={"train_loss": loss})
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    assert ckpt.get("state_feedback") is True
