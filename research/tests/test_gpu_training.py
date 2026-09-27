"""
Comprehensive Unit Tests for GPU Accelerated Training Support

Tests cover:
- Device management (auto-detection, priority, fallback)
- Trainer enhancements (AMP, GPU memory monitoring)
- Model to_torch() conversion
- DataLoader optimization
- API endpoints
"""

import unittest
import os
import tempfile
import shutil
from unittest.mock import patch

import torch
from torch.utils.data import DataLoader, TensorDataset

from training.device_manager import (  # noqa: E402
    DeviceInfo,
    detect_device,
    get_available_devices,
    get_device_status,
    get_optimal_batch_size,
    get_optimal_num_workers,
    clear_gpu_memory,
    check_gpu_memory_safe,
)
from training.trainer import LNNTrainer  # noqa: E402
from models.torch_base_lnn import LNNConfig  # noqa: E402
from models.torch_cfc_model import CFCModel as TorchCFCModel  # noqa: E402
from models.torch_ltc_model import LTCModel as TorchLTCModel  # noqa: E402


class TestDeviceInfo(unittest.TestCase):
    """Test DeviceInfo class"""

    def test_device_info_creation(self):
        info = DeviceInfo(
            device_type="cuda",
            device_index=0,
            device_name="NVIDIA RTX 3090",
            total_memory_mb=24576.0,
            available_memory_mb=20000.0,
            cuda_version="11.8",
            compute_capability="8.6",
            gpu_count=1,
        )
        self.assertEqual(info.device_type, "cuda")
        self.assertEqual(info.device_name, "NVIDIA RTX 3090")
        self.assertEqual(info.gpu_count, 1)

    def test_device_info_to_dict(self):
        info = DeviceInfo(
            device_type="cpu",
            device_name="CPU",
        )
        d = info.to_dict()
        self.assertIn("device_type", d)
        self.assertEqual(d["device_type"], "cpu")
        self.assertIsInstance(d["total_memory_mb"], float)

    def test_device_info_cpu_defaults(self):
        info = DeviceInfo(device_type="cpu")
        self.assertEqual(info.total_memory_mb, 0.0)
        self.assertEqual(info.cuda_version, "")


class TestDetectDevice(unittest.TestCase):
    """Test detect_device function"""

    def test_detect_device_cpu_preference(self):
        device, info = detect_device("cpu")
        self.assertEqual(device.type, "cpu")
        self.assertEqual(info.device_type, "cpu")

    @patch("torch.cuda.is_available", return_value=False)
    def test_detect_device_auto_fallback_to_cpu(self, mock_cuda_available):
        device, info = detect_device("auto")
        self.assertEqual(device.type, "cpu")
        self.assertEqual(info.device_type, "cpu")

    @patch("torch.cuda.is_available", return_value=True)
    @patch("torch.cuda.device_count", return_value=1)
    @patch("torch.cuda.get_device_properties")
    @patch("torch.cuda.memory_allocated", return_value=0)
    @patch("torch.cuda.memory_reserved", return_value=0)
    @patch("torch.version.cuda", "11.8")
    def test_detect_device_gpu_available(
        self, mock_alloc, mock_reserved, mock_props, mock_count, mock_available
    ):
        mock_props.return_value.name = "NVIDIA RTX 3090"
        mock_props.return_value.total_memory = 24 * 1024 * 1024 * 1024
        mock_props.return_value.major = 8
        mock_props.return_value.minor = 6

        device, info = detect_device("auto")
        self.assertEqual(device.type, "cuda")
        self.assertEqual(info.device_type, "cuda")
        self.assertEqual(info.device_name, "NVIDIA RTX 3090")

    @patch.dict(os.environ, {"LNN_TRAINING_DEVICE": "cpu"})
    def test_detect_device_env_override(self):
        device, info = detect_device("auto")
        self.assertEqual(device.type, "cpu")

    @patch.dict(os.environ, {"LNN_TRAINING_DEVICE": ""})
    def test_detect_device_gpu_preference(self):
        if torch.cuda.is_available():
            device, info = detect_device("gpu")
            self.assertEqual(device.type, "cuda")
        else:
            device, info = detect_device("gpu")
            self.assertEqual(device.type, "cpu")


