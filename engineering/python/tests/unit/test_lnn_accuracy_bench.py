"""LNN 精度基准与权重状态透出测试.

随包权重存在时验证基准链路与健康状态；权重缺失（CI 未拉 LFS 等）
自动 skip/降级，不视为失败。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.benchmarks.accuracy.lnn_accuracy_bench import DEFAULT_GRID, run_benchmark

pytestmark = pytest.mark.unit

_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "lnn"
_HAS_TRAINED_WEIGHTS = (_MODEL_DIR / "cutting_force.npz").exists()


class TestAccuracyBench:
    def test_skipped_when_no_weights(self, tmp_path):
        result = run_benchmark(model_dir=tmp_path)
        assert result["status"] == "skipped"
        assert "weights_source" in result["reason"]

    def test_unknown_material_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="未登记编码"):
            run_benchmark(model_dir=tmp_path, material="unobtanium")

    @pytest.mark.skipif(not _HAS_TRAINED_WEIGHTS, reason="models/lnn/cutting_force.npz 不存在")
    def test_trained_benchmark_runs(self):
        result = run_benchmark()
        assert result["status"] == "trained"
        assert result["reference"] == "kienzle_analytic"
        assert result["sample_count"] == len(DEFAULT_GRID["rpm"]) * len(DEFAULT_GRID["feed"]) * len(
            DEFAULT_GRID["depth"]
        )
        metrics = result["metrics"]
        # 链路保真度门禁：训练数据即 Kienzle 响应面，插值网格上 R² 必须 ≥0.95
        assert metrics["r2"] >= 0.95, f"保真度劣化: {metrics}"
        assert np.isfinite(metrics["mae"]) and metrics["mae"] >= 0


class TestHealthWeightsStatus:
    def test_status_shape_without_weights(self, tmp_path, monkeypatch):
        from app.api.v1 import health

        monkeypatch.setattr(
            "app.services.model_registry_service._default_registry_model_dir",
            lambda: str(tmp_path),
        )
        status = health._get_lnn_weights_status()
        assert status["total"] == 4
        assert status["trained_count"] == 0
        assert all(m["weights_source"] == "random_init" for m in status["models"].values())

    @pytest.mark.skipif(not _HAS_TRAINED_WEIGHTS, reason="models/lnn/cutting_force.npz 不存在")
    def test_status_reports_trained_models(self):
        from app.api.v1 import health

        status = health._get_lnn_weights_status()
        assert status["trained_count"] >= 1
        assert status["models"]["cutting_force"]["weights_source"] == "trained"
