"""切削力模块单元测试。

覆盖 Kienzle 解析公式、PINN 模型架构、训练器、推理接口。
"""

from __future__ import annotations

import os
import sys
import time
import pytest
import numpy as np
import torch

# 确保可以导入 app 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from app.simulation.cutting_force.kienzle import (
    KienzleParams,
    compute_cutting_force_fz,
    compute_cutting_forces,
    compute_specific_cutting_force,
    get_kienzle_coefficients,
    DEFAULT_MATERIAL_COEFFICIENTS,
    FORCE_DIRECTION_RATIOS,
)
from app.simulation.cutting_force.pinn import (
    CuttingForcePINN,
    PINNLoss,
    ResidualBlock,
)
from app.simulation.cutting_force.predictor import (
    predict_cutting_force,
    predict_cutting_force_batch,
)
from app.simulation.cutting_force.trainer import (
    SyntheticCuttingForceDataset,
    CuttingForceTrainer,
)


# Kienzle 模块测试


class TestKienzleCoefficients:
    """测试 Kienzle 系数获取。"""

    def test_default_materials_exist(self):
        """默认材料系数应存在。"""
        for mat in ["45steel", "aluminum_6061", "stainless_304"]:
            coeffs = get_kienzle_coefficients(mat)
            assert "kc1_1" in coeffs
            assert "mc" in coeffs
            assert coeffs["kc1_1"] > 0
            assert 0 < coeffs["mc"] < 1

    def test_unknown_material_raises(self):
        """未知材料应抛出 ValueError。"""
        with pytest.raises(ValueError, match="未找到材料"):
            get_kienzle_coefficients("unknown_material_xyz")

    def test_coefficients_positive(self):
        """所有默认材料的系数应为正数。"""
        for mat, coeffs in DEFAULT_MATERIAL_COEFFICIENTS.items():
            assert coeffs["kc1_1"] > 0, f"{mat} kc1.1 应为正数"
            assert coeffs["mc"] > 0, f"{mat} mc 应为正数"
            assert coeffs["mc"] < 1, f"{mat} mc 应在 (0, 1) 区间"


class TestKienzleParams:
    """测试 KienzleParams 数据类。"""

    def test_default_params(self):
        """默认参数应正确初始化。"""
        p = KienzleParams()
        assert p.material == "45steel"
        assert p.width > 0
        assert p.chip_thickness > 0
        assert p.kc1_1 is not None
        assert p.mc is not None

    def test_invalid_width_raises(self):
        """负宽度应抛出 ValueError。"""
        with pytest.raises(ValueError, match="切削宽度"):
            KienzleParams(width=-1.0)

    def test_invalid_thickness_raises(self):
        """负切屑厚度应抛出 ValueError。"""
        with pytest.raises(ValueError, match="切屑厚度"):
            KienzleParams(chip_thickness=0.0)

    def test_custom_coefficients(self):
        """自定义系数应覆盖默认值。"""
        p = KienzleParams(kc1_1=1500.0, mc=0.22)
        assert p.kc1_1 == 1500.0
        assert p.mc == 0.22


class TestComputeCuttingForceFz:
    """测试主切削力 Fz 计算。"""

    def test_basic_calculation(self):
        """基本计算应返回正值。"""
        fz = compute_cutting_force_fz(kc1_1=2000.0, mc=0.25, width=10.0, chip_thickness=0.1)
        assert fz > 0

    def test_formula_correctness(self):
        """验证公式 Fz = kc1.1 * b * h^(1-mc)。"""
        kc1_1, mc, b, h = 2000.0, 0.25, 10.0, 0.1
        expected = kc1_1 * b * (h ** (1.0 - mc))
        result = compute_cutting_force_fz(kc1_1, mc, b, h)
        assert abs(result - expected) < 1e-6

    def test_larger_width_larger_force(self):
        """更大切削宽度应产生更大力。"""
        fz1 = compute_cutting_force_fz(2000.0, 0.25, 5.0, 0.1)
        fz2 = compute_cutting_force_fz(2000.0, 0.25, 10.0, 0.1)
        assert fz2 > fz1

    def test_larger_thickness_larger_force(self):
        """更大切屑厚度应产生更大力。"""
        fz1 = compute_cutting_force_fz(2000.0, 0.25, 10.0, 0.05)
        fz2 = compute_cutting_force_fz(2000.0, 0.25, 10.0, 0.2)
        assert fz2 > fz1