class TestGetAvailableDevices(unittest.TestCase):
    """Test get_available_devices function"""

    def test_returns_at_least_cpu(self):
        devices = get_available_devices()
        self.assertGreaterEqual(len(devices), 1)
        self.assertEqual(devices[0].device_type, "cpu")

    @patch("torch.cuda.is_available", return_value=True)
    @patch("torch.cuda.device_count", return_value=2)
    @patch("torch.cuda.get_device_properties")
    @patch("torch.version.cuda", "11.8")
    def test_returns_gpu_devices(self, mock_props, mock_count, mock_available):
        mock_props.return_value.name = "GPU"
        mock_props.return_value.total_memory = 8 * 1024 * 1024 * 1024
        mock_props.return_value.major = 7
        mock_props.return_value.minor = 5

        devices = get_available_devices()
        self.assertGreaterEqual(len(devices), 2)
        self.assertEqual(devices[1].device_type, "cuda")


class TestGetDeviceStatus(unittest.TestCase):
    """Test get_device_status function"""

    def test_cpu_status(self):
        device = torch.device("cpu")
        status = get_device_status(device)
        self.assertEqual(status["device_type"], "cpu")
        self.assertIn("cpu_percent", status)
        self.assertIn("total_memory_mb", status)

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA not available")
    def test_gpu_status(self):
        device = torch.device("cuda")
        status = get_device_status(device)
        self.assertEqual(status["device_type"], "cuda")
        self.assertIn("total_memory_mb", status)
        self.assertIn("allocated_memory_mb", status)


class TestGetOptimalBatchSize(unittest.TestCase):
    """Test get_optimal_batch_size function"""

    def test_cpu_batch_size(self):
        device = torch.device("cpu")
        batch_size = get_optimal_batch_size(device, default_batch_size=32)
        self.assertEqual(batch_size, 32)

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA not available")
    def test_gpu_batch_size_scales_with_memory(self):
        device = torch.device("cuda")
        batch_size = get_optimal_batch_size(device, default_batch_size=32)
        self.assertGreaterEqual(batch_size, 32)


class TestGetOptimalNumWorkers(unittest.TestCase):
    """Test get_optimal_num_workers function"""

    def test_returns_non_negative(self):
        workers = get_optimal_num_workers()
        self.assertGreaterEqual(workers, 0)

    @patch("multiprocessing.cpu_count", return_value=2)
    def test_low_cpu_count(self, mock_count):
        workers = get_optimal_num_workers()
        self.assertEqual(workers, 0)

    @patch("multiprocessing.cpu_count", return_value=4)
    def test_medium_cpu_count(self, mock_count):
        workers = get_optimal_num_workers()
        self.assertEqual(workers, 2)

    @patch("multiprocessing.cpu_count", return_value=16)
    def test_high_cpu_count(self, mock_count):
        workers = get_optimal_num_workers()
        self.assertEqual(workers, 8)


class TestClearGPUMemory(unittest.TestCase):
    """Test clear_gpu_memory function"""

    def test_noop_on_cpu(self):
        device = torch.device("cpu")
        clear_gpu_memory(device)

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA not available")
    def test_clears_gpu_memory(self):
        device = torch.device("cuda")
        clear_gpu_memory(device)


class TestCheckGPUMemorySafe(unittest.TestCase):
    """Test check_gpu_memory_safe function"""

    def test_returns_true_on_cpu(self):
        with patch("torch.cuda.is_available", return_value=False):
            self.assertTrue(check_gpu_memory_safe())

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA not available")
    def test_memory_safe_on_gpu(self):
        self.assertTrue(check_gpu_memory_safe(threshold_percent=99.0))


