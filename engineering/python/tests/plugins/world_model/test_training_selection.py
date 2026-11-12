"""验证训练导出的权重与报告的最佳验证轮一致。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from app.plugins.world_model.net import WorldModelConfig, WorldModelNet  # noqa: E402
from app.plugins.world_model.training.fusion_trainer import FusionTrainerError, FusionWorldModelTrainer  # noqa: E402


def make_trainer(tmp_path, epochs=3):
    config = WorldModelConfig(hidden_dim=8, num_lstm_layers=1, num_ltc_layers=1, feature_dim=4, d_model=8, fused_dim=16)
    return FusionWorldModelTrainer(WorldModelNet(config), model_uri="model://world_model/selection-test",
                                   models_dir=str(tmp_path), epochs=epochs, early_stopping_patience=10,
                                   device="cpu", use_amp=False, track_experiment=False, lr_scheduler_type="none")


@pytest.mark.parametrize("automatic_save", [False, True])
def test_export_restores_best_model_and_optimizer_instead_of_last_epoch(tmp_path, monkeypatch, automatic_save):
    trainer = make_trainer(tmp_path)
    trainer.save_every_epoch = automatic_save
    trainer.lr_scheduler = torch.optim.lr_scheduler.StepLR(trainer.optimizer, step_size=1, gamma=0.5)
    observed = {}
    parameter = trainer.model.state_head.bias

    def train_epoch(loader, horizon):
        trainer.optimizer.param_groups[0]["lr"] = 0.01 * trainer.current_epoch
        trainer.optimizer.zero_grad()
        (parameter - trainer.current_epoch).square().sum().backward()
        trainer.optimizer.step()
        observed[trainer.current_epoch] = (parameter.detach().clone(), deepcopy(trainer.optimizer.state_dict()))
        return 1.0

    losses = iter([3.0, 0.2, 2.0])
    monkeypatch.setattr(trainer, "_train_epoch", train_epoch)
    monkeypatch.setattr(trainer, "_validate", lambda *_: next(losses))
    loader = SimpleNamespace(dataset=[])
    trainer.train(loader, loader, horizon=3)
    assert torch.equal(parameter, observed[2][0])
    assert not torch.equal(parameter, observed[3][0])
    assert trainer.optimizer.param_groups[0]["lr"] == 0.01
    assert trainer.lr_scheduler.last_epoch == 2
    actual_state = trainer.optimizer.state_dict()["state"]
    for key, values in observed[2][1]["state"].items():
        for name, value in values.items():
            assert torch.equal(actual_state[key][name], value)
    if automatic_save:
        automatic = torch.load(tmp_path / "world_model" / "selection-test.pt", weights_only=True)
        assert torch.equal(automatic["model_state_dict"]["state_head.bias"], observed[2][0])
    checkpoint = torch.load(trainer.save_checkpoint(version="best"), weights_only=True)
    assert torch.equal(checkpoint["model_state_dict"]["state_head.bias"], observed[2][0])
    assert checkpoint["model_selection"]["selected_epoch"] == 2
    assert checkpoint["model_selection"]["weights_epoch"] == 2
    assert checkpoint["epoch"] == 3
    assert checkpoint["training_history"]["val_loss"] == [3.0, 0.2, 2.0]
    restored = make_trainer(tmp_path)
    restored.load_checkpoint(trainer.save_checkpoint(version="reload"))
    assert restored.selected_epoch == 2
    assert torch.equal(restored.model.state_head.bias, observed[2][0])

    # 新一轮微调须重新选模，不把旧数据上的较小损失当作本轮门槛。
    trainer.epochs = 1
    monkeypatch.setattr(trainer, "_validate", lambda *_: 5.0)
    trainer.train(loader, loader, horizon=1)
    assert trainer.best_val_loss == 5.0
    assert trainer.selected_epoch == 1
    assert trainer.training_history["val_loss"] == [5.0]


@pytest.mark.parametrize("loss", [float("nan"), float("inf")])
def test_nonfinite_validation_loss_cannot_be_reported_as_training_success(tmp_path, monkeypatch, loss):
    trainer = make_trainer(tmp_path, epochs=1)
    monkeypatch.setattr(trainer, "_train_epoch", lambda *_: 1.0)
    monkeypatch.setattr(trainer, "_validate", lambda *_: loss)
    with pytest.raises(FusionTrainerError, match="非有限"):
        loader = SimpleNamespace(dataset=[])
        trainer.train(loader, loader, horizon=3)


def test_saved_input_contract_records_observed_training_window(tmp_path):
    from torch.utils.data import DataLoader, TensorDataset

    trainer = make_trainer(tmp_path, epochs=1)
    loader = DataLoader(TensorDataset(torch.zeros(2, 4, 9), torch.zeros(2, 4, 6),
                                     torch.zeros(2, 6, 4), torch.zeros(2, 2, 8)), batch_size=2)
    trainer.train(loader, loader, horizon=2)
    checkpoint = torch.load(trainer.save_checkpoint(version="window"), weights_only=True)
    assert checkpoint["input_contract"]["history_steps"] == [4]
    assert checkpoint["input_contract"]["training_horizon"] == 2