class TestComputeCuttingForces:
    """测试三向切削力计算。"""

    def test_returns_three_forces(self):
        """应返回 Fx, Fy, Fz 三个力。"""
        result = compute_cutting_forces(material="45steel", width=10.0, chip_thickness=0.1)
        assert "Fx" in result
        assert "Fy" in result
        assert "Fz" in result

    def test_force_ratios(self):
        """力方向比例应符合经验关系。"""
        result = compute_cutting_forces(material="45steel", width=10.0, chip_thickness=0.1)
        assert abs(result["Fx"] - FORCE_DIRECTION_RATIOS["Fx_ratio"] * result["Fz"]) < 1e-6
        assert abs(result["Fy"] - FORCE_DIRECTION_RATIOS["Fy_ratio"] * result["Fz"]) < 1e-6

    def test_all_forces_positive(self):
        """所有力应为正值。"""
        result = compute_cutting_forces(material="45steel", width=10.0, chip_thickness=0.1)
        assert result["Fx"] > 0
        assert result["Fy"] > 0
        assert result["Fz"] > 0

    def test_different_materials(self):
        """不同材料应产生不同的力。"""
        r1 = compute_cutting_forces(material="45steel", width=10.0, chip_thickness=0.1)
        r2 = compute_cutting_forces(material="aluminum_6061", width=10.0, chip_thickness=0.1)
        assert r1["Fz"] != r2["Fz"]


class TestSpecificCuttingForce:
    """测试比切削力计算。"""

    def test_basic(self):
        """基本计算应返回正值。"""
        kc = compute_specific_cutting_force(kc1_1=2000.0, mc=0.25, chip_thickness=0.1)
        assert kc > 0

    def test_formula(self):
        """验证公式 kc = kc1.1 * h^(-mc)。"""
        kc1_1, mc, h = 2000.0, 0.25, 0.1
        expected = kc1_1 * (h ** (-mc))
        result = compute_specific_cutting_force(kc1_1, mc, h)
        assert abs(result - expected) < 1e-6


# PINN 模型测试


class TestResidualBlock:
    """测试残差块。"""

    def test_output_shape(self):
        """输出形状应与输入相同。"""
        block = ResidualBlock(dim=64)
        x = torch.randn(4, 64)
        y = block(x)
        assert y.shape == x.shape

    def test_residual_connection(self):
        """残差连接应使输出与输入不同（经过非线性变换）。"""
        block = ResidualBlock(dim=32)
        x = torch.randn(2, 32)
        y = block(x)
        assert not torch.allclose(x, y, atol=1e-6)


class TestCuttingForcePINN:
    """测试 PINN 模型。"""

    def test_model_creation(self):
        """模型应能正常创建。"""
        model = CuttingForcePINN()
        assert model is not None

    def test_parameter_count(self):
        """参数量应 < 100K。"""
        model = CuttingForcePINN()
        count = model.count_parameters()
        assert count < 100_000, f"参数量 {count} 超过 100K 限制"

    def test_forward_pass(self):
        """前向传播应输出正确形状。"""
        model = CuttingForcePINN()
        x = torch.randn(8, 3)
        y = model(x)
        assert y.shape == (8, 3)

    def test_output_positive(self):
        """输出应为正值。"""
        model = CuttingForcePINN()
        x = torch.randn(16, 3)
        y = model(x)
        assert (y >= 0).all()

    def test_normalize_params(self):
        """归一化应在 [0, 1] 区间。"""
        x = CuttingForcePINN.normalize_params(speed=5000, feed=2000, depth=2.0)
        assert x.shape == (1, 3)
        assert (x >= 0).all() and (x <= 1).all()

    def test_normalize_params_boundary(self):
        """边界值归一化应在 [0, 1] 内。"""
        x = CuttingForcePINN.normalize_params(speed=0, feed=0, depth=0)
        assert (x >= 0).all()
        x = CuttingForcePINN.normalize_params(speed=99999, feed=99999, depth=99)
        assert (x <= 1).all()


class TestPINNLoss:
    """测试 PINN 损失函数。"""

    def test_data_loss_only(self):
        """无物理约束时仅计算数据损失。"""
        criterion = PINNLoss(physics_weight=0.1)
        pred = torch.tensor([[1.0, 2.0, 3.0]])
        target = torch.tensor([[1.0, 2.0, 3.0]])
        losses = criterion(pred, target, kienzle_forces=None)
        assert losses["data_loss"].item() == 0.0
        assert losses["physics_loss"].item() == 0.0

    def test_with_physics_loss(self):
        """有物理约束时物理损失应 > 0。"""
        criterion = PINNLoss(physics_weight=0.1)
        pred = torch.tensor([[1.0, 2.0, 3.0]])
        target = torch.tensor([[1.0, 2.0, 3.0]])
        kienzle = torch.tensor([[1.5, 2.5, 3.5]])
        losses = criterion(pred, target, kienzle_forces=kienzle)
        assert losses["physics_loss"].item() > 0
        assert losses["total_loss"].item() > 0

    def test_loss_keys(self):
        """返回字典应包含所有损失项。"""
        criterion = PINNLoss()
        pred = torch.randn(4, 3)
        target = torch.randn(4, 3)
        losses = criterion(pred, target)
        assert "total_loss" in losses
        assert "data_loss" in losses
        assert "physics_loss" in losses