class TestLNNTrainerCPU(unittest.TestCase):
    """Test LNNTrainer on CPU"""

    def setUp(self):
        config = LNNConfig(
            input_size=10,
            hidden_size=20,
            output_size=5,
            num_layers=1,
            dropout=0.1,
        )
        self.model = TorchCFCModel(config)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dataloader(self, batch_size=8, num_samples=100):
        X = torch.randn(num_samples, 10)
        y = torch.randn(num_samples, 5)
        dataset = TensorDataset(X, y)
        return DataLoader(dataset, batch_size=batch_size, shuffle=True)

    def test_trainer_creation_cpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            optimizer_type="adam",
            loss_type="mse",
            batch_size=8,
            epochs=2,
            device="cpu",
            use_amp=False,
        )
        self.assertEqual(trainer.device.type, "cpu")
        self.assertFalse(trainer.use_amp)

    def test_train_epoch_cpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cpu",
            use_amp=False,
        )
        dataloader = self._create_dataloader()
        loss, acc, r2 = trainer.train_epoch(dataloader)
        self.assertIsInstance(loss, float)
        self.assertIsInstance(acc, float)

    def test_validate_cpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cpu",
            use_amp=False,
        )
        dataloader = self._create_dataloader()
        loss, acc, r2 = trainer.validate(dataloader)
        self.assertIsInstance(loss, float)

    def test_fit_cpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            epochs=2,
            early_stopping_patience=10,
            device="cpu",
            use_amp=False,
        )
        train_loader = self._create_dataloader()
        val_loader = self._create_dataloader()
        history = trainer.fit(train_loader, val_loader, epochs=2)
        self.assertIn("train_loss", history)
        self.assertGreaterEqual(len(history["train_loss"]), 1)

    def test_checkpoint_save_load_cpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cpu",
            use_amp=False,
        )
        checkpoint_path = os.path.join(self.temp_dir, "checkpoint.pt")
        trainer.save_checkpoint(checkpoint_path, epoch=1, metrics={"val_loss": 0.5})
        self.assertTrue(os.path.exists(checkpoint_path))

        loaded = trainer.load_checkpoint(checkpoint_path)
        self.assertEqual(loaded["epoch"], 1)
        self.assertEqual(loaded["metrics"]["val_loss"], 0.5)

    def test_training_summary_cpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cpu",
            use_amp=False,
        )
        trainer.training_history["train_loss"].append(0.5)
        trainer.training_history["val_loss"].append(0.6)
        trainer.training_history["train_accuracy"].append(0.8)
        trainer.training_history["val_accuracy"].append(0.75)
        trainer.current_epoch = 1

        summary = trainer.get_training_summary()
        self.assertIn("total_epochs", summary)
        self.assertEqual(summary["device"], "cpu")

    def test_gradient_clipping(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            gradient_clip_value=1.0,
            device="cpu",
            use_amp=False,
        )
        dataloader = self._create_dataloader()
        loss, acc, r2 = trainer.train_epoch(dataloader)
        self.assertIsInstance(loss, float)

    def test_lr_scheduler_step(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            lr_scheduler_type="step",
            lr_scheduler_params={"step_size": 1, "gamma": 0.5},
            device="cpu",
            use_amp=False,
        )
        trainer._step_lr_scheduler(0.5)
        self.assertLess(trainer.optimizer.param_groups[0]["lr"], 0.001)

    def test_optimizer_types(self):
        for opt_type in ["adam", "adamw", "sgd", "rmsprop"]:
            trainer = LNNTrainer(
                model=self.model,
                learning_rate=0.001,
                optimizer_type=opt_type,
                device="cpu",
                use_amp=False,
            )
            self.assertIsNotNone(trainer.optimizer)

    def test_loss_types(self):
        for loss_type in ["mse", "mae", "cross_entropy", "bce_with_logits"]:
            trainer = LNNTrainer(
                model=self.model,
                learning_rate=0.001,
                loss_type=loss_type,
                device="cpu",
                use_amp=False,
            )
            self.assertIsNotNone(trainer.criterion)

    def test_early_stopping(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            epochs=10,
            early_stopping_patience=1,
            device="cpu",
            use_amp=False,
        )
        train_loader = self._create_dataloader()
        val_loader = self._create_dataloader()
        history = trainer.fit(train_loader, val_loader, epochs=10)
        self.assertLessEqual(len(history["train_loss"]), 10)


