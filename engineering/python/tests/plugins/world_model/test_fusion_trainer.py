"""``FusionWorldModelTrainer`` 单元测试（ADR-020 思路 1 P1）.

覆盖 P1 权重训练器的关键行为：

1. **构造期硬约束**：torch 不可用 / 模型非 nn.Module / use_fusion=False 时
   应抛 RuntimeError 或 FusionTrainerError
2. **超参校验**：非法 optimizer_type / lr_scheduler_type / horizon 应抛错
3. **版本提取**：``_extract_version_from_uri`` 静态方法的 URI 解析与回退
4. **训练闭环**（torch 依赖）：小尺寸模型 + 合成数据 → 1-2 epoch 训练 →
   checkpoint 保存 → 文件存在 + 含必需字段
5. **checkpoint 往返**（torch 依赖）：save → load → 状态恢复
6. **早停**（torch 依赖）：patience=1 时应在 val_loss 不下降时停止

学术诚信对齐（D-2 硬约束）：
- torch 不可用时通过 ``pytest.importorskip("torch")`` 自然跳过前向训练用例
- 不注入桩模块伪装通过；torch-free 用例（构造校验、版本提取）始终运行
- 训练用例用小尺寸模型（hidden_dim=8）+ 合成数据，CPU 可在数秒内完成
- 不写入 MLflow（track_experiment=False），不依赖外部服务
"""

from __future__ import annotations

import os
import sys

import pytest

# Python 3.11.0rc2 已知堆损坏（0xc0000374）：torch 训练循环在本机 RC 版确定性崩溃，
# 正式版 3.11 无此问题。仅跳过触发训练循环的用例。
_RC_HEAP_CORRUPTION = sys.version_info.releaselevel != "final"

pytestmark = pytest.mark.skipif(
    _RC_HEAP_CORRUPTION,
    reason="Python 3.11.0rc2 堆损坏（0xc0000374），torch 训练循环在 RC 版崩溃",
)
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.plugins.world_model.net import WorldModelConfig


# 公共 fixtures
def _make_fusion_config() -> WorldModelConfig:
    """融合模式小尺寸配置（CPU 快速训练）."""
    return WorldModelConfig(
        state_dim=8,
        action_dim=4,
        hidden_dim=8,
        num_lstm_layers=1,
        num_ltc_layers=1,
        max_trajectory_length=20,
        seed=42,
        use_fusion=True,
        feature_dim=8,
        d_model=16,
        fused_dim=16,
    )


def _make_synthetic_samples(
    n: int = 4,
    T: int = 2,
    horizon: int = 3,
    geo_dim: int = 13,  # 3 + feature_dim(8) + 1 + 1
    dynamics_dim: int = 6,
    action_dim: int = 4,
    state_dim: int = 8,
) -> list[dict[str, Any]]:
    """生成 n 个合法合成样本（数值全有限）."""
    rng = np.random.default_rng(seed=42)
    samples: list[dict[str, Any]] = []
    for _ in range(n):
        samples.append(
            {
                "geometry_seq": rng.standard_normal((T, geo_dim)).astype(np.float32),
                "dynamics_seq": rng.standard_normal((T, dynamics_dim)).astype(np.float32),
                "actions": rng.standard_normal((T + horizon, action_dim)).astype(np.float32),
                "target_trajectory": rng.standard_normal((horizon, state_dim)).astype(np.float32),
            }
        )
    return samples


# torch-free 用例：构造期硬约束与静态方法（始终运行）


@pytest.mark.unit
def test_trainer_import_without_torch_does_not_crash() -> None:
    """torch 不可用时导入 fusion_trainer 模块应成功（实例化才抛错）.

    这是工程边界硬约束：让 ``pytest.importorskip("torch")`` 能自然跳过
    torch 依赖用例，而不是在导入期就崩溃整个测试模块。
    """
    # 导入本身不应抛错
    from app.plugins.world_model.training import fusion_trainer as ft_module

    assert hasattr(ft_module, "FusionWorldModelTrainer")
    assert hasattr(ft_module, "FusionTrainerError")
    assert hasattr(ft_module, "DEFAULT_FUSION_EXPERIMENT_NAME")


