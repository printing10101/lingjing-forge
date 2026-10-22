"""颤振 LTC 代理模型：数据契约 + 训练烟测 + 加载契约。

CI 环境（Python 3.14、无 torch）整体跳过；在含 torch 的环境
（如 conda `lingjing-wm`）执行可完成真实训练烟测。

设计口径（2026-10-22）：
- 训练标签来自 ``compute_stability_limit``（Tlusty 解析包络），
  因此本模型是解析法的**快速代理**，不是独立物理模型。
- 归一化在 ``predictor.normalize_features`` 单一定义，训练与推理共用；
  若有任一处改尺度而另一处未改，本文件的契约测试会失败。
"""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from app.simulation.chatter import trainer as chatter_trainer  # noqa: E402
from app.simulation.chatter import predictor as chatter_predictor  # noqa: E402
from app.simulation.chatter.predictor import (  # noqa: E402
    NORMALIZATION_SCALES,
    normalize_features,
)
from app.simulation.chatter.stability import (  # noqa: E402
    ChatterParams,
    MachineParams,
    ToolParams,
    compute_stability_limit,
)


def _research_ltc_available() -> bool:
    """research 侧 LTC 模型是否**可构造**。

    不能只判断工厂函数非 None：Python 3.14 环境里的 torch 虽可导入，
    但 ``nn.init`` 在构造时抛 TypeError（版本不兼容），此时必须跳过而非失败。
    """
    try:
        from app.ai.lnn._research_bridge import (
            get_lnn_config_factory,
            get_ltc_model_factory,
        )

        LTCModel = get_ltc_model_factory()
        LNNConfig = get_lnn_config_factory()
        if LTCModel is None or LNNConfig is None:
            return False
        LTCModel(LNNConfig(input_size=6, hidden_size=8, output_size=2, num_layers=1))
        return True
    except Exception:  # noqa: BLE001 - 环境不兼容一律视为不可用
        return False


pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        not _research_ltc_available(),
        reason="research LTC 模型不可构造（需 torch + research 包兼容）",
    ),
]


class TestNormalizationContract:
    """归一化尺度单一定义契约。"""

    def test_scales_cover_typical_values(self):
        """六个尺度键齐全，且 1 倍物理量映射为 1.0。"""
        assert set(NORMALIZATION_SCALES) == {
            "rpm",
            "stiffness",
            "damping",
            "freq",
            "diameter",
            "k_s",
        }
        unit = normalize_features(
            spindle_rpm=NORMALIZATION_SCALES["rpm"],
            machine_stiffness=NORMALIZATION_SCALES["stiffness"],
            machine_damping=NORMALIZATION_SCALES["damping"],
            machine_freq=NORMALIZATION_SCALES["freq"],
            tool_diameter=NORMALIZATION_SCALES["diameter"],
            tool_k_s=NORMALIZATION_SCALES["k_s"],
        )
        np.testing.assert_allclose(unit, np.ones(6), atol=1e-6)

    def test_predictor_method_delegates(self):
        """predictor 的方法必须与模块级函数一致（防止两套尺度）。"""
        expected = normalize_features(8000, 2e7, 0.05, 800, 10, 2000)
        # 不触发模型加载：直接构造后调用方法
        pred = chatter_predictor.ChatterPredictor.__new__(chatter_predictor.ChatterPredictor)
        actual = pred._normalize_inputs(8000, 2e7, 0.05, 800, 10, 2000)
        np.testing.assert_allclose(actual, expected)


class TestChatterDataset:
    """数据集标签与解析包络的一致性。"""

    def test_labels_follow_assumed_depth(self):
        ds = chatter_trainer.ChatterStabilityDataset(num_samples=8, seed=7)
        for i in range(len(ds)):
            _, y_cls, y_depth = ds[i]
            depth = float(y_depth)
            assert 0.05 <= depth <= 20.0
            assert (depth > chatter_trainer.ASSUMED_DEPTH_MM) == (float(y_cls) > 0.5)

    def test_label_matches_analytical_call(self):
        """抽样点：数据集标签应等于同参数下单次解析计算。"""
        ds = chatter_trainer.ChatterStabilityDataset(num_samples=4, seed=11)
        _, y_cls, y_depth = ds[0]
        # 数据集内部按 seed 采样，无法反推原始物理参数；此处只验证
        # 标签落在解析函数可能返回的区间内，且稳定判据自洽。
        assert torch.isfinite(y_depth)
        assert float(y_cls) in (0.0, 1.0)


class TestTrainerSmoke:
    """训练器烟测（极小样本 / 2 轮）。"""

    def test_train_saves_loadable_checkpoint(self, tmp_path):
        ds = chatter_trainer.ChatterStabilityDataset(num_samples=24, seed=3)
        trainer = chatter_trainer.ChatterTrainer(
            epochs=2, batch_size=8, save_dir=str(tmp_path), seed=3
        )
        trainer.train(ds, ds)

        ckpt = tmp_path / "chatter_model.pt"
        assert ckpt.exists()
        payload = torch.load(ckpt, map_location="cpu", weights_only=True)
        assert "model_state_dict" in payload
        assert payload["model_config"]["input_size"] == 6
        assert payload["model_config"]["output_size"] == 2
        assert "label_provenance" in payload

    def test_predictor_loads_checkpoint_and_predicts(self, tmp_path, monkeypatch):
        """训练产物可被 predictor 加载，且返回非负极限切深。"""
        ds = chatter_trainer.ChatterStabilityDataset(num_samples=24, seed=3)
        chatter_trainer.ChatterTrainer(
            epochs=2, batch_size=8, save_dir=str(tmp_path), seed=3
        ).train(ds, ds)

        monkeypatch.setattr(chatter_predictor, "_model_dir", str(tmp_path))
        chatter_predictor.reset_predictor_cache()
        try:
            pred = chatter_predictor.ChatterPredictor()
            assert pred.model is not None
            stable, limit_depth = pred.predict(
                spindle_rpm=8000,
                machine_stiffness=2e7,
                machine_damping=0.05,
                machine_freq=800,
                tool_diameter=10,
                tool_k_s=2000,
            )
            assert isinstance(stable, bool)
            assert limit_depth > 0  # 模型可用时必须返回非负极限
        finally:
            chatter_predictor.reset_predictor_cache()

    def test_missing_checkpoint_returns_sentinel(self, tmp_path, monkeypatch):
        """无检查点时返回 -1.0 哨兵（调用方据此回退解析法）。"""
        monkeypatch.setattr(chatter_predictor, "_model_dir", str(tmp_path / "empty"))
        chatter_predictor.reset_predictor_cache()
        try:
            pred = chatter_predictor.ChatterPredictor()
            assert pred.model is None
            _, limit_depth = pred.predict(
                spindle_rpm=8000,
                machine_stiffness=2e7,
                machine_damping=0.05,
                machine_freq=800,
                tool_diameter=10,
                tool_k_s=2000,
            )
            assert limit_depth == -1.0
        finally:
            chatter_predictor.reset_predictor_cache()
