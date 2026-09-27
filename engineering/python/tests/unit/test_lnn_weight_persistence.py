"""LNN NumPy 模型权重持久化（v2 npz）与随机初始化显式标记测试.

背景（2026-09 接线方案 WP1，docs/development/lnn-权重训练与分发接线方案.md）：
历史 save()/load() 只存元数据不存参数（H1），注册表声明 .pt 却用 np.load
解析（H3），权重文件缺失时静默保持随机初始化且上层无法辨识（H2）。

本套件锁定：
- v2 npz save→load 往返后预测输出逐元素一致（三类模型）；
- 维度/配置/is_trained 元数据随权重一并恢复；
- legacy v1 仅元数据文件兼容加载（不炸、显式告警、不得标记为 trained）；
- 参数数组损坏（缺配对/索引空洞）显式失败而非静默错位；
- 注册表加载路径的 weights_source 三态（trained / random_init / file_untrained）。
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.ai.lnn.inference._runtime_registry import ModelRegistry
from app.ai.lnn.core import ModelType
from app.ai.lnn.models.base_lnn import NPZ_FORMAT_VERSION, BaseLNNModel
from app.ai.lnn.models.cfc_model import CFCModel
from app.ai.lnn.models.hybrid_lnn import HybridLNNModel
from app.ai.lnn.models.ltc_model import LTCModel

pytestmark = pytest.mark.unit

_MODEL_CASES = [
    pytest.param(
        lambda: LTCModel(
            model_name="t_ltc",
            input_dim=5,
            output_dim=2,
            hidden_dim=8,
            memory_size=16,
            num_layers=2,
        ),
        id="ltc",
    ),
    pytest.param(
        lambda: CFCModel(model_name="t_cfc", input_dim=5, output_dim=2, hidden_dim=8, num_layers=2),
        id="cfc",
    ),
    pytest.param(
        lambda: HybridLNNModel(
            model_name="t_hybrid",
            input_dim=5,
            output_dim=2,
            cnn_filters=[4, 8],
            cnn_kernel_sizes=[3, 3],
            lnn_hidden_dim=8,
            lnn_num_layers=1,
        ),
        id="hybrid",
    ),
]


def _build_and_predict(model: BaseLNNModel, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((4, 5))
    np.random.seed(seed)  # build 内部用全局 np.random 初始化
    model.build()
    return x, model.predict(x)


class TestWeightPersistenceRoundtrip:
    """v2 npz save→load 往返：预测输出逐元素一致。"""

    @pytest.mark.parametrize("factory", _MODEL_CASES)
    def test_roundtrip_predictions_identical(self, tmp_path, factory):
        model = factory()
        x, y1 = _build_and_predict(model)

        path = tmp_path / "model.npz"
        model.save(str(path))

        restored = factory()
        # 故意用不同全局种子构建（随机初始化不同），load 后必须完全覆盖
        np.random.seed(7)
        restored.build()
        restored.load(str(path))

        y2 = restored.predict(x)
        np.testing.assert_allclose(y1, y2, rtol=0, atol=0)

    @pytest.mark.parametrize("factory", _MODEL_CASES)
    def test_roundtrip_metadata(self, tmp_path, factory):
        model = factory()
        model.is_trained = True
        path = tmp_path / "model.npz"
        model.save(str(path))

        restored = factory()
        restored.load(str(path))
        assert restored.is_trained is True
        assert restored.model_name == model.model_name
        assert restored.input_dim == 5
        assert restored.output_dim == 2

    def test_config_json_roundtrip(self, tmp_path):
        model = CFCModel(model_name="t", input_dim=5, output_dim=1, custom_alpha=0.5, tag="a")
        np.random.seed(0)
        model.build()
        path = tmp_path / "m.npz"
        model.save(str(path))

        restored = CFCModel(model_name="t", input_dim=5, output_dim=1)
        restored.load(str(path))
        assert restored.config.get("custom_alpha") == 0.5
        assert restored.config.get("tag") == "a"

    def test_format_version_written(self, tmp_path):
        model = CFCModel(model_name="t", input_dim=5, output_dim=1)
        np.random.seed(0)
        model.build()
        path = tmp_path / "m.npz"
        model.save(str(path))
        with np.load(path) as data:
            assert int(data["format_version"]) == NPZ_FORMAT_VERSION
            assert any(k.startswith("param.") for k in data.files)

    def test_load_overrides_mismatched_constructor_dims(self, tmp_path):
        """先以错误维度构建，load 后维度以文件为准且可正常预测。"""
        model = CFCModel(model_name="t", input_dim=5, output_dim=1, hidden_dim=8, num_layers=1)
        np.random.seed(3)
        model.build()
        path = tmp_path / "m.npz"
        model.save(str(path))

        wrong = CFCModel(model_name="t", input_dim=9, output_dim=3, hidden_dim=8, num_layers=1)
        np.random.seed(4)
        wrong.build()
        wrong.load(str(path))
        assert wrong.input_dim == 5
        assert wrong.output_dim == 1
        y = wrong.predict(np.zeros((2, 5)))
        assert y.shape == (2, 1)


class TestLegacyAndCorruptWeights:
    """v1 仅元数据文件兼容加载；损坏参数数组显式失败。"""

    def test_v1_metadata_only_file_loads_with_warning(self, tmp_path, caplog):
        path = tmp_path / "v1.npz"
        np.savez(
            path,
            model_name="legacy",
            input_dim=5,
            output_dim=1,
            is_trained=False,
        )
        model = CFCModel(model_name="t", input_dim=5, output_dim=1)
        np.random.seed(0)
        model.build()
        with caplog.at_level("WARNING"):
            model.load(str(path))
        assert model.model_name == "legacy"
        assert any("v1" in r.message for r in caplog.records)

    def test_missing_bias_array_raises(self, tmp_path):
        path = tmp_path / "broken.npz"
        np.savez(path, **{"param.weights.0": np.zeros((5, 8)), "param.weights.1": np.zeros((8, 1))})
        model = CFCModel(model_name="t", input_dim=5, output_dim=1)
        with pytest.raises(ValueError, match="层数"):
            model.load(str(path))

    def test_index_gap_raises(self, tmp_path):
        path = tmp_path / "gap.npz"
        np.savez(
            path,
            **{
                "param.weights.0": np.zeros((5, 8)),
                "param.weights.2": np.zeros((8, 1)),
                "param.biases.0": np.zeros(8),
                "param.biases.2": np.zeros(1),
            },
        )
        model = CFCModel(model_name="t", input_dim=5, output_dim=1)
        with pytest.raises(ValueError, match="索引不连续"):
            model.load(str(path))

    def test_base_class_rejects_arrays_without_override(self, tmp_path):
        """未实现 load_state_arrays 的子类收到参数数组必须显式失败。"""

        class _Bare(BaseLNNModel):
            def build(self) -> None: ...

            def forward(self, x):
                return x

            def predict(self, x):
                return x

            def _train_step(self, data, labels, batch_size, learning_rate):
                return 0.0

            def _validate(self, val_data, val_labels):
                return 0.0

        path = tmp_path / "bare.npz"
        np.savez(path, **{"param.weights.0": np.zeros((1, 1))})
        with pytest.raises(ValueError, match="load_state_arrays"):
            _Bare(model_name="b", input_dim=1, output_dim=1).load(str(path))


class TestOnnxAdapterContract:
    """OnnxLNNModel 最小契约（无 onnx 工件环境下的行为）。"""

    def test_predict_before_load_raises(self):
        from app.ai.lnn.models.onnx_runtime_model import OnnxLNNModel

        model = OnnxLNNModel(model_name="t", input_dim=4, output_dim=1)
        with pytest.raises(RuntimeError, match="尚未加载"):
            model.predict(np.zeros((1, 4)))

    def test_save_not_supported(self):
        from app.ai.lnn.models.onnx_runtime_model import OnnxLNNModel

        with pytest.raises(NotImplementedError):
            OnnxLNNModel(model_name="t").save("x.onnx")

    def test_config_json_survives_json_roundtrip_in_npz(self, tmp_path):
        """config 中含非 JSON 原生类型时 save 不炸（default=str 兜底）。"""
        model = CFCModel(model_name="t", input_dim=5, output_dim=1, weird=(1, 2))
        np.random.seed(0)
        model.build()
        path = tmp_path / "m.npz"
        model.save(str(path))
        with np.load(path) as data:
            json.loads(str(data["config_json"]))  # 可解析


class TestRuntimeRegistryWeightsSource:
    """ModelRegistry._load_model 的 weights_source 三态标记。"""

    def _register_and_load(self, tmp_path: object, model_path: str | None):
        registry = ModelRegistry()
        registry.register(
            "m1",
            ModelType.CFC,
            model_path=model_path,
            config={"input_dim": 4, "output_dim": 1},
        )
        registry.get("m1", load_if_needed=True)
        return registry

    def test_trained(self, tmp_path):
        model = CFCModel(model_name="m1", input_dim=4, output_dim=1, hidden_dim=8, num_layers=2)
        np.random.seed(0)
        model.build()
        model.is_trained = True
        path = str(tmp_path / "m1.npz")
        model.save(path)
        registry = self._register_and_load(tmp_path, path)
        assert registry.registry["m1"].metadata["weights_source"] == "trained"

    def test_file_untrained(self, tmp_path):
        # v1 仅元数据文件：存在但 is_trained=False → 不得视为 trained
        path = str(tmp_path / "m1.npz")
        np.savez(path, model_name="m1", input_dim=4, output_dim=1, is_trained=False)
        registry = self._register_and_load(tmp_path, path)
        assert registry.registry["m1"].metadata["weights_source"] == "file_untrained"

    def test_random_init_when_missing(self, tmp_path):
        registry = self._register_and_load(tmp_path, str(tmp_path / "not_exist.npz"))
        assert registry.registry["m1"].metadata["weights_source"] == "random_init"

    def test_info_and_list_expose_weights_source(self, tmp_path):
        registry = self._register_and_load(tmp_path, str(tmp_path / "not_exist.npz"))
        assert registry.get_model_info("m1")["weights_source"] == "random_init"
        listing = registry.list_models()
        assert listing[0]["weights_source"] == "random_init"


class TestLNNModelRegistryValidation:
    """LNNModelRegistry.validate_model 的 weights_source 判定。"""

    def _registry(self, model_dir: str):
        from app.ai.lnn.inference._lnn_registry import LNNModelRegistry

        return LNNModelRegistry(model_dir=model_dir)

    def test_trained_npz(self, tmp_path):
        model = CFCModel(model_name="cutting_force", input_dim=4, output_dim=1, hidden_dim=8, num_layers=2)
        np.random.seed(0)
        model.build()
        model.is_trained = True
        model.save(str(tmp_path / "cutting_force.npz"))
        result = self._registry(str(tmp_path)).validate_model("cutting_force")
        assert result["valid"] is True
        assert result["weights_source"] == "trained"

    def test_missing_file(self, tmp_path):
        result = self._registry(str(tmp_path)).validate_model("cutting_force")
        assert result["valid"] is False
        assert result["weights_source"] == "random_init"

    def test_v1_file_untrained(self, tmp_path):
        np.savez(
            tmp_path / "cutting_force.npz",
            model_name="cutting_force",
            input_dim=4,
            output_dim=1,
            is_trained=False,
        )
        result = self._registry(str(tmp_path)).validate_model("cutting_force")
        assert result["valid"] is True
        assert result["weights_source"] == "file_untrained"


class TestPredefinedModelPaths:
    """预定义模型路径口径：统一 models/lnn/*.npz。"""

    def test_all_paths_are_npz_under_models_lnn(self):
        from app.ai.lnn.inference._lnn_registry import LNNModelRegistry

        for name in LNNModelRegistry.PREDEFINED_MODELS:
            info = LNNModelRegistry.PREDEFINED_MODELS[name]
            assert info.model_path.startswith("models/lnn/"), name
            assert info.model_path.endswith(".npz"), name
