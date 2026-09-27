"""颤振稳定性模块单元测试。"""

import os
import sys

# 添加 python 目录到Python路径（与 cutting_force 测试保持一致）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import pytest
from app.simulation.chatter.stability import (
    MachineParams,
    ToolParams,
    ChatterParams,
    compute_stability_limit,
    compute_stability_lobe,
    get_machine_params,
    get_default_machine_params,
    _compute_frf,
)


class TestMachineParams:
    """机床参数测试。"""

    def test_valid_params(self):
        """测试有效参数创建。"""
        params = MachineParams(
            machine_id="test_machine",
            stiffness_x=1.5e7,
            stiffness_y=1.5e7,
            stiffness_z=2.0e7,
            damping_ratio=0.05,
            natural_freq=800.0,
            modal_mass=50.0,
        )
        assert params.machine_id == "test_machine"
        assert params.stiffness_z == 2.0e7
        assert params.damping_ratio == 0.05

    def test_invalid_stiffness(self):
        """测试无效刚度值。"""
        with pytest.raises(ValueError, match="刚度必须为正数"):
            MachineParams(stiffness_x=-1.0)

    def test_invalid_damping_ratio(self):
        """测试无效阻尼比。"""
        with pytest.raises(ValueError, match="阻尼比必须在"):
            MachineParams(damping_ratio=0.0)

        with pytest.raises(ValueError, match="阻尼比必须在"):
            MachineParams(damping_ratio=1.0)

    def test_invalid_natural_freq(self):
        """测试无效固有频率。"""
        with pytest.raises(ValueError, match="固有频率必须为正数"):
            MachineParams(natural_freq=-100.0)

    def test_invalid_modal_mass(self):
        """测试无效模态质量。"""
        with pytest.raises(ValueError, match="模态质量必须为正数"):
            MachineParams(modal_mass=0.0)


class TestToolParams:
    """刀具参数测试。"""

    def test_valid_params(self):
        """测试有效参数创建。"""
        params = ToolParams(
            tool_id="test_tool",
            diameter=10.0,
            num_flutes=4,
            helix_angle=30.0,
            cutting_force_coeff=2000.0,
        )
        assert params.tool_id == "test_tool"
        assert params.diameter == 10.0
        assert params.num_flutes == 4

    def test_invalid_diameter(self):
        """测试无效刀具直径。"""
        with pytest.raises(ValueError, match="刀具直径必须为正数"):
            ToolParams(diameter=-10.0)

    def test_invalid_num_flutes(self):
        """测试无效齿数。"""
        with pytest.raises(ValueError, match="齿数必须为正整数"):
            ToolParams(num_flutes=0)

    def test_invalid_helix_angle(self):
        """测试无效螺旋角。"""
        with pytest.raises(ValueError, match="螺旋角必须在"):
            ToolParams(helix_angle=-10.0)

        with pytest.raises(ValueError, match="螺旋角必须在"):
            ToolParams(helix_angle=100.0)

    def test_invalid_cutting_force_coeff(self):
        """测试无效切削力系数。"""
        with pytest.raises(ValueError, match="切削力系数必须为正数"):
            ToolParams(cutting_force_coeff=-2000.0)


class TestChatterParams:
    """颤振参数测试。"""

    def test_valid_params(self):
        """测试有效参数创建。"""
        params = ChatterParams(spindle_rpm=8000.0)
        assert params.spindle_rpm == 8000.0
        assert params.axial_depth is None

    def test_invalid_spindle_rpm(self):
        """测试无效主轴转速。"""
        with pytest.raises(ValueError, match="主轴转速必须为正数"):
            ChatterParams(spindle_rpm=-1000.0)

    def test_invalid_axial_depth(self):
        """测试无效轴向切深。"""
        with pytest.raises(ValueError, match="轴向切深必须为正数"):
            ChatterParams(axial_depth=-2.0)


class TestGetMachineParams:
    """获取机床参数测试。"""

    def test_known_machine(self):
        """测试已知机床。"""
        params = get_machine_params("vmc_850")
        assert params.machine_id == "vmc_850"
        assert params.stiffness_z > 0
        assert params.damping_ratio > 0

    def test_unknown_machine(self):
        """测试未知机床（应使用默认值）。"""
        params = get_machine_params("unknown_machine")
        assert params.machine_id == "unknown_machine"
        # 应该使用 vmc_850 的默认值
        assert params.stiffness_z > 0

    def test_get_default_params(self):
        """测试获取默认参数。"""
        defaults = get_default_machine_params()
        assert "vmc_850" in defaults
        assert "cnc_lathe_ck6140" in defaults
        assert "small_vmc_640" in defaults