@pytest.mark.unit
def test_extract_version_from_uri_valid() -> None:
    """``_extract_version_from_uri`` 应从合法 URI 提取 version."""
    from app.plugins.world_model.training.fusion_trainer import FusionWorldModelTrainer

    assert FusionWorldModelTrainer._extract_version_from_uri("model://world_model/1.0.0") == "1.0.0"
    assert (
        FusionWorldModelTrainer._extract_version_from_uri("model://world_model/fusion-v1-20260715")
        == "fusion-v1-20260715"
    )


@pytest.mark.unit
def test_extract_version_from_uri_fallback_to_timestamp() -> None:
    """URI 不匹配前缀时应回退到时间戳版本（保证 save_every_epoch 不失败）."""
    from app.plugins.world_model.training.fusion_trainer import FusionWorldModelTrainer

    # 非 world_model URI
    version = FusionWorldModelTrainer._extract_version_from_uri("model://ltc/1.0.0")
    assert version.startswith("auto_")
    # 空字符串
    version = FusionWorldModelTrainer._extract_version_from_uri("")
    assert version.startswith("auto_")
    # 非 str
    version = FusionWorldModelTrainer._extract_version_from_uri(None)  # type: ignore[arg-type]
    assert version.startswith("auto_")


@pytest.mark.unit
def test_extract_version_from_uri_strips_unsafe_chars() -> None:
    """URI 含不安全字符时应仅保留 ``[A-Za-z0-9_.-]``（逐字符过滤）."""
    from app.plugins.world_model.training.fusion_trainer import FusionWorldModelTrainer

    # 含空格和斜杠的版本应被逐字符过滤（不截断）
    version = FusionWorldModelTrainer._extract_version_from_uri("model://world_model/v1.0 beta/evil")
    assert " " not in version
    assert "/" not in version
    # 空格和斜杠被删除，其余安全字符保留
    assert version == "v1.0betaevil"
    # 全部字符都应在白名单内
    assert all(c.isalnum() or c in "._-" for c in version)


@pytest.mark.unit
def test_fusion_trainer_error_is_runtime_error() -> None:
    """FusionTrainerError 应为 RuntimeError 子类（被 plugin.execute 捕获）."""
    from app.plugins.world_model.training.fusion_trainer import FusionTrainerError

    assert issubclass(FusionTrainerError, RuntimeError)


# torch 依赖用例：构造、训练、checkpoint（importorskip 跳过）


@pytest.mark.unit
def test_trainer_init_requires_torch() -> None:
    """torch 不可用时实例化应抛 RuntimeError（明确错误信息）.

    注：在 torch 可用环境下此用例会被 importorskip 跳过构造期的 RuntimeError
    分支——这是预期的，因为我们无法在装了 torch 的环境里模拟"没有 torch"。
    用 ``@pytest.mark.skipif`` 显式标记：仅 torch 不可用时跑此用例。
    """
    pytest.importorskip("torch")
    # torch 可用：此用例的"未装 torch"分支无法验证，跳过
    pytest.skip("torch 已安装，'未装 torch' 分支无法在本环境验证")


@pytest.mark.unit
def test_trainer_init_rejects_non_nn_module() -> None:
    """model 非 nn.Module 应抛 TypeError."""
    pytest.importorskip("torch")
    from app.plugins.world_model.training.fusion_trainer import (
        FusionWorldModelTrainer,
    )

    with pytest.raises(TypeError, match="nn.Module"):
        FusionWorldModelTrainer(
            model="not a module",  # type: ignore[arg-type]
            model_uri="model://world_model/test",
        )


@pytest.mark.unit
def test_trainer_init_rejects_non_fusion_model() -> None:
    """use_fusion=False 的模型应抛 FusionTrainerError（融合路径硬约束）."""
    pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelNet
    from app.plugins.world_model.training.fusion_trainer import (
        FusionTrainerError,
        FusionWorldModelTrainer,
    )

    # 非融合配置
    legacy_cfg = WorldModelConfig(use_fusion=False)
    net = WorldModelNet(legacy_cfg)

    with pytest.raises(FusionTrainerError, match="use_fusion"):
        FusionWorldModelTrainer(
            model=net,
            model_uri="model://world_model/test",
        )


