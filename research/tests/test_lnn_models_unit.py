"""
LNN模型组件单元测试套件

8.1 模型测试规范
覆盖场景：
- 模型初始化测试
- 前向传播测试
- 隐藏状态初始化测试
- 梯度计算测试
- TorchScript导出测试
"""

import torch

from models.torch_base_lnn import LNNConfig
from models.torch_cfc_model import CFCModel
from models.torch_ltc_model import LTCModel
from models.torch_hybrid_lnn import HybridLNN


# 8.1.1 模型初始化测试


class TestLNNConfigInitialization:
    """测试LNNConfig配置参数的正确实例化"""

    def test_default_config(self):
        """测试默认配置参数"""
        config = LNNConfig(
            input_size=10,
            hidden_size=20,
            output_size=5,
        )
        assert config.input_size == 10
        assert config.hidden_size == 20
        assert config.output_size == 5
        assert config.num_layers == 1
        assert config.dropout == 0.0
        assert config.time_constant == 1.0

    def test_full_config(self):
        """测试完整配置参数"""
        config = LNNConfig(
            input_size=64,
            hidden_size=128,
            output_size=10,
            num_layers=3,
            dropout=0.2,
            time_constant=0.5,
        )
        assert config.num_layers == 3
        assert config.dropout == 0.2
        assert config.time_constant == 0.5

    def test_config_to_dict(self):
        """测试配置转字典"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        d = config.to_dict()
        assert isinstance(d, dict)
        assert d["input_size"] == 10
        assert d["hidden_size"] == 20
        assert d["output_size"] == 5
        assert d["num_layers"] == 1
        assert d["dropout"] == 0.0
        assert d["time_constant"] == 1.0

    def test_config_repr(self):
        """测试配置字符串表示"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        repr_str = repr(config)
        assert "LNNConfig" in repr_str
        assert "input_size" in repr_str


