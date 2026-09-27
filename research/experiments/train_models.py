"""
模型训练脚本 - 切削力预测 & 磨损预测

功能：
- 使用模拟Bosch数据集训练模型
- 支持切削力预测模型和磨损预测模型
- 自动导出TorchScript格式
- 保存训练指标和模型权重
"""

import os
import sys
import time
import json
import logging
import argparse
import torch
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from training.bosch_dataset import (  # noqa: E402
    BoschDatasetProcessor,
    BoschDataConfig,
    BoschDataGenerator,
)
from training.trainer import LNNTrainer  # noqa: E402
from training.evaluator import LNNEvaluator  # noqa: E402
from models.torch_cfc_model import CFCModel, LNNConfig as CFCConfig  # noqa: E402
from models.torch_ltc_model import LTCModel, LNNConfig as LTCConfig  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def train_cutting_force_model(
    output_dir: str = "models/lnn",
    epochs: int = 200,
    batch_size: int = 64,
    learning_rate: float = 0.001,
    hidden_size: int = 128,
    num_layers: int = 2,
    model_type: str = "cfc",
):
    """训练切削力预测模型"""
    logger.info("=" * 60)
    logger.info("Training Cutting Force Prediction Model")
    logger.info("=" * 60)

    logger.info("Generating simulated Bosch cutting force dataset...")
    df = BoschDataGenerator.generate_cutting_force_data(
        n_samples=50000, n_features=15, noise_level=0.05, seed=42
    )
    logger.info(f"Generated dataset: {df.shape}")

    target_columns = ["cutting_force"]
    config = BoschDataConfig(
        target_columns=target_columns,
        test_size=0.15,
        val_size=0.1,
        normalization_method="standard",
        batch_size=batch_size,
        window_size=50,
    )

    processor = BoschDatasetProcessor(config)
    processor.load_data_from_dataframe(df)
    processor.clean_data()
    processor.engineer_features(
        add_lag_features=True,
        lag_steps=[1, 3, 5],
        add_rolling_stats=True,
        rolling_windows=[5, 10],
    )

    train_loader, val_loader, test_loader = processor.create_dataloaders()
    stats = processor.get_stats()
    logger.info(f"Data stats: {stats}")

    feature_dim = (
        processor.feature_data.shape[1] if processor.feature_data is not None else 15
    )
    output_dim = len(target_columns)

    if model_type == "cfc":
        model_config = CFCConfig(
            input_size=feature_dim,
            hidden_size=hidden_size,
            output_size=output_dim,
            num_layers=num_layers,
            dropout=0.1,
        )
        model = CFCModel(model_config)
    else:
        model_config = LTCConfig(
            input_size=feature_dim,
            hidden_size=hidden_size,
            output_size=output_dim,
            num_layers=num_layers,
            dropout=0.1,
        )
        model = LTCModel(model_config)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device)

    trainer = LNNTrainer(
        model=model,
        learning_rate=learning_rate,
        optimizer_type="adamw",
        loss_type="mse",
        batch_size=batch_size,
        epochs=epochs,
        device=device,
        early_stopping_patience=10,
        gradient_clip_value=1.0,
        lr_scheduler_type="cosine",
        use_amp=(device == "cuda"),
        weight_decay=1e-5,
    )

    start_time = time.perf_counter()
    trainer.fit(train_loader, val_loader)
    training_time = time.perf_counter() - start_time

    logger.info(f"Training completed in {training_time:.2f}s")
    logger.info(f"Best validation loss: {trainer.best_val_loss:.6f}")

    save_path = os.path.join(output_dir, "cutting_force_v1.pt")
    trainer.save_checkpoint(save_path)

    example_input = torch.randn(1, feature_dim, device=device)
    torchscript_path = os.path.join(output_dir, "cutting_force_v1.torchscript.pt")
    trainer.export_torchscript(torchscript_path, example_input)

    evaluator = LNNEvaluator(model, device=device)
    test_metrics = evaluator.evaluate(test_loader, task_type="regression")
    logger.info(f"Test metrics: {test_metrics}")

    evaluator.plot_results(output_dir, prefix="cutting_force")

    summary = trainer.get_training_summary()
    summary["training_time"] = training_time
    summary["test_metrics"] = test_metrics
    summary_path = os.path.join(output_dir, "cutting_force_training_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    logger.info(f"Model saved to {save_path}")
    logger.info(f"TorchScript saved to {torchscript_path}")
    logger.info(f"Summary saved to {summary_path}")

    return model, summary


def train_wear_prediction_model(
    output_dir: str = "models/lnn",
    epochs: int = 200,
    batch_size: int = 64,
    learning_rate: float = 0.001,
    hidden_size: int = 128,
    num_layers: int = 2,
    model_type: str = "ltc",
):
    """训练磨损预测模型"""
    logger.info("=" * 60)
    logger.info("Training Tool Wear Prediction Model")
    logger.info("=" * 60)

    logger.info("Generating simulated Bosch tool wear dataset...")
    df = BoschDataGenerator.generate_cutting_force_data(
        n_samples=50000, n_features=15, noise_level=0.05, seed=123
    )
    logger.info(f"Generated dataset: {df.shape}")

    target_columns = ["tool_wear"]
    config = BoschDataConfig(
        target_columns=target_columns,
        test_size=0.15,
        val_size=0.1,
        normalization_method="standard",
        batch_size=batch_size,
        window_size=50,
    )

    processor = BoschDatasetProcessor(config)
    processor.load_data_from_dataframe(df)
    processor.clean_data()
    processor.engineer_features(
        add_lag_features=True,
        lag_steps=[1, 3, 5, 10],
        add_rolling_stats=True,
        rolling_windows=[5, 10, 20],
        add_diff_features=True,
    )

    train_loader, val_loader, test_loader = processor.create_dataloaders()
    stats = processor.get_stats()
    logger.info(f"Data stats: {stats}")

    feature_dim = (
        processor.feature_data.shape[1] if processor.feature_data is not None else 20
    )
    output_dim = len(target_columns)

    if model_type == "cfc":
        model_config = CFCConfig(
            input_size=feature_dim,
            hidden_size=hidden_size,
            output_size=output_dim,
            num_layers=num_layers,
            dropout=0.1,
        )
        model = CFCModel(model_config)
    else:
        model_config = LTCConfig(
            input_size=feature_dim,
            hidden_size=hidden_size,
            output_size=output_dim,
            num_layers=num_layers,
            dropout=0.1,
        )
        model = LTCModel(model_config)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device)

    trainer = LNNTrainer(
        model=model,
        learning_rate=learning_rate,
        optimizer_type="adamw",
        loss_type="mse",
        batch_size=batch_size,
        epochs=epochs,
        device=device,
        early_stopping_patience=10,
        gradient_clip_value=1.0,
        lr_scheduler_type="cosine",
        use_amp=(device == "cuda"),
        weight_decay=1e-5,
    )

    start_time = time.perf_counter()
    trainer.fit(train_loader, val_loader)
    training_time = time.perf_counter() - start_time

    logger.info(f"Training completed in {training_time:.2f}s")
    logger.info(f"Best validation loss: {trainer.best_val_loss:.6f}")

    save_path = os.path.join(output_dir, "wear_prediction_v1.pt")
    trainer.save_checkpoint(save_path)

    example_input = torch.randn(1, feature_dim, device=device)
    torchscript_path = os.path.join(output_dir, "wear_prediction_v1.torchscript.pt")
    trainer.export_torchscript(torchscript_path, example_input)

    evaluator = LNNEvaluator(model, device=device)
    test_metrics = evaluator.evaluate(test_loader, task_type="regression")
    logger.info(f"Test metrics: {test_metrics}")

    evaluator.plot_results(output_dir, prefix="wear_prediction")

    summary = trainer.get_training_summary()
    summary["training_time"] = training_time
    summary["test_metrics"] = test_metrics
    summary_path = os.path.join(output_dir, "wear_prediction_training_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    logger.info(f"Model saved to {save_path}")
    logger.info(f"TorchScript saved to {torchscript_path}")
    logger.info(f"Summary saved to {summary_path}")

    return model, summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train LNN models")
    parser.add_argument(
        "--model", choices=["cutting_force", "wear", "both"], default="both"
    )
    parser.add_argument("--output_dir", default="models/lnn")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch_size", type=int, default=64)
    parser.add_argument("--learning_rate", type=float, default=0.001)
    parser.add_argument("--hidden_size", type=int, default=64)
    parser.add_argument("--num_layers", type=int, default=2)

    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    if args.model in ("cutting_force", "both"):
        train_cutting_force_model(
            output_dir=args.output_dir,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            hidden_size=args.hidden_size,
            num_layers=args.num_layers,
        )

    if args.model in ("wear", "both"):
        train_wear_prediction_model(
            output_dir=args.output_dir,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            hidden_size=args.hidden_size,
            num_layers=args.num_layers,
        )

    logger.info("All training completed!")