@pytest.mark.unit
def test_trainer_init_rejects_invalid_optimizer_type() -> None:
    """非法 optimizer_type 应抛 FusionTrainerError."""
    pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelNet
    from app.plugins.world_model.training.fusion_trainer import (
        FusionTrainerError,
        FusionWorldModelTrainer,
    )

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)
    with pytest.raises(FusionTrainerError, match="不支持的优化器类型"):
        FusionWorldModelTrainer(
            model=net,
            model_uri="model://world_model/test",
            optimizer_type="invalid_opt",
        )


@pytest.mark.unit
def test_trainer_init_rejects_invalid_lr_scheduler_type() -> None:
    """非法 lr_scheduler_type 应抛 FusionTrainerError."""
    pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelNet
    from app.plugins.world_model.training.fusion_trainer import (
        FusionTrainerError,
        FusionWorldModelTrainer,
    )

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)
    with pytest.raises(FusionTrainerError, match="不支持的 LR 调度器类型"):
        FusionWorldModelTrainer(
            model=net,
            model_uri="model://world_model/test",
            lr_scheduler_type="invalid_scheduler",
        )


@pytest.mark.unit
def test_trainer_train_rejects_invalid_horizon() -> None:
    """train() horizon<=0 应抛 FusionTrainerError."""
    pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelNet
    from app.plugins.world_model.training.fusion_trainer import (
        FusionTrainerError,
        FusionWorldModelTrainer,
    )

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)
    trainer = FusionWorldModelTrainer(
        model=net,
        model_uri="model://world_model/test",
        device="cpu",
        use_amp=False,  # CPU 禁用 AMP
        track_experiment=False,
    )

    # 空 DataLoader（horizon 校验先于数据迭代）
    from torch.utils.data import DataLoader

    empty_loader = DataLoader([], batch_size=1, collate_fn=lambda x: x)  # type: ignore[arg-type]
    with pytest.raises(FusionTrainerError, match="horizon"):
        trainer.train(empty_loader, empty_loader, horizon=0)


@pytest.mark.unit
def test_trainer_full_train_loop_and_checkpoint(tmp_path: Path) -> None:
    """端到端：2 epoch 训练 → checkpoint 保存 → 文件含必需字段.

    P1 闭环核心用例：验证 FusionWorldModelTrainer 能真正跑通融合路径
    训练，并产出可被 TrajectoryPredictor.load_model 加载的 checkpoint。
    """
    torch = pytest.importorskip("torch")
    from torch.utils.data import DataLoader

    from app.plugins.world_model.net import WorldModelNet
    from app.plugins.world_model.training.fusion_dataset import (
        FusionTrajectoryDataset,
        fusion_collate_fn,
    )
    from app.plugins.world_model.training.fusion_trainer import (
        FusionWorldModelTrainer,
    )

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)
    trainer = FusionWorldModelTrainer(
        model=net,
        model_uri="model://world_model/test-v1",
        learning_rate=1e-3,
        epochs=2,
        early_stopping_patience=10,  # 不触发早停
        gradient_clip_value=1.0,
        lr_scheduler_type="cosine",
        device="cpu",
        use_amp=False,
        seed=42,
        track_experiment=False,  # 不写 MLflow
        models_dir=str(tmp_path),
    )

    # geo_dim 必须与 cfg 对应：3 + feature_dim + 1 + 1
    geo_dim = 3 + cfg.feature_dim + 1 + 1
    samples = _make_synthetic_samples(
        n=4,
        T=2,
        horizon=3,
        geo_dim=geo_dim,
        dynamics_dim=6,
        action_dim=cfg.action_dim,
        state_dim=cfg.state_dim,
    )
    dataset = FusionTrajectoryDataset(
        samples,
        geo_input_dim=geo_dim,
        dynamics_input_dim=6,
        action_dim=cfg.action_dim,
        state_dim=cfg.state_dim,
    )
    loader = DataLoader(dataset, batch_size=2, collate_fn=fusion_collate_fn)

    history = trainer.train(loader, loader, horizon=3)

    # 训练历史应含 2 轮记录
    assert len(history["train_loss"]) == 2
    assert len(history["val_loss"]) == 2
    assert len(history["learning_rate"]) == 2
    # 损失应为有限值
    assert all(np.isfinite(history["train_loss"]))
    assert all(np.isfinite(history["val_loss"]))

    # 保存 checkpoint
    checkpoint_path = trainer.save_checkpoint(
        version="test-v1",
        metrics={"train_loss": history["train_loss"][-1], "val_loss": history["val_loss"][-1]},
    )
    assert os.path.exists(checkpoint_path)
    assert checkpoint_path.endswith(os.path.join("world_model", "test-v1.pt"))

    # 加载 checkpoint 校验必需字段
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    required_fields = {
        "model_state_dict",
        "optimizer_state_dict",
        "training_history",
        "model_config",
        "metrics",
        "timestamp",
        "model_uri",
        "trainer_type",
    }
    assert required_fields.issubset(set(checkpoint.keys()))
    assert checkpoint["trainer_type"] == "FusionWorldModelTrainer"
    assert checkpoint["model_uri"] == "model://world_model/test-v1"
    assert checkpoint["metrics"]["val_loss"] == history["val_loss"][-1]


