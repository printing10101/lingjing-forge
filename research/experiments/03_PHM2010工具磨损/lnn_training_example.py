"""
LNN训练模块使用示例

展示如何使用BoschCNCDataset、LNNTrainer和LNNEvaluator
"""

import logging
import torch
from torch.utils.data import DataLoader
from training import BoschCNCDataset, LNNTrainer, LNNEvaluator

logger = logging.getLogger(__name__)


def example_training():
    """完整训练流程示例"""

    # 1. 创建数据集
    dataset = BoschCNCDataset(
        hdf5_path="data/bosch_cnc.hdf5",
        operation=None,  # 使用所有工序
        extract_features=True,
        fs=1000.0,
        cache_data=True,
    )

    # 2. 划分数据集
    train_dataset, val_dataset, test_dataset = dataset.split(
        train_ratio=0.7,
        val_ratio=0.15,
        test_ratio=0.15,
        shuffle=True,
        random_seed=42,
    )

    # 3. 创建DataLoader
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)

    # 4. 创建模型（使用示例模型）
    from models.torch_cfc_model import CFCModel as TorchCFCModel

    model_config = {
        "model_name": "ExampleModel",
        "input_dim": 9,
        "output_dim": 2,
        "hidden_dim": 64,
        "num_layers": 2,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
    }
    model = TorchCFCModel(**model_config)

    # 5. 创建训练器
    trainer = LNNTrainer(
        model=model,
        learning_rate=0.001,
        optimizer_type="adam",
        loss_type="cross_entropy",
        epochs=100,
        early_stopping_patience=5,
        gradient_clip_value=1.0,
        lr_scheduler_type="step",
        lr_scheduler_params={"step_size": 30, "gamma": 0.1},
        device="cuda" if torch.cuda.is_available() else "cpu",
    )

    # 6. 训练模型
    history = trainer.fit(train_loader, val_loader, epochs=100)

    # 7. 保存检查点
    trainer.save_checkpoint("checkpoints/best_model.pth", metrics=history)

    # 8. 创建评估器
    evaluator = LNNEvaluator(model, device=trainer.device)

    # 9. 评估模型
    results = evaluator.evaluate(test_loader, task_type="classification")

    # 10. 生成报告
    report = evaluator.generate_report(results)
    logger.info("\n%s", report)
    evaluator.save_report(results, "reports/evaluation_report.txt")

    # 11. 特征重要性分析
    importance = evaluator.feature_importance(
        test_dataset._data,
        method="permutation",
        n_permutations=10,
    )
    logger.info("Feature Ranking: %s", importance["feature_ranking"])


if __name__ == "__main__":
    example_training()
