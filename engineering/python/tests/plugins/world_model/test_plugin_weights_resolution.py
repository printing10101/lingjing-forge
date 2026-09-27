"""``WorldModelPlugin._resolve_weights_path`` 集成测试（ADR-020 思路 1 P1）.

验证 plugin 层权重路径解析的 P1 闭环：

    FusionWorldModelTrainer.save_checkpoint(version)
        → build_canonical_weights_path(version, models_dir)
        → 写入 <models_dir>/world_model/<version>.pt
    WorldModelPlugin._resolve_weights_path(model_uri)
        → ModelRegistry 未命中
        → resolve_world_model_weights_path(model_uri)
        → 返回 checkpoint 绝对路径

torch-free：本测试只验证路径解析（不加载权重，不前向推理），
plugin 层在纯 numpy 环境下也能完成 URI → path 解析。

学术诚信对齐（D-2 硬约束）：
- 不伪造文件存在性：用 tmp_path 真实创建 checkpoint 文件
- 不注入桩模块：直接调用真实 WorldModelPlugin 实例
- 不依赖 ModelRegistry 注册：验证"约定式解析"回退路径
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.plugins.world_model.net import WorldModelConfig
from app.plugins.world_model.plugin import WorldModelPlugin


# fixtures
def _make_plugin() -> WorldModelPlugin:
    """构造 WorldModelPlugin 实例（不需要 torch）."""
    return WorldModelPlugin(config=WorldModelConfig())


# 用例 1：ModelRegistry 未命中 + checkpoint 存在 返回路径
@pytest.mark.unit
def test_resolve_weights_path_returns_checkpoint_path(tmp_path: Path) -> None:
    """训练产出的 checkpoint 应被 _resolve_weights_path 解析到（P1 闭环核心）.

    场景：FusionWorldModelTrainer 训练后 save_checkpoint(version="1.0.0")，
    然后 WorldModelPlugin._resolve_weights_path("model://world_model/1.0.0")
    应返回该 checkpoint 的绝对路径，让 TrajectoryPredictor 能加载真实权重。
    """
    from app.plugins.world_model.training import (
        build_canonical_weights_path,
    )

    plugin = _make_plugin()
    models_dir = str(tmp_path)
    version = "1.0.0"
    uri = f"model://world_model/{version}"

    # 模拟训练器产出 checkpoint
    checkpoint_path = build_canonical_weights_path(version, models_dir=models_dir)
    Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
    Path(checkpoint_path).write_bytes(b"fake checkpoint")

    # 临时覆盖 DEFAULT_MODELS_DIR（resolve_world_model_weights_path 不传 models_dir 时用默认值）
    # 这里通过 monkeypatch 环境变量 + 重新加载模块实现
    import app.plugins.world_model.training.weights_resolver as resolver_module

    original_default = resolver_module.DEFAULT_MODELS_DIR
    resolver_module.DEFAULT_MODELS_DIR = models_dir
    try:
        resolved = plugin._resolve_weights_path(uri)
    finally:
        resolver_module.DEFAULT_MODELS_DIR = original_default

    assert resolved is not None
    assert os.path.abspath(resolved) == os.path.abspath(checkpoint_path)


# 用例 2：checkpoint 不存在 返回 None（随机初始化）
@pytest.mark.unit
def test_resolve_weights_path_returns_none_when_no_checkpoint(tmp_path: Path) -> None:
    """checkpoint 文件不存在时应返回 None（保持 "None = random init" 契约）."""
    plugin = _make_plugin()
    uri = "model://world_model/untrained-version"

    import app.plugins.world_model.training.weights_resolver as resolver_module

    original_default = resolver_module.DEFAULT_MODELS_DIR
    resolver_module.DEFAULT_MODELS_DIR = str(tmp_path)
    try:
        resolved = plugin._resolve_weights_path(uri)
    finally:
        resolver_module.DEFAULT_MODELS_DIR = original_default

    assert resolved is None


# 用例 3：非 world_model URI 返回 None（交由其他解析路径）
@pytest.mark.unit
def test_resolve_weights_path_non_world_model_uri() -> None:
    """非 model://world_model/ 前缀的 URI 应返回 None."""
    plugin = _make_plugin()
    other_uris = [
        "model://ltc/1.0.0",
        "model://cfc/2.0",
        "file:///path/to/model.pt",
        "random_string",
    ]
    for uri in other_uris:
        resolved = plugin._resolve_weights_path(uri)
        # ModelRegistry 也未命中时应返回 None
        assert resolved is None, f"URI={uri} 应返回 None，实际={resolved}"