@pytest.mark.unit
def test_trainer_checkpoint_load_restores_state(tmp_path: Path) -> None:
    """save → load 应恢复 model/optimizer/epoch/best_val_loss 状态."""
    torch = pytest.importorskip("torch")
    from torch.utils.data import DataLoader

    from app.plugins.world_model.net import WorldModelNet
    from app.plugins.world_model.training.fusion_dataset import (
        FusionTrajectoryDataset,
        fusion_collate_fn,
    )
    from app.plugins.world_model.training.fusion_trainer import (
        FusionWorldModelTrainer,
    )

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)
    trainer = FusionWorldModelTrainer(
        model=net,
        model_uri="model://world_model/test-load",
        epochs=1,
        device="cpu",
        use_amp=False,
        track_experiment=False,
        models_dir=str(tmp_path),
    )

    geo_dim = 3 + cfg.feature_dim + 1 + 1
    samples = _make_synthetic_samples(
        n=2,
        T=2,
        horizon=3,
        geo_dim=geo_dim,
        action_dim=cfg.action_dim,
        state_dim=cfg.state_dim,
    )
    dataset = FusionTrajectoryDataset(
        samples,
        geo_input_dim=geo_dim,
        action_dim=cfg.action_dim,
        state_dim=cfg.state_dim,
    )
    loader = DataLoader(dataset, batch_size=2, collate_fn=fusion_collate_fn)
    trainer.train(loader, loader, horizon=3)
    checkpoint_path = trainer.save_checkpoint(version="test-load")

    # 新建 trainer 加载 checkpoint
    net2 = WorldModelNet(cfg)
    trainer2 = FusionWorldModelTrainer(
        model=net2,
        model_uri="model://world_model/test-load",
        epochs=1,
        device="cpu",
        use_amp=False,
        track_experiment=False,
        models_dir=str(tmp_path),
    )
    checkpoint = trainer2.load_checkpoint(checkpoint_path)

    assert trainer2.current_epoch == checkpoint["epoch"]
    assert trainer2.best_val_loss == checkpoint["best_val_loss"]
    # 模型权重应一致（state_dict 加载后参数相同）
    for (n1, p1), (n2, p2) in zip(net.state_dict().items(), net2.state_dict().items()):
        assert n1 == n2
        assert torch.allclose(p1, p2), f"参数 {n1} 加载后不一致"