class TestCFCModelInitialization:
    """测试CFC模型初始化"""

    def test_cfc_basic_initialization(self):
        """测试CFC模型基本初始化"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        assert model is not None
        assert model.cfc_layer is not None
        assert model.output_layer is not None
        assert model.cfc_layer.input_size == 10
        assert model.cfc_layer.hidden_size == 20

    def test_cfc_with_dropout(self):
        """测试CFC模型带dropout初始化"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, dropout=0.3)
        model = CFCModel(config)
        assert not isinstance(model.dropout, torch.nn.Identity)

    def test_cfc_without_dropout(self):
        """测试CFC模型不带dropout初始化"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, dropout=0.0)
        model = CFCModel(config)
        assert isinstance(model.dropout, torch.nn.Identity)

    def test_cfc_weight_initialization(self):
        """测试CFC模型权重初始化非零"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        for module in model.cfc_layer.backbone:
            if isinstance(module, torch.nn.Linear):
                assert not torch.all(module.weight == 0)
                assert torch.all(module.bias == 0)

    def test_cfc_device_attribute(self):
        """测试CFC模型设备属性"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        device = model.device
        assert isinstance(device, torch.device)
        assert device.type == "cpu"


class TestLTCModelInitialization:
    """测试LTC模型初始化"""

    def test_ltc_basic_initialization(self):
        """测试LTC模型基本初始化"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=2)
        model = LTCModel(config)
        assert model is not None
        assert len(model.ltc_cells) == 2

    def test_ltc_single_layer(self):
        """测试LTC模型单层初始化"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=1)
        model = LTCModel(config)
        assert len(model.ltc_cells) == 1

    def test_ltc_tau_initialization(self):
        """测试LTC模型tau参数初始化为1"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = LTCModel(config)
        for cell in model.ltc_cells:
            assert torch.all(cell.tau == 1.0)

    def test_ltc_weight_initialization(self):
        """测试LTC模型权重初始化"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = LTCModel(config)
        for cell in model.ltc_cells:
            assert not torch.all(cell.W == 0)
            assert not torch.all(cell.U == 0)
            assert torch.all(cell.bias == 0)


class TestHybridLNNInitialization:
    """测试HybridLNN模型初始化"""

    def test_hybrid_basic_initialization(self):
        """测试HybridLNN基本初始化"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        assert model is not None
        assert model.cnn is not None
        assert len(model.ltc_cells) > 0
        assert model.output_layer is not None

    def test_hybrid_cnn_has_multiple_layers(self):
        """测试HybridLNN CNN包含多层卷积"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        conv_count = sum(1 for m in model.cnn if isinstance(m, torch.nn.Conv1d))
        assert conv_count >= 3

    def test_hybrid_cnn_has_batchnorm_relu(self):
        """测试HybridLNN CNN包含BatchNorm和ReLU"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        has_batchnorm = any(isinstance(m, torch.nn.BatchNorm1d) for m in model.cnn)
        has_relu = any(isinstance(m, torch.nn.ReLU) for m in model.cnn)
        assert has_batchnorm
        assert has_relu

    def test_hybrid_output_layer_has_multiple_fc(self):
        """测试HybridLNN输出层包含至少2个全连接层"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        linear_count = sum(1 for m in model.output_layer if isinstance(m, torch.nn.Linear))
        assert linear_count >= 2


# 8.1.2 前向传播测试


class TestCFCForwardPropagation:
    """测试CFC模型前向传播"""

    def setup_method(self):
        self.config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        self.model = CFCModel(self.config)

    def test_single_sample_output_shape(self):
        """测试单样本输入输出形状"""
        x = torch.randn(1, 10)
        output, hidden = self.model(x, dt=0.1)
        assert output.shape == (1, 5)
        assert hidden.shape == (1, 20)

    def test_single_sample_output_dtype(self):
        """测试单样本输入输出数据类型"""
        x = torch.randn(1, 10)
        output, hidden = self.model(x, dt=0.1)
        assert output.dtype == torch.float32
        assert hidden.dtype == torch.float32

    def test_batch_input_output_shape(self):
        """测试批量输入输出形状"""
        x = torch.randn(32, 10)
        output, hidden = self.model(x, dt=0.1)
        assert output.shape == (32, 5)
        assert hidden.shape == (32, 20)

    def test_batch_dimension_consistency(self):
        """测试批量输入维度一致性"""
        for batch_size in [1, 8, 16, 64, 128]:
            x = torch.randn(batch_size, 10)
            output, hidden = self.model(x, dt=0.1)
            assert output.shape[0] == batch_size
            assert hidden.shape[0] == batch_size

    def test_sequence_input_output_shape(self):
        """测试序列输入输出形状"""
        x = torch.randn(16, 8, 10)
        output, hidden = self.model(x, dt=0.1)
        assert output.shape == (16, 8, 5)
        assert hidden.shape == (16, 20)

    def test_forward_with_provided_hidden_state(self):
        """测试带预定义隐藏状态的前向传播"""
        x = torch.randn(8, 10)
        h = torch.zeros(8, 20)
        output, hidden = self.model(x, dt=0.1, hidden_state=h)
        assert output.shape == (8, 5)
        assert hidden.shape == (8, 20)

    def test_forward_auto_init_hidden(self):
        """测试自动初始化隐藏状态"""
        x = torch.randn(8, 10)
        output, hidden = self.model(x, dt=0.1)
        assert hidden is not None
        assert hidden.shape == (8, 20)

    def test_forward_output_is_finite(self):
        """测试输出值有限（无NaN/Inf）"""
        x = torch.randn(16, 10)
        output, hidden = self.model(x, dt=0.1)
        assert not torch.isnan(output).any()
        assert not torch.isinf(output).any()

    def test_forward_changes_hidden_state(self):
        """测试前向传播更新隐藏状态"""
        x = torch.randn(8, 10)
        h_before = torch.zeros(8, 20)
        _, h_after = self.model(x, dt=0.1, hidden_state=h_before)
        assert not torch.all(h_before == h_after)

    def test_cfc_predict(self):
        """Test CFC prediction"""
        x = torch.randn(5, 10)
        result, _ = self.model(x, dt=0.1)

        assert result is not None
        assert result.shape[0] == 5
        assert result.shape[1] == 5


class TestLTCForwardPropagation:
    """测试LTC模型前向传播"""

    def setup_method(self):
        self.config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=2)
        self.model = LTCModel(self.config)

    def test_single_sample_output_shape(self):
        """测试单样本输入输出形状"""
        x = torch.randn(1, 10)
        output, hidden = self.model(x, dt=0.1)
        assert output.shape == (1, 5)
        assert hidden.shape == (2, 1, 20)

    def test_batch_input_output_shape(self):
        """测试批量输入输出形状"""
        x = torch.randn(16, 10)
        output, hidden = self.model(x, dt=0.1)
        assert output.shape == (16, 5)
        assert hidden.shape == (2, 16, 20)

    def test_batch_dimension_consistency(self):
        """测试批量维度一致性"""
        for batch_size in [1, 4, 32, 64]:
            x = torch.randn(batch_size, 10)
            output, hidden = self.model(x, dt=0.1)
            assert output.shape[0] == batch_size
            assert hidden.shape[1] == batch_size

    def test_sequence_input_output_shape(self):
        """测试序列输入输出形状"""
        x = torch.randn(8, 5, 10)
        output, hidden = self.model(x, dt=0.1)
        assert output.shape == (8, 5, 5)
        assert hidden.shape == (2, 8, 20)

    def test_forward_output_is_finite(self):
        """测试输出值有限"""
        x = torch.randn(16, 10)
        output, hidden = self.model(x, dt=0.1)
        assert not torch.isnan(output).any()
        assert not torch.isinf(output).any()


class TestHybridForwardPropagation:
    """测试HybridLNN模型前向传播"""

    def setup_method(self):
        self.config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        self.model = HybridLNN(self.config)

    def test_single_sample_output_shape(self):
        """测试单样本输入输出形状"""
        x = torch.randn(1, 8, 6)
        output, hidden = self.model(x, dt=0.1)
        assert output.shape == (1, 5)
        assert hidden.dim() == 3

    def test_batch_input_output_shape(self):
        """测试批量输入输出形状"""
        x = torch.randn(16, 8, 6)
        output, hidden = self.model(x, dt=0.1)
        assert output.shape == (16, 5)

    def test_sequence_input_output_shape(self):
        """测试序列输入输出形状"""
        x = torch.randn(8, 8, 6)
        output, hidden = self.model(x, dt=0.1)
        assert output.shape == (8, 5)

    def test_forward_output_is_finite(self):
        """测试输出值有限"""
        x = torch.randn(16, 8, 6)
        output, hidden = self.model(x, dt=0.1)
        assert not torch.isnan(output).any()
        assert not torch.isinf(output).any()


# 8.1.3 隐藏状态初始化测试


class TestHiddenStateInitialization:
    """测试隐藏状态初始化逻辑"""

    def test_cfc_init_hidden_shape(self):
        """测试CFC隐藏状态初始化形状"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        h = model.init_hidden(8)
        assert h.shape == (8, 20)

    def test_cfc_init_hidden_zeros(self):
        """测试CFC隐藏状态初始化为零"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        h = model.init_hidden(16)
        assert torch.all(h == 0)

    def test_cfc_init_hidden_dtype(self):
        """测试CFC隐藏状态数据类型"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        h = model.init_hidden(8)
        assert h.dtype == torch.float32

    def test_cfc_hidden_state_persistence_across_steps(self):
        """测试CFC隐藏状态在多步传播中的传递"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        x1 = torch.randn(8, 10)
        _, h1 = model(x1, dt=0.1)
        x2 = torch.randn(8, 10)
        _, h2 = model(x2, dt=0.1, hidden_state=h1)
        assert not torch.all(h1 == h2)

    def test_ltc_init_hidden_shape(self):
        """测试LTC隐藏状态初始化形状（包含层维度）"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=3)
        model = LTCModel(config)
        h = model.init_hidden(16)
        assert h.shape == (3, 16, 20)

    def test_ltc_init_hidden_zeros(self):
        """测试LTC隐藏状态初始化为零"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=2)
        model = LTCModel(config)
        h = model.init_hidden(8)
        assert torch.all(h == 0)

    def test_ltc_hidden_state_persistence_across_steps(self):
        """测试LTC隐藏状态在多步传播中的传递"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=2)
        model = LTCModel(config)
        x1 = torch.randn(8, 10)
        _, h1 = model(x1, dt=0.1)
        x2 = torch.randn(8, 10)
        _, h2 = model(x2, dt=0.1, hidden_state=h1)
        assert not torch.all(h1 == h2)

    def test_hybrid_init_hidden_shape(self):
        """测试HybridLNN隐藏状态初始化形状"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        h = model.init_hidden(10)
        assert h.dim() == 3
        assert h.shape[1] == 10
        assert h.shape[2] == 32

    def test_hybrid_init_hidden_zeros(self):
        """测试HybridLNN隐藏状态初始化为零"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        h = model.init_hidden(10)
        assert torch.all(h == 0)

    def test_hidden_state_reset_clears_state(self):
        """测试重置操作清除隐藏状态"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        x = torch.randn(4, 10)
        model(x, dt=0.1)
        assert model.hidden_state is not None
        model.reset()
        assert model.hidden_state is None

    def test_hidden_state_batch_size_mismatch_reinitializes(self):
        """测试批量大小不匹配时重新初始化隐藏状态"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        x1 = torch.randn(4, 10)
        model(x1, dt=0.1)
        assert model.hidden_state.shape[0] == 4
        x2 = torch.randn(8, 10)
        model(x2, dt=0.1)
        assert model.hidden_state.shape[0] == 8