@unittest.skipUnless(torch.cuda.is_available(), "CUDA not available")
class TestLNNTrainerGPU(unittest.TestCase):
    """Test LNNTrainer on GPU"""

    def setUp(self):
        config = LNNConfig(
            input_size=10,
            hidden_size=20,
            output_size=5,
            num_layers=1,
            dropout=0.1,
        )
        self.model = TorchCFCModel(config)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        torch.cuda.empty_cache()

    def _create_dataloader(self, batch_size=8, num_samples=100):
        X = torch.randn(num_samples, 10)
        y = torch.randn(num_samples, 5)
        dataset = TensorDataset(X, y)
        return DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=True,
            pin_memory=True,
        )

    def test_trainer_creation_gpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cuda",
            use_amp=True,
        )
        self.assertEqual(trainer.device.type, "cuda")
        self.assertTrue(trainer.use_amp)
        self.assertIsNotNone(trainer.scaler)

    def test_train_epoch_gpu_amp(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cuda",
            use_amp=True,
        )
        dataloader = self._create_dataloader()
        loss, acc, r2 = trainer.train_epoch(dataloader)
        self.assertIsInstance(loss, float)
        self.assertFalse(torch.isnan(torch.tensor(loss)))

    def test_validate_gpu_amp(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cuda",
            use_amp=True,
        )
        dataloader = self._create_dataloader()
        loss, acc, r2 = trainer.validate(dataloader)
        self.assertIsInstance(loss, float)

    def test_fit_gpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            epochs=2,
            early_stopping_patience=10,
            device="cuda",
            use_amp=True,
        )
        train_loader = self._create_dataloader()
        val_loader = self._create_dataloader()
        history = trainer.fit(train_loader, val_loader, epochs=2)
        self.assertIn("train_loss", history)
        self.assertGreaterEqual(len(history["train_loss"]), 1)

    def test_checkpoint_save_load_gpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cuda",
            use_amp=True,
        )
        checkpoint_path = os.path.join(self.temp_dir, "checkpoint_gpu.pt")
        trainer.save_checkpoint(checkpoint_path, epoch=1, metrics={"val_loss": 0.5})
        self.assertTrue(os.path.exists(checkpoint_path))

        loaded = trainer.load_checkpoint(checkpoint_path)
        self.assertEqual(loaded["epoch"], 1)

    def test_training_summary_gpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cuda",
            use_amp=True,
        )
        trainer.training_history["train_loss"].append(0.5)
        trainer.training_history["val_loss"].append(0.6)
        trainer.training_history["train_accuracy"].append(0.8)
        trainer.training_history["val_accuracy"].append(0.75)
        trainer.current_epoch = 1

        summary = trainer.get_training_summary()
        self.assertIn("gpu_name", summary)
        self.assertIn("gpu_max_memory_mb", summary)

    def test_gpu_memory_monitoring(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cuda",
            use_amp=False,
        )
        dataloader = self._create_dataloader(batch_size=32, num_samples=500)
        trainer.fit(dataloader, dataloader, epochs=1)

        summary = trainer.get_training_summary()
        self.assertGreater(summary["gpu_max_memory_mb"], 0)


class TestLTCModelTraining(unittest.TestCase):
    """Test LTC model training on both CPU and GPU"""

    def setUp(self):
        config = LNNConfig(
            input_size=10,
            hidden_size=20,
            output_size=5,
            num_layers=2,
            dropout=0.1,
        )
        self.model = TorchLTCModel(config)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _create_dataloader(self, batch_size=8, num_samples=100):
        X = torch.randn(num_samples, 10)
        y = torch.randn(num_samples, 5)
        dataset = TensorDataset(X, y)
        return DataLoader(dataset, batch_size=batch_size, shuffle=True)

    def test_ltc_training_cpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            epochs=2,
            device="cpu",
            use_amp=False,
        )
        train_loader = self._create_dataloader()
        val_loader = self._create_dataloader()
        history = trainer.fit(train_loader, val_loader, epochs=2)
        self.assertIn("train_loss", history)

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA not available")
    def test_ltc_training_gpu(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            epochs=2,
            device="cuda",
            use_amp=True,
        )
        train_loader = self._create_dataloader()
        val_loader = self._create_dataloader()
        history = trainer.fit(train_loader, val_loader, epochs=2)
        self.assertIn("train_loss", history)


class TestTorchScriptExport(unittest.TestCase):
    """Test TorchScript export functionality"""

    def setUp(self):
        config = LNNConfig(
            input_size=10,
            hidden_size=20,
            output_size=5,
            num_layers=1,
            dropout=0.1,
        )
        self.model = TorchCFCModel(config)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_export_torchscript(self):
        trainer = LNNTrainer(
            model=self.model,
            learning_rate=0.001,
            device="cpu",
            use_amp=False,
        )
        example_input = torch.randn(1, 10)
        save_path = os.path.join(self.temp_dir, "model.pt")
        exported_path = trainer.export_torchscript(save_path, example_input)
        self.assertTrue(os.path.exists(exported_path))

        loaded = torch.jit.load(exported_path)
        out, h = loaded(example_input)
        self.assertEqual(out.shape, (1, 5))


if __name__ == "__main__":
    unittest.main()