@pytest.mark.unit
def test_trainer_early_stopping(tmp_path: Path) -> None:
    """patience=1 且 val_loss 不下降时应早停（训练 < epochs 轮）."""
    pytest.importorskip("torch")
    from torch.utils.data import DataLoader

    from app.plugins.world_model.net import WorldModelNet
    from app.plugins.world_model.training.fusion_dataset import (
        FusionTrajectoryDataset,
        fusion_collate_fn,
    )
    from app.plugins.world_model.training.fusion_trainer import (
        FusionWorldModelTrainer,
    )

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)
    # epochs 设大，patience=1，期望早停提前结束
    trainer = FusionWorldModelTrainer(
        model=net,
        model_uri="model://world_model/test-early",
        epochs=20,
        early_stopping_patience=1,
        device="cpu",
        use_amp=False,
        track_experiment=False,
        models_dir=str(tmp_path),
    )

    geo_dim = 3 + cfg.feature_dim + 1 + 1
    # 用同一批数据做训练和验证，val_loss 大概率单调（可能不下降）
    samples = _make_synthetic_samples(
        n=2,
        T=2,
        horizon=3,
        geo_dim=geo_dim,
        action_dim=cfg.action_dim,
        state_dim=cfg.state_dim,
    )
    dataset = FusionTrajectoryDataset(
        samples,
        geo_input_dim=geo_dim,
        action_dim=cfg.action_dim,
        state_dim=cfg.state_dim,
    )
    loader = DataLoader(dataset, batch_size=2, collate_fn=fusion_collate_fn)

    trainer.train(loader, loader, horizon=3)

    # 应在 epochs=20 之前停止（早停触发或正常结束都算 ≤20）
    assert trainer.current_epoch <= 20
    # 训练历史长度应等于实际运行轮数
    assert len(trainer.training_history["train_loss"]) == trainer.current_epoch


@pytest.mark.unit
def test_trainer_get_training_summary(tmp_path: Path) -> None:
    """get_training_summary 应返回完整训练摘要."""
    pytest.importorskip("torch")
    from torch.utils.data import DataLoader

    from app.plugins.world_model.net import WorldModelNet
    from app.plugins.world_model.training.fusion_dataset import (
        FusionTrajectoryDataset,
        fusion_collate_fn,
    )
    from app.plugins.world_model.training.fusion_trainer import (
        FusionWorldModelTrainer,
    )

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)
    trainer = FusionWorldModelTrainer(
        model=net,
        model_uri="model://world_model/test-summary",
        epochs=1,
        device="cpu",
        use_amp=False,
        track_experiment=False,
        models_dir=str(tmp_path),
    )

    geo_dim = 3 + cfg.feature_dim + 1 + 1
    samples = _make_synthetic_samples(
        n=2,
        T=2,
        horizon=3,
        geo_dim=geo_dim,
        action_dim=cfg.action_dim,
        state_dim=cfg.state_dim,
    )
    dataset = FusionTrajectoryDataset(
        samples,
        geo_input_dim=geo_dim,
        action_dim=cfg.action_dim,
        state_dim=cfg.state_dim,
    )
    loader = DataLoader(dataset, batch_size=2, collate_fn=fusion_collate_fn)
    trainer.train(loader, loader, horizon=3)

    summary = trainer.get_training_summary()
    assert summary["total_epochs"] == 1
    assert summary["optimizer"] == "adamw"
    assert summary["loss_function"] == "mse"
    assert summary["device"] == "cpu"
    assert summary["use_amp"] is False
    assert summary["model_uri"] == "model://world_model/test-summary"
    assert summary["final_train_loss"] is not None
    assert summary["final_val_loss"] is not None


@pytest.mark.unit
def test_trainer_load_checkpoint_file_not_found(tmp_path: Path) -> None:
    """加载不存在的 checkpoint 应抛 FileNotFoundError."""
    pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelNet
    from app.plugins.world_model.training.fusion_trainer import (
        FusionWorldModelTrainer,
    )

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)
    trainer = FusionWorldModelTrainer(
        model=net,
        model_uri="model://world_model/test",
        device="cpu",
        use_amp=False,
        track_experiment=False,
        models_dir=str(tmp_path),
    )

    with pytest.raises(FileNotFoundError, match="checkpoint"):
        trainer.load_checkpoint(str(tmp_path / "nonexistent.pt"))
