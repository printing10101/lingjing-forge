"""
Comprehensive Unit Tests for PyTorch-based LNN System

Tests cover all core PyTorch modules: BaseLNN, CFC, LTC, and Hybrid models.
"""

import unittest
import torch
import time
import os
import sys

# Add parent directory to path for imports
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
)

from models.torch_base_lnn import BaseLNN, LNNConfig  # noqa: E402
from models.torch_cfc_model import CFCModel as TorchCFCModel, CFCLayer  # noqa: E402
from models.torch_ltc_model import LTCModel as TorchLTCModel, LTCCell  # noqa: E402
from models.torch_hybrid_lnn import HybridLNN as TorchHybridLNN  # noqa: E402


class TestLNNConfig(unittest.TestCase):
    """Test LNNConfig class"""

    def test_config_creation(self):
        config = LNNConfig(
            input_size=10,
            hidden_size=20,
            output_size=5,
            num_layers=2,
            dropout=0.1,
            time_constant=0.5,
        )
        self.assertEqual(config.input_size, 10)
        self.assertEqual(config.hidden_size, 20)
        self.assertEqual(config.output_size, 5)
        self.assertEqual(config.num_layers, 2)
        self.assertEqual(config.dropout, 0.1)
        self.assertEqual(config.time_constant, 0.5)

    def test_config_to_dict(self):
        config = LNNConfig(
            input_size=10,
            hidden_size=20,
            output_size=5,
        )
        d = config.to_dict()
        self.assertIn("input_size", d)
        self.assertIn("hidden_size", d)
        self.assertIn("output_size", d)
        self.assertEqual(d["input_size"], 10)

    def test_config_repr(self):
        config = LNNConfig(
            input_size=10,
            hidden_size=20,
            output_size=5,
        )
        repr_str = repr(config)
        self.assertIn("LNNConfig", repr_str)


class TestBaseLNN(unittest.TestCase):
    """Test BaseLNN abstract base class"""

    def test_cannot_instantiate_abstract_class(self):
        with self.assertRaises(TypeError):
            BaseLNN(LNNConfig(10, 20, 5))

    def test_concrete_implementation_works(self):
        class ConcreteLNN(BaseLNN):
            def forward(self, x, dt, hidden_state):
                return x, hidden_state

            def init_hidden(self, batch_size):
                return torch.zeros(batch_size, 10)

        config = LNNConfig(10, 20, 5)
        model = ConcreteLNN(config)
        x = torch.randn(3, 10)
        h = model.init_hidden(3)
        out, h_new = model(x, 0.1, h)
        self.assertEqual(out.shape, (3, 10))

    def test_get_info(self):
        class ConcreteLNN(BaseLNN):
            def __init__(self, config):
                super().__init__(config)
                self.linear = torch.nn.Linear(10, 5)
                self.device = torch.device("cpu")

            def forward(self, x, dt, hidden_state):
                return self.linear(x), hidden_state

            def init_hidden(self, batch_size):
                return torch.zeros(batch_size, 10)

        config = LNNConfig(10, 20, 5)
        model = ConcreteLNN(config)
        info = model.get_info()
        self.assertIn("total_parameters", info)
        self.assertIn("trainable_parameters", info)
        self.assertIn("device", info)
        self.assertIn("config", info)
        self.assertGreater(info["total_parameters"], 0)

    def test_to_torchscript(self):
        class ConcreteLNN(BaseLNN):
            def __init__(self, config):
                super().__init__(config)
                self.linear = torch.nn.Linear(10, 5)
                self.device = torch.device("cpu")

            def forward(self, x, dt, hidden_state):
                return self.linear(x), hidden_state

            def init_hidden(self, batch_size):
                return torch.zeros(batch_size, 10)

        config = LNNConfig(10, 20, 5)
        model = ConcreteLNN(config)
        example = torch.randn(3, 10)
        scripted = model.to_torchscript(example)
        self.assertIsInstance(scripted, torch.jit.ScriptModule)