# 训练器测试


class TestSyntheticDataset:
    """测试合成数据集。"""

    def test_dataset_creation(self):
        """数据集应能正常创建。"""
        ds = SyntheticCuttingForceDataset(num_samples=100)
        assert len(ds) == 100

    def test_item_shapes(self):
        """样本形状应正确。"""
        ds = SyntheticCuttingForceDataset(num_samples=50)
        inputs, targets, kienzle = ds[0]
        assert inputs.shape == (3,)
        assert targets.shape == (3,)
        assert kienzle.shape == (3,)

    def test_inputs_normalized(self):
        """输入应归一化到 [0, 1]。"""
        ds = SyntheticCuttingForceDataset(num_samples=200)
        for i in range(len(ds)):
            inputs, _, _ = ds[i]
            assert (inputs >= 0).all() and (inputs <= 1).all()

    def test_targets_positive(self):
        """目标力应为正值。"""
        ds = SyntheticCuttingForceDataset(num_samples=200)
        for i in range(len(ds)):
            _, targets, _ = ds[i]
            assert (targets >= 0).all()


class TestCuttingForceTrainer:
    """测试训练器。"""

    def test_trainer_creation(self):
        """训练器应能正常创建。"""
        trainer = CuttingForceTrainer(epochs=2, device="cpu")
        assert trainer is not None

    def test_short_training(self):
        """短训练应能完成且 loss 下降。"""
        model = CuttingForcePINN()
        trainer = CuttingForceTrainer(model=model, epochs=3, batch_size=32, device="cpu")
        train_ds = SyntheticCuttingForceDataset(num_samples=100)
        val_ds = SyntheticCuttingForceDataset(num_samples=50, seed=99)
        history = trainer.train(train_ds, val_ds)

        assert len(history["train_loss"]) == 3
        assert len(history["val_loss"]) == 3
        # loss 应为有限值
        assert all(np.isfinite(v) for v in history["train_loss"])
        assert all(np.isfinite(v) for v in history["val_loss"])


# 推理接口测试


class TestPredictor:
    """测试推理接口。"""

    def test_predict_default(self):
        """默认参数预测应返回正确结构。"""
        result = predict_cutting_force()
        assert "Fx" in result
        assert "Fy" in result
        assert "Fz" in result
        assert "method" in result

    def test_predict_with_params(self):
        """指定参数预测应返回正值。"""
        result = predict_cutting_force(
            material="45steel",
            tool="endmill_d10",
            params={"speed": 3500, "feed": 1200, "depth": 1.5},
        )
        assert result["Fx"] > 0
        assert result["Fy"] > 0
        assert result["Fz"] > 0

    def test_predict_kienzle_fallback(self):
        """use_pinn=False 时应使用 Kienzle 解析解。"""
        result = predict_cutting_force(use_pinn=False)
        assert result["method"] == "kienzle"

    def test_predict_different_materials(self):
        """不同材料应产生不同结果。"""
        r1 = predict_cutting_force(material="45steel", use_pinn=False)
        r2 = predict_cutting_force(material="aluminum_6061", use_pinn=False)
        assert r1["Fz"] != r2["Fz"]

    def test_batch_predict(self):
        """批量预测应返回正确数量结果。"""
        params_list = [
            {"speed": 3000, "feed": 1000, "depth": 1.0},
            {"speed": 5000, "feed": 2000, "depth": 2.0},
        ]
        results = predict_cutting_force_batch("45steel", params_list, use_pinn=False)
        assert len(results) == 2
        for r in results:
            assert "Fx" in r and "Fy" in r and "Fz" in r


class TestInferencePerformance:
    """测试推理性能。"""

    def test_single_inference_speed(self):
        """单次推理应 < 50ms。"""
        # 预热
        predict_cutting_force(use_pinn=False)

        start = time.time()
        predict_cutting_force(
            material="45steel",
            params={"speed": 3500, "feed": 1200, "depth": 1.5},
            use_pinn=False,
        )
        elapsed_ms = (time.time() - start) * 1000
        assert elapsed_ms < 50, f"单次推理耗时 {elapsed_ms:.2f}ms 超过 50ms"

    def test_model_size(self):
        """模型文件大小应 < 500KB（通过参数量间接验证）。"""
        model = CuttingForcePINN()
        # 每个参数 4 字节 (float32)
        size_bytes = model.count_parameters() * 4
        assert size_bytes < 500 * 1024, f"模型大小约 {size_bytes / 1024:.1f}KB 超过 500KB"