# 用例 4：非法版本字符串 降级为 None + 警告（不抛错）
@pytest.mark.unit
def test_resolve_weights_path_unsafe_version_degrades_to_none(tmp_path: Path) -> None:
    """URI 版本字符串非法时应降级为 None（不抛 WeightsResolutionError）.

    设计权衡：_resolve_weights_path 既有契约是 "None = random init"，
    不引入新失败路径。非法 URI 通过警告日志暴露，而非抛错中断任务。
    """
    plugin = _make_plugin()
    bad_uris = [
        "model://world_model/../evil",
        "model://world_model/a/b",
        "model://world_model/",  # 空版本
    ]
    import app.plugins.world_model.training.weights_resolver as resolver_module

    original_default = resolver_module.DEFAULT_MODELS_DIR
    resolver_module.DEFAULT_MODELS_DIR = str(tmp_path)
    try:
        for uri in bad_uris:
            resolved = plugin._resolve_weights_path(uri)
            assert resolved is None, f"非法 URI={uri} 应降级为 None"
    finally:
        resolver_module.DEFAULT_MODELS_DIR = original_default


# 用例 5：端到端闭环 — 训练器写入 plugin 解析读取
@pytest.mark.unit
def test_end_to_end_train_save_resolve_loop(tmp_path: Path) -> None:
    """端到端闭环：build_canonical_weights_path 写入 → plugin._resolve_weights_path 读取.

    这是 P1 "解锁 L3 权重阻塞" 的验收用例：训练产出的 checkpoint 能被
    plugin 层无需手动注册到 ModelRegistry 即可解析加载。
    """
    pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelNet
    from app.plugins.world_model.training.fusion_trainer import (
        FusionWorldModelTrainer,
    )

    models_dir = str(tmp_path)
    version = "fusion-e2e-v1"
    uri = f"model://world_model/{version}"

    # 训练器侧：构造 + 保存（无需真正训练，只验证 save_checkpoint 路径）
    cfg = WorldModelConfig(
        state_dim=8,
        action_dim=4,
        hidden_dim=8,
        num_lstm_layers=1,
        num_ltc_layers=1,
        use_fusion=True,
        feature_dim=8,
        d_model=16,
        fused_dim=16,
        seed=42,
    )
    net = WorldModelNet(cfg)
    trainer = FusionWorldModelTrainer(
        model=net,
        model_uri=uri,
        device="cpu",
        use_amp=False,
        track_experiment=False,
        models_dir=models_dir,
    )
    checkpoint_path = trainer.save_checkpoint(version=version)
    assert os.path.exists(checkpoint_path)

    # plugin 侧：解析应返回同一路径
    plugin = WorldModelPlugin(config=cfg)
    import app.plugins.world_model.training.weights_resolver as resolver_module

    original_default = resolver_module.DEFAULT_MODELS_DIR
    resolver_module.DEFAULT_MODELS_DIR = models_dir
    try:
        resolved = plugin._resolve_weights_path(uri)
    finally:
        resolver_module.DEFAULT_MODELS_DIR = original_default

    assert resolved is not None
    assert os.path.abspath(resolved) == os.path.abspath(checkpoint_path)