class TestCFCLayer(unittest.TestCase):
    """Test CFCLayer"""

    def test_cfc_layer_forward(self):
        layer = CFCLayer(input_size=10, hidden_size=20)
        x = torch.randn(5, 10)
        h = torch.zeros(5, 20)
        h_new = layer(x, h)
        self.assertEqual(h_new.shape, (5, 20))
        self.assertFalse(torch.all(h_new == h))

    def test_cfc_layer_with_custom_dt(self):
        layer = CFCLayer(input_size=10, hidden_size=20, dt=0.5)
        x = torch.randn(5, 10)
        h = torch.zeros(5, 20)
        h_new = layer(x, h, dt=0.2)
        self.assertEqual(h_new.shape, (5, 20))

    def test_cfc_layer_weights_initialized(self):
        layer = CFCLayer(input_size=10, hidden_size=20)
        for module in layer.backbone:
            if isinstance(module, torch.nn.Linear):
                self.assertFalse(torch.all(module.weight == 0))


class TestTorchCFCModel(unittest.TestCase):
    """Test TorchCFCModel"""

    def setUp(self):
        self.config = LNNConfig(
            input_size=10,
            hidden_size=20,
            output_size=5,
            num_layers=1,
            dropout=0.1,
            time_constant=0.1,
        )
        self.model = TorchCFCModel(self.config)

    def test_model_creation(self):
        self.assertIsInstance(self.model, BaseLNN)
        self.assertIsNotNone(self.model.cfc_layer)
        self.assertIsNotNone(self.model.output_layer)

    def test_forward_single_step(self):
        x = torch.randn(5, 10)
        h = torch.zeros(5, 20)
        out, h_new = self.model(x, dt=0.1, hidden_state=h)
        self.assertEqual(out.shape, (5, 5))
        self.assertEqual(h_new.shape, (5, 20))

    def test_forward_sequence(self):
        x = torch.randn(5, 10, 10)
        h = torch.zeros(5, 20)
        out, h_new = self.model(x, dt=0.1, hidden_state=h)
        self.assertEqual(out.shape, (5, 10, 5))
        self.assertEqual(h_new.shape, (5, 20))

    def test_forward_auto_init_hidden(self):
        x = torch.randn(5, 10)
        out, h_new = self.model(x, dt=0.1)
        self.assertEqual(out.shape, (5, 5))
        self.assertEqual(h_new.shape, (5, 20))

    def test_init_hidden(self):
        h = self.model.init_hidden(10)
        self.assertEqual(h.shape, (10, 20))
        self.assertTrue(torch.all(h == 0))

    def test_reset(self):
        x = torch.randn(5, 10)
        self.model(x, dt=0.1)
        self.model.reset()
        self.assertIsNone(self.model.hidden_state)

    def test_get_info(self):
        info = self.model.get_info()
        self.assertIn("total_parameters", info)
        self.assertIn("config", info)
        self.assertEqual(info["config"]["input_size"], 10)

    def test_to_torchscript(self):
        x = torch.randn(5, 10)
        scripted = self.model.to_torchscript(x)
        self.assertIsInstance(scripted, torch.jit.ScriptModule)

    def test_model_device(self):
        device = self.model.device
        self.assertIsInstance(device, torch.device)

    def test_inference_speed(self):
        x = torch.randn(1, 10)
        self.model.eval()

        start = time.perf_counter()
        for _ in range(100):
            with torch.no_grad():
                self.model(x, dt=0.1)
        end = time.perf_counter()

        avg_time_ms = (end - start) / 100 * 1000
        self.assertLess(avg_time_ms, 50)


class TestLTCCell(unittest.TestCase):
    """Test LTCCell"""

    def test_ltc_cell_forward(self):
        cell = LTCCell(input_size=10, hidden_size=20)
        x = torch.randn(5, 10)
        h = torch.zeros(5, 20)
        h_new = cell(x, h, dt=0.1)
        self.assertEqual(h_new.shape, (5, 20))

    def test_ltc_cell_tau_constraint(self):
        cell = LTCCell(input_size=10, hidden_size=20)
        cell.tau.data.fill_(-1.0)
        x = torch.randn(5, 10)
        h = torch.zeros(5, 20)
        h_new = cell(x, h, dt=0.1)
        self.assertFalse(torch.isnan(h_new).any())

    def test_ltc_cell_weights_initialized(self):
        cell = LTCCell(input_size=10, hidden_size=20)
        self.assertFalse(torch.all(cell.W == 0))
        self.assertFalse(torch.all(cell.U == 0))

    def test_ltc_cell_tau_init(self):
        cell = LTCCell(input_size=10, hidden_size=20)
        self.assertTrue(torch.all(cell.tau == 1.0))

    def test_ltc_cell_gradients(self):
        cell = LTCCell(input_size=10, hidden_size=20)
        x = torch.randn(5, 10, requires_grad=True)
        h = torch.zeros(5, 20, requires_grad=True)
        h_new = cell(x, h, dt=0.1)
        loss = h_new.sum()
        loss.backward()
        self.assertIsNotNone(x.grad)
        self.assertIsNotNone(cell.W.grad)