# 8.1.4 梯度计算测试


class TestGradientComputation:
    """测试模型梯度计算正确性"""

    def test_cfc_parameters_require_grad(self):
        """测试CFC模型可训练参数需要梯度"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        trainable_params = [p for p in model.parameters() if p.requires_grad]
        assert len(trainable_params) > 0

    def test_cfc_gradient_computation(self):
        """测试CFC模型反向传播梯度计算"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        x = torch.randn(8, 10)
        output, hidden = model(x, dt=0.1)
        loss = output.sum()
        loss.backward()
        for name, param in model.named_parameters():
            if param.requires_grad:
                assert param.grad is not None, f"可训练参数 {name} 的梯度为None"

    def test_cfc_gradients_are_finite(self):
        """测试CFC模型梯度值有限"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        x = torch.randn(8, 10)
        output, hidden = model(x, dt=0.1)
        loss = output.sum()
        loss.backward()
        for name, param in model.named_parameters():
            if param.requires_grad and param.grad is not None:
                assert torch.isfinite(param.grad).all(), f"参数 {name} 的梯度包含NaN或Inf"

    def test_ltc_parameters_require_grad(self):
        """测试LTC模型参数需要梯度"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=2)
        model = LTCModel(config)
        for name, param in model.named_parameters():
            assert param.requires_grad, f"参数 {name} 不需要梯度"

    def test_ltc_gradient_computation(self):
        """测试LTC模型反向传播梯度计算"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=2)
        model = LTCModel(config)
        x = torch.randn(8, 10)
        output, hidden = model(x, dt=0.1)
        loss = output.sum()
        loss.backward()
        for name, param in model.named_parameters():
            assert param.grad is not None, f"参数 {name} 的梯度为None"

    def test_ltc_gradients_are_finite(self):
        """测试LTC模型梯度值有限"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=2)
        model = LTCModel(config)
        x = torch.randn(8, 10)
        output, hidden = model(x, dt=0.1)
        loss = output.sum()
        loss.backward()
        for name, param in model.named_parameters():
            assert torch.isfinite(param.grad).all(), f"参数 {name} 的梯度包含NaN或Inf"

    def test_hybrid_parameters_require_grad(self):
        """测试HybridLNN模型可训练参数需要梯度"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        trainable_params = [p for p in model.parameters() if p.requires_grad]
        assert len(trainable_params) > 0

    def test_hybrid_gradient_computation(self):
        """测试HybridLNN模型反向传播梯度计算"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        x = torch.randn(8, 8, 6)
        output, hidden = model(x, dt=0.1)
        loss = output.sum()
        loss.backward()
        for name, param in model.named_parameters():
            if param.requires_grad:
                assert param.grad is not None, f"可训练参数 {name} 的梯度为None"

    def test_hybrid_gradients_are_finite(self):
        """测试HybridLNN模型梯度值有限"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        x = torch.randn(8, 8, 6)
        output, hidden = model(x, dt=0.1)
        loss = output.sum()
        loss.backward()
        for name, param in model.named_parameters():
            if param.requires_grad and param.grad is not None:
                assert torch.isfinite(param.grad).all(), f"参数 {name} 的梯度包含NaN或Inf"

    def test_trainable_parameter_count(self):
        """测试模型可训练参数数量大于零"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        info = model.get_info()
        assert info["trainable_parameters"] > 0
        assert info["total_parameters"] > 0

    def test_gradient_computation_with_sequence_input(self):
        """测试序列输入的梯度计算"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        x = torch.randn(8, 5, 10, requires_grad=True)
        output, hidden = model(x, dt=0.1)
        loss = output.sum()
        loss.backward()
        assert x.grad is not None
        assert torch.isfinite(x.grad).all()


# 8.1.5 TorchScript导出测试


class TestTorchScriptExport:
    """测试模型TorchScript导出功能"""

    def test_cfc_torchscript_export(self):
        """测试CFC模型成功导出为TorchScript"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        example = torch.randn(4, 10)
        scripted = model.to_torchscript(example)
        assert isinstance(scripted, torch.jit.ScriptModule)

    def test_cfc_torchscript_forward(self):
        """测试TorchScript导出的CFC模型能够执行前向传播"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        example = torch.randn(4, 10)
        scripted = model.to_torchscript(example)
        h = model.init_hidden(4)
        dt = torch.tensor(0.1)
        output, hidden = scripted(example, dt, h)
        assert output.shape == (4, 5)

    def test_ltc_torchscript_export(self):
        """测试LTC模型成功导出为TorchScript"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=2)
        model = LTCModel(config)
        example = torch.randn(4, 10)
        scripted = model.to_torchscript(example)
        assert isinstance(scripted, torch.jit.ScriptModule)

    def test_ltc_torchscript_forward(self):
        """测试TorchScript导出的LTC模型能够执行前向传播"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=2)
        model = LTCModel(config)
        example = torch.randn(4, 10)
        scripted = model.to_torchscript(example)
        h = model.init_hidden(4)
        dt = torch.tensor(0.1)
        output, hidden = scripted(example, dt, h)
        assert output.shape == (4, 5)

    def test_hybrid_torchscript_export(self):
        """测试HybridLNN模型成功导出为TorchScript"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        example = torch.randn(4, 8, 6)
        scripted = model.to_torchscript(example)
        assert isinstance(scripted, torch.jit.ScriptModule)

    def test_hybrid_torchscript_forward(self):
        """测试TorchScript导出的HybridLNN模型能够执行前向传播"""
        config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        model = HybridLNN(config)
        example = torch.randn(4, 8, 6)
        scripted = model.to_torchscript(example)
        h = model.init_hidden(4)
        dt = torch.tensor(0.1)
        output, hidden = scripted(example, dt, h)
        assert output.shape == (4, 5)

    def test_torchscript_output_matches_original(self):
        """测试TorchScript导出模型与原模型输出一致"""
        config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        model = CFCModel(config)
        model.eval()
        example = torch.randn(4, 10)
        scripted = model.to_torchscript(example)
        h = model.init_hidden(4)
        dt = torch.tensor(0.1)
        scripted_output, _ = scripted(example, dt, h)
        model.hidden_state = None
        original_output, _ = model(example, dt=0.1)
        assert torch.allclose(original_output, scripted_output, atol=1e-5)


# 设备无关测试


class TestDeviceAgnostic:
    """测试模型在CPU上正常运行"""

    def test_all_models_on_cpu(self):
        """测试所有模型在CPU上正常运行"""
        device = torch.device("cpu")

        cfc_config = LNNConfig(input_size=10, hidden_size=20, output_size=5)
        cfc = CFCModel(cfc_config).to(device)
        x_cfc = torch.randn(3, 10, device=device)
        out_cfc, _ = cfc(x_cfc, dt=0.1)
        assert out_cfc.device == device

        ltc_config = LNNConfig(input_size=10, hidden_size=20, output_size=5, num_layers=2)
        ltc = LTCModel(ltc_config).to(device)
        x_ltc = torch.randn(3, 10, device=device)
        out_ltc, _ = ltc(x_ltc, dt=0.1)
        assert out_ltc.device == device

        hybrid_config = LNNConfig(input_size=6, hidden_size=32, output_size=5, num_layers=3)
        hybrid = HybridLNN(hybrid_config).to(device)
        x_hybrid = torch.randn(3, 8, 6, device=device)
        out_hybrid, _ = hybrid(x_hybrid, dt=0.1)
        assert out_hybrid.device == device