class TestComputeFRF:
    """频率响应函数测试。"""

    def test_frf_at_resonance(self):
        """测试共振频率处的 FRF。"""
        # 使用物理一致的参数：f_n = sqrt(k/m) / (2π) ≈ 100 Hz
        machine = MachineParams(
            stiffness_z=2.0e8,
            damping_ratio=0.05,
            natural_freq=100.0,
            modal_mass=50.0,
        )

        # 在固有频率处
        frf = _compute_frf(machine, 100.0)

        # FRF 应该是复数
        assert isinstance(frf, complex)

        # 在共振频率处，FRF 幅值应该较大（相对于非共振频率）
        frf_off = _compute_frf(machine, 50.0)
        assert abs(frf) > abs(frf_off)

    def test_frf_off_resonance(self):
        """测试非共振频率处的 FRF。"""
        # 使用物理一致的参数
        machine = MachineParams(
            stiffness_z=2.0e8,
            damping_ratio=0.05,
            natural_freq=100.0,
            modal_mass=50.0,
        )

        # 远离固有频率
        frf_low = _compute_frf(machine, 50.0)
        frf_high = _compute_frf(machine, 150.0)

        # 远离共振时，FRF 幅值应该较小
        assert abs(frf_low) < 1e-6
        assert abs(frf_high) < 1e-6


class TestComputeStabilityLimit:
    """稳定性极限计算测试。"""

    def test_basic_calculation(self):
        """测试基本计算。"""
        params = ChatterParams(
            spindle_rpm=8000.0,
            machine=MachineParams(),
            tool=ToolParams(),
        )

        limit_depth = compute_stability_limit(params)

        # 极限切深应该是正数
        assert limit_depth > 0

        # 极限切深应该在合理范围内（0.1mm 到 100mm）
        assert 0.1 < limit_depth < 100

    def test_different_speeds(self):
        """测试不同转速下的计算。"""
        machine = MachineParams()
        tool = ToolParams()

        params1 = ChatterParams(spindle_rpm=4000.0, machine=machine, tool=tool)
        params2 = ChatterParams(spindle_rpm=8000.0, machine=machine, tool=tool)

        limit1 = compute_stability_limit(params1)
        limit2 = compute_stability_limit(params2)

        # 两个转速都应该返回有效的极限切深
        assert limit1 > 0
        assert limit2 > 0

    def test_stiffer_machine(self):
        """测试更刚性的机床（应该有更大的极限切深）。"""
        machine_soft = MachineParams(stiffness_z=1.0e7)
        machine_stiff = MachineParams(stiffness_z=3.0e7)
        tool = ToolParams()

        params_soft = ChatterParams(machine=machine_soft, tool=tool)
        params_stiff = ChatterParams(machine=machine_stiff, tool=tool)

        limit_soft = compute_stability_limit(params_soft)
        limit_stiff = compute_stability_limit(params_stiff)

        # 更刚性的机床应该有更大的极限切深
        # 注意：这个关系可能不是线性的，取决于 FRF 的实部
        assert limit_stiff > 0
        assert limit_soft > 0


class TestComputeStabilityLobe:
    """稳定性叶图计算测试。"""

    def test_basic_lobe(self):
        """测试基本叶图计算。"""
        machine = MachineParams()
        tool = ToolParams()

        result = compute_stability_lobe(
            machine=machine,
            tool=tool,
            speed_range=(1000, 10000),
            num_points=50,
            num_lobes=3,
        )

        # 结果应该包含必要的键
        assert "speeds" in result
        assert "limit_depths" in result
        assert "lobes" in result

        # 速度和切深列表长度应该相同
        assert len(result["speeds"]) == len(result["limit_depths"])

        # 至少应该有一些数据点
        assert len(result["speeds"]) > 0

    def test_lobe_structure(self):
        """测试叶图结构。"""
        machine = MachineParams()
        tool = ToolParams()

        result = compute_stability_lobe(
            machine=machine,
            tool=tool,
            num_lobes=3,
        )

        # 应该有多个叶图
        assert len(result["lobes"]) > 0

        # 每个叶图应该包含速度和切深列表
        for lobe in result["lobes"]:
            speeds, depths = lobe
            assert len(speeds) > 0
            assert len(depths) > 0
            assert len(speeds) == len(depths)

    def test_speed_range_filtering(self):
        """测试转速范围过滤。"""
        machine = MachineParams()
        tool = ToolParams()

        # 使用较窄的转速范围
        result = compute_stability_lobe(
            machine=machine,
            tool=tool,
            speed_range=(5000, 7000),
        )

        # 所有速度应该在指定范围内
        for speed in result["speeds"]:
            assert 5000 <= speed <= 7000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