class TestTorchLTCModel(unittest.TestCase):
    """Test TorchLTCModel"""

    def setUp(self):
        self.config = LNNConfig(
            input_size=10,
            hidden_size=20,
            output_size=5,
            num_layers=2,
            dropout=0.1,
            time_constant=0.1,
        )
        self.model = TorchLTCModel(self.config)

    def test_model_creation(self):
        self.assertIsInstance(self.model, BaseLNN)
        self.assertEqual(len(self.model.ltc_cells), 2)

    def test_forward_single_step(self):
        x = torch.randn(5, 10)
        h = torch.zeros(2, 5, 20)
        out, h_new = self.model(x, dt=0.1, hidden_state=h)
        self.assertEqual(out.shape, (5, 5))
        self.assertEqual(h_new.shape, (2, 5, 20))

    def test_forward_sequence(self):
        x = torch.randn(5, 8, 10)
        h = torch.zeros(2, 5, 20)
        out, h_new = self.model(x, dt=0.1, hidden_state=h)
        self.assertEqual(out.shape, (5, 8, 5))
        self.assertEqual(h_new.shape, (2, 5, 20))

    def test_forward_auto_init_hidden(self):
        x = torch.randn(5, 10)
        out, h_new = self.model(x, dt=0.1)
        self.assertEqual(out.shape, (5, 5))

    def test_init_hidden(self):
        h = self.model.init_hidden(10)
        self.assertEqual(h.shape, (2, 10, 20))
        self.assertTrue(torch.all(h == 0))

    def test_reset(self):
        x = torch.randn(5, 10)
        self.model(x, dt=0.1)
        self.model.reset()
        self.assertIsNone(self.model.hidden_state)

    def test_get_info(self):
        info = self.model.get_info()
        self.assertIn("total_parameters", info)
        self.assertGreater(info["total_parameters"], 0)

    def test_to_torchscript(self):
        x = torch.randn(5, 10)
        scripted = self.model.to_torchscript(x)
        self.assertIsInstance(scripted, torch.jit.ScriptModule)

    def test_tau_clamping_in_cells(self):
        x = torch.randn(5, 10)
        h = torch.zeros(5, 20)
        cell = self.model.ltc_cells[0]
        cell.tau.data.fill_(-0.5)
        h_new = cell(x, h, dt=0.1)
        self.assertFalse(torch.isnan(h_new).any())


class TestTorchHybridLNN(unittest.TestCase):
    """Test TorchHybridLNN"""

    def setUp(self):
        self.config = LNNConfig(
            input_size=6,
            hidden_size=32,
            output_size=5,
            num_layers=3,
            dropout=0.1,
            time_constant=0.1,
        )
        self.model = TorchHybridLNN(self.config)

    def test_model_creation(self):
        self.assertIsInstance(self.model, BaseLNN)
        self.assertIsNotNone(self.model.cnn)
        self.assertIsNotNone(self.model.ltc_cells)
        self.assertIsNotNone(self.model.output_layer)

    def test_forward_sequence(self):
        x = torch.randn(5, 8, 6)
        out, h_new = self.model(x, dt=0.1)
        self.assertEqual(out.shape, (5, 5))
        self.assertIsNotNone(h_new)

    def test_forward_2d_input(self):
        x = torch.randn(5, 16, 6)
        out, h_new = self.model(x, dt=0.1)
        self.assertEqual(out.shape, (5, 5))

    def test_forward_auto_init_hidden(self):
        x = torch.randn(5, 8, 6)
        out, h_new = self.model(x, dt=0.1)
        self.assertIsNotNone(h_new)

    def test_init_hidden(self):
        h = self.model.init_hidden(10)
        self.assertEqual(h.dim(), 3)
        self.assertEqual(h.shape[1], 10)

    def test_reset(self):
        x = torch.randn(5, 8, 6)
        self.model(x, dt=0.1)
        self.model.reset()
        self.assertIsNone(self.model.hidden_state)

    def test_get_info(self):
        info = self.model.get_info()
        self.assertIn("total_parameters", info)
        self.assertGreater(info["total_parameters"], 0)

    def test_cnn_layers_count(self):
        conv_count = sum(1 for m in self.model.cnn if isinstance(m, torch.nn.Conv1d))
        self.assertGreaterEqual(conv_count, 3)

    def test_cnn_batchnorm_relu(self):
        has_batchnorm = any(isinstance(m, torch.nn.BatchNorm1d) for m in self.model.cnn)
        has_relu = any(isinstance(m, torch.nn.ReLU) for m in self.model.cnn)
        self.assertTrue(has_batchnorm)
        self.assertTrue(has_relu)

    def test_output_layer_has_two_fc(self):
        linear_count = sum(
            1 for m in self.model.output_layer if isinstance(m, torch.nn.Linear)
        )
        self.assertGreaterEqual(linear_count, 2)


class TestBatchProcessing(unittest.TestCase):
    """Test batch processing capabilities for all models"""

    def test_cfc_batch_sizes(self):
        config = LNNConfig(10, 20, 5)
        model = TorchCFCModel(config)
        for batch_size in [1, 16, 64, 256]:
            x = torch.randn(batch_size, 10)
            out, h = model(x, dt=0.1)
            self.assertEqual(out.shape[0], batch_size)

    def test_ltc_batch_sizes(self):
        config = LNNConfig(10, 20, 5, num_layers=2)
        model = TorchLTCModel(config)
        for batch_size in [1, 16, 64, 256]:
            x = torch.randn(batch_size, 10)
            out, h = model(x, dt=0.1)
            self.assertEqual(out.shape[0], batch_size)

    def test_hybrid_batch_sizes(self):
        config = LNNConfig(6, 32, 5, num_layers=3)
        model = TorchHybridLNN(config)
        for batch_size in [1, 16, 64]:
            x = torch.randn(batch_size, 8, 6)
            out, h = model(x, dt=0.1)
            self.assertEqual(out.shape[0], batch_size)


class TestHiddenStateManagement(unittest.TestCase):
    """Test hidden state save and restore mechanisms"""

    def test_cfc_hidden_state_persistence(self):
        config = LNNConfig(10, 20, 5)
        model = TorchCFCModel(config)

        x1 = torch.randn(3, 10)
        out1, h1 = model(x1, dt=0.1)

        x2 = torch.randn(3, 10)
        out2, h2 = model(x2, dt=0.1, hidden_state=h1)

        self.assertFalse(torch.all(h1 == h2))

    def test_ltc_hidden_state_persistence(self):
        config = LNNConfig(10, 20, 5, num_layers=2)
        model = TorchLTCModel(config)

        x1 = torch.randn(3, 10)
        out1, h1 = model(x1, dt=0.1)

        x2 = torch.randn(3, 10)
        out2, h2 = model(x2, dt=0.1, hidden_state=h1)

        self.assertFalse(torch.all(h1 == h2))

    def test_hidden_state_reset_clears(self):
        config = LNNConfig(10, 20, 5)
        model = TorchCFCModel(config)

        x = torch.randn(3, 10)
        model(x, dt=0.1)
        self.assertIsNotNone(model.hidden_state)

        model.reset()
        self.assertIsNone(model.hidden_state)

        out, h = model(x, dt=0.1)
        self.assertIsNotNone(h)
        self.assertIsNotNone(model.hidden_state)


class TestDeviceAgnostic(unittest.TestCase):
    """Test device-agnostic code support"""

    def test_models_on_cpu(self):
        device = torch.device("cpu")

        cfc_config = LNNConfig(10, 20, 5)
        cfc = TorchCFCModel(cfc_config)
        cfc = cfc.to(device)

        ltc_config = LNNConfig(10, 20, 5, num_layers=2)
        ltc = TorchLTCModel(ltc_config)
        ltc = ltc.to(device)

        hybrid_config = LNNConfig(6, 32, 5, num_layers=3)
        hybrid = TorchHybridLNN(hybrid_config)
        hybrid = hybrid.to(device)

        x_cfc = torch.randn(3, 10, device=device)
        out_cfc, _ = cfc(x_cfc, dt=0.1)
        self.assertEqual(out_cfc.device, device)

        x_ltc = torch.randn(3, 10, device=device)
        out_ltc, _ = ltc(x_ltc, dt=0.1)
        self.assertEqual(out_ltc.device, device)

        x_hybrid = torch.randn(3, 8, 6, device=device)
        out_hybrid, _ = hybrid(x_hybrid, dt=0.1)
        self.assertEqual(out_hybrid.device, device)


if __name__ == "__main__":
    unittest.main()
